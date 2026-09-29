"""Register identical starts, run controllers, train PPO, or compare frozen trials."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex

from .common import ROOT, ExperimentError, load_config


def load_environment():
    """Load the existing dotenv convention as data, never shell code."""
    for path in (ROOT / ".env", ROOT / "pokemon/.env"):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key.isidentifier():
                continue
            try:
                words = shlex.split(value, comments=True)
            except ValueError:
                raise ExperimentError("invalid_dotenv") from None
            os.environ.setdefault(key, " ".join(words))


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    register = commands.add_parser(
        "register", help="Copy a hash-verified checkpoint into an isolated suite"
    )
    register.add_argument("--suite", type=Path, required=True)
    register.add_argument("--id", required=True)
    register.add_argument("--family", required=True)
    register.add_argument("--split", choices=("train", "validation", "test"), required=True)
    register.add_argument("--state", type=Path, required=True)
    register.add_argument("--goal", required=True)
    register.add_argument(
        "--success",
        required=True,
        choices=(
            "map_changed",
            "map_entered",
            "battle_finished",
            "dialog_closed",
            "scene_changed",
            "party_grew",
            "badge_gained",
            "game_completed",
        ),
    )
    register.add_argument("--map-id", type=int)
    for name in ("run", "compare", "train"):
        command = commands.add_parser(name)
        command.add_argument("--suite", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--config", type=Path)
        command.add_argument("--rom", type=Path)
        if name != "train":
            command.add_argument("--checkpoint", type=Path)
            command.add_argument("--allow-model-calls", action="store_true")
            command.add_argument("--max-steps", type=int)
            command.add_argument("--max-seconds", type=float)
            command.add_argument("--max-requests", type=int)
            command.add_argument("--max-tokens", type=int)
        if name == "run":
            command.add_argument("--case", required=True)
            command.add_argument("--controller", required=True, choices=("brain", "hybrid", "rl"))
        elif name == "compare":
            command.add_argument("--controllers", default="brain,hybrid,rl")
            command.add_argument("--split", choices=("train", "validation", "test"), default="test")
            command.add_argument("--repeats", type=int, default=1)
            command.add_argument("--max-trials", type=int, default=30)
            command.add_argument("--batch-max-seconds", type=float)
        else:
            command.add_argument("--timesteps", type=int, required=True)
            command.add_argument("--num-envs", type=int, default=1)
            command.add_argument("--seed", type=int, default=0)
            command.add_argument("--resume", type=Path)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "register":
            from .suite import register_case

            success = {"type": args.success}
            if args.map_id is not None:
                success["map_id"] = args.map_id
            result = register_case(
                args.suite, args.id, args.family, args.split, args.state, args.goal, success
            )
        else:
            config = load_config(args.config)
            for key in ("max_steps", "max_seconds", "max_requests", "max_tokens"):
                if getattr(args, key, None) is not None:
                    config["budget"][key] = getattr(args, key)
            if getattr(args, "max_steps", None) is not None:
                config["environment"]["max_steps"] = args.max_steps
            if args.command == "train":
                from .rl import train

                result = train(
                    args.suite,
                    args.output,
                    config,
                    args.timesteps,
                    args.num_envs,
                    args.seed,
                    args.resume,
                    args.rom,
                )
            else:
                # Ordinary help, import, register and RL training do not load model secrets.
                load_environment()
                from .runner import compare, run_trial

                if args.command == "run":
                    result = run_trial(
                        args.suite,
                        args.case,
                        args.controller,
                        args.output,
                        config=config,
                        checkpoint=args.checkpoint,
                        allow_model_calls=args.allow_model_calls,
                        rom=args.rom,
                    )
                else:
                    result = compare(
                        args.suite,
                        args.output,
                        controllers=args.controllers.split(","),
                        split=args.split,
                        repeats=args.repeats,
                        max_trials=args.max_trials,
                        config=config,
                        checkpoint=args.checkpoint,
                        allow_model_calls=args.allow_model_calls,
                        rom=args.rom,
                        max_seconds=args.batch_max_seconds,
                    )
        # Detailed traces remain in artifacts; do not dump full model packets in terminal.
        summary = {
            key: result[key]
            for key in (
                "id",
                "status",
                "success",
                "stop_reason",
                "completed_trials",
                "planned_trials",
                "checkpoint",
                "timesteps",
            )
            if key in result
        }
        print(json.dumps(summary, ensure_ascii=False))
        if args.command == "run":
            return 0 if result["success"] else 2
        if args.command == "compare":
            rows = result["trials"]
            return (
                0
                if len(rows) == result["planned_trials"]
                and all(r["status"] in ("success", "truncated", "terminated") for r in rows)
                else 2
            )
        return 0
    except ExperimentError as exc:
        print(json.dumps({"status": "blocked", "reason": exc.code}))
        return 2
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(json.dumps({"status": "error", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
