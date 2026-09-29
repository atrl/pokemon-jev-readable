"""Interchangeable model controllers; neither controller executes game inputs."""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path

from .common import ACTIONS, ExperimentError, digest
from .providers import Provider

PROMPTS = Path(__file__).resolve().parents[2] / "prompts" / "experiments"
# parents[2] is pokemon's parent (the repository root).
BUTTON_CRITERIA = {
    "up": "Press UP: walk or turn north, or move a menu cursor up.",
    "down": "Press DOWN: walk or turn south, or move a menu cursor down.",
    "left": "Press LEFT: walk or turn west, or move a menu cursor left.",
    "right": "Press RIGHT: walk or turn east, or move a menu cursor right.",
    "a": "Press A: confirm, interact or advance dialogue.",
    "b": "Press B: cancel or return from a menu.",
    "start": "Press START: use the game's start/menu function.",
    "select": "Press SELECT: use the game's context-specific select function.",
    "wait": "Release all buttons and advance animation or text.",
}
PLAN_FIT = {
    "applicable": "Observed evidence supports using the current plan now.",
    "contradicted": "Observed evidence conflicts with the current plan.",
    "unknown": "Available evidence is insufficient to judge the current plan.",
}


def _prompt(name):
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def _text(value, maximum):
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum


def validate_plan(value, max_ttl):
    if not isinstance(value, dict) or set(value) != {"intent", "policy", "ttl"}:
        raise ExperimentError("invalid_plan")
    if not _text(value["intent"], 500):
        raise ExperimentError("invalid_plan")
    policy = value["policy"]
    if not isinstance(policy, list) or not 1 <= len(policy) <= 8:
        raise ExperimentError("invalid_plan")
    if any(not _text(statement, 500) for statement in policy):
        raise ExperimentError("invalid_plan")
    if type(value["ttl"]) is not int or not 1 <= value["ttl"] <= max_ttl:
        raise ExperimentError("invalid_plan")
    return deepcopy(value)


def validate_choice(value, candidates):
    if not isinstance(value, dict) or value.get("type") != "choice":
        raise ExperimentError("kev_invalid_choice")
    probabilities = value.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(candidates):
        raise ExperimentError("kev_invalid_choice")
    if not isinstance(value.get("choice"), str) or value["choice"] not in candidates:
        raise ExperimentError("kev_invalid_choice")
    numbers = [*probabilities.values(), value.get("confidence")]
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in numbers):
        raise ExperimentError("kev_invalid_choice")
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.02):
        raise ExperimentError("kev_invalid_choice")
    if probabilities[value["choice"]] < max(probabilities.values()) - 1e-6:
        raise ExperimentError("kev_invalid_choice")
    # Kev uses normalized confidence; it need not equal the selected probability.
    return {
        "type": "choice",
        "choice": value["choice"],
        "confidence": value["confidence"],
        "probabilities": dict(probabilities),
    }


class BrainController:
    def __init__(self, budget, config, *, allow_model_calls=False):
        self.provider = Provider("deepseek", budget, config, allow_model_calls=allow_model_calls)
        self.notes = ""

    def public_config(self):
        return {"controller": "brain", "providers": {"deepseek": self.provider.public_config()}}

    def reset(self):
        self.notes = ""

    def decide(self, packet):
        response = self.provider.chat(
            _prompt("brain"),
            {
                "packet": packet,
                "private_notes": {"source": "model_hypothesis", "text": self.notes},
            },
            packet,
        )
        data = response.data
        if set(data) != {"button", "notes"} or data.get("button") not in ACTIONS:
            raise ExperimentError("deepseek_invalid_action")
        if not isinstance(data["notes"], str) or len(data["notes"]) > 600:
            raise ExperimentError("deepseek_invalid_notes")
        self.notes = data["notes"]
        return {
            "button": data["button"],
            "owner": "deepseek",
            "source": "model",
            "request_id": response.request_id,
            "model": response.model,
            "private_notes": {"source": "model_hypothesis", "text": self.notes},
        }


class HybridController:
    def __init__(self, budget, config, *, allow_model_calls=False):
        self.planner = Provider("deepseek", budget, config, allow_model_calls=allow_model_calls)
        self.actor = Provider("kev", budget, config, allow_model_calls=allow_model_calls)
        cfg = config.get("hybrid", {})
        self.max_ttl = cfg.get("max_plan_ttl", 12)
        self.max_replans = cfg.get("max_replans_per_decision", 1)
        if type(self.max_ttl) is not int or not 1 <= self.max_ttl <= 64:
            raise ExperimentError("invalid_max_plan_ttl")
        if type(self.max_replans) is not int or not 0 <= self.max_replans <= 2:
            raise ExperimentError("invalid_max_replans")
        self.reset()

    def public_config(self):
        return {
            "controller": "hybrid",
            "providers": {
                "deepseek": self.planner.public_config(),
                "kev": self.actor.public_config(),
            },
            "max_plan_ttl": self.max_ttl,
            "max_replans_per_decision": self.max_replans,
        }

    def reset(self):
        self.plan = None
        self.remaining = 0
        self.plan_request_id = None
        self.plan_model = None

    def _plan(self, packet, rejected=None):
        response = self.planner.chat(
            _prompt("planner"),
            {
                "packet": packet,
                "max_plan_ttl": self.max_ttl,
                "previous_plan": {"source": "model_hypothesis", "value": self.plan},
                "plan_review": {"source": "model_hypothesis", "judgment": rejected},
            },
            packet,
        )
        self.plan = validate_plan(response.data, self.max_ttl)
        self.remaining = self.plan["ttl"]
        self.plan_request_id, self.plan_model = response.request_id, response.model

    def decide(self, packet):
        if self.plan is None or self.remaining <= 0:
            self._plan(packet)
        discarded = []
        for attempt in range(self.max_replans + 1):
            response = self.actor.choices(
                {
                    "packet": packet,
                    "proposed_plan": {"source": "model_hypothesis", "value": self.plan},
                },
                {
                    "button": {
                        "type": "choice",
                        "instructions": _prompt("actor"),
                        "criteria": BUTTON_CRITERIA,
                    },
                    "plan_fit": {
                        "type": "choice",
                        "instructions": _prompt("actor"),
                        "criteria": PLAN_FIT,
                    },
                },
                packet,
            )
            answers = response.data.get("answers")
            if not isinstance(answers, dict) or set(answers) != {"button", "plan_fit"}:
                raise ExperimentError("kev_invalid_response")
            button = validate_choice(answers["button"], ACTIONS)
            fit = validate_choice(answers["plan_fit"], PLAN_FIT)
            if fit["choice"] == "applicable":
                self.remaining -= 1
                return {
                    "button": button["choice"],
                    "owner": "kev",
                    "source": "model",
                    "request_id": response.request_id,
                    "model": response.model,
                    "choice": button,
                    "plan_fit": fit,
                    "plan": {
                        "source": "model_hypothesis",
                        "value": deepcopy(self.plan),
                        "id": digest(self.plan),
                        "request_id": self.plan_request_id,
                        "owner": "deepseek",
                        "model": self.plan_model,
                    },
                    "discarded_requests": discarded,
                }
            discarded.append(response.request_id)
            self.remaining = 0
            if attempt == self.max_replans:
                raise ExperimentError("plan_not_applicable")
            # The old simultaneous button is never returned, cached, or executed.
            self._plan(packet, rejected=fit["choice"])
        raise ExperimentError("plan_not_applicable")


def build_controller(name, budget, config, allow_model_calls=False):
    if name == "brain":
        return BrainController(budget, config, allow_model_calls=allow_model_calls)
    if name == "hybrid":
        return HybridController(budget, config, allow_model_calls=allow_model_calls)
    raise ExperimentError("unknown_model_controller")
