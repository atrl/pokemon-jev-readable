"""Default observed mode: counterfactual leakage checks and dual-model contract tests.

All game/HTTP data here are explicit test doubles. Real adapter smoke tests live
under tests/integration. These assertions test agency, NOT a completion claim.
"""
import io
import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from _paths import ROOT, POKEMON
from perception import POLICY, project
from experience import Experience
from plan_manager import PlanManager
from plan_contract import normalize_plan
from model_context import build_situation
from jev import build_request, validate_plan_fit, choose
from controls import BUTTONS
from run import run
import planning


def raw(x=4, y=4, mid=38):
    return {'game': 'EXPLICIT OFFLINE DOUBLE', 'frame': 100, 'errors': [],
            'player': {'name': 'TEST', 'map_id': mid, 'x': x, 'y': y, 'money': 3000,
                       'facing': 'down', 'facing_quality': 'verified_direction_response'},
            'scene': {'mode': 'overworld', 'verified': True}, 'dialog': {'open': False, 'text': '', 'awaiting_input': False},
            'screen_text': {'rows': []}, 'menu_cursor_raw': 0,
            'party': [], 'party_state': {'ready': True, 'verified': True}, 'bag': [],
            'battle': {'active': False, 'verified': True},
            'milestones': {'party_count': {'value': 0, 'verified': True}, 'badge_count': {'value': 0, 'verified': True},
                           'hidden_plot': {'value': False, 'verified': True}},
            'world': {'map_id': mid, 'name': 'HIDDEN MAP LABEL', 'width': 24, 'height': 24,
                      'source_match': True, 'player_position_valid': True, 'input_lock': {},
                      'objects': [{'object_id': 1, 'x': 5, 'y': 2, 'sprite': 'VISIBLE_SPRITE', 'text_id': 99,
                                   'active': True, 'visible': True, 'source_x': 999},
                                  {'object_id': 2, 'x': 20, 'y': 20, 'sprite': 'HIDDEN NURSE',
                                   'active': True, 'visible': False}],
                      'warps': [{'warp_id': 0, 'x': 6, 'y': 4, 'destination_map_id': 99,
                                 'destination_name': 'UNVISITED ANSWER', 'quality': 'verified_current_ram'},
                                {'warp_id': 1, 'x': 20, 'y': 20, 'destination_map_id': 98, 'quality': 'verified_current_ram'}],
                      'connections': [{'direction': 'north', 'destination_map_id': 88}],
                      'script_triggers': [{'x': 1, 'action': 'SPOILER'}]},
            'local_map': {'verified': True, 'quality': 'advisory_background_only',
                          'rows': ['.....', '.....', '..@..', '.....', '.....'], 'player_cell': {'x': 2, 'y': 2}},
            'progress': {'total_steps': 0, 'visited_tiles': 1, 'loop_detected': False,
                         'same_position_steps': 0, 'steps_since_new_tile': 0}}


def setup(raw_observation=None):
    game = project(raw_observation or raw())
    game['progress'] = deepcopy((raw_observation or raw())['progress'])
    manager = PlanManager(); manager.model_planning_enabled = True
    ctx = manager.context(game)
    return manager, game, build_situation(game, ctx, game['progress'])


def proposal(**changes):
    value = {'subgoal': 'user_defined', 'intent': 'Test an observed location',
             'reasoning': 'An explicit offline test, not a strategy',
             'target_ref': 'cell:38:5,4', 'success': {'type': 'target_reached'},
             'expires_steps': 40, 'max_no_effect_steps': 8}
    value.update(changes)
    return value


def response(button='right', fit='applicable'):
    return {'answers': {'button': {'type': 'choice', 'choice': button, 'confidence': 1,
                                  'probabilities': {k: int(k == button) for k in BUTTONS}},
                        'plan_fit': {'type': 'choice', 'choice': fit, 'confidence': 1,
                                     'probabilities': {k: int(k == fit) for k in ('applicable', 'contradicted', 'unknown')}}}}


def keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(keys(v) for v in value), set())
    return set()


class PerceptionTests(unittest.TestCase):
    def test_projection_is_pure_and_idempotent(self):
        r = raw(); original = deepcopy(r)
        v = project(r)
        self.assertEqual(v, project(v)); self.assertEqual(r, original)

    def test_unseen_world_changes_do_not_change_model_observation(self):
        a = raw(); b = deepcopy(a)
        b['world']['objects'][1]['sprite'] = 'DIFFERENT HIDDEN NPC'
        b['world']['objects'][1]['x'] = 22
        b['world']['warps'][0]['destination_map_id'] = 201
        b['world']['warps'][0]['destination_name'] = 'OTHER FUTURE'
        b['world']['script_triggers'] = [{'action': 'OTHER SPOILER'}]
        b['milestones']['hidden_plot']['value'] = True
        self.assertEqual(project(a), project(b))

    def test_both_model_inputs_invariant_to_hidden_world(self):
        a = raw(); b = deepcopy(a)
        b['world']['warps'][0]['destination_map_id'] = 200
        b['world']['objects'][1]['sprite'] = 'NO SPOILER'
        b['milestones']['hidden_plot']['value'] = True
        ma, ga, sa = setup(a); mb, gb, sb = setup(b)
        self.assertEqual(sa, sb)
        ga['campaign'] = ma.context(ga); gb['campaign'] = mb.context(gb)
        self.assertEqual(build_request(ga, 'same goal', []), build_request(gb, 'same goal', []))

    def test_own_status_is_allowed_but_no_auto_strategy(self):
        r = raw(); r['party'] = [{'hp': 1, 'max_hp': 40, 'level': 8, 'status_bits': 8, 'moves': []}]
        r['milestones']['party_count']['value'] = 1
        m, g, s = setup(r)
        self.assertEqual(s['game']['party'][0]['hp'], 1)
        self.assertIsNone(m.plan)
        self.assertNotIn('heal_party', json.dumps(s))
        self.assertNotIn('recommended_move', keys(s))

    def test_opponent_internal_stats_and_exact_hp_are_not_exposed(self):
        r = raw(); r['scene']['mode'] = 'battle'
        r['battle'] = {'active': True, 'verified': True, 'type': 'wild', 'menu': 'command',
                       'enemy': {'level': 12, 'hp': 50, 'max_hp': 100, 'attack': 90, 'defense': 55, 'moves': ['secret']}}
        g = project(r)
        self.assertEqual(g['battle']['enemy']['health_bar_units'], 24)
        self.assertTrue({'hp','max_hp','attack','defense','moves'}.isdisjoint(g['battle']['enemy']))
        r['battle']['enemy'].update(hp=100, max_hp=200, defense=999)
        self.assertEqual(g, project(r))

    def test_visible_entities_not_offscreen_roles(self):
        g = project(raw())
        self.assertEqual(len(g['world']['objects']), 1)
        self.assertEqual(len(g['world']['warps']), 1)
        self.assertFalse({'text_id','source_x','script_triggers','destination_map_id'} & keys(g))
        self.assertEqual(g['world']['connections'], [])

    def test_missing_inventory_is_unknown_not_empty(self):
        r=raw(); r['party']=None; r['bag']=None
        g=project(r); self.assertIsNone(g['party']); self.assertIsNone(g['bag'])

    def test_unverified_geometry_cannot_create_known_targets(self):
        r=raw(); r['world']['source_match']=False
        m,g,s=setup(r)
        self.assertEqual(s['targets'], {}); self.assertEqual(m.memory.maps, {})

    def test_legacy_strategy_in_raw_progress_cannot_enter_projection(self):
        r=raw(); r['progress']['current_focus']='DO_PREWRITTEN_ROUTE'
        r['campaign']={'story_objective': {'intent': 'SPOILER'}}
        self.assertNotIn('DO_PREWRITTEN_ROUTE', json.dumps(project(r)))
        self.assertNotIn('SPOILER', json.dumps(build_request(r,'goal',[])))

    def test_frame_and_blink_do_not_fake_state_change(self):
        a=raw(); a['scene']['mode']='dialog'; a['dialog'].update(open=True,text='Hello▼')
        b=deepcopy(a); b['frame'] += 500; b['dialog']['text']='Hello'
        self.assertEqual(project(a)['observation_id'],project(b)['observation_id'])


class ExperienceTests(unittest.TestCase):
    def test_actual_transition_learns_destination_not_reverse(self):
        m=Experience(); a=raw(); b=raw(mid=37)
        m.observe(a); self.assertNotIn('map:37',m.catalog(project(a)))
        m.record('right',a,b)
        self.assertEqual(m.transitions[0]['to'][0],37)
        self.assertEqual(m.transitions[0]['reversible'],'unknown')
        self.assertEqual(len(m.transitions),1)

    def test_blackout_is_not_a_walk_connection(self):
        m=Experience(); a=raw(); a['scene']['mode']='battle'
        m.record('a',a,raw(mid=37)); self.assertEqual(m.transitions,[])

    def test_remembered_objects_marked_stale_not_fresh(self):
        m=Experience(); m.observe(raw()); later=raw(); later['world']['objects'][0]['visible']=False
        m.observe(later); candidate=m.catalog(project(later))['object:38:1']
        self.assertFalse(candidate['currently_visible']); self.assertEqual(candidate['last_seen_step'],0)

    def test_geometry_has_no_recommended_button(self):
        m,g,s=setup(); target=s['targets']['cell:38:5,4']
        path=m.memory.path_to(g,target)
        self.assertEqual(path['coordinates'],[[4,4],[5,4]])
        self.assertNotIn('next_button',path)

    def test_no_target_selection_without_model_plan(self):
        m,g,_=setup(); c=m.context(g)
        self.assertIsNone(c['active_objective']['intent'])
        self.assertEqual(c['navigation']['status'],'no_model_target')

    def test_new_memory_survives_json_checkpoint(self):
        m,g,s=setup(); m.memory.record('right',raw(),raw(x=5))
        restored=PlanManager(json.loads(json.dumps(m.snapshot())))
        self.assertEqual(restored.memory.snapshot(),m.memory.snapshot())

    def test_legacy_migration_keeps_observations_not_prior_instructions(self):
        old={'version':1,'steps':100,'tiles':{'38':{'1,1':{'passable':True,'source':'observed_background'},
                                                  '2,2':{'passable':True,'source':'source_prior'}}},
             'plan':{'intent':'SPOILER'},'history_facts':{'secret':True},'support':{'id':'heal_party'},
             'clues':[{'id':'t','map_id':38,'position':[1,1],'text':'A recorded clue','source':'observed_dialog'}]}
        m=PlanManager(old)
        self.assertIn('1,1',m.memory.maps['38']['cells']); self.assertNotIn('2,2',m.memory.maps['38']['cells'])
        self.assertIsNone(m.plan); self.assertNotIn('SPOILER',json.dumps(m.snapshot()))
        self.assertEqual(m.memory.dialogues[0]['text'],'A recorded clue'); self.assertEqual(m.steps,100)

    def test_recovery_preserves_all_observed_terrain(self):
        m,g,s=setup(); before=deepcopy(m.memory.maps)
        m.recover(g,attempt=1,reason='test',failed_button='up')
        self.assertEqual(before,m.memory.maps); self.assertNotIn('instruction',m.recovery)


class ContractTests(unittest.TestCase):
    def test_no_defaults_for_flee_heal_or_team_size(self):
        _,_,s=setup(); p=normalize_plan(proposal(),s)
        self.assertEqual(p['resource_policy'],{}); self.assertEqual(p['policy'],''); self.assertEqual(p['replan_when'],[])

    def test_notes_need_existing_evidence_and_remain_hypotheses(self):
        m,g,s=setup()
        p=normalize_plan(proposal(memory_updates=[{'text':'This may be an exit','evidence_refs':[g['observation_id']]}]),s)
        m.set_plan(p)
        self.assertEqual(m.memory.notes[0]['source'],'system2_hypothesis_not_verified_fact')
        # A note with no real evidence ref is dropped, not accepted as a hypothesis.
        p=normalize_plan(proposal(memory_updates=[{'text':'I know the future','evidence_refs':['unknown']}]),s)
        self.assertEqual(p['memory_updates'],[])

    def test_memory_note_may_cite_a_target_ref(self):
        _,_,s=setup(); ref=next(iter(s['targets']))
        p=normalize_plan(proposal(memory_updates=[{'text':'considered','evidence_refs':[ref]}]), s)
        self.assertEqual(p['memory_updates'][0]['evidence_refs'],[ref])

    def test_memory_note_may_cite_a_nested_action_observation(self):
        _,_,s=setup(); s=dict(s)
        s['memory']=dict(s.get('memory') or {})
        s['memory']['recent_actions']=[{'after':{'observation_id':'obs:abc123'}}]
        p=normalize_plan(proposal(target_ref=None,success={'type':'state_changed'},
                                  memory_updates=[{'text':'seen','evidence_refs':['obs:abc123']}]), s)
        self.assertEqual(p['memory_updates'][0]['evidence_refs'],['obs:abc123'])

    def test_frontier_cells_are_selectable_targets(self):
        m,g,s=setup(); c=m.context(g)
        self.assertTrue(c['frontier'])
        for ref in c['frontier']:
            self.assertIn(ref, c['targets'])
            self.assertTrue(c['targets'][ref].get('frontier'))

    def test_model_context_has_no_game_specific_ui_rules(self):
        source = (POKEMON / "model_context.py").read_text()
        for token in ("Choose a POK", "FIGHT", "PKMN", "RAGE", "top-left"):
            self.assertNotIn(token, source)

    def test_jev_request_avoids_duplicate_state(self):
        from model_context import build_request
        _,g,s=setup(); g["campaign"]={**s,"model_planning_enabled":False}
        request=build_request(g,"goal",[])
        self.assertNotIn("recent_actions", request["state"])
        self.assertNotIn("current_map", request["state"]["campaign"]["memory"])

    def test_ui_steps_are_validated(self):
        _,_,s=setup()
        for bad in (['teleport'], ['a']*7, 'a', [1, 2]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_plan(proposal(ui_steps=bad), s)
        self.assertEqual(normalize_plan(proposal(ui_steps=['down','a']), s)['ui_steps'], ['down','a'])

    def test_unsupported_fields_and_raw_coordinates_rejected(self):
        _,_,s=setup()
        for fields in ({'target_map_id':99},{'buttons':['a']},{'target_ref':'map:99'},{'success':{'type':'eval','code':'x'}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): normalize_plan(proposal(**fields),s)

    def test_model_plan_completed_returns_to_system_one_control(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s))
        m.record('right',g,raw(x=5)); c=m.context(raw(x=5))
        self.assertIsNone(m.plan); self.assertEqual(c['plan_history'][-1]['status'],'completed')
        # No runtime auto-plan: the next step is System One's to decide.
        self.assertEqual(c['active_objective']['id'],'awaiting_model_plan')

    def test_plan_fit_is_requested_with_an_active_plan(self):
        from model_context import build_request
        _,g,s=setup(); g['campaign']={**s, 'model_planning_enabled':True,
                                      'plan':{'subgoal':'x','intent':'go','policy':''},
                                      'active_objective':{'id':'plan:x'}}
        request=build_request(g,'goal',[])
        self.assertIn('plan_fit',request['questions'])
        self.assertEqual(set(request['questions']['plan_fit']['criteria']),{'applicable','contradicted','unknown'})
        self.assertNotIn('plan_fit', build_request({**g,'campaign':{**g['campaign'],'plan':None}}, 'goal', [])['questions'])

    def test_gameplay_risk_does_not_replace_model_plan(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s))
        danger=raw(); danger['party']=[{'hp':1,'max_hp':90,'status_bits':8,'moves':[]}]
        danger['milestones']['party_count']['value']=1
        c=m.context(danger)
        self.assertEqual(c['active_objective']['id'],'plan:user_defined'); self.assertFalse(c['plan_suspended'])

    def test_only_model_authored_risk_interrupt_is_executed(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(replan_when=[{'type':'party_hp_below','ratio':0.3}]),s))
        danger=raw(); danger['party']=[{'hp':1,'max_hp':90,'moves':[]}]; danger['milestones']['party_count']['value']=1
        c=m.context(danger)
        self.assertIsNone(m.plan); self.assertEqual(c['plan_history'][-1]['reason'],'planner_interrupt_condition')
        self.assertNotIn('heal_party',json.dumps(c))

    def test_unknown_path_not_automatically_failure(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s)); m.steps=10
        c=m.context(g); self.assertIsNotNone(m.plan)

    def test_old_contract_cannot_be_a_default_plan(self):
        m,_,_=setup()
        with self.assertRaises(ValueError): m.set_plan({'schema_version':2,'intent':'old guide'})

    def test_no_completion_on_plan_creation(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(target_ref=None,success={'type':'state_changed'}),s))
        m.context(g); self.assertIsNotNone(m.plan)

    def test_expiry_is_not_success(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(expires_steps=10),s)); m.steps=10
        m.context(g); self.assertEqual(m.plan_history[-1]['status'],'expired')

    def test_enemy_full_details_cannot_appear_in_plan_situation(self):
        r=raw(); r['scene']['mode']='battle'; r['battle']={'active':True,'verified':True,'enemy':{'hp':1,'max_hp':10,'defense':999,'moves':['secret']}}
        _,_,s=setup(r); self.assertNotIn('defense',s['game']['battle']['enemy'])

    def test_inherited_loop_does_not_end_a_fresh_plan(self):
        # A loop that predates the plan must not fail it before it produced evidence.
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s)); m.steps=20
        r=raw(); r['progress'].update(loop_detected=True, same_position_steps=0, steps_since_new_tile=0)
        m.context(r); self.assertIsNotNone(m.plan)

    def test_failed_target_is_annotated_not_hidden(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s))
        m.finish_plan('failed','observed_repetition_requires_model_review',g)
        c=m.context(raw())
        self.assertIn('cell:38:5,4', c['failed_target_refs'])
        # The ref stays selectable so the model never references an absent one.
        self.assertIn('cell:38:5,4', c['targets'])
        self.assertEqual(c['targets']['cell:38:5,4'].get('previous_attempts'), 1)
        normalize_plan(proposal(), build_situation(raw(), c, raw()['progress']))


class RuntimeTests(unittest.TestCase):
    def run_double(self, folder, reviews=('applicable','applicable')):
        current=raw(); world=Mock(); world.game=SimpleNamespace(frame_count=100); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.side_effect=lambda:deepcopy(current)
        def press(button,**_):
            if button=='right': current['player']['x']+=1
            current['frame']+=48
        world.press.side_effect=press
        http_requests=[]; upper_requests=[]; turn=iter(reviews)
        def upper(request,**_):
            body=json.loads(request.data); upper_requests.append(body)
            situation=json.loads(body['messages'][1]['content'])['situation']
            x=situation['game']['player']['x']
            plan=proposal(target_ref=f'cell:38:{x+1},4')
            return io.BytesIO(json.dumps({'model':'EXPLICIT TEST DOUBLE','choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}).encode())
        opener=Mock(); opener.open.side_effect=upper
        def lower(request,**_):
            body=json.loads(request.data); http_requests.append(body)
            return io.BytesIO(json.dumps(response(fit=next(turn))).encode())
        with (patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-jev','DEEPSEEK_API_KEY':'fixture-ds'}),
              patch('run.Emulator',return_value=world),patch('run.Reader',return_value=reader),
              patch('urllib.request.build_opener',return_value=opener),patch('urllib.request.urlopen',side_effect=lower)):
            report=run(Path('OFFLINE.gb'),folder,goal='User supplied goal',steps=len(reviews),planner_mode='deepseek',max_seconds=5)
        return report,world,upper_requests,http_requests

    def test_brain_ui_steps_execute_without_jev(self):
        _,_,s=setup()
        plan=normalize_plan(proposal(target_ref=None, success={'type':'state_changed'},
                                     ui_steps=['down','a']), s)
        world=Mock(); world.game=SimpleNamespace(frame_count=0); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.return_value=raw()
        choose=Mock(return_value={'answer':{'choice':'wait'}})
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-jev','DEEPSEEK_API_KEY':'fixture-ds'}), \
             patch('run.Emulator',return_value=world), patch('run.Reader',return_value=reader), \
             patch('run.choose',choose), patch('planning.call_planner',return_value=plan):
            report=run(Path('OFFLINE.gb'),Path(tmp)/'run',goal='g',steps=2,planner_mode='deepseek')
        self.assertEqual(report['plans'],1)
        self.assertEqual(report['executed_actions'],2)
        choose.assert_not_called()

    def test_unusable_planner_reply_does_not_pause_the_run(self):
        world=Mock(); world.game=SimpleNamespace(frame_count=0); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.return_value=raw()
        choose=Mock(return_value={'answer':{'choice':'wait'}})
        bad=planning.PlannerError('invalid_plan_or_json')
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-jev','DEEPSEEK_API_KEY':'fixture-ds'}), \
             patch('run.Emulator',return_value=world), patch('run.Reader',return_value=reader), \
             patch('run.choose',choose), patch('planning.call_planner',side_effect=bad):
            report=run(Path('OFFLINE.gb'),Path(tmp)/'run',goal='g',steps=6,planner_mode='deepseek')
        self.assertEqual(report['plans'],0)
        self.assertGreaterEqual(report['executed_actions'],1)
        self.assertNotEqual(report['status'],'planner_unavailable')

    def test_code_triggers_a_bootstrap_plan_then_executes(self):
        with TemporaryDirectory() as tmp:
            report,world,upper,lower=self.run_double(Path(tmp)/'run')
            self.assertEqual(report['observation_policy'],'structured_player_v1'); self.assertEqual(report['executed_actions'],2)
            # Code triggers the bootstrap plan; JEV never commands orchestration.
            self.assertEqual(report['planning_calls'],1); self.assertEqual(len(upper),1); self.assertEqual(len(lower),2)
            for sent in lower:
                self.assertTrue({'recommended_move','next_button','story_reference','story_objective','heal_party'}.isdisjoint(keys(sent)))
                self.assertEqual(sent['questions']['button']['criteria'],BUTTONS)
            self.assertEqual(world.press.call_count,2)

    def test_escalation_is_code_gated_by_contradiction_and_cooldown(self):
        with TemporaryDirectory() as tmp:
            # JEV judges contradicted every step, but a fresh plan has not run 8
            # actions, so code does not escalate yet and still executes buttons.
            report,world,upper,_=self.run_double(Path(tmp)/'run',('contradicted',)*3)
            self.assertEqual(report['planning_calls'],1)  # bootstrap plan only
            self.assertEqual(report['plan_review_requests'],0)
            self.assertGreaterEqual(report['executed_actions'],1)

    def test_malformed_planner_reply_is_retried_not_fatal(self):
        _,_,s=setup(); good=normalize_plan(proposal(),s); calls=[]
        def planner(situation,goal,**_):
            calls.append(1)
            if len(calls)==1: raise planning.PlannerError('invalid_plan_or_json')
            return good
        world=Mock(); world.game=SimpleNamespace(frame_count=0); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.return_value=raw()
        decision={'answer':{'choice':'wait'},'source':'offline-test-double'}
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-jev','DEEPSEEK_API_KEY':'fixture-ds'}), \
             patch('run.Emulator',return_value=world), patch('run.Reader',return_value=reader), \
             patch('run.choose',Mock(return_value=decision)), \
             patch('planning.call_planner',side_effect=planner):
            report=run(Path('OFFLINE.gb'),Path(tmp)/'run',goal='g',steps=1,planner_mode='deepseek')
            rows=[json.loads(x) for x in (Path(tmp)/'run/events.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls),2); self.assertEqual(report['plans'],1)
        self.assertTrue(any(r['type']=='planning_retry' for r in rows))

    def test_remembered_maps_remain_targets_when_position_is_unaligned(self):
        m=Experience(); m.observe(raw())
        unaligned=raw(); unaligned['world']['source_match']=False
        self.assertIn('map:38', m.catalog(project(unaligned)))

    def test_null_target_ui_plan_is_valid_without_a_catalog(self):
        _,_,s=setup(); s=dict(s); s['targets']={}
        p=normalize_plan({'subgoal':'probe','intent':'press a once and observe','reasoning':'no aligned target yet',
                          'target_ref':None,'success':{'type':'state_changed'},
                          'expires_steps':20,'max_no_effect_steps':8}, s)
        self.assertIsNone(p['target_ref'])

    def test_model_requests_bound_history_and_memory_text(self):
        from model_context import _compact_history, _compact_memory
        rows=[{'subgoal':'a','intent':'x'*500,'reasoning':'y'*5000,'status':'failed','reason':'r',
               'target_ref':None,'success':{'type':'state_changed'},'step':i} for i in range(30)]
        compact=_compact_history(rows,8)
        self.assertEqual(len(compact),8); self.assertNotIn('reasoning',compact[0])
        memory={'transitions':[{'a':i} for i in range(64)],'dialogues':[{'text':'z'*900} for _ in range(40)],
                'recent_actions':[{} for _ in range(12)],'notes':[{'text':'n'*900} for _ in range(32)]}
        m=_compact_memory(memory)
        self.assertLessEqual(len(m['transitions']),8); self.assertLessEqual(len(m['dialogues']),8)
        self.assertLessEqual(len(m['recent_actions']),6); self.assertLessEqual(len(m['notes']),8)
        self.assertTrue(all(len(d.get('text',''))<=200 for d in m['dialogues']))

    def test_planner_response_is_streamed_even_when_invalid(self):
        _,_,s=setup(); seen=[]
        body={'choices':[{'message':{'content':'{"subgoal":"x"}'},'finish_reason':'stop'}],'model':'test','usage':{}}
        opener=Mock(); opener.open.return_value=io.BytesIO(json.dumps(body).encode())
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'k'}), \
             patch('urllib.request.build_opener',return_value=opener), self.assertRaises(planning.PlannerError):
            planning.call_planner(s,'goal',on_event=seen.append)
        self.assertTrue(any(e['type']=='planner_response' for e in seen))
        self.assertTrue(any(e['type']=='planner_validation_error' for e in seen))

    def test_transition_state_holds_do_not_consume_the_budget(self):
        r=raw(); r['scene']={'mode':'unknown','verified':False}; r['battle']={'active':False,'verified':True}
        world=Mock(); world.game=SimpleNamespace(frame_count=0); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.return_value=r
        choose=Mock(return_value={'answer':{'choice':'a'}})
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture','DEEPSEEK_API_KEY':''}), \
             patch('run.Emulator',return_value=world), patch('run.Reader',return_value=reader), patch('run.choose',choose):
            report=run(Path('X.gb'),Path(tmp)/'run',goal='g',steps=1)
            rows=[json.loads(x) for x in (Path(tmp)/'run/events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['type']=='holding' for e in rows))
        # Frames advance during holds, but the one decision round still happens.
        self.assertEqual(report['executed_actions'],1)
        self.assertEqual(choose.call_count,1)

    def test_scene_change_triggers_a_scene_plan(self):
        from test_model_agency import raw, proposal
        ow=raw(); bt=raw(); bt['scene']={'mode':'battle','verified':True}
        bt['battle']={'active':True,'verified':True,'menu':'command','player':{'moves':[]}}
        seq=iter([ow, bt, bt, bt, bt, bt])
        world=Mock(); world.game=SimpleNamespace(frame_count=0); world.save.return_value=b'EXPLICIT OFFLINE STATE'
        reader=Mock(); reader.snapshot.side_effect=lambda: deepcopy(next(seq))
        calls=[]
        def planner(situation,goal,**_):
            calls.append(situation)
            return normalize_plan(proposal(target_ref=None, success={'type':'state_changed'}), situation)
        decision={'answer':{'choice':'a'},'source':'offline-test-double'}
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-jev','DEEPSEEK_API_KEY':'fixture-ds'}), \
             patch('run.Emulator',return_value=world), patch('run.Reader',return_value=reader), \
             patch('run.choose',Mock(return_value=decision)), patch('planning.call_planner',side_effect=planner):
            run(Path('OFFLINE.gb'),Path(tmp)/'run',goal='g',steps=2,planner_mode='deepseek')
            rows=[json.loads(x) for x in (Path(tmp)/'run/events.jsonl').read_text().splitlines()]
        # The scene change asks the brain for a scene plan, not a silent reuse.
        self.assertGreaterEqual(len(calls),2)
        self.assertTrue(any(r['type']=='scene_change' for r in rows))

    def test_missing_required_key_does_not_start_game(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture','DEEPSEEK_API_KEY':''}), patch('run.Emulator') as emulator:
            report=run(Path('OFFLINE.gb'),Path(tmp)/'run',goal='test',steps=1,planner_mode='deepseek')
        self.assertEqual(report['status'],'blocked_missing_planner_key'); emulator.assert_not_called()

    def test_plan_fit_response_must_be_complete(self):
        for value in (None, {}, {'type':'choice','choice':'applicable','probabilities':{'applicable':1}}):
            with self.assertRaises(ValueError): validate_plan_fit({'answers':{'plan_fit':value}})
        self.assertEqual(validate_plan_fit(response())['choice'],'applicable')

    def test_wrong_fit_or_provider_failure_never_executes_a_button(self):
        m,g,s=setup(); m.set_plan(normalize_plan(proposal(),s)); g['campaign']=m.context(g)
        payload=response(); del payload['answers']['plan_fit']
        with (patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture'}),patch('jev.time.sleep'),
              patch('urllib.request.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(payload).encode())) as network,
              self.assertRaises(ValueError)):
            choose(g,'goal',[])
        self.assertEqual(network.call_count,3)


if __name__=='__main__': unittest.main()
