"""Conservative scene/map decoding; live behavior is in test_observation_live.py."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from _paths import ROOT, POKEMON
from memory import Reader, decode_text, load_profile


class FakeMemory:
    def __init__(self):
        self.memory = bytearray(65536)
        self.rom_bytes = bytearray(0x4000)
        self.game = SimpleNamespace(frame_count=0)

    def read(self, address, length=1):
        return bytes(self.memory[address : address + length])


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        self.memory = FakeMemory()
        self.reader = Reader(self.memory, self.profile)

    def set_field(self, name, values):
        if isinstance(values, int):
            values = bytes([values])
        start = self.profile["addresses"][name]
        self.memory.memory[start : start + len(values)] = values

    def overworld(self):
        self.set_field("wTileMap", bytes([1]) * 360)
        self.set_field("wTilesetCollisionPtr", (0x1744).to_bytes(2, "little"))
        self.memory.rom_bytes[0x1744:0x1747] = bytes([1, 2, 255])
        self.set_field("PlayerYPixels", 60)
        self.set_field("PlayerXPixels", 64)
        self.set_field("PlayerFacingDirection", 4)

    def dialog_box(self):
        self.overworld()
        self.set_field("wFontLoaded", 1)
        tiles = bytearray([0x7F] * 360)
        tiles[240:260] = bytes([0x79] + [0x7A] * 18 + [0x7B])
        tiles[340:360] = bytes([0x7D] + [0x7A] * 18 + [0x7E])
        for y in range(13, 17):
            tiles[y * 20] = tiles[y * 20 + 19] = 0x7C
        self.set_field("wTileMap", tiles)

    def test_blank_overworld_is_not_waiting_dialog(self):
        self.overworld()
        self.assertFalse(any(self.reader.screen_text()["rows"]))
        self.assertEqual(self.reader.scene()["mode"], "overworld")
        self.assertFalse(self.reader.dialog()["open"])

    def test_empty_but_open_text_box_is_not_overworld(self):
        self.dialog_box()
        self.assertEqual(self.reader.scene()["mode"], "dialog")
        self.assertTrue(self.reader.dialog()["open"])
        self.assertEqual(self.reader.dialog()["text"], "")
        self.assertIsNone(self.reader.dialog()["awaiting_input"])

    def test_absent_blinking_arrow_does_not_prove_not_waiting(self):
        self.dialog_box()
        self.assertIsNone(self.reader.dialog()["awaiting_input"])
        self.memory.memory[self.profile["addresses"]["wTileMap"] + 358] = 0xEE
        self.assertTrue(self.reader.dialog()["awaiting_input"])

    def test_font_flag_alone_does_not_prove_dialog(self):
        self.overworld()
        self.set_field("wFontLoaded", 1)
        self.assertEqual(self.reader.scene()["mode"], "unknown")
        self.assertIsNone(self.reader.dialog()["open"])

    def test_unknown_and_battle_never_publish_a_walk_grid(self):
        self.assertEqual(self.reader.scene()["mode"], "unknown")
        self.assertIsNone(self.reader.local_map())
        self.overworld()
        self.set_field("wIsInBattle", 1)
        self.assertEqual(self.reader.scene()["mode"], "unknown")
        self.assertIsNone(self.reader.local_map())

    def test_collision_pointer_uses_rom_zero_not_graphics_bank(self):
        self.overworld()
        self.set_field("wTilesetBank", 56)
        grid = self.reader.local_map()
        self.assertTrue(grid["neighbors"]["right"]["background_passable"])
        self.assertIn("ROM0", grid["source"])
        self.assertEqual(grid["player_cell"], {"x": 4, "y": 4})

    def test_banked_unterminated_or_empty_collision_tables_fail_closed(self):
        self.overworld()
        for ptr in (0, 0x4000, 0x8000):
            self.set_field("wTilesetCollisionPtr", ptr.to_bytes(2, "little"))
            self.assertIsNone(self.reader.local_map())
        self.set_field("wTilesetCollisionPtr", (0x1744).to_bytes(2, "little"))
        for raw in (bytes(128), bytes([255]) * 128):
            self.memory.rom_bytes[0x1744 : 0x1744 + 128] = raw
            self.assertIsNone(self.reader.local_map())

    def test_dialog_and_misaligned_player_suppress_map(self):
        self.dialog_box()
        self.assertIsNone(self.reader.local_map())
        self.overworld()
        self.set_field("PlayerXPixels", 65)
        self.assertIsNone(self.reader.local_map())

    def test_invalid_facing_remains_null(self):
        self.dialog_box()
        self.set_field("PlayerFacingDirection", 3)
        self.assertIsNone(self.reader.snapshot()["player"]["facing"])

    def test_main_menu_words_inside_dialog_do_not_create_main_menu(self):
        self.dialog_box()
        # Exact menu text is still insufficient without the right menu border.
        encoder = {
            char: value for value, char in __import__("memory").CHARACTERS.items() if len(char) == 1
        }
        for y, text in zip(range(13, 17), ("PACK", "SAVE", "OPTION", "EXIT")):
            start = self.profile["addresses"]["wTileMap"] + y * 20 + 12
            self.memory.memory[start : start + len(text)] = bytes(encoder[c] for c in text)
        self.assertEqual(self.reader.scene()["mode"], "dialog")

    def test_pinned_contraction_glyphs_are_preserved(self):
        self.assertEqual(decode_text(bytes([0x88, 0xB3, 0xBD, 0x50])), "It's")


if __name__ == "__main__":
    unittest.main()
