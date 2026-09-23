"""Plan-local diagnostic regressions, using explicit synthetic observations.

No ROM, API key or model calls. Integration assertions use the actual observed
PlanManager and request builder, not the historical assisted controller.
"""
from copy import deepcopy
import unittest
from _paths import ROOT, POKEMON
from plan_review import repetition_evidence, target_failure_context


def plan(created=0, limit=8):
    return {'created_step': created, 'max_no_effect_steps': limit}


def state(x=0, mid=38, mode='overworld', identity=None):
    return {'position': [mid, x, 4], 'scene': {'mode': mode, 'verified': True},
            'dialog': {'open': False, 'text': ''}, 'battle': {'active': mode == 'battle'},
            'party': [], 'bag': [], 'observation_id': identity or f'obs:{mid}:{mode}:{x}'}


def effect(step, a=None, b=None):
    a, b = a or state(), b or state()
    return {'step': step, 'before': a, 'after': b,
            'changed_fields': [] if a == b else ['position'], 'ref': f'action:{step}'}


class RepetitionTests(unittest.TestCase):
    def test_no_effect_window_yields_diagnostic_not_action(self):
        evidence = repetition_evidence(plan(), [effect(i) for i in range(1, 9)], 8)
        self.assertEqual(evidence['symptom'], 'unchanged_observation')
        self.assertEqual(evidence['observed_actions'], 8)
        self.assertNotIn('next_button', evidence)
        self.assertIn('not_proof_target_unreachable', evidence['scope'])

    def test_plan_age_without_recorded_effects_is_not_failure(self):
        self.assertIsNone(repetition_evidence(plan(), [], 20))

    def test_previous_plan_effects_cannot_fill_new_plan_window(self):
        history = [effect(i) for i in range(1, 17)]
        self.assertIsNone(repetition_evidence(plan(created=12), history, 16))

    def test_gap_in_evidence_is_not_a_full_window(self):
        history = [effect(i) for i in (1, 2, 3, 4, 5, 6, 7, 9)]
        self.assertIsNone(repetition_evidence(plan(), history, 9))

    def test_eighty_action_budget_handles_sixty_four_retained_effects(self):
        history = [effect(i) for i in range(17, 81)]
        evidence = repetition_evidence(plan(limit=80), history, 80)
        self.assertEqual(evidence['observed_actions'], 64)
        self.assertEqual(evidence['window_start'], 17)

    def test_missing_changed_fields_does_not_prove_no_effect(self):
        history = [{'step': i} for i in range(1, 9)]
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_unchanged_legacy_effects_without_ids_are_supported(self):
        history = [{'step': i, 'changed_fields': []} for i in range(1, 9)]
        self.assertEqual(repetition_evidence(plan(), history, 8)['symptom'], 'unchanged_observation')

    def test_normal_backtracking_has_no_new_tile_requirement(self):
        history = [effect(i, state(x=20-i), state(x=19-i)) for i in range(1, 9)]
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_stationary_dialogue_progress_is_not_repetition(self):
        history = []
        for i in range(1, 9):
            a = state(mode='dialog', identity=f'obs:text:{i}')
            b = state(mode='dialog', identity=f'obs:text:{i+1}')
            a['dialog']['text'], b['dialog']['text'] = f'line {i}', f'line {i+1}'
            history.append(effect(i, a, b))
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_battle_progress_is_not_repetition_at_one_coordinate(self):
        history = [effect(i, state(mode='battle', identity=f'obs:hp:{i}'),
                          state(mode='battle', identity=f'obs:hp:{i+1}')) for i in range(1, 9)]
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_same_overworld_cycle_has_plan_local_evidence(self):
        history = [effect(i, state(x=i % 2), state(x=(i+1) % 2)) for i in range(1, 9)]
        self.assertEqual(repetition_evidence(plan(), history, 8)['symptom'], 'small_position_cycle')

    def test_cycle_with_new_object_evidence_is_not_old_context(self):
        history = [effect(i, state(x=i % 2), state(x=(i+1) % 2)) for i in range(1, 9)]
        history[-1]['after']['observation_id'] = 'obs:newly-visible-object'
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_scene_transition_and_resources_break_the_cycle(self):
        history = [effect(i, state(x=i % 2), state(x=(i+1) % 2)) for i in range(1, 9)]
        history[-1]['after']['party'] = [{'hp': 40, 'max_hp': 50}]
        self.assertIsNone(repetition_evidence(plan(), history, 8))

    def test_diagnostics_are_pure(self):
        history = [effect(i) for i in range(1, 9)]
        saved = deepcopy(history)
        result = repetition_evidence(plan(), history, 8)
        result['recent_actions'][0]['before']['position'][0] = 99
        self.assertEqual(saved, history)


class FailureContextTests(unittest.TestCase):
    def history(self, **changes):
        return [{'target_ref': 'object:38:1', 'status': 'failed', 'reason': 'unchanged',
                 'step': 8, 'failure_observation_id': 'obs:old', **changes}]

    def test_same_observation_suppresses_exact_failed_retry(self):
        refs, attempts = target_failure_context(self.history(), {'observation_id': 'obs:old'})
        self.assertEqual(refs, ['object:38:1'])
        self.assertTrue(attempts[0]['same_observation'])

    def test_new_evidence_allows_reconsidering_but_preserves_failure(self):
        refs, attempts = target_failure_context(self.history(), {'observation_id': 'obs:new'})
        self.assertEqual(refs, [])
        self.assertEqual(attempts[0]['reason'], 'unchanged')
        self.assertFalse(attempts[0]['same_observation'])

    def test_legacy_failure_without_context_cannot_ban_target_forever(self):
        history = self.history(); del history[0]['failure_observation_id']
        refs, attempts = target_failure_context(history, {'observation_id': 'obs:old'})
        self.assertEqual(refs, []); self.assertEqual(len(attempts), 1)

    def test_single_non_completion_is_recorded_but_not_withheld(self):
        for status in ('expired', 'invalidated'):
            refs, attempts = target_failure_context(self.history(status=status), {'observation_id': 'obs:old'})
            self.assertEqual(refs, [])
            self.assertEqual(attempts[0]['status'], status)

    def test_repeated_non_completion_withholds_the_target(self):
        history = [self.history(status='invalidated', step=8 + i, plan_id=f'p{i}')[0] for i in range(3)]
        refs, attempts = target_failure_context(history, {'observation_id': 'obs:new'})
        self.assertEqual(refs, ['object:38:1'])
        self.assertEqual(len(attempts), 3)


class ManagerIntegrationTests(unittest.TestCase):
    def setup_manager(self):
        from test_model_agency import setup, proposal
        from plan_contract import normalize_plan
        manager, game, situation = setup()
        manager.set_plan(normalize_plan(proposal(), situation))
        return manager, game

    def test_progress_counters_alone_do_not_fail_a_plan(self):
        from test_model_agency import raw
        manager, game = self.setup_manager()
        manager.steps = 8
        r = raw(); r['progress'].update(loop_detected=True, same_position_steps=100, steps_since_new_tile=100)
        manager.context(r)
        self.assertIsNotNone(manager.plan)

    def test_real_record_pipeline_allows_long_stationary_dialogue(self):
        from test_model_agency import raw
        manager, _ = self.setup_manager()
        previous = raw()
        for i in range(1, 10):
            current = raw(); current['scene']['mode'] = 'dialog'
            current['dialog'].update(open=True, text=f'Observed line {i}')
            current['progress'].update(total_steps=i, same_position_steps=i, steps_since_new_tile=i)
            manager.record('a', previous, current); manager.context(current)
            self.assertIsNotNone(manager.plan)
            previous = current

    def test_real_record_pipeline_detects_no_effect(self):
        from test_model_agency import raw
        manager, _ = self.setup_manager()
        for i in range(8):
            manager.record('wait', raw(), raw())
            manager.context(raw())
        self.assertIsNone(manager.plan)
        self.assertEqual(manager.plan_history[-1]['evidence']['symptom'], 'unchanged_observation')

    def test_changed_object_releases_target_without_erasing_history(self):
        from test_model_agency import raw
        manager, game = self.setup_manager()
        manager.finish_plan('failed', 'observed_repetition_requires_model_review', game)
        old = manager.context(raw())
        self.assertNotIn('cell:38:5,4', old['targets'])
        changed = raw(); changed['world']['objects'][0]['x'] = 3
        new = manager.context(changed)
        self.assertIn('cell:38:5,4', new['targets'])
        self.assertEqual(new['target_failures'][0]['reason'], 'observed_repetition_requires_model_review')

    def test_both_models_receive_qualified_failure_evidence(self):
        from test_model_agency import raw
        from model_context import build_situation, build_request
        manager, game = self.setup_manager()
        manager.finish_plan('failed', 'observed_repetition_requires_model_review', game)
        game['campaign'] = manager.context(raw())
        situation = build_situation(game, game['campaign'], game['progress'])
        request = build_request(game, 'Explore', [])
        self.assertTrue(situation['target_failures'][0]['same_observation'])
        self.assertEqual(request['state']['campaign']['target_failures'], situation['target_failures'])

    def test_checkpoint_preserves_context_qualified_failures(self):
        from test_model_agency import raw
        from plan_manager import PlanManager
        manager, game = self.setup_manager()
        manager.finish_plan('failed', 'observed_repetition_requires_model_review', game)
        restored = PlanManager(manager.snapshot())
        self.assertIn('cell:38:5,4', restored.context(raw())['failed_target_refs'])
        changed = raw(x=3)
        self.assertNotIn('cell:38:5,4', restored.context(changed)['failed_target_refs'])


if __name__ == '__main__':
    unittest.main()
