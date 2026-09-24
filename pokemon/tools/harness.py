#!/usr/bin/env python3
"""Harness: run the agent over fixed checkpoints/variants and collect metrics.

This is the evaluation side of the RSI loop. It starts real runs (which call the
configured models), then reuses tools/summarize_run.py to produce comparable
metrics so a proposed change can be accepted or rolled back.

Usage:
  python pokemon/tools/harness.py --state pokemon/runs/<run>/last.state \
      --steps 200 --planner-mode deepseek --settle 64 --review-every 40
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

POKEMON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POKEMON))

from controls import DEFAULT_GAME_GOAL  # noqa: E402
from paths import default_rom  # noqa: E402
from run import run  # noqa: E402
from tools.summarize_run import summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", required=True, help="Checkpoint last.state (repeatable)")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--planner-mode", default="deepseek")
    parser.add_argument("--settle", type=int, default=64)
    parser.add_argument("--review-every", type=int, default=40)
    parser.add_argument("--rom", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("outputs/harness"))
    args = parser.parse_args()
    rom = args.rom or default_rom()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root = args.out / stamp
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for state in args.state:
        state_path = Path(state)
        out = root / state_path.parent.name
        out.mkdir(parents=True, exist_ok=True)
        report = run(
            rom, out, goal=DEFAULT_GAME_GOAL, steps=args.steps, state_file=state_path,
            video=False, planner_mode=args.planner_mode,
            settle_frames=args.settle, review_every=args.review_every,
        )
        results.append({
            "state": str(state_path), "run": out.name, "status": report.get("status"),
            "metrics": summarize(out),
        })
    (root / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
