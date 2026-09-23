"""Offline long-run durability checks; never use a ROM, real key, or JEV API."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from _paths import ROOT, POKEMON
from assisted_helpers import run

TEST_KEY = "fixture-only-not-a-real-key"
PROFILE = {"rom_sha1": "offline-fixture-rom-identity"}
OBSERVATION = {
    "game": "OFFLINE TEST DOUBLE",
    "errors": [],
    "player": {},
    "party": [],
    "bag": [],
    "screen_text": {"rows": ["OFFLINE TEST DOUBLE"]},
}


def events(output):
    return [json.loads(row) for row in (output / "events.jsonl").read_text().splitlines()]


class LongRunTests(unittest.TestCase):
    def test_5000_step_budget_is_accepted_without_a_key_or_network_call(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}),
            patch("run.Emulator") as emulator,
            patch("run.choose") as choose,
        ):
            output = Path(directory) / "offline-run"
            report = run(
                Path("NO-ROM.gb"),
                output,
                goal="Offline validation",
                steps=5000,
                allow_missing_key=True,
            )
            self.assertEqual(report["status"], "blocked_missing_key")
            self.assertEqual(report["executed_actions"], 0)
            self.assertEqual(events(output)[0]["maxSteps"], 5000)
            self.assertEqual(events(output)[-1]["status"], "blocked_missing_key")
            emulator.assert_not_called()
            choose.assert_not_called()

    def test_zero_steps_means_no_budget_and_negative_is_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}),
            patch("run.Emulator") as emulator,
        ):
            output = Path(directory) / "unlimited-run"
            report = run(
                Path("NO-ROM.gb"),
                output,
                goal="Offline validation",
                steps=0,
                allow_missing_key=True,
            )
            self.assertEqual(report["status"], "blocked_missing_key")
            self.assertEqual(events(output)[0]["maxSteps"], 0)
            emulator.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run(
                    Path("NO-ROM.gb"),
                    Path(directory) / "run",
                    goal="Offline validation",
                    steps=-1,
                    allow_missing_key=True,
                )

    def test_checkpoints_are_available_during_run_with_verified_hash_and_step(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "offline-run"
            world = Mock()
            world.game = SimpleNamespace(frame_count=0)
            count = 0
            saved_steps = []
            observed_checkpoints = []

            def press(*args, **kwargs):
                nonlocal count
                count += 1
                world.game.frame_count += 40

            def save():
                saved_steps.append(count)
                return f"offline state after {count} actions".encode()

            def choose(_before, _goal, _history, *, on_event):
                if count in (2, 4):
                    manifest = json.loads((output / "last.state.json").read_text())
                    state = (output / "last.state").read_bytes()
                    self.assertEqual(manifest["rom_sha1"], PROFILE["rom_sha1"])
                    self.assertEqual(manifest["state_sha256"], hashlib.sha256(state).hexdigest())
                    self.assertEqual(manifest["step"], count)
                    observed_checkpoints.append(count)
                on_event({"type": "jev_request", "attempt": 1})
                on_event(
                    {"type": "jev_response", "attempt": 1, "latency_ms": 100, "httpStatus": 503}
                )
                on_event({"type": "jev_error", "attempt": 1, "latency_ms": 100, "httpStatus": 503})
                on_event({"type": "jev_request", "attempt": 2})
                on_event(
                    {"type": "jev_response", "attempt": 2, "latency_ms": 200, "httpStatus": 200}
                )
                return {
                    "answer": {"choice": "a"},
                    "source": "offline-test-double",
                    "latency_ms": 300,
                }

            world.press.side_effect = press
            world.save.side_effect = save
            reader = Mock()
            reader.snapshot.return_value = OBSERVATION
            with (
                patch.dict(os.environ, {"TYPESAFE_API_KEY": TEST_KEY}),
                patch("run.Emulator", return_value=world),
                patch("run.Reader", return_value=reader),
                patch("run.load_profile", return_value=PROFILE),
                patch("run.choose", side_effect=choose),
            ):
                report = run(
                    Path("NO-ROM.gb"),
                    output,
                    goal="Offline validation",
                    steps=5,
                    checkpoint_every=2,
                )
            self.assertEqual(observed_checkpoints, [2, 4])
            self.assertEqual(saved_steps, [2, 4, 5])
            self.assertEqual(
                [row["saved_step"] for row in events(output) if row["type"] == "checkpoint"], [2, 4]
            )
            manifest = json.loads((output / "last.state.json").read_text())
            self.assertEqual(manifest["step"], 5)
            self.assertEqual(
                manifest["state_sha256"],
                hashlib.sha256((output / "last.state").read_bytes()).hexdigest(),
            )
            campaign = json.loads((output / "last.campaign.json").read_text())
            self.assertEqual(
                {key: campaign[key] for key in ("rom_sha1", "state_sha256", "step")},
                {key: manifest[key] for key in ("rom_sha1", "state_sha256", "step")},
            )
            self.assertEqual(campaign["campaign"]["steps"], 5)
            self.assertEqual(report["executed_actions"], 5)
            self.assertEqual(report["jev_http_attempts"], 10)
            self.assertEqual(report["model_ms"], 1500)
            self.assertEqual(report["game_frames"], 200)
            self.assertEqual(report["status"], "budget_reached")
            self.assertFalse(list(output.glob("*.tmp")))
            world.screenshot.assert_not_called()
            world.close.assert_called_once()

    def test_campaign_memory_resume_requires_matching_checkpoint_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            state = source / "last.state"
            state.write_bytes(b"offline campaign checkpoint")
            identity = {
                "rom_sha1": PROFILE["rom_sha1"],
                "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                "step": 24,
            }
            (source / "last.state.json").write_text(json.dumps(identity))
            for matching in (True, False):
                with self.subTest(matching=matching):
                    sidecar = {
                        **identity,
                        "campaign": {
                            "steps": 24,
                            "history_facts": {
                                "pokedex_received": {"value": True, "verified": True}
                            },
                        },
                    }
                    if not matching:
                        sidecar["state_sha256"] = "different-state"
                    (source / "last.campaign.json").write_text(json.dumps(sidecar))
                    world = Mock()
                    world.save.return_value = b"offline resumed state"
                    reader = Mock()
                    reader.snapshot.return_value = OBSERVATION
                    output = Path(directory) / str(matching)
                    with (
                        patch.dict(os.environ, {"TYPESAFE_API_KEY": TEST_KEY}),
                        patch("run.Emulator", return_value=world),
                        patch("run.Reader", return_value=reader),
                        patch("run.load_profile", return_value=PROFILE),
                        patch("run.choose", return_value={"answer": {"choice": "wait"}}),
                    ):
                        report = run(
                            Path("NO-ROM.gb"),
                            output,
                            goal="Offline validation",
                            steps=1,
                            state_file=state,
                        )
                    memory = json.loads((output / "last.campaign.json").read_text())["campaign"]
                    self.assertEqual(
                        report["campaign_memory_source"],
                        "verified_checkpoint" if matching else "new_memory",
                    )
                    self.assertEqual(memory["steps"], 25 if matching else 1)
                    self.assertEqual("pokedex_received" in memory["history_facts"], matching)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "requires POSIX SIGTERM")
    def test_cli_sigterm_finalizes_a_waiting_run_and_saves_state(self):
        # A subprocess protects the test runner's own signal handlers. Every
        # emulator/decision dependency is replaced before main starts.
        script = """
import sys, signal
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, sys.argv.pop(1))
import run
ready = Path(sys.argv.pop(1))
world = Mock()
world.save.return_value = b'offline interrupted state'
reader = Mock()
reader.snapshot.return_value = {'errors': [], 'player': {}, 'party': [], 'bag': [], 'screen_text': {'rows': []}}
def waiting(*args, **kwargs):
    ready.write_text('offline decision waiting')
    signal.pause()
    raise AssertionError('SIGTERM did not interrupt the decision')
with patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), patch('run.load_profile', return_value={'rom_sha1': 'offline-rom'}), patch('run.choose', side_effect=waiting):
    run.main()
"""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "offline-run"
            ready = Path(directory) / "ready"
            environment = {**os.environ, "TYPESAFE_API_KEY": TEST_KEY}
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(POKEMON),
                    str(ready),
                    "--output",
                    str(output),
                    "--steps",
                    "5000",
                    "--no-video",
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "offline CLI did not reach its mocked decision")
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout)["status"], "interrupted")
                self.assertEqual(events(output)[-1]["status"], "interrupted")
                self.assertEqual((output / "last.state").read_bytes(), b"offline interrupted state")
                manifest = json.loads((output / "last.state.json").read_text())
                self.assertEqual(manifest["step"], 0)
                self.assertEqual(
                    manifest["state_sha256"],
                    hashlib.sha256(b"offline interrupted state").hexdigest(),
                )
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
