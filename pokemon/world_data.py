"""Pinned Red Star world priors, kept distinct from read-only live RAM facts.

Rebuild metadata with: python world_data.py /path/to/redstarbluestar
The source revision is related to, but NOT byte-identical to, the user's ROM.
Nothing in this module emits buttons or mutates emulator state.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from paths import DATA

SOURCE_COMMIT = "08deafad427f0904f285e515c360003efc19d3dc"


@lru_cache(maxsize=1)
def load_world_data() -> dict:
    return json.loads((DATA / "redstar-world.json").read_text())


def map_prior(map_id: int) -> dict | None:
    return load_world_data()["maps"].get(str(map_id))


def named_lookup(kind: str, value: int) -> str | None:
    entry = load_world_data()[kind].get(str(value))
    return entry.get("name") if isinstance(entry, dict) else entry


def move_prior(move_id: int) -> dict:
    return dict(load_world_data()["moves"].get(str(move_id), {}))


def type_effectiveness(
    attack_type_id: int, defender_type_ids: list[int], *, verified=False
) -> dict:
    """Version-pinned chart math; caller must supply actual-ROM verification.

    Duplicate types are one type in this engine, not squared effectiveness.
    This is only the type multiplier: STAB, damage rounding, critical hits,
    status, screens and accuracy are separate battle mechanisms.
    """
    data = load_world_data()
    known = data["types"]
    if (
        str(attack_type_id) not in known
        or not defender_type_ids
        or len(defender_type_ids) > 2
        or any(str(t) not in known for t in defender_type_ids)
    ):
        return {
            "multiplier": None,
            "verified": False,
            "quality": "needs_data",
            "source": "Unknown move or defender type; no neutral-type assumption",
        }
    defenders = set(defender_type_ids)
    multiplier = 1.0
    matches = []
    for row in data["type_chart"]["entries"]:
        if row["attack_type_id"] == attack_type_id and row["defender_type_id"] in defenders:
            multiplier *= row["factor_tenths"] / 10
            matches.append(row)
    return {
        "multiplier": multiplier,
        "verified": verified is True,
        "quality": "verified_exact_rom_type_chart" if verified is True else "source_prior",
        "source": "Pinned Red Star data/type_effects.asm; all 82 type pairs plus terminator match exact ROM"
        if verified is True
        else "Pinned Red Star source chart; actual-ROM match not supplied",
        "attack_type_id": attack_type_id,
        "defender_type_ids": sorted(defenders),
        "matched_pairs": matches,
        "factors": [row["factor_tenths"] / 10 for row in matches],
        "limitations": "Type multiplier only; no STAB, damage rounding, critical-hit, screen or accuracy calculation.",
    }


def build(source: Path) -> dict:
    """Offline compatibility entrypoint; parsing lives outside the runtime reader."""
    from tools.build_world_data import build as build_from_source

    return build_from_source(source)


if __name__ == "__main__":
    import sys

    source = Path(sys.argv[1])
    result = build(source)
    (DATA / "redstar-world.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Built {len(result['maps'])} maps and {len(result['moves'])} move priors")
