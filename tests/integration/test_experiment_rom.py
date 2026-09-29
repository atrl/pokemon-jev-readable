"""Real ROM and shared experiment adapter; scripted fixture, no model calls."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _bootstrap import DEFAULT_ROM, ROOT  # noqa: F401
from pokemon.experiments.environment import ExperimentEnv
from pokemon.experiments.suite import register_case


@unittest.skipUnless(
    DEFAULT_ROM.exists() and importlib.util.find_spec("pyboy"),
    "Real ROM or PyBoy unavailable; not an executed experiment check",
)
class ExperimentROMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_rom import verify

        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.state = cls.root / "fixture" / "last.state"
        with redirect_stdout(io.StringIO()):
            verify(DEFAULT_ROM, cls.root / "fixture-diagnostics", cls.state)
        cls.original_hash = hashlib.sha256(cls.state.read_bytes()).hexdigest()
        cls.suite = cls.root / "suite" / "suite.json"
        cls.case = register_case(
            cls.suite,
            "bedroom",
            "scripted-bedroom",
            "test",
            cls.state,
            "Explore the observed room",
            {"type": "map_changed"},
        )

    def test_real_checkpoint_resets_identically_and_wait_advances_24_frames(self):
        env = ExperimentEnv(self.case, self.suite, DEFAULT_ROM, {"max_steps": 2})
        try:
            initial = env.reset()
            after, _, terminated, truncated, _ = env.step("wait")
            self.assertEqual(after["observation"]["frame"] - initial["observation"]["frame"], 24)
            self.assertFalse(terminated or truncated)
            _, _, _, truncated, _ = env.step("wait")
            self.assertTrue(truncated)
            self.assertEqual(initial, env.reset())
            self.assertEqual(
                hashlib.sha256(self.state.read_bytes()).hexdigest(), self.original_hash
            )
        finally:
            env.close()

    def test_real_button_and_wait_share_total_frames_and_memory_records_only_execution(self):
        env = ExperimentEnv(self.case, self.suite, DEFAULT_ROM)
        try:
            initial = env.reset()
            after, *_ = env.step("right")
            self.assertEqual(after["observation"]["frame"] - initial["observation"]["frame"], 24)
            self.assertEqual(after["memory"]["recent_actions"][-1]["button"], "right")
            self.assertEqual(after["memory"]["steps"], 1)
            screenshot = self.root / "last.png"
            env.screenshot(screenshot)
            self.assertTrue(screenshot.is_file())
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
