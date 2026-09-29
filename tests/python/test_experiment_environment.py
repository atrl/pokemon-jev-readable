"""Explicit synthetic transitions test contracts, never model gameplay performance."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from _paths import ROOT  # noqa: F401
from pokemon.experiments.common import ACTIONS, PROTOCOL, ExperimentError, canonical_json
from pokemon.experiments.context import EpisodeMemory
from pokemon.experiments.environment import ExperimentEnv
from pokemon.experiments.evaluation import Evaluator
from pokemon.experiments.suite import get_case, load_suite, register_case, validate_suite
from perception import project


def observation(x=4, map_id=38, badges=0):
    return {
        "game": "EXPLICIT SYNTHETIC FIXTURE",
        "frame": 100,
        "errors": {},
        "player": {"map_id": map_id, "x": x, "y": 4, "badge_bits": badges},
        "scene": {"mode": "overworld", "verified": True},
        "dialog": {"open": False, "text": "", "awaiting_input": False},
        "screen_text": {"rows": []},
        "party": [],
        "party_state": {"ready": True},
        "bag": [],
        "battle": {"active": False, "verified": True},
        "milestones": {
            "party_count": {"value": 0, "verified": True},
            "badge_bits": {"value": badges, "verified": True},
            "badge_count": {"value": badges.bit_count(), "verified": True},
            "hidden_story": {"value": "secret", "verified": True},
        },
        "world": {
            "map_id": map_id,
            "width": 100,
            "height": 30,
            "source_match": True,
            "player_position_valid": True,
            "objects": [],
            "warps": [],
            "input_lock": {},
            "script_triggers": [{"answer": "hidden"}],
        },
        "local_map": {
            "verified": True,
            "quality": "advisory_background_only",
            "rows": ["...", ".@.", "..."],
            "player_cell": {"x": 1, "y": 1},
        },
    }


def checkpoint(root, content=b"EXPLICIT FAKE SAVE", rom=b"EXPLICIT FAKE ROM"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "last.state"
    path.write_bytes(content)
    path.with_suffix(".state.json").write_text(
        json.dumps(
            {
                "state_sha256": hashlib.sha256(content).hexdigest(),
                "rom_sha1": hashlib.sha1(rom).hexdigest(),
                "step": 888,
            }
        )
    )
    return path


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = checkpoint(self.root / "original")
        self.path = self.root / "suite" / "suite.json"

    def register(self, **kwargs):
        values = dict(
            suite_path=self.path,
            case_id="case-1",
            family="session-1",
            split="train",
            state=self.state,
            goal="Explore the observed room",
            success={"type": "map_changed"},
        )
        return register_case(**{**values, **kwargs})

    def test_registration_copies_only_verified_state_and_preserves_original(self):
        original = {p.name: p.read_bytes() for p in self.state.parent.iterdir()}
        (self.state.parent / "last.campaign.json").write_text('{"ui_steps":["a"]}')
        case = self.register()
        self.assertEqual(get_case(load_suite(self.path), "case-1"), case)
        self.assertEqual((self.path.parent / case["state"]).read_bytes(), self.state.read_bytes())
        self.assertFalse((self.path.parent / "states" / "last.campaign.json").exists())
        for name, content in original.items():
            self.assertEqual((self.state.parent / name).read_bytes(), content)

    def test_same_hash_cannot_cross_splits_even_with_new_name_and_family(self):
        self.register()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "state hash"):
            self.register(case_id="test-copy", family="renamed", split="test")
        self.assertEqual(self.path.read_bytes(), before)

    def test_family_cannot_cross_splits_even_with_different_state(self):
        self.register()
        other = checkpoint(self.root / "other", b"DIFFERENT FAKE SAVE")
        with self.assertRaisesRegex(ValueError, "session family"):
            self.register(case_id="test-adjacent-frame", split="test", state=other)

    def test_tampered_source_or_registered_copy_is_rejected(self):
        self.state.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.register()
        self.state = checkpoint(self.state.parent)
        case = self.register()
        (self.path.parent / case["state"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "manifest"):
            load_suite(self.path)

    def test_suite_cannot_escape_its_directory(self):
        case = self.register()
        for path in ("../original/last.state", str(self.state.resolve())):
            case["state"] = path
            with self.assertRaises(ValueError):
                validate_suite({"protocol": PROTOCOL, "cases": [case]})

    def test_duplicate_id_and_unknown_success_arguments_rejected(self):
        self.register()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.register()
        with self.assertRaises(ValueError):
            self.register(case_id="new", success={"type": "map_changed", "map_id": 1})


class EvaluationTests(unittest.TestCase):
    def evaluate(self, evaluator, raw):
        return evaluator.step(raw, project(raw), "a")

    def test_task_reward_is_once_and_success_requires_verified_position(self):
        raw = observation()
        evaluator = Evaluator({"type": "map_entered", "map_id": 99})
        evaluator.reset(raw, project(raw))
        target = observation(map_id=99)
        target["world"]["player_position_valid"] = False
        self.assertFalse(self.evaluate(evaluator, target)[1])
        target["world"]["player_position_valid"] = True
        first = self.evaluate(evaluator, target)
        second = self.evaluate(evaluator, target)
        self.assertTrue(first[1])
        self.assertEqual(first[2]["reward_components"]["task"], 10)
        self.assertEqual(second[2]["reward_components"]["task"], 0)

    def test_badges_are_deduplicated_even_if_bits_disappear_and_return(self):
        raw = observation(badges=1)
        evaluator = Evaluator({"type": "map_changed"})
        evaluator.reset(raw, project(raw))
        self.assertEqual(self.evaluate(evaluator, observation(badges=3))[2]["new_badges"], 1)
        self.evaluate(evaluator, observation(badges=0))
        self.assertEqual(self.evaluate(evaluator, observation(badges=3))[2]["new_badges"], 0)

    def test_exploration_is_first_visit_capped_and_healing_has_no_reward(self):
        raw = observation()
        evaluator = Evaluator({"type": "map_changed"}, {"exploration_limit": 1})
        evaluator.reset(raw, project(raw))
        self.assertEqual(
            self.evaluate(evaluator, observation(x=5))[2]["reward_components"]["exploration"], 0.01
        )
        self.assertEqual(
            self.evaluate(evaluator, observation(x=6))[2]["reward_components"]["exploration"], 0
        )
        healed = observation(x=4)
        healed["milestones"]["party_fully_healed"] = {"verified": True, "value": True}
        reward, _, info = self.evaluate(evaluator, healed)
        self.assertEqual(info["reward_components"]["exploration"], 0)
        self.assertEqual(reward, -0.001)

    def test_completed_or_inapplicable_initial_tasks_are_rejected(self):
        raw = observation()
        for success in (
            {"type": "map_entered", "map_id": 38},
            {"type": "battle_finished"},
            {"type": "dialog_closed"},
        ):
            with self.assertRaises(ValueError):
                Evaluator(success).reset(raw, project(raw))

    def test_hidden_completion_requires_independent_verified_evidence(self):
        raw = observation()
        evaluator = Evaluator({"type": "game_completed"})
        evaluator.reset(raw, project(raw))
        raw["milestones"]["game_completed"] = {"value": True, "verified": False}
        self.assertFalse(self.evaluate(evaluator, raw)[1])
        raw["milestones"]["game_completed"]["verified"] = True
        self.assertTrue(self.evaluate(evaluator, raw)[1])


class MemoryTests(unittest.TestCase):
    def test_accumulated_map_keeps_old_cells_beyond_current_viewport(self):
        memory = EpisodeMemory()
        before, after = project(observation(x=4)), project(observation(x=70))
        memory.observe(before)
        memory.record("right", before, after)
        context = memory.context(after)
        cells = context["maps"][0]["cells"]
        self.assertIn([3, 3, "."], cells)
        self.assertIn([69, 3, "."], cells)
        self.assertEqual(len(context["recent_actions"]), 1)

    def test_old_dialogue_is_retrieved_when_returning_to_its_map(self):
        memory = EpisodeMemory()
        for index in range(50):
            raw = observation(map_id=38 if index == 0 else 40)
            raw["scene"]["mode"] = "dialog"
            raw["dialog"] = {
                "open": True,
                "text": f"observed dialogue {index}",
                "awaiting_input": True,
            }
            memory.observe(project(raw))
            memory.experience.steps += 1
        texts = [row["text"] for row in memory.context(project(observation()))["dialogues"]]
        self.assertIn("observed dialogue 0", texts)
        self.assertIn("observed dialogue 49", texts)
        self.assertEqual(EpisodeMemory().context(project(observation()))["maps"], [])


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.rom = root / "fixture.gb"
        self.rom.write_bytes(b"EXPLICIT FAKE ROM")
        state = checkpoint(root / "original")
        self.suite = root / "suite.json"
        self.case = register_case(
            self.suite,
            "case",
            "session",
            "test",
            state,
            "Explore current observations",
            {"type": "map_entered", "map_id": 99},
        )
        self.world = Mock()
        self.reader = Mock()
        self.reader.snapshot.side_effect = lambda: deepcopy(observation())
        for target, kwargs in [
            ("Emulator", {"return_value": self.world}),
            ("Reader", {"return_value": self.reader}),
            ("load_profile", {"return_value": {"rom_sha1": self.case["rom_sha1"]}}),
        ]:
            patcher = patch("pokemon.experiments.environment." + target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, **config):
        env = ExperimentEnv(self.case, self.suite, self.rom, config)
        self.addCleanup(env.close)
        return env

    def test_all_nine_actions_have_identical_timing_and_single_input_contract(self):
        env = self.env()
        env.reset()
        for button in ACTIONS:
            env.step(button)
            self.world.press.assert_called_with(button, held=8, settle=16)
        for invalid in ("a,b", ["a", "b"], None):
            with self.assertRaises(ValueError):
                env.step(invalid)

    def test_reset_clears_memory_and_hidden_evaluator_spec_never_enters_packet(self):
        env = self.env()
        first = env.reset()
        packet, *_ = env.step("right")
        self.assertEqual(packet["memory"]["steps"], 1)
        self.assertEqual(first, env.reset())
        text = json.dumps(first)
        for hidden in ("hidden_story", "script_triggers", "map_entered", "ui_steps", "campaign"):
            self.assertNotIn(hidden, text)
        other_case = {**self.case, "success": {"type": "map_entered", "map_id": 98}}
        other = ExperimentEnv(other_case, self.suite, self.rom)
        try:
            self.assertEqual(first, other.reset())
        finally:
            other.close()

    def test_step_budget_cannot_be_reset_by_controller_feedback(self):
        env = self.env(max_steps=1)
        packet = env.reset()
        packet["feedback"]["steps"] = -999
        _, _, terminated, truncated, info = env.step("wait")
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(info["steps"], 1)
        with self.assertRaises(RuntimeError):
            env.step("a")

    def test_protocol_rejects_timing_variants(self):
        for config in ({"held_frames": 9}, {"settle_frames": 24}):
            with self.assertRaisesRegex(ValueError, "protocol requires"):
                self.env(**config)

    def test_actions_remain_counted_if_observation_fails_after_input(self):
        env = self.env()
        env.reset()
        self.reader.snapshot.side_effect = lambda: {"errors": {"memory": "bad snapshot"}}
        with self.assertRaises(RuntimeError):
            env.step("a")
        self.assertEqual(env.attempted_actions, 1)
        self.assertEqual(env.executed_actions, 1)
        env.close()
        self.assertEqual(env.executed_actions, 1)

    def test_partial_execution_is_an_attempt_not_a_completed_input(self):
        env = self.env()
        env.reset()
        self.world.press.side_effect = RuntimeError("emulator stopped during input")
        with self.assertRaises(RuntimeError):
            env.step("a")
        self.assertEqual(env.attempted_actions, 1)
        self.assertEqual(env.executed_actions, 0)

    def test_reader_errors_close_on_reset_instead_of_leaking_raw_data(self):
        env = self.env()
        self.reader.snapshot.side_effect = lambda: {"errors": {"private": "sensitive RAM dump"}}
        with self.assertRaisesRegex(RuntimeError, "sanity checks") as raised:
            env.reset()
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertIsNone(env.world)
        self.world.close.assert_called_once()

    def test_shared_capacity_counts_utf8_bytes_and_accepts_exact_boundary(self):
        self.case["goal"] = "观察当前房间"
        packet = self.env().reset()
        size = len(canonical_json(packet).encode("utf-8"))
        self.assertGreater(size, len(canonical_json(packet)))
        self.assertEqual(self.env(max_packet_bytes=size).reset(), packet)
        env = self.env(max_packet_bytes=size - 1)
        with self.assertRaisesRegex(ExperimentError, "^observation_packet_capacity$"):
            env.reset()
        self.assertIsNone(env.world)


if __name__ == "__main__":
    unittest.main()
