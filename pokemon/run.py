#!/usr/bin/env python3
"""ROM -> memory snapshot -> Jev -> one physical button -> a new snapshot.

Start cold or resume a verified local checkpoint. The main loop never runs
a scripted introduction. A decision budget is not a win condition.
The live progress stream contains structured observations, never screenshots.
"""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import signal
import time

from emulator import Emulator
from memory import Reader, load_profile
from jev import choose, observation_for_model, redact_secrets, DEFAULT_GAME_GOAL, JevUnavailable
from progress import ProgressTracker
from campaign import CampaignPlanner
from activity import StallMonitor
from artifacts import (
    write_json,
    load_state,
    restore_progress,
    restore_campaign,
    save_checkpoint as write_checkpoint,
)


def run(
    rom: Path,
    output: Path,
    *,
    goal: str,
    steps: int,
    state_file: Path | None = None,
    visible: bool = False,
    allow_missing_key: bool = False,
    screenshots: bool = False,
    video: bool = False,
    checkpoint_every: int = 50,
    max_stalled_steps: int = 80,
    max_recovery_attempts: int = 3,
) -> dict:
    if type(steps) is not int or not 1 <= steps <= 100_000:
        raise ValueError("steps must be an integer in 1..100000")
    if type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 1000:
        raise ValueError("checkpoint_every must be an integer in 1..1000")
    if type(max_stalled_steps) is not int or not 12 <= max_stalled_steps <= 10000:
        raise ValueError("max_stalled_steps must be an integer in 12..10000")
    if type(max_recovery_attempts) is not int or not 0 <= max_recovery_attempts <= 10:
        raise ValueError("max_recovery_attempts must be an integer in 0..10")
    if not goal.strip():
        raise ValueError("goal cannot be empty")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose a new empty output directory; no previous run will be overwritten")
    started = time.monotonic()
    world = None
    reader = None
    first_frame = None
    video_restarts = 0
    tracker = ProgressTracker()
    campaign = CampaignPlanner()
    stall_monitor = StallMonitor(max_stalled_steps, max_recovery_attempts)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model_ms": 0,
        "video_enabled": video,
        "jev_calls": 0,
        "jev_http_attempts": 0,
        "executed_actions": 0,
        "goal": goal,
        "status": "starting",
        "game_completed": False,
        "policy": "jev_only",
        "new_tiles_this_run": 0,
        "movement_actions": 0,
        "map_changes": 0,
        "progress_memory_source": "new_memory",
        "campaign_memory_source": "new_memory",
        "resume_from_user_state": state_file is not None,
        "max_recovery_attempts": max_recovery_attempts,
        "recovery_attempts": 0,
        "recovery_count": 0,
    }

    def elapsed_ms() -> int:
        return round((time.monotonic() - started) * 1000)

    def game_frames() -> int | None:
        frame = getattr(getattr(world, "game", None), "frame_count", None)
        return (
            max(0, frame - first_frame) if type(frame) is int and type(first_frame) is int else None
        )

    def emit(event_type: str, **payload) -> None:
        event = redact_secrets(
            {
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "type": event_type,
                "game": "pokemon",
                "elapsed_ms": elapsed_ms(),
                "game_frames": game_frames(),
                **payload,
            }
        )
        with (output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

    def save_report() -> None:
        report["elapsed_ms"] = elapsed_ms()
        report["game_frames"] = game_frames()
        write_json(output / "report.json", report)

    def save_checkpoint() -> None:
        write_checkpoint(
            world, output, profile["rom_sha1"], report["executed_actions"], tracker, campaign
        )

    def decide(before, step):
        """Request one decision, keeping the same observation during network outages."""
        nonlocal video_restarts
        measured_attempts = set()

        def decision_event(event: dict) -> None:
            if (
                event["type"] in ("jev_response", "jev_error")
                and type(event.get("latency_ms")) in (int, float)
                and event.get("attempt") not in measured_attempts
            ):
                measured_attempts.add(event.get("attempt"))
                report["model_ms"] += event["latency_ms"]
            if event["type"] == "jev_request":
                report["jev_http_attempts"] += 1
                save_report()
            emit(
                event["type"],
                step=step,
                **{key: value for key, value in event.items() if key != "type"},
            )

        attempt_offset = 0
        unavailable_windows = 0
        while True:

            def retry_event(event):
                # HTTP attempt identities remain distinct across waiting
                # windows on the same paused observation/physical step.
                if type(event.get("attempt")) is int:
                    event = {**event, "attempt": event["attempt"] + attempt_offset}
                decision_event(event)

            try:
                decision = choose(before, goal, [], on_event=retry_event)
                if unavailable_windows:
                    emit("jev_resumed", step=step, consecutive_windows=unavailable_windows)
                report["status"] = "running"
                report.pop("waiting_reason", None)
                break
            except JevUnavailable:
                unavailable_windows += 1
                attempt_offset = max(measured_attempts, default=attempt_offset)
                retry_seconds = min(5 * 2 ** min(unavailable_windows - 1, 3), 40)
                report.update(status="waiting_for_jev", waiting_reason="temporarily_unavailable")
                save_checkpoint()
                save_report()
                emit(
                    "jev_wait",
                    step=step,
                    retry_after_seconds=retry_seconds,
                    reason="temporarily_unavailable",
                    consecutive_windows=unavailable_windows,
                )
                if video and world.video_status().get("terminal_error"):
                    raise RuntimeError("Video encoder recovery exhausted while waiting for JEV")
                # The emulator remains paused; its video encoder keeps
                # publishing the last real framebuffer during this wait.
                time.sleep(retry_seconds)
        if video:
            video_health = world.video_status()
            if video_health.get("terminal_error"):
                emit("video_failed", step=step, error=video_health["terminal_error"])
                raise RuntimeError(
                    "Video encoder recovery exhausted; game checkpoint will be preserved"
                )
            if video_health.get("restart_count", 0) != video_restarts:
                video_restarts = video_health["restart_count"]
                report["video_restarts"] = video_restarts
                emit(
                    "video_restarted",
                    step=step,
                    restart_count=video_restarts,
                    error=video_health.get("last_error"),
                )
        return decision

    def record_decision(decision, step, screenshot_hash):
        """Publish the accepted choice before sending any input to the game."""
        report["jev_calls"] += 1
        button = decision["answer"]["choice"]
        # Keep legacy artifact indexing; streamed step numbers are 1-based.
        row = {"step": step - 1, "source": "jev", "screen_sha256": screenshot_hash, **decision}
        write_json(output / f"{step - 1:04d}-decision.json", row)
        save_report()
        emit(
            "decision",
            step=step,
            status="accepted",
            button=button,
            selected=button,
            answer=decision["answer"],
            source=decision.get("source", "jev"),
            model=decision.get("response", {}).get(
                "model", decision.get("request", {}).get("model")
            ),
            usage=decision.get("response", {}).get("usage"),
            latency_ms=decision.get("latency_ms"),
        )
        return button

    def execute(button, verified_before, step):
        """Execute exactly that button, then record a fresh RAM observation."""
        emit("executing", step=step, button=button, action=button)
        try:
            world.press(
                button, held=16 if button in ("up", "down", "left", "right") else 8, settle=32
            )
            report["executed_actions"] += 1
            save_report()
            after = reader.snapshot()
            write_json(output / f"{step - 1:04d}-after.json", after)
            if after["errors"]:
                raise RuntimeError("Memory sanity check failed after action")
        except (Exception, KeyboardInterrupt) as exc:
            emit(
                "result",
                step=step,
                button=button,
                success=False,
                error="interrupted" if isinstance(exc, KeyboardInterrupt) else type(exc).__name__,
                result={"button": button, "before": verified_before, "after": None},
            )
            raise
        return after

    def record_outcome(button, before, after):
        """Update planning memory and keep activity separate from story achievement."""
        old_objective = before["campaign"]["active_objective"]["id"]
        old_verified = set(before["campaign"]["active_objective"].get("completed_ids", []))
        campaign_effect = campaign.record(button, before, after)
        after["campaign"] = campaign.context(after)
        new_verified = set(after["campaign"]["active_objective"].get("completed_ids", []))
        outcome = tracker.record(button, before, after)
        outcome["objective_changed"] = after["campaign"]["active_objective"]["id"] != old_objective
        outcome["milestones_completed"] = sorted(new_verified - old_verified)
        old_enemy = (before.get("battle") or {}).get("enemy") or {}
        new_enemy = (after.get("battle") or {}).get("enemy") or {}
        outcome["battle_damage"] = bool(
            old_enemy and new_enemy and new_enemy.get("hp", 9999) < old_enemy.get("hp", 0)
        )
        outcome["new_dialog_clue"] = campaign_effect["new_dialog_clue"]
        outcome["meaningful_progress"] = bool(
            outcome["world_progress"]
            or outcome["objective_changed"]
            or outcome["milestones_completed"]
            or outcome["battle_damage"]
            or outcome["new_dialog_clue"]
        )
        after["progress"] = tracker.context(after)
        activity = stall_monitor.observe(before, after, outcome, after["progress"])
        outcome["observable_changes"] = activity["observable_changes"]
        outcome["observable_activity"] = bool(activity["observable_changes"])
        report["new_tiles_this_run"] += int(outcome["new_tile"])
        report["movement_actions"] += int(outcome["position_changed"])
        report["map_changes"] += int(outcome["map_changed"])
        report["steps_without_strategic_progress"] = activity["no_strategic_progress_steps"]
        report["steps_without_observable_change"] = activity["no_effect_steps"]
        report["recovery_attempts"] = stall_monitor.recovery_attempts
        report["steps_without_new_tile_this_run"] = after["progress"]["steps_since_new_tile"]
        report["loop_detected"] = after["progress"]["loop_detected"]
        return outcome, activity

    save_report()
    emit(
        "started",
        goal=goal,
        maxSteps=steps,
        status=report["status"],
        pid=os.getpid(),
        video_enabled=video,
        checkpoint_every=checkpoint_every,
        max_stalled_steps=max_stalled_steps,
        max_recovery_attempts=max_recovery_attempts,
        resume_from_user_state=state_file is not None,
    )
    try:
        if not os.environ.get("TYPESAFE_API_KEY", "").strip():
            report.update(
                status="blocked_missing_key",
                reason="Configure TYPESAFE_API_KEY. No fallback policy ran.",
            )
            save_report()
            emit("jev_error", error="missing_api_key", phase="configuration")
            if allow_missing_key:
                return report
            raise RuntimeError(report["reason"])
        profile = load_profile()
        world = Emulator(rom, profile, visible=visible)
        reader = Reader(world, profile)
        if state_file:
            state, manifest = load_state(state_file, profile["rom_sha1"])
            world.load(state)
            tracker, report["progress_memory_source"] = restore_progress(state_file, manifest)
            campaign, report["campaign_memory_source"] = restore_campaign(state_file, manifest)

        first_frame = getattr(world.game, "frame_count", None) if hasattr(world, "game") else None
        if video:
            metadata = world.enable_video(output)
            emit("video_started", video=metadata)
        if not state_file:
            world.tick(600)
        report["status"] = "running"
        save_report()
        for i in range(steps):
            step = i + 1
            report["current_step"] = step
            save_report()
            before = reader.snapshot()
            if before["errors"]:
                raise RuntimeError("Memory sanity check failed before action")
            before["progress"] = tracker.context(before)
            before["campaign"] = campaign.context(before)
            objective = before["campaign"]["active_objective"]
            if report.get("active_objective") != objective["id"]:
                report["active_objective"] = objective["id"]
                if before.get("world"):
                    emit(
                        "objective",
                        step=step,
                        objective=objective,
                        navigation=before["campaign"]["navigation"],
                    )
            report["completed_objectives"] = objective.get("completed_ids", [])
            if objective.get("id") == "main_story_complete" and objective.get("completion") is True:
                report.update(
                    status="completed",
                    game_completed=True,
                    completion_evidence=objective.get("completion_evidence"),
                )
                break
            verified_before = observation_for_model(before)
            verified_before["campaign"] = before["campaign"]
            emit("observation", step=step, observation=verified_before)
            screenshot_hash = (
                world.screenshot(output / f"{i:04d}-before.png") if screenshots else None
            )

            decision = decide(before, step)
            button = record_decision(decision, step, screenshot_hash)
            after = execute(button, verified_before, step)
            outcome, activity = record_outcome(button, before, after)
            verified_after = observation_for_model(after)
            verified_after["campaign"] = after["campaign"]
            report["completed_objectives"] = after["campaign"]["active_objective"].get(
                "completed_ids", []
            )
            report["active_objective"] = after["campaign"]["active_objective"]["id"]
            emit(
                "result",
                step=step,
                button=button,
                success=True,
                outcome=outcome,
                observation=verified_after,
                result={"button": button, "before": verified_before, "after": verified_after},
            )
            if outcome["objective_changed"] and after.get("world"):
                emit(
                    "objective",
                    step=step,
                    objective=after["campaign"]["active_objective"],
                    navigation=after["campaign"]["navigation"],
                    completed=outcome["milestones_completed"],
                )
            if report["executed_actions"] % checkpoint_every == 0:
                save_checkpoint()
                emit("checkpoint", step=step, saved_step=report["executed_actions"])
            save_report()
            if activity["should_recover"]:
                attempt = stall_monitor.begin_recovery()
                campaign.recover(
                    after, attempt=attempt, reason=activity["reason"], failed_button=button
                )
                report.update(
                    status="recovering",
                    recovery_attempts=attempt,
                    recovery_count=stall_monitor.total_recoveries,
                )
                save_checkpoint()
                save_report()
                emit(
                    "recovery",
                    step=step,
                    attempt=attempt,
                    max_attempts=max_recovery_attempts,
                    reason=activity["reason"],
                    no_effect_steps=activity["no_effect_steps"],
                    loop_kind=after["progress"].get("loop_kind"),
                    failed_button=button,
                )
            elif activity["exhausted"]:
                report.update(
                    status="stalled",
                    reason=f"{activity['reason']} persisted after {stall_monitor.recovery_attempts} bounded recovery attempts; saved for review.",
                )
                break
        else:
            report["status"] = "budget_reached"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as exc:
        if report["status"] != "blocked_missing_key":
            # Arbitrary exception text can contain request credentials or bodies.
            report.update(status="failed", error=type(exc).__name__)
            emit("error", error=type(exc).__name__, phase="run")
        raise
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if world:
                try:
                    save_checkpoint()
                    if screenshots:
                        world.screenshot(output / "last.png")
                    if reader is not None:
                        write_json(output / "last-observation.json", reader.snapshot())
                finally:
                    world.close()
        except KeyboardInterrupt:
            report["status"] = "interrupted"
            emit("error", error="interrupted", phase="finalization")
        except Exception as exc:
            cleanup_error = exc
            report.update(status="failed", finalization_error=type(exc).__name__)
            emit("error", error=type(exc).__name__, phase="finalization")
        finally:
            save_report()
            emit(
                "finished",
                status=report["status"],
                report=report,
                reason=report.get("reason")
                or report.get("error")
                or report.get("finalization_error"),
            )
        if cleanup_error is not None and not active_exception:
            raise RuntimeError("Run finalization failed; see report.json") from None
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=Path("red-star-2020-08-18.gb"))
    ap.add_argument("--output", type=Path, default=Path("pokemon/runs/session"))
    ap.add_argument("--state", type=Path)
    ap.add_argument("--steps", type=int, default=5000)
    ap.add_argument(
        "--video",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stream direct emulator video through FFmpeg HLS (enabled by default)",
    )
    ap.add_argument("--checkpoint-every", type=int, default=50)
    ap.add_argument("--max-stalled-steps", type=int, default=80)
    ap.add_argument(
        "--max-recovery-attempts",
        type=int,
        default=3,
        help="Re-observe and revise guidance before pausing a persistent loop; 0 disables recovery",
    )
    ap.add_argument("--visible", action="store_true")
    ap.add_argument(
        "--screenshots",
        action="store_true",
        help="Explicitly save optional screenshot evidence; the live stream never uses images",
    )
    ap.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="CI records blocked, never substitutes a fake decision",
    )
    ap.add_argument("--goal", default=DEFAULT_GAME_GOAL)
    a = ap.parse_args()

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(
        json.dumps(
            run(
                a.rom,
                a.output,
                goal=a.goal,
                steps=a.steps,
                state_file=a.state,
                visible=a.visible,
                allow_missing_key=a.allow_missing_key,
                screenshots=a.screenshots,
                video=a.video,
                checkpoint_every=a.checkpoint_every,
                max_stalled_steps=a.max_stalled_steps,
                max_recovery_attempts=a.max_recovery_attempts,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
