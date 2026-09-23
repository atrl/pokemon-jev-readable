"""Bounded, provenance-labelled experience. Records what happened, never what to do.

No import of source maps, walkthroughs, healing rules or battle scoring. Learned
transitions are directed and retained only after actual eligible movement.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from perception import POLICY, digest, point, project, take

DIRECTIONS = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}


class Experience:
    def __init__(self, data=None):
        data = data if isinstance(data, dict) and data.get('policy') == POLICY else {}
        self.maps = deepcopy(data.get('maps', {}))
        self.transitions = deepcopy(data.get('transitions', []))[-512:]
        self.dialogues = deepcopy(data.get('dialogues', []))[-160:]
        self.effects = deepcopy(data.get('effects', []))[-64:]
        self.notes = deepcopy(data.get('notes', []))[-32:]
        self.steps = int(data.get('steps', 0))
        self.last_observation = deepcopy(data.get('last_observation'))

    def observe(self, raw):
        game = project(raw)
        self.last_observation = self.brief(game)
        p = point(game)
        new_text = False
        if p:
            mid, x, y = p
            record = self.maps.setdefault(str(mid), {'cells': {}, 'objects': {}, 'portals': {}, 'visits': 0})
            record['last_seen_step'] = self.steps
            record['geometry'] = take(game['world'], ('width', 'height'))
            grid = game.get('local_map') or {}
            center = grid.get('player_cell') or {'x': 4, 'y': 4}
            for gy, row in enumerate(grid.get('rows') or []):
                for gx, glyph in enumerate(row):
                    tx, ty = x + gx - center['x'], y + gy - center['y']
                    if glyph not in '.#' or not (0 <= tx < game['world'].get('width', 0) and 0 <= ty < game['world'].get('height', 0)):
                        continue
                    key = f'{tx},{ty}'
                    old = record['cells'].get(key) or {}
                    record['cells'][key] = {'x': tx, 'y': ty, 'glyph': glyph,
                                           'visited': old.get('visited', 0), 'last_seen_step': self.steps,
                                           'evidence_ref': game['observation_id']}
            if (game.get('scene') or {}).get('mode') == 'overworld':
                current_key = f'{x},{y}'
                record['cells'].setdefault(current_key, {'x': x, 'y': y, 'glyph': '.', 'visited': 0, 'last_seen_step': self.steps, 'evidence_ref': game['observation_id']})
                for obj in game['world'].get('objects', []):
                    key = str(obj.get('object_id', f"{obj['x']},{obj['y']}"))
                    record['objects'][key] = {**deepcopy(obj), 'last_seen_step': self.steps,
                                               'evidence_ref': game['observation_id']}
                for portal in game['world'].get('warps', []):
                    key = str(portal['warp_id'])
                    record['portals'][key] = {**deepcopy(portal), 'last_seen_step': self.steps,
                                              'evidence_ref': game['observation_id']}
            while len(record['cells']) > 12000:
                del record['cells'][next(iter(record['cells']))]
        text = (game.get('dialog') or {}).get('text')
        if text and game['scene']['mode'] == 'dialog':
            ref = 'text:' + digest([p, text])
            found = next((r for r in self.dialogues if r['ref'] == ref), None)
            if found is None:
                self.dialogues.append({'ref': ref, 'text': text, 'position': p,
                                       'first_seen_step': self.steps, 'last_seen_step': self.steps,
                                       'source': 'observed_dialogue', 'evidence_ref': game['observation_id']})
                self.dialogues = self.dialogues[-160:]
                new_text = True
            else:
                found['last_seen_step'] = self.steps
        while len(self.maps) > 256:
            del self.maps[next(iter(self.maps))]
        return new_text

    def record(self, button, before, after):
        before, after = project(before), project(after)
        self.observe(before)
        self.steps += 1
        new_text = self.observe(after)
        p, q = point(before), point(after)
        ref = 'action:' + str(self.steps)
        from activity import observable_changes
        changed = observable_changes(before, after)
        self.effects.append({'ref': ref, 'step': self.steps, 'button': button,
                             'before': self.brief(before), 'after': self.brief(after),
                             'changed_fields': changed, 'source': 'executed_input_then_observed'})
        self.effects = self.effects[-64:]
        if q and after['scene']['mode'] == 'overworld':
            record = self.maps[str(q[0])]
            record['visits'] += 1
            key = f'{q[1]},{q[2]}'
            if key in record['cells']:
                record['cells'][key]['visited'] += 1
        eligible = (p and q and p[0] != q[0] and button in DIRECTIONS
                    and before['scene']['mode'] == after['scene']['mode'] == 'overworld'
                    and not before['world'].get('input_lock', {}).get('scripted_movement_remaining')
                    and not before['world'].get('input_lock', {}).get('ignored_buttons_mask'))
        if eligible:
            edge = {'from': p, 'to': q, 'button': button, 'evidence_ref': ref,
                    'step': self.steps, 'reversible': 'unknown', 'source': 'actual_directional_transition'}
            if not any((e['from'], e['to'], e['button']) == (p, q, button) for e in self.transitions):
                self.transitions.append(edge)
                self.transitions = self.transitions[-512:]
        return {'new_dialog_clue': new_text}

    @staticmethod
    def brief(game):
        return {'observation_id': game.get('observation_id'), 'position': point(game),
                'scene': game.get('scene'), 'dialog': take(game.get('dialog'), ('open', 'text', 'awaiting_input')),
                'battle': take(game.get('battle'), ('active', 'menu', 'selected_command', 'selected_move_slot')),
                'party': [{**take(m, ('slot', 'hp', 'max_hp', 'status_bits')), 'moves': [take(v, ('move_id', 'pp')) for v in m.get('moves', [])]} for m in game.get('party') or []],
                'bag': [take(v, ('item_id', 'quantity')) for v in game.get('bag', [])] if game.get('bag') is not None else None}

    def catalog(self, game):
        """All visible candidate kinds, plus encountered objects/maps. No ranking by strategy."""
        p = point(game)
        result = {}
        if not p:
            return result
        mid = p[0]
        record = self.maps.get(str(mid), {})
        visible_objects = {str(o.get('object_id')) for o in game['world'].get('objects', [])}
        for key, obj in record.get('objects', {}).items():
            ref = f'object:{mid}:{key}'
            result[ref] = {'map_id': mid, 'kind': 'object', 'label': obj.get('sprite'),
                           'selector': take(obj, ('object_id', 'x', 'y', 'sprite')),
                           'evidence_ref': obj['evidence_ref'], 'last_seen_step': obj['last_seen_step'],
                           'currently_visible': key in visible_objects, 'quality': 'observed_then_remembered'}
        for key, obj in record.get('portals', {}).items():
            # Approach a doorway without claiming that its destination is already known.
            ref = f'portal:{mid}:{key}'
            result[ref] = {'map_id': mid, 'kind': 'coordinate', 'label': 'previously_observed_portal',
                           'selector': take(obj, ('x', 'y')), 'evidence_ref': obj['evidence_ref'],
                           'last_seen_step': obj['last_seen_step'], 'quality': 'observed_then_remembered'}
        # Visible floor candidates: deterministic coordinate order is enumeration, not a recommendation.
        grid = game.get('local_map') or {}
        center = grid.get('player_cell') or {'x': 4, 'y': 4}
        for gy, row in enumerate(grid.get('rows') or []):
            for gx, glyph in enumerate(row):
                x, y = p[1] + gx - center['x'], p[2] + gy - center['y']
                if glyph == '.' and [x, y] != p[1:] and (record.get('cells') or {}).get(f'{x},{y}'):
                    result[f'cell:{mid}:{x},{y}'] = {'map_id': mid, 'kind': 'coordinate', 'label': 'observed_floor',
                        'selector': {'x': x, 'y': y}, 'quality': 'observed_background_only', 'evidence_ref': game['observation_id']}
        for key, record in sorted(self.maps.items()):
            if int(key) != mid:
                result[f'map:{key}'] = {'map_id': int(key), 'kind': 'map', 'label': 'previously_visited_map',
                    'selector': None, 'quality': 'observed_then_remembered',
                    'last_seen_step': record['last_seen_step'], 'evidence_ref': f'memory:map:{key}'}
        return result

    def spatial(self, game):
        p = point(game)
        if not p or str(p[0]) not in self.maps:
            return None
        record = self.maps[str(p[0])]
        cells = list(record['cells'].values())
        if not cells:
            return {'map_id': p[0], 'rows': [], 'unknown': True}
        # Bounded window. Ellipsis is never substituted for known geometry.
        x0, x1 = max(0, p[1]-32, min(c['x'] for c in cells)), min(p[1]+32, max(c['x'] for c in cells))
        y0, y1 = max(0, p[2]-32, min(c['y'] for c in cells)), min(p[2]+32, max(c['y'] for c in cells))
        rows = [''.join('@' if [x, y] == p[1:] else record['cells'].get(f'{x},{y}', {}).get('glyph', '?')
                        for x in range(x0, x1+1)) for y in range(y0, y1+1)]
        return {'map_id': p[0], 'origin': [x0, y0], 'rows': rows,
                'legend': {'?': 'not observed/retained', '.': 'observed background floor', '#': 'observed background obstruction', '@': 'current player'},
                'window_limit': 65, 'source': 'accumulated_viewports',
                'limitations': 'Floor is not a guarantee of walking; objects and scripts can block it.'}

    def path_to(self, game, target):
        """Optional geometry for a MODEL-CHOSEN target, never a target chooser or controller."""
        p = point(game)
        if not p or not target:
            return {'status': 'no_model_target'}
        if p[0] != target.get('map_id'):
            return {'status': 'cross_map_not_computed', 'known_edges': deepcopy(self.transitions[-32:])}
        obj = target.get('selector') or {}
        if not all(type(obj.get(k)) is int for k in ('x', 'y')):
            return {'status': 'no_coordinate_target'}
        end = (obj['x'], obj['y'])
        goals = {end}
        if target.get('kind') == 'object':
            goals = {(end[0]+dx, end[1]+dy) for dx, dy in DIRECTIONS.values()}
        cells = self.maps.get(str(p[0]), {}).get('cells', {})
        blocked = {(o['x'], o['y']) for o in game['world'].get('objects', [])}
        start = tuple(p[1:])
        queue, parents = deque([start]), {start: None}
        while queue:
            at = queue.popleft()
            if at in goals:
                path = []
                while at is not None:
                    path.append(list(at)); at = parents[at]
                return {'status': 'observed_path', 'coordinates': list(reversed(path)),
                        'source': 'BFS_on_observed_background_for_model_selected_target',
                        'limitation': 'No execution or recommended_button; recheck obstacles after input.'}
            for dx, dy in DIRECTIONS.values():
                nxt = (at[0]+dx, at[1]+dy)
                if nxt not in parents and nxt not in blocked and cells.get(f'{nxt[0]},{nxt[1]}', {}).get('glyph') == '.':
                    parents[nxt] = at; queue.append(nxt)
        return {'status': 'unknown_path', 'meaning': 'insufficient retained geometry, not proof of unreachability'}

    def context(self, game):
        return {'policy': POLICY, 'current_map': self.spatial(game),
                'maps': [{'map_id': int(k), 'observed_cells': len(v['cells']), 'last_seen_step': v['last_seen_step']}
                         for k, v in self.maps.items()],
                'objects': deepcopy(self.maps.get(str((point(game) or [None])[0]), {}).get('objects', {})),
                'transitions': deepcopy(self.transitions[-64:]), 'dialogues': deepcopy(self.dialogues[-40:]),
                'recent_actions': deepcopy(self.effects[-12:]), 'notes': deepcopy(self.notes),
                'retention': {'action_tail': 64, 'dialogues': 160, 'model_notes': 32},
                'limitations': 'Remembered is not currently visible; model notes are hypotheses, never facts.'}

    def add_notes(self, notes, plan_id):
        evidence = {r['ref']: take(r, ('text', 'position', 'step', 'changed_fields')) for r in [*self.dialogues, *self.effects]}
        if self.last_observation:
            evidence[self.last_observation['observation_id']] = take(self.last_observation, ('position', 'scene', 'dialog'))
        for note in notes:
            self.notes.append({**deepcopy(note), 'plan_id': plan_id, 'step': self.steps,
                               'evidence_snapshots': {ref: deepcopy(evidence[ref]) for ref in note['evidence_refs'] if ref in evidence},
                               'source': 'system2_hypothesis_not_verified_fact'})
        self.notes = self.notes[-32:]

    def snapshot(self):
        return deepcopy({'policy': POLICY, 'steps': self.steps, 'maps': self.maps,
                         'transitions': self.transitions, 'dialogues': self.dialogues,
                         'effects': self.effects, 'notes': self.notes, 'last_observation': self.last_observation})

    def import_legacy_observations(self, source):
        """Allowlisted migration of hash-bound, explicitly observed legacy records only.

        Never import story facts, plans, support objectives, static routes or model text.
        Original checkpoint files are not mutated; migrated provenance is disclosed.
        """
        if source.get('version') != 1:
            return
        steps = source.get('steps', 0)
        self.steps = steps if type(steps) is int and steps >= 0 else 0
        for mid, cells in list((source.get('tiles') or {}).items())[:256]:
            if not str(mid).isdigit() or not 0 <= int(mid) <= 255 or not isinstance(cells, dict):
                continue
            accepted = {}
            for key, cell in list(cells.items())[:12000]:
                if not isinstance(cell, dict) or cell.get('source') not in ('observed_background', 'observed_player_position'):
                    continue
                try:
                    x, y = map(int, key.split(','))
                except (ValueError, AttributeError):
                    continue
                if not (0 <= x <= 255 and 0 <= y <= 255) or type(cell.get('passable')) is not bool:
                    continue
                accepted[key] = {'x': x, 'y': y, 'glyph': '.' if cell['passable'] else '#',
                                 'visited': 0, 'last_seen_step': self.steps,
                                 'evidence_ref': f'legacy-observed-cell:{mid}:{key}'}
            if accepted:
                self.maps[str(mid)] = {'cells': accepted, 'objects': {}, 'portals': {}, 'visits': 0,
                                      'last_seen_step': self.steps,
                                      'provenance': 'hash_bound_legacy_observed_cells_not_static_map'}
        for row in (source.get('clues') or [])[-80:]:
            if not isinstance(row, dict) or row.get('source') != 'observed_dialog' or not isinstance(row.get('text'), str):
                continue
            p = [row.get('map_id'), *(row.get('position') or [])]
            if len(p) != 3 or not all(type(n) is int and 0 <= n <= 255 for n in p):
                continue
            self.dialogues.append({'ref': 'text:' + digest([p, row['text']]), 'text': row['text'][:2000],
                                   'position': p, 'first_seen_step': None, 'last_seen_step': self.steps,
                                   'source': 'hash_bound_legacy_observed_dialogue',
                                   'evidence_ref': 'legacy-dialog:' + str(row.get('id', digest(row['text'])))})
        for row in (source.get('transitions') or [])[-256:]:
            if not isinstance(row, dict) or row.get('source') != 'observed_map_transition' or row.get('kind') != 'walk':
                continue
            a = [row.get('from_map'), *(row.get('from_position') or [])]
            b = [row.get('to_map'), *(row.get('arrival') or [])]
            if len(a) != 3 or len(b) != 3 or not all(type(n) is int and 0 <= n <= 255 for n in a+b) or row.get('button') not in DIRECTIONS:
                continue
            self.transitions.append({'from': a, 'to': b, 'button': row['button'], 'step': row.get('step'),
                                     'evidence_ref': 'legacy-transition:' + digest(row), 'reversible': 'unknown',
                                     'source': 'legacy_recorded_transition_cause_not_reverified'})
