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
}


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

    def background_hint(self) -> dict | None:
        # The font flag alone does not identify every dialog/menu. This is
        # explicitly a background-only hint, never used to filter actions.
        if self.byte("wFontLoaded") or self.byte("wIsInBattle"):
            return None
        bank = self.byte("wTilesetBank")
        ptr = int.from_bytes(self.data("wTilesetCollisionPtr", 2), "little")
        if not 0x4000 <= ptr < 0x8000:
            return None
        offset = bank * 0x4000 + ptr - 0x4000
        raw = self.emulator.rom_bytes[offset:offset+128]
        if 255 not in raw:
            return None
        passable = set(raw[:raw.index(255)])
        tm = self.data("wTileMap", 360)
        rows = []
        for y in range(9):
            rows.append("".join("@" if (x,y)==(4,4) else "." if tm[(2*y+1)*20+2*x] in passable else "#" for x in range(10)))
        return {"rows": rows, "legend": "@ player; . background hint passable; # otherwise",
                "limitations": "Not a complete collision engine. NPCs, warps, ledges, menus and scrolling can invalidate this hint."}

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
            return {"name":decode_text(self.data("wPlayerName",11)),
                    "map_id":self.byte("wCurMap"),"x":self.byte("wXCoord"),"y":self.byte("wYCoord"),
                    "money":value,"badge_bits":self.byte("wObtainedBadges")}
        return {
            "game":"Pokemon Red Star 2020-08-18", "observation_mode":"read_only_ram",
            "frame":self.emulator.game.frame_count,
            "screen_text":section("screen_text", self.screen_text),
            "player":section("player",player), "party":section("party",self.party),
            "bag":section("bag",self.bag),
            "battle_type_raw":section("battle",lambda:self.byte("wIsInBattle")),
            "menu_cursor_raw":section("menu",lambda:self.byte("wCurrentMenuItem")),
            "background_hint":section("background_hint",self.background_hint),
            "limitations":["RAM can retain intro/menu/previous-scene values.",
                            "Menu cursor is meaningful only with a visible matching menu.",
                            "Active enemy details and story success are not inferred.",
                            "No route or goal is supplied by the memory reader."],
            "errors":errors,
        }
