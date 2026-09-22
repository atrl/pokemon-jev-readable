"""Bounded observation memory. This module records facts; it never selects inputs."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib


DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
MAX_VISITED = 20_000
MAX_POSITIONS = 2_048
MAX_INTERACTIONS = 256
MAX_TEXTS = 16
MAX_RECENT = 8
MAX_TRANSITIONS = 3


def _position(observation):
    world = observation.get("world") or {}
    if world.get("source_match") is False or world.get("player_position_valid") is False:
        return None
    player = observation.get("player") or {}
    values = tuple(player.get(key) for key in ("map_id", "x", "y"))
    return values if all(type(value) is int and value >= 0 for value in values) else None


def _key(position):
    return ":".join(map(str, position)) if position is not None else None


def _dialog(observation):
    value = observation.get("dialog") or (observation.get("scene") or {}).get("dialog") or {}
    return value if isinstance(value, dict) else {}


def _open(observation):
    value = _dialog(observation).get("open")
    return value if type(value) is bool else None


def _text(observation):
    scene = observation.get("scene") or {}
    if scene.get("verified") is True and scene.get("mode") == "overworld":
        return ""  # Background tile IDs are not conversation text.
    dialog_text = _dialog(observation).get("text")
    if isinstance(dialog_text, str) and (dialog_text or _open(observation) is True):
        return " ".join(dialog_text.split())[:2000]
    rows = (observation.get("screen_text") or {}).get("rows") or []
    return " ".join(" ".join(str(row) for row in rows).split())[:2000]


def _fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()[:24] if text else None


def _site(observation):
    position = _key(_position(observation))
    facing = (observation.get("player") or {}).get("facing")
    return f"{position}|{facing or 'unknown'}" if position is not None else None


def _trim(mapping, limit):
    while len(mapping) > limit:
        mapping.popitem(last=False)


def _compact_observation(observation):
    """Keep action-aligned facts without promoting raw RAM or unknown phases."""
    position = _position(observation)
    scene = observation.get("scene") or {}
    verified = scene.get("verified") is True and scene.get("mode") in (
        "overworld",
        "dialog",
        "main_menu",
        "battle",
        "name_entry",
        "species_preview",
    )
    dialog = _dialog(observation) if verified else {}
    opened = dialog.get("open")
    awaiting_input = dialog.get("awaiting_input")
    text = dialog.get("text")
    frame = observation.get("frame")
    compact = {
        "position": dict(zip(("map_id", "x", "y"), position)) if position is not None else None,
        "scene": {"mode": scene["mode"] if verified else "unknown", "verified": verified},
        "dialog": {
            "open": opened if type(opened) is bool else None,
            "awaiting_input": awaiting_input if type(awaiting_input) is bool else None,
            "text": " ".join(text.split())[:180] if isinstance(text, str) else None,
        },
        "frame": frame if type(frame) is int and frame >= 0 else None,
    }
    player = observation.get("player") or {}
    if (
        player.get("facing_quality") == "verified_direction_response"
        and player.get("facing") in DIRECTIONS
    ):
        compact["facing"] = player["facing"]
    return compact


class ProgressTracker:
    def __init__(self, snapshot: dict | None = None):
        data = snapshot or {}
        if data.get("version", 1) != 1:
            raise ValueError("Unsupported progress snapshot version")
        self.visited = OrderedDict(deepcopy(data.get("visited", [])[-MAX_VISITED:]))
        self.positions = OrderedDict(deepcopy(data.get("positions", [])[-MAX_POSITIONS:]))
        self.interactions = OrderedDict(deepcopy(data.get("interactions", [])[-MAX_INTERACTIONS:]))
        self.same_position_steps = int(data.get("same_position_steps", 0))
        self.steps_since_new_tile = int(data.get("steps_since_new_tile", 0))
        self.total_steps = int(data.get("total_steps", 0))
        self.stationary_buttons = deepcopy(data.get("stationary_buttons", {}))
        self.last_position = data.get("last_position")
        self.recent_effects = deepcopy(data.get("recent_effects", [])[-MAX_RECENT:])
        self.recent_transitions = deepcopy(data.get("recent_transitions", [])[-MAX_TRANSITIONS:])
        self.active_dialog = deepcopy(data.get("active_dialog"))

    def _visit(self, key):
        if key is None:
            return False
        novel = key not in self.visited
        self.visited[key] = self.visited.get(key, 0) + 1
        self.visited.move_to_end(key)
        _trim(self.visited, MAX_VISITED)
        return novel

    def _position_info(self, key):
        if key is None:
            return None
        if key not in self.positions:
            self.positions[key] = {"directions": {}, "texts": {}}
        self.positions.move_to_end(key)
        _trim(self.positions, MAX_POSITIONS)
        return self.positions[key]

    def _observe_dialog_text(self, observation):
        if self.active_dialog is None:
            return
        text = _text(observation)
        if not text:
            return
        fingerprint = _fingerprint(text)
        self.active_dialog["fingerprint"] = fingerprint
        signature = f"{self.active_dialog['site']}|{fingerprint}"
        previous = self.interactions.get(signature)
        # Exact text repeated in a later explicitly opened episode is evidence
        # of recurrence. Repeated frames inside one episode are not reopens.
        if previous and previous["completed"] and signature not in self.active_dialog["matches"]:
            previous["reopens"] += 1
            self.active_dialog["matches"].append(signature)
            self.active_dialog["matches"] = self.active_dialog["matches"][-MAX_TEXTS:]
            self.interactions.move_to_end(signature)

    def _dialog_effect(self, before, after):
        before_open, after_open = _open(before), _open(after)
        before_site, after_site = _site(before), _site(after)
        if (
            before_open is True
            and before_site
            and (self.active_dialog is None or self.active_dialog["site"] != before_site)
        ):
            self.active_dialog = {"site": before_site, "fingerprint": None, "matches": []}
            self._observe_dialog_text(before)
        opened = before_open is False and after_open is True
        closed = before_open is True and after_open is False
        if after_open is True and after_site:
            if opened or self.active_dialog is None or self.active_dialog["site"] != after_site:
                self.active_dialog = {"site": after_site, "fingerprint": None, "matches": []}
            self._observe_dialog_text(after)
        elif closed and self.active_dialog:
            self._observe_dialog_text(before)
            fingerprint = self.active_dialog.get("fingerprint")
            if fingerprint:
                signature = f"{self.active_dialog['site']}|{fingerprint}"
                entry = self.interactions.setdefault(
                    signature,
                    {
                        "site": self.active_dialog["site"],
                        "fingerprint": fingerprint,
                        "completed": 0,
                        "reopens": 0,
                    },
                )
                entry["completed"] += 1
                self.interactions.move_to_end(signature)
                _trim(self.interactions, MAX_INTERACTIONS)
            self.active_dialog = None
        elif after_open is not True:
            # Missing phase information cannot establish a close transition.
            self.active_dialog = None
        return opened, closed

    def record(self, button, before, after) -> dict:
        before_position, after_position = _position(before), _position(after)
        before_key, after_key = _key(before_position), _key(after_position)
        # Observe the initial position before judging the first action: a first
        # stationary press does not discover a tile merely by starting a run.
        if before_key is not None and before_key != self.last_position:
            self._visit(before_key)
            self.same_position_steps = 0
            self.stationary_buttons = {}
        position_changed = (
            before_position is not None
            and after_position is not None
            and before_position != after_position
        )
        map_changed = (
            before_position is not None
            and after_position is not None
            and before_position[0] != after_position[0]
        )
        new_tile = self._visit(after_key)
        stationary = before_key is not None and before_key == after_key
        self.same_position_steps = self.same_position_steps + 1 if stationary else 0
        if stationary:
            self.stationary_buttons[button] = self.stationary_buttons.get(button, 0) + 1
        else:
            self.stationary_buttons = {}
        self.steps_since_new_tile = 0 if new_tile else self.steps_since_new_tile + 1
        self.total_steps += 1
        before_info = self._position_info(before_key)
        before_scene = before.get("scene") or {}
        if (
            button in DIRECTIONS
            and before_info is not None
            and before_scene.get("verified") is True
            and before_scene.get("mode") == "overworld"
        ):
            attempts = before_info["directions"].setdefault(
                button,
                {
                    "moved": 0,
                    "blocked_or_turn_only": 0,
                    "unknown": 0,
                },
            )
            effect = (
                "moved" if position_changed else "blocked_or_turn_only" if stationary else "unknown"
            )
            attempts[effect] += 1
        after_info = self._position_info(after_key)
        text_fingerprint = _fingerprint(_text(after))
        if after_info is not None and text_fingerprint:
            texts = after_info["texts"]
            texts[text_fingerprint] = texts.get(text_fingerprint, 0) + 1
            while len(texts) > MAX_TEXTS:
                del texts[next(iter(texts))]
        opened, closed = self._dialog_effect(before, after)
        outcome = {
            "position_changed": position_changed,
            "map_changed": map_changed,
            "new_tile": new_tile,
            "dialog_opened": opened,
            "dialog_closed": closed,
            "text_changed": _text(before) != _text(after),
            # Dialog or UI changes may be useful but cannot prove a new world
            # milestone without an independently validated event reader.
            "world_progress": new_tile,
        }
        self.recent_effects.append(
            {"button": button, "before": before_key, "after": after_key, **outcome}
        )
        self.recent_effects = self.recent_effects[-MAX_RECENT:]
        self.recent_transitions.append(
            {
                "step": self.total_steps,
                "button": button,
                "before": _compact_observation(before),
                "after": _compact_observation(after),
                "outcome": deepcopy(outcome),
            }
        )
        self.recent_transitions = self.recent_transitions[-MAX_TRANSITIONS:]
        self.last_position = after_key
        return outcome

    def context(self, observation) -> dict:
        position = _position(observation)
        key = _key(position)
        info = self.positions.get(key, {"directions": {}, "texts": {}})
        repeated_text = max(info["texts"].values(), default=0)
        repeated_interactions = sum(
            entry["reopens"]
            for entry in self.interactions.values()
            if entry["site"] == _site(observation)
        )
        same_position = (
            self.same_position_steps if key == self.last_position and key is not None else 0
        )
        repetitive_buttons = self.stationary_buttons.get("a", 0) + self.stationary_buttons.get(
            "wait", 0
        )
        stationary_loop = same_position >= 12 and (
            repetitive_buttons >= 8 or repeated_text >= 3 or repeated_interactions > 0
        )
        recent_destinations = [effect.get("after") for effect in self.recent_effects]
        moving_cycle = (
            self.steps_since_new_tile >= 24
            and len(self.recent_effects) == MAX_RECENT
            and all(destination is not None for destination in recent_destinations)
            and all(not effect.get("new_tile") for effect in self.recent_effects)
            and sum(bool(effect.get("position_changed")) for effect in self.recent_effects) >= 6
            and len(set(recent_destinations)) <= 4
        )
        loop = stationary_loop or moving_cycle
        scene = observation.get("scene") or {}
        mode = scene.get("mode") if scene.get("verified") is True else "unknown"
        if moving_cycle and mode == "overworld":
            loop_kind = "position_cycle"
        elif stationary_loop and repeated_interactions and mode in ("overworld", "dialog"):
            loop_kind = "repeated_interaction"
        elif stationary_loop and mode == "main_menu":
            loop_kind = "menu_cycle"
        elif stationary_loop:
            loop_kind = "stationary_repetition"
        else:
            loop_kind = None
        if mode == "dialog" and loop:
            focus = "Resolve the repeated dialog, then explore an untried neighbor when the dialog is closed."
        elif mode == "dialog":
            focus = "Resolve the current dialog and check whether it closes or changes; preserve the overall goal."
        elif mode == "main_menu":
            focus = "Use the visible menu toward the overall goal, or leave it when no menu task is needed."
        elif mode == "overworld" and loop:
            focus = "Explore an untried neighbor and compare the result; avoid reopening the same completed interaction."
        elif mode == "overworld":
            focus = "Explore toward the overall goal using observed local information and prior movement outcomes."
        else:
            focus = "Identify the current phase from available evidence; blank text alone does not justify waiting."
        neighbors = {}
        if position is not None:
            map_id, x, y = position
            neighbors = {
                direction: self.visited.get(_key((map_id, x + dx, y + dy)), 0)
                for direction, (dx, dy) in DIRECTIONS.items()
            }
        return {
            "loop_detected": loop,
            "loop_kind": loop_kind,
            "same_position_steps": same_position,
            "steps_since_new_tile": self.steps_since_new_tile,
            "untried_directions": [
                direction for direction in DIRECTIONS if direction not in info["directions"]
            ],
            "direction_outcomes": deepcopy(info["directions"]),
            "neighbor_visits": neighbors,
            "recent_effects": deepcopy(self.recent_effects),
            "recent_transitions": deepcopy(self.recent_transitions),
            "current_focus": focus,
            "visited_tiles": len(self.visited),
            "repeated_interactions": repeated_interactions,
            "repeated_text_observations": repeated_text,
            "total_steps": self.total_steps,
            "limitations": [
                "World progress means a newly observed coordinate, not verified story completion.",
                "Stationary dialog can be legitimate; loop_detected is a symptom, not proof of failure.",
                "Visits and direction attempts are bounded; zero/untried means absent from retained records, not necessarily never visited/tried.",
                "A direction with no movement may be blocked, turn-only, or unavailable in the current phase.",
            ],
        }

    def snapshot(self) -> dict:
        return deepcopy(
            {
                "version": 1,
                "visited": list(self.visited.items()),
                "positions": list(self.positions.items()),
                "interactions": list(self.interactions.items()),
                "same_position_steps": self.same_position_steps,
                "steps_since_new_tile": self.steps_since_new_tile,
                "total_steps": self.total_steps,
                "stationary_buttons": self.stationary_buttons,
                "last_position": self.last_position,
                "recent_effects": self.recent_effects,
                "active_dialog": self.active_dialog,
                "recent_transitions": self.recent_transitions,
            }
        )
