"""Reporting tests use fixtures; they are not claims of model gameplay."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from _paths import ROOT, POKEMON
from render_results import export_results


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.output = self.root / "output"
        self.source.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        (self.source / name).write_text(json.dumps(data))

    def decision(self, button="a", after=True, text=""):
        player = {"name": "RED", "map_id": 38, "x": 3, "y": 6}
        self.write(
            "0000-decision.json",
            {
                "source": "jev",
                "step": 0,
                "answer": {"choice": button, "probabilities": {button: 1}},
                "request": {"state": {"game": {"player": player, "screen_text": {"rows": [text]}}}},
            },
        )
        if after:
            self.write("0000-after.json", {"player": player})

    def test_missing_run_is_not_success(self):
        summary = export_results(self.source, self.output)
        self.assertEqual(summary["status"], "not_run")
        self.assertEqual(summary["recorded_decisions"], 0)
        self.assertTrue((self.output / "index.html").is_file())

    def test_no_movement_not_game_completion(self):
        self.decision()
        self.write(
            "report.json", {"status": "budget_reached", "jev_calls": 1, "executed_actions": 1}
        )
        summary = export_results(self.source, self.output)
        self.assertEqual(summary["position_changes"], 0)
        self.assertEqual(summary["distinct_positions"], 1)
        self.assertEqual(summary["game_completion"], "not_verified")
        self.assertIsNotNone(summary["warning"])

    def test_rejected_or_failed_action_not_counted_as_executed(self):
        self.decision(after=False)
        summary = export_results(self.source, self.output)
        self.assertEqual(summary["recorded_executed_actions"], 0)

    def test_script_tag_cannot_escape_json(self):
        attack = '</script><script>alert("bad")</script>'
        self.decision(text=attack)
        export_results(self.source, self.output)
        page = (self.output / "index.html").read_text()
        self.assertNotIn(attack, page)
        self.assertIn("\\u003c/script\\u003e", page)

    def test_regression_is_not_jev(self):
        self.write("report.json", {"policy": "scripted_regression_not_jev", "status": "passed"})
        summary = export_results(self.source, self.output)
        self.assertEqual(summary["status"], "not_run")

    def test_fixture_decisions_not_counted(self):
        self.write("0000-decision.json", {"source": "fake"})
        self.assertEqual(export_results(self.source, self.output)["recorded_decisions"], 0)

    def test_old_artifact_layout(self):
        self.decision()
        folder = self.source / "jev"
        folder.mkdir()
        for path in self.source.glob("*.json"):
            path.rename(folder / path.name)
        self.assertEqual(export_results(self.source, self.output)["recorded_decisions"], 1)

    def test_missing_key_visible(self):
        self.write("report.json", {"status": "blocked_missing_key", "reason": "No key"})
        summary = export_results(self.source, self.output)
        self.assertEqual(summary["status"], "blocked_missing_key")
        self.assertIn("No key", (self.output / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
