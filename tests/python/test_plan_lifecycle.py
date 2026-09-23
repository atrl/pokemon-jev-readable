"""Regression tests for the two-model boundary. No API key or ROM required."""
import io
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import urllib.error

from _paths import ROOT, POKEMON
import planning
from campaign import CampaignPlanner
from plan_contract import baseline, normalize_plan, plan_outcome, ball_count
from prompt import build_request
from run import run
from jev import redact_secrets


def obs(mid=38, x=3, y=6):
    return {'game': 'OFFLINE TEST DOUBLE', 'errors': [], 'frame': 100,
            'player': {'name': 'TEST', 'map_id': mid, 'x': x, 'y': y},
            'scene': {'mode': 'overworld', 'verified': True},
            'dialog': {'open': False, 'text': '', 'quality': 'verified'},
            'screen_text': {'rows': []}, 'party': [], 'party_state': {'ready': True, 'verified': True},
            'bag': [], 'battle': {'active': False, 'verified': True},
            'milestones': {'party_count': {'verified': True, 'value': 0},
                           'starter_received': {'verified': False, 'value': None}},
            'world': {'map_id': mid, 'name': 'TEST ROOM', 'width': 8, 'height': 8,
                      'source_match': True, 'player_position_valid': True, 'input_lock': {},
                      'warps': [{'warp_id': 0, 'x': 7, 'y': 1, 'destination_map_id': 37,
                                 'quality': 'verified', 'destination_name': 'REDS_HOUSE_1F'}],
                      'objects': [{'object_id': 1, 'x': 4, 'y': 5, 'sprite': 'TEST_SPRITE',
                                   'active': True, 'text_id': 1, 'quality': 'verified'}], 'connections': []},
            'progress': {'total_steps': 10, 'visited_tiles': 3, 'loop_detected': False,
                         'same_position_steps': 0, 'steps_since_new_tile': 0}}


def situation(raw=None):
    raw = raw or obs()
    ctx = {'model_planning_enabled': True, 'active_objective': {'id': 'awaiting_model_plan'},
           'visited_map_ids': [38, 37], 'route_map_ids': [38, 37], 'objective_history': [],
           'navigation': {'status': 'needs_target'}, 'plan_history': []}
    return planning.build_situation(raw, ctx, raw['progress'])


def payload(**changes):
    data = {'subgoal': 'leave_room', 'intent': 'Use the observed exit',
            'target_ref': 'warp:38:0', 'success': {'type': 'target_reached'},
            'resource_policy': {'wild_battle': 'run', 'heal_hp_ratio': 0.5},
            'expires_steps': 160, 'max_no_effect_steps': 16}
    data.update(changes)
    return data


def plan(**changes):
    result = normalize_plan(payload(**changes), situation())
    result['created_step'] = 0
    return result


class ContractTests(unittest.TestCase):
    def test_bootstrap_plans_without_waiting_for_a_stall(self):
        self.assertTrue(planning.needs_planning(situation()))

    def test_success_condition_survives_normalization(self):
        self.assertEqual(plan()['success'], {'type': 'target_reached'})

    def test_rejects_unknown_target_ref(self):
        with self.assertRaises(ValueError):
            plan(target_ref='invented:9999')

    def test_production_rejects_raw_coordinates_and_code(self):
        for extra in ({'target_map_id': -99}, {'selectors': [{'x': 'broken'}]}, {'button': 'a'}, {'exec': 'print(1)'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                plan(**extra)

    def test_rejects_invalid_resource_fields(self):
        for resource in ({'wild_battle': 'dance'}, {'heal_hp_ratio': float('nan')},
                         {'heal_hp_ratio': 99}, {'max_party_size': True}, {'catch_species': 'INVENTED'}):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                plan(resource_policy=resource)

    def test_rejects_malformed_success(self):
        for success in ({}, {'type': 'eval', 'code': 'x'}, {'type': 'fact_true', 'fact': 'not_registered'},
                        {'type': 'fact_true', 'fact': []}, {'type': 'new_tile', 'value': True}):
            with self.subTest(success=success), self.assertRaises(ValueError):
                plan(success=success)

    def test_rejects_trivial_current_map_goal(self):
        with self.assertRaises(ValueError):
            plan(target_ref='map:38')

    def test_explicit_failure_interrupts_before_ttl(self):
        raw = obs()
        raw['progress'].update(loop_detected=True, same_position_steps=50)
        result = plan_outcome(plan(), raw, raw['progress'], 17)
        self.assertEqual(result['status'], 'failed')

    def test_target_success_uses_actual_map_not_text(self):
        raw = obs(); raw['screen_text']['rows'] = ['We reached the exit!']
        self.assertIsNone(plan_outcome(plan(), raw, raw['progress'], 1))
        raw = obs(mid=37)
        self.assertEqual(plan_outcome(plan(), raw, raw['progress'], 2)['status'], 'completed')

    def test_bad_alignment_cannot_certify_map_entry(self):
        raw = obs(mid=37); raw['world']['source_match'] = False
        self.assertIsNone(plan_outcome(plan(), raw, raw['progress'], 1))

    def test_unknown_fact_cannot_complete(self):
        p = plan(target_ref=None, success={'type': 'fact_true', 'fact': 'starter_received'})
        raw = obs(); raw['milestones']['starter_received'] = {'value': True, 'verified': False}
        self.assertIsNone(plan_outcome(p, raw, raw['progress'], 1))
        raw['milestones']['starter_received']['verified'] = True
        self.assertEqual(plan_outcome(p, raw, raw['progress'], 2)['status'], 'completed')

    def test_dialog_must_have_opened(self):
        p = plan(target_ref='object:38:1', success={'type': 'dialog_closed'})
        raw = obs()
        self.assertIsNone(plan_outcome(p, raw, raw['progress'], 1))
        raw['dialog']['open'] = True
        plan_outcome(p, raw, raw['progress'], 2)
        raw['dialog']['open'] = False
        self.assertEqual(plan_outcome(p, raw, raw['progress'], 3)['status'], 'completed')

    def test_disappearing_target_invalidates_not_completes(self):
        p = plan(target_ref='object:38:1', success={'type': 'dialog_closed'})
        raw = obs(); raw['world']['objects'][0]['active'] = False
        self.assertEqual(plan_outcome(p, raw, raw['progress'], 2)['status'], 'invalidated')

    def test_expiry_is_not_success(self):
        raw = obs()
        self.assertEqual(plan_outcome(plan(), raw, raw['progress'], 160)['status'], 'expired')

    def test_old_failed_plan_does_not_block_replan(self):
        s = situation(); s.update(plan_active=True, plan_invalid=True, steps_since_plan=1)
        self.assertTrue(planning.needs_planning(s))

    def test_network_failure_cools_down(self):
        s = situation(); s.update(last_request_failed=True, steps_since_plan=1)
        self.assertFalse(planning.needs_planning(s))

    def test_ancient_healing_does_not_remain_recent(self):
        entries = [{'step': 1, 'to': 'heal_party'}, {'step': 100, 'to': 'heal_party'}]
        self.assertEqual(planning._recent_heal_count(entries, 300, current_step=2000), 0)

    def test_situation_includes_missing_decision_evidence(self):
        s = situation()
        for name in ('dialog', 'scene', 'world', 'navigation', 'milestones', 'targets', 'failed_plans'):
            self.assertIn(name, s)
        self.assertIn('warp:38:0', s['targets'])
        self.assertIn('object:38:1', s['targets'])

    def test_unverified_party_and_bag_stay_unknown(self):
        raw = obs(); raw['party'] = [{'nickname': 'unverified'}]; raw['bag'] = [{'quantity': 20}]
        s = situation(raw)
        self.assertIsNone(s['party']); self.assertIsNone(s['bag']); self.assertIsNone(s['pokeballs'])

    def test_verified_ball_count_has_no_name_substring_guess(self):
        self.assertEqual(ball_count([{'item_id': 99, 'quantity': 2, 'name_verified': True, 'name': 'BALL puzzle'}]), 0)
        self.assertIsNone(ball_count([None]))

    def test_redacts_both_credentials(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'SECRET-DS', 'TYPESAFE_API_KEY': 'SECRET-JEV'}):
            value = redact_secrets({'text': 'SECRET-DS and SECRET-JEV', 'DEEPSEEK_API_KEY': 'other'})
        self.assertNotIn('SECRET-', json.dumps(value)); self.assertEqual(value['DEEPSEEK_API_KEY'], '[REDACTED]')


class ManagerTests(unittest.TestCase):
    def test_completed_plan_clears_and_retains_evidence(self):
        c = CampaignPlanner(); c.model_planning_enabled = True; c.set_plan(plan())
        ctx = c.context(obs(mid=37))
        self.assertIsNone(c.plan)
        self.assertEqual(ctx['plan_history'][-1]['status'], 'completed')
        self.assertEqual(ctx['active_objective']['id'], 'awaiting_model_plan')

    def test_legacy_checkpoint_is_invalidated(self):
        c = CampaignPlanner({'plan': {'subgoal': 'old', 'intent': 'old'}}); c.model_planning_enabled = True
        c.context(obs())
        self.assertIsNone(c.plan)
        self.assertEqual(c.plan_history[-1]['reason'], 'legacy_plan_requires_revalidation')

    def test_emergency_suspends_one_plan_and_pauses_ttl(self):
        c = CampaignPlanner(); c.model_planning_enabled = True; c.set_plan(plan())
        c.steps = 5; c._refresh_model_plan(obs(), True)
        self.assertEqual(c.plan['status'], 'suspended')
        c.steps = 500; c._refresh_model_plan(obs(), False)
        self.assertEqual(c.plan['status'], 'active')
        self.assertEqual(c.plan['created_step'], 495)

    def test_suspended_intent_does_not_override_emergency_focus(self):
        raw = obs(); p = plan(); p.update(status='suspended', intent='CONTINUE_TO_CAVE')
        raw['campaign'] = {'active_objective': {'id': 'heal_party', 'intent': 'HEAL_NOW'},
                           'plan': p, 'plan_suspended': True}
        focus = build_request(raw, 'test', [])['state']['current_focus']
        self.assertIn('HEAL_NOW', focus); self.assertNotIn('CONTINUE_TO_CAVE', focus)

    def test_recovery_keeps_known_terrain(self):
        c = CampaignPlanner(); c.tiles = {'38': {'1,1': {'passable': True}}}
        c.recover(obs(), attempt=1, reason='test', failed_button='up')
        self.assertIn('1,1', c.tiles['38'])

    def test_plan_history_survives_checkpoint(self):
        c = CampaignPlanner(); c.set_plan(plan()); c.finish_plan('failed', 'test')
        restored = CampaignPlanner(c.snapshot())
        self.assertEqual(restored.plan_history[-1]['reason'], 'test')


class HttpTests(unittest.TestCase):
    def test_actual_request_schema_and_response_binding(self):
        body = {'choices': [{'message': {'content': json.dumps(payload())}, 'finish_reason': 'stop'}],
                'model': 'test-provider', 'usage': {'total_tokens': 99}}
        opener = Mock(); opener.open.return_value = io.BytesIO(json.dumps(body).encode())
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture-only', 'DEEPSEEK_MODEL': 'deepseek-flash'}), patch('urllib.request.build_opener', return_value=opener):
            p = planning.call_planner(situation(), 'leave room')
        request = opener.open.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent['response_format'], {'type': 'json_object'})
        self.assertEqual(sent['thinking'], {'type': 'disabled'})
        self.assertEqual(p['schema_version'], 2); self.assertEqual(p['model'], 'test-provider')

    def test_truncation_is_not_a_plan(self):
        opener = Mock(); opener.open.return_value = io.BytesIO(json.dumps({'choices': [{'finish_reason': 'length'}]}).encode())
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture-only'}), patch('urllib.request.build_opener', return_value=opener), self.assertRaises(planning.PlannerError) as caught:
            planning.call_planner(situation(), 'test')
        self.assertEqual(caught.exception.code, 'truncated_response')

    def test_http_error_does_not_expose_echoed_key(self):
        opener = Mock(); opener.open.side_effect = urllib.error.HTTPError('https://api.deepseek.com', 401, 'fixture-secret', {}, io.BytesIO(b'fixture-secret'))
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture-secret'}), patch('urllib.request.build_opener', return_value=opener), self.assertRaises(planning.PlannerError) as caught:
            planning.call_planner(situation(), 'test')
        self.assertEqual(caught.exception.http_status, 401)
        self.assertNotIn('fixture-secret', str(caught.exception))


class RuntimeTests(unittest.TestCase):
    def runtime(self, folder, planner, observations=None, **kwargs):
        world = Mock(); world.game = SimpleNamespace(frame_count=0); world.save.return_value = b'fake-state'
        reader = Mock(); reader.snapshot.side_effect = observations if observations else None
        reader.snapshot.return_value = obs()
        choose = Mock(return_value={'answer': {'choice': 'right'}, 'source': 'offline-test-double'})
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-jev', 'DEEPSEEK_API_KEY': 'fixture-ds'}), \
             patch('run.Emulator', return_value=world), patch('run.Reader', return_value=reader), \
             patch('run.choose', choose), patch('planning.call_planner', planner):
            report = run(Path('NO-ROM.gb'), folder, goal='test', steps=2, **kwargs)
        return report, world, choose

    def test_bootstrap_call_reaches_jev_request_without_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            planner = Mock(return_value=plan())
            report, world, choose = self.runtime(Path(d)/'run', planner)
            self.assertEqual(report['plans'], 1); self.assertEqual(world.press.call_count, 2)
            self.assertEqual(choose.call_args.args[0]['campaign']['plan']['subgoal'], 'leave_room')

    def test_planner_failure_pauses_without_a_button(self):
        with tempfile.TemporaryDirectory() as d:
            report, world, choose = self.runtime(Path(d)/'run', Mock(side_effect=planning.PlannerError('http_error', 401)))
            self.assertEqual(report['status'], 'planner_unavailable')
            self.assertEqual(report['plans'], 0); world.press.assert_not_called(); choose.assert_not_called()
            self.assertTrue((Path(d)/'run'/'last.state').exists())

    def test_missing_required_key_never_starts_the_emulator(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture', 'DEEPSEEK_API_KEY': ''}), patch('run.Emulator') as emulator:
            report = run(Path('NO-ROM.gb'), Path(d)/'run', goal='test', steps=1, planner_mode='deepseek')
            self.assertEqual(report['status'], 'blocked_missing_planner_key'); emulator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
