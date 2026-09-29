"""Shared paused emulator, player observation, episode memory and independent scoring."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from emulator import Emulator
from memory import Reader, load_profile
from paths import default_rom
from perception import project

from .common import ACTIONS, PROTOCOL, ExperimentError, canonical_json
from .context import EpisodeMemory
from .evaluation import Evaluator
from .suite import state_path, validate_suite


class ExperimentEnv:
    def __init__(
        self, case: dict, suite_path: Path, rom: Path | None = None, config: dict | None = None
    ):
        self.case = deepcopy(case)
        self.suite_path = Path(suite_path)
        self.rom = Path(rom) if rom is not None else default_rom()
        self.config = deepcopy(config or {})
        self.held_frames = self.config.get("held_frames", 8)
        self.settle_frames = self.config.get("settle_frames", 16)
        self.max_steps = self.config.get("max_steps", 200)
        self.max_packet_bytes = self.config.get("max_packet_bytes", 65536)
        for name in ("held_frames", "settle_frames"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 120:
                raise ValueError(f"{name} must be an integer in 1..120")
        if (self.held_frames, self.settle_frames) != (8, 16):
            raise ValueError("The v1 experiment protocol requires 8 held and 16 settle frames")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 10_000_000:
            raise ValueError("max_steps must be an integer in 1..10000000")
        if type(self.max_packet_bytes) is not int or self.max_packet_bytes <= 0:
            raise ValueError("max_packet_bytes must be a positive integer")
        self.world = None
        self.reader = None
        self.memory = None
        self.evaluator = None
        self._observation = None
        self._finished = False
        self.executed_actions = 0
        self.attempted_actions = 0

    def reset(self) -> dict:
        self.close()
        self.executed_actions = 0
        self.attempted_actions = 0
        validate_suite({"protocol": PROTOCOL, "cases": [self.case]}, self.suite_path)
        profile = load_profile()
        if self.case["rom_sha1"] != profile["rom_sha1"]:
            raise ValueError("Suite ROM identity does not match the verified reader profile")
        if hashlib.sha1(self.rom.read_bytes()).hexdigest() != self.case["rom_sha1"]:
            raise ValueError("ROM identity does not match the suite checkpoint")
        state = state_path(self.case, self.suite_path).read_bytes()
        self.memory = EpisodeMemory()
        self.evaluator = Evaluator(self.case["success"], self.config.get("reward"))
        try:
            self.world = Emulator(self.rom, profile)
            self.world.load(state)
            self.reader = Reader(self.world, profile)
            raw, observation = self._observe()
            self._observation = observation
            self.memory.observe(observation)
            self.evaluator.reset(raw, observation)
            self._finished = False
            return self._packet({"steps": 0, "reward": 0.0, "success": False})
        except Exception:
            self.close()
            raise

    def _observe(self) -> tuple[dict, dict]:
        raw = self.reader.snapshot()
        if raw.get("errors"):
            # Fail closed without putting raw RAM/debug fields into model or exception output.
            raise RuntimeError("Reader observation failed its memory sanity checks")
        return raw, project(raw)

    def _packet(self, feedback: dict) -> dict:
        packet = {
            "protocol": PROTOCOL,
            "observation": deepcopy(self._observation),
            "memory": self.memory.context(self._observation),
            "goal": self.case["goal"],
            "feedback": deepcopy(feedback),
            "observation_id": self._observation["observation_id"],
        }
        # One capacity boundary for text models and RL. Never silently discard old
        # dialogue or map cells in only one arm to fit its input representation.
        if len(canonical_json(packet).encode("utf-8")) > self.max_packet_bytes:
            raise ExperimentError("observation_packet_capacity")
        return packet

    def step(self, button: str) -> tuple[dict, float, bool, bool, dict]:
        if self.world is None or self._observation is None:
            raise RuntimeError("Call reset before taking an experiment action")
        if self._finished:
            raise RuntimeError("Episode has finished; reset before taking another action")
        if not isinstance(button, str) or button not in ACTIONS:
            raise ValueError("An experiment action must be exactly one of the nine basic inputs")
        before = self._observation
        self.attempted_actions += 1
        self.world.press(button, held=self.held_frames, settle=self.settle_frames)
        self.executed_actions += 1
        raw, after = self._observe()
        self.memory.record(button, before, after)
        reward, terminated, info = self.evaluator.step(raw, after, button)
        truncated = not terminated and self.evaluator.steps >= self.max_steps
        self._observation = after
        self._finished = terminated or truncated
        info.update(
            {
                "truncated": truncated,
                "terminated": terminated,
                "stop_reason": "success" if terminated else "step_budget" if truncated else None,
            }
        )
        # All feedback is about observed outcomes; task internals and hidden targets stay private.
        feedback = {
            key: info[key]
            for key in ("success", "steps", "reward_components", "new_badges", "first_visit")
        }
        feedback.update(
            reward=reward, observation_changed=before["observation_id"] != after["observation_id"]
        )
        return self._packet(feedback), reward, terminated, truncated, deepcopy(info)

    def screenshot(self, path: Path):
        if self.world is None:
            raise RuntimeError("No active experiment emulator")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self.world.screenshot(path)

    def close(self) -> None:
        world, self.world = self.world, None
        self.reader = None
        self._observation = None
        if world is not None:
            world.close()
