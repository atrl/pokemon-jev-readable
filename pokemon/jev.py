"""Call JEV, redact credentials, and validate one physical-input answer.

Request construction is in prompt.py; this module owns HTTP retries and the
response contract. No action executes until validate_response succeeds.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Callable

from controls import BUTTONS
from prompt import build_request

# Preserve the small public API while request construction lives in prompt.py.
from prompt import DEFAULT_GAME_GOAL as DEFAULT_GAME_GOAL
from prompt import observation_for_model as observation_for_model

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TRANSIENT_HTTP_STATUSES = (429, 500, 502, 503, 504, 529)


class JevUnavailable(RuntimeError):
    """Temporary service/network failure; no physical action was authorized."""


def validate_response(response: dict) -> dict:
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValueError("Jev returned an invalid response; nothing will execute")
    answer = response["answers"].get("button")
    if not isinstance(answer, dict):
        raise ValueError("Jev returned an invalid response; nothing will execute")
    if answer.get("type") != "choice" or answer.get("choice") not in BUTTONS:
        raise ValueError("Jev returned an invalid button; nothing will execute")
    probs = answer.get("probabilities", {})
    if not isinstance(probs, dict) or set(probs) != set(BUTTONS):
        raise ValueError("Jev omitted/added candidates; nothing will execute")
    values = [*probs.values(), answer.get("confidence")]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise ValueError("Jev returned invalid probabilities")
    if not math.isclose(sum(probs.values()), 1.0, abs_tol=0.02):
        raise ValueError("Jev probabilities do not sum to 1")
    if probs[answer["choice"]] < max(probs.values()) - 1e-6:
        raise ValueError("Chosen button is not the highest-probability option")
    return answer


def redact_secrets(value, secrets: tuple[str, ...] = ()):
    """Keep credentials out of events/artifacts, including API-echoed values."""
    keys = tuple(
        secret for secret in (*secrets, os.environ.get("TYPESAFE_API_KEY", "").strip(), os.environ.get("DEEPSEEK_API_KEY", "").strip()) if secret
    )
    sensitive_fields = {
        "authorization",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
    }
    if isinstance(value, dict):
        return {
            redact_secrets(str(k), keys): "[REDACTED]"
            if str(k).lower().replace("_", "").replace("-", "") in sensitive_fields
            or str(k).lower().replace("_", "").endswith("apikey")
            else redact_secrets(v, keys)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(v, keys) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "[NONFINITE]"
    if isinstance(value, str):
        for key in keys:
            value = value.replace(key, "[REDACTED]")
    return value


def choose(
    observation: dict,
    goal: str,
    history: list[dict],
    *,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()

    def emit(event_type: str, **payload) -> None:
        if on_event is not None:
            on_event(redact_secrets({"type": event_type, **payload}, (key,)))

    if not key:
        emit("jev_error", error="missing_api_key", phase="configuration")
        raise RuntimeError("TYPESAFE_API_KEY is missing; no Jev call or fallback action was made")
    try:
        body = build_request(observation, goal, history)
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body, allow_nan=False).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except Exception:
        emit("jev_error", error="invalid_request", phase="request")
        raise ValueError("Could not prepare Jev request; no action executed") from None
    started = time.monotonic()
    for attempt in range(1, 4):
        emit("jev_request", attempt=attempt, request=body)
        attempt_started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as result:
                status = getattr(result, "status", 200)
                raw_response = result.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                error_body = json.loads(exc.read())
            except Exception:
                error_body = {"unavailable": "HTTP error body could not be parsed as JSON"}
            finally:
                exc.close()
            emit(
                "jev_response",
                attempt=attempt,
                response=error_body,
                httpStatus=status,
                latency_ms=round((time.monotonic() - attempt_started) * 1000),
            )
            emit(
                "jev_error",
                attempt=attempt,
                error="http_error",
                phase="request",
                httpStatus=status,
                latency_ms=round((time.monotonic() - attempt_started) * 1000),
            )
            if status in TRANSIENT_HTTP_STATUSES and attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            if status in TRANSIENT_HTTP_STATUSES:
                raise JevUnavailable(f"Jev HTTP {status}; no action executed") from None
            raise RuntimeError(f"Jev HTTP {status}; no action executed") from None
        except KeyboardInterrupt:
            emit("jev_error", attempt=attempt, error="interrupted", phase="request")
            raise
        except Exception as exc:
            emit(
                "jev_error",
                attempt=attempt,
                error="connection_failed",
                phase="request",
                exception_type=type(exc).__name__,
                reason_type=type(getattr(exc, "reason", None)).__name__,
                errno=getattr(getattr(exc, "reason", exc), "errno", None)
                if type(getattr(getattr(exc, "reason", exc), "errno", None)) is int
                else None,
                latency_ms=round((time.monotonic() - attempt_started) * 1000),
            )
            if isinstance(exc, (OSError, http.client.HTTPException)) and attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            if isinstance(exc, (OSError, http.client.HTTPException)):
                raise JevUnavailable("Jev connection failed; no action executed") from None
            raise RuntimeError("Jev connection failed; no action executed") from None
        try:
            response = json.loads(raw_response)
        except (ValueError, UnicodeDecodeError):
            emit(
                "jev_response",
                attempt=attempt,
                response={"invalid_json": True},
                httpStatus=status,
                latency_ms=round((time.monotonic() - attempt_started) * 1000),
            )
            emit(
                "jev_error",
                attempt=attempt,
                error="invalid_json",
                phase="validation",
                httpStatus=status,
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise ValueError("Jev returned invalid JSON; no action executed") from None
        emit(
            "jev_response",
            attempt=attempt,
            response=response,
            httpStatus=status,
            latency_ms=round((time.monotonic() - attempt_started) * 1000),
        )
        try:
            answer = validate_response(response)
        except (ValueError, TypeError, AttributeError):
            emit("jev_error", attempt=attempt, error="invalid_response", phase="validation")
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise ValueError("Jev returned an invalid decision; no action executed") from None
        return redact_secrets(
            {
                "answer": answer,
                "request": body,
                "response": response,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "source": "jev",
            },
            (key,),
        )
