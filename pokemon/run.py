#!/usr/bin/env python3
"""ROM -> memory snapshot -> Jev -> one physical button -> a new snapshot.

No scripted introduction is used here. Start cold or explicitly load a local
state made by a human/test fixture. A decision budget is not a win condition.
The live progress stream contains structured observations, never screenshots.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import signal
import time

from emulator import Emulator
from memory import Reader, load_profile
from jev import choose, observation_for_model, redact_secrets
from progress import ProgressTracker


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(redact_secrets(data),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    temp.replace(path)



def restore_progress(state_file: Path, manifest: dict) -> tuple[ProgressTracker, str]:
    """Restore only memory bound to this state hash, or replay matching legacy effects."""
    sidecar = state_file.parent/'last.progress.json'
    if sidecar.exists():
        data = json.loads(sidecar.read_text())
        if (data.get('rom_sha1') == manifest['rom_sha1'] and
                data.get('state_sha256') == manifest['state_sha256'] and
                data.get('step') == manifest.get('step')):
            return ProgressTracker(data['tracker']), 'verified_checkpoint'
        # A mismatched sidecar must not attach observations from another save.
    tracker = ProgressTracker()
    log = state_file.parent/'events.jsonl'
    saved_step = manifest.get('step')
    replayed = 0
    if state_file.name == 'last.state' and type(saved_step) is int and log.exists():
        with log.open() as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if row.get('type') != 'result' or row.get('success') is not True or not 1 <= row.get('step', 0) <= saved_step:
                        continue
                    effect = row.get('result')
                    if not isinstance(effect,dict) or not isinstance(effect.get('before'),dict) or not isinstance(effect.get('after'),dict):
                        continue
                    tracker.record(row.get('button',effect.get('button')),effect['before'],effect['after'])
                    replayed += 1
                except (ValueError,TypeError):
                    continue
    return tracker, f'legacy_observed_effects:{replayed}' if replayed else 'new_memory'

def run(rom: Path, output: Path, *, goal: str, steps: int, state_file: Path | None = None,
        visible: bool = False, allow_missing_key: bool = False, screenshots: bool = False,
        video: bool = False, checkpoint_every: int = 50, max_stalled_steps: int = 80) -> dict:
    if type(steps) is not int or not 1 <= steps <= 100_000:
        raise ValueError("steps must be an integer in 1..100000")
    if type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 1000:
        raise ValueError("checkpoint_every must be an integer in 1..1000")
    if type(max_stalled_steps) is not int or not 12 <= max_stalled_steps <= 10000:
        raise ValueError("max_stalled_steps must be an integer in 12..10000")
    if not goal.strip():
        raise ValueError("goal cannot be empty")
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose a new empty output directory; no previous run will be overwritten")
    started = time.monotonic()
    world = None
    reader = None
    first_frame = None
    video_restarts = 0
    tracker = ProgressTracker()
    stalled_steps = 0
    report = {"started_at":datetime.now(timezone.utc).isoformat(),"model_ms":0,"video_enabled":video,"jev_calls":0,"jev_http_attempts":0,"executed_actions":0,"goal":goal,"status":"starting",
              "game_completed":False,"policy":"jev_only","new_tiles_this_run":0,"movement_actions":0,"map_changes":0,"progress_memory_source":"new_memory","resume_from_user_state":state_file is not None}

    def elapsed_ms() -> int:
        return round((time.monotonic() - started) * 1000)

    def game_frames() -> int | None:
        frame = getattr(getattr(world,"game",None),"frame_count",None)
        return max(0,frame - first_frame) if type(frame) is int and type(first_frame) is int else None

    def emit(event_type: str, **payload) -> None:
        event = redact_secrets({"time":datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                "type":event_type,"game":"pokemon","elapsed_ms":elapsed_ms(),"game_frames":game_frames(),**payload})
        with (output/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+"\n")
            stream.flush()

    def save_report() -> None:
        report['elapsed_ms'] = elapsed_ms()
        report['game_frames'] = game_frames()
        write_json(output/'report.json',report)

    def save_checkpoint() -> None:
        state = world.save()
        state_path = output/'last.state'
        temp = output/'last.state.tmp'
        temp.write_bytes(state)
        temp.replace(state_path)
        identity = {'rom_sha1':load_profile()['rom_sha1'],'state_sha256':hashlib.sha256(state).hexdigest(),
                    'step':report['executed_actions'],'saved_at':datetime.now(timezone.utc).isoformat()}
        write_json(output/'last.progress.json',{**identity,'tracker':tracker.snapshot()})
        write_json(output/'last.state.json',identity)

    save_report()
    emit('started',goal=goal,maxSteps=steps,status=report['status'],pid=os.getpid(),
         video_enabled=video,checkpoint_every=checkpoint_every,max_stalled_steps=max_stalled_steps,resume_from_user_state=state_file is not None)
    history = []
    try:
        if not os.environ.get("TYPESAFE_API_KEY", "").strip():
            report.update(status="blocked_missing_key",reason="Configure TYPESAFE_API_KEY. No fallback policy ran.")
            save_report()
            emit('jev_error',error='missing_api_key',phase='configuration')
            if allow_missing_key:
                return report
            raise RuntimeError(report['reason'])
        profile = load_profile()
        world = Emulator(rom,profile,visible=visible)
        reader = Reader(world,profile)
        if state_file:
            manifest = json.loads(state_file.with_suffix(state_file.suffix+'.json').read_text())
            if manifest['rom_sha1'] != profile['rom_sha1'] or hashlib.sha256(state_file.read_bytes()).hexdigest()!=manifest['state_sha256']:
                raise ValueError("State identity mismatch")
            world.load(state_file.read_bytes())
            tracker, report["progress_memory_source"] = restore_progress(state_file,manifest)
        first_frame = getattr(world.game,'frame_count',None) if hasattr(world,'game') else None
        if video:
            metadata = world.enable_video(output)
            emit('video_started',video=metadata)
        if not state_file:
            world.tick(600)
        report['status']='running'
        save_report()
        for i in range(steps):
            step = i + 1
            report['current_step'] = step
            save_report()
            before = reader.snapshot()
            if before['errors']:
                raise RuntimeError("Memory sanity check failed before action")
            before["progress"] = tracker.context(before)
            verified_before = observation_for_model(before)
            emit('observation',step=step,observation=verified_before)
            screenshot_hash = world.screenshot(output/f'{i:04d}-before.png') if screenshots else None

            measured_attempts = set()
            def decision_event(event: dict) -> None:
                if event['type'] in ('jev_response','jev_error') and type(event.get('latency_ms')) in (int,float) and event.get('attempt') not in measured_attempts:
                    measured_attempts.add(event.get('attempt'))
                    report['model_ms'] += event['latency_ms']
                if event['type'] == 'jev_request':
                    report['jev_http_attempts'] += 1
                    save_report()
                emit(event['type'],step=step,**{key:value for key,value in event.items() if key != 'type'})

            decision = choose(before,goal,history,on_event=decision_event)
            if video:
                video_health = world.video_status()
                if video_health.get('restart_count',0) != video_restarts:
                    video_restarts = video_health['restart_count']
                    report['video_restarts'] = video_restarts
                    emit('video_restarted',step=step,restart_count=video_restarts,error=video_health.get('last_error'))
            report['jev_calls'] += 1
            button = decision['answer']['choice']
            # Keep legacy artifact indexing; streamed step numbers are 1-based.
            row = {'step':i,'source':'jev','screen_sha256':screenshot_hash,**decision}
            write_json(output/f'{i:04d}-decision.json',row)
            save_report()
            emit('decision',step=step,status='accepted',button=button,selected=button,
                 answer=decision['answer'],source=decision.get('source','jev'),
                 model=decision.get('response',{}).get('model',decision.get('request',{}).get('model')),
                 usage=decision.get('response',{}).get('usage'),latency_ms=decision.get('latency_ms'))
            emit('executing',step=step,button=button,action=button)
            try:
                world.press(button,held=16 if button in ('up','down','left','right') else 8,settle=32)
                report['executed_actions'] += 1
                save_report()
                after = reader.snapshot()
                write_json(output/f'{i:04d}-after.json',after)
                if after['errors']:
                    raise RuntimeError("Memory sanity check failed after action")
            except (Exception, KeyboardInterrupt) as exc:
                emit('result',step=step,button=button,success=False,
                     error='interrupted' if isinstance(exc,KeyboardInterrupt) else type(exc).__name__,
                     result={'button':button,'before':verified_before,'after':None})
                raise
            outcome = tracker.record(button,before,after)
            after['progress'] = tracker.context(after)
            report['new_tiles_this_run'] += int(outcome['new_tile'])
            report['movement_actions'] += int(outcome['position_changed'])
            report['map_changes'] += int(outcome['map_changed'])
            stalled_steps = 0 if outcome['world_progress'] else stalled_steps + 1
            report['steps_without_new_tile_this_run'] = stalled_steps
            report['loop_detected'] = after['progress']['loop_detected']
            history.append({'button':button,'before':before.get('player'),'after':after.get('player'),
                            'text_after':after.get('screen_text',{}).get('rows')})
            verified_after = observation_for_model(after)
            emit('result',step=step,button=button,success=True,outcome=outcome,observation=verified_after,
                 result={'button':button,'before':verified_before,'after':verified_after})
            if report['executed_actions'] % checkpoint_every == 0:
                save_checkpoint()
                emit('checkpoint',step=step,saved_step=report['executed_actions'])
            history = history[-12:]
            save_report()
            if stalled_steps >= max_stalled_steps and after['progress']['loop_detected'] and (after.get('scene') or {}).get('verified') is True:
                report.update(status='stalled',reason=f'{stalled_steps} inputs without a new coordinate and a repeated interaction/movement loop; saved for review.')
                break
        else:
            report['status']='budget_reached'
    except KeyboardInterrupt:
        report['status']='interrupted'
    except Exception as exc:
        if report['status'] != 'blocked_missing_key':
            # Arbitrary exception text can contain request credentials or bodies.
            report.update(status='failed',error=type(exc).__name__)
            emit('error',error=type(exc).__name__,phase='run')
        raise
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if world:
                try:
                    save_checkpoint()
                    if screenshots:
                        world.screenshot(output/'last.png')
                    if reader is not None:
                        write_json(output/'last-observation.json',reader.snapshot())
                finally:
                    world.close()
        except KeyboardInterrupt:
            report['status']='interrupted'
            emit('error',error='interrupted',phase='finalization')
        except Exception as exc:
            cleanup_error = exc
            report.update(status='failed',finalization_error=type(exc).__name__)
            emit('error',error=type(exc).__name__,phase='finalization')
        finally:
            save_report()
            emit('finished',status=report['status'],report=report,
                 reason=report.get('reason') or report.get('error') or report.get('finalization_error'))
        if cleanup_error is not None and not active_exception:
            raise RuntimeError("Run finalization failed; see report.json") from None
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rom',type=Path,default=Path('red-star-2020-08-18.gb'))
    ap.add_argument('--output',type=Path,default=Path('pokemon/runs/session'))
    ap.add_argument('--state',type=Path)
    ap.add_argument('--steps',type=int,default=5000)
    ap.add_argument('--video',action=argparse.BooleanOptionalAction,default=True,help='Stream direct emulator video through FFmpeg HLS (enabled by default)')
    ap.add_argument('--checkpoint-every',type=int,default=50)
    ap.add_argument('--max-stalled-steps',type=int,default=80)
    ap.add_argument('--visible',action='store_true')
    ap.add_argument('--screenshots',action='store_true',help='Explicitly save optional screenshot evidence; the live stream never uses images')
    ap.add_argument('--allow-missing-key',action='store_true',help='CI records blocked, never substitutes a fake decision')
    ap.add_argument('--goal',default='Explore Pokemon Red Star and progress through the adventure. Infer your route from dialog and observations.')
    a=ap.parse_args()
    def stop(_signum,_frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    print(json.dumps(run(a.rom,a.output,goal=a.goal,steps=a.steps,state_file=a.state,visible=a.visible,
                         allow_missing_key=a.allow_missing_key,screenshots=a.screenshots,video=a.video,checkpoint_every=a.checkpoint_every,max_stalled_steps=a.max_stalled_steps),indent=2))

if __name__=='__main__':main()
