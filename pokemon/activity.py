"""Separate observable game activity from exploration and story achievement."""
from __future__ import annotations
import re


def normalized_text(value):
    # A blinking acknowledgement arrow or cursor is not advancing dialogue.
    return ' '.join(re.sub('[▼▲▶▷]', '', str(value or '')).split())


def observable_state(observation):
    world=observation.get('world') or {}; scene=observation.get('scene') or {}
    mode=scene.get('mode') if scene.get('verified') is True else None
    result={}
    player=observation.get('player') or {}
    if world.get('source_match') is not False and world.get('player_position_valid') is not False:
        values=tuple(player.get(k) for k in ('map_id','x','y'))
        if all(type(v) is int and v>=0 for v in values):result['position']=values
    if mode is not None:result['phase']=mode
    if mode in ('dialog','battle','main_menu','name_entry','species_preview'):
        dialog=observation.get('dialog') or {}
        text=dialog.get('text') if mode=='dialog' else '\n'.join((observation.get('screen_text') or {}).get('rows') or [])
        result['text']=normalized_text(text)
    if mode=='main_menu' and type(observation.get('menu_cursor_raw')) is int:
        result['menu_cursor']=observation['menu_cursor_raw']
    battle=observation.get('battle') or {}
    if battle.get('verified') is True or battle.get('phase_verified') is True:
        result['battle_phase']=(battle.get('active'),battle.get('phase'),battle.get('menu'))
        result['battle_text']=normalized_text(battle.get('visible_text'))
    if battle.get('verified') is True:
        result['battle_selection']=(battle.get('selected_command'),battle.get('selected_move_slot'))
        for name in ('player','enemy'):
            mon=battle.get(name)
            if isinstance(mon,dict):
                result[name]={k:mon.get(k) for k in ('species_internal_id','level','hp','max_hp','status_bits')}
                result[name]['moves']=[(m.get('move_id'),m.get('pp')) for m in (mon.get('moves') or [])]
    count=(observation.get('milestones') or {}).get('party_count') or {}
    party=observation.get('party')
    if count.get('verified') is True and isinstance(party,list) and len(party)==count.get('value'):
        result['party']=[(m.get('species_internal_id'),m.get('level'),m.get('hp'),m.get('status_bits'),
                          [(v.get('move_id'),v.get('pp')) for v in m.get('moves',[])]) for m in party]
    return result


def observable_changes(before,after):
    a,b=observable_state(before),observable_state(after)
    # Invalid/unavailable fields appearing or disappearing do not prove effect.
    return sorted(key for key in a.keys() & b.keys() if a[key]!=b[key])


class StallMonitor:
    def __init__(self,threshold=80,max_recovery_attempts=3):
        self.threshold=threshold;self.max_recovery_attempts=max_recovery_attempts
        self.no_effect_steps=0;self.no_strategic_progress_steps=0
        self.recovery_attempts=0;self.total_recoveries=0;self.active_steps=0

    def observe(self,before,after,outcome,progress):
        changes=observable_changes(before,after)
        self.no_effect_steps=0 if changes else self.no_effect_steps+1
        self.no_strategic_progress_steps=0 if outcome.get('meaningful_progress') else self.no_strategic_progress_steps+1
        cycle=progress.get('loop_kind') in ('position_cycle','repeated_interaction','menu_cycle')
        self.active_steps=self.active_steps+1 if changes and not cycle else 0
        if outcome.get('objective_changed') or outcome.get('milestones_completed') or outcome.get('battle_damage') or self.active_steps>=12:
            self.recovery_attempts=0
        reason=('no_observable_effect' if self.no_effect_steps>=self.threshold else
                'repeating_workflow' if cycle and self.no_strategic_progress_steps>=self.threshold else None)
        return {'observable_changes':changes,'reason':reason,'no_effect_steps':self.no_effect_steps,
                'no_strategic_progress_steps':self.no_strategic_progress_steps,
                'should_recover':reason is not None and self.recovery_attempts<self.max_recovery_attempts,
                'exhausted':reason is not None and self.recovery_attempts>=self.max_recovery_attempts}

    def begin_recovery(self):
        self.recovery_attempts+=1;self.total_recoveries+=1
        self.no_effect_steps=0;self.no_strategic_progress_steps=0;self.active_steps=0
        return self.recovery_attempts
