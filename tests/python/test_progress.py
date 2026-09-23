import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _paths import ROOT, POKEMON
from progress import ProgressTracker


def observation(x=3, y=6, *, map_id=38, mode="overworld", text="", facing="up"):
    result = {
        "player": {"map_id": map_id, "x": x, "y": y, "facing": facing},
        "screen_text": {"rows": [text]},
    }
    if mode is not None:
        result["scene"] = {"mode": mode, "verified": True}
        result["dialog"] = {
            "open": mode == "dialog",
            "awaiting_input": mode == "dialog",
            "text": text,
        }
    return result


class ProgressTests(unittest.TestCase):
    def test_first_stationary_action_is_not_new_tile(self):
        tracker = ProgressTracker()
        state = observation()
        outcome = tracker.record("a", state, state)
        self.assertFalse(outcome["world_progress"])
        self.assertFalse(outcome["new_tile"])
        self.assertEqual(tracker.context(state)["same_position_steps"], 1)
        self.assertEqual(tracker.context(state)["visited_tiles"], 1)

    def test_generic_open_close_reopen_cycle_is_exposed(self):
        tracker = ProgressTracker()
        closed = observation()
        opened = observation(mode="dialog", text="Playing the N64.")
        for _ in range(6):
            first = tracker.record("a", closed, opened)
            second = tracker.record("a", opened, closed)
            self.assertTrue(first["dialog_opened"])
            self.assertTrue(second["dialog_closed"])
            self.assertFalse(first["world_progress"])
            self.assertFalse(second["world_progress"])
        context = tracker.context(closed)
        self.assertTrue(context["loop_detected"])
        self.assertEqual(context["repeated_interactions"], 5)
        self.assertEqual(context["same_position_steps"], 12)
        self.assertEqual(context["steps_since_new_tile"], 12)
        self.assertEqual(len(context["untried_directions"]), 4)
        self.assertIn("untried neighbor", context["current_focus"])

    def test_repeated_frames_inside_one_dialog_are_not_reopens(self):
        tracker = ProgressTracker()
        opened = observation(mode="dialog", text="An ongoing conversation.")
        for _ in range(15):
            tracker.record("wait", opened, opened)
        self.assertEqual(tracker.context(opened)["repeated_interactions"], 0)

    def test_non_novel_backtracking_is_movement_without_new_progress(self):
        tracker = ProgressTracker()
        left, right = observation(), observation(x=4)
        out = tracker.record("right", left, right)
        self.assertTrue(out["new_tile"])
        out = tracker.record("left", right, left)
        self.assertTrue(out["position_changed"])
        self.assertFalse(out["new_tile"])
        self.assertFalse(out["world_progress"])
        context = tracker.context(left)
        self.assertEqual(context["same_position_steps"], 0)
        self.assertEqual(context["neighbor_visits"]["right"], 1)
        self.assertNotIn("right", context["untried_directions"])
        self.assertEqual(context["direction_outcomes"]["right"]["moved"], 1)
        tracker.record("up", left, left)
        self.assertEqual(
            tracker.context(left)["direction_outcomes"]["up"]["blocked_or_turn_only"], 1
        )

    def test_repeated_moving_cycle_is_detected_without_stationary_streak(self):
        tracker = ProgressTracker()
        left, right = observation(), observation(x=4)
        for _ in range(60):
            tracker.record("right", left, right)
            tracker.record("left", right, left)
        context = tracker.context(left)
        self.assertEqual(context["same_position_steps"], 0)
        self.assertEqual(context["steps_since_new_tile"], 119)
        self.assertTrue(context["loop_detected"])

    def test_backtracking_along_a_known_path_is_not_a_small_moving_cycle(self):
        tracker = ProgressTracker()
        for x in range(3, 44):
            tracker.record("right", observation(x=x), observation(x=x + 1))
        for x in range(44, 3, -1):
            tracker.record("left", observation(x=x), observation(x=x - 1))
        context = tracker.context(observation(x=3))
        self.assertGreaterEqual(context["steps_since_new_tile"], 24)
        self.assertFalse(context["loop_detected"])

    def test_only_verified_overworld_directions_count_as_neighbor_attempts(self):
        for mode in ("main_menu", "dialog", None):
            with self.subTest(mode=mode):
                tracker = ProgressTracker()
                state = observation(mode=mode)
                tracker.record("down", state, state)
                context = tracker.context(observation())
                self.assertIn("down", context["untried_directions"])
                self.assertNotIn("down", context["direction_outcomes"])
                self.assertEqual(context["recent_effects"][-1]["button"], "down")
        tracker = ProgressTracker()
        unverified = observation()
        unverified["scene"]["verified"] = False
        tracker.record("down", unverified, unverified)
        self.assertIn("down", tracker.context(unverified)["untried_directions"])

    def test_overworld_background_glyph_changes_are_not_conversation_effects(self):
        tracker = ProgressTracker()
        before = observation(text="BACKGROUND A")
        after = observation(text="BACKGROUND B")
        outcome = tracker.record("wait", before, after)
        self.assertFalse(outcome["text_changed"])
        self.assertEqual(tracker.context(after)["repeated_text_observations"], 0)

    def test_map_id_is_part_of_coordinate_identity(self):
        tracker = ProgressTracker()
        out = tracker.record("down", observation(), observation(map_id=39))
        self.assertTrue(out["map_changed"])
        self.assertTrue(out["new_tile"])

    def test_transient_map_header_mismatch_does_not_create_position_or_progress(self):
        tracker = ProgressTracker()
        stable = observation(map_id=0, x=12, y=11)
        stable["world"] = {"source_match": True, "player_position_valid": True}
        transition = observation(map_id=40, x=12, y=11)
        transition["world"] = {"source_match": False, "player_position_valid": True}
        outcome = tracker.record("wait", stable, transition)
        self.assertFalse(outcome["position_changed"])
        self.assertFalse(outcome["map_changed"])
        self.assertFalse(outcome["new_tile"])
        self.assertFalse(outcome["world_progress"])
        self.assertNotIn("40:12:11", tracker.visited)
        self.assertIsNone(tracker.recent_transitions[-1]["after"]["position"])
        # Settling to a valid lab position is a new observation, without
        # inventing a movement delta from the stale-coordinate frame.
        arrived = observation(map_id=40, x=5, y=11)
        arrived["world"] = {"source_match": True, "player_position_valid": True}
        outcome = tracker.record("wait", transition, arrived)
        self.assertFalse(outcome["position_changed"])
        self.assertTrue(outcome["new_tile"])
        self.assertIn("40:5:11", tracker.visited)

    def test_out_of_bounds_player_cannot_add_an_explored_tile(self):
        tracker = ProgressTracker()
        invalid = observation(x=200)
        invalid["world"] = {"source_match": True, "player_position_valid": False}
        result = tracker.record("right", invalid, invalid)
        self.assertFalse(result["new_tile"])
        self.assertEqual(len(tracker.visited), 0)
        self.assertIsNone(tracker.recent_transitions[-1]["after"]["position"])

    def test_discontinuous_observation_does_not_carry_stationary_streak(self):
        tracker = ProgressTracker()
        state = observation()
        for _ in range(15):
            tracker.record("wait", state, state)
        other = observation(x=4)
        tracker.record("wait", other, other)
        self.assertEqual(tracker.context(other)["same_position_steps"], 1)
        self.assertFalse(tracker.context(other)["loop_detected"])

    def test_menu_text_change_is_recorded_without_world_progress(self):
        tracker = ProgressTracker()
        before, after = observation(mode="main_menu"), observation(mode="main_menu")
        before["screen_text"]["rows"] = ["PACK SAVE"]
        after["screen_text"]["rows"] = ["OPTIONS EXIT"]
        outcome = tracker.record("down", before, after)
        self.assertTrue(outcome["text_changed"])
        self.assertFalse(outcome["world_progress"])

    def test_empty_text_never_establishes_a_dialog_transition(self):
        tracker = ProgressTracker()
        blank = observation(mode=None)
        text = observation(mode=None, text="Some text")
        for before, after in [(blank, text), (text, blank)]:
            out = tracker.record("a", before, after)
            self.assertFalse(out["dialog_opened"])
            self.assertFalse(out["dialog_closed"])
        self.assertIn("blank text alone", tracker.context(blank)["current_focus"])
        self.assertEqual(tracker.context(blank)["repeated_interactions"], 0)

    def test_historical_phase_less_loop_uses_symptoms_not_invented_dialog(self):
        tracker = ProgressTracker()
        blank = observation(mode=None)
        text = observation(mode=None, text="Repeated text")
        for _ in range(20):
            tracker.record("a", blank, text)
            tracker.record("wait", text, blank)
        context = tracker.context(blank)
        self.assertTrue(context["loop_detected"])
        self.assertEqual(context["repeated_interactions"], 0)

    def test_different_facing_does_not_claim_same_interaction(self):
        tracker = ProgressTracker()
        closed = observation()
        opened = observation(mode="dialog", text="Hello")
        tracker.record("a", closed, opened)
        tracker.record("a", opened, closed)
        other = observation(mode="dialog", text="Hello", facing="left")
        tracker.record("a", observation(facing="left"), other)
        self.assertEqual(tracker.context(other)["repeated_interactions"], 0)

    def test_json_roundtrip_preserves_all_context_and_future_recurrence(self):
        tracker = ProgressTracker()
        closed = observation()
        opened = observation(mode="dialog", text="A stable dialog page")
        for _ in range(8):
            tracker.record("a", closed, opened)
            tracker.record("a", opened, closed)
        tracker.record("a", closed, opened)
        restored = ProgressTracker(json.loads(json.dumps(tracker.snapshot())))
        self.assertEqual(restored.context(opened), tracker.context(opened))
        self.assertEqual(len(restored.context(opened)["recent_effects"]), 8)
        for button, before, after in [("a", opened, closed), ("a", closed, opened)]:
            self.assertEqual(
                restored.record(button, before, after), tracker.record(button, before, after)
            )
        self.assertEqual(restored.context(opened), tracker.context(opened))
        self.assertEqual(
            json.loads(json.dumps(restored.snapshot())), json.loads(json.dumps(tracker.snapshot()))
        )

    def test_bounded_memory_and_context_do_not_mutate_the_tracker(self):
        with (
            patch("progress.MAX_VISITED", 4),
            patch("progress.MAX_POSITIONS", 3),
            patch("progress.MAX_INTERACTIONS", 2),
        ):
            tracker = ProgressTracker()
            for x in range(8):
                closed = observation(x=x)
                opened = observation(x=x, mode="dialog", text=f"Text {x}")
                tracker.record("a", closed, opened)
                tracker.record("a", opened, closed)
            self.assertLessEqual(len(tracker.snapshot()["visited"]), 4)
            self.assertLessEqual(len(tracker.snapshot()["positions"]), 3)
            self.assertLessEqual(len(tracker.snapshot()["interactions"]), 2)
            previous = tracker.snapshot()
            context = tracker.context(closed)
            context["recent_effects"].clear()
            self.assertEqual(previous, tracker.snapshot())

    def test_transitions_are_chronological_and_aligned_to_each_action(self):
        tracker = ProgressTracker()
        states = [
            observation(x=3),
            observation(x=4),
            observation(x=3),
            observation(x=3, map_id=39),
            observation(x=4, map_id=39),
        ]
        frames = [100, 124, 160, 184, 208]
        for state, frame in zip(states, frames):
            state["frame"] = frame
        buttons = ["right", "left", "down", "right"]
        outcomes = [
            tracker.record(button, before, after)
            for button, before, after in zip(buttons, states, states[1:])
        ]
        transitions = tracker.context(states[-1])["recent_transitions"]
        self.assertEqual([item["step"] for item in transitions], [2, 3, 4])
        self.assertEqual([item["button"] for item in transitions], buttons[1:])
        for index, transition in enumerate(transitions, 1):
            self.assertEqual(transition["before"]["position"]["x"], states[index]["player"]["x"])
            self.assertEqual(transition["after"]["position"]["x"], states[index + 1]["player"]["x"])
            self.assertEqual(transition["before"]["frame"], frames[index])
            self.assertEqual(transition["after"]["frame"], frames[index + 1])
            self.assertEqual(transition["outcome"], outcomes[index])
        self.assertEqual(transitions[0]["after"]["frame"] - transitions[0]["before"]["frame"], 36)
        self.assertTrue(transitions[1]["outcome"]["map_changed"])

    def test_transition_distinguishes_empty_dialog_blank_overworld_and_unknown(self):
        tracker = ProgressTracker()
        world, dialog, unknown = observation(), observation(mode="dialog"), observation(mode=None)
        tracker.record("a", world, dialog)
        tracker.record("a", dialog, world)
        tracker.record("wait", world, unknown)
        transitions = tracker.context(unknown)["recent_transitions"]
        self.assertEqual(transitions[0]["before"]["dialog"]["text"], "")
        self.assertEqual(transitions[0]["after"]["dialog"]["text"], "")
        self.assertIs(transitions[0]["before"]["dialog"]["open"], False)
        self.assertIs(transitions[0]["after"]["dialog"]["open"], True)
        self.assertEqual(transitions[2]["after"]["scene"], {"mode": "unknown", "verified": False})
        self.assertIsNone(transitions[2]["after"]["dialog"]["open"])
        self.assertIsNone(transitions[2]["after"]["frame"])

    def test_transition_compaction_excludes_unverified_raw_fields_and_bounds_text(self):
        tracker = ProgressTracker()
        before = observation(mode="dialog", text="x" * 300)
        before["player"].update(
            {"money": 9000, "badge_bits": 255, "facing_quality": "verified_direction_response"}
        )
        before["party"] = [{"species": "unverified"}]
        after = observation(mode="dialog", text="unverified")
        after["scene"]["verified"] = False
        after["player"]["facing_quality"] = "needs_data"
        after["frame"] = True
        tracker.record("a", before, after)
        transition = tracker.context(after)["recent_transitions"][0]
        self.assertEqual(transition["before"]["facing"], "up")
        self.assertEqual(len(transition["before"]["dialog"]["text"]), 180)
        self.assertNotIn("facing", transition["after"])
        self.assertEqual(transition["after"]["scene"]["mode"], "unknown")
        self.assertIsNone(transition["after"]["dialog"]["open"])
        self.assertIsNone(transition["after"]["frame"])
        serialized = json.dumps(transition)
        for field in ("money", "badge_bits", "species", "party"):
            self.assertNotIn(field, serialized)

    def test_transition_snapshot_is_independent_and_accepts_legacy_version_one(self):
        tracker = ProgressTracker()
        state = observation()
        for _ in range(5):
            tracker.record("wait", state, state)
        saved = json.loads(json.dumps(tracker.snapshot()))
        restored = ProgressTracker(saved)
        self.assertEqual(restored.context(state), tracker.context(state))
        context = restored.context(state)
        context["recent_transitions"][0]["before"]["position"]["x"] = 99
        self.assertEqual(restored.context(state), tracker.context(state))
        del saved["recent_transitions"]
        legacy = ProgressTracker(saved)
        self.assertEqual(legacy.context(state)["recent_transitions"], [])
        self.assertEqual(legacy.context(state)["same_position_steps"], 5)
        legacy.record("wait", state, state)
        self.assertEqual(legacy.context(state)["recent_transitions"][0]["step"], 6)


if __name__ == "__main__":
    unittest.main()
