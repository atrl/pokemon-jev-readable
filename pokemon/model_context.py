"""Neutral inputs for both models. Plans come from System Two; facts from experience.

No game-specific menu or target rules live here. The models read the structured
observation (scene, screen text, battle menu, memory) and decide; the runtime
only executes, computes geometry and reports facts.
"""
from __future__ import annotations
from copy import deepcopy
import os
from controls import BUTTONS
from paths import load_prompt
from perception import POLICY, project, take, digest, PROGRESS_FIELDS
from plan_contract import baseline

OBSERVED_SUCCESS_TYPES = ('target_reached', 'fact_true', 'dialog_closed', 'party_grew',
                          'balls_increased', 'new_tile', 'battle_finished', 'map_changed',
                          'scene_changed', 'state_changed')


def _short(value, limit=240):
    return value[:limit] if isinstance(value, str) else value


def _compact_history(rows, limit=8):
    """Keep the outcome of recent plans without their long reasoning/policy text."""
    out = []
    for row in (rows or [])[-limit:]:
        if not isinstance(row, dict):
            continue
        out.append({'subgoal': _short(row.get('subgoal'), 80), 'status': row.get('status'),
                    'reason': _short(row.get('reason'), 80), 'target_ref': row.get('target_ref'),
                    'success': row.get('success'), 'step': row.get('step')})
    return out


def _compact_memory(memory, *, transitions=8, dialogues=8, actions=6, notes=8):
    """Bound the request and avoid duplicating the current viewport (game.local_map)."""
    if not isinstance(memory, dict):
        return memory
    result = {k: deepcopy(memory.get(k)) for k in ('policy', 'maps', 'objects',
                                                   'retention', 'limitations') if k in memory}
    result['transitions'] = deepcopy((memory.get('transitions') or [])[-transitions:])
    result['dialogues'] = [{**deepcopy(row), 'text': _short(row.get('text'), 200)}
                           for row in (memory.get('dialogues') or [])[-dialogues:] if isinstance(row, dict)]
    result['recent_actions'] = deepcopy((memory.get('recent_actions') or [])[-actions:])
    result['notes'] = [{**deepcopy(row), 'text': _short(row.get('text'), 200)}
                       for row in (memory.get('notes') or [])[-notes:] if isinstance(row, dict)]
    return result


def _waypoint_step(coordinates):
    if len(coordinates) < 2 or not all(isinstance(c, list) and len(c) == 2 for c in coordinates[:2]):
        return None
    return {(0, -1): 'up', (0, 1): 'down', (-1, 0): 'left', (1, 0): 'right'}.get(
        (coordinates[1][0] - coordinates[0][0], coordinates[1][1] - coordinates[0][1]))


def build_situation(observation, campaign, progress):
    game = project(observation)
    memory = _compact_memory(campaign.get('memory') or {})
    plan = deepcopy(campaign.get('plan'))
    history = _compact_history(campaign.get('plan_history'), 12)
    result = {
        'knowledge_mode': 'observed', 'observation_policy': POLICY,
        'step': progress.get('total_steps'), 'planning_enabled': campaign.get('model_planning_enabled'),
        'observation_id': game['observation_id'], 'game': game,
        'memory': memory, 'active_plan': plan, 'plan_active': bool(plan),
        'previous_plan_outcomes': deepcopy(history),
        'targets': deepcopy(campaign.get('targets') or {}),
        'frontier': deepcopy(campaign.get('frontier') or []),
        'failed_target_refs': deepcopy(campaign.get('failed_target_refs') or []),
        'target_failures': deepcopy(campaign.get('target_failures') or []),
        'resume_target': deepcopy(campaign.get('resume_target')),
        'baseline': {**baseline({**game, 'progress': progress}), 'scene': game['scene']['mode'],
                     'observation_id': game['observation_id']},
        'milestones': deepcopy(game.get('milestones') or {}),
        'success_types': list(OBSERVED_SUCCESS_TYPES),
        'feedback': take(progress, PROGRESS_FIELDS),
        'loop_detected': progress.get('loop_detected') is True,
        'steps_since_new_tile': progress.get('steps_since_new_tile'),
        'suspended': False,
        'navigation_geometry': deepcopy(campaign.get('navigation')),
        'observed_action_definitions': dict(BUTTONS),
    }
    result['situation_id'] = digest(result)
    return result


def build_request(observation, goal, history):
    game = project(observation)
    context = observation.get('campaign') or {}
    # Only a PlanManager-produced context can enter the default model request.
    if context.get('knowledge_mode') != 'observed':
        context = {}
    campaign = take(context, ('knowledge_mode', 'active_objective', 'plan', 'plan_history',
                              'memory', 'navigation', 'recovery', 'model_planning_enabled',
                              'failed_target_refs', 'target_failures'))
    campaign['plan_history'] = _compact_history(campaign.get('plan_history'))
    campaign['memory'] = _compact_memory(campaign.get('memory') or {})
    plan = plan_from(campaign)
    criteria = dict(BUTTONS)
    progress = take(observation.get('progress'), PROGRESS_FIELDS)

    current_focus = plan.get('intent') or "No active System Two plan."
    if plan.get('policy'):
        current_focus += f" 策略：{plan['policy']}"
    navigation = campaign.get('navigation') or {}
    step = _waypoint_step(navigation.get('coordinates') or [])
    if step:
        current_focus += f" Deterministic path next waypoint is {navigation['coordinates'][1]}; the next input toward it is {step}."
    else:
        current_focus += " Read game.scene, game.screen_text and game.battle and choose the input that advances the shown UI."

    for direction, row in (progress.get('direction_outcomes') or {}).items():
        if direction in criteria and isinstance(row, dict):
            criteria[direction] += (
                f" CURRENT from this tile: moved={row.get('moved', 0)}, "
                f"blocked_or_turn_only={row.get('blocked_or_turn_only', 0)}, unknown={row.get('unknown', 0)}.")
            background_passable = ((game.get('local_map') or {}).get('neighbors') or {}).get(
                direction, {}).get('background_passable')
            if background_passable is True and row.get('blocked_or_turn_only', 0) > 0:
                criteria[direction] += (" CURRENT: the background says this tile is passable but movement was "
                                        "blocked here, so it may be a one-way ledge or cliff; do not keep pushing it.")
    untried = progress.get('untried_directions')
    if untried:
        current_focus += f" Untried directions here: {', '.join(untried)}."
    blocked = [direction for direction, row in (progress.get('direction_outcomes') or {}).items()
               if isinstance(row, dict) and row.get('moved', 0) == 0
               and row.get('blocked_or_turn_only', 0) >= 3]
    if blocked:
        current_focus += f" Directions already blocked at this tile: {', '.join(blocked)}."

    battle = game.get('battle') or {}
    if battle.get('active') is True and (battle.get('verified') is True or battle.get('phase_verified') is True):
        current_focus += (" An active battle is described by game.battle (menu, selected_command, "
                          "selected_move_slot, awaiting_input) and game.screen_text (visible labels); "
                          "resolve it before the retained plan can continue.")
        current_focus += (f" Cursor: menu={battle.get('menu')}, "
                          f"selected={battle.get('selected_command') or battle.get('selected_move_slot')}.")
        criteria['wait'] += " CURRENT: waiting does not advance completed text or choose a menu option."

    questions = {'button': {
        'type': 'choice', 'criteria': criteria,
        'instructions': load_prompt('system1/button.txt'),
    }}
    if campaign.get('model_planning_enabled') and plan:
        questions['plan_fit'] = {
            'type': 'choice', 'instructions': load_prompt('system1/plan_fit.txt'),
            'criteria': {
                'applicable': 'The current observation is consistent with the active plan and it can be carried out.',
                'contradicted': 'The observation contradicts the active plan or shows it cannot be carried out.',
                'unknown': 'There is not enough evidence to judge.',
            },
        }
    return {'model': os.environ.get('TYPESAFE_MODEL', 'jev-latest'),
            'state': {'goal': goal, 'game': game, 'campaign': campaign,
                      'current_focus': current_focus,
                      'feedback': progress,
                      'input_policy': 'No ranked actions or default game strategy. plan_fit is a judgment; code decides whether to escalate to System Two.'},
            'questions': questions}


def plan_from(campaign):
    plan = campaign.get('plan')
    return plan if isinstance(plan, dict) else {}
