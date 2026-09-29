"""Recurrent PPO using exactly the shared public packet, with frozen evaluation.

The complete canonical UTF-8 packet is retained, including dialogue and memory.
The byte CNN learns its representation from scratch; this is a modest baseline,
not a pretrained language encoder. Capacity overflow is an error, never silent
RL-only truncation. No raw emulator state or evaluator event bits enter policy.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import time
from pathlib import Path
from typing import ClassVar

from .common import ACTIONS, PROTOCOL, ExperimentError, canonical_json, digest

ENCODING_VERSION = "complete-utf8-byte-cnn-v1"
DEFAULT_PACKET_BYTES = 65536
NUMERIC_SIZE = 24
_METADATA_VERSION = 1
_DEPENDENCIES = None


def _dependencies():
    """Keep installing/importing torch optional for the other two controllers."""
    global _DEPENDENCIES
    if _DEPENDENCIES is not None:
        return _DEPENDENCIES
    try:
        import gymnasium as gym
        import numpy as np
        import torch
        from sb3_contrib import RecurrentPPO
        from stable_baselines3.common.callbacks import BaseCallback
        from stable_baselines3.common.logger import configure
        from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
        from stable_baselines3.common.vec_env import DummyVecEnv
    except ImportError as exc:
        raise ExperimentError(
            "RL dependencies missing; install pokemon/experiments/requirements-rl.txt"
        ) from exc

    class PacketFeatures(BaseFeaturesExtractor):
        """Compress every byte before the LSTM, keeping recurrent input small."""

        def __init__(self, observation_space, features_dim=128):
            super().__init__(observation_space, features_dim)
            self.embedding = torch.nn.Embedding(257, 8, padding_idx=0)
            self.text = torch.nn.Sequential(
                torch.nn.Conv1d(8, 16, kernel_size=8, stride=8),
                torch.nn.ReLU(),
                torch.nn.Conv1d(16, 24, kernel_size=8, stride=8),
                torch.nn.ReLU(),
                torch.nn.AdaptiveAvgPool1d(16),
                torch.nn.Flatten(),
            )
            self.combine = torch.nn.Sequential(
                torch.nn.Linear(24 * 16 + NUMERIC_SIZE, features_dim),
                torch.nn.ReLU(),
            )

        def forward(self, observations):
            embedded = self.embedding(observations["packet"].long()).transpose(1, 2)
            return self.combine(torch.cat((self.text(embedded), observations["numeric"]), dim=1))

    class TrainingEnv(gym.Env):
        metadata: ClassVar[dict] = {"render_modes": []}

        def __init__(self, cases, suite_path, rom, config, offset):
            super().__init__()
            self.cases, self.suite_path = cases, suite_path
            self.rom, self.config, self.offset = rom, config, offset
            self.episode_index = 0
            self.current = None
            self.capacity = _capacity(config)
            self.observation_space = _observation_space(gym, np, self.capacity)
            self.action_space = gym.spaces.Discrete(len(ACTIONS))

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            from .environment import ExperimentEnv

            if self.current is not None:
                self.current.close()
            # Every train case is visited, even when num_envs < len(train cases).
            case = self.cases[(self.offset + self.episode_index) % len(self.cases)]
            self.episode_index += 1
            self.current = ExperimentEnv(
                case,
                self.suite_path,
                rom=self.rom,
                config=self.config.get("environment", self.config),
            )
            result = self.current.reset()
            packet, info = result if isinstance(result, tuple) else (result, {})
            return encode_packet(packet, self.capacity), info

        def step(self, action):
            packet, reward, terminated, truncated, info = self.current.step(ACTIONS[int(action)])
            return (
                encode_packet(packet, self.capacity),
                float(reward),
                bool(terminated),
                bool(truncated),
                info,
            )

        def close(self):
            if self.current is not None:
                self.current.close()
                self.current = None

    class CurveCallback(BaseCallback):
        def __init__(self, output, start, previous_steps, num_envs):
            super().__init__()
            self.output, self.start, self.previous_steps = output, start, previous_steps
            self.returns = np.zeros(num_envs)
            self.lengths = np.zeros(num_envs, dtype=int)
            self.episodes = 0

        def _on_step(self):
            self.returns += self.locals["rewards"]
            self.lengths += 1
            for i, done in enumerate(self.locals["dones"]):
                if done:
                    self.episodes += 1
                    row = {
                        "timesteps": self.num_timesteps,
                        "elapsed_seconds": time.monotonic() - self.start,
                        "episode": self.episodes,
                        "env": i,
                        "return": float(self.returns[i]),
                        "length": int(self.lengths[i]),
                    }
                    with self.output.open("a", encoding="utf-8") as handle:
                        handle.write(canonical_json(row) + "\n")
                    self.returns[i], self.lengths[i] = 0, 0
            return True

    _DEPENDENCIES = {
        "gym": gym,
        "np": np,
        "torch": torch,
        "RecurrentPPO": RecurrentPPO,
        "PacketFeatures": PacketFeatures,
        "TrainingEnv": TrainingEnv,
        "CurveCallback": CurveCallback,
        "DummyVecEnv": DummyVecEnv,
        "configure": configure,
    }
    return _DEPENDENCIES


def _capacity(config):
    environment_capacity = config.get("environment", config).get(
        "max_packet_bytes", DEFAULT_PACKET_BYTES
    )
    capacity = config.get("rl", {}).get("packet_bytes", environment_capacity)
    if capacity != environment_capacity:
        raise ExperimentError(
            "RL packet capacity contract mismatch: environment.max_packet_bytes and rl.packet_bytes must agree"
        )
    if type(capacity) is not int or capacity < 1024 or capacity > 1048576 or capacity % 64:
        raise ExperimentError("rl.packet_bytes must be a multiple of 64 between 1024 and 1048576")
    return capacity


def _observation_space(gym, np, capacity):
    return gym.spaces.Dict(
        {
            "packet": gym.spaces.Box(0, 256, shape=(capacity,), dtype=np.uint16),
            "numeric": gym.spaces.Box(-1.0, 1.0, shape=(NUMERIC_SIZE,), dtype=np.float32),
        }
    )


def _number(value, scale=1.0):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return math.tanh(value / scale)
    return 0.0


def encode_packet(packet: dict, capacity: int = DEFAULT_PACKET_BYTES) -> dict:
    """Losslessly encode JSON bytes; numeric channels derive only from that JSON."""
    try:
        import numpy as np
    except ImportError as exc:
        raise ExperimentError("RL requires numpy; install requirements-rl.txt") from exc
    required = {"observation", "memory", "goal", "feedback", "observation_id"}
    if not isinstance(packet, dict) or not required.issubset(packet):
        raise ExperimentError("RL requires the complete shared observation packet")
    if set(packet) != required | {"protocol"} or packet["protocol"] != PROTOCOL:
        raise ExperimentError("RL shared packet protocol or public fields mismatch")
    if not isinstance(packet["observation"], dict):
        raise ExperimentError("RL observation must be an object")
    raw = canonical_json(packet).encode("utf-8")
    if len(raw) > capacity:
        raise ExperimentError(
            f"shared packet needs {len(raw)} bytes, RL capacity is {capacity}; "
            "increase packet_bytes and retrain; no text was truncated"
        )
    encoded = np.zeros(capacity, dtype=np.uint16)
    encoded[: len(raw)] = np.frombuffer(raw, dtype=np.uint8).astype(np.uint16) + 1
    observation = packet["observation"]
    player = observation.get("player") or {}
    party = observation.get("party") or []
    if isinstance(party, dict):
        party = party.get("members") or []
    numeric = [
        len(raw) / capacity,
        _number(player.get("map_id"), 128),
        _number(player.get("x"), 64),
        _number(player.get("y"), 64),
        _number(player.get("money"), 100000),
        _number(len(party), 6),
    ]
    for index in range(6):
        member = party[index] if index < len(party) and isinstance(party[index], dict) else {}
        maximum = member.get("max_hp")
        hp_ratio = (
            member.get("hp", 0) / maximum
            if isinstance(maximum, (int, float)) and maximum > 0
            else 0
        )
        moves = member.get("moves") or []
        pp = sum(
            move.get("pp", 0)
            for move in moves
            if isinstance(move, dict) and isinstance(move.get("pp"), (int, float))
        )
        numeric.extend((_number(hp_ratio), _number(member.get("level"), 100), _number(pp, 160)))
    return {"packet": encoded, "numeric": np.asarray(numeric, dtype=np.float32)}


def _sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _contract(config):
    environment = config.get("environment", config)
    return {
        "protocol": PROTOCOL,
        "actions": list(ACTIONS),
        "held_frames": environment.get("held_frames", 8),
        "settle_frames": environment.get("settle_frames", 16),
        "observation_policy": "structured_player_v1",
        "encoding": ENCODING_VERSION,
        "packet_bytes": _capacity(config),
        "numeric_size": NUMERIC_SIZE,
        "architecture": {
            "features": 128,
            "byte_embedding": 8,
            "lstm_hidden_size": config.get("rl", {}).get("lstm_hidden_size", 64),
        },
        "public_packet_fields": ["observation", "memory", "goal", "feedback", "observation_id"],
    }


def _case_identity(case):
    return {"id": case["id"], "family": case["family"], "state_sha256": case["state_sha256"]}


def _read_metadata(checkpoint, config):
    checkpoint = Path(checkpoint)
    if not checkpoint.is_file():
        raise ExperimentError(f"RL checkpoint missing: {checkpoint}; train a policy first")
    metadata_path = checkpoint.with_suffix(".json")
    if not metadata_path.is_file():
        raise ExperimentError(f"RL checkpoint metadata missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ExperimentError("invalid RL checkpoint metadata") from exc
    if not isinstance(metadata, dict):
        raise ExperimentError("invalid RL checkpoint metadata object")
    if (
        metadata.get("metadata_version") != _METADATA_VERSION
        or metadata.get("protocol") != PROTOCOL
    ):
        raise ExperimentError("RL checkpoint protocol/metadata version mismatch")
    if (
        metadata.get("algorithm") != "sb3-contrib.RecurrentPPO"
        or metadata.get("trained") is not True
    ):
        raise ExperimentError("RL checkpoint is not a trained RecurrentPPO policy")
    if metadata.get("model_sha256") != _sha256(checkpoint):
        raise ExperimentError("RL checkpoint weight hash mismatch")
    if metadata.get("contract") != _contract(config):
        raise ExperimentError("RL checkpoint observation/action contract mismatch")
    training = metadata.get("training") or {}
    if not isinstance(training, dict) or not isinstance(training.get("cases"), list):
        raise ExperimentError("invalid RL training provenance")
    if (
        not training.get("cases")
        or not training.get("rom_sha1")
        or training.get("total_timesteps", 0) <= 0
        or training.get("updates", 0) <= 0
    ):
        raise ExperimentError("RL checkpoint is missing actual training provenance")
    for case in training["cases"]:
        if not isinstance(case, dict) or not all(
            isinstance(case.get(key), str) and case[key] for key in ("id", "family", "state_sha256")
        ):
            raise ExperimentError("invalid RL training case provenance")
    return metadata


def validate_checkpoint(checkpoint: Path, case: dict, config: dict) -> dict:
    """Validate identity before SB3 deserialization; no random-policy fallback."""
    metadata = _read_metadata(checkpoint, config)
    training = metadata["training"]
    if case.get("rom_sha1") != training["rom_sha1"]:
        raise ExperimentError("RL checkpoint ROM hash mismatch")
    if not case.get("family") or not case.get("state_sha256"):
        raise ExperimentError("evaluation case lacks state/family identity")
    for trained in training["cases"]:
        if case["state_sha256"] == trained.get("state_sha256"):
            raise ExperimentError("evaluation state hash was used for RL training")
        if case["family"] == trained.get("family"):
            raise ExperimentError("evaluation family was used for RL training")
    return metadata


class RLController:
    """Frozen held-out evaluation; recurrent state is scoped to one episode."""

    def __init__(self, checkpoint: Path, case: dict, config: dict):
        self.metadata = validate_checkpoint(checkpoint, case, config)
        dependencies = _dependencies()
        self.np = dependencies["np"]
        self.capacity = _capacity(config)
        self.model = dependencies["RecurrentPPO"].load(
            str(checkpoint), device=config.get("rl", {}).get("device", "cpu")
        )
        self.model.policy.set_training_mode(False)
        self.model.policy.requires_grad_(False)
        expected = _observation_space(dependencies["gym"], self.np, self.capacity)
        if (
            self.model.observation_space != expected
            or self.model.action_space.n != len(ACTIONS)
            or self.model.policy.lstm_actor.hidden_size
            != self.metadata["contract"]["architecture"]["lstm_hidden_size"]
        ):
            raise ExperimentError("serialized RL observation/action space mismatch")
        self.reset()

    def reset(self):
        self.state = None
        self.episode_start = self.np.ones((1,), dtype=bool)

    def decide(self, packet: dict) -> dict:
        started = time.monotonic()
        action, self.state = self.model.predict(
            encode_packet(packet, self.capacity),
            state=self.state,
            episode_start=self.episode_start,
            deterministic=True,
        )
        self.episode_start[:] = False
        index = int(self.np.asarray(action).item())
        if index not in range(len(ACTIONS)):
            raise ExperimentError("RL returned an invalid action")
        return {
            "button": ACTIONS[index],
            "owner": "rl",
            "source": "policy",
            "inference_seconds": time.monotonic() - started,
        }

    def close(self):
        self.reset()


def train(
    suite_path: Path,
    output: Path,
    config: dict,
    timesteps: int,
    num_envs: int = 1,
    seed: int = 0,
    resume: Path | None = None,
    rom: Path | None = None,
) -> dict:
    """Collect real environment rollouts, update PPO, and persist provenance."""
    from .suite import load_suite

    if type(seed) is not int or seed < 0:
        raise ExperimentError("seed must be a nonnegative integer")
    if type(timesteps) is not int or timesteps < 1 or type(num_envs) is not int or num_envs < 1:
        raise ExperimentError("timesteps and num_envs must be positive integers")
    suite_path, output = Path(suite_path), Path(output)
    suite = load_suite(suite_path)
    cases = [case for case in suite["cases"] if case["split"] == "train"]
    if not cases:
        raise ExperimentError("RL training requires independently registered train cases")
    rom_hashes = {case["rom_sha1"] for case in cases}
    if len(rom_hashes) != 1:
        raise ExperimentError("all RL train cases must use the same ROM hash")
    identities = sorted((_case_identity(case) for case in cases), key=lambda case: case["id"])
    previous = _read_metadata(resume, config) if resume else None
    if previous and previous.get("config_sha256") != digest(config):
        raise ExperimentError("resume requires the same training configuration hash")
    if previous and (
        previous["training"]["cases"] != identities
        or previous["training"]["rom_sha1"] != next(iter(rom_hashes))
    ):
        raise ExperimentError(
            "resume requires the identical training state hashes, families, and ROM"
        )
    if output.exists() and any(output.iterdir()):
        raise ExperimentError(
            "training output must be a new or empty directory; preserve prior evidence"
        )
    dependencies = _dependencies()
    torch = dependencies["torch"]
    rl_config = config.get("rl", {})
    threads = rl_config.get("torch_threads", 1)
    if type(threads) is not int or threads < 1:
        raise ExperimentError("rl.torch_threads must be a positive integer")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    initial_threads = torch.get_num_threads()
    torch.set_num_threads(threads)
    vector_env = None
    training_logger = None
    start_record = {
        "protocol": PROTOCOL,
        "suite_sha256": _sha256(suite_path),
        "training_config": _safe_training_config(config),
        "config_sha256": digest(config),
        "requested_timesteps": timesteps,
        "num_envs": num_envs,
        "seed": seed,
        "train_cases": identities,
        "resume": str(resume) if resume else None,
        "resume_model_sha256": previous["model_sha256"] if previous else None,
    }
    _write_json(output / "training-start.json", start_record)
    try:
        vector_env = dependencies["DummyVecEnv"](
            [
                lambda offset=offset: dependencies["TrainingEnv"](
                    cases, suite_path, rom, config, offset
                )
                for offset in range(num_envs)
            ]
        )
        vector_env.seed(seed)
        device = rl_config.get("device", "cpu")
        if resume:
            model = dependencies["RecurrentPPO"].load(
                str(resume), env=vector_env, device=device, seed=seed, force_reset=True
            )
        else:
            steps = rl_config.get("n_steps", 128)
            batch = rl_config.get("batch_size", 32)
            epochs = rl_config.get("n_epochs", 4)
            if (
                any(type(value) is not int or value < 1 for value in (steps, batch, epochs))
                or steps * num_envs < 2
                or batch < 2
                or batch > steps * num_envs
            ):
                raise ExperimentError(
                    "RL requires positive n_steps/n_epochs and 2 <= batch_size <= n_steps * num_envs"
                )
            model = dependencies["RecurrentPPO"](
                "MultiInputLstmPolicy",
                vector_env,
                n_steps=steps,
                batch_size=batch,
                n_epochs=epochs,
                learning_rate=rl_config.get("learning_rate", 0.0003),
                gamma=rl_config.get("gamma", 0.99),
                seed=seed,
                device=device,
                policy_kwargs={
                    "features_extractor_class": dependencies["PacketFeatures"],
                    "features_extractor_kwargs": {"features_dim": 128},
                    "lstm_hidden_size": rl_config.get("lstm_hidden_size", 64),
                    "net_arch": {"pi": [64], "vf": [64]},
                    "normalize_images": False,
                },
                verbose=0,
            )
        training_logger = dependencies["configure"](str(output), ["csv", "json"])
        model.set_logger(training_logger)
        before = int(model.num_timesteps)
        curve = output / "learning-curve.jsonl"
        curve.touch()
        callback = dependencies["CurveCallback"](curve, started, before, num_envs)
        initial_policy = _policy_digest(model)
        model.learn(timesteps, callback=callback, reset_num_timesteps=resume is None)
        final_policy = _policy_digest(model)
        if final_policy == initial_policy or int(model._n_updates) <= 0:
            raise ExperimentError(
                "PPO did not update policy parameters; no trained checkpoint published"
            )
        # SB3 normally logs before each update; explicitly flush the final update.
        model.logger.dump(step=model.num_timesteps)
        checkpoint = output / "policy.zip"
        model.save(str(checkpoint))
        metadata = {
            "metadata_version": _METADATA_VERSION,
            "protocol": PROTOCOL,
            "algorithm": "sb3-contrib.RecurrentPPO",
            "trained": True,
            "model_sha256": _sha256(checkpoint),
            "contract": _contract(config),
            "config_sha256": digest(config),
            "versions": {
                package: importlib.metadata.version(package)
                for package in ("sb3-contrib", "stable-baselines3", "gymnasium", "torch", "numpy")
            },
            "training": {
                "cases": identities,
                "rom_sha1": next(iter(rom_hashes)),
                "suite_sha256": _sha256(suite_path),
                "seed": seed,
                "num_envs": num_envs,
                "requested_timesteps": timesteps,
                "total_timesteps": int(model.num_timesteps),
                "collected_timesteps": int(model.num_timesteps) - before,
                "updates": int(model._n_updates),
                "completed_episodes": callback.episodes,
                "elapsed_seconds": time.monotonic() - started,
                "device": str(model.device),
                "torch_threads": threads,
                "initial_parameter_sha256": initial_policy,
                "final_parameter_sha256": final_policy,
                "resume_model_sha256": previous["model_sha256"] if previous else None,
                "cost": {"api_calls": 0, "api_tokens": 0, "local_compute_price": None},
            },
        }
        _write_json(checkpoint.with_suffix(".json"), metadata)
        return {
            "checkpoint": str(checkpoint),
            "metadata": str(checkpoint.with_suffix(".json")),
            **metadata,
        }
    except Exception as exc:
        _write_json(
            output / "training-error.json",
            {
                "error_type": type(exc).__name__,
                "elapsed_seconds": time.monotonic() - started,
            },
        )
        raise
    finally:
        if vector_env is not None:
            vector_env.close()
        if training_logger is not None:
            training_logger.close()
        torch.set_num_threads(initial_threads)


def _safe_training_config(config):
    """Only numeric learner/environment controls; never provider credentials."""
    environment = config.get("environment", config)
    rl_config = config.get("rl", {})
    numeric = lambda source, names: {
        name: source[name]
        for name in names
        if type(source.get(name)) in (int, float) and math.isfinite(source[name])
    }
    return {
        "environment": numeric(environment, ("held_frames", "settle_frames", "max_steps")),
        "reward": numeric(
            environment.get("reward", {}),
            ("task", "badge", "exploration", "action_cost", "exploration_limit"),
        ),
        "rl": numeric(
            rl_config,
            (
                "packet_bytes",
                "n_steps",
                "batch_size",
                "n_epochs",
                "lstm_hidden_size",
                "torch_threads",
                "learning_rate",
                "gamma",
            ),
        ),
    }


def _policy_digest(model):
    hasher = hashlib.sha256()
    for name, parameter in sorted(model.policy.state_dict().items()):
        hasher.update(name.encode("utf-8"))
        hasher.update(parameter.detach().cpu().contiguous().numpy().tobytes())
    return hasher.hexdigest()


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
