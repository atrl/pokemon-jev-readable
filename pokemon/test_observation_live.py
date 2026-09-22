"""Read-only validation on an isolated copy of the stopped run; no Jev/network.

The deterministic button sequence is a regression fixture, never a game policy.
No image files are captured. The production state is hashed before and after.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from emulator import Emulator
from memory import Reader, load_profile

EXPECTED_STATE_SHA256 = "bc2086287ed9783e0974a0b0e587b26e722b26708f620429f8785a18fca52f94"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(rom: Path, state: Path, output: Path) -> dict:
    profile = load_profile()
    assert digest(state.read_bytes()) == EXPECTED_STATE_SHA256, "Unexpected stopped-run checkpoint"
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rom_sha1": profile["rom_sha1"],
        "source_commit": profile["source_commit"],
        "source_binary_match": False,
        "input_state_sha256": EXPECTED_STATE_SHA256,
        "policy": "isolated_copy_scripted_regression_not_jev",
        "jev_calls": 0,
        "production_actions": 0,
        "screenshots_created": 0,
        "checks": {},
        "source_evidence": {
            "repository": "https://github.com/Rangi42/redstarbluestar",
            "facing": "wram.asm:212-255; explicit Sprite State Data section at 0xc100; player facing offset 9",
            "collision_table_bank": "home.asm:33,874; data/collision.asm included in ROM0",
            "neighbor_tile_sampling": "engine/overworld/player_state.asm:277-309",
            "dialog_border": "home/text.asm:1-40; charmap.asm:160-170",
            "awaiting_input_marker": "home/text.asm:505-519; absent blinking arrow is inconclusive",
        },
    }
    with tempfile.TemporaryDirectory(prefix="jev-observation-regression-") as folder:
        copied = Path(folder) / "checkpoint.state"
        shutil.copyfile(state, copied)
        baseline = copied.read_bytes()
        assert digest(baseline) == EXPECTED_STATE_SHA256
        world = Emulator(rom, profile)
        reader = Reader(world, profile)
        try:
            world.load(baseline)
            initial = reader.snapshot()
            assert not initial["errors"], initial["errors"]
            assert initial["scene"]["mode"] == "overworld"
            assert not any(row.strip() for row in initial["screen_text"]["rows"])
            assert initial["dialog"]["open"] is False
            grid = initial["local_map"]
            assert grid and grid["quality"] == "advisory_background_only"
            table = world.rom_bytes[0x1744:0x174E]
            assert table.hex() == "010203111213141c1aff"
            report["checks"]["blank_overworld"] = {
                "position": {k: initial["player"][k] for k in ("map_id", "x", "y", "facing")},
                "scene": initial["scene"],
                "dialog": initial["dialog"],
                "local_map": grid,
                "collision_table_rom0_address": "0x1744",
                "collision_table_bytes": table.hex(),
            }

            movements = []
            for button, dx, dy in [("right", 1, 0), ("left", -1, 0), ("down", 0, 1), ("up", 0, -1)]:
                world.load(baseline)
                before = reader.snapshot()["player"]
                before_tiles = reader.data("wTileMap", 360)
                world.press(button, 16, 32)
                observed = reader.snapshot()
                after = observed["player"]
                moved = (before["x"], before["y"]) != (after["x"], after["y"])
                assert observed["scene"]["mode"] == "overworld"
                assert after["facing"] == button
                assert moved == grid["neighbors"][button]["background_passable"]
                if moved:
                    assert (after["x"], after["y"]) == (before["x"] + dx, before["y"] + dy)
                    assert reader.data("wTileMap", 360) != before_tiles
                movements.append(
                    {
                        "button": button,
                        "before": [before["x"], before["y"]],
                        "after": [after["x"], after["y"]],
                        "facing": after["facing"],
                        "raw_facing": reader.byte("PlayerFacingDirection"),
                        "moved": moved,
                        "background_prediction": grid["neighbors"][button],
                    }
                )
            report["checks"]["direction_facing_and_background"] = movements

            world.load(baseline)
            world.game.button_press("a")
            try:
                world.tick(2)
                empty_dialog = reader.snapshot()
                assert empty_dialog["scene"]["mode"] == "dialog"
                assert empty_dialog["dialog"]["text"] == ""
                assert empty_dialog["dialog"]["open"] is True
                assert empty_dialog["dialog"]["awaiting_input"] is None
                assert empty_dialog["local_map"] is None
                world.tick(6)
            finally:
                world.game.button_release("a")
            world.tick(52)
            first = reader.snapshot()
            assert first["scene"]["mode"] == "dialog"
            assert "playing the N64!" in first["dialog"]["text"]
            assert first["dialog"]["awaiting_input"] is True
            world.tick(30)
            hidden_arrow = reader.snapshot()
            assert hidden_arrow["dialog"]["text"] == first["dialog"]["text"]
            assert hidden_arrow["dialog"]["awaiting_input"] is None
            report["checks"]["empty_dialog_before_letters"] = empty_dialog["dialog"]
            report["checks"]["n64_visible_arrow"] = first["dialog"]
            report["checks"]["n64_hidden_blinking_arrow"] = hidden_arrow["dialog"]

            pages = []
            for _ in range(5):
                world.press("b", 8, 120)
                observed = reader.snapshot()
                pages.append(observed["dialog"])
                if observed["scene"]["mode"] == "overworld":
                    break
            assert observed["scene"]["mode"] == "overworld"
            assert observed["dialog"]["open"] is False
            assert any("It's time to go!" in (page["text"] or "") for page in pages)
            assert observed["player"]["x"] == 3 and observed["player"]["y"] == 6
            world.press("a", 8, 52)
            reopened = reader.snapshot()
            assert reopened["scene"]["mode"] == "dialog"
            assert reopened["dialog"]["text"] == first["dialog"]["text"]
            report["checks"]["dialog_close_then_a_reopens"] = {
                "closed_without_moving": True,
                "dialog_pages": pages,
                "reopened_same_first_page": True,
                "reopened_dialog": reopened["dialog"],
            }

            world.load(baseline)
            world.press("start", 8, 64)
            menu_before = reader.snapshot()
            assert menu_before["scene"]["mode"] == "main_menu"
            assert menu_before["dialog"]["open"] is False and menu_before["local_map"] is None
            world.press("down", 8, 24)
            menu_after = reader.snapshot()
            assert menu_after["scene"]["mode"] == "main_menu"
            assert menu_after["menu_cursor_raw"] == menu_before["menu_cursor_raw"] + 1
            assert any("▶PACK" in row for row in menu_after["screen_text"]["rows"])
            world.press("b", 8, 64)
            assert reader.snapshot()["scene"]["mode"] == "overworld"
            report["checks"]["main_menu_cursor"] = {
                "scene": menu_before["scene"]["mode"],
                "before": menu_before["menu_cursor_raw"],
                "after": menu_after["menu_cursor_raw"],
                "visible_selection": "PACK",
                "b_returns_to_overworld": True,
            }
            report["status"] = "passed"
            report["not_tested"] = [
                "Other maps",
                "NPC collision",
                "Warps/exits",
                "Ledges",
                "Battles",
                "Story progress",
                "Jev behavior after prompt changes",
            ]
        finally:
            world.close()
            report["input_state_unchanged"] = digest(state.read_bytes()) == EXPECTED_STATE_SHA256
            assert report["input_state_unchanged"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("pokemon/evidence/observation-verified.json")
    )
    args = parser.parse_args()
    result = verify(args.rom, args.state, args.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": list(result["checks"]),
                "input_state_unchanged": result["input_state_unchanged"],
                "jev_calls": result["jev_calls"],
            }
        )
    )
