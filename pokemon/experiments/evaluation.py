"""Independent task acceptance and bounded, deduplicated game rewards."""

from __future__ import annotations

import math
from copy import deepcopy

from perception import point

SUCCESS_TYPES = frozenset(
    {
        "map_changed",
        "map_entered",
        "battle_finished",
        "dialog_closed",
        "scene_changed",
        "party_grew",
        "badge_gained",
        "game_completed",
    }
)
DEFAULT_REWARD = {
    "task": 10.0,
    "badge": 5.0,
    "exploration": 0.01,
    "action_cost": 0.001,
    "exploration_limit": 100,
}


def validate_success(success: dict) -> dict:
    if not isinstance(success, dict) or success.get("type") not in SUCCESS_TYPES:
        raise ValueError("Unsupported independent success predicate")
    allowed = {"type", "map_id"} if success["type"] == "map_entered" else {"type"}
    if set(success) != allowed:
        raise ValueError("Success predicate has missing or unexpected fields")
    if success["type"] == "map_entered" and (
        type(success["map_id"]) is not int or not 0 <= success["map_id"] <= 255
    ):
        raise ValueError("map_entered requires a verified map_id in 0..255")
    return deepcopy(success)


def _fact(raw: dict, name: str):
    row = (raw.get("milestones") or {}).get(name) or {}
    return row.get("value") if row.get("verified") is True else None


def _badges(raw: dict):
    value = _fact(raw, "badge_bits")
    return value if type(value) is int and 0 <= value <= 255 else None


class Evaluator:
    """Only this object receives hidden evaluator facts; its specification stays private."""

    def __init__(self, success: dict, reward: dict | None = None):
        self.success_spec = validate_success(success)
        self.weights = {**DEFAULT_REWARD, **(reward or {})}
        if set(self.weights) != set(DEFAULT_REWARD):
            raise ValueError("Unknown reward setting")
        for name, value in self.weights.items():
            if name == "exploration_limit":
                if type(value) is not int or not 0 <= value <= 10000:
                    raise ValueError("exploration_limit must be an integer in 0..10000")
            elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Reward weights must be finite nonnegative numbers")
        self.steps = 0
        self.success = False

    def reset(self, raw: dict, observation: dict) -> None:
        self.steps = 0
        self.success = False
        self.initial = deepcopy(observation)
        self.initial_point = point(observation)
        self.initial_party_count = _fact(raw, "party_count")
        self.badges_seen = _badges(raw)
        self.initial_badges = self.badges_seen
        self.visited = set()
        if self.initial_point and observation["scene"]["mode"] == "overworld":
            self.visited.add(tuple(self.initial_point))
        self.exploration_awards = 0
        self.return_total = 0.0
        kind = self.success_spec["type"]
        if kind in {"map_changed", "map_entered"} and self.initial_point is None:
            raise ValueError("Task requires a verified initial map and position")
        if kind == "map_entered" and self.initial_point[0] == self.success_spec["map_id"]:
            raise ValueError("Initial checkpoint already satisfies map_entered")
        if kind == "battle_finished" and not self._active_battle(observation):
            raise ValueError("battle_finished requires an active verified battle at reset")
        if kind == "dialog_closed" and not self._open_dialog(observation):
            raise ValueError("dialog_closed requires a verified open dialogue at reset")
        if kind == "game_completed" and _fact(raw, "game_completed") is True:
            raise ValueError("Initial checkpoint already satisfies game_completed")
        if kind == "party_grew" and type(self.initial_party_count) is not int:
            raise ValueError("party_grew requires a verified initial party count")
        if kind == "badge_gained" and self.initial_badges is None:
            raise ValueError("badge_gained requires verified initial badge bits")
        if kind == "scene_changed" and observation.get("scene", {}).get("verified") is not True:
            raise ValueError("scene_changed requires a verified initial scene")

    @staticmethod
    def _active_battle(observation):
        battle = observation.get("battle") or {}
        return battle.get("active") is True and battle.get("verified") is True

    @staticmethod
    def _open_dialog(observation):
        return (
            observation.get("scene", {}).get("verified") is True
            and observation.get("dialog", {}).get("open") is True
        )

    def _completed(self, raw: dict, observation: dict) -> bool:
        kind = self.success_spec["type"]
        current = point(observation)
        scene = observation.get("scene") or {}
        if kind == "map_changed":
            return bool(current and current[0] != self.initial_point[0])
        if kind == "map_entered":
            return bool(current and current[0] == self.success_spec["map_id"])
        if kind == "battle_finished":
            battle = observation.get("battle") or {}
            return battle.get("verified") is True and battle.get("active") is False
        if kind == "dialog_closed":
            return (
                scene.get("verified") is True and observation.get("dialog", {}).get("open") is False
            )
        if kind == "scene_changed":
            return (
                scene.get("verified") is True and scene.get("mode") != self.initial["scene"]["mode"]
            )
        if kind == "party_grew":
            count = _fact(raw, "party_count")
            return type(count) is int and count > self.initial_party_count
        if kind == "badge_gained":
            badges = _badges(raw)
            return badges is not None and bool(badges & ~self.initial_badges)
        return _fact(raw, "game_completed") is True

    def step(self, raw: dict, observation: dict, button: str) -> tuple[float, bool, dict]:
        self.steps += 1
        components = {
            "task": 0.0,
            "badge": 0.0,
            "exploration": 0.0,
            "action_cost": -float(self.weights["action_cost"]),
        }
        current_badges = _badges(raw)
        new_badges = 0
        if current_badges is not None:
            if self.badges_seen is not None:
                new_badges = (current_badges & ~self.badges_seen).bit_count()
                self.badges_seen |= current_badges
            else:
                self.badges_seen = current_badges
        components["badge"] = new_badges * self.weights["badge"]
        current = point(observation)
        new_tile = bool(
            current
            and observation["scene"]["mode"] == "overworld"
            and tuple(current) not in self.visited
        )
        if new_tile:
            self.visited.add(tuple(current))
            if self.exploration_awards < self.weights["exploration_limit"]:
                self.exploration_awards += 1
                components["exploration"] = self.weights["exploration"]
        if not self.success and self._completed(raw, observation):
            self.success = True
            components["task"] = self.weights["task"]
        reward = float(sum(components.values()))
        self.return_total += reward
        info = {
            "success": self.success,
            "steps": self.steps,
            "reward_components": components,
            "return": self.return_total,
            "new_badges": new_badges,
            "first_visit": new_tile,
            "visited_positions": len(self.visited),
            "exploration_awards": self.exploration_awards,
            "evaluation_scope": "independent_task_predicate_not_necessarily_game_completion",
        }
        return reward, self.success, info
