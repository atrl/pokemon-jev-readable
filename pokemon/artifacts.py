"""Atomic run artifacts and hash-bound observation/planning checkpoints.

The runtime keeps these I/O details outside its observe/decide/act loop.
Existing last.state / last.progress.json / last.campaign.json remain compatible.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from jev import redact_secrets
from progress import ProgressTracker


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(redact_secrets(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    temp.replace(path)


def restore_progress(state_file: Path, manifest: dict) -> tuple[ProgressTracker, str]:
    """Restore only memory bound to this state hash, or replay matching legacy effects."""
    sidecar = state_file.parent / "last.progress.json"
    if sidecar.exists():
        data = json.loads(sidecar.read_text())
        if (
            data.get("rom_sha1") == manifest["rom_sha1"]
            and data.get("state_sha256") == manifest["state_sha256"]
            and data.get("step") == manifest.get("step")
        ):
            return ProgressTracker(data["tracker"]), "verified_checkpoint"
        # A mismatched sidecar must not attach observations from another save.
    tracker = ProgressTracker()
    log = state_file.parent / "events.jsonl"
    saved_step = manifest.get("step")
    replayed = 0
    if state_file.name == "last.state" and type(saved_step) is int and log.exists():
        with log.open() as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if (
                        row.get("type") != "result"
                        or row.get("success") is not True
                        or not 1 <= row.get("step", 0) <= saved_step
                    ):
                        continue
                    effect = row.get("result")
                    if (
                        not isinstance(effect, dict)
                        or not isinstance(effect.get("before"), dict)
                        or not isinstance(effect.get("after"), dict)
                    ):
                        continue
                    tracker.record(
                        row.get("button", effect.get("button")), effect["before"], effect["after"]
                    )
                    replayed += 1
                except (ValueError, TypeError):
                    continue
    return tracker, f"legacy_observed_effects:{replayed}" if replayed else "new_memory"


def load_state(state_file: Path, rom_sha1: str) -> tuple[bytes, dict]:
    manifest = json.loads(state_file.with_suffix(state_file.suffix + ".json").read_text())
    state = state_file.read_bytes()
    if (
        manifest["rom_sha1"] != rom_sha1
        or hashlib.sha256(state).hexdigest() != manifest["state_sha256"]
    ):
        raise ValueError("State identity mismatch")
    return state, manifest


def restore_campaign(state_file: Path, manifest: dict, *, knowledge_mode="assisted"):
    # Old callers retain their explicit comparator API; the runtime passes observed by default.
    from plan_manager import PlanManager
    if knowledge_mode == "observed":
        manager = PlanManager
    else:
        from campaign import CampaignPlanner
        manager = CampaignPlanner
    sidecar = state_file.parent / "last.campaign.json"
    if sidecar.exists():
        saved = json.loads(sidecar.read_text())
        if all(saved.get(key) == manifest.get(key) for key in ("rom_sha1", "state_sha256", "step")):
            if knowledge_mode == "assisted" and saved["campaign"].get("manager") == "observed_v1":
                return manager(), "mode_changed_new_assisted_memory; original_observed_sidecar_preserved"
            return manager(saved["campaign"]), "verified_checkpoint"
    return manager(), "new_memory"


def save_checkpoint(world, output: Path, rom_sha1: str, step: int, tracker, campaign) -> None:
    state = world.save()
    temporary = output / "last.state.tmp"
    temporary.write_bytes(state)
    temporary.replace(output / "last.state")
    identity = {
        "rom_sha1": rom_sha1,
        "state_sha256": hashlib.sha256(state).hexdigest(),
        "step": step,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(output / "last.progress.json", {**identity, "tracker": tracker.snapshot()})
    write_json(output / "last.campaign.json", {**identity, "campaign": campaign.snapshot()})
    write_json(output / "last.state.json", identity)
