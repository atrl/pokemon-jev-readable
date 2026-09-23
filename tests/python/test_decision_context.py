"""Input/loop integration checks with explicit test doubles, no API or gameplay."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from _paths import ROOT, POKEMON
from jev import BUTTONS
from assisted_helpers import build_request, observation_for_model
from progress import ProgressTracker
from run import restore_progress
from assisted_helpers import run


def world_state(x=3, mode="overworld", verified=True):
    return {
        "game": "OFFLINE TEST DOUBLE",
        "errors": [],
        "player": {
            "map_id": 38,
            "x": x,
            "y": 6,
            "name": "TEST",
            "facing": "up",
            "facing_quality": "verified_direction_response",
        },
        "scene": {"mode": mode, "verified": verified, "source": "test fixture"},
        "dialog": {
            "open": mode == "dialog",
            "awaiting_input": mode == "dialog",
            "text": "TEST" if mode == "dialog" else "",
        },
        "screen_text": {"rows": [""] * 18},
        "party": [],
        "bag": [],
        "local_map": {
            "verified": verified,
            "quality": "advisory_background_only",
            "neighbors": {
                "up": {"background_passable": False},
                "right": {"background_passable": True},
            },
        },
    }


class DecisionContextTests(unittest.TestCase):
    def test_verified_blank_world_reaches_model_with_geometry_and_all_buttons(self):
        before = world_state()
        tracker = ProgressTracker()
        for _ in range(24):
            tracker.record("a", before, before)
        before["progress"] = tracker.context(before)
        request = build_request(before, "Explore", [])
        self.assertEqual(request["state"]["game"]["scene"]["mode"], "overworld")
        self.assertIs(request["state"]["game"]["dialog"]["open"], False)
        self.assertEqual(request["state"]["game"]["screen_text"]["rows"], [])
        self.assertTrue(request["state"]["feedback"]["loop_detected"])
        self.assertEqual(request["state"]["feedback"]["same_position_steps"], 24)
        self.assertEqual(set(request["questions"]["button"]["criteria"]), set(BUTTONS))
        self.assertFalse(
            request["questions"]["button"]["criteria"]["up"]["background_neighbor"][
                "background_passable"
            ]
        )
        self.assertTrue(
            request["questions"]["button"]["criteria"]["right"]["background_neighbor"][
                "background_passable"
            ]
        )

    def test_request_carries_three_chronological_action_aligned_states(self):
        tracker = ProgressTracker()
        for x in range(3, 8):
            tracker.record("right", world_state(x), world_state(x + 1))
        current = world_state(8)
        current["progress"] = tracker.context(current)
        state = build_request(current, "Explore", [])["state"]
        transitions = state["temporal_context"]["transitions"]
        self.assertEqual(len(transitions), 3)
        self.assertEqual([row["step"] for row in transitions], [3, 4, 5])
        self.assertEqual(transitions[-1]["before"]["position"]["x"], 7)
        self.assertEqual(transitions[-1]["after"]["position"]["x"], 8)
        self.assertNotIn("recent_transitions", state["feedback"])
        self.assertEqual(state["recent_actions"][-1]["result"], "new_tile")

    def test_background_tile_letters_do_not_become_world_dialog(self):
        raw = world_state()
        raw["screen_text"]["rows"] = ["FAKE BACKGROUND LETTERS"]
        model = observation_for_model(raw)
        self.assertEqual(model["screen_text"]["rows"], [])
        self.assertIs(model["dialog"]["open"], False)
        raw = world_state(mode="dialog")
        raw["screen_text"]["rows"] = ["FAKE UPPER BACKGROUND", "Actual text"]
        raw["dialog"]["text"] = "Actual text"
        self.assertEqual(observation_for_model(raw)["screen_text"]["rows"], ["Actual text"])

    def test_unverified_mode_geometry_and_facing_are_not_promoted(self):
        raw = world_state(verified=False)
        raw["player"]["facing_quality"] = "needs_data"
        model = observation_for_model(raw)
        self.assertEqual(model["scene"]["mode"], "unknown")
        self.assertIsNone(model["dialog"]["open"])
        self.assertIsNone(model["local_map"])
        self.assertNotIn("facing", model["player"])

    def test_battle_intro_pauses_navigation_without_inventing_combatants(self):
        raw = world_state(mode="battle")
        raw["screen_text"]["rows"] = ["Wild PIDGEY", "appeared!"]
        raw["battle"] = {
            "active": True,
            "verified": False,
            "phase_verified": True,
            "phase": "text_before_combatants_ready",
            "combatants_ready": False,
            "menu": "text_or_animation",
            "visible_text": "Wild PIDGEY appeared!",
            "player": {"uninitialized": True},
        }
        raw["campaign"] = {"active_objective": {"intent": "Deliver the parcel"}}
        request = build_request(raw, "Complete the story", [])
        game = request["state"]["game"]
        self.assertEqual(game["scene"]["mode"], "battle")
        self.assertTrue(game["battle"]["phase_verified"])
        self.assertEqual(game["battle"]["menu"], "text_or_animation")
        self.assertNotIn("player", game["battle"])
        self.assertTrue(request["state"]["current_focus"].startswith("Resolve the current battle"))
        self.assertEqual(set(request["questions"]["button"]["criteria"]), set(BUTTONS))

    def test_trainer_intro_candidates_explain_acknowledgement_before_initialization(self):
        raw = world_state(mode="battle")
        raw["dialog"] = {"open": None, "awaiting_input": None, "text": None}
        raw["battle"] = {
            "active": True,
            "verified": False,
            "phase_verified": True,
            "phase": "text_before_combatants_ready",
            "combatants_ready": False,
            "menu": "text_or_animation",
            "visible_text": "JR.TRAINER♂ JERRY\n\nwants to fight!",
        }
        request = build_request(raw, "Win a badge", [])
        self.assertEqual(
            request["state"]["game"]["battle"]["input_guidance"]["suggested_button"], "a"
        )
        self.assertIn(
            "before combatant initialization", request["questions"]["button"]["criteria"]["a"]
        )
        self.assertIn(
            "missing combatant data is not a reason",
            request["questions"]["button"]["criteria"]["wait"],
        )
        self.assertNotIn("player", request["state"]["game"]["battle"])
        self.assertEqual(set(request["questions"]["button"]["criteria"]), set(BUTTONS))
        raw["battle"]["phase_verified"] = False
        self.assertNotIn(
            "input_guidance", build_request(raw, "Win a badge", [])["state"]["game"]["battle"]
        )

    def test_checkpoint_memory_requires_the_same_state_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "last.state"
            state.write_bytes(b"offline state")
            manifest = {
                "rom_sha1": "test-only",
                "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                "step": 1,
            }
            tracker = ProgressTracker()
            tracker.record("right", world_state(), world_state(4))
            sidecar = state.parent / "last.progress.json"
            sidecar.write_text(json.dumps({**manifest, "tracker": tracker.snapshot()}))
            restored, source = restore_progress(state, manifest)
            self.assertEqual(source, "verified_checkpoint")
            self.assertEqual(restored.context(world_state(4)), tracker.context(world_state(4)))
            restored, source = restore_progress(
                state, {**manifest, "state_sha256": "another-state"}
            )
            self.assertEqual(source, "new_memory")
            self.assertEqual(restored.total_steps, 0)

    def test_legacy_effect_replay_stops_at_the_saved_step(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "last.state"
            rows = [
                {
                    "type": "result",
                    "step": step,
                    "success": True,
                    "button": "a",
                    "result": {"before": world_state(), "after": world_state()},
                }
                for step in range(1, 25)
            ]
            (state.parent / "events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
            tracker, source = restore_progress(
                state, {"rom_sha1": "test-only", "state_sha256": "test-only", "step": 12}
            )
            self.assertEqual(source, "legacy_observed_effects:12")
            self.assertEqual(tracker.total_steps, 12)
            self.assertTrue(tracker.context(world_state())["loop_detected"])

    def test_verified_repetitive_run_stops_and_saves_instead_of_spending_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            world = Mock()
            world.save.return_value = b"OFFLINE STATE"
            reader = Mock()
            reader.snapshot.side_effect = lambda: world_state()
            observed_contexts = []

            def choose(before, *args, **kwargs):
                observed_contexts.append(before["progress"])
                return {"answer": {"choice": "a"}, "source": "OFFLINE TEST DOUBLE"}

            with (
                patch.dict("os.environ", {"TYPESAFE_API_KEY": "fixture-only-not-real"}),
                patch("run.Emulator", return_value=world),
                patch("run.Reader", return_value=reader),
                patch("run.choose", side_effect=choose),
            ):
                report = run(
                    Path("TEST.gb"),
                    output,
                    goal="Explore",
                    steps=100,
                    max_stalled_steps=12,
                    max_recovery_attempts=0,
                )
            self.assertEqual(report["status"], "stalled")
            self.assertEqual(report["executed_actions"], 12)
            self.assertEqual(report["new_tiles_this_run"], 0)
            self.assertEqual(world.press.call_count, 12)
            self.assertEqual(observed_contexts[-1]["same_position_steps"], 11)
            saved = json.loads((output / "last.progress.json").read_text())
            self.assertEqual(saved["step"], 12)
            self.assertEqual(
                saved["state_sha256"],
                hashlib.sha256((output / "last.state").read_bytes()).hexdigest(),
            )
            rows = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
            self.assertTrue(
                all(not row["outcome"]["world_progress"] for row in rows if row["type"] == "result")
            )
            self.assertEqual(rows[-1]["status"], "stalled")

    def test_true_no_effect_loop_gets_bounded_replanning_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            world = Mock()
            world.save.return_value = b"OFFLINE RECOVERY STATE"
            reader = Mock()
            reader.snapshot.side_effect = lambda: world_state()
            hints = []

            def choose(before, *args, **kwargs):
                hints.append(before["campaign"].get("recovery"))
                return {"answer": {"choice": "a"}, "source": "OFFLINE TEST DOUBLE"}

            with (
                patch.dict("os.environ", {"TYPESAFE_API_KEY": "fixture-only-not-real"}),
                patch("run.Emulator", return_value=world),
                patch("run.Reader", return_value=reader),
                patch("run.choose", side_effect=choose),
            ):
                report = run(
                    Path("TEST.gb"),
                    output,
                    goal="Explore",
                    steps=100,
                    max_stalled_steps=12,
                    max_recovery_attempts=3,
                )
            rows = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
            self.assertEqual(report["status"], "stalled")
            self.assertEqual(report["executed_actions"], 48)
            self.assertEqual([r["attempt"] for r in rows if r["type"] == "recovery"], [1, 2, 3])
            self.assertEqual(report["recovery_count"], 3)
            self.assertEqual(world.press.call_count, 48)  # only actual mocked JEV choices
            self.assertTrue(any(h and h["attempt"] == 3 for h in hints))


if __name__ == "__main__":
    unittest.main()
