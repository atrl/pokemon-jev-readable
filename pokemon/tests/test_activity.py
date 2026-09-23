"""Activity/recovery regressions; no emulator, credentials or network."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity import StallMonitor, observable_changes


def state(x=4, text="Challenge!", mode="battle"):
    return {
        "player": {"map_id": 54, "x": x, "y": 6},
        "world": {"source_match": True, "player_position_valid": True},
        "scene": {"mode": mode, "verified": True},
        "screen_text": {"rows": [text]},
        "dialog": {"text": text, "open": mode == "dialog"},
        "battle": {
            "active": mode == "battle",
            "phase_verified": True,
            "phase": "intro",
            "menu": "text_or_animation",
            "visible_text": text,
        },
    }


class ActivityTests(unittest.TestCase):
    def test_known_route_and_dialog_activity_do_not_false_halt(self):
        # Synthetic sequence, not a recorded or claimed playthrough. Guards
        # against equating "no new story/tile" with "no observable activity".
        monitor = StallMonitor(80, 0)
        for step in range(101):
            if step < 45:
                before = state(step, mode="overworld")
                after = state(step + 1, mode="overworld")
            else:
                before = state(text=f"Dialog page {step}")
                after = state(text=f"Dialog page {step + 1}")
            activity = monitor.observe(before, after, {}, {"loop_kind": None})
            self.assertFalse(activity["exhausted"], f"Unexpected stop at step {step}")
        self.assertEqual(activity["no_effect_steps"], 0)
        self.assertEqual(activity["no_strategic_progress_steps"], 101)

    def test_arrow_blink_and_frame_counter_do_not_fake_activity(self):
        a = state()
        b = state(text="Challenge! ▼")
        a["frame"] = 0
        b["frame"] = 600
        self.assertEqual(observable_changes(a, b), [])

    def test_own_hp_pp_and_repeated_but_advancing_dialogue_are_activity(self):
        a = state()
        b = state(text="Sent out DIGLETT!")
        self.assertIn("text", observable_changes(a, b))
        a["battle"].update(verified=True, player={"hp": 28, "moves": [{"move_id": 52, "pp": 25}]})
        b["battle"].update(verified=True, player={"hp": 21, "moves": [{"move_id": 52, "pp": 24}]})
        self.assertIn("player", observable_changes(a, b))

    def test_known_route_movement_is_activity_without_claiming_story_progress(self):
        monitor = StallMonitor(12, 0)
        for x in range(30):
            result = monitor.observe(
                state(x, mode="overworld"), state(x + 1, mode="overworld"), {}, {"loop_kind": None}
            )
            self.assertFalse(result["exhausted"])
        self.assertEqual(result["no_effect_steps"], 0)
        self.assertEqual(result["no_strategic_progress_steps"], 30)

    def test_cycling_positions_still_has_bounded_recovery(self):
        monitor = StallMonitor(12, 1)
        for i in range(12):
            result = monitor.observe(
                state(i % 2, mode="overworld"),
                state((i + 1) % 2, mode="overworld"),
                {},
                {"loop_kind": "position_cycle"},
            )
        self.assertTrue(result["should_recover"])
        self.assertFalse(result["exhausted"])
        monitor.begin_recovery()
        for i in range(12):
            result = monitor.observe(
                state(i % 2, mode="overworld"),
                state((i + 1) % 2, mode="overworld"),
                {},
                {"loop_kind": "position_cycle"},
            )
        self.assertTrue(result["exhausted"])

    def test_stable_game_waits_recover_then_pause_but_no_move_is_selected(self):
        monitor = StallMonitor(12, 3)
        s = state()
        for attempt in range(3):
            for _ in range(12):
                result = monitor.observe(s, s, {}, {"loop_kind": "stationary_repetition"})
            self.assertTrue(result["should_recover"])
            self.assertEqual(monitor.begin_recovery(), attempt + 1)
        for _ in range(12):
            result = monitor.observe(s, s, {}, {"loop_kind": "stationary_repetition"})
        self.assertTrue(result["exhausted"])
        self.assertNotIn("next_button", result)


if __name__ == "__main__":
    unittest.main()
