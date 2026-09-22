"""Read-only Red Star adapter. Addresses come from a pinned source symbol table.

Only the exact user-supplied ROM hash is accepted. Source-derived addresses are
also checked with real input/screen tests; this is NOT the original Red profile.
"""
from __future__ import annotations

import json
from pathlib import Path

CHARACTERS = {
    0x7F: " ",
    **{0x80 + i: c for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
    **{0xA0 + i: c for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{0xF6 + i: c for i, c in enumerate("0123456789")},
    0xBA: "é", 0xE0: "'", 0xE1: "PK", 0xE2: "MN", 0xE3: "-", 0xE6: "?",
    0xE7: "!", 0xE8: ".", 0xEC: "▷", 0xED: "▶", 0xEE: "▼", 0xEF: "♂",
    0xF0: "$", 0xF1: "×", 0xF3: "/", 0xF4: ",", 0xF5: "♀",
    0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";",
    0x9E: "[", 0x9F: "]", 0xBB: "'d", 0xBC: "'l", 0xBD: "'s",
    0xBE: "'t", 0xBF: "'v", 0xE4: "'r", 0xE5: "'m", 0xEB: "▲",
}

FACING = {0: "down", 4: "up", 8: "left", 12: "right"}
NEIGHBORS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def decode_text(data: bytes, *, terminated: bool = True, unknown: str = "�") -> str:
    result = []
    for value in data:
        if terminated and value == 0x50:
            break
        result.append(CHARACTERS.get(value, unknown))
    return "".join(result).rstrip()


def load_profile() -> dict:
    return json.loads(Path(__file__).with_name("redstar-profile.json").read_text())


class Reader:
    def __init__(self, emulator, profile: dict):
        self.emulator = emulator
        self.symbols = profile["addresses"]

    def address(self, name: str) -> int:
        return self.symbols[name]

    def data(self, name: str, length: int = 1, offset: int = 0) -> bytes:
        return self.emulator.read(self.address(name) + offset, length)

    def byte(self, name: str) -> int:
        return self.data(name)[0]

    def screen_text(self) -> dict:
        tiles = self.data("wTileMap", 360)
        # Keep rows/cursor positions. Unknown background tiles become spaces,
        # not guessed words. Dialog can be only partly printed this frame.
        rows = [decode_text(tiles[i:i+20], terminated=False, unknown=" ") for i in range(0, 360, 20)]
        return {"rows": rows, "source": "RAM wTileMap, not OCR",
                "limitations": "Background tiles may resemble letters; scrolling text may be incomplete."}

    def party(self) -> list[dict]:
        count = self.byte("wPartyCount")
        if count > 6:
            raise ValueError("Uninitialized or incompatible party count")
        stride = self.address("wPartyMon2") - self.address("wPartyMon1")
        if stride != 44:
            raise ValueError("Unsupported party structure")
        result = []
        for i in range(count):
            mon = self.data("wPartyMon1", 44, i * stride)
            hp = int.from_bytes(mon[1:3], "big")
            max_hp = int.from_bytes(mon[34:36], "big")
            if not 1 <= mon[33] <= 100 or not 0 <= hp <= max_hp <= 999 or max_hp == 0:
                raise ValueError("Party HP/level sanity check failed")
            result.append({
                "slot": i, "nickname": decode_text(self.data("wPartyMonNicks", 11, i * 11)),
                "species_internal_id": mon[0], "level": mon[33], "hp": hp, "max_hp": max_hp,
                "status_bits": mon[4],
                "moves": [{"move_id": mon[8+j], "pp": mon[29+j] & 63} for j in range(4) if mon[8+j]],
            })
        return result

    def bag(self) -> list[dict]:
        count = self.byte("wNumBagItems")
        if count > 20:
            raise ValueError("Uninitialized or incompatible bag count")
        raw = self.data("wBagItems", count * 2)
        return [{"item_id": raw[i], "quantity": raw[i+1]} for i in range(0,len(raw),2)]

    def _background_collision(self) -> tuple[int, set[int]] | None:
        # Pinned Red Star home.asm includes data/collision.asm in ROM0.
        # wTilesetBank is the GRAPHICS bank; using it for collision is wrong.
        # Unknown/banked pointers fail closed rather than guessing a bank.
        ptr = int.from_bytes(self.data("wTilesetCollisionPtr", 2), "little")
        if not 0 < ptr < 0x4000:
            return None
        raw = self.emulator.rom_bytes[ptr:min(ptr + 128, 0x4000)]
        if 255 not in raw or raw[0] == 255:
            return None
        return ptr, set(raw[:raw.index(255)])

    @staticmethod
    def _box(tiles: bytes, x: int, y: int, width: int, height: int) -> bool:
        """Recognize the actual font border, not the presence/absence of text."""
        def at(dx, dy):
            return tiles[(y + dy) * 20 + x + dx]
        return (at(0, 0) == 0x79 and at(width - 1, 0) == 0x7B
                and at(0, height - 1) == 0x7D and at(width - 1, height - 1) == 0x7E
                and all(at(dx, 0) == 0x7A for dx in range(1, width - 1))
                and all(at(dx, height - 1) in (0x7A, 0xEE) for dx in range(1, width - 1))
                and all(at(0, dy) == at(width - 1, dy) == 0x7C for dy in range(1, height - 1)))

    def scene(self) -> dict:
        tiles = self.data("wTileMap", 360)
        font = self.byte("wFontLoaded")
        mode = "unknown"
        # Battles and other interfaces have not passed this profile's live
        # scene regression; their shared text-box graphics do not prove dialog.
        if self.byte("wIsInBattle") == 0 and font == 1:
            menu_rows = [decode_text(tiles[y * 20 + 12:y * 20 + 19],
                                     terminated=False, unknown=" ").strip() for y in range(18)]
            menu_border = any(self._box(tiles, 10, 0, 10, height) for height in (14, 16))
            if menu_border and {"PACK", "SAVE", "OPTION", "EXIT"}.issubset(menu_rows):
                mode = "main_menu"
            elif self._box(tiles, 0, 12, 20, 6):
                mode = "dialog"
        elif self.byte("wIsInBattle") == 0 and font == 0:
            collision = self._background_collision()
            # The source samples tile (8,9) under the centered player and uses
            # the same grid for adjacent movement checks. Reject transitions.
            if (collision and tiles[9 * 20 + 8] in collision[1]
                    and self.byte("PlayerYPixels") == 60 and self.byte("PlayerXPixels") == 64
                    and self.byte("PlayerFacingDirection") in FACING):
                mode = "overworld"
        return {"mode": mode, "verified": mode != "unknown",
                "quality": "verified_pattern" if mode != "unknown" else "needs_data",
                "source": "RAM font flag, battle exclusion, tile borders/menu labels, ROM0 background and player alignment",
                "validation_scope": "Exact ROM; live regression in starting room, N64 dialog, and main menu",
                "limitations": "Conservative pattern classification; unrecognized scenes remain unknown. Blank text alone does not identify a scene."}

    def dialog(self, scene: dict | None = None) -> dict:
        scene = scene or self.scene()
        mode = scene["mode"]
        if mode == "dialog":
            tiles = self.data("wTileMap", 360)
            lines = [decode_text(tiles[y * 20 + 1:y * 20 + 19], terminated=False, unknown=" ").strip()
                     for y in range(13, 17)]
            arrow = tiles[17 * 20 + 18] == 0xEE
            return {"open": True, "awaiting_input": True if arrow else None,
                    "text": "\n".join(line for line in lines if line),
                    "quality": "verified_text_box_partial_text_possible",
                    "source": "RAM wTileMap dialog border/interior and visible down arrow",
                    "awaiting_input_reason": "visible_down_arrow" if arrow else "arrow_absent_or_blinking; printing_or_waiting_unknown"}
        known = mode in ("overworld", "main_menu")
        return {"open": False if known else None, "awaiting_input": False if known else None,
                "text": "" if known else None,
                "quality": "verified_closed" if known else "needs_data",
                "source": "Verified scene pattern" if known else "No recognized scene pattern"}

    def local_map(self, scene: dict | None = None) -> dict | None:
        scene = scene or self.scene()
        if scene["mode"] != "overworld":
            return None
        collision = self._background_collision()
        if collision is None:
            return None
        ptr, passable = collision
        tiles = self.data("wTileMap", 360)
        def tile(x, y):
            return tiles[(2 * y + 1) * 20 + 2 * x]
        rows = ["".join("@" if (x, y) == (4, 4) else "." if tile(x, y) in passable else "#"
                        for x in range(10)) for y in range(9)]
        neighbors = {button: {"dx": dx, "dy": dy, "tile_id": tile(4 + dx, 4 + dy),
                              "background_passable": tile(4 + dx, 4 + dy) in passable}
                     for button, (dx, dy) in NEIGHBORS.items()}
        return {"rows": rows, "player_cell": {"x": 4, "y": 4}, "neighbors": neighbors,
                "legend": "@ player; . background permits walking; # background does not permit walking",
                "quality": "advisory_background_only", "verified": True,
                "source": f"RAM wTileMap lower-left tiles; exact ROM0 collision table at 0x{ptr:04x}",
                "validation_scope": "All four neighbors and coordinate/facing responses tested in starting room",
                "limitations": "Not complete collision or route data. Does not identify NPCs, objects, exits, warps, ledges or scripted movement. Never filters buttons."}

    def background_hint(self) -> dict | None:
        # Compatibility for raw evidence consumers; model uses explicit local_map.
        return self.local_map()

    def snapshot(self) -> dict:
        errors = {}
        def section(name, fn):
            try:
                return fn()
            except (ValueError, KeyError, IndexError) as exc:
                errors[name] = str(exc)
                return None
        def player():
            money = self.data("wPlayerMoney",3)
            if any((b>>4)>9 or (b&15)>9 for b in money):
                raise ValueError("Invalid BCD money")
            value = 0
            for b in money:
                value = value*100 + (b>>4)*10 + (b&15)
            facing = FACING.get(self.byte("PlayerFacingDirection")) if scene and scene["verified"] else None
            return {"name":decode_text(self.data("wPlayerName",11)),
                    "map_id":self.byte("wCurMap"),"x":self.byte("wXCoord"),"y":self.byte("wYCoord"),
                    "money":value,"badge_bits":self.byte("wObtainedBadges"),
                    "facing":facing, "facing_source":"RAM PlayerFacingDirection at 0xc109",
                    "facing_quality":"verified_direction_response" if facing is not None else "needs_data"}
        scene = section("scene", self.scene)
        local_map = section("local_map", lambda: self.local_map(scene)) if scene else None
        return {
            "game":"Pokemon Red Star 2020-08-18", "observation_mode":"read_only_ram",
            "frame":self.emulator.game.frame_count,
            "screen_text":section("screen_text", self.screen_text),
            "player":section("player",player), "party":section("party",self.party),
            "bag":section("bag",self.bag),
            "battle_type_raw":section("battle",lambda:self.byte("wIsInBattle")),
            "menu_cursor_raw":section("menu",lambda:self.byte("wCurrentMenuItem")),
            "scene":scene, "dialog":section("dialog",lambda:self.dialog(scene)) if scene else None,
            "local_map":local_map, "background_hint":local_map,
            "limitations":["RAM can retain intro/menu/previous-scene values.",
                            "Menu cursor is meaningful only with a visible matching menu.",
                            "Active enemy details and story success are not inferred.",
                            "No route or goal is supplied by the memory reader."],
            "errors":errors,
        }
