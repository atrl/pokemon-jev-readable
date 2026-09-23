"""Ground model plans in observed targets and check their lifecycle without an LLM.

The model writes intent, never coordinates, Python or button sequences. Target
references are resolved against the exact situation sent to it. A successful
local plan is not evidence of story completion.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

WILD_POLICIES = ('fight', 'run', 'catch')
SUCCESS_TYPES = ('target_reached', 'fact_true', 'dialog_closed', 'party_grew',
                 'balls_increased', 'new_tile', 'battle_finished')


def verified_value(record):
    return record.get('value') if isinstance(record, dict) and record.get('verified') is True else None


def stable_position(observation):
    world, scene = observation.get('world') or {}, observation.get('scene') or {}
    p = observation.get('player') or {}
    if not (world.get('source_match') is True and world.get('player_position_valid') is True
            and scene.get('verified') is True and scene.get('mode') == 'overworld'):
        return None
    point = [p.get(k) for k in ('map_id', 'x', 'y')]
    return point if all(type(v) is int and v >= 0 for v in point) else None


def ball_count(bag):
    """Unknown inventory is unknown, not zero. Only verified Gen-1 ball IDs."""
    if not isinstance(bag, list) or any(not isinstance(row, dict) or row.get('name_verified') is not True for row in bag):
        return None
    return sum(row['quantity'] for row in bag if row.get('item_id') in (1, 2, 3, 4)
               and type(row.get('quantity')) is int and 0 < row['quantity'] <= 99)


def baseline(observation):
    return {
        'position': stable_position(observation),
        'party_count': verified_value((observation.get('milestones') or {}).get('party_count')),
        'balls': ball_count(observation.get('bag')),
        'visited_tiles': (observation.get('progress') or {}).get('visited_tiles'),
        'dialog_open': (observation.get('dialog') or {}).get('open'),
        'battle_active': (observation.get('battle') or {}).get('active')
        if (observation.get('battle') or {}).get('verified') else None,
    }


def target_catalog(game, campaign):
    """Finite references to observed objects/exits and explicitly labelled map priors."""
    from world_data import map_prior
    world = game.get('world') or {}
    mid = world.get('map_id')
    targets = {}
    known = set(campaign.get('visited_map_ids') or []) | set(campaign.get('route_map_ids') or [])
    if type(mid) is int:
        known.add(mid)
    story = campaign.get('story_objective') or campaign.get('active_objective') or {}
    if type(story.get('target_map_id')) is int:
        known.add(story['target_map_id'])
    aligned = world.get('source_match') is True and world.get('player_position_valid') is True
    if aligned:
        for obj in world.get('objects', []):
            if obj.get('active') is False or any(type(obj.get(k)) is not int for k in ('x', 'y')):
                continue
            ref = f"object:{mid}:{obj.get('object_id', str(obj['x']) + ',' + str(obj['y']))}"
            targets[ref] = {
                'map_id': mid, 'kind': 'object', 'label': obj.get('sprite'),
                'quality': obj.get('quality', 'source_prior'),
                'selector': {**{k: obj[k] for k in ('object_id', 'sprite', 'text_id', 'x', 'y') if k in obj},
                             'kind': 'object'},
            }
        for kind, entries in (('warp', world.get('warps', [])), ('connection', world.get('connections', []))):
            for index, entry in enumerate(entries):
                dest = entry.get('destination_map_id')
                if type(dest) is not int or not map_prior(dest):
                    continue
                known.add(dest)
                selector = {k: entry[k] for k in ('x', 'y', 'direction', 'destination_map_id', 'destination_warp_id') if k in entry}
                selector['kind'] = kind
                ref = f"{kind}:{mid}:{entry.get('warp_id', entry.get('direction', index))}"
                targets[ref] = {
                    'map_id': mid, 'kind': kind, 'label': entry.get('destination_name'),
                    'quality': entry.get('quality', 'source_prior'), 'selector': selector,
                    'destination_map_id': dest,
                }
        # Local exploration candidates come from observed cells, never invented coordinates.
        local = game.get('local_map') or {}
        center = local.get('player_cell') or {'x': 4, 'y': 4}
        p = game.get('player') or {}
        if type(p.get('x')) is int and type(p.get('y')) is int:
            choices = []
            for gy, row in enumerate(local.get('rows') or []):
                for gx, tile in enumerate(row):
                    x, y = p['x'] + gx - center['x'], p['y'] + gy - center['y']
                    if tile != '.' or not (0 <= x < (world.get('width') or 0) and 0 <= y < (world.get('height') or 0)):
                        continue
                    visits = (campaign.get('cell_visits') or {}).get(f'{mid}:{x},{y}', 0)
                    choices.append((visits, -(abs(x-p['x']) + abs(y-p['y'])), x, y))
            for _, _, x, y in sorted(choices)[:6]:
                ref = f'cell:{mid}:{x},{y}'
                targets[ref] = {'map_id': mid, 'kind': 'coordinate', 'label': 'observed local floor',
                                'quality': 'advisory_background_only',
                                'selector': {'kind': 'coordinate', 'x': x, 'y': y}}
    for ident in sorted(k for k in known if type(k) is int):
        record = map_prior(ident)
        if record:
            targets[f'map:{ident}'] = {'kind': 'map', 'map_id': ident, 'label': record['name'],
                                      'quality': 'source_prior', 'selector': None}
    return dict(list(targets.items())[:96])


def _text(value, name, length):
    if not isinstance(value, str) or not value.strip() or len(value) > length:
        raise ValueError(f'Invalid plan {name}')
    return value.strip()


def normalize_plan(data, situation):
    if not isinstance(data, dict):
        raise ValueError('Plan must be a JSON object')
    allowed = {'subgoal', 'intent', 'reasoning', 'target_ref', 'success', 'resource_policy',
               'expires_steps', 'max_no_effect_steps'}
    if set(data) - allowed:
        raise ValueError('Unknown plan fields; return target_ref and success, not coordinates')
    ref = data.get('target_ref')
    catalog = situation.get('targets') or {}
    if ref is not None and (not isinstance(ref, str) or ref not in catalog):
        raise ValueError('Unknown target_ref')
    target = deepcopy(catalog.get(ref))
    success = data.get('success')
    if not isinstance(success, dict) or success.get('type') not in SUCCESS_TYPES:
        raise ValueError('A registered success predicate is required')
    kind = success['type']
    if set(success) - ({'type', 'fact'} if kind == 'fact_true' else {'type'}):
        raise ValueError('Invalid success predicate arguments')
    if kind == 'target_reached' and target is None:
        raise ValueError('target_reached needs a grounded target')
    if kind == 'fact_true' and (not isinstance(success.get('fact'), str) or success.get('fact') not in (situation.get('milestones') or {})):
        raise ValueError('Unknown fact identifier')
    # Do not repeatedly accept a goal already satisfied at plan creation.
    if kind == 'fact_true' and verified_value(situation['milestones'][success['fact']]) is True:
        raise ValueError('Success condition is already satisfied')
    base = deepcopy(situation.get('baseline') or {})
    if kind == 'target_reached' and target['kind'] == 'map' and base.get('position') and base['position'][0] == target['map_id']:
        raise ValueError('Already in target map; choose an exit/object/cell')
    if kind in ('party_grew', 'balls_increased', 'new_tile'):
        key = {'party_grew': 'party_count', 'balls_increased': 'balls', 'new_tile': 'visited_tiles'}[kind]
        if type(base.get(key)) is not int:
            raise ValueError('Success requires a verified baseline')
    ttl = data.get('expires_steps', 160)
    no_effect = data.get('max_no_effect_steps', 24)
    if type(ttl) is not int or not 10 <= ttl <= 1000:
        raise ValueError('expires_steps must be 10..1000')
    if type(no_effect) is not int or not 8 <= no_effect <= 80:
        raise ValueError('max_no_effect_steps must be 8..80')
    policy = data.get('resource_policy') or {}
    if not isinstance(policy, dict) or set(policy) - {'wild_battle', 'catch_species', 'heal_hp_ratio', 'max_party_size'}:
        raise ValueError('Invalid resource policy')
    wild = policy.get('wild_battle', 'run')
    if wild not in WILD_POLICIES:
        raise ValueError('Unknown wild_battle policy')
    ratio = policy.get('heal_hp_ratio', 0.5)
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0.15 <= ratio <= 0.9:
        raise ValueError('heal_hp_ratio must be finite, 0.15..0.9')
    species = policy.get('catch_species')
    if species is not None:
        species = _text(species, 'catch_species', 32)
        known_species = situation.get('species_options') or []
        if species.upper().strip('@') not in known_species:
            raise ValueError('catch_species must reference observed species')
    size = policy.get('max_party_size', 2)
    if type(size) is not int or not 1 <= size <= 6:
        raise ValueError('max_party_size must be 1..6')
    result = {
        'schema_version': 2, 'subgoal': _text(data.get('subgoal'), 'subgoal', 80),
        'intent': _text(data.get('intent'), 'intent', 600),
        'reasoning': _text(data.get('reasoning') or 'Planner selected this local objective.', 'reasoning', 800),
        'target_ref': ref, 'target': target,
        'target_map_id': target['map_id'] if target else None,
        'selectors': [deepcopy(target['selector'])] if target and target.get('selector') else [],
        'success': deepcopy(success), 'baseline': base,
        'resource_policy': {'wild_battle': wild, 'catch_species': species,
                            'heal_hp_ratio': ratio, 'max_party_size': size},
        'expires_steps': ttl, 'max_no_effect_steps': no_effect,
        'status': 'active', 'situation_id': situation.get('situation_id'),
    }
    result['plan_id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
    return result


def success_evidence(plan, observation, progress):
    """Return factual evidence or None; no model-generated assertion counts."""
    success = plan.get('success') or {}
    base = plan.get('baseline') or {}
    kind = success.get('type')
    facts = observation.get('milestones') or {}
    if kind == 'fact_true':
        record = facts.get(success.get('fact'))
        if verified_value(record) is True:
            return {'fact': success['fact'], 'record': deepcopy(record)}
    elif kind == 'target_reached':
        pos = stable_position(observation)
        target = plan.get('target') or {}
        selector = target.get('selector') or {}
        if pos:
            mid, x, y = pos
            if target.get('kind') == 'map' and mid == target.get('map_id'):
                return {'position': pos}
            if target.get('kind') in ('warp', 'connection') and mid == target.get('destination_map_id') and mid != target.get('map_id'):
                return {'position': pos, 'origin_map': target['map_id']}
            if mid == target.get('map_id') and type(selector.get('x')) is int and type(selector.get('y')) is int:
                distance = abs(x-selector['x']) + abs(y-selector['y'])
                if (target.get('kind') == 'coordinate' and distance == 0) or (target.get('kind') == 'object' and distance == 1):
                    return {'position': pos, 'meaning': 'approach reached, not interaction/story completion'}
    elif kind == 'dialog_closed':
        dialog = observation.get('dialog') or {}
        if dialog.get('open') is True:
            plan['saw_dialog'] = True
        if (base.get('dialog_open') is True or plan.get('saw_dialog')) and dialog.get('open') is False and stable_position(observation):
            return {'dialog_closed': True, 'meaning': 'dialog episode ended, not story completion'}
    elif kind == 'battle_finished':
        battle = observation.get('battle') or {}
        if battle.get('active') is True:
            plan['saw_battle'] = True
        if (base.get('battle_active') is True or plan.get('saw_battle')) and battle.get('verified') is True and battle.get('active') is False and stable_position(observation):
            return {'battle_ended': True, 'victory': 'not_implied'}
    else:
        key, now = {
            'party_grew': ('party_count', verified_value(facts.get('party_count'))),
            'balls_increased': ('balls', ball_count(observation.get('bag'))),
            'new_tile': ('visited_tiles', progress.get('visited_tiles')),
        }.get(kind, (None, None))
        if key and type(now) is int and type(base.get(key)) is int and now > base[key]:
            return {'field': key, 'before': base[key], 'after': now, 'scope': 'local_plan_only'}
    return None


def plan_outcome(plan, observation, progress, step, navigation=None):
    """Completion, invalidation and expiry are distinct; failures can preempt TTL."""
    if plan.get('schema_version') != 2:
        return {'status': 'invalidated', 'reason': 'legacy_plan_requires_revalidation'}
    proof = success_evidence(plan, observation, progress)
    if proof is not None:
        return {'status': 'completed', 'reason': 'predicate_verified', 'evidence': proof}
    if plan.get('status') == 'suspended':
        return None
    if step - plan.get('created_step', step) >= plan['expires_steps']:
        return {'status': 'expired', 'reason': 'action_budget_reached'}
    # Initial loop counters are relative to this plan, not inherited failure history.
    age = step - plan.get('created_step', step)
    scene = (observation.get('scene') or {}).get('mode')
    if age >= plan['max_no_effect_steps'] and scene == 'overworld':
        if progress.get('loop_detected') or (progress.get('same_position_steps') or 0) >= plan['max_no_effect_steps']:
            return {'status': 'failed', 'reason': 'repeated_no_effect_or_loop'}
    target = plan.get('target') or {}
    if stable_position(observation) and target.get('kind') == 'object' and observation['player']['map_id'] == target.get('map_id'):
        selector = target.get('selector') or {}
        matches = [o for o in (observation.get('world') or {}).get('objects', [])
                   if o.get('object_id') == selector.get('object_id') and selector.get('object_id') is not None]
        if matches and all(o.get('active') is False for o in matches):
            return {'status': 'invalidated', 'reason': 'observed_target_disappeared'}
    nav = navigation or {}
    if nav.get('status') in ('needs_map_connection', 'no_observed_path') and age >= 8 and stable_position(observation):
        return {'status': 'failed', 'reason': 'route_not_executable', 'navigation': deepcopy(nav)}
    return None
