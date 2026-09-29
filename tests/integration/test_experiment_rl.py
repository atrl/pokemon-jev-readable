"""Actual PPO optimizer/save/load tests; synthetic and opt-in ROM rollouts.

The synthetic test verifies the learner pipeline, not Pokemon skill. Set
POKEMON_EXPERIMENT_SUITE to an independently registered train/test suite and
POKEMON_ROM to enable the real-ROM smoke as well.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pokemon"))
from pokemon.experiments.common import ACTIONS, PROTOCOL, ExperimentError
from pokemon.experiments.rl import RLController, _policy_digest, train

HAS_RL = all(
    importlib.util.find_spec(name) is not None for name in ("torch", "gymnasium", "sb3_contrib")
)


def packet(step=0):
    return {
        "protocol": PROTOCOL,
        "observation_id": str(step),
        "observation": {
            "player": {"x": step, "y": 0},
            "party": [],
            "dialog": {"text": "Press a. 按 A。"},
        },
        "memory": {"steps": step},
        "goal": "Synthetic optimizer check only",
        "feedback": {"steps": step, "reward": 0},
    }


class SyntheticEnv:
    seen_cases: ClassVar[list] = []

    def __init__(self, case, suite_path, rom=None, config=None):
        self.seen_cases.append(case["id"])
        self.steps = 0

    def reset(self):
        self.steps = 0
        return packet()

    def step(self, button):
        self.steps += 1
        return packet(self.steps), 1.0 if button == "a" else -0.01, self.steps == 4, False, {}

    def close(self):
        pass


@unittest.skipUnless(HAS_RL, "install pokemon/experiments/requirements-rl.txt")
class RecurrentPPOLifecycleTests(unittest.TestCase):
    def test_real_optimizer_save_load_frozen_reset_and_resume(self):
        cases = [
            {
                "id": str(index),
                "split": "train" if index < 3 else "test",
                "family": f"family-{index}",
                "state_sha256": str(index) * 64,
                "rom_sha1": "a" * 40,
            }
            for index in range(4)
        ]
        config = {
            "rl": {
                "packet_bytes": 1024,
                "n_steps": 8,
                "batch_size": 8,
                "n_epochs": 1,
                "lstm_hidden_size": 64,
                "torch_threads": 1,
            },
            "environment": {"max_steps": 4, "max_packet_bytes": 1024},
        }
        SyntheticEnv.seen_cases = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite_path = root / "suite.json"
            suite = {"protocol": PROTOCOL, "cases": cases}
            suite_path.write_text(json.dumps(suite))
            with (
                patch("pokemon.experiments.suite.load_suite", return_value=suite),
                patch("pokemon.experiments.environment.ExperimentEnv", SyntheticEnv),
            ):
                result = train(suite_path, root / "first", config, 16)
                self.assertEqual(set(SyntheticEnv.seen_cases), {"0", "1", "2"})
                evidence = result["training"]
                self.assertGreater(evidence["updates"], 0)
                self.assertEqual(evidence["collected_timesteps"], 16)
                self.assertNotEqual(
                    evidence["initial_parameter_sha256"], evidence["final_parameter_sha256"]
                )
                self.assertTrue((root / "first" / "learning-curve.jsonl").read_text().strip())
                controller = RLController(Path(result["checkpoint"]), cases[3], config)
                initial = _policy_digest(controller.model)
                first_action = controller.decide(packet())
                self.assertIn(first_action["button"], ACTIONS)
                self.assertEqual(first_action["owner"], "rl")
                self.assertFalse(controller.episode_start[0])
                self.assertIsNotNone(controller.state)
                controller.decide(packet(1))
                controller.reset()
                self.assertIsNone(controller.state)
                self.assertTrue(controller.episode_start[0])
                self.assertEqual(controller.decide(packet())["button"], first_action["button"])
                self.assertEqual(_policy_digest(controller.model), initial)
                self.assertTrue(
                    all(
                        not parameter.requires_grad
                        for parameter in controller.model.policy.parameters()
                    )
                )
                controller.close()
                with self.assertRaisesRegex(ExperimentError, "used for RL training"):
                    RLController(Path(result["checkpoint"]), cases[0], config)
                resumed = train(
                    suite_path, root / "resumed", config, 8, resume=Path(result["checkpoint"])
                )
                self.assertEqual(resumed["training"]["total_timesteps"], 24)
                self.assertEqual(resumed["training"]["collected_timesteps"], 8)
                self.assertEqual(resumed["training"]["resume_model_sha256"], result["model_sha256"])

    @unittest.skipUnless(
        os.environ.get("POKEMON_EXPERIMENT_SUITE"),
        "set POKEMON_EXPERIMENT_SUITE for real ROM smoke",
    )
    def test_real_rom_short_training(self):
        suite_path = Path(os.environ["POKEMON_EXPERIMENT_SUITE"])
        rom = Path(os.environ["POKEMON_ROM"]) if os.environ.get("POKEMON_ROM") else None
        config = {
            "environment": {"held_frames": 8, "settle_frames": 16, "max_steps": 8},
            "rl": {"n_steps": 8, "batch_size": 8, "n_epochs": 1, "torch_threads": 1},
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = train(suite_path, Path(temporary) / "real", config, 16, rom=rom)
            self.assertGreater(result["training"]["updates"], 0)
            self.assertNotEqual(
                result["training"]["initial_parameter_sha256"],
                result["training"]["final_parameter_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
