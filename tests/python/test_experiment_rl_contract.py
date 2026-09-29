"""Dependency-light checks for policy information parity and provenance gates."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401

from pokemon.experiments.common import PROTOCOL, ExperimentError, canonical_json, digest
from pokemon.experiments.rl import (
    _contract,
    encode_packet,
    validate_checkpoint,
)


def public_packet():
    return {
        "protocol": PROTOCOL,
        "observation_id": "same-observation",
        "observation": {
            "player": {"x": 3, "y": 4},
            "party": [],
            "dialog": {"text": "洞口还未发现。Use another MOVE!"},
        },
        "memory": {"dialogues": ["Earlier complete dialogue"], "maps": []},
        "goal": "Leave the current observed map",
        "feedback": {"reward": 0.0},
    }


class PacketEncodingTests(unittest.TestCase):
    def test_complete_utf8_packet_is_recoverable(self):
        packet = public_packet()
        observation = encode_packet(packet)
        nonpadding = observation["packet"][observation["packet"] != 0]
        decoded = bytes(int(value) - 1 for value in nonpadding).decode("utf-8")
        self.assertEqual(decoded, canonical_json(packet))
        self.assertEqual(json.loads(decoded), packet)
        self.assertEqual(observation["numeric"].shape, (24,))

    def test_overflow_never_drops_dialogue(self):
        packet = public_packet()
        packet["observation"]["dialog"]["text"] = "完整对白" * 1000
        with self.assertRaisesRegex(ExperimentError, "no text was truncated"):
            encode_packet(packet, 1024)

    def test_missing_feedback_and_extra_private_fields_rejected(self):
        packet = public_packet()
        del packet["feedback"]
        with self.assertRaises(ExperimentError):
            encode_packet(packet)
        packet = public_packet()
        packet["hidden_event_bits"] = [1]
        with self.assertRaises(ExperimentError):
            encode_packet(packet)


class CheckpointProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkpoint = Path(self.temporary.name) / "policy.zip"
        self.checkpoint.write_bytes(b"not a real policy - metadata gate test only")
        self.case = {
            "id": "test",
            "family": "held-out",
            "state_sha256": "2" * 64,
            "rom_sha1": "a" * 40,
            "split": "test",
        }
        self.metadata = {
            "metadata_version": 1,
            "protocol": PROTOCOL,
            "algorithm": "sb3-contrib.RecurrentPPO",
            "trained": True,
            "model_sha256": hashlib.sha256(self.checkpoint.read_bytes()).hexdigest(),
            "contract": _contract({}),
            "config_sha256": digest({}),
            "training": {
                "cases": [{"id": "train", "family": "train-family", "state_sha256": "1" * 64}],
                "rom_sha1": "a" * 40,
                "total_timesteps": 16,
                "updates": 2,
            },
        }
        self.write()

    def write(self):
        self.checkpoint.with_suffix(".json").write_text(json.dumps(self.metadata))

    def test_valid_identity_without_loading_weights(self):
        self.assertEqual(validate_checkpoint(self.checkpoint, self.case, {}), self.metadata)

    def test_budget_and_provider_config_are_not_policy_contract(self):
        validate_checkpoint(
            self.checkpoint,
            self.case,
            {"budget": {"max_steps": 99}, "deepseek": {"model": "unused"}},
        )

    def test_state_and_family_leakage_rejected_independently(self):
        for field, value in (("state_sha256", "1" * 64), ("family", "train-family")):
            case = {**self.case, field: value}
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ExperimentError, "used for RL training"),
            ):
                validate_checkpoint(self.checkpoint, case, {})

    def test_wrong_rom_and_execution_contract_rejected(self):
        with self.assertRaisesRegex(ExperimentError, "ROM hash mismatch"):
            validate_checkpoint(self.checkpoint, {**self.case, "rom_sha1": "b" * 40}, {})
        with self.assertRaisesRegex(ExperimentError, "contract mismatch"):
            validate_checkpoint(self.checkpoint, self.case, {"environment": {"held_frames": 9}})
        with self.assertRaisesRegex(ExperimentError, "contract mismatch"):
            validate_checkpoint(self.checkpoint, self.case, {"rl": {"packet_bytes": 131072}})

    def test_tampered_weights_and_untrained_metadata_rejected(self):
        self.checkpoint.write_bytes(b"different weights")
        with self.assertRaisesRegex(ExperimentError, "weight hash mismatch"):
            validate_checkpoint(self.checkpoint, self.case, {})
        self.metadata["trained"] = False
        self.write()
        with self.assertRaisesRegex(ExperimentError, "not a trained"):
            validate_checkpoint(self.checkpoint, self.case, {})

    def test_missing_checkpoint_or_metadata_does_not_fall_back(self):
        self.checkpoint.with_suffix(".json").unlink()
        with self.assertRaisesRegex(ExperimentError, "metadata missing"):
            validate_checkpoint(self.checkpoint, self.case, {})
        self.checkpoint.unlink()
        with self.assertRaisesRegex(ExperimentError, "checkpoint missing"):
            validate_checkpoint(self.checkpoint, self.case, {})

    def test_missing_training_update_evidence_is_rejected(self):
        self.metadata["training"]["updates"] = 0
        self.write()
        with self.assertRaisesRegex(ExperimentError, "actual training provenance"):
            validate_checkpoint(self.checkpoint, self.case, {})


if __name__ == "__main__":
    unittest.main()
