#!/usr/bin/env python3
"""RSI refiner: turn harness metrics into a bounded change proposal.

Reads a harness metrics file (or a run directory) and asks the planner model for
a small, whitelisted set of changes. It NEVER applies changes; a human or the
gate applies them on a branch and the harness decides whether to keep them.

Usage:
  python pokemon/tools/refine.py --metrics outputs/harness/<ts>/metrics.json --out outputs/harness/<ts>/suggestion.json
  python pokemon/tools/refine.py --run pokemon/runs/<run>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.summarize_run import summarize  # noqa: E402

SYSTEM_PROMPT = """You are the RSI refiner for a two-model agent harness (System Two planner + JEV controller).
Given metrics and a trace digest, propose at most 3 SMALL changes that could improve progress
(new_tiles, map_changes) or reduce wasted effort (battle_streaks, planning_calls, loop rate).
The whitelist is: settle_frames, review_every, escalation_threshold, and rewriting a prompt file.
Do NOT propose game-specific hardcoding (menu names, map ids, coordinates or button macros).
Return JSON only: {"diagnosis":"...","changes":[{"target":"settle_frames|review_every|escalation_threshold|prompt:system1|prompt:system2","kind":"set|replace","value":"..."}]}"""


def call_deepseek(system: str, user: str) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash").strip()
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2, "max_tokens": 2048, "response_format": {"type": "json_object"},
    }
    if model.startswith(("deepseek-flash", "deepseek-v4")):
        body["thinking"] = {"type": "disabled"}
    request = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read(1_000_001))
    return json.loads(payload["choices"][0]["message"]["content"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, help="harness metrics.json")
    parser.add_argument("--run", type=Path, help="a single run directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.metrics:
        digest = json.loads(args.metrics.read_text())
    elif args.run:
        digest = summarize(args.run)
    else:
        parser.error("provide --metrics or --run")
    proposal = call_deepseek(SYSTEM_PROMPT, json.dumps({"metrics": digest}, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(proposal, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
