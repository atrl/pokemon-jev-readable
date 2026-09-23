"""Reject stale RAM/prior-only story claims and expose usable current state."""

import sys
import unittest
import hashlib
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from _paths import ROOT, POKEMON
from memory import (
    Reader,
    load_profile,
    CHARACTERS,
    PartyNotReady,
    PP_BONUS_SIGNATURE,
    DIVIDE_WRAPPER_SIGNATURE,
)
from world_data import map_prior, load_world_data, type_effectiveness


class FakeMemory:
    def __init__(self):
        self.memory = bytearray(65536)
        self.rom_bytes = bytearray(0x8000)
        self.game = SimpleNamespace(frame_count=0)

    def read(self, address, length=1):
        return bytes(self.memory[address : address + length])


class CampaignObservationTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        self.emulator = FakeMemory()
        self.reader = Reader(self.emulator, self.profile)

    def field(self, name, values):
        values = bytes([values]) if isinstance(values, int) else bytes(values)
        address = self.profile["addresses"][name]
        self.emulator.memory[address : address + len(values)] = values

    def matching_map(self, map_id):
        prior = map_prior(map_id)
        self.field("wCurMap", map_id)
        self.field("wCurMapWidth", prior["width"] // 2)
        self.field("wCurMapHeight", prior["height"] // 2)
        self.field("wNumberOfWarps", len(prior["warps"]))
        self.field(
            "wWarpEntries",
            [
                part
                for w in prior["warps"]
                for part in (
                    w["y"],
                    w["x"],
                    w["destination_warp_id"],
                    w["destination_map_id"] & 255,
                )
            ],
        )
        self.field("wNumSprites", len(prior["objects"]))
        self.field("wMissableObjectList", 255)

    def text(self, text):
        reverse = {char: code for code, char in CHARACTERS.items() if len(char) == 1}
        raw = [reverse.get(c, 0x7F) for c in text]
        self.field("wTileMap", raw + [0x7F] * (360 - len(raw)))

    def pending_party(self):
        signature = bytes.fromhex("216bd1fa49cce60f280321a4d8")
        self.emulator.rom_bytes[0x1000 : 0x1000 + len(signature)] = signature
        self.emulator.game.register_file = SimpleNamespace(SP=0xDFE0)
        self.emulator.memory[0xDFE0:0xDFE2] = bytes.fromhex("0010")
        self.reader = Reader(self.emulator, self.profile)
        for name, value in {
            "wPartyCount": 1,
            "wPartySpecies": [176, 255],
            "wMonDataLocation": 0,
            "wNamingScreenType": 2,
            "hNewPartyLength": 1,
            "wcf91": 176,
            "wFontLoaded": 1,
        }.items():
            self.field(name, value)

    def verified_pp_reader(self):
        table = bytes.fromhex(load_world_data()["move_data_table"]["bytes_hex"])
        self.emulator.rom_bytes[0x2000 : 0x2000 + len(table)] = table
        self.emulator.rom_bytes[0x1000 : 0x1000 + len(PP_BONUS_SIGNATURE)] = PP_BONUS_SIGNATURE
        self.emulator.rom_bytes[0x38D0 : 0x38D0 + len(DIVIDE_WRAPPER_SIGNATURE)] = (
            DIVIDE_WRAPPER_SIGNATURE
        )
        self.reader = Reader(self.emulator, self.profile)

    def combatants(self):
        self.field("wPartyCount", 1)
        party = bytearray(44)
        party[0] = 176
        party[1:3] = (28).to_bytes(2, "big")
        party[33] = 10
        party[34:36] = (28).to_bytes(2, "big")
        party[8] = 52  # Ember, actual ROM-table ID.
        party[29] = 25
        self.field("wPartyMon1", party)
        for symbol, species, level, hp, types, stats in (
            ("wBattleMon", 176, 10, 28, [20, 20], [16, 14, 19, 15]),
            ("wEnemyMon", 59, 11, 24, [4, 4], [19, 9, 29, 14]),
        ):
            mon = bytearray(29)
            mon[0] = species
            mon[1:3] = hp.to_bytes(2, "big")
            mon[5:7] = bytes(types)
            mon[8] = 52 if symbol == "wBattleMon" else 10
            mon[14] = level
            mon[15:17] = hp.to_bytes(2, "big")
            for offset, value in zip((17, 19, 21, 23), stats):
                mon[offset : offset + 2] = value.to_bytes(2, "big")
            mon[25] = 25 if symbol == "wBattleMon" else 35
            self.field(symbol, mon)
        self.field("wIsInBattle", 2)

    def test_type_chart_preserves_gen1_immunities_dual_types_and_rom_order(self):
        self.assertEqual(
            type_effectiveness(8, [24], verified=True)["multiplier"], 0
        )  # GHOST -> PSYCHIC
        self.assertEqual(
            type_effectiveness(21, [20, 20], verified=True)["multiplier"], 2
        )  # WATER -> FIRE once
        self.assertEqual(type_effectiveness(21, [5, 4], verified=True)["multiplier"], 4)
        mixed = type_effectiveness(25, [21, 26], verified=True)  # ICE -> WATER/DRAGON
        self.assertEqual(mixed["multiplier"], 1)
        self.assertEqual(mixed["factors"], [0.5, 2])
        self.assertFalse(type_effectiveness(21, [20])["verified"])
        self.assertIsNone(type_effectiveness(21, [255], verified=True)["multiplier"])

    def test_battle_tactical_fields_and_chart_require_actual_rom_match(self):
        self.verified_pp_reader()
        self.combatants()
        self.assertFalse(self.reader.battle()["player"]["moves"][0]["effectiveness"]["verified"])
        chart = bytes.fromhex(load_world_data()["type_chart"]["bytes_hex"])
        self.emulator.rom_bytes[0x3000 : 0x3000 + len(chart)] = chart
        self.reader = Reader(self.emulator, self.profile)
        b = self.reader.battle()
        self.assertTrue(b["verified"])
        self.assertEqual(b["player"]["attack"], 16)
        self.assertEqual(b["enemy"]["speed"], 29)
        self.assertEqual([t["id"] for t in b["player"]["types"]], [20])
        self.assertTrue(b["player"]["types_verified"])
        self.assertTrue(b["player"]["verified_stats"])
        self.assertTrue(b["player"]["moves"][0]["knowledge"]["numeric_data_verified"])
        self.assertTrue(b["player"]["moves"][0]["effectiveness"]["verified"])
        self.assertEqual(b["player"]["moves"][0]["effectiveness"]["multiplier"], 1)
        self.emulator.rom_bytes[0x3000] ^= 1
        self.reader = Reader(self.emulator, self.profile)
        self.assertFalse(self.reader.battle()["type_chart"]["verified"])

    def test_unusable_extra_stat_or_type_never_erases_verified_hp(self):
        self.combatants()
        enemy_address = self.profile["addresses"]["wEnemyMon"]
        self.emulator.memory[enemy_address + 19 : enemy_address + 21] = b"\x00\x00"
        self.emulator.memory[enemy_address + 5] = 255
        b = self.reader.battle()
        self.assertTrue(b["verified"])
        self.assertEqual(b["enemy"]["hp"], 24)
        self.assertIsNone(b["enemy"]["defense"])
        self.assertFalse(b["enemy"]["verified_stats"])
        self.assertFalse(b["enemy"]["types_verified"])
        self.assertFalse(b["player"]["moves"][0]["effectiveness"]["verified"])

    def test_max_pp_requires_both_complete_move_table_and_calculation_match(self):
        self.assertIsNone(self.reader.move(0, 10, 35)["max_pp"])
        self.verified_pp_reader()
        self.assertEqual(self.reader.move(0, 10, 35)["max_pp"], 35)
        self.assertTrue(self.reader.move(0, 10, 35)["max_pp_verified"])
        self.emulator.rom_bytes[0x1000] ^= 1
        self.reader = Reader(self.emulator, self.profile)
        self.assertFalse(self.reader.move(0, 10, 35)["max_pp_verified"])

    def test_pp_ups_use_high_two_bits_and_seven_point_bonus_cap(self):
        self.verified_pp_reader()
        for pp_ups in range(4):
            move = self.reader.move(0, 10, (pp_ups << 6) | 12)
            self.assertEqual(move["pp"], 12)
            self.assertEqual(move["pp_ups"], pp_ups)
            self.assertEqual(move["max_pp"], 35 + 7 * pp_ups)
        self.assertEqual(self.reader.move(1, 45, 255)["max_pp"], 61)  # Growl: 40 + 3*7, never 64

    def test_healed_fact_requires_current_hp_status_and_verified_full_pp(self):
        self.matching_map(38)
        self.verified_pp_reader()
        member = {"hp": 20, "max_hp": 20, "status_bits": 0, "moves": [self.reader.move(0, 10, 35)]}
        check = lambda: self.reader.milestones(party=[member], bag=[])["party_fully_healed"]
        self.assertEqual((check()["value"], check()["verified"]), (True, True))
        member["moves"][0]["pp"] = 34
        self.assertEqual((check()["value"], check()["verified"]), (False, True))
        member["moves"][0]["pp"] = 35
        member["status_bits"] = 8
        self.assertFalse(check()["value"])
        member["status_bits"] = 0
        self.field("wIsInBattle", 1)
        self.assertIsNone(check()["value"])
        self.assertFalse(check()["verified"])

    def test_interactive_party_creation_is_unknown_not_empty_or_complete(self):
        self.pending_party()
        with self.assertRaises(PartyNotReady):
            self.reader.party()
        observed = self.reader.snapshot()
        self.assertEqual(observed["errors"], {})
        self.assertIsNone(observed["party"])
        self.assertFalse(observed["party_state"]["ready"])
        for key in ("has_party", "party_count", "party_move_ids"):
            self.assertIsNone(observed["milestones"][key]["value"])
            self.assertFalse(observed["milestones"][key]["verified"])

    def test_empty_party_struct_without_active_creation_stack_still_errors(self):
        self.pending_party()
        self.emulator.memory[0xDFE0:0xDFE2] = b"\x00\x00"
        self.assertIn("party", self.reader.snapshot()["errors"])

    def test_nonzero_malformed_party_is_never_excused_by_naming_context(self):
        self.pending_party()
        self.field("wPartyMon1", [176, 0, 20])
        self.assertIn("party", self.reader.snapshot()["errors"])

    def test_mismatched_creation_species_still_errors(self):
        self.pending_party()
        self.field("wcf91", 177)
        self.assertIn("party", self.reader.snapshot()["errors"])



    def test_world_warp_requires_live_tuple_and_dimension_match(self):
        self.matching_map(38)
        result = self.reader.world({"mode": "overworld"})
        self.assertTrue(result["source_match"])
        self.assertEqual(result["warps"][0]["destination_map_id"], 37)
        self.assertEqual((result["width"], result["height"]), (8, 8))
        self.field("wWarpEntries", [1, 6, 2, 37])
        result = self.reader.world({"mode": "overworld"})
        self.assertFalse(result["source_match"])
        self.assertEqual(result["quality"], "needs_data")
        self.assertEqual(result["warps"][0]["quality"], "needs_data")

    def test_runtime_indoor_exit_resolves_last_outdoor_map(self):
        self.matching_map(37)
        self.field("wLastMap", 0)
        self.assertEqual(
            self.reader.world({"mode": "overworld"})["warps"][0]["destination_map_id"], 0
        )

    def test_hidden_npc_is_not_present_even_with_picture_and_coordinates(self):
        self.matching_map(37)
        sprite1 = bytearray(32)
        sprite1[16] = 0x33
        sprite1[18] = 255
        sprite2 = bytearray(32)
        sprite2[20] = 8
        sprite2[21] = 9
        self.field("wSpriteStateData1", sprite1)
        self.field("wSpriteStateData2", sprite2)
        self.field("wMapSpriteData", [0, 1])
        self.field("wMissableObjectList", [1, 3, 255])
        self.field("wMissableObjectFlags", 8)
        obj = self.reader.world({"mode": "overworld"})["objects"][0]
        self.assertFalse(obj["active"])
        self.assertEqual(obj["quality"], "verified_hidden_sprite")
        self.field("wMissableObjectFlags", 0)
        obj = self.reader.world({"mode": "overworld"})["objects"][0]
        self.assertTrue(obj["active"])
        self.assertFalse(obj["visible"])  # offscreen does not mean hidden
        self.assertEqual((obj["x"], obj["y"]), (5, 4))
        self.assertEqual(
            self.reader.world({"mode": "battle"})["objects"][0]["quality"], "source_prior"
        )

    def test_source_event_unvalidated_flag_does_not_complete(self):
        self.matching_map(38)
        event = load_world_data()["events"]["EVENT_BEAT_CHAMPION_RIVAL"]
        raw = bytearray(320)
        raw[event["offset"]] = 1 << event["bit"]
        self.field("wEventFlags", raw)
        result = self.reader.milestones(party=[], bag=[])
        self.assertTrue(result["champion_defeated"]["value"])
        self.assertFalse(result["champion_defeated"]["verified"])
        self.assertIsNone(result["game_completed"]["value"])

    def test_source_unverified_quest_item_absence_is_unknown(self):
        self.matching_map(38)
        result = self.reader.milestones(party=[], bag=[])
        self.assertIsNone(result["silph_scope_received"]["value"])
        self.assertFalse(result["silph_scope_received"]["verified"])

    def test_key_item_receipt_requires_exact_rom_name_table_and_current_possession(self):
        self.matching_map(38)
        self.field("wNumBagItems", 1)
        self.field("wBagItems", [0x48, 1])
        self.assertFalse(self.reader.milestones(party=[])["silph_scope_received"]["verified"])
        table = bytes.fromhex(load_world_data()["name_tables"]["items"]["bytes_hex"])
        self.emulator.rom_bytes[0x1000 : 0x1000 + len(table)] = table
        reader = Reader(self.emulator, self.profile)
        self.assertTrue(reader.milestones(party=[])["silph_scope_received"]["verified"])
        self.assertTrue(reader.milestones(party=[])["silph_scope_received"]["value"])
        self.field("wNumBagItems", 0)
        self.assertIsNone(reader.milestones(party=[])["silph_scope_received"]["value"])

    def test_hm_range_without_matching_move_table_does_not_verify_item(self):
        self.matching_map(38)
        self.field("wNumBagItems", 1)
        self.field("wBagItems", [196, 1])
        signature = bytes.fromhex("fec43803fec9c9a7c90f13394694ff")
        self.emulator.rom_bytes[0x1000 : 0x1000 + len(signature)] = signature
        reader = Reader(self.emulator, self.profile)
        self.assertFalse(reader.milestones(party=[])["cut_received"]["verified"])
        table = bytes.fromhex(load_world_data()["name_tables"]["moves"]["bytes_hex"])
        self.emulator.rom_bytes[0x2000 : 0x2000 + len(table)] = table
        reader = Reader(self.emulator, self.profile)
        self.assertTrue(reader.milestones(party=[])["cut_received"]["verified"])

    def test_hof_count_map_and_champion_are_insufficient_without_registration(self):
        self.matching_map(118)
        self.field("wNumHoFTeams", 1)
        self.field("wObtainedBadges", 255)
        self.reader._hof_counter_layout = True
        result = self.reader.milestones(party=[{"moves": []}], bag=[])
        self.assertIsNone(result["game_completed"]["value"])
        self.text("HALL OF FAME")
        result = self.reader.milestones(party=[{"moves": []}], bag=[])
        self.assertTrue(result["game_completed"]["value"])
        self.assertTrue(result["game_completed"]["verified"])
        # Registration stays recorded when the game clears text/Elite Four bits.
        self.text("")
        self.assertTrue(
            self.reader.milestones(party=[{"moves": []}], bag=[])["game_completed"]["value"]
        )

    def test_source_hof_counter_signature_is_required(self):
        self.matching_map(118)
        self.field("wNumHoFTeams", 1)
        self.field("wObtainedBadges", 255)
        self.text("HALL OF FAME")
        self.assertIsNone(
            self.reader.milestones(party=[{"moves": []}], bag=[])["game_completed"]["value"]
        )

    def test_battle_flag_alone_cannot_expose_stale_enemy_or_menu(self):
        self.field("wIsInBattle", 2)
        battle = self.reader.battle()
        self.assertFalse(battle["verified"])
        self.assertNotIn("enemy", battle)
        self.assertEqual(self.reader.scene()["mode"], "unknown")

    def test_world_knowledge_pinned_and_complete_enough_for_route_graph(self):
        world = load_world_data()
        self.assertEqual(world["source_commit"], self.profile["source_commit"])
        self.assertFalse(world["source_binary_match"])
        self.assertEqual(len(world["maps"]), 248)
        self.assertEqual(len(world["moves"]), 165)
        self.assertEqual(map_prior(0)["connections"][0]["destination_map_id"], 12)
        self.assertEqual(world["moves"]["33"]["name"], "TACKLE")


if __name__ == "__main__":
    unittest.main()
