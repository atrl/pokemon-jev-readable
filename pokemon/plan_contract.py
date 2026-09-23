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
import re

WILD_POLICIES = ('fight', 'run', 'catch')


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
    progress = observation.get('progress') or {}
    return {
        'position': stable_position(observation),
        'party_count': verified_value((observation.get('milestones') or {}).get('party_count')),
        'balls': ball_count(observation.get('bag')),
        'visited_tiles': progress.get('visited_tiles'),
        # Loop counters at plan creation; repetition is judged relative to these
        # so a loop inherited from older history cannot end a fresh plan.
        'same_position_steps': progress.get('same_position_steps'),
        'steps_since_new_tile': progress.get('steps_since_new_tile'),
        'dialog_open': (observation.get('dialog') or {}).get('open'),
        'battle_active': (observation.get('battle') or {}).get('active')
        if (observation.get('battle') or {}).get('verified') else None,
    }


def counter_growth(base, progress, key, limit):
    """True when a monotonic progress counter grew by at least ``limit`` since plan start."""
    before, now = (base or {}).get(key), (progress or {}).get(key)
    return type(before) is int and type(now) is int and now - before >= limit


def _text(value, name, length):
    if not isinstance(value, str) or not value.strip() or len(value) > length:
        raise ValueError(f'Invalid plan {name}')
    return value.strip()


REF_PATTERN = re.compile(r'^(obs|action|legacy|memory|object|portal|cell|map):')


def _collect_refs(value, out):
    """Collect every reference token actually present in the supplied situation."""
    if isinstance(value, dict):
        for child in value.values():
            _collect_refs(child, out)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_refs(child, out)
    elif isinstance(value, str) and REF_PATTERN.match(value):
        out.add(value)


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


def normalize_plan(data, situation):
    """Ground a model plan against the exact observed situation; no gameplay defaults."""
    return normalize_observed_plan(data, situation)


def normalize_observed_plan(data, situation):
    """Contract checks, not gameplay defaults. Intent and preferences belong to the model."""
    from model_context import OBSERVED_SUCCESS_TYPES
    from perception import POLICY
    if not isinstance(data, dict):
        raise ValueError('Plan must be a JSON object')
    allowed = {'subgoal', 'intent', 'reasoning', 'target_ref', 'success', 'policy',
               'resource_policy', 'memory_updates', 'replan_when', 'expires_steps', 'max_no_effect_steps'}
    if set(data) - allowed:
        raise ValueError('Unknown plan fields; no coordinates or executable code')
    catalog = situation.get('targets') or {}
    ref = data.get('target_ref')
    if ref is not None and (not isinstance(ref, str) or ref not in catalog):
        raise ValueError('Target was not present in the supplied observation memory')
    target = deepcopy(catalog.get(ref))
    success = data.get('success')
    if not isinstance(success, dict) or success.get('type') not in OBSERVED_SUCCESS_TYPES:
        raise ValueError('A supported observable success predicate is required')
    kind = success['type']
    if set(success) - ({'type', 'fact'} if kind == 'fact_true' else {'type'}):
        raise ValueError('Unexpected success arguments')
    facts = situation.get('milestones') or {}
    if kind == 'fact_true':
        name = success.get('fact')
        if not isinstance(name, str) or name not in facts or verified_value(facts[name]) is True:
            raise ValueError('Unknown or already satisfied fact')
    base = deepcopy(situation.get('baseline') or {})
    if kind == 'target_reached':
        if target is None:
            raise ValueError('A target is required')
        at = base.get('position')
        sel = target.get('selector') or {}
        if at and at[0] == target['map_id']:
            reached = target['kind'] == 'map' or (target['kind'] == 'coordinate' and at[1:] == [sel.get('x'), sel.get('y')])
            if target['kind'] == 'object' and all(type(sel.get(k)) is int for k in ('x', 'y')):
                reached = abs(at[1]-sel['x']) + abs(at[2]-sel['y']) <= 1
            if reached:
                raise ValueError('Target approach already satisfied; choose an interaction predicate')
    for name, low, high, default in [('expires_steps', 10, 1000, 160), ('max_no_effect_steps', 8, 80, 24)]:
        value = data.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid ' + name)
    increments = {'party_grew': 'party_count', 'balls_increased': 'balls', 'new_tile': 'visited_tiles'}
    if kind in increments and type(base.get(increments[kind])) is not int:
        raise ValueError('A known baseline is required')
    if kind == 'map_changed' and not base.get('position'):
        raise ValueError('Map transition needs a known starting position')
    policy = data.get('resource_policy', {})
    if not isinstance(policy, dict) or set(policy) - {'wild_battle', 'catch_species', 'heal_hp_ratio', 'max_party_size'}:
        raise ValueError('Invalid optional resource policy')
    if 'wild_battle' in policy and policy['wild_battle'] not in WILD_POLICIES:
        raise ValueError('Invalid wild battle preference')
    if 'heal_hp_ratio' in policy and (type(policy['heal_hp_ratio']) not in (int, float) or not math.isfinite(policy['heal_hp_ratio']) or not 0 <= policy['heal_hp_ratio'] <= 1):
        raise ValueError('Invalid optional HP preference')
    if 'max_party_size' in policy and (type(policy['max_party_size']) is not int or not 1 <= policy['max_party_size'] <= 6):
        raise ValueError('Invalid party size preference')
    if policy.get('catch_species') is not None:
        species = _text(policy['catch_species'], 'catch_species', 40)
        mons = list((situation.get('game') or {}).get('party') or [])
        mons.append(((situation.get('game') or {}).get('battle') or {}).get('enemy') or {})
        known = {m.get('species_name_prior') for m in mons}
        if species not in known:
            raise ValueError('Capture preference must name an observed species')
    guidance = data.get('policy', '')
    if not isinstance(guidance, str) or len(guidance) > 1600:
        raise ValueError('Invalid policy text')
    rules = data.get('replan_when', [])
    if not isinstance(rules, list) or len(rules) > 6:
        raise ValueError('Invalid interrupt list')
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('type') not in ('scene_changed', 'map_changed', 'party_hp_below'):
            raise ValueError('Unknown interrupt predicate')
        if rule['type'] == 'party_hp_below':
            ratio = rule.get('ratio')
            if set(rule) != {'type', 'ratio'} or type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError('Invalid HP interrupt')
        elif set(rule) != {'type'}:
            raise ValueError('Unexpected interrupt fields')
    memory = situation.get('memory') or {}
    refs = {situation.get('observation_id')}
    # A target reference is itself an observed reference; the prompt allows notes
    # to cite observation, targets or memory. Collect every reference token that
    # was actually present in the situation, including nested action before/after
    # observation ids.
    refs.update(catalog)
    for section in (memory, situation.get('game'), situation.get('active_plan'), catalog):
        _collect_refs(section, refs)
    refs.discard(None)
    names = data.get('memory_updates', [])
    if not isinstance(names, list) or len(names) > 4:
        raise ValueError('At most four memory notes per plan')
    notes = []
    for note in names:
        if not isinstance(note, dict) or set(note) != {'text', 'evidence_refs'}:
            raise ValueError('Notes need text and evidence_refs')
        _text(note['text'], 'memory note', 600)
        citations = note['evidence_refs']
        if not isinstance(citations, list) or not 1 <= len(citations) <= 6:
            raise ValueError('Notes need 1..6 evidence_refs')
        # Ground each note; drop field names or hallucinated refs, and skip a note
        # that has none left rather than failing the whole plan.
        valid = [r for r in citations if isinstance(r, str) and r in refs]
        if valid:
            notes.append({**note, 'evidence_refs': valid})
    result = {'schema_version': 3, 'observation_policy': POLICY,
              'subgoal': _text(data.get('subgoal'), 'subgoal', 80),
              'intent': _text(data.get('intent'), 'intent', 800),
              'reasoning': _text(data.get('reasoning') or 'Model-authored plan', 'reasoning', 1600),
              'target_ref': ref, 'target': target, 'target_map_id': target['map_id'] if target else None,
              'success': deepcopy(success), 'baseline': base, 'policy': guidance,
              'resource_policy': deepcopy(policy), 'memory_updates': deepcopy(notes),
              'replan_when': deepcopy(rules), 'expires_steps': data.get('expires_steps', 160),
              'max_no_effect_steps': data.get('max_no_effect_steps', 24),
              'status': 'active', 'situation_id': situation.get('situation_id')}
    result['plan_id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
    return result
