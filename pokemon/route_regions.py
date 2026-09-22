"""Source-labelled land regions and portals, never a controller.

Rebuild the bundled early-game topology from the pinned source checkout with
``python route_regions.py /path/to/redstarbluestar``. Tile components are not
map IDs: entering the other side of Route 2/4 can require leaving that map.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import subprocess

from world_data import SOURCE_COMMIT, load_world_data

DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
COMPASS = {"north": "up", "south": "down", "west": "left", "east": "right"}
# This is a coverage boundary, not a route or an input script. Later field-move
# and puzzle maps must not silently inherit full-map connectivity assumptions.
EARLY_MAP_IDS = {0, 1, 2, 3, 12, 13, 14, 15, 35, 36, 37, 38, 39, 40,
                 41, 42, 43, 44, 47, 50, 51, 52, 53, 54, 55, 56, 57, 58,
                 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 88}
LIMITATIONS = (
    "Pinned-source land topology is advisory, not verified live terrain. "
    "Coverage is the opening through Cerulean/Bill; unsupported maps return needs_data. "
    "NPCs, story gates, trainers, items, CUT/SURF, switches and puzzles require live decisions. "
    "A region route does not prove a portal is currently usable or an objective complete."
)


def _number(text):
    return int(text[1:], 16) if text.startswith('$') else int(text)


def _incbins(text):
    result = {}; labels = []
    for raw in text.splitlines():
        line = raw.partition(';')[0].strip()
        if not line:
            continue
        match = re.match(r'^(\w+)::?\s*(.*)$', line)
        if match:
            labels.append(match[1]); line = match[2]
            if not line:
                continue
        incbin = re.match(r'INCBIN\s+"([^"]+)"', line)
        if incbin:
            for label in labels:
                result[label] = incbin[1]
        labels = []
    return result


def _region(entry, x, y):
    if not entry or not (0 <= x < entry['width'] and 0 <= y < entry['height']):
        return None
    region = entry['region_rows'][y][x]
    return region if region >= 0 else None


def build(source: Path) -> dict:
    """Derive components from map blocks, blocksets, and source collision rules."""
    source = Path(source)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != SOURCE_COMMIT:
        raise ValueError('Region topology requires the pinned source revision')
    world = load_world_data()['maps']
    binaries = _incbins((source / 'main.asm').read_text())
    binaries.update(_incbins((source / 'data/collision.asm').read_text()))
    constants = {name: i for i, name in enumerate(re.findall(
        r'^\s*const\s+(\w+)', (source / 'constants/tilesets.asm').read_text(), re.M))}
    tilesets = []
    for match in re.finditer(r'^\s*tileset\s+([^;\n]+)', (source / 'data/tileset_headers.asm').read_text(), re.M):
        args = [part.strip() for part in match[1].split(',')]
        tilesets.append((binaries[args[0]], binaries[args[2]],
                         {_number(value) for value in args[3:6]} - {255}))
    collision_source = (source / 'home/overworld.asm').read_text()
    pairs_text = collision_source.split('TilePairCollisionsLand::', 1)[1].split('TilePairCollisionsWater::', 1)[0]
    pairs = {}
    for tileset, a, b in re.findall(r'db\s+(\w+),\s*(\$[\dA-Fa-f]+),\s*(\$[\dA-Fa-f]+)', pairs_text):
        pairs.setdefault(constants[tileset], set()).add(frozenset((_number(a), _number(b))))
    ledges = []
    for direction, a, b in re.findall(r'db\s+SPRITE_FACING_(\w+),\s*(\$[\dA-Fa-f]+),\s*(\$[\dA-Fa-f]+)',
                                     (source / 'engine/overworld/ledges.asm').read_text()):
        ledges.append((direction.lower(), _number(a), _number(b)))
    maps = {}; tiles_by_map = {}; skipped = {}
    rule_files = ('main.asm', 'data/collision.asm', 'data/tileset_headers.asm',
                  'constants/tilesets.asm', 'macros/data_macros.asm',
                  'home/overworld.asm', 'engine/overworld/ledges.asm',
                  'engine/overworld/player_state.asm')
    files = {relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
             for relative in rule_files}
    for mid in sorted(EARLY_MAP_IDS):
        prior = world[str(mid)]
        header_path = prior.get('source_header')
        if not header_path:
            skipped[str(mid)] = 'missing_source_header'; continue
        header = (source / header_path).read_text()
        token = re.search(r'^\s*db\s+([\w$]+)', header, re.M)[1]
        tileset = constants[token] if token in constants else _number(token)
        label = re.search(r'^\s*dw\s+(\w+Blocks)\s*,', header, re.M)[1]
        block_file = binaries[label]
        blockset_file, collision_file, counter_tiles = tilesets[tileset]
        blocks, blockset = (source / block_file).read_bytes(), (source / blockset_file).read_bytes()
        collision = set((source / collision_file).read_bytes().split(b'\xff', 1)[0])
        width, height = prior['width'], prior['height']
        if len(blocks) != width * height // 4:
            skipped[str(mid)] = 'block_count_does_not_match_dimensions'; continue
        # The engine samples each walking tile's lower-left 8px tile.
        tiles = [[blockset[blocks[(y // 2) * (width // 2) + x // 2] * 16
                            + (2 * (y % 2) + 1) * 4 + 2 * (x % 2)]
                  for x in range(width)] for y in range(height)]
        rows = [[-1] * width for _ in range(height)]; count = 0
        forbidden = pairs.get(tileset, set())
        for y in range(height):
            for x in range(width):
                if rows[y][x] >= 0 or tiles[y][x] not in collision:
                    continue
                rows[y][x] = count; queue = deque([(x, y)])
                while queue:
                    px, py = queue.popleft()
                    for dx, dy in DIRECTIONS.values():
                        nx, ny = px + dx, py + dy
                        if not (0 <= nx < width and 0 <= ny < height) or rows[ny][nx] >= 0:
                            continue
                        if tiles[ny][nx] not in collision or frozenset((tiles[py][px], tiles[ny][nx])) in forbidden:
                            continue
                        rows[ny][nx] = count; queue.append((nx, ny))
                count += 1
        entry = {'map_id': mid, 'name': prior['name'], 'width': width, 'height': height,
                 'tileset_id': tileset, 'region_count': count, 'region_rows': rows,
                 'counter_cells': [{'x': x, 'y': y, 'tile_id': tiles[y][x], 'quality': 'source_prior'}
                                   for y in range(height) for x in range(width) if tiles[y][x] in counter_tiles],
                 'warps': deepcopy(prior['warps']), 'connections': [], 'quality': 'source_prior',
                 'source_files': [header_path, block_file, blockset_file, collision_file]}
        for match in re.finditer(r'(NORTH|SOUTH|WEST|EAST)_MAP_CONNECTION\s+([^;\n]+)', header):
            args = [part.strip() for part in match[2].split(',')]
            original = next(c for c in prior['connections'] if c['direction'] == match[1].lower())
            entry['connections'].append({**original, 'coordinate_alignment': -2 * (_number(args[2]) - _number(args[3]))})
        maps[str(mid)] = entry; tiles_by_map[mid] = tiles
        for relative in entry['source_files']:
            files[relative] = hashlib.sha256((source / relative).read_bytes()).hexdigest()
    edges = []

    def add_edge(mid, x, y, destination, ax, ay, transition):
        a, b = _region(maps.get(str(mid)), x, y), _region(maps.get(str(destination)), ax, ay)
        if a is None or b is None:
            return
        edges.append({'from': [mid, a], 'to': [destination, b],
                      'transition': {**transition, 'x': x, 'y': y,
                                     'destination_map_id': destination, 'destination_name': maps[str(destination)]['name'], 'arrival': [ax, ay],
                                     'quality': 'source_prior', 'source': 'pinned_source_land_regions_and_portals'}})

    for key, entry in maps.items():
        mid = int(key)
        for warp in entry['warps']:
            destination = warp['destination_map_id']
            if destination == -1:
                # -1 is wLastMap. Resolve only a unique source outdoor parent;
                # never connect this exit indiscriminately to every outdoor map.
                parents = [int(k) for k, prior in world.items() if int(k) <= 36
                           and any(w['destination_map_id'] == mid for w in prior['warps'])]
                if len(parents) != 1:
                    continue
                destination = parents[0]
            other = maps.get(str(destination))
            index = warp['destination_warp_id']
            if not other or not (0 <= index < len(other['warps'])):
                continue
            arrival = other['warps'][index]
            add_edge(mid, warp['x'], warp['y'], destination, arrival['x'], arrival['y'], {'kind': 'warp', **warp})
        for link in entry['connections']:
            destination = link['destination_map_id']; other = maps.get(str(destination))
            if not other:
                continue
            direction = link['direction']; offset = link['coordinate_alignment']
            if direction in ('north', 'south'):
                points = [(x, 0 if direction == 'north' else entry['height'] - 1,
                           x + offset, other['height'] - 1 if direction == 'north' else 0)
                          for x in range(entry['width'])]
            else:
                points = [(0 if direction == 'west' else entry['width'] - 1, y,
                           other['width'] - 1 if direction == 'west' else 0, y + offset)
                          for y in range(entry['height'])]
            for x, y, ax, ay in points:
                add_edge(mid, x, y, destination, ax, ay, {'kind': 'connection', **link})
        if entry['tileset_id'] == 0:
            tiles = tiles_by_map[mid]
            for y in range(entry['height']):
                for x in range(entry['width']):
                    for direction, start_tile, ledge_tile in ledges:
                        dx, dy = DIRECTIONS[direction]; nx, ny = x + dx, y + dy
                        if (tiles[y][x] == start_tile and 0 <= nx < entry['width'] and 0 <= ny < entry['height']
                                and tiles[ny][nx] == ledge_tile):
                            add_edge(mid, x, y, mid, x + 2 * dx, y + 2 * dy,
                                     {'kind': 'ledge', 'button': direction,
                                      'source_rule': 'engine/overworld/ledges.asm:LedgeTiles'})
    # Same-region ledges add no reachability and would only clutter plans.
    edges = [e for e in edges if e['from'] != e['to']]
    return {'schema_version': 1, 'source_commit': SOURCE_COMMIT, 'source_binary_match': False,
            'quality': 'source_prior', 'generation': 'lower-left walking tiles; passable collision list; tileset-header counters; forbidden tile pairs; directional ledges; exact warp indices and connection coordinate alignment',
            'limitations': LIMITATIONS, 'source_sha256': files, 'maps': maps, 'edges': edges, 'skipped_maps': skipped}


@lru_cache(maxsize=1)
def load_regions():
    return json.loads(Path(__file__).with_name('redstar-route-regions.json').read_text())


def interaction_positions(map_id, target):
    """Source-advisory standing cells for an object, including real counters.

    A two-cell interaction is allowed only when the middle walking cell's
    lower-left tile matches one of this map's three tileset counter IDs.
    The caller must still compare the live map/object and let JEV select input.
    """
    entry = load_regions()['maps'].get(str(map_id))
    tx, ty = target.get('x'), target.get('y')
    if not entry or type(tx) is not int or type(ty) is not int:
        return []
    counters = {(cell['x'], cell['y']) for cell in entry.get('counter_cells', [])}
    positions = []
    for facing, (dx, dy) in DIRECTIONS.items():
        for distance in (1, 2):
            x, y = tx - dx * distance, ty - dy * distance
            if _region(entry, x, y) is None:
                continue
            if distance == 2 and (tx - dx, ty - dy) not in counters:
                continue
            positions.append({'x': x, 'y': y, 'facing': facing,
                              'quality': 'source_prior', 'over_counter': distance == 2,
                              'source': 'source walking collision cells and tileset-header counter tile IDs'})
    return positions


def plan_route(current_world, player, target_map_id, target=None, facts=None):
    """Return one portal suggestion; all actual physical inputs remain JEV's.

    ``target`` is optional coordinate/object selector or a list of selectors.
    If given, reaching another disconnected region of the same map is planned
    correctly. ``facts`` is reserved for verified ability-gated routing; this
    version does not infer CUT/SURF access from possession or source priors.
    """
    data = load_regions(); maps = data['maps']
    base = {'quality': 'source_prior', 'source_commit': data['source_commit'],
            'limitations': data['limitations'], 'next_transition': None, 'map_route': []}
    mid = player.get('map_id'); x, y = player.get('x'), player.get('y')
    if (any(type(v) is not int for v in (mid, x, y, target_map_id))
            or current_world.get('source_match') is False or current_world.get('player_position_valid') is False):
        return {**base, 'status': 'needs_data', 'reason': 'current_map_alignment_unverified'}
    here, destination = maps.get(str(mid)), maps.get(str(target_map_id))
    if not here or not destination:
        return {**base, 'status': 'needs_data', 'reason': 'outside_early_land_topology_coverage'}
    region = _region(here, x, y)
    if region is None:
        return {**base, 'status': 'needs_data', 'reason': 'player_not_on_source_walkable_region'}
    goals = set()
    targets = target if isinstance(target, list) else [target] if isinstance(target, dict) else []
    for candidate in targets:
        tx, ty = candidate.get('x'), candidate.get('y')
        if type(tx) is not int or type(ty) is not int:
            continue
        points = ([(position['x'], position['y']) for position in interaction_positions(target_map_id, candidate)]
                  if candidate.get('kind') == 'object' else [(tx, ty)])
        if 'y' in (candidate.get('trigger') or {}):
            points = [(cx, candidate['trigger']['y']) for cx in range(destination['width'])]
        for px, py in points:
            value = _region(destination, px, py)
            if value is not None:
                goals.add((target_map_id, value))
    if not goals and targets:
        return {**base, 'status': 'needs_data', 'reason': 'target_not_on_source_walkable_region'}
    if not targets:
        goals = {(target_map_id, r) for r in range(destination['region_count'])}
    start = (mid, region)
    if start in goals:
        return {**base, 'status': 'same_region', 'map_route': [mid]}
    adjacency = {}
    for edge in data['edges']:
        adjacency.setdefault(tuple(edge['from']), []).append(edge)
    adjacency[start] = sorted(adjacency.get(start, []), key=lambda e: abs(e['transition']['x'] - x) + abs(e['transition']['y'] - y))
    queue = deque([start]); parents = {start: None}; found = None
    while queue:
        at = queue.popleft()
        if at in goals:
            found = at; break
        for edge in adjacency.get(at, []):
            nxt = tuple(edge['to'])
            if nxt not in parents:
                parents[nxt] = (at, edge); queue.append(nxt)
    if found is None:
        return {**base, 'status': 'unreachable', 'reason': 'no_source_land_portal_route_from_this_region',
                'start_region': region}
    path = []; at = found
    while parents[at] is not None:
        at, edge = parents[at]; path.append(edge)
    path.reverse(); route = [mid]
    for edge in path:
        if edge['to'][0] != route[-1]:
            route.append(edge['to'][0])
    transition = deepcopy(path[0]['transition'])
    # Cross-check the chosen first portal against current RAM metadata. Static
    # priors cannot override an observed portal mismatch.
    if transition['kind'] == 'warp' and 'warps' in current_world:
        matching = [w for w in current_world['warps'] if w.get('x') == transition['x'] and w.get('y') == transition['y']
                    and w.get('destination_map_id') == transition['destination_map_id']]
        if not matching:
            return {**base, 'status': 'needs_data', 'reason': 'first_warp_disagrees_with_current_world'}
    if transition['kind'] == 'connection' and 'connections' in current_world:
        matching = [c for c in current_world['connections'] if c.get('direction') == transition['direction']
                    and c.get('destination_map_id') == transition['destination_map_id']]
        if not matching:
            return {**base, 'status': 'needs_data', 'reason': 'first_connection_disagrees_with_current_world'}
    return {**base, 'status': 'planned', 'next_transition': transition, 'map_route': route,
            'region_route': [list(start)] + [edge['to'] for edge in path]}


if __name__ == '__main__':
    import sys
    result = build(Path(sys.argv[1]))
    Path(__file__).with_name('redstar-route-regions.json').write_text(json.dumps(result, separators=(',', ':')) + '\n')
    print(json.dumps({'maps': len(result['maps']), 'portal_edges': len(result['edges']), 'skipped': result['skipped_maps']}))
