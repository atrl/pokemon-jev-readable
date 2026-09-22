#!/usr/bin/env python3
"""ROM -> memory snapshot -> Jev -> one physical button -> a new snapshot.

No scripted introduction is used here. Start cold or explicitly load a local
state made by a human/test fixture. A decision budget is not a win condition.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from emulator import Emulator
from memory import Reader, load_profile
from jev import choose


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    temp.replace(path)


def run(rom: Path, output: Path, *, goal: str, steps: int, state_file: Path | None = None,
        visible: bool = False, allow_missing_key: bool = False) -> dict:
    if type(steps) is not int or not 1 <= steps <= 1000:
        raise ValueError("steps must be an integer in 1..1000")
    if not goal.strip():
        raise ValueError("goal cannot be empty")
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose a new empty output directory; no previous run will be overwritten")
    report = {"jev_calls":0,"executed_actions":0,"goal":goal,"status":"starting",
              "game_completed":False,"policy":"jev_only","resume_from_user_state":state_file is not None}
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        report.update(status="blocked_missing_key",reason="Configure TYPESAFE_API_KEY. No fallback policy ran.")
        write_json(output/'report.json',report)
        if allow_missing_key:
            return report
        raise RuntimeError(report['reason'])
    world = None
    history = []
    try:
        profile = load_profile()
        world = Emulator(rom,profile,visible=visible)
        reader = Reader(world,profile)
        if state_file:
            manifest = json.loads(state_file.with_suffix(state_file.suffix+'.json').read_text())
            if manifest['rom_sha1'] != profile['rom_sha1'] or hashlib.sha256(state_file.read_bytes()).hexdigest()!=manifest['state_sha256']:
                raise ValueError("State identity mismatch")
            world.load(state_file.read_bytes())
        else:
            world.tick(600)
        for i in range(steps):
            before = reader.snapshot()
            if before['errors']:
                raise RuntimeError(f"Memory sanity check failed: {before['errors']}")
            screenshot_hash = world.screenshot(output/f'{i:04d}-before.png')
            decision = choose(before,goal,history)
            report['jev_calls'] += 1
            button = decision['answer']['choice']
            # Log accepted decision BEFORE execution; an error must not erase it.
            row = {'step':i,'source':'jev','screen_sha256':screenshot_hash,**decision}
            write_json(output/f'{i:04d}-decision.json',row)
            world.press(button,held=16 if button in ('up','down','left','right') else 8,settle=32)
            after = reader.snapshot()
            history.append({'button':button,'before':before.get('player'),'after':after.get('player'),
                            'text_after':after.get('screen_text',{}).get('rows')})
            report['executed_actions'] += 1
            write_json(output/f'{i:04d}-after.json',after)
        report['status']='budget_reached'
    except KeyboardInterrupt:
        report['status']='interrupted'
    except Exception as exc:
        report.update(status='failed',error=str(exc))
        raise
    finally:
        if world:
            try:
                state=world.save()
                (output/'last.state').write_bytes(state)
                write_json(output/'last.state.json',{'rom_sha1':load_profile()['rom_sha1'],'state_sha256':hashlib.sha256(state).hexdigest()})
                world.screenshot(output/'last.png')
                write_json(output/'last-observation.json',reader.snapshot())
            finally:
                world.close()
        write_json(output/'report.json',report)
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rom',type=Path,default=Path('red-star-2020-08-18.gb'))
    ap.add_argument('--output',type=Path,default=Path('pokemon/runs/session'))
    ap.add_argument('--state',type=Path)
    ap.add_argument('--steps',type=int,default=20)
    ap.add_argument('--visible',action='store_true')
    ap.add_argument('--allow-missing-key',action='store_true',help='CI records blocked, never substitutes a fake decision')
    ap.add_argument('--goal',default='Explore Pokemon Red Star and progress through the adventure. Infer your route from dialog and observations.')
    a=ap.parse_args()
    print(json.dumps(run(a.rom,a.output,goal=a.goal,steps=a.steps,state_file=a.state,visible=a.visible,allow_missing_key=a.allow_missing_key),indent=2))

if __name__=='__main__':main()
