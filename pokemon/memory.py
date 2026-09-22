"""Read-only Red Star adapter. Addresses come from a pinned source symbol table.

Only the exact user-supplied ROM hash is accepted. Source-derived addresses are
also checked with real input/screen tests; this is NOT the original Red profile.
"""
from __future__ import annotations

import json
from pathlib import Path
from world_data import load_world_data, map_prior, move_prior, named_lookup

CHARACTERS = {
    0x7F: " ",
    **{0x80 + i: c for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
    **{0xA0 + i: c for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{0xF6 + i: c for i, c in enumerate("0123456789")},
    0xBA: "é", 0xE0: "'", 0xE1: "PK", 0xE2: "MN", 0xE3: "-", 0xE6: "?",
    0xE7: "!", 0xE8: ".", 0xEC: "▷", 0xED: "▶", 0xEE: "▼", 0xEF: "♂",
    0xF0: "$", 0xF1: "×", 0xF3: "/", 0xF4: ",", 0xF5: "♀",
    0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";",
    0x9E: "[", 0x9F: "]", 0xBB: "'d", 0xBC: "'l", 0xBD: "'s",
    0xBE: "'t", 0xBF: "'v", 0xE4: "'r", 0xE5: "'m", 0xEB: "▲",
}

FACING = {0: "down", 4: "up", 8: "left", 12: "right"}
NEIGHBORS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
PP_BONUS_SIGNATURE = bytes.fromhex(
    'c51ae098afe095e096e0973e05e0990604cdd038'
    '7e47cb37e60fcb3fcb3f4ff098fe0838023e078047fa1ed13d28030d20ed70c1c9')
DIVIDE_WRAPPER_SIGNATURE = bytes.fromhex('e5d5c5f0b8f53e0de0b8ea0020cd2071f1e0b8ea0020c1d1e1c9')


class PartyNotReady(ValueError):
    """An identified AddPartyMon call is awaiting the nickname interaction."""
    def __init__(self, details: dict):
        super().__init__('Party registration is awaiting nickname interaction')
        self.details = details


def decode_text(data: bytes, *, terminated: bool = True, unknown: str = "�") -> str:
    result = []
    for value in data:
        if terminated and value == 0x50:
            break
        result.append(CHARACTERS.get(value, unknown))
    return "".join(result).rstrip()


def load_profile() -> dict:
    return json.loads(Path(__file__).with_name("redstar-profile.json").read_text())


class Reader:
    def __init__(self, emulator, profile: dict):
        self.emulator = emulator
        self.profile = profile
        self.symbols = profile["addresses"]
        self._hof_registered = False
        rom = getattr(self.emulator, 'rom_bytes', b'')
        self._hof_counter_layout = rom.count(bytes.fromhex('21a2d57e3c280134')) == 1
        self._verified_name_tables = {kind: rom.count(bytes.fromhex(table['bytes_hex'])) == 1
                                     for kind, table in load_world_data()['name_tables'].items()}
        hm_test = bytes.fromhex('fec43803fec9c9a7c9')
        hm_address = rom.find(hm_test)
        self._verified_hm_ids = (rom.count(hm_test) == 1
            and bytes.fromhex('0f13394694ff') in rom[hm_address:hm_address + 64]
            and self._verified_name_tables['moves'])
        table = bytes.fromhex(load_world_data()['move_data_table']['bytes_hex'])
        self._verified_move_data = rom.count(table) == 1
        self._verified_pp_bonus = (rom.count(PP_BONUS_SIGNATURE) == 1
                                  and rom[0x38d0:0x38d0 + len(DIVIDE_WRAPPER_SIGNATURE)] == DIVIDE_WRAPPER_SIGNATURE)
        # Exact-ROM continuation immediately after _AddPartyMon -> AskName.
        # A return address for this continuation must be on the live CPU stack
        # before an empty last party structure is treated as pending creation.
        naming_return = bytes.fromhex('216bd1fa49cce60f280321a4d8')
        at = rom.find(naming_return)
        self._party_naming_return_pc = (at if at < 0x4000 else 0x4000 + at % 0x4000) if rom.count(naming_return) == 1 else None

    def address(self, name: str) -> int:
        return self.symbols[name]

    def data(self, name: str, length: int = 1, offset: int = 0) -> bytes:
        return self.emulator.read(self.address(name) + offset, length)

    def byte(self, name: str) -> int:
        return self.data(name)[0]

    def screen_text(self) -> dict:
        tiles = self.data("wTileMap", 360)
        # Keep rows/cursor positions. Unknown background tiles become spaces,
        # not guessed words. Dialog can be only partly printed this frame.
        rows = [decode_text(tiles[i:i+20], terminated=False, unknown=" ") for i in range(0, 360, 20)]
        return {"rows": rows, "source": "RAM wTileMap, not OCR",
                "limitations": "Background tiles may resemble letters; scrolling text may be incomplete."}

    def move(self, slot: int, move_id: int, raw_pp: int) -> dict:
        knowledge = move_prior(move_id)
        base_pp = knowledge.get('base_pp')
        pp_ups = raw_pp >> 6
        verified = bool(self._verified_move_data and self._verified_pp_bonus and isinstance(base_pp, int))
        maximum = base_pp + min(base_pp // 5, 7) * pp_ups if verified else None
        return {'slot': slot, 'move_id': move_id, 'pp': raw_pp & 63,
                'pp_ups': pp_ups, 'max_pp': maximum, 'max_pp_verified': verified,
                'max_pp_source': 'Entire 165x6-byte move table and exact-ROM AddBonusPP/Divide instructions; base + min(base//5,7) * high-two-bit PP Up count' if verified else 'needs_data: move table or PP Up calculation not verified against exact ROM',
                'knowledge': knowledge}

    def party(self) -> list[dict]:
        count = self.byte("wPartyCount")
        if count > 6:
            raise ValueError("Uninitialized or incompatible party count")
        stride = self.address("wPartyMon2") - self.address("wPartyMon1")
        if stride != 44:
            raise ValueError("Unsupported party structure")
        result = []
        for i in range(count):
            mon = self.data("wPartyMon1", 44, i * stride)
            hp = int.from_bytes(mon[1:3], "big")
            max_hp = int.from_bytes(mon[34:36], "big")
            if not 1 <= mon[33] <= 100 or not 0 <= hp <= max_hp <= 999 or max_hp == 0:
                pending = self._pending_party_registration(count, i, mon)
                if pending:
                    raise PartyNotReady(pending)
                raise ValueError("Party HP/level sanity check failed")
            result.append({
                "slot": i, "nickname": decode_text(self.data("wPartyMonNicks", 11, i * 11)),
                "species_internal_id": mon[0], "level": mon[33], "hp": hp, "max_hp": max_hp,
                "species_name_prior": named_lookup('species', mon[0]),
                "status_bits": mon[4],
                "moves": [self.move(j, mon[8+j], mon[29+j]) for j in range(4) if mon[8+j]],
            })
        return result

    def _pending_party_registration(self, count: int, slot: int, mon: bytes) -> dict | None:
        """Accept only the exact new-slot/nickname call state, never bad HP data.

        _AddPartyMon increments count and species first, asks about a nickname,
        then fills the 44-byte structure. This can await input indefinitely;
        advancing frames alone is not a repair. Nothing here presses buttons.
        """
        if slot != count - 1 or any(mon) or self._party_naming_return_pc is None:
            return None
        species = self.data('wPartySpecies', count + 1)
        if (species[-1] != 255 or not all(named_lookup('species', value) for value in species[:-1])
                or self.byte('wMonDataLocation') != 0 or self.byte('wNamingScreenType') != 2
                or self.byte('hNewPartyLength') != count or self.byte('wcf91') != species[slot]
                or self.byte('wFontLoaded') != 1):
            return None
        registers = getattr(self.emulator.game, 'register_file', None)
        stack_pointer = getattr(registers, 'SP', None)
        if not isinstance(stack_pointer, int) or not 0xc000 <= stack_pointer < 0xe000:
            return None
        stack = self.emulator.read(stack_pointer, min(128, 0xe000 - stack_pointer))
        return_address = self._party_naming_return_pc.to_bytes(2, 'little')
        if return_address not in stack:
            return None
        return {'ready': False, 'quality': 'verified_interactive_initialization',
                'phase': 'nickname_interaction_before_party_structure', 'pending_slot': slot,
                'pending_species_internal_id': species[slot],
                'pending_species_name_prior': named_lookup('species', species[slot]),
                'count_raw': count, 'registered_usable_count': None,
                'source': 'Exact-ROM AddPartyMon/AskName return address on live CPU stack; new party species list/count and naming context agree; last 44-byte structure remains all zero',
                'next_observation': 'Finish the visible nickname question/name entry, then wait for valid party fields; do not infer a usable team or completed starter receipt from the raw count.'}

    def bag(self) -> list[dict]:
        count = self.byte("wNumBagItems")
        if count > 20:
            raise ValueError("Uninitialized or incompatible bag count")
        raw = self.data("wBagItems", count * 2)
        result = []
        for i in range(0, len(raw), 2):
            item_id = raw[i]
            verified_name = (1 <= item_id <= len(load_world_data()['name_tables']['items']['names'])
                             and self._verified_name_tables['items'] or 196 <= item_id <= 200 and self._verified_hm_ids)
            result.append({'item_id': item_id, 'quantity': raw[i+1],
                           'name_prior': named_lookup('items', item_id), 'name_verified': verified_name,
                           'quality': 'verified_current_bag_and_rom_name' if verified_name else 'current_bag_source_name_prior'})
        return result

    def world(self, scene: dict | None = None) -> dict:
        """Source identity is advisory; current map geometry is RAM crosschecked."""
        map_id = self.byte('wCurMap')
        prior = map_prior(map_id)
        width, height = self.byte('wCurMapWidth') * 2, self.byte('wCurMapHeight') * 2
        if not prior or not 0 < width <= 256 or not 0 < height <= 256:
            return {'map_id': map_id, 'name': prior['name'] if prior else None,
                    'quality': 'needs_data', 'warps': [], 'objects': [], 'connections': [],
                    'source': 'No recognized current map geometry'}
        count = self.byte('wNumberOfWarps')
        if count > 32:
            raise ValueError('Current map warp count exceeds table capacity')
        raw = self.data('wWarpEntries', count * 4)
        source_tuples = [(w['y'], w['x'], w['destination_warp_id'], w['destination_map_id'] & 255)
                         for w in prior['warps']]
        tuples = [tuple(raw[i:i+4]) for i in range(0, len(raw), 4)]
        matched = (width == prior['width'] and height == prior['height'] and tuples == source_tuples)
        warps = []
        for i, (y, x, destination_warp, destination_raw) in enumerate(tuples):
            destination = self.byte('wLastMap') if destination_raw == 255 else destination_raw
            target = map_prior(destination)
            warps.append({'warp_id': i, 'x': x, 'y': y,
                          'destination_map_id': destination, 'destination_map_id_raw': destination_raw,
                          'destination_name': target['name'] if target else None,
                          'destination_warp_id': destination_warp,
                          'quality': 'verified_current_ram' if matched else 'needs_data',
                          'source': 'RAM wWarpEntries (y,x,destination warp,destination map); 255 uses wLastMap'})
        objects = []
        n_sprites = self.byte('wNumSprites')
        sprite_table_valid = matched and n_sprites == len(prior['objects']) and n_sprites <= 15
        missable = self.data('wMissableObjectList', 34)
        hidden = {}
        hidden_valid = False
        for i in range(0, 34, 2):
            if missable[i] == 255:
                hidden_valid = True
                break
            slot, flag = missable[i:i+2]
            if not 1 <= slot <= n_sprites:
                break
            hidden[slot] = bool(self.data('wMissableObjectFlags', 1, flag // 8)[0] & (1 << (flag % 8)))
        usable_scene = (scene or self.scene())['mode'] in ('overworld', 'dialog', 'main_menu')
        for obj in prior['objects']:
            item = dict(obj)
            item['source_x'], item['source_y'] = obj['x'], obj['y']
            item['present'] = None
            item['active'] = None
            if sprite_table_valid and usable_scene:
                slot = obj['object_id']
                state1 = self.data('wSpriteStateData1', 16, slot * 16)
                state2 = self.data('wSpriteStateData2', 16, slot * 16)
                text = self.data('wMapSpriteData', 2, (slot - 1) * 2)[1] & 0x3f
                x, y = state2[5] - 4, state2[4] - 4
                # Picture ID 0 is hidden/unloaded; image FF alone also means offscreen.
                coherent = state1[0] == obj['sprite_id'] and text == obj['text_id']
                if coherent and hidden_valid and hidden.get(slot, False):
                    item.update(present=False, active=False, visible=False,
                                quality='verified_hidden_sprite', source='RAM current map missable-object list and hidden bit')
                elif coherent and hidden_valid and 0 <= x < width and 0 <= y < height:
                    item.update(x=x, y=y, present=True, active=True, facing=FACING.get(state1[9]),
                                quality='verified_current_ram', visible=state1[2] != 255,
                                source='RAM sprite picture/text identity, state2 map coordinates minus 4')
                elif state1[0] == 0:
                    item.update(present=False, active=False, visible=False, quality='verified_absent_sprite',
                                source='RAM sprite picture ID is zero; source object may be hidden')
            objects.append(item)
        connections = []
        mask = self.byte('wMapConnections')
        for bit, direction, symbol in [(8,'north','wMapConn1Ptr'),(4,'south','wMapConn2Ptr'),
                                       (2,'west','wMapConn3Ptr'),(1,'east','wMapConn4Ptr')]:
            if not mask & bit:
                continue
            dest = self.byte(symbol)
            known = next((c for c in prior['connections']
                          if c['direction'] == direction and c['destination_map_id'] == dest), None)
            target = map_prior(dest)
            connections.append({**(known or {}), 'direction': direction, 'destination_map_id': dest,
                'destination_name': target['name'] if target else None,
                'quality': 'verified_current_ram' if matched and known else 'needs_data',
                'source': 'RAM connection flag and destination ID; alignment remains source prior'})
        return {'map_id': map_id, 'name': prior['name'], 'width': width, 'height': height,
                'player_position_valid': 0 <= self.byte('wXCoord') < width and 0 <= self.byte('wYCoord') < height,
                'warps': warps, 'objects': objects, 'signs': prior['signs'], 'connections': connections,
                'script_triggers': prior.get('script_triggers', []),
                'input_lock': {'ignored_buttons_mask': self.byte('wJoyIgnore'),
                    'scripted_movement_remaining': self.byte('wSimulatedJoypadStatesIndex'),
                    'quality': 'source_profile_live_ram', 'source': 'wJoyIgnore and wSimulatedJoypadStatesIndex'},
                'quality': 'verified_current_map_geometry' if matched else 'needs_data',
                'source_match': matched, 'source': {'commit': load_world_data()['source_commit'],
                    'source_binary_match': False, 'header': prior.get('source_header'),
                    'objects': prior.get('source_objects'), 'live_fields': 'map ID, dimensions, warp tuples, connections, sprite occupancy'},
                'limitations': 'Map/NPC names and script triggers are source priors. Warp destinations and dimensions crosschecked with current RAM. Unknown sprite occupancy never blocks movement.'}

    def battle(self) -> dict:
        battle_type = self.byte('wIsInBattle')
        if battle_type == 0:
            return {'active': False, 'verified': True, 'quality': 'verified_battle_flag'}
        if battle_type not in (1, 2):
            return {'active': None, 'verified': False, 'quality': 'needs_data', 'type_raw': battle_type}
        def combatant(symbol, nickname):
            mon = self.data(symbol, 29)
            hp, max_hp = int.from_bytes(mon[1:3], 'big'), int.from_bytes(mon[15:17], 'big')
            if not (named_lookup('species', mon[0]) and 1 <= mon[14] <= 100 and 0 <= hp <= max_hp <= 999 and max_hp > 0):
                raise ValueError('Battle combatant HP/level/species sanity failed')
            return {'nickname': decode_text(self.data(nickname, 11)), 'species_internal_id': mon[0],
                    'species_name_prior': named_lookup('species', mon[0]), 'level': mon[14],
                    'hp': hp, 'max_hp': max_hp, 'status_bits': mon[4],
                    'moves': [self.move(j, mon[8+j], mon[25+j]) for j in range(4) if mon[8+j]]}
        try:
            own = combatant('wBattleMon', 'wBattleMonNick')
            enemy = combatant('wEnemyMon', 'wEnemyMonNick')
            party = self.party()
            coherent = any(mon['species_internal_id'] == own['species_internal_id']
                           and mon['level'] == own['level'] and mon['max_hp'] == own['max_hp'] for mon in party)
            if not coherent:
                raise ValueError('Active battle mon does not correlate with current party')
        except ValueError as exc:
            tiles = self.data('wTileMap', 360)
            text_box = self._box(tiles, 0, 12, 20, 6)
            # The wild-intro text is drawn before the player battle structure
            # is initialized. The font flag can be 0 in this real interface.
            # Verify only the phase from battle flag + actual text-box border;
            # never publish stale/zero combatant structures as usable state.
            visible_text = '\n'.join(decode_text(tiles[y * 20 + 1:y * 20 + 19],
                        terminated=False, unknown=' ').strip() for y in range(13, 17)).strip() if text_box else None
            return {'active': True, 'type_raw': battle_type, 'verified': False,
                    'type': 'wild' if battle_type == 1 else 'trainer',
                    'quality': 'initializing' if text_box else 'needs_data',
                    'combatants_ready': False, 'phase': 'text_before_combatants_ready' if text_box else 'unknown',
                    'phase_verified': text_box, 'menu': 'text_or_animation' if text_box else 'unknown',
                    'visible_text': visible_text, 'awaiting_input': True if text_box and tiles[358] == 0xee else None,
                    'source': 'RAM battle flag and visible battle text-box border; combatant fields withheld until valid and correlated',
                    'error': str(exc)}
        rows = self.screen_text()['rows']
        text = '\n'.join(rows)
        menu = ('command' if all(word in text for word in ('FIGHT', 'RUN'))
                and any(word in text for word in ('PACK', 'ITEM')) else
                'move' if 'TYPE/' in text or 'TYPE' in text and any(m['knowledge'].get('name', '').replace('_',' ') in text for m in own['moves']) else
                'text_or_animation')
        selected_move_slot = None
        selected_command = None
        pointed = next((row.split('▶', 1)[1].strip() for row in rows if '▶' in row), '')
        if menu == 'move' and pointed:
            for move in own['moves']:
                names = load_world_data()['name_tables']['moves']['names']
                if 1 <= move['move_id'] <= len(names) and pointed == names[move['move_id'] - 1].rstrip('@'):
                    selected_move_slot = move['slot']
                    break
        elif menu == 'command' and pointed:
            label = pointed.split()[0]
            if label in ('FIGHT', 'PKMN', 'ITEM', 'PACK', 'RUN'):
                selected_command = label
        return {'active': True, 'type': 'wild' if battle_type == 1 else 'trainer',
                'type_raw': battle_type, 'player': own, 'enemy': enemy,
                'verified': True, 'quality': 'verified_coherent_battle_structures',
                'combatants_ready': True, 'phase': 'active', 'phase_verified': True,
                'menu': menu, 'menu_cursor_raw': self.byte('wCurrentMenuItem'),
                'selected_move_slot': selected_move_slot, 'selected_command': selected_command,
                'selection_source': 'Visible tilemap arrow plus matching menu label; normalized move slots start at 0, raw RAM cursor has menu-specific indexing',
                'menu_quality': 'visible_text_pattern' if menu != 'text_or_animation' else 'unknown',
                'source': 'RAM battle flag + current combatants + party correlation + visible tilemap text',
                'limitations': 'Enemy moves/PP are engine RAM, not necessarily player-visible. Move/type names are source priors. Cursor only applies to a visibly identified menu.'}

    def milestones(self, *, party=None, bag=None, world=None, party_ready=True) -> dict:
        party = self.party() if party is None else party
        bag = self.bag() if bag is None else bag
        world = self.world() if world is None else world
        def fact(value, verified, source):
            return {'value': value, 'verified': verified,
                    'quality': 'verified' if verified else 'source_profile_unverified', 'source': source}
        badge_bits = self.byte('wObtainedBadges')
        result = {'has_party': fact(bool(party), True, 'validated current party count and structures'),
                  'party_count': fact(len(party), True, 'validated wPartyCount'),
                  'party_move_ids': fact(sorted({m['move_id'] for mon in party for m in mon['moves']}),
                                         True, 'validated current party move bytes'),
                  'badge_bits': fact(badge_bits, True, 'validated wObtainedBadges'),
                  'badge_count': fact(badge_bits.bit_count(), True, 'popcount of wObtainedBadges')}
        if not party_ready:
            for key in ('has_party', 'party_count', 'party_move_ids'):
                result[key] = {'value': None, 'verified': False, 'quality': 'initializing',
                               'source': 'AddPartyMon is awaiting nickname interaction; raw count is not a usable party'}
        healing_observable = (party_ready and bool(party) and self.byte('wIsInBattle') == 0
            and all(mon.get('moves') and all(move.get('max_pp_verified') is True for move in mon['moves']) for mon in party))
        fully_healed = (all(mon['hp'] == mon['max_hp'] and mon['status_bits'] == 0
                             and all(move['pp'] == move['max_pp'] for move in mon['moves']) for mon in party)
                         if healing_observable else None)
        result['party_fully_healed'] = fact(fully_healed, healing_observable,
            'Fresh valid nonempty party outside battle; every HP equals maxHP, status is clear, and every PP equals exact-ROM-verified maximum. No dialogue or location inference.')
        for bit, badge in enumerate(('boulder','cascade','thunder','rainbow','soul','marsh','volcano','earth')):
            result['badge_' + badge] = fact(bool(badge_bits & 1 << bit), True, 'wObtainedBadges bit ' + str(bit))
        event_names = {
            'oak_appeared_in_pallet': 'EVENT_OAK_APPEARED_IN_PALLET',
            'followed_oak_into_lab': 'EVENT_FOLLOWED_OAK_INTO_LAB',
            'oak_asked_to_choose_mon': 'EVENT_OAK_ASKED_TO_CHOOSE_MON',
            'starter_received': 'EVENT_GOT_STARTER', 'rival_lab_battled': 'EVENT_BATTLED_RIVAL_IN_OAKS_LAB',
            'oak_parcel_received': 'EVENT_GOT_OAKS_PARCEL', 'parcel_delivered': 'EVENT_OAK_GOT_PARCEL',
            'pokedex_received': 'EVENT_GOT_POKEDEX', 'ss_ticket_received': 'EVENT_GOT_SS_TICKET',
            'cut_received': 'EVENT_GOT_HM01', 'surf_received': 'EVENT_GOT_HM03',
            'strength_received': 'EVENT_GOT_HM04', 'poke_flute_received': 'EVENT_GOT_POKE_FLUTE',
            'silph_liberated': 'EVENT_BEAT_SILPH_CO_GIOVANNI',
            'fuji_rescued': 'EVENT_RESCUED_MR_FUJI',
            'route12_snorlax_cleared': 'EVENT_BEAT_ROUTE12_SNORLAX',
            'elite4_lorelei_defeated': 'EVENT_BEAT_LORELEIS_ROOM_TRAINER_0',
            'elite4_bruno_defeated': 'EVENT_BEAT_BRUNOS_ROOM_TRAINER_0',
            'elite4_agatha_defeated': 'EVENT_BEAT_AGATHAS_ROOM_TRAINER_0',
            'elite4_lance_defeated': 'EVENT_BEAT_LANCE', 'champion_defeated': 'EVENT_BEAT_CHAMPION_RIVAL'}
        verified_flags = self.profile.get('campaign_validation', {}).get('verified_event_flags', [])
        for key, name in event_names.items():
            event = load_world_data()['events'][name]
            value = bool(self.data('wEventFlags', 1, event['offset'])[0] & 1 << event['bit'])
            result[key] = fact(value, name in verified_flags,
                f"RAM wEventFlags+{event['offset']} bit {event['bit']}; pinned source {name}")
        # Inventory is current possession, not permanent receipt. Never infer a
        # discarded/consumed quest item was never obtained merely from absence.
        inventory = {item['name_prior']: item for item in bag if item['quantity'] > 0}
        for key, name in {'oak_parcel_received':'OAKS_PARCEL','ss_ticket_received':'S_S_TICKET',
                          'cut_received':'HM_01','surf_received':'HM_03','strength_received':'HM_04',
                          'poke_flute_received':'POKE_FLUTE','silph_scope_received':'SILPH_SCOPE','secret_key_received':'SECRET_KEY',
                          'gold_teeth_received':'GOLD_TEETH','lift_key_received':'LIFT_KEY',
                          'card_key_received':'CARD_KEY'}.items():
            if name in inventory and inventory[name].get('name_verified'):
                result[key] = fact(True, True, 'current bag ID and quantity; entire item-name table or HM-range/move-order matched to exact ROM')
            elif key not in result:
                result[key] = fact(True if name in inventory else None, False,
                    'current bag ID matched to unverified pinned-source item name; absence is not nonreceipt')
        result['pokedex_owned_count'] = fact(sum(b.bit_count() for b in self.data('wPokedexOwned', 19)),
                                            False, 'source-address pokedex bitset, not possession of device')
        hof_count = self.byte('wNumHoFTeams')
        # Exact source increment sequence at a unique location in the supplied
        # ROM confirms the counter's address. It does not alone prove completion.
        counter_layout = self._hof_counter_layout
        result['hall_of_fame_count'] = fact(hof_count, counter_layout,
            'wNumHoFTeams; unique exact-ROM ld hl,$d5a2 / increment-and-saturate sequence from AnimateHallOfFame')
        visible_registration = ('HALL OF FAME' in '\n'.join(self.screen_text()['rows'])
                                and world.get('name') == 'HALL_OF_FAME'
                                and world.get('source_match') is True and hof_count > 0
                                and counter_layout and badge_bits == 255 and bool(party))
        if visible_registration:
            self._hof_registered = True
        result['hall_of_fame_entered'] = fact(world.get('name') == 'HALL_OF_FAME' and world.get('source_match') is True,
                                            False, 'current map identity alone does not prove Hall of Fame registration')
        result['game_completed'] = fact(True if self._hof_registered else None, self._hof_registered,
            'Observed HALL OF FAME registration text + source-matched Hall of Fame map + exact-ROM durable counter + 8 badges + validated party; latches only after conjunction')
        return result

    def _background_collision(self) -> tuple[int, set[int]] | None:
        # Pinned Red Star home.asm includes data/collision.asm in ROM0.
        # wTilesetBank is the GRAPHICS bank; using it for collision is wrong.
        # Unknown/banked pointers fail closed rather than guessing a bank.
        ptr = int.from_bytes(self.data("wTilesetCollisionPtr", 2), "little")
        if not 0 < ptr < 0x4000:
            return None
        raw = self.emulator.rom_bytes[ptr:min(ptr + 128, 0x4000)]
        if 255 not in raw or raw[0] == 255:
            return None
        return ptr, set(raw[:raw.index(255)])

    @staticmethod
    def _box(tiles: bytes, x: int, y: int, width: int, height: int) -> bool:
        """Recognize the actual font border, not the presence/absence of text."""
        def at(dx, dy):
            return tiles[(y + dy) * 20 + x + dx]
        return (at(0, 0) == 0x79 and at(width - 1, 0) == 0x7B
                and at(0, height - 1) == 0x7D and at(width - 1, height - 1) == 0x7E
                and all(at(dx, 0) == 0x7A for dx in range(1, width - 1))
                and all(at(dx, height - 1) in (0x7A, 0xEE) for dx in range(1, width - 1))
                and all(at(0, dy) == at(width - 1, dy) == 0x7C for dy in range(1, height - 1)))

    def scene(self) -> dict:
        tiles = self.data("wTileMap", 360)
        font = self.byte("wFontLoaded")
        mode = "unknown"
        rows = self.screen_text()['rows']
        text = '\n'.join(rows)
        compact = text.replace(' ', '').replace('\n', '')
        battle = self.battle() if self.byte('wIsInBattle') in (1, 2) else None
        if battle and (battle.get('verified') or battle.get('phase_verified')):
            mode = 'battle'
        # A verified battle phase may precede initialized combatant fields.
        # Other interfaces still require their own visible pattern below.
        if self.byte("wIsInBattle") == 0 and font == 1:
            menu_rows = [decode_text(tiles[y * 20 + 12:y * 20 + 19],
                                     terminated=False, unknown=" ").strip() for y in range(18)]
            menu_border = any(self._box(tiles, 10, 0, 10, height) for height in (14, 16))
            if ('NAME?' in text and ('ABCDEFGHI' in compact or 'abcdefghi' in compact)
                    and ('lower case' in text or 'UPPER CASE' in text)):
                mode = 'name_entry'
            elif any('HT ' in row for row in rows) and any('WT ' in row for row in rows):
                mode = 'species_preview'
            elif menu_border and {"PACK", "SAVE", "OPTION", "EXIT"}.issubset(menu_rows):
                mode = "main_menu"
            elif self._box(tiles, 0, 12, 20, 6):
                mode = "dialog"
        elif self.byte("wIsInBattle") == 0 and font == 0:
            collision = self._background_collision()
            # The source samples tile (8,9) under the centered player and uses
            # the same grid for adjacent movement checks. Reject transitions.
            if (collision and tiles[9 * 20 + 8] in collision[1]
                    and self.byte("PlayerYPixels") == 60 and self.byte("PlayerXPixels") == 64
                    and self.byte("PlayerFacingDirection") in FACING):
                mode = "overworld"
        if mode == 'overworld':
            width, height = self.byte('wCurMapWidth') * 2, self.byte('wCurMapHeight') * 2
            if width and height and not (self.byte('wXCoord') < width and self.byte('wYCoord') < height):
                mode = 'transition'
        return {"mode": mode, "verified": mode != "unknown",
                "quality": "verified_pattern" if mode != "unknown" else "needs_data",
                "source": "RAM font flag, battle flag/text-box phase or correlated combatants, tile borders/menu labels, ROM0 background and player alignment",
                "validation_scope": "Exact ROM starting room, N64 dialog, main menu, rival battle and wild intro; battle stats require correlated structures independently of phase",
                "limitations": "Conservative pattern classification; unrecognized scenes remain unknown. Blank text alone does not identify a scene."}

    def dialog(self, scene: dict | None = None) -> dict:
        scene = scene or self.scene()
        mode = scene["mode"]
        if mode == "dialog":
            tiles = self.data("wTileMap", 360)
            lines = [decode_text(tiles[y * 20 + 1:y * 20 + 19], terminated=False, unknown=" ").strip()
                     for y in range(13, 17)]
            arrow = tiles[17 * 20 + 18] == 0xEE
            return {"open": True, "awaiting_input": True if arrow else None,
                    "text": "\n".join(line for line in lines if line),
                    "choices": ['YES', 'NO'] if any('YES' in line for line in self.screen_text()['rows']) and any('NO' in line for line in self.screen_text()['rows']) else [],
                    "quality": "verified_text_box_partial_text_possible",
                    "source": "RAM wTileMap dialog border/interior and visible down arrow",
                    "awaiting_input_reason": "visible_down_arrow" if arrow else "arrow_absent_or_blinking; printing_or_waiting_unknown"}
        known = mode in ("overworld", "main_menu")
        return {"open": False if known else None, "awaiting_input": False if known else None,
                "text": "" if known else None,
                "quality": "verified_closed" if known else "needs_data",
                "source": "Verified scene pattern" if known else "No recognized scene pattern"}

    def local_map(self, scene: dict | None = None) -> dict | None:
        scene = scene or self.scene()
        if scene["mode"] != "overworld":
            return None
        collision = self._background_collision()
        if collision is None:
            return None
        ptr, passable = collision
        tiles = self.data("wTileMap", 360)
        def tile(x, y):
            return tiles[(2 * y + 1) * 20 + 2 * x]
        rows = ["".join("@" if (x, y) == (4, 4) else "." if tile(x, y) in passable else "#"
                        for x in range(10)) for y in range(9)]
        neighbors = {button: {"dx": dx, "dy": dy, "tile_id": tile(4 + dx, 4 + dy),
                              "background_passable": tile(4 + dx, 4 + dy) in passable}
                     for button, (dx, dy) in NEIGHBORS.items()}
        return {"rows": rows, "player_cell": {"x": 4, "y": 4}, "neighbors": neighbors,
                "legend": "@ player; . background permits walking; # background does not permit walking",
                "quality": "advisory_background_only", "verified": True,
                "source": f"RAM wTileMap lower-left tiles; exact ROM0 collision table at 0x{ptr:04x}",
                "validation_scope": "All four neighbors and coordinate/facing responses tested in starting room",
                "limitations": "Not complete collision or route data. Does not identify NPCs, objects, exits, warps, ledges or scripted movement. Never filters buttons."}

    def background_hint(self) -> dict | None:
        # Compatibility for raw evidence consumers; model uses explicit local_map.
        return self.local_map()

    def snapshot(self) -> dict:
        errors = {}
        pending = {}
        def section(name, fn):
            try:
                return fn()
            except PartyNotReady as exc:
                pending[name] = exc.details
                return None
            except (ValueError, KeyError, IndexError) as exc:
                errors[name] = str(exc)
                return None
        def player():
            money = self.data("wPlayerMoney",3)
            if any((b>>4)>9 or (b&15)>9 for b in money):
                raise ValueError("Invalid BCD money")
            value = 0
            for b in money:
                value = value*100 + (b>>4)*10 + (b&15)
            facing = FACING.get(self.byte("PlayerFacingDirection")) if scene and scene["verified"] else None
            return {"name":decode_text(self.data("wPlayerName",11)),
                    "map_id":self.byte("wCurMap"),"x":self.byte("wXCoord"),"y":self.byte("wYCoord"),
                    "money":value,"badge_bits":self.byte("wObtainedBadges"),
                    "facing":facing, "facing_source":"RAM PlayerFacingDirection at 0xc109",
                    "facing_quality":"verified_direction_response" if facing is not None else "needs_data"}
        scene = section("scene", self.scene)
        local_map = section("local_map", lambda: self.local_map(scene)) if scene else None
        party = section('party', self.party)
        bag = section('bag', self.bag)
        world = section('world', lambda: self.world(scene))
        return {
            "game":"Pokemon Red Star 2020-08-18", "observation_mode":"read_only_ram",
            "frame":self.emulator.game.frame_count,
            "screen_text":section("screen_text", self.screen_text),
            "player":section("player",player), "party":party, "bag":bag,
            "party_state": pending.get('party', {'ready': party is not None,
                'quality': 'verified' if party is not None else 'needs_data'}),
            "world":world,
            "milestones":section('milestones', lambda: self.milestones(party=party if party is not None else [],bag=bag,world=world,party_ready=party is not None)) if (party is not None or 'party' in pending) and bag is not None and world is not None else None,
            "battle":section('battle_state', self.battle),
            "battle_type_raw":section("battle",lambda:self.byte("wIsInBattle")),
            "menu_cursor_raw":section("menu",lambda:self.byte("wCurrentMenuItem")),
            "scene":scene, "dialog":section("dialog",lambda:self.dialog(scene)) if scene else None,
            "local_map":local_map, "background_hint":local_map,
            "limitations":["RAM can retain intro/menu/previous-scene values.",
                            "Menu cursor is meaningful only with a visible matching menu.",
                            "Source world names, NPC roles and move descriptions are version-labelled priors.",
                            "Unknown events and terminal success are not inferred from exploration."],
            "errors":errors,
        }
