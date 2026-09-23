"""The model's observation boundary, not a game-playing policy.

Structured-player mode permits own inventory/status without opening their UI,
current position, text/menu and viewport geometry. It excludes source攻略,
offscreen objects, unexplored destinations and opponent internals. The raw
Reader remains available to independent diagnostics/evaluation, never to models.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import math

POLICY = 'structured_player_v1'
MODES = {'overworld', 'dialog', 'main_menu', 'battle', 'name_entry', 'species_preview'}
FACTS = {'party_count', 'badge_count'}
PROGRESS_FIELDS = ('loop_detected', 'loop_kind', 'same_position_steps', 'steps_since_new_tile',
                   'visited_tiles', 'total_steps', 'neighbor_visits', 'untried_directions',
                   'direction_outcomes', 'repeated_interactions', 'repeated_text_observations')


def take(row, fields):
    return {k: deepcopy(row[k]) for k in fields if k in row} if isinstance(row, dict) else {}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()[:20]


def clean_text(value, limit=2000):
    return value[:limit] if isinstance(value, str) else None


def point(game):
    world = game.get('world') or {}
    p = game.get('player') or {}
    values = [p.get(k) for k in ('map_id', 'x', 'y')]
    if world.get('source_match') is True and world.get('player_position_valid') is True and all(type(n) is int and n >= 0 for n in values):
        return values
    return None


def own_mon(mon):
    """Self status and public move rule descriptions; no ranked/optimal actions."""
    out = take(mon, ('slot', 'nickname', 'species_internal_id', 'species_name_prior',
                     'level', 'hp', 'max_hp', 'status_bits', 'experience', 'types',
                     'attack', 'defense', 'speed', 'special'))
    out['moves'] = []
    for move in mon.get('moves') or []:
        row = take(move, ('slot', 'move_id', 'pp', 'max_pp', 'max_pp_verified',
                          'max_pp_quality', 'disabled'))
        info = take(move.get('knowledge'), ('name', 'type_id', 'power', 'accuracy_percent',
                                           'base_pp', 'effect', 'numeric_data_verified'))
        if info:
            info['scope'] = 'public_move_rule_reference_not_a_recommendation'
            row['knowledge'] = info
        out['moves'].append(row)
    return out


def project(raw):
    """Explicit allowlist. Idempotent on an already projected observation."""
    if raw.get('observation_policy') == POLICY:
        # Keep only the exact public keys, not an attached campaign/raw debug payload.
        result = take(raw, ('observation_policy', 'observation_id', 'frame', 'game', 'scene',
                          'dialog', 'screen_text', 'player', 'party', 'party_state', 'bag',
                          'battle', 'world', 'local_map', 'milestones', 'main_menu_cursor',
                          'progress', 'unavailable'))
        result['progress'] = take(raw.get('progress'), PROGRESS_FIELDS)
        return result
    scene = raw.get('scene') or {}
    verified = scene.get('verified') is True and scene.get('mode') in MODES
    mode = scene.get('mode') if verified else 'unknown'
    dialog = raw.get('dialog') or {}
    text = [clean_text(line, 240) for line in (raw.get('screen_text') or {}).get('rows', [])
            if isinstance(line, str)][:24]
    if mode == 'overworld':
        text = []
    elif mode == 'dialog' and isinstance(dialog.get('text'), str):
        text = dialog['text'][:2000].splitlines()
    player = take(raw.get('player'), ('name', 'map_id', 'x', 'y', 'money', 'badge_bits'))
    if (raw.get('player') or {}).get('facing_quality') == 'verified_direction_response':
        player['facing'] = raw['player'].get('facing')
    world = raw.get('world') or {}
    # Dimensions/coordinates are explicitly permitted structured state, not a full map.
    public_world = take(world, ('map_id', 'width', 'height', 'player_position_valid'))
    public_world.update(source_match=world.get('source_match') is True,
                        quality='structured_geometry_not_source_routes')
    public_world['input_lock'] = take(world.get('input_lock'),
                                      ('ignored_buttons_mask', 'scripted_movement_remaining'))
    local = raw.get('local_map') or {}
    usable_grid = (mode == 'overworld' and local.get('verified') is True
                   and local.get('quality') == 'advisory_background_only'
                   and world.get('source_match') is True and world.get('player_position_valid') is True)
    grid = take(local, ('rows', 'player_cell', 'neighbors', 'legend', 'quality')) if usable_grid else None
    if grid:
        grid['verified'] = True
        grid['source'] = 'current viewport background; not proof of traversability'
    cells = set()
    if grid:
        center = grid.get('player_cell') or {'x': 4, 'y': 4}
        for gy, row in enumerate(grid.get('rows') or []):
            for gx, tile in enumerate(row):
                if tile != '?':
                    cells.add((player.get('x', 0) + gx - center['x'], player.get('y', 0) + gy - center['y']))
    public_world['objects'] = []
    # Do not infer roles, scripts, hidden items or destinations from sprite metadata.
    if world.get('source_match') is True and mode == 'overworld':
        for obj in world.get('objects') or []:
            if obj.get('active') is True and obj.get('visible') is True and all(type(obj.get(k)) is int for k in ('x', 'y')):
                row = take(obj, ('object_id', 'sprite', 'x', 'y', 'facing'))
                row.update(active=True, visible=True, quality='observed_on_screen',
                           label_scope='sprite_appearance_not_quest_role')
                public_world['objects'].append(row)
    # A current viewport doorway is known locally; its destination is NOT known.
    public_world['warps'] = []
    for index, warp in enumerate(world.get('warps') or []):
        if usable_grid and (warp.get('x'), warp.get('y')) in cells and warp.get('quality') == 'verified_current_ram':
            row = take(warp, ('warp_id', 'x', 'y'))
            row.setdefault('warp_id', index)
            row['quality'] = 'viewport_portal_location_only'
            public_world['warps'].append(row)
    public_world['connections'] = []  # Discovered exclusively by actual traversal.
    facts = raw.get('milestones') or {}
    party = raw.get('party')
    count = facts.get('party_count') or {}
    ready = raw.get('party_state') or {}
    valid_party = (isinstance(party, list) and count.get('verified') is True
                   and count.get('value') == len(party) and ready.get('ready') is not False)
    bag = raw.get('bag')
    valid_bag = isinstance(bag, list) and all(isinstance(r, dict) and r.get('name_verified') is True for r in bag)
    battle = raw.get('battle') or {}
    public_battle = take(battle, ('active', 'verified', 'type', 'phase', 'phase_verified',
                                  'combatants_ready', 'menu', 'selected_command', 'selected_move_slot',
                                  'awaiting_input', 'visible_text'))
    if battle.get('active') is True and battle.get('verified') is True:
        own = battle.get('player')
        if isinstance(own, dict):
            public_battle['player'] = own_mon(own)
        enemy = battle.get('enemy') or {}
        public_battle['enemy'] = take(enemy, ('nickname', 'species_name_prior', 'level', 'status_bits'))
        hp, maximum = enemy.get('hp'), enemy.get('max_hp')
        if type(hp) is int and type(maximum) is int and 0 <= hp <= maximum and maximum > 0:
            public_battle['enemy']['health_bar_units'] = math.ceil(48 * hp / maximum)
            public_battle['enemy']['health_bar_scale'] = 48
            public_battle['enemy']['health_source'] = 'quantized_RAM_ratio_not_exact_screen_pixel_decode'
    out = {
        'observation_policy': POLICY, 'frame': raw.get('frame'), 'game': raw.get('game'),
        'scene': {'mode': mode, 'verified': verified},
        'dialog': take(dialog, ('open', 'awaiting_input', 'text')) if verified else
                  {'open': None, 'awaiting_input': None, 'text': None},
        'screen_text': {'rows': text, 'quality': 'scene_checked' if verified else 'unclassified_tile_text'},
        'player': player, 'world': public_world, 'local_map': grid,
        'party': [own_mon(mon) for mon in party] if valid_party else None,
        'party_state': take(ready, ('ready', 'verified', 'quality')),
        'bag': [take(row, ('item_id', 'name_prior', 'name_verified', 'quantity')) for row in bag] if valid_bag else None,
        'battle': public_battle,
        'milestones': {k: take(v, ('value', 'verified')) for k, v in facts.items()
                       if k in FACTS and isinstance(v, dict)},
        'main_menu_cursor': raw.get('menu_cursor_raw') if mode == 'main_menu' else None,
        'progress': take(raw.get('progress'), PROGRESS_FIELDS),
        'unavailable': ['Unvisited maps, portal destinations before traversal, hidden scripts and offscreen objects.',
                        'Enemy exact HP/stats/moves and unobserved storyline flags.',
                        'No auto-healing, default fleeing, move recommendation or walkthrough.'],
    }
    if isinstance(out['dialog'].get('text'), str):
        out['dialog']['text'] = out['dialog']['text'][:2000]
    # Identity represents actionable observations, not a wall clock or a blink frame.
    identity = {k: v for k, v in out.items() if k not in ('frame', 'progress')}
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [stable(v) for v in value]
        if isinstance(value, str):
            return value.translate(str.maketrans('', '', '▼▲▶▷')).rstrip()
        return value
    out['observation_id'] = 'obs:' + digest(stable(identity))
    return out
