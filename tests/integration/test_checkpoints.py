"""Optional exact historical checkpoints, tested on copies; no model requests."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from _bootstrap import ROOT, POKEMON, DEFAULT_ROM
from memory import Reader, load_profile


class HistoricalCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()

    def test_real_step108_creation_checkpoint_when_available(self):
        """The actual JEV failure fixture is private/ignored, never a ROM upload."""
        pokemon = POKEMON
        state = pokemon / ".work/campaign-live-test/run1/last.state"
        rom = DEFAULT_ROM
        if not state.exists() or not rom.exists():
            self.skipTest("Private exact-ROM/step108 regression fixture not available")
        from emulator import Emulator

        expected = "af45c8c94029023164d10d43891dc9fe258b06e26683daeaf956c5e4aa006d82"
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)
        with tempfile.TemporaryDirectory(prefix="party-init-regression-") as folder:
            copied = Path(folder) / "checkpoint.state"
            shutil.copyfile(state, copied)
            emulator = Emulator(rom, self.profile)
            try:
                emulator.load(copied.read_bytes())
                reader = Reader(emulator, self.profile)
                initial = reader.snapshot()
                self.assertFalse(initial["party_state"]["ready"])
                self.assertEqual(initial["errors"], {})
                emulator.tick(240)
                waiting = reader.snapshot()
                self.assertIsNone(waiting["party"])
                self.assertIn("nickname", waiting["dialog"]["text"])
                # Regression input only; this test is not imported by the runner.
                emulator.press("b", 8, 24)
                self.assertEqual(reader.snapshot()["dialog"]["choices"], ["YES", "NO"])
                emulator.press("b", 8, 24)
                complete = reader.snapshot()
                self.assertTrue(complete["party_state"]["ready"])
                self.assertEqual(complete["errors"], {})
                self.assertEqual(complete["party"][0]["nickname"], "CHARMANDER")
                self.assertEqual(complete["party"][0]["hp"], 18)
                self.assertEqual(complete["party"][0]["level"], 5)
                self.assertTrue(complete["milestones"]["party_fully_healed"]["value"])
                self.assertTrue(complete["milestones"]["party_fully_healed"]["verified"])
            finally:
                emulator.close()
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)


    def test_real_wild_battle_intro_checkpoint_when_available(self):
        pokemon = POKEMON
        state = pokemon / ".work/campaign-live-test/run2/last.state"
        rom = DEFAULT_ROM
        if not state.exists() or not rom.exists():
            self.skipTest("Private exact-ROM/wild-intro regression fixture not available")
        expected = "fd3e62c7d0f908974dcddebcefab53242f921622b70bf73397e408506186d9e7"
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)
        from emulator import Emulator

        with tempfile.TemporaryDirectory(prefix="battle-intro-regression-") as folder:
            copied = Path(folder) / "checkpoint.state"
            shutil.copyfile(state, copied)
            emulator = Emulator(rom, self.profile)
            try:
                emulator.load(copied.read_bytes())
                reader = Reader(emulator, self.profile)
                first = reader.snapshot()
                self.assertEqual(first["scene"]["mode"], "battle")
                self.assertTrue(first["scene"]["verified"])
                self.assertTrue(first["battle"]["phase_verified"])
                self.assertFalse(first["battle"]["combatants_ready"])
                self.assertNotIn("player", first["battle"])
                self.assertNotIn("enemy", first["battle"])
                self.assertIn("appeared!", first["battle"]["visible_text"])
                for _ in range(12):
                    emulator.press("a", 8, 32)
                    after = reader.snapshot()
                    self.assertEqual(after["scene"]["mode"], "battle")
                    self.assertEqual(after["errors"], {})
                    if after["battle"].get("menu") == "command":
                        break
                self.assertEqual(after["battle"]["selected_command"], "FIGHT")
                self.assertTrue(after["battle"]["combatants_ready"])
            finally:
                emulator.close()
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)



if __name__ == "__main__":
    unittest.main()
