"""Guard prompt loading, resource paths and explicit paid-test opt-in."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from _paths import ROOT, POKEMON
import paths
from planning import PLANNER_SYSTEM_PROMPT


class ResourceTests(unittest.TestCase):
    def test_prompt_files_load_from_a_single_source(self):
        self.assertEqual(PLANNER_SYSTEM_PROMPT, (ROOT / "prompts/system2/planner.txt").read_text())
        self.assertTrue(paths.load_prompt("system1/button.txt", single_line=True))

    def test_loader_rejects_outside_path_and_empty_prompts(self):
        with self.assertRaises(ValueError):
            paths.load_prompt("../README.md")
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "empty.txt"
            file.write_text("  ")
            with patch.object(paths, 'PROMPTS', Path(folder)):
                with self.assertRaisesRegex(ValueError, 'Empty prompt'):
                    paths.load_prompt('empty.txt')

    def test_missing_prompt_does_not_silently_fallback(self):
        with self.assertRaises(FileNotFoundError):
            paths.load_prompt('system2/does-not-exist.txt')

    def test_default_rom_preserves_legacy_path_then_uses_restored_asset(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'ROOT', Path(folder)):
            root = Path(folder)
            self.assertEqual(paths.default_rom(), root / 'roms' / paths.ROM_NAME)
            # Not a cartridge; only checks resolution and never starts an emulator.
            (root / paths.ROM_NAME).write_bytes(b'path-fixture-only')
            self.assertEqual(paths.default_rom(), root / paths.ROM_NAME)

    def test_entrypoints_help_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            for relative in ('pokemon/run.py', 'tests/integration/test_rom.py',
                             'tests/integration/test_observation.py',
                             'tests/integration/test_dual_models.py'):
                result = subprocess.run([sys.executable, str(ROOT / relative), '--help'], cwd=folder,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, relative + ': ' + result.stderr)

    def test_dual_test_requires_explicit_model_opt_in(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tests/integration/test_dual_models.py')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--allow-model-calls', result.stderr)

    def test_required_json_data_remains_loadable(self):
        from memory import load_profile
        from world_data import load_world_data
        self.assertEqual(load_profile()['rom_sha1'], 'e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9')
        self.assertTrue(load_world_data()['maps'])


if __name__ == '__main__':
    unittest.main()
