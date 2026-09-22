"""Reject stale RAM/prior-only story claims and expose usable current state."""
import sys
import unittest
import hashlib
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import Reader, load_profile, CHARACTERS, PartyNotReady, PP_BONUS_SIGNATURE, DIVIDE_WRAPPER_SIGNATURE
from world_data import map_prior, load_world_data


class FakeMemory:
    def __init__(self):
        self.memory = bytearray(65536)
        self.rom_bytes = bytearray(0x8000)
        self.game = SimpleNamespace(frame_count=0)

    def read(self, address, length=1):
        return bytes(self.memory[address:address + length])


class CampaignObservationTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile()
        self.emulator = FakeMemory()
        self.reader = Reader(self.emulator, self.profile)

    def field(self, name, values):
        values = bytes([values]) if isinstance(values, int) else bytes(values)
        address = self.profile['addresses'][name]
        self.emulator.memory[address:address + len(values)] = values

    def matching_map(self, map_id):
        prior = map_prior(map_id)
        self.field('wCurMap', map_id)
        self.field('wCurMapWidth', prior['width'] // 2)
        self.field('wCurMapHeight', prior['height'] // 2)
        self.field('wNumberOfWarps', len(prior['warps']))
        self.field('wWarpEntries', [part for w in prior['warps'] for part in
                  (w['y'], w['x'], w['destination_warp_id'], w['destination_map_id'] & 255)])
        self.field('wNumSprites', len(prior['objects']))
        self.field('wMissableObjectList', 255)

    def text(self, text):
        reverse = {char: code for code, char in CHARACTERS.items() if len(char) == 1}
        raw = [reverse.get(c, 0x7f) for c in text]
        self.field('wTileMap', raw + [0x7f] * (360 - len(raw)))

    def pending_party(self):
        signature = bytes.fromhex('216bd1fa49cce60f280321a4d8')
        self.emulator.rom_bytes[0x1000:0x1000 + len(signature)] = signature
        self.emulator.game.register_file = SimpleNamespace(SP=0xdfe0)
        self.emulator.memory[0xdfe0:0xdfe2] = bytes.fromhex('0010')
        self.reader = Reader(self.emulator, self.profile)
        for name, value in {'wPartyCount':1,'wPartySpecies':[176,255],'wMonDataLocation':0,
                            'wNamingScreenType':2,'hNewPartyLength':1,'wcf91':176,'wFontLoaded':1}.items():
            self.field(name, value)

    def verified_pp_reader(self):
        table = bytes.fromhex(load_world_data()['move_data_table']['bytes_hex'])
        self.emulator.rom_bytes[0x2000:0x2000 + len(table)] = table
        self.emulator.rom_bytes[0x1000:0x1000 + len(PP_BONUS_SIGNATURE)] = PP_BONUS_SIGNATURE
        self.emulator.rom_bytes[0x38d0:0x38d0 + len(DIVIDE_WRAPPER_SIGNATURE)] = DIVIDE_WRAPPER_SIGNATURE
        self.reader = Reader(self.emulator, self.profile)

    def test_max_pp_requires_both_complete_move_table_and_calculation_match(self):
        self.assertIsNone(self.reader.move(0, 10, 35)['max_pp'])
        self.verified_pp_reader()
        self.assertEqual(self.reader.move(0, 10, 35)['max_pp'], 35)
        self.assertTrue(self.reader.move(0, 10, 35)['max_pp_verified'])
        self.emulator.rom_bytes[0x1000] ^= 1
        self.reader = Reader(self.emulator, self.profile)
        self.assertFalse(self.reader.move(0, 10, 35)['max_pp_verified'])

    def test_pp_ups_use_high_two_bits_and_seven_point_bonus_cap(self):
        self.verified_pp_reader()
        for pp_ups in range(4):
            move = self.reader.move(0, 10, (pp_ups << 6) | 12)
            self.assertEqual(move['pp'], 12)
            self.assertEqual(move['pp_ups'], pp_ups)
            self.assertEqual(move['max_pp'], 35 + 7 * pp_ups)
        self.assertEqual(self.reader.move(1, 45, 255)['max_pp'], 61)  # Growl: 40 + 3*7, never 64

    def test_healed_fact_requires_current_hp_status_and_verified_full_pp(self):
        self.matching_map(38)
        self.verified_pp_reader()
        member = {'hp':20, 'max_hp':20, 'status_bits':0, 'moves':[self.reader.move(0,10,35)]}
        check = lambda: self.reader.milestones(party=[member], bag=[])['party_fully_healed']
        self.assertEqual((check()['value'], check()['verified']), (True, True))
        member['moves'][0]['pp'] = 34
        self.assertEqual((check()['value'], check()['verified']), (False, True))
        member['moves'][0]['pp'] = 35
        member['status_bits'] = 8
        self.assertFalse(check()['value'])
        member['status_bits'] = 0
        self.field('wIsInBattle', 1)
        self.assertIsNone(check()['value'])
        self.assertFalse(check()['verified'])

    def test_interactive_party_creation_is_unknown_not_empty_or_complete(self):
        self.pending_party()
        with self.assertRaises(PartyNotReady):
            self.reader.party()
        observed = self.reader.snapshot()
        self.assertEqual(observed['errors'], {})
        self.assertIsNone(observed['party'])
        self.assertFalse(observed['party_state']['ready'])
        for key in ('has_party','party_count','party_move_ids'):
            self.assertIsNone(observed['milestones'][key]['value'])
            self.assertFalse(observed['milestones'][key]['verified'])

    def test_empty_party_struct_without_active_creation_stack_still_errors(self):
        self.pending_party()
        self.emulator.memory[0xdfe0:0xdfe2] = b'\x00\x00'
        self.assertIn('party', self.reader.snapshot()['errors'])

    def test_nonzero_malformed_party_is_never_excused_by_naming_context(self):
        self.pending_party()
        self.field('wPartyMon1', [176, 0, 20])
        self.assertIn('party', self.reader.snapshot()['errors'])

    def test_mismatched_creation_species_still_errors(self):
        self.pending_party()
        self.field('wcf91', 177)
        self.assertIn('party', self.reader.snapshot()['errors'])

    def test_real_step108_creation_checkpoint_when_available(self):
        """The actual JEV failure fixture is private/ignored, never a ROM upload."""
        pokemon = Path(__file__).resolve().parents[1]
        state = pokemon / '.work/campaign-live-test/run1/last.state'
        rom = pokemon.parents[1] / 'minecraft-jev-readable/red-star-2020-08-18.gb'
        if not state.exists() or not rom.exists():
            self.skipTest('Private exact-ROM/step108 regression fixture not available')
        from emulator import Emulator
        expected = 'af45c8c94029023164d10d43891dc9fe258b06e26683daeaf956c5e4aa006d82'
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)
        with tempfile.TemporaryDirectory(prefix='party-init-regression-') as folder:
            copied = Path(folder) / 'checkpoint.state'
            shutil.copyfile(state, copied)
            emulator = Emulator(rom, self.profile)
            try:
                emulator.load(copied.read_bytes())
                reader = Reader(emulator, self.profile)
                initial = reader.snapshot()
                self.assertFalse(initial['party_state']['ready'])
                self.assertEqual(initial['errors'], {})
                emulator.tick(240)
                waiting = reader.snapshot()
                self.assertIsNone(waiting['party'])
                self.assertIn('nickname', waiting['dialog']['text'])
                # Regression input only; this test is not imported by the runner.
                emulator.press('b', 8, 24)
                self.assertEqual(reader.snapshot()['dialog']['choices'], ['YES', 'NO'])
                emulator.press('b', 8, 24)
                complete = reader.snapshot()
                self.assertTrue(complete['party_state']['ready'])
                self.assertEqual(complete['errors'], {})
                self.assertEqual(complete['party'][0]['nickname'], 'CHARMANDER')
                self.assertEqual(complete['party'][0]['hp'], 18)
                self.assertEqual(complete['party'][0]['level'], 5)
                self.assertTrue(complete['milestones']['party_fully_healed']['value'])
                self.assertTrue(complete['milestones']['party_fully_healed']['verified'])
            finally:
                emulator.close()
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)

    def test_real_wild_battle_intro_checkpoint_when_available(self):
        pokemon = Path(__file__).resolve().parents[1]
        state = pokemon / '.work/campaign-live-test/run2/last.state'
        rom = pokemon.parents[1] / 'minecraft-jev-readable/red-star-2020-08-18.gb'
        if not state.exists() or not rom.exists():
            self.skipTest('Private exact-ROM/wild-intro regression fixture not available')
        expected = 'fd3e62c7d0f908974dcddebcefab53242f921622b70bf73397e408506186d9e7'
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)
        from emulator import Emulator
        with tempfile.TemporaryDirectory(prefix='battle-intro-regression-') as folder:
            copied = Path(folder) / 'checkpoint.state'
            shutil.copyfile(state, copied)
            emulator = Emulator(rom, self.profile)
            try:
                emulator.load(copied.read_bytes())
                reader = Reader(emulator, self.profile)
                first = reader.snapshot()
                self.assertEqual(first['scene']['mode'], 'battle')
                self.assertTrue(first['scene']['verified'])
                self.assertTrue(first['battle']['phase_verified'])
                self.assertFalse(first['battle']['combatants_ready'])
                self.assertNotIn('player', first['battle'])
                self.assertNotIn('enemy', first['battle'])
                self.assertIn('appeared!', first['battle']['visible_text'])
                for _ in range(12):
                    emulator.press('a', 8, 32)
                    after = reader.snapshot()
                    self.assertEqual(after['scene']['mode'], 'battle')
                    self.assertEqual(after['errors'], {})
                    if after['battle'].get('menu') == 'command':
                        break
                self.assertEqual(after['battle']['selected_command'], 'FIGHT')
                self.assertTrue(after['battle']['combatants_ready'])
            finally:
                emulator.close()
        self.assertEqual(hashlib.sha256(state.read_bytes()).hexdigest(), expected)

    def test_world_warp_requires_live_tuple_and_dimension_match(self):
        self.matching_map(38)
        result = self.reader.world({'mode': 'overworld'})
        self.assertTrue(result['source_match'])
        self.assertEqual(result['warps'][0]['destination_map_id'], 37)
        self.assertEqual((result['width'], result['height']), (8, 8))
        self.field('wWarpEntries', [1, 6, 2, 37])
        result = self.reader.world({'mode': 'overworld'})
        self.assertFalse(result['source_match'])
        self.assertEqual(result['quality'], 'needs_data')
        self.assertEqual(result['warps'][0]['quality'], 'needs_data')

    def test_runtime_indoor_exit_resolves_last_outdoor_map(self):
        self.matching_map(37)
        self.field('wLastMap', 0)
        self.assertEqual(self.reader.world({'mode':'overworld'})['warps'][0]['destination_map_id'], 0)

    def test_hidden_npc_is_not_present_even_with_picture_and_coordinates(self):
        self.matching_map(37)
        sprite1 = bytearray(32); sprite1[16] = 0x33; sprite1[18] = 255
        sprite2 = bytearray(32); sprite2[20] = 8; sprite2[21] = 9
        self.field('wSpriteStateData1', sprite1)
        self.field('wSpriteStateData2', sprite2)
        self.field('wMapSpriteData', [0, 1])
        self.field('wMissableObjectList', [1, 3, 255])
        self.field('wMissableObjectFlags', 8)
        obj = self.reader.world({'mode':'overworld'})['objects'][0]
        self.assertFalse(obj['active'])
        self.assertEqual(obj['quality'], 'verified_hidden_sprite')
        self.field('wMissableObjectFlags', 0)
        obj = self.reader.world({'mode':'overworld'})['objects'][0]
        self.assertTrue(obj['active'])
        self.assertFalse(obj['visible'])  # offscreen does not mean hidden
        self.assertEqual((obj['x'], obj['y']), (5, 4))
        self.assertEqual(self.reader.world({'mode':'battle'})['objects'][0]['quality'], 'source_prior')

    def test_source_event_unvalidated_flag_does_not_complete(self):
        self.matching_map(38)
        event = load_world_data()['events']['EVENT_BEAT_CHAMPION_RIVAL']
        raw = bytearray(320); raw[event['offset']] = 1 << event['bit']
        self.field('wEventFlags', raw)
        result = self.reader.milestones(party=[], bag=[])
        self.assertTrue(result['champion_defeated']['value'])
        self.assertFalse(result['champion_defeated']['verified'])
        self.assertIsNone(result['game_completed']['value'])

    def test_source_unverified_quest_item_absence_is_unknown(self):
        self.matching_map(38)
        result = self.reader.milestones(party=[], bag=[])
        self.assertIsNone(result['silph_scope_received']['value'])
        self.assertFalse(result['silph_scope_received']['verified'])

    def test_key_item_receipt_requires_exact_rom_name_table_and_current_possession(self):
        self.matching_map(38)
        self.field('wNumBagItems', 1)
        self.field('wBagItems', [0x48, 1])
        self.assertFalse(self.reader.milestones(party=[])['silph_scope_received']['verified'])
        table = bytes.fromhex(load_world_data()['name_tables']['items']['bytes_hex'])
        self.emulator.rom_bytes[0x1000:0x1000 + len(table)] = table
        reader = Reader(self.emulator, self.profile)
        self.assertTrue(reader.milestones(party=[])['silph_scope_received']['verified'])
        self.assertTrue(reader.milestones(party=[])['silph_scope_received']['value'])
        self.field('wNumBagItems', 0)
        self.assertIsNone(reader.milestones(party=[])['silph_scope_received']['value'])

    def test_hm_range_without_matching_move_table_does_not_verify_item(self):
        self.matching_map(38)
        self.field('wNumBagItems', 1)
        self.field('wBagItems', [196, 1])
        signature = bytes.fromhex('fec43803fec9c9a7c90f13394694ff')
        self.emulator.rom_bytes[0x1000:0x1000 + len(signature)] = signature
        reader = Reader(self.emulator, self.profile)
        self.assertFalse(reader.milestones(party=[])['cut_received']['verified'])
        table = bytes.fromhex(load_world_data()['name_tables']['moves']['bytes_hex'])
        self.emulator.rom_bytes[0x2000:0x2000 + len(table)] = table
        reader = Reader(self.emulator, self.profile)
        self.assertTrue(reader.milestones(party=[])['cut_received']['verified'])

    def test_hof_count_map_and_champion_are_insufficient_without_registration(self):
        self.matching_map(118)
        self.field('wNumHoFTeams', 1)
        self.field('wObtainedBadges', 255)
        self.reader._hof_counter_layout = True
        result = self.reader.milestones(party=[{'moves':[]}], bag=[])
        self.assertIsNone(result['game_completed']['value'])
        self.text('HALL OF FAME')
        result = self.reader.milestones(party=[{'moves':[]}], bag=[])
        self.assertTrue(result['game_completed']['value'])
        self.assertTrue(result['game_completed']['verified'])
        # Registration stays recorded when the game clears text/Elite Four bits.
        self.text('')
        self.assertTrue(self.reader.milestones(party=[{'moves':[]}], bag=[])['game_completed']['value'])

    def test_source_hof_counter_signature_is_required(self):
        self.matching_map(118)
        self.field('wNumHoFTeams', 1)
        self.field('wObtainedBadges', 255)
        self.text('HALL OF FAME')
        self.assertIsNone(self.reader.milestones(party=[{'moves':[]}], bag=[])['game_completed']['value'])

    def test_battle_flag_alone_cannot_expose_stale_enemy_or_menu(self):
        self.field('wIsInBattle', 2)
        battle = self.reader.battle()
        self.assertFalse(battle['verified'])
        self.assertNotIn('enemy', battle)
        self.assertEqual(self.reader.scene()['mode'], 'unknown')

    def test_world_knowledge_pinned_and_complete_enough_for_route_graph(self):
        world = load_world_data()
        self.assertEqual(world['source_commit'], self.profile['source_commit'])
        self.assertFalse(world['source_binary_match'])
        self.assertEqual(len(world['maps']), 248)
        self.assertEqual(len(world['moves']), 165)
        self.assertEqual(map_prior(0)['connections'][0]['destination_map_id'], 12)
        self.assertEqual(world['moves']['33']['name'], 'TACKLE')


if __name__ == '__main__':
    unittest.main()
