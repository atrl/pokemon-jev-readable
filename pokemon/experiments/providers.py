"""Independent Kev System One and DeepSeek chat transports with bounded accounting.

Kev's wire contract: github.com/jaredpalmer/kev/blob/main/kev/api.py and serve.py.
DeepSeek: api-docs.deepseek.com/api/create-chat-completion/.
No import, constructor, or disabled request performs network I/O.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .common import ExperimentError, canonical_json, digest

TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504, 529}
MAX_RESPONSE_BYTES = 2_000_000
DEFAULTS = {
    "kev": {
        "base_url": "http://127.0.0.1:8009",
        "model": "kev-latest",
        "api_key_env": "KEV_API_KEY",
        "max_output_tokens": 2048,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "max_output_tokens": 1024,
    },
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def state_key(packet: dict) -> str:
    """Plans and model notes never grant a fresh same-state request allowance."""
    identity = packet.get("observation_id")
    if isinstance(identity, str) and identity:
        return identity
    observation = packet.get("observation")
    if not isinstance(observation, dict):
        raise ExperimentError("invalid_observation_packet")
    return digest(observation)


def _positive_number(value, name: str, *, integer=False, maximum=None):
    expected = (int,) if integer else (int, float)
    if type(value) not in expected or not math.isfinite(value) or value <= 0:
        raise ExperimentError(f"invalid_provider_{name}")
    if maximum is not None and value > maximum:
        raise ExperimentError(f"invalid_provider_{name}")
    return value


def _endpoint(base_url: str, owner: str) -> str:
    if not isinstance(base_url, str):
        raise ExperimentError("invalid_provider_endpoint")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        parsed.port  # Validate malformed port numbers without exposing the URL.
    except ValueError:
        raise ExperimentError("invalid_provider_endpoint") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ExperimentError("invalid_provider_endpoint")
    suffix = "/v1/systemone" if owner == "kev" else "/chat/completions"
    path = parsed.path.rstrip("/")
    if path.endswith(suffix):
        return base_url.rstrip("/")
    if owner == "kev" and path.endswith("/v1"):
        suffix = "/systemone"
    return urllib.parse.urlunsplit(parsed._replace(path=path + suffix))


def _safe_model(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if any(ord(char) < 32 for char in value):
        return None
    return value


def _usage(value):
    """Keep the provider's actual token fields; missing/partial usage stays unknown."""
    if not isinstance(value, dict):
        return None
    fields = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    clean = {key: value[key] for key in fields if key in value}
    if not clean or any(type(count) is not int or count < 0 for count in clean.values()):
        return None
    if "total_tokens" not in clean and not (
        {"input_tokens", "output_tokens"} <= clean.keys()
        or {"prompt_tokens", "completion_tokens"} <= clean.keys()
    ):
        return None
    if "total_tokens" in clean:
        for input_key, output_key in (
            ("input_tokens", "output_tokens"),
            ("prompt_tokens", "completion_tokens"),
        ):
            if input_key in clean and output_key in clean:
                if clean["total_tokens"] != clean[input_key] + clean[output_key]:
                    return None
    return clean


@dataclass(frozen=True)
class Response:
    data: dict
    request_id: int
    model: str | None
    usage: dict | None


class Provider:
    def __init__(self, owner, budget, config, *, allow_model_calls=False):
        if owner not in DEFAULTS:
            raise ExperimentError("unknown_provider")
        self.owner, self.budget = owner, budget
        self.allow_model_calls = allow_model_calls is True
        providers = config.get("providers", {})
        if not isinstance(providers, dict):
            raise ExperimentError("invalid_provider_config")
        supplied = providers.get(owner, config.get(owner, {}))
        if not isinstance(supplied, dict):
            raise ExperimentError("invalid_provider_config")
        cfg = {**DEFAULTS[owner], **supplied}
        prefix = owner.upper()
        self.url = _endpoint(os.environ.get(f"{prefix}_BASE_URL") or cfg["base_url"], owner)
        self.model = os.environ.get(f"{prefix}_MODEL") or cfg["model"]
        if _safe_model(self.model) is None:
            raise ExperimentError("invalid_provider_model")
        self.api_key_env = cfg.get("api_key_env")
        if self.api_key_env is not None and (
            not isinstance(self.api_key_env, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env)
        ):
            raise ExperimentError("invalid_provider_auth")
        self.timeout = _positive_number(cfg.get("timeout_seconds", 30), "timeout")
        self.max_output_tokens = _positive_number(
            cfg["max_output_tokens"], "max_output_tokens", integer=True
        )
        self.max_retries = cfg.get("max_retries", 1)
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise ExperimentError("invalid_provider_max_retries")
        self.checkpoint_sha256 = cfg.get("checkpoint_sha256")
        if self.checkpoint_sha256 is not None and (
            not isinstance(self.checkpoint_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.checkpoint_sha256)
        ):
            raise ExperimentError("invalid_provider_checkpoint_sha256")
        self._opener = urllib.request.build_opener(_NoRedirect())

    def public_config(self) -> dict:
        """Resolved connection identity, never authentication values or verified weights."""

        def redact(value):
            for name in (self.api_key_env, "DEEPSEEK_API_KEY", "KEV_API_KEY", "TYPESAFE_API_KEY"):
                key = os.environ.get(name, "").strip() if name else ""
                if key:
                    value = value.replace(key, "[REDACTED]")
                    value = value.replace(urllib.parse.quote(key, safe=""), "[REDACTED]")
            return value

        result = {
            "provider": self.owner,
            "protocol": "typesafe-systemone-v1"
            if self.owner == "kev"
            else "openai-chat-completions",
            "endpoint": redact(self.url),
            "configured_model": redact(self.model),
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout,
            "max_output_tokens": self.max_output_tokens,
            "max_retries": self.max_retries,
            "checkpoint_verified": False,
        }
        if self.checkpoint_sha256 is not None:
            result["declared_checkpoint_sha256"] = self.checkpoint_sha256
        return result

    def _post(self, body: dict, packet: dict) -> Response:
        if not self.allow_model_calls:
            raise ExperimentError("model_calls_not_authorized")
        key = os.environ.get(self.api_key_env, "").strip() if self.api_key_env else ""
        if self.owner == "deepseek" and self.api_key_env and not key:
            raise ExperimentError("missing_deepseek_api_key")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        encoded = canonical_json(body).encode("utf-8")
        request = urllib.request.Request(self.url, data=encoded, headers=headers, method="POST")
        for attempt in range(self.max_retries + 1):
            self.budget.check()
            timeout = min(self.timeout, self.budget.remaining_seconds)
            if timeout <= 0:
                self.budget.check()
                raise ExperimentError("request_deadline")
            # Byte count is a conservative reservation, never reported as measured tokens.
            request_id = self.budget.before_request(
                self.owner,
                state_key(packet),
                estimated_tokens=len(encoded) + self.max_output_tokens,
            )
            started = time.monotonic()
            usage, model, status = None, None, "network_error"
            retryable, error_code, response_data = False, None, None
            try:
                with self._opener.open(request, timeout=timeout) as result:
                    response_status = result.status
                    if not 200 <= response_status < 300:
                        status = f"http_{response_status}"
                        error_code = f"{self.owner}_{status}"
                        retryable = response_status in TRANSIENT_STATUSES
                    else:
                        raw = result.read(MAX_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError("response_too_large")
                        response_data = json.loads(raw)
                        if not isinstance(response_data, dict):
                            raise ValueError("invalid_response")
                        usage = _usage(response_data.get("usage"))
                        model = _safe_model(response_data.get("model"))
                        # A provider must not be able to echo credentials into artifacts.
                        if model and key and key in model:
                            model = None
                        status = "ok"
            except urllib.error.HTTPError as exc:
                status = f"http_{exc.code}"
                retryable = exc.code in TRANSIENT_STATUSES
                error_code = f"{self.owner}_{status}"
                exc.close()  # Never read or log remote error bodies.
            except (ValueError, UnicodeDecodeError):
                status = "invalid_json"
                error_code = f"{self.owner}_invalid_json"
            except (OSError, http.client.HTTPException):
                retryable = True
                error_code = f"{self.owner}_network_error"
            finally:
                self.budget.after_request(
                    request_id,
                    usage=usage,
                    latency_seconds=time.monotonic() - started,
                    status=status,
                    model=model,
                )
            self.budget.check()
            if error_code is None:
                return Response(response_data, request_id, model, usage)
            if not retryable or attempt == self.max_retries:
                raise ExperimentError(error_code) from None
        raise ExperimentError("provider_request_failed")

    def chat(self, system: str, payload: dict, packet: dict) -> Response:
        if self.owner != "deepseek":
            raise ExperimentError("provider_protocol_mismatch")
        response = self._post(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": canonical_json(payload)},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": self.max_output_tokens,
                "stream": False,
            },
            packet,
        )
        try:
            choices = response.data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            if choices[0].get("finish_reason") != "stop":
                raise ValueError
            content = choices[0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise ExperimentError("deepseek_invalid_response") from None
        return Response(data, response.request_id, response.model, response.usage)

    def choices(self, state: dict, questions: dict, packet: dict) -> Response:
        if self.owner != "kev":
            raise ExperimentError("provider_protocol_mismatch")
        return self._post({"state": state, "model": self.model, "questions": questions}, packet)
