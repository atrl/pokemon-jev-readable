"""Hash-bound checkpoint registration and session-family split isolation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import tempfile

from .common import PROTOCOL
from .evaluation import validate_success

SPLITS = frozenset({"train", "validation", "test"})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")


def _hash(value, size: int, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is None:
        raise ValueError(f"Invalid {name}")


def _identity(state: Path) -> tuple[bytes, dict]:
    data = state.read_bytes()
    manifest = json.loads(state.with_suffix(state.suffix + ".json").read_text())
    _hash(manifest.get("rom_sha1"), 40, "manifest ROM hash")
    _hash(manifest.get("state_sha256"), 64, "manifest state hash")
    if not data or hashlib.sha256(data).hexdigest() != manifest["state_sha256"]:
        raise ValueError("Checkpoint manifest does not match the state bytes")
    return data, manifest


def state_path(case: dict, suite_path: Path) -> Path:
    relative = Path(case["state"])
    root = Path(suite_path).resolve().parent
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Suite checkpoint paths must stay relative to the suite directory")
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Suite checkpoint resolves outside its directory")
    return result


def validate_suite(suite: dict, suite_path: Path | None = None) -> dict:
    if not isinstance(suite, dict) or suite.get("protocol") != PROTOCOL:
        raise ValueError("Suite protocol does not match this experiment implementation")
    if set(suite) - {"protocol", "cases"} or not isinstance(suite.get("cases"), list):
        raise ValueError("Suite must contain protocol and a cases list")
    ids, hashes, families = set(), {}, {}
    for case in suite["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Suite case must be an object")
        required = {"id", "family", "split", "state", "state_sha256", "rom_sha1", "goal", "success"}
        if set(case) != required:
            raise ValueError("Suite case has missing or unknown fields")
        for field in ("id", "family"):
            if not isinstance(case[field], str) or not _NAME.fullmatch(case[field]):
                raise ValueError(f"Invalid case {field}")
        if case["id"] in ids:
            raise ValueError("Duplicate suite case id")
        ids.add(case["id"])
        if case["split"] not in SPLITS:
            raise ValueError("Case split must be train, validation or test")
        if (
            not isinstance(case["goal"], str)
            or not case["goal"].strip()
            or len(case["goal"]) > 2000
        ):
            raise ValueError("Case goal must contain 1..2000 characters")
        _hash(case["state_sha256"], 64, "case state hash")
        _hash(case["rom_sha1"], 40, "case ROM hash")
        validate_success(case["success"])
        for identity, seen, label in [
            (case["state_sha256"], hashes, "state hash"),
            (case["family"], families, "session family"),
        ]:
            if identity in seen and seen[identity] != case["split"]:
                raise ValueError(f"Train/validation/test leakage: {label} occurs across splits")
            seen[identity] = case["split"]
        if not isinstance(case["state"], str) or not case["state"]:
            raise ValueError("Case needs a relative checkpoint path")
        path = state_path(case, suite_path or Path("suite.json"))
        if suite_path is not None:
            _, manifest = _identity(path)
            if any(manifest[key] != case[key] for key in ("rom_sha1", "state_sha256")):
                raise ValueError("Suite case identity disagrees with its registered checkpoint")
    return deepcopy(suite)


def load_suite(path: Path) -> dict:
    path = Path(path)
    return validate_suite(json.loads(path.read_text()), path)


def get_case(suite: dict, case_id: str) -> dict:
    matches = [case for case in suite.get("cases", []) if case.get("id") == case_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one suite case named {case_id!r}")
    return deepcopy(matches[0])


def _write_json(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix="." + path.name, delete=False
    ) as file:
        temporary = Path(file.name)
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def register_case(
    suite_path: Path, case_id: str, family: str, split: str, state: Path, goal: str, success: dict
) -> dict:
    """Copy state plus its verified manifest; never import legacy memory or mutate the source."""
    suite_path, state = Path(suite_path), Path(state)
    data, manifest = _identity(state)
    suite = load_suite(suite_path) if suite_path.exists() else {"protocol": PROTOCOL, "cases": []}
    relative = f"states/{manifest['state_sha256']}.state"
    case = {
        "id": case_id,
        "family": family,
        "split": split,
        "state": relative,
        "state_sha256": manifest["state_sha256"],
        "rom_sha1": manifest["rom_sha1"],
        "goal": goal,
        "success": validate_success(success),
    }
    candidate = {"protocol": PROTOCOL, "cases": [*suite["cases"], case]}
    validate_suite(candidate)  # Reject leaks and malformed metadata before creating files.
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    destination = state_path(case, suite_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing, existing_manifest = _identity(destination)
        if existing != data or existing_manifest["rom_sha1"] != manifest["rom_sha1"]:
            raise ValueError("Registered checkpoint destination has conflicting content")
    else:
        # Only the state identity belongs to the suite; old campaign/strategy files do not.
        with destination.open("xb") as file:
            file.write(data)
        _write_json(
            destination.with_suffix(".state.json"),
            {key: manifest[key] for key in ("rom_sha1", "state_sha256")},
        )
    validate_suite(candidate, suite_path)
    _write_json(suite_path, candidate)
    return deepcopy(case)
