"""Execution diagnostics, not a game policy or an unreachable-target oracle.

A counter is a symptom. Only this plan's recorded effects can establish a
no-effect window or a small repeated movement cycle. Failure history qualifies
an attempt in one observation context; it does not ban a place forever.
"""
from copy import deepcopy


def repetition_evidence(plan, effects, step):
    """Return a bounded plan-local symptom, or None when evidence is insufficient."""
    created, limit = plan['created_step'], plan['max_no_effect_steps']
    if step - created < limit:
        return None
    # Experience retains at most 64 effects, even for an 80-action plan budget.
    size = min(limit, 64)
    tail = [e for e in effects if type(e.get('step')) is int and created < e['step'] <= step][-size:]
    if len(tail) != size or [e['step'] for e in tail] != list(range(step-size+1, step+1)):
        return None

    def unchanged(effect):
        a, b = effect.get('before') or {}, effect.get('after') or {}
        aid, bid = a.get('observation_id'), b.get('observation_id')
        if isinstance(aid, str) and isinstance(bid, str):
            return aid == bid
        # Missing comparison data is unknown, not proof that nothing changed.
        return effect.get('changed_fields') == []

    symptom = 'unchanged_observation' if all(unchanged(e) for e in tail) else None
    if symptom is None:
        states = []
        for effect in tail:
            states.extend((effect.get('before') or {}, effect.get('after') or {}))
        positions = [s.get('position') for s in states]
        valid = all(isinstance(p, list) and len(p) == 3 and all(type(n) is int for n in p)
                    for p in positions)
        # Movement is not repetition when text, battle, team or inventory changed.
        fields = ('scene', 'dialog', 'battle', 'party', 'bag')
        same_context = all(all(k in s for k in fields) for s in states) and all(
            {k: s[k] for k in fields} == {k: states[0][k] for k in fields} for s in states)
        walking = all((s.get('scene') or {}).get('mode') == 'overworld'
                      and (s.get('scene') or {}).get('verified') is True for s in states)
        # Revisited coordinates must also show the same actionable observation.
        # New objects, text, resources or geometry must not be dismissed as a loop.
        identities = {}
        if valid:
            for state, pos in zip(states, positions):
                identities.setdefault(tuple(pos), set()).add(state.get('observation_id'))
        repeated_context = bool(identities) and all(
            len(ids) == 1 and all(isinstance(i, str) and i for i in ids) for ids in identities.values())
        if valid and same_context and walking and repeated_context and len({p[0] for p in positions}) == 1:
            moves = sum(e['before']['position'] != e['after']['position'] for e in tail)
            if moves >= max(6, size // 2) and len({tuple(p) for p in positions}) <= 4:
                symptom = 'small_position_cycle'
    if symptom is None:
        return None
    return {'symptom': symptom, 'scope': 'plan_local_execution_not_proof_target_unreachable',
            'plan_created_step': created, 'window_start': tail[0]['step'],
            'window_end': tail[-1]['step'], 'observed_actions': size,
            'recent_actions': deepcopy(tail[-8:])}


def target_failure_context(history, game):
    """Suppress identical retries only while the actionable observation is unchanged.

    Old records lacking an observation identity remain visible as history but
    cannot impose a permanent prohibition. Changed evidence allows reassessment,
    not an assertion that the previously attempted target is now reachable.
    """
    current = game.get('observation_id')
    withheld, attempts = set(), []
    for record in history:
        ref = record.get('target_ref')
        if record.get('status') != 'failed' or not isinstance(ref, str) or not ref:
            continue
        at_failure = record.get('failure_observation_id')
        same = isinstance(current, str) and bool(current) and at_failure == current
        if same:
            withheld.add(ref)
        attempts.append({'target_ref': ref, 'plan_id': record.get('plan_id'),
                         'reason': record.get('reason'), 'step': record.get('step'),
                         'failure_observation_id': at_failure, 'same_observation': same,
                         'scope': 'failed_attempt_not_permanent_unreachability'})
    return sorted(withheld), attempts[-32:]
