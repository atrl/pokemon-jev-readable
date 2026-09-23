"""Bounded real dual-model smoke run, never a fixture or a completion claim.

The initial bedroom checkpoint comes from the separately labelled emulator
regression. All actions after that checkpoint are real JEV decisions under
real accepted DeepSeek plans. No secret, ROM or binary save is published.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from run import run
from render_results import export_results
from artifacts import write_json


def verify(rom: Path, state: Path, output: Path, steps: int = 80):
    game_dir = output / 'jev'
    caught = None
    try:
        report = run(rom, game_dir,
                     goal='Leave the current room through an observed exit, then continue toward the adventure. Inspect dialogue before detouring. Do not invent routes.',
                     steps=steps, state_file=state, planner_mode='deepseek',
                     planner_call_budget=4, max_seconds=240, screenshots=True)
    except Exception as exc:
        caught = type(exc).__name__
        report = json.loads((game_dir/'report.json').read_text()) if (game_dir/'report.json').exists() else {'status': 'failed'}
    log = game_dir / 'events.jsonl'
    events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    plans = [event for event in events if event.get('type') == 'plan']
    requests = [event for event in events if event.get('type') == 'jev_request']
    accepted_ids = {(event.get('plan') or {}).get('plan_id') for event in plans}
    linked = [event for event in requests if ((event.get('request') or {}).get('state', {}).get('campaign', {}).get('plan') or {}).get('plan_id') in accepted_ids]
    results = [event for event in events if event.get('type') == 'result' and event.get('success') is True]
    positions = [(event.get('observation') or {}).get('player') for event in results]
    positions = [p for p in positions if p]
    outcomes = [event for event in events if event.get('type') == 'plan_outcome']
    both_ran = bool(plans and linked and report.get('executed_actions', 0))
    evidence = {
        'kind': 'real_emulator_and_authenticated_models' if both_ran else 'blocked_or_failed_live_attempt',
        'initial_state': 'explicit_scripted_regression_bedroom_checkpoint_not_model_progress',
        'status': report.get('status'), 'error': caught or report.get('reason'),
        'deepseek_requests': report.get('planning_calls', 0), 'accepted_plans': len(plans),
        'jev_calls': report.get('jev_calls', 0), 'actions': report.get('executed_actions', 0),
        'actual_planner_model': report.get('planner_model'),
        'requests_linked_to_real_plan': len(linked),
        'position_changes': report.get('movement_actions', 0),
        'map_changes': report.get('map_changes', 0),
        'last_position': positions[-1] if positions else None,
        'plan_outcomes': [{k: e.get(k) for k in ('plan_id', 'status', 'reason', 'evidence')} for e in outcomes],
        'dual_model_integration_observed': both_ran,
        'mt_moon_tested': False, 'game_completed': report.get('game_completed', False),
        'limits': {'actions': steps, 'planner_requests': 4, 'wall_seconds': 240},
    }
    export_results(output, output)
    write_json(output/'dual-model-verification.json', evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, default=Path('red-star-2020-08-18.gb'))
    parser.add_argument('--state', type=Path, default=Path('pokemon/.work/bedroom.state'))
    parser.add_argument('--output', type=Path, default=Path('pokemon-evidence/dual'))
    parser.add_argument('--steps', type=int, default=80)
    args = parser.parse_args()
    if not 1 <= args.steps <= 80:
        parser.error('smoke steps must be 1..80')
    result = verify(args.rom, args.state, args.output, args.steps)
    if result['status'] == 'failed':
        raise SystemExit(1)
