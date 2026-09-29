"""Shared experiment identity, bounded resources, and safe artifact helpers."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time

PROTOCOL = "pokemon-three-arm-v1"
ACTIONS = ("up", "down", "left", "right", "a", "b", "start", "select", "wait")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = {
    "protocol": PROTOCOL,
    "environment": {
        "held_frames": 8,
        "settle_frames": 16,
        "max_steps": 200,
        "max_packet_bytes": 65536,
    },
    "budget": {
        "max_steps": 200,
        "max_seconds": 300,
        "max_requests": 60,
        "max_tokens": 200000,
        "max_same_state_requests": 6,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout_seconds": 45,
        "max_output_tokens": 1024,
        "max_retries": 1,
    },
    "kev": {
        "base_url": "http://127.0.0.1:8009",
        "model": "kev-latest",
        "api_key_env": "KEV_API_KEY",
        "timeout_seconds": 30,
        "max_output_tokens": 256,
        "max_retries": 1,
    },
}


class ExperimentError(RuntimeError):
    """A safe code; remote error bodies and secrets never belong here."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class BudgetExceeded(ExperimentError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def source_digest():
    """Bind evidence to source bytes, including uncommitted verification builds."""
    paths = list((ROOT / "pokemon").rglob("*.py"))
    paths += list((ROOT / "pokemon/data").glob("*.json"))
    paths += list((ROOT / "prompts").rglob("*.txt"))
    return digest(
        {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)
            if not any(p.startswith(".") for p in path.relative_to(ROOT).parts)
        }
    )


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def load_config(path=None):
    result = deepcopy(DEFAULT_CONFIG)
    if path:
        supplied = json.loads(Path(path).read_text())
        if not isinstance(supplied, dict):
            raise ValueError("Experiment config must be an object")
        for key, value in supplied.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key].update(value)
            else:
                result[key] = value
    if result.get("protocol") != PROTOCOL:
        raise ValueError("Unsupported experiment protocol")
    # Validation also happens in the environment and providers at their boundaries.
    Budget(result)
    return result


class Budget:
    """Episode-wide accounting. Replanning never resets request/evidence counters.

    Tokens supplied by the provider are measured; missing usage is separately
    labelled unknown and charged the conservative request reservation. No claim
    of exact token cost is made for unknown usage or server-side failed requests.
    """

    def __init__(self, config=None, *, clock=time.monotonic):
        supplied = config or {}
        limits = supplied.get("budget", supplied)
        self.limits = {**DEFAULT_CONFIG["budget"], **limits}
        for key in DEFAULT_CONFIG["budget"]:
            value = self.limits[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be positive and finite")
            if key != "max_seconds" and type(value) is not int:
                raise ValueError(f"{key} must be an integer")
        self.clock = clock
        self.started = clock()
        self.records = []
        self.states = {}
        self.actions = 0

    @property
    def remaining_seconds(self):
        return max(0.0, self.limits["max_seconds"] - (self.clock() - self.started))

    @property
    def charged_tokens(self):
        return sum(r["charged_tokens"] for r in self.records)

    def check(self):
        if self.remaining_seconds <= 0:
            raise BudgetExceeded("wall_time_budget")
        if self.charged_tokens > self.limits["max_tokens"]:
            raise BudgetExceeded("token_budget")

    def before_action(self):
        self.check()
        if self.actions >= self.limits["max_steps"]:
            raise BudgetExceeded("action_budget")

    def record_action(self):
        self.actions += 1

    def before_request(self, owner, state_key, *, estimated_tokens=0):
        self.check()
        if len(self.records) >= self.limits["max_requests"]:
            raise BudgetExceeded("request_budget")
        # Count across owners as well: bouncing from actor to planner is still
        # another purchase of judgment about unchanged evidence.
        if self.states.get(state_key, 0) >= self.limits["max_same_state_requests"]:
            raise BudgetExceeded("same_state_request_budget")
        if type(estimated_tokens) is not int or estimated_tokens < 0:
            raise ValueError("Invalid token reservation")
        if self.charged_tokens + estimated_tokens > self.limits["max_tokens"]:
            raise BudgetExceeded("token_budget")
        self.states[state_key] = self.states.get(state_key, 0) + 1
        request_id = len(self.records) + 1
        self.records.append(
            {
                "id": request_id,
                "owner": owner,
                "state_key": state_key,
                "status": "in_flight",
                "usage": None,
                "reserved_tokens": estimated_tokens,
                "charged_tokens": estimated_tokens,
                "usage_known": False,
            }
        )
        return request_id

    def after_request(self, request_id, usage=None, latency_seconds=0, status="ok", model=None):
        record = self.records[request_id - 1]
        if record["status"] != "in_flight":
            raise ValueError("Request was already accounted")
        total = None
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
            if total is None:
                prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
                completion = usage.get("completion_tokens", usage.get("output_tokens"))
                if type(prompt) is int and type(completion) is int and min(prompt, completion) >= 0:
                    total = prompt + completion
            if type(total) is not int or total < 0:
                total = None
        record.update(
            status=status,
            latency_seconds=round(latency_seconds, 6),
            model=model,
            usage=deepcopy(usage) if total is not None else None,
            usage_known=total is not None,
            charged_tokens=total if total is not None else record["reserved_tokens"],
        )

    def snapshot(self):
        return {
            "limits": deepcopy(self.limits),
            "executed_actions": self.actions,
            "http_requests": len(self.records),
            "charged_tokens": self.charged_tokens,
            "measured_tokens": sum(r["charged_tokens"] for r in self.records if r["usage_known"]),
            "unknown_usage_requests": sum(not r["usage_known"] for r in self.records),
            "repeated_evidence_requests": sum(max(0, count - 1) for count in self.states.values()),
            "elapsed_seconds": round(self.clock() - self.started, 6),
            "requests": deepcopy(self.records),
        }
