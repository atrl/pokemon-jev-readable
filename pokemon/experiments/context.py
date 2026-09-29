"""One episode's observed facts, shared by every controller architecture."""

from __future__ import annotations

from copy import deepcopy

from experience import Experience
from perception import point, project


class EpisodeMemory:
    """Start empty; never restore campaign plans, model notes or another arm's history."""

    def __init__(self):
        self.experience = Experience()

    def observe(self, observation: dict) -> None:
        self.experience.observe(project(observation))

    def record(self, button: str, before: dict, after: dict) -> dict:
        return self.experience.record(button, project(before), project(after))

    def context(self, observation: dict) -> dict:
        game = project(observation)
        memory = self.experience
        current = point(game)
        current_map = current[0] if current else None
        # Retrieve older dialogue from the current map as well as the recent tail.
        relevant = [
            row
            for row in memory.dialogues
            if row.get("position") and row["position"][0] == current_map
        ][-24:]
        selected = {row["ref"]: row for row in [*relevant, *memory.dialogues[-16:]]}
        dialogues = sorted(selected.values(), key=lambda row: row["last_seen_step"])
        maps = []
        for map_id, record in sorted(memory.maps.items(), key=lambda item: int(item[0])):
            maps.append(
                {
                    "map_id": int(map_id),
                    "geometry": deepcopy(record.get("geometry", {})),
                    "cells": [
                        [cell["x"], cell["y"], cell["glyph"]]
                        for cell in sorted(
                            record["cells"].values(), key=lambda cell: (cell["y"], cell["x"])
                        )
                    ],
                    "cell_format": ["x", "y", "observed_background_glyph"],
                    "objects": deepcopy(record["objects"]),
                    "portals": deepcopy(record["portals"]),
                    "last_seen_step": record["last_seen_step"],
                }
            )
        return {
            "source": "this_episode_observed_facts_only",
            "steps": memory.steps,
            "maps": maps,
            "current_map": memory.spatial(game),
            "dialogues": deepcopy(dialogues),
            "transitions": deepcopy(memory.transitions),
            "recent_actions": deepcopy(memory.effects[-12:]),
            "retention": {
                "maps": 256,
                "cells_per_map": 12000,
                "transitions": 512,
                "stored_dialogues": 160,
                "current_map_dialogues": 24,
                "recent_dialogues": 16,
                "recent_actions": 12,
            },
            "limitations": (
                "Retained observations are not a complete world map. Objects may move; "
                "observed floor is not proof of traversability. Missing retained history "
                "does not mean an event never happened. No model hypotheses are facts."
            ),
        }
