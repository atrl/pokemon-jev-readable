"""Neutral inputs for both models. Plans come from System Two; facts from experience."""
from __future__ import annotations
from copy import deepcopy
import os
from controls import BUTTONS
from paths import load_prompt
from perception import POLICY, project, take, digest, PROGRESS_FIELDS
from plan_contract import baseline, ball_count

OBSERVED_SUCCESS_TYPES = ('target_reached', 'fact_true', 'dialog_closed', 'party_grew',
                          'balls_increased', 'new_tile', 'battle_finished', 'map_changed',
                          'scene_changed', 'state_changed')


def build_situation(observation, campaign, progress):
    game = project(observation)
    memory = deepcopy(campaign.get('memory') or {})
    plan = deepcopy(campaign.get('plan'))
    result = {
        'knowledge_mode': 'observed', 'observation_policy': POLICY,
        'step': progress.get('total_steps'), 'planning_enabled': campaign.get('model_planning_enabled'),
        'observation_id': game['observation_id'], 'game': game,
        'memory': memory, 'active_plan': plan, 'plan_active': bool(plan),
        'previous_plan_outcomes': deepcopy(campaign.get('plan_history', [])[-12:]),
        'failed_plans': deepcopy(campaign.get('plan_history', [])[-12:]),
        'targets': deepcopy(campaign.get('targets') or {}),
        'failed_target_refs': deepcopy(campaign.get('failed_target_refs') or []),
        'target_failures': deepcopy(campaign.get('target_failures') or []),
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
    plan = campaign.get('plan') or {}
    questions = {'button': {
        'type': 'choice', 'criteria': dict(BUTTONS),
        'instructions': load_prompt('system1/button.txt'),
    }}
    if plan and campaign.get('model_planning_enabled'):
        questions['plan_status'] = {
            'type': 'choice', 'instructions': load_prompt('system1/plan_status.txt'),
            'criteria': {
                'continue': 'The current model-authored plan still applies; local input selection is sufficient.',
                'replan': 'Current evidence contradicts the plan or presents an unresolved strategic decision; request System Two before acting.',
            },
        }
    return {'model': os.environ.get('TYPESAFE_MODEL', 'jev-latest'),
            'state': {'goal': goal, 'game': game, 'campaign': campaign,
                      'current_focus': plan.get('intent') or goal,
                      'feedback': take(observation.get('progress'), PROGRESS_FIELDS),
                      'recent_actions': deepcopy((campaign.get('memory') or {}).get('recent_actions', [])),
                      'input_policy': 'No ranked actions or default game strategy. A plan_status=replan answer withholds the parallel button answer.'},
            'questions': questions}
