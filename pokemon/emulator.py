"""A real, paused-between-decisions PyBoy session, with no task macros."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from jev import BUTTONS


class Emulator:
    def __init__(self, rom: Path, profile: dict, *, visible: bool = False):
        self.rom_bytes = rom.read_bytes()
        if hashlib.sha1(self.rom_bytes).hexdigest() != profile["rom_sha1"]:
            raise ValueError("ROM does not match the verified memory profile")
        from pyboy import PyBoy
        self.game = PyBoy(str(rom), window="SDL2" if visible else "null", sound_emulated=False)
        self.game.set_emulation_speed(0)

    def tick(self, frames: int) -> None:
        if type(frames) is not int or not 1 <= frames <= 3600:
            raise ValueError("frames must be an integer in 1..3600")
        if not self.game.tick(frames, render=True, sound=False):
            raise RuntimeError("Emulator stopped")

    def press(self, button: str, held: int = 8, settle: int = 24) -> None:
        if button not in BUTTONS:
            raise ValueError("Only physical buttons and wait are supported")
        if any(type(n) is not int or not 1 <= n <= 120 for n in (held, settle)):
            raise ValueError("held and settle must be integers in 1..120")
        # No other input source is active in headless operation.
        for key in BUTTONS:
            if key != "wait":
                self.game.button_release(key)
        if button == "wait":
            self.tick(held + settle)
            return
        self.game.button_press(button)
        try:
            self.tick(held)
        finally:
            self.game.button_release(button)
        self.tick(settle)

    def read(self, address: int, length: int = 1) -> bytes:
        if not 0 <= address <= 65535 or not 0 <= length <= 65536 - address:
            raise ValueError("Invalid memory range")
        return bytes(self.game.memory[address:address + length])

    def save(self) -> bytes:
        stream = io.BytesIO()
        self.game.save_state(stream)
        return stream.getvalue()

    def load(self, state: bytes) -> None:
        self.game.load_state(io.BytesIO(state))

    def screenshot(self, path: Path) -> str:
        image = self.game.screen.image.convert("RGB")
        image.save(path)
        return hashlib.sha256(image.tobytes()).hexdigest()

    def close(self) -> None:
        # Do not overwrite a cartridge .sav belonging to the user.
        self.game.stop(save=False)
