"""Model plan lifecycle + empirical memory, with no story/team/battle policy.

The compatibility name `campaign` remains in events/checkpoints. This manager
never imports CampaignPlanner, source maps, a damage ranker or healing logic.
"""
from __future__ import annotations
from copy import deepcopy
from experience import Experience
from perception import POLICY, project, point, take
from plan_contract import success_evidence
from plan_review import repetition_evidence, target_failure_context


class PlanManager:
    def __init__(self, snapshot=None):
        source = snapshot or {}
        valid = source.get('manager') == 'observed_v1'
        data = source if valid else {}
        self.memory = Experience(data.get('experience'))
        if not valid and source:
            self.memory.import_legacy_observations(source)
        self.steps = self.memory.steps
        self.plan = deepcopy(data.get('plan'))
        self.plan_history = deepcopy(data.get('plan_history', []))[-32:]
        self.planning_state = deepcopy(data.get('planning_state', {'last_step': None, 'last_request_failed': False}))
        self.resume_target = deepcopy(data.get('resume_target'))
        self.model_planning_enabled = False
        self.recovery = None
        self.completion_evidence = None  # Independent evaluator, not model input/knowledge.
        self.migration = 'observed_memory_restored' if valid else 'legacy_observations_only; prior_plans_discarded' if source else 'new_observed_memory'

    def evaluate(self, raw):
        """Terminal-only evaluator. Never turns hidden event flags into a suggested task."""
        fact = (raw.get('milestones') or {}).get('game_completed') or {}
        if fact.get('verified') is True and fact.get('value') is True:
            self.completion_evidence = deepcopy(fact)

    def set_plan(self, plan):
        if plan.get('schema_version') != 3 or plan.get('observation_policy') != POLICY:
            raise ValueError('Observed mode requires a validated observed-plan contract')
        self.plan = deepcopy(plan)
        self.plan['created_step'] = self.steps
        self.memory.add_notes(self.plan.get('memory_updates', []), self.plan['plan_id'])

    def finish_plan(self, status, reason, observation=None, evidence=None):
        if not self.plan:
            return
        if reason == 'scene_changed_requires_replan' and self.plan.get('target_ref'):
            # Remember the interrupted destination so the next plan can resume it.
            self.resume_target = {
                'target_ref': self.plan.get('target_ref'), 'subgoal': self.plan.get('subgoal'),
                'scene': (self.plan.get('baseline') or {}).get('scene'), 'reason': reason,
            }
        game = project(observation) if observation is not None else {}
        self.plan_history.append({**take(self.plan, ('plan_id', 'subgoal', 'intent', 'reasoning', 'target_ref',
                                                    'target', 'success', 'policy', 'resource_policy')),
                                  'status': status, 'reason': reason, 'step': self.steps,
                                  'position': point(game) if game else None,
                                  'observation_id': game.get('observation_id') if game else None,
                                  'failure_observation_id': game.get('observation_id') if status == 'failed' else None,
                                  'evidence': deepcopy(evidence)})
        self.plan_history = self.plan_history[-32:]
        self.plan = None

    def record(self, button, before, after):
        effect = self.memory.record(button, before, after)
        self.steps = self.memory.steps
        return effect

    def recover(self, observation, *, attempt, reason, failed_button=None):
        # Failure evidence is retained; no clearing map and no fallback direction.
        self.recovery = {'attempt': attempt, 'reason': reason, 'failed_button': failed_button,
                         'position': point(observation), 'source': 'execution_observation'}
        self.finish_plan('failed', reason, observation, self.recovery)

    def _refresh(self, game, progress):
        plan = self.plan
        if not plan:
            return
        if plan.get('schema_version') != 3 or plan.get('observation_policy') != POLICY:
            self.finish_plan('invalidated', 'incompatible_observation_contract', game)
            return
        age = self.steps - plan['created_step']
        kind = plan['success']['type']
        base = plan['baseline']
        mode = game['scene']['mode']
        baseline_scene = base.get('scene')
        # A scene the plan was not written for (battle/dialog/menu) suspends a
        # movement plan instead of discarding it; it resumes when that scene ends.
        if plan.get('status') == 'suspended':
            if mode == baseline_scene:
                plan['status'] = 'active'
                plan.pop('suspended_reason', None)
            else:
                return
        elif baseline_scene == 'overworld' and mode in (
                'battle', 'dialog', 'main_menu', 'name_entry', 'species_preview'):
            plan['status'] = 'suspended'
            plan['suspended_reason'] = 'scene_changed_to_' + mode
            return
        evidence = None
        # A zero-action existing condition is never progress caused by this plan.
        if age > 0:
            if kind == 'map_changed' and point(game) and base.get('position') and point(game)[0] != base['position'][0]:
                evidence = {'before': base['position'], 'after': point(game), 'scope': 'map_transition_not_story_completion'}
            elif kind == 'scene_changed' and game['scene']['verified'] and mode != baseline_scene:
                evidence = {'scene': game['scene'], 'scope': 'ui_transition_only'}
            elif kind == 'state_changed' and game['observation_id'] != base.get('observation_id'):
                evidence = {'observation_id': game['observation_id'], 'scope': 'observable_change_only'}
            elif kind not in ('map_changed', 'scene_changed', 'state_changed'):
                evidence = success_evidence(plan, game, progress)
        if evidence:
            self.finish_plan('completed', 'predicate_verified', game, evidence)
        elif age >= plan['expires_steps']:
            self.finish_plan('expired', 'action_budget_reached', game)
        else:
            # No-new-tile/stationary counters alone cannot prove a failed plan:
            # dialogue, battles and valid backtracking can advance without them.
            repetition = repetition_evidence(plan, self.memory.effects, self.steps)
            if repetition is not None:
                self.finish_plan('failed', 'observed_repetition_requires_model_review', game, repetition)
        if self.plan:
            # Only the planner's explicit interrupt contract may use risk thresholds.
            for rule in plan.get('replan_when', []):
                matched = False
                if rule['type'] == 'scene_changed':
                    matched = game['scene']['verified'] and mode != baseline_scene
                elif rule['type'] == 'map_changed':
                    matched = bool(point(game) and base.get('position') and point(game)[0] != base['position'][0])
                elif rule['type'] == 'party_hp_below' and mode == 'overworld':
                    party = game.get('party')
                    if party and all(type(m.get('hp')) is int and type(m.get('max_hp')) is int and m['max_hp'] > 0 for m in party):
                        matched = sum(m['hp'] for m in party) / sum(m['max_hp'] for m in party) < rule['ratio']
                if matched:
                    if rule['type'] == 'scene_changed':
                        # Keep exploration progress for when the scene returns.
                        plan['status'] = 'suspended'
                        plan['suspended_reason'] = 'scene_changed'
                        return
                    self.finish_plan('invalidated', 'planner_interrupt_condition', game, rule)
                    break

    def context(self, observation):
        game = project(observation)
        self.memory.observe(game)
        if self.plan and (self.plan.get('target') or {}).get('kind') == 'object':
            current = self.memory.catalog(game).get(self.plan.get('target_ref'))
            if current and current.get('currently_visible'):
                # Resolve the model-selected entity, do not select a different objective.
                self.plan['target'] = deepcopy(current)
        self._refresh(game, observation.get('progress') or {})
        plan = self.plan
        objective = {'id': 'plan:' + plan['subgoal'] if plan else 'awaiting_model_plan',
                     'intent': plan['intent'] if plan else None, 'completion': False, 'completed_ids': [],
                     'target_map_id': plan.get('target_map_id') if plan else None,
                     'source': {'quality': 'model_authored' if plan else 'no_plan'}}
        if self.completion_evidence:
            objective = {'id': 'main_story_complete', 'intent': None, 'completion': True,
                         'completion_evidence': self.completion_evidence, 'completed_ids': []}
        failed_target_refs, target_failures = target_failure_context(self.plan_history, game)
        # Annotate rather than hide: a target the model wants but cannot see would
        # make it reference an absent ref and fail validation. The model decides.
        attempts = {}
        for row in target_failures:
            ref = row.get('target_ref')
            if isinstance(ref, str) and ref:
                attempts[ref] = attempts.get(ref, 0) + 1
        targets = self.memory.catalog(game)
        for ref, entry in targets.items():
            if ref in attempts:
                entry['previous_attempts'] = attempts[ref]
                entry['prefer_alternative'] = True
        frontier = set(self.memory.frontier(game))
        for ref in frontier:
            if ref in targets:
                targets[ref]['frontier'] = True
                continue
            # Frontier cells may sit outside the current viewport, so add them as
            # valid observed targets instead of letting the model invent one.
            parts = ref.split(':')
            if len(parts) != 3 or parts[0] != 'cell' or ',' not in parts[2]:
                continue
            x_text, y_text = parts[2].split(',', 1)
            if not x_text.lstrip('-').isdigit() or not y_text.lstrip('-').isdigit():
                continue
            targets[ref] = {
                'map_id': int(parts[1]), 'kind': 'coordinate', 'label': 'observed floor frontier',
                'selector': {'kind': 'coordinate', 'x': int(x_text), 'y': int(y_text)},
                'quality': 'observed_background_only', 'frontier': True,
                'evidence_ref': f'memory:cell:{parts[1]}:{parts[2]}',
            }
        # General anti-backtrack: do not offer a portal back to the map just left,
        # so the agent cannot ping-pong across one connection. Released as the
        # recent transition ages out.
        blocked_backtrack = []
        current_map = point(game)
        recent_from = None
        if self.memory.transitions:
            last = self.memory.transitions[-1]
            if self.steps - last.get('step', -(10 ** 9)) <= 12:
                recent_from = last.get('from_map')
        if recent_from is not None and current_map and recent_from != current_map[0]:
            for ref, entry in list(targets.items()):
                destination = entry.get('destination_map_id')
                if (entry.get('kind') in ('warp', 'connection') and destination == recent_from) or ref == f'map:{recent_from}':
                    del targets[ref]
                    blocked_backtrack.append(ref)
        return {'knowledge_mode': 'observed', 'active_objective': objective,
                'navigation': self.memory.path_to(game, (plan or {}).get('target')),
                'plan': deepcopy(plan), 'plan_history': deepcopy(self.plan_history[-12:]),
                'memory': self.memory.context(game), 'targets': targets,
                'frontier': sorted(frontier),
                'failed_target_refs': failed_target_refs, 'target_failures': target_failures,
                'resume_target': deepcopy(self.resume_target),
                'blocked_backtrack': blocked_backtrack,
                'recovery': deepcopy(self.recovery), 'model_planning_enabled': self.model_planning_enabled,
                'plan_suspended': False, 'visited_map_ids': [int(k) for k in self.memory.maps],
                'recorded_action_count': self.steps, 'migration': self.migration,
                'roles': {'planner': 'System Two', 'physical_input': 'System One',
                          'memory': 'observations_and_model_hypotheses_separate',
                          'runtime': 'contracts_geometry_execution_only'}}

    def snapshot(self):
        return {'manager': 'observed_v1', 'experience': self.memory.snapshot(),
                'plan': deepcopy(self.plan), 'plan_history': deepcopy(self.plan_history),
                'planning_state': deepcopy(self.planning_state),
                'resume_target': deepcopy(self.resume_target)}
