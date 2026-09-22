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


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(redact_secrets(data),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    temp.replace(path)


def run(rom: Path, output: Path, *, goal: str, steps: int, state_file: Path | None = None,
        visible: bool = False, allow_missing_key: bool = False, screenshots: bool = False,
        video: bool = False, checkpoint_every: int = 50) -> dict:
    if type(steps) is not int or not 1 <= steps <= 100_000:
        raise ValueError("steps must be an integer in 1..100000")
    if type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 1000:
        raise ValueError("checkpoint_every must be an integer in 1..1000")
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
    report = {"started_at":datetime.now(timezone.utc).isoformat(),"model_ms":0,"video_enabled":video,"jev_calls":0,"jev_http_attempts":0,"executed_actions":0,"goal":goal,"status":"starting",
              "game_completed":False,"policy":"jev_only","resume_from_user_state":state_file is not None}

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
        write_json(output/'last.state.json',{'rom_sha1':load_profile()['rom_sha1'],
                   'state_sha256':hashlib.sha256(state).hexdigest(),'step':report['executed_actions'],
                   'saved_at':datetime.now(timezone.utc).isoformat()})

    save_report()
    emit('started',goal=goal,maxSteps=steps,status=report['status'],pid=os.getpid(),
         video_enabled=video,checkpoint_every=checkpoint_every,resume_from_user_state=state_file is not None)
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
            history.append({'button':button,'before':before.get('player'),'after':after.get('player'),
                            'text_after':after.get('screen_text',{}).get('rows')})
            verified_after = observation_for_model(after)
            emit('result',step=step,button=button,success=True,observation=verified_after,
                 result={'button':button,'before':verified_before,'after':verified_after})
            if report['executed_actions'] % checkpoint_every == 0:
                save_checkpoint()
                emit('checkpoint',step=step,saved_step=report['executed_actions'])
            history = history[-12:]
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
    ap.add_argument('--visible',action='store_true')
    ap.add_argument('--screenshots',action='store_true',help='Explicitly save optional screenshot evidence; the live stream never uses images')
    ap.add_argument('--allow-missing-key',action='store_true',help='CI records blocked, never substitutes a fake decision')
    ap.add_argument('--goal',default='Explore Pokemon Red Star and progress through the adventure. Infer your route from dialog and observations.')
    a=ap.parse_args()
    def stop(_signum,_frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    print(json.dumps(run(a.rom,a.output,goal=a.goal,steps=a.steps,state_file=a.state,visible=a.visible,
                         allow_missing_key=a.allow_missing_key,screenshots=a.screenshots,video=a.video,checkpoint_every=a.checkpoint_every),indent=2))

if __name__=='__main__':main()
