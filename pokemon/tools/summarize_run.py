#!/usr/bin/env python3
"""Summarize one run's progress and request cost for A/B verification.

Usage: python pokemon/tools/summarize_run.py pokemon/runs/<run-dir>
Prints a compact JSON summary; no model calls, no game.
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path


def summarize(run_dir: Path) -> dict:
    report = json.loads((run_dir / "report.json").read_text())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()]
    observations = [e["observation"] for e in events if e.get("type") == "observation"]
    decisions = [e for e in events if e.get("type") == "decision"]
    outcomes = Counter((e.get("status"), e.get("reason")) for e in events if e.get("type") == "plan_outcome")
    requests = [len(json.dumps(e["request"])) for e in events if e.get("type") == "jev_request"]
    scenes = Counter((o.get("scene") or {}).get("mode") for o in observations)
    maps = Counter((o.get("player") or {}).get("map_id") for o in observations)
    streaks, current = [], 0
    for observation in observations:
        if (observation.get("scene") or {}).get("mode") == "battle":
            current += 1
        elif current:
            streaks.append(current)
            current = 0
    if current:
        streaks.append(current)
    return {
        "run": run_dir.name,
        "status": report.get("status"),
        "executed_actions": report.get("executed_actions"),
        "new_tiles": report.get("new_tiles_this_run"),
        "movement": report.get("movement_actions"),
        "map_changes": report.get("map_changes"),
        "planning_calls": report.get("planning_calls"),
        "plans": report.get("plans"),
        "planning_failures": report.get("planning_failures"),
        "loop_detected": report.get("loop_detected"),
        "outcomes": {f"{k[0]}:{k[1]}": v for k, v in outcomes.items()},
        "buttons": dict(Counter(e.get("button") for e in decisions)),
        "scenes": dict(scenes),
        "maps": {str(k): v for k, v in maps.items()},
        "battle_streaks": streaks,
        "request_bytes": {"max": max(requests, default=0),
                          "avg": round(sum(requests) / len(requests)) if requests else 0,
                          "count": len(requests)},
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    run_dir = Path(sys.argv[1])
    if not (run_dir / "report.json").is_file():
        print(json.dumps({"error": "report.json not found", "run": str(run_dir)}))
        return 1
    print(json.dumps(summarize(run_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
