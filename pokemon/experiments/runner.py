"""Run frozen trials and compare their independent outcomes, including failures."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import html
import json
import math
from pathlib import Path
import time

from .common import (
    ACTIONS,
    PROTOCOL,
    Budget,
    BudgetExceeded,
    ExperimentError,
    digest,
    source_digest,
    write_json,
)


def make_controller(name, case, checkpoint, budget, config, allow_model_calls):
    if name == "rl":
        if checkpoint is None:
            raise ExperimentError("rl_checkpoint_required")
        from .rl import RLController

        return RLController(Path(checkpoint), case, config)
    from .controllers import build_controller

    return build_controller(name, budget, config, allow_model_calls=allow_model_calls)


def run_trial(
    suite_path,
    case_id,
    controller,
    output,
    *,
    config,
    checkpoint=None,
    allow_model_calls=False,
    rom=None,
    env_factory=None,
    controller_factory=None,
):
    from .environment import ExperimentEnv
    from .suite import get_case, load_suite

    suite_path, output = Path(suite_path), Path(output)
    suite = load_suite(suite_path)
    case = get_case(suite, case_id)
    if controller not in ("brain", "hybrid", "rl"):
        raise ValueError("Unknown controller")
    if controller != "rl" and not allow_model_calls:
        raise ExperimentError("model_calls_not_authorized")
    output.mkdir(parents=True, exist_ok=False)
    budget = Budget(config)
    report = {
        "protocol": PROTOCOL,
        "controller": controller,
        "case_id": case_id,
        "family": case["family"],
        "split": case["split"],
        "state_sha256": case["state_sha256"],
        "suite_sha256": digest(suite),
        "config_sha256": digest(config),
        "rom_sha1": case["rom_sha1"],
        "source_sha256": source_digest(),
        "environment_config": deepcopy(config.get("environment", {})),
        "status": "starting",
        "success": False,
        "terminated": False,
        "truncated": False,
        "total_reward": 0.0,
        "action_owners": {},
        "inference_seconds": 0.0,
        "compute_cost_usd": None,
        "cost_note": "No price/energy model supplied; local inference and training are not free.",
        "memory_initialization": "empty_common_memory",
        "frozen_evaluation": True,
    }
    env = policy = None
    owners = Counter()
    try:
        policy = (controller_factory or make_controller)(
            controller, case, checkpoint, budget, config, allow_model_calls
        )
        if hasattr(policy, "public_config"):
            report["controller_provenance"] = policy.public_config()
        elif hasattr(policy, "metadata"):
            report["controller_provenance"] = {
                "controller": controller,
                "model_sha256": policy.metadata["model_sha256"],
                "training": deepcopy(policy.metadata["training"]),
                "contract": deepcopy(policy.metadata["contract"]),
            }
        env_config = deepcopy(config.get("environment", {}))
        env_config["max_steps"] = min(env_config.get("max_steps", 200), budget.limits["max_steps"])
        env = (env_factory or ExperimentEnv)(case, suite_path, rom=rom, config=env_config)
        packet = env.reset()
        if hasattr(policy, "reset"):
            policy.reset()
        report["initial_packet_sha256"] = digest(packet)
        write_json(output / "initial-observation.json", packet)
        report["status"] = "running"
        with (output / "trajectory.jsonl").open("w") as events:
            while True:
                budget.before_action()
                before = packet
                began = time.monotonic()
                try:
                    decision = policy.decide(deepcopy(packet))
                finally:
                    report["inference_seconds"] += time.monotonic() - began
                # A late response must not spend a physical action after the deadline.
                budget.before_action()
                if not isinstance(decision, dict) or decision.get("button") not in ACTIONS:
                    raise ExperimentError("invalid_controller_action")
                expected_owner = {"brain": "deepseek", "hybrid": "kev", "rl": "rl"}[controller]
                if decision.get("owner") != expected_owner:
                    raise ExperimentError("invalid_action_owner")
                packet, reward, terminated, truncated, info = env.step(decision["button"])
                budget.record_action()
                owners[decision["owner"]] += 1
                report["total_reward"] += reward
                report.update(
                    success=bool(info.get("success")),
                    terminated=bool(terminated),
                    truncated=bool(truncated),
                    final_evaluation=info,
                )
                # Preserve the exact common input and observed next state for later
                # verified data curation. Teacher actions are not labelled correct.
                row = {
                    "step": budget.actions,
                    "packet": before,
                    "decision": decision,
                    "next_packet": packet,
                    "reward": reward,
                    "evaluation": info,
                    "terminated": terminated,
                    "truncated": truncated,
                }
                events.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                events.flush()
                if terminated or truncated:
                    report["status"] = (
                        "success"
                        if report["success"]
                        else "truncated"
                        if truncated
                        else "terminated"
                    )
                    report["stop_reason"] = info.get("stop_reason")
                    break
    except BudgetExceeded as exc:
        report.update(status="truncated", truncated=True, stop_reason=exc.reason)
    except ExperimentError as exc:
        unavailable = exc.code.startswith(("blocked", "missing", "rl_")) or exc.code.endswith(
            ("_http_401", "_http_402", "_http_403", "_network_error")
        )
        report.update(status="blocked" if unavailable else "error", stop_reason=exc.code)
    except KeyboardInterrupt:
        report.update(status="interrupted", truncated=True, stop_reason="user_interrupt")
        raise
    except Exception as exc:
        # Exception messages may contain URLs, remote bodies or credentials.
        report.update(status="error", stop_reason=type(exc).__name__)
        raise
    finally:
        if env is not None:
            if hasattr(env, "executed_actions"):
                budget.actions = env.executed_actions
                report["attempted_actions"] = env.attempted_actions
            try:
                env.screenshot(output / "last.png")
            except Exception:
                report["screenshot_status"] = "unavailable"
            try:
                env.close()
            except Exception:
                report["cleanup_status"] = "environment_close_failed"
        if policy is not None and hasattr(policy, "close"):
            try:
                policy.close()
            except Exception:
                report["policy_cleanup_status"] = "failed"
        report["action_owners"] = dict(owners)
        report["budget"] = budget.snapshot()
        report["inference_seconds"] = round(report["inference_seconds"], 6)
        report["total_reward"] = round(report["total_reward"], 6)
        write_json(output / "report.json", report)
    return report


def compare(
    suite_path,
    output,
    *,
    controllers,
    split="test",
    repeats=1,
    max_trials=30,
    config,
    checkpoint=None,
    allow_model_calls=False,
    rom=None,
    max_seconds=None,
):
    from .suite import load_suite

    if type(repeats) is not int or repeats < 1 or type(max_trials) is not int or max_trials < 1:
        raise ValueError("Positive repeat/trial limits required")
    if max_seconds is not None and (
        type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0
    ):
        raise ValueError("Batch time limit must be positive and finite")
    if (
        not controllers
        or len(set(controllers)) != len(controllers)
        or set(controllers) - {"brain", "hybrid", "rl"}
    ):
        raise ValueError("Choose distinct controllers from brain,hybrid,rl")
    if set(controllers) - {"rl"} and not allow_model_calls:
        raise ExperimentError("model_calls_not_authorized")
    if "rl" in controllers and (checkpoint is None or not Path(checkpoint).is_file()):
        raise ExperimentError("rl_checkpoint_required")
    suite = load_suite(Path(suite_path))
    cases = [c for c in suite["cases"] if c["split"] == split]
    trials = [
        (case, repeat, name) for case in cases for repeat in range(repeats) for name in controllers
    ]
    if not trials or len(trials) > max_trials:
        raise ValueError("Empty suite split or trial count exceeds --max-trials")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    start, results = time.monotonic(), []
    for index, (case, repeat, name) in enumerate(trials):
        if max_seconds is not None and time.monotonic() - start >= max_seconds:
            break
        trial_config = deepcopy(config)
        if max_seconds is not None:
            trial_config["budget"]["max_seconds"] = min(
                trial_config["budget"]["max_seconds"], max_seconds - (time.monotonic() - start)
            )
        folder = f"trial-{index + 1:04d}-{name}"
        try:
            result = run_trial(
                suite_path,
                case["id"],
                name,
                output / folder,
                config=trial_config,
                checkpoint=checkpoint,
                allow_model_calls=allow_model_calls,
                rom=rom,
            )
        except Exception:
            path = output / folder / "report.json"
            if not path.exists():
                raise
            result = json.loads(path.read_text())
        results.append({**result, "repeat": repeat, "directory": folder})
        write_comparison(output, results, len(trials), time.monotonic() - start)
    return write_comparison(output, results, len(trials), time.monotonic() - start)


def write_comparison(output, results, planned_trials, seconds):
    by_case = {}
    for row in results:
        by_case.setdefault(row["case_id"], []).append(row)
    checks = {}
    for case_id, rows in by_case.items():
        hashes = {r["initial_packet_sha256"] for r in rows if r.get("initial_packet_sha256")}
        observed = sum(bool(r.get("initial_packet_sha256")) for r in rows)
        status = (
            "MISMATCH"
            if len(hashes) > 1
            else "match"
            if observed == len(rows) and observed >= 2
            else "insufficient"
        )
        checks[case_id] = {
            "status": status,
            "initialized_trials": observed,
            "attempted_trials": len(rows),
        }
    groups = {}
    for name in sorted({r["controller"] for r in results}):
        rows = [r for r in results if r["controller"] == name]
        ds_requests = sum(
            q["owner"] == "deepseek" for r in rows for q in r["budget"].get("requests", [])
        )
        http_requests = sum(r["budget"]["http_requests"] for r in rows)
        groups[name] = {
            "trials": len(rows),
            "successes": sum(r["success"] for r in rows),
            "http_requests": http_requests,
            "deepseek_http_requests": ds_requests,
            "deepseek_http_fraction": ds_requests / http_requests if http_requests else None,
            "repeated_evidence_requests": sum(
                r["budget"].get("repeated_evidence_requests", 0) for r in rows
            ),
            "measured_tokens": sum(r["budget"]["measured_tokens"] for r in rows),
            "unknown_usage_requests": sum(r["budget"]["unknown_usage_requests"] for r in rows),
            "total_inference_seconds": sum(r["inference_seconds"] for r in rows),
            "monetary_cost_per_success": None,
        }
    report = {
        "protocol": PROTOCOL,
        "planned_trials": planned_trials,
        "completed_trials": len(results),
        "elapsed_seconds": seconds,
        "groups": groups,
        "initial_input_checks": checks,
        "limitations": [
            "Same emulator checkpoint repetitions are not independent random trials.",
            "Training and local compute costs must be included before monetary comparisons.",
            "Unknown usage is not zero; failures remain in the denominator.",
        ],
        "trials": results,
    }
    write_json(Path(output) / "comparison.json", report)
    rows = []
    for row in results:
        esc = html.escape
        href = esc(row["directory"], quote=True)
        rows.append(
            f"<tr><td>{esc(row['case_id'])}</td><td>{esc(row['controller'])}</td>"
            f"<td>{esc(row['status'])}</td><td>{row['budget']['executed_actions']}</td>"
            f"<td>{row['budget']['http_requests']}</td><td>{row['budget']['measured_tokens']}</td>"
            f"<td>{row['budget']['unknown_usage_requests']}</td>"
            f'<td><a href="{href}/report.json">report</a> · <a href="{href}/last.png">frame</a></td></tr>'
        )
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
    document += "<title>Pokémon experiment comparison</title><style>body{font:16px system-ui;margin:2rem;max-width:1100px}table{border-collapse:collapse;width:100%}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}</style>"
    document += "<h1>Pokémon experiment comparison</h1><p>Independent task results. Repeated checkpoints are not independent random trials. Unknown usage is not free.</p>"
    document += f'<p>{len(results)} / {planned_trials} trials completed. <a href="comparison.json">Full evidence</a></p>'
    document += "<table><thead><tr><th>Case</th><th>Controller</th><th>Outcome</th><th>Actions</th><th>HTTP</th><th>Measured tokens</th><th>Unknown usage</th><th>Evidence</th></tr></thead><tbody>"
    document += "".join(rows) + "</tbody></table><h2>Initial input checks</h2><pre>"
    document += html.escape(json.dumps(report["initial_input_checks"], indent=2)) + "</pre></html>"
    (Path(output) / "index.html").write_text(document)
    return report
