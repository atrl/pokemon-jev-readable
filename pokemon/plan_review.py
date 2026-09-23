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


def target_failure_context(history, game, *, repeated_threshold=3):
    """Qualify attempts so the planner stops re-issuing an unproductive target.

    A plan that ends without completing (failed/expired/invalidated) is evidence
    about one attempt, not proof a place is permanently unreachable. Exact
    same-observation failures are withheld immediately; a target that keeps
    ending without completion across retained history is also withheld until it
    completes or the history rolls over. This is a working-memory limit, not a
    world fact.
    """
    current = game.get('observation_id')
    withheld, attempts, unsuccessful = set(), [], {}
    for record in history:
        ref = record.get('target_ref')
        if not isinstance(ref, str) or not ref:
            continue
        status = record.get('status')
        observed = record.get('failure_observation_id') or record.get('observation_id')
        same = isinstance(current, str) and bool(current) and observed == current
        if status in ('failed', 'expired', 'invalidated'):
            unsuccessful[ref] = unsuccessful.get(ref, 0) + 1
        if status == 'failed' and same:
            withheld.add(ref)
        attempts.append({'target_ref': ref, 'plan_id': record.get('plan_id'),
                         'reason': record.get('reason'), 'step': record.get('step'),
                         'status': status, 'observation_id': observed, 'same_observation': same,
                         'scope': 'attempt_not_permanent_unreachability'})
    for ref, count in unsuccessful.items():
        if count >= repeated_threshold:
            withheld.add(ref)
    return sorted(withheld), attempts[-32:]
