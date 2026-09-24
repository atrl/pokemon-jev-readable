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
    """Bound the request: keep recent evidence, truncate long free text."""
    if not isinstance(memory, dict):
        return memory
    result = {k: deepcopy(memory.get(k)) for k in ('policy', 'current_map', 'maps', 'objects',
                                                   'retention', 'limitations') if k in memory}
    result['transitions'] = deepcopy((memory.get('transitions') or [])[-transitions:])
    result['dialogues'] = [{**deepcopy(row), 'text': _short(row.get('text'), 200)}
                           for row in (memory.get('dialogues') or [])[-dialogues:] if isinstance(row, dict)]
    result['recent_actions'] = deepcopy((memory.get('recent_actions') or [])[-actions:])
    result['notes'] = [{**deepcopy(row), 'text': _short(row.get('text'), 200)}
                       for row in (memory.get('notes') or [])[-notes:] if isinstance(row, dict)]
    return result


def _scene_guidance(game):
    """Scene-appropriate success kinds, so one planner prompt fits every scene."""
    battle = game.get('battle') or {}
    if battle.get('active') is True:
        return ('A battle is active. Use success battle_finished; do not plan overworld movement '
                'or a UI predicate.')
    mode = (game.get('scene') or {}).get('mode')
    if mode == 'dialog':
        return ('A dialog is open. Use success dialog_closed for the interaction, or a fact predicate, '
                'rather than target_reached.')
    if mode in ('main_menu', 'name_entry', 'species_preview'):
        return 'A menu/entry screen is open. Use state_changed or the matching UI predicate.'
    if mode == 'overworld':
        return ('Overworld. Use target_reached/new_tile/map_changed for movement; do not use '
                'battle_finished. For a portal, map_changed may be the true transition test.')
    return 'The scene is uncertain. Use state_changed; do not assume a specific UI.'


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
        'failed_plans': deepcopy(history),
        'targets': deepcopy(campaign.get('targets') or {}),
        'frontier': deepcopy(campaign.get('frontier') or []),
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
        'scene_guidance': _scene_guidance(game),
        'battle_ui': _battle_ui(game.get('battle') or {}, (game.get('screen_text') or {}).get('rows')),
    }
    result['situation_id'] = digest(result)
    return result


def _battle_ui(battle, rows):
    """Factual battle UI description from the menu state and visible text."""
    text = " ".join(str(row) for row in (rows or []))
    if "Choose a POK" in text or "choose a POK" in text:
        return ("A party-selection prompt is open: A confirms the highlighted party member and "
                "up/down changes the highlight. This is not the command menu; do not try RUN.")
    menu = battle.get('menu')
    if menu == 'command':
        return ("A battle command menu is open (FIGHT top-left, PKMN top-right, ITEM bottom-left, RUN "
                f"bottom-right); the cursor is on {battle.get('selected_command') or 'unknown'}.")
    if menu == 'move':
        return "A battle move list is open."
    if battle.get('phase') == 'text_before_combatants_ready':
        return "Battle intro text: A advances it."
    if menu == 'text_or_animation':
        return "Battle text/animation: A advances completed text; waiting does not."
    return None


def _battle_focus(battle):
    """Neutral UI routing: the battle UI is resolved before the plan's predicate can be checked."""
    if battle.get('menu') == 'move':
        return ("A battle move menu is open. A confirms the selected move; a move with 0 PP is rejected and changes "
                "nothing, so move the cursor to a move with PP above 0 first.")
    if battle.get('menu') == 'command':
        return "A battle command menu is open. Choose a command to resolve the battle."
    return "Resolve the active battle UI (text, animation or menu). Waiting does not advance completed text."


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
    plan = campaign.get('plan') or {}
    battle = game.get('battle') or {}
    battle_active = battle.get('active') is True and (
        battle.get('verified') is True or battle.get('phase_verified') is True)
    criteria = dict(BUTTONS)
    navigation = campaign.get('navigation') or {}
    policy = plan.get('policy')
    if battle_active:
        rows = (game.get('screen_text') or {}).get('rows')
        note = _battle_ui(battle, rows) or _battle_focus(battle)
        current_focus = (f"{plan['intent']} " if plan.get('intent') else "")
        current_focus += (f" 策略：{policy} " if policy else "") + note
        menu = battle.get('menu')
        text = " ".join(str(row) for row in (rows or []))
        if "Choose a POK" in text or "choose a POK" in text:
            criteria['a'] = (
                "Press A to confirm the highlighted party member. CURRENT: a party-selection prompt is open; "
                "up/down changes the highlight. This is not the command menu; RUN is not available.")
            criteria['up'] += " CURRENT: moves the party highlight up."
            criteria['down'] += " CURRENT: moves the party highlight down."
            criteria['b'] += " CURRENT: the party-selection prompt does not accept RUN; confirm a member with A."
        elif menu == 'command':
            current = battle.get('selected_command')
            criteria['a'] += (
                " CURRENT: command grid is FIGHT top-left, PKMN top-right, ITEM bottom-left, RUN "
                f"bottom-right; cursor is on {current or 'unknown'}, A confirms the highlighted command.")
            criteria['right'] += " CURRENT: right moves the command cursor horizontally (FIGHT<->PKMN, ITEM<->RUN)."
            criteria['down'] += " CURRENT: down moves the command cursor vertically (FIGHT<->ITEM, PKMN<->RUN)."
            criteria['left'] += " CURRENT: left moves the command cursor horizontally (PKMN<->FIGHT, RUN<->ITEM)."
            criteria['up'] += " CURRENT: up moves the command cursor vertically (ITEM<->FIGHT, RUN<->PKMN)."
            criteria['b'] += " CURRENT: B leaves the battle command menu where the game allows it."
        elif menu == 'move':
            moves = (battle.get('player') or {}).get('moves') or []
            slot = battle.get('selected_move_slot')
            row = next((m for m in moves if m.get('slot') == slot), None)
            if isinstance(row, dict):
                name = (row.get('knowledge') or {}).get('name') or row.get('move_id')
                criteria['a'] = (
                    f"Press A to confirm the selected move. CURRENT: selected move {name} has {row.get('pp')} PP; "
                    "if PP is 0 the game rejects it and no input advances, so move the cursor to a move with PP above 0 first.")
            criteria['b'] += " CURRENT: B returns from the move list to the battle command menu."
            for direction in ('up', 'down', 'left', 'right'):
                criteria[direction] += " CURRENT: moves the battle menu cursor."
        criteria['wait'] += " CURRENT: waiting does not advance completed battle text or a battle menu."
    else:
        current_focus = plan.get('intent') or "No active System Two plan."
        if policy:
            current_focus += f" 策略：{policy}"
        coordinates = navigation.get('coordinates') or []
        step = None
        if len(coordinates) >= 2 and all(isinstance(c, list) and len(c) == 2 for c in coordinates[:2]):
            step = {(0, -1): 'up', (0, 1): 'down', (-1, 0): 'left', (1, 0): 'right'}.get(
                (coordinates[1][0] - coordinates[0][0], coordinates[1][1] - coordinates[0][1]))
        if step:
            current_focus += f" Deterministic path next waypoint is {coordinates[1]}; the next input toward it is {step}."
        else:
            current_focus += " Use the nearby background grid, untried directions and retained memory to explore."
        progress = take(observation.get('progress'), PROGRESS_FIELDS)
        for direction, row in (progress.get('direction_outcomes') or {}).items():
            if direction in criteria and isinstance(row, dict):
                criteria[direction] += (
                    f" CURRENT from this tile: moved={row.get('moved', 0)}, "
                    f"blocked_or_turn_only={row.get('blocked_or_turn_only', 0)}, unknown={row.get('unknown', 0)}.")
        untried = progress.get('untried_directions')
        if untried:
            current_focus += f" Untried directions here: {', '.join(untried)}."
    questions = {'button': {
        'type': 'choice', 'criteria': criteria,
        'instructions': load_prompt('system1/button.txt'),
    }}
    if campaign.get('model_planning_enabled'):
        questions['plan_status'] = {
            'type': 'choice', 'instructions': load_prompt('system1/plan_status.txt'),
            'criteria': {
                'continue': 'Handle this step yourself. Valid with or without an active plan: use the current observation, retained memory and any active plan to choose the button. No System Two call is made.',
                'replan': 'Escalate to System Two and wait for a new plan before any input. Your parallel button answer is withheld. Use it for a strategic decision, not a routine battle, menu or dialogue; escalate when several consecutive actions changed nothing and local input cannot progress.',
            },
        }
    return {'model': os.environ.get('TYPESAFE_MODEL', 'jev-latest'),
            'state': {'goal': goal, 'game': game, 'campaign': campaign,
                      'current_focus': current_focus,
                      'feedback': take(observation.get('progress'), PROGRESS_FIELDS),
                      'recent_actions': deepcopy((campaign.get('memory') or {}).get('recent_actions', [])),
                      'input_policy': 'No ranked actions or default game strategy. A plan_status=replan answer withholds the parallel button answer.'},
            'questions': questions}
