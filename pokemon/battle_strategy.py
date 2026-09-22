"""Explainable Gen-I damage/menu advice; JEV remains the only input selector.

The damage formula follows the pinned Red Star battle engine. Numeric move and
type-chart inputs must be independently matched to the actual ROM by Reader;
unverified source labels never silently become damage evidence.
"""
from __future__ import annotations

import math

TYPE_IDS = set(range(9)) | set(range(20, 27))
SPECIAL_TYPE_MIN = 20  # FIRE; Gen I categorizes damage by type, not by move.
CONDITIONAL_EFFECTS = {
    'OHKO_EFFECT', 'BIDE_EFFECT', 'SPECIAL_DAMAGE_EFFECT', 'SUPER_FANG_EFFECT',
    'CHARGE_EFFECT', 'FLY_EFFECT', 'TWO_TO_FIVE_ATTACKS_EFFECT', 'ATTACK_TWICE_EFFECT',
    'TWINEEDLE_EFFECT', 'EXPLODE_EFFECT', 'TRAPPING_EFFECT', 'HYPER_BEAM_EFFECT',
    'THRASH_PETAL_DANCE_EFFECT', 'RAGE_EFFECT', 'RECOIL_EFFECT', 'JUMP_KICK_EFFECT',
    'MIRROR_MOVE_EFFECT', 'METRONOME_EFFECT',
}
ASSUMPTIONS = [
    'Conditional noncritical damage estimate using the pinned Gen-I engine; not a win probability or actual damage result.',
    'Probability estimates assume uniform RNG draws, no unobserved hit modifiers, and that this attack executes before the user faints; they are not measured exact combat probabilities.',
    'Uses current battle attack/defense/special, including changes already reflected in those RAM fields; does not apply burn/stat changes a second time.',
    'Reflect, Light Screen, accuracy/evasion stages, invulnerability, Disable, critical hits, secondary effects and enemy next action are not inferred when absent.',
    'Gen-I type-based physical/special split, STAB, ROM type-chart order and 217..255 damage roll are applied; ordinary accuracy includes the 1/256 miss possibility.',
    'Conditional, delayed, self-sacrificing, fixed-damage and multi-hit mechanics require separate support and are excluded from this simple damage ranking.',
    'Suggestions never press buttons, remove choices, or replace the actual JEV answer.',
]


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _stat(mon, name):
    verified = mon.get('verified_stats') is True or (mon.get('stats_verified') or {}).get(name) is True
    value = mon.get(name)
    return value if verified and _integer(value, 1, 999) else None


def _types(mon):
    if mon.get('types_verified') is False:
        return None
    values = mon.get('types')
    if not isinstance(values, list) or not values:
        return None
    result = set()
    for row in values:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] not in TYPE_IDS:
            return None
        if mon.get('types_verified') is not True and not str(row.get('quality', '')).startswith('verified_'):
            return None
        result.add(row['id'])
    return result


def _damage_distribution(level, power, attack, defense, stab, factors):
    # GetDamageVarsForPlayerAttack scales BOTH stats if either exceeds a byte.
    if max(attack, defense) > 255:
        attack, defense = max(1, attack // 4), max(1, defense // 4)
    base = min(997, ((2 * level // 5 + 2) * power * attack // defense) // 50) + 2
    typed = base * 3 // 2 if stab else base
    # Apply each ROM-table match in order, retaining Gen-I integer truncation.
    for factor in factors:
        typed = int(typed * factor)
    distribution = ([typed] * 39 if typed < 2 else [typed * roll // 255 for roll in range(217, 256)])
    return base, distribution, attack, defense


def _rank_move(move, player, enemy):
    knowledge = move.get('knowledge') or {}
    slot = move.get('slot')
    row = {'slot': slot, 'move_id': move.get('move_id'), 'name': knowledge.get('name'),
           'pp': move.get('pp'), 'eligible': False, 'missing_evidence': [],
           'quality': 'conditional_source_engine_estimate'}

    def excluded(reason):
        row['exclusion_reason'] = reason
        return row

    if not _integer(slot, 0, 3):
        return excluded('invalid_normalized_move_slot')
    if not _integer(move.get('pp'), 0, 63):
        row['missing_evidence'].append('current_pp')
        return excluded('needs_data')
    if move['pp'] == 0:
        return excluded('no_pp')
    if move.get('disabled') is True:
        return excluded('observed_disabled_move')
    if knowledge.get('numeric_data_verified') is not True:
        row['missing_evidence'].append('rom_verified_move_numeric_data')
        return excluded('needs_data')
    power, accuracy, attack_type = knowledge.get('power'), knowledge.get('accuracy_percent'), knowledge.get('type_id')
    if not (_integer(power, 0, 255) and _integer(accuracy, 0, 100)
            and type(attack_type) is int and attack_type in TYPE_IDS):
        row['missing_evidence'].append('coherent_power_accuracy_type')
        return excluded('needs_data')
    effect = knowledge.get('effect')
    if not isinstance(effect, str):
        row['missing_evidence'].append('verified_move_effect')
        return excluded('needs_data')
    if effect in CONDITIONAL_EFFECTS or knowledge.get('name') == 'COUNTER':
        return excluded('conditional_mechanic_not_estimated')
    if power == 0:
        return excluded('no_direct_damage')
    if accuracy == 0:
        return excluded('zero_base_accuracy')
    if effect == 'DREAM_EATER_EFFECT':
        if not _integer(enemy.get('status_bits'), 0, 255):
            row['missing_evidence'].append('target_sleep_status')
            return excluded('needs_data')
        if not enemy['status_bits'] & 7:
            return excluded('dream_eater_requires_sleeping_target')
    effectiveness = move.get('effectiveness') or {}
    multiplier = effectiveness.get('multiplier')
    if effectiveness.get('verified') is not True or not _number(multiplier, 0, 4):
        row['missing_evidence'].append('rom_verified_current_defender_type_effectiveness')
        return excluded('needs_data')
    row['type_effectiveness'] = multiplier
    row['type_effectiveness_source'] = effectiveness.get('source')
    if multiplier == 0:
        return excluded('target_type_immunity')
    factors = effectiveness.get('factors')
    if factors is None:
        factors = [multiplier]
        row['rounding_note'] = 'Only combined type multiplier supplied; intermediate dual-type rounding is approximate.'
    elif (not isinstance(factors, list) or any(not _number(factor, 0, 4) for factor in factors)
          or not math.isclose(math.prod(factors), multiplier, abs_tol=1e-9)):
        row['missing_evidence'].append('coherent_type_chart_factors')
        return excluded('needs_data')
    own_types = _types(player)
    if own_types is None:
        row['missing_evidence'].append('verified_current_attacker_types_for_stab')
    category = 'special' if attack_type >= SPECIAL_TYPE_MIN else 'physical'
    attack_stat, defense_stat = ('special', 'special') if category == 'special' else ('attack', 'defense')
    attack, defense = _stat(player, attack_stat), _stat(enemy, defense_stat)
    if attack is None:
        row['missing_evidence'].append('verified_player_' + attack_stat)
    if defense is None:
        row['missing_evidence'].append('verified_enemy_' + defense_stat)
    if not _integer(player.get('level'), 1, 100):
        row['missing_evidence'].append('verified_player_level')
    if row['missing_evidence']:
        return excluded('needs_data')
    stab = attack_type in own_types
    base, damages, scaled_attack, scaled_defense = _damage_distribution(player['level'], power, attack, defense, stab, factors)
    if not any(damages):
        return excluded('zero_damage_after_integer_type_scaling')
    hit_probability = 1.0 if effect == 'SWIFT_EFFECT' else (accuracy * 255 // 100) / 256
    mean = sum(damages) / len(damages)
    expected = mean * hit_probability
    effective = sum(min(enemy['hp'], damage) for damage in damages) / len(damages) * hit_probability
    ko_probability = sum(damage >= enemy['hp'] for damage in damages) / len(damages) * hit_probability
    row.update(eligible=True, category=category, power=power, attack_type_id=attack_type,
               attack_stat=attack_stat, attack=attack, defense_stat=defense_stat, defense=defense,
               scaled_attack=scaled_attack, scaled_defense=scaled_defense, stab=1.5 if stab else 1.0,
               type_factors=factors, base_damage=base, damage_min=min(damages), damage_max=max(damages),
               damage_mean_on_hit=round(mean, 4), base_hit_probability=round(hit_probability, 6),
               expected_damage=round(expected, 4), expected_effective_damage=round(effective, 4),
               estimated_ko_probability=round(ko_probability, 6),
               probability_scope='single noncritical attack under uniform RNG, baseline accuracy and no unobserved defensive/invulnerability modifiers; not battle win probability',
               explanation=f'{category}: {attack_stat} {attack} / enemy {defense_stat} {defense}; '
                           f'power {power}, STAB {1.5 if stab else 1:g}, type multiplier {multiplier:g}, '
                           f'noncritical damage on hit {min(damages)}..{max(damages)}')
    return row


def plan_battle(observation_or_battle):
    """Rank current verified damage moves and suggest at most one menu input."""
    value = observation_or_battle if isinstance(observation_or_battle, dict) else {}
    battle = value.get('battle', value)
    battle = battle if isinstance(battle, dict) else {}
    result = {'status': 'needs_data', 'ranked_moves': [], 'recommended_slot': None,
              'next_button': None, 'missing_evidence': [], 'assumptions': list(ASSUMPTIONS),
              'role': 'advisory_only; JEV chooses every physical input',
              'mechanics_quality': 'pinned_source_engine_estimate; numeric moves and type chart require separate actual-ROM verification',
              'source': 'pinned Red Star engine/battle/core.asm:GetDamageVarsForPlayerAttack, CalculateDamage, AdjustDamageForMoveType, RandomizeDamage, MoveHitTest'}
    if battle.get('active') is False:
        return {**result, 'status': 'not_in_battle'}
    if battle.get('active') is not True or battle.get('verified') is not True:
        result['missing_evidence'].append('verified_active_battle_and_combatants')
        return result
    player, enemy = battle.get('player') or {}, battle.get('enemy') or {}
    if not (_integer(player.get('hp'), 0, 999) and _integer(enemy.get('hp'), 0, 999)):
        result['missing_evidence'].append('current_combatant_hp')
        return result
    if player['hp'] == 0:
        return {**result, 'status': 'player_fainted_needs_switch'}
    if enemy['hp'] == 0:
        return {**result, 'status': 'enemy_fainted_resolve_ui'}
    moves = player.get('moves')
    if not isinstance(moves, list) or not moves:
        result['missing_evidence'].append('current_player_moves')
        return result
    ranked = [_rank_move(move, player, enemy) for move in moves if isinstance(move, dict)]
    ranked.sort(key=lambda row: (row['eligible'], row.get('estimated_ko_probability', -1),
                                row.get('expected_effective_damage', -1), row.get('expected_damage', -1),
                                row.get('pp') if type(row.get('pp')) is int else -1,
                                -(row['slot'] if type(row['slot']) is int else 99)), reverse=True)
    result['ranked_moves'] = ranked
    result['missing_evidence'] = [f"slot {row['slot']}: {missing}" for row in ranked for missing in row['missing_evidence']]
    candidates = [row for row in ranked if row['eligible']]
    if not candidates:
        result['status'] = 'needs_data' if result['missing_evidence'] else 'no_usable_estimated_damage_move'
        return result
    best = candidates[0]; result['recommended_slot'] = best['slot']
    result['status'] = 'advice_ready'
    result['recommendation_reason'] = 'Highest estimated knockout probability, then expected effective damage, among currently usable evaluated moves.'
    result['recommended_move'] = best['name']
    menu = battle.get('menu')
    if menu == 'command':
        command = battle.get('selected_command')
        result['next_button'] = {'FIGHT': 'a', 'PKMN': 'left', 'ITEM': 'up', 'PACK': 'up', 'RUN': 'up'}.get(command)
        if result['next_button'] is None:
            result['missing_evidence'].append('visible_selected_command')
    elif menu == 'move':
        selected = battle.get('selected_move_slot')
        slots = {move.get('slot') for move in moves if isinstance(move, dict) and type(move.get('slot')) is int}
        if type(selected) is int and selected in slots:
            result['next_button'] = 'a' if selected == best['slot'] else 'down' if selected < best['slot'] else 'up'
        else:
            result['missing_evidence'].append('visible_normalized_selected_move_slot')
    else:
        result['ui_reason'] = 'Damage ranking is retained; text, animation or an unrecognized menu needs current visible UI handling.'
    return result
