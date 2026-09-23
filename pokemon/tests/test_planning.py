"""Offline tests for deterministic stall signals and the advisory planner bridge."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

POKEMON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POKEMON))

import planning
from campaign import CampaignPlanner
from jev import build_request
from run import run

TEST_KEY = "fixture-only-not-a-real-key"
PROFILE = {"rom_sha1": "offline-fixture-rom-identity"}
OFFLINE_OBSERVATION = {
    "game": "OFFLINE TEST DOUBLE",
    "errors": [],
    "player": {"name": "TEST", "map_id": 61, "x": 25, "y": 12},
    "scene": {"mode": "overworld", "verified": True, "source": "fixture"},
    "dialog": {"open": False, "text": ""},
    "screen_text": {"rows": []},
    "party": [],
    "bag": [],
    "world": {"map_id": 61, "name": "MT_MOON_3", "width": 28, "height": 28},
    "milestones": {},
    "battle": {"active": False, "verified": True},
}


def observation(**overrides):
    base = {
        "game": "Pokemon Red Star 2020-08-18",
        "scene": {"mode": "overworld", "verified": True},
        "dialog": {"open": False, "text": ""},
        "screen_text": {"rows": []},
        "player": {"map_id": 61, "x": 25, "y": 12},
        "world": {"map_id": 61, "name": "MT_MOON_3", "width": 28, "height": 28},
        "party": [
            {
                "species_name_prior": "CHARMELEON",
                "level": 20,
                "hp": 40,
                "max_hp": 55,
                "status_bits": 0,
                "moves": [{"knowledge": {"name": "EMBER"}, "pp": 20}],
            }
        ],
        "bag": [],
        "milestones": {"badge_count": {"value": 1, "verified": True}},
        "battle": {"active": False},
    }
    base.update(overrides)
    return base


def campaign_context(**overrides):
    base = {
        "active_objective": {"id": "cascade_badge", "intent": "到华蓝市", "target_map_id": 65},
        "navigation": {"status": "toward_unseen_target"},
        "visited_map_ids": [59, 60, 61],
        "route_map_names": ["MT_MOON_1", "MT_MOON_2", "MT_MOON_3"],
        "objective_history": [],
        "plan": None,
    }
    base.update(overrides)
    return base


def progress_context(**overrides):
    base = {
        "total_steps": 500,
        "loop_detected": False,
        "loop_kind": None,
        "steps_since_new_tile": 10,
        "same_position_steps": 0,
        "visited_tiles": 70,
        "recent_effects": [],
    }
    base.update(overrides)
    return base


class PokeballTests(unittest.TestCase):
    def test_only_verified_balls_are_counted(self):
        bag = [
            {"item_id": 3, "name_prior": "POKé BALL@", "quantity": 5, "name_verified": True},
            {"item_id": 3, "name_prior": "POKé BALL@", "quantity": 5, "name_verified": False},
            {"item_id": 13, "name_prior": "POTION@", "quantity": 2, "name_verified": True},
            {"item_id": 2, "name_prior": "GREAT BALL@", "quantity": 1, "name_verified": True},
        ]
        self.assertEqual(planning.pokeball_count(bag), 6)
        self.assertEqual(planning.pokeball_count(None), 0)
        self.assertEqual(planning.pokeball_count([]), 0)


class SituationTests(unittest.TestCase):
    def test_situation_is_compact_and_verified(self):
        situation = planning.build_situation(
            observation(), campaign_context(), progress_context()
        )
        self.assertEqual(situation["map"]["name"], "MT_MOON_3")
        self.assertEqual(situation["position"], [25, 12])
        self.assertEqual(situation["pokeballs"], 0)
        self.assertEqual(situation["badges"], 1)
        self.assertEqual(situation["party"][0]["species"], "CHARMELEON")
        self.assertFalse(situation["plan_active"])

    def test_triggers_fire_on_stall_signals(self):
        situation = planning.build_situation(
            observation(),
            campaign_context(),
            progress_context(loop_detected=True, steps_since_new_tile=200),
        )
        reasons = planning.planning_reasons(situation)
        self.assertIn("loop_detected", reasons)
        self.assertIn("no_new_tile", reasons)
        self.assertTrue(planning.needs_planning(situation))

    def test_active_plan_and_cooldown_suppress_replanning(self):
        stalled = planning.build_situation(
            observation(), campaign_context(plan={"subgoal": "x"}), progress_context(loop_detected=True)
        )
        self.assertFalse(planning.needs_planning(stalled))
        cooling = planning.build_situation(
            observation(), campaign_context(), progress_context(loop_detected=True)
        )
        cooling["steps_since_plan"] = 5
        self.assertFalse(planning.needs_planning(cooling))

    def test_healthy_state_needs_no_planning(self):
        situation = planning.build_situation(
            observation(
                bag=[{"item_id": 3, "name_prior": "POKé BALL@", "quantity": 3, "name_verified": True}],
                party=[
                    {"species_name_prior": "CHARMELEON", "level": 20, "hp": 55, "max_hp": 55,
                     "status_bits": 0, "moves": []},
                    {"species_name_prior": "PIDGEY", "level": 8, "hp": 20, "max_hp": 20,
                     "status_bits": 0, "moves": []},
                ],
            ),
            campaign_context(),
            progress_context(),
        )
        self.assertFalse(planning.needs_planning(situation))


class PlanValidationTests(unittest.TestCase):
    def test_valid_plan_is_normalized(self):
        plan = planning.valid_plan(
            {
                "subgoal": "buy_balls",
                "intent": "去深灰商店买精灵球",
                "reasoning": "单宠且无球",
                "target_map_id": 56,
                "resource_policy": {"wild_battle": "run"},
                "expires_steps": 99999,
            }
        )
        self.assertEqual(plan["subgoal"], "buy_balls")
        self.assertEqual(plan["target_map_id"], 56)
        self.assertEqual(plan["resource_policy"]["wild_battle"], "run")
        self.assertEqual(plan["expires_steps"], 1000)

    def test_invalid_plans_are_rejected(self):
        for bad in (
            None,
            {},
            {"subgoal": "x"},
            {"subgoal": "x", "intent": "y", "resource_policy": {"wild_battle": "dance"}},
            {"subgoal": "x", "intent": "y", "target_map_id": "56"},
        ):
            with self.assertRaises(ValueError):
                planning.valid_plan(bad)

    def test_missing_key_raises_without_network(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                planning.call_planner({"step": 1}, "goal")


class CampaignPlanTests(unittest.TestCase):
    def test_plan_is_stored_used_and_expired(self):
        planner = CampaignPlanner()
        planner.set_plan(
            {
                "subgoal": "buy_balls",
                "intent": "去深灰商店买精灵球",
                "target_map_id": 56,
                "selectors": [{"kind": "object", "sprite": "SPRITE_MART_GUY", "x": 0, "y": 5}],
                "expires_steps": 10,
                "resource_policy": {"wild_battle": "run"},
            }
        )
        self.assertEqual(planner.plan["created_step"], 0)
        objective = planner._plan_objective(observation(), {"id": "cascade_badge", "completed_ids": []})
        self.assertEqual(objective["id"], "plan:buy_balls")
        self.assertEqual(objective["target_map_id"], 56)
        self.assertEqual(objective["source"]["quality"], "planner_model_advisory")
        planner.steps = 5
        self.assertFalse(planner._plan_expired(observation()))
        planner.steps = 10
        self.assertTrue(planner._plan_expired(observation()))

    def test_plan_survives_snapshot_roundtrip(self):
        planner = CampaignPlanner()
        planner.set_plan({"subgoal": "cross_mt_moon", "intent": "穿过月见山", "expires_steps": 40})
        restored = CampaignPlanner(planner.snapshot())
        self.assertEqual(restored.plan["subgoal"], "cross_mt_moon")


class PromptPlanTests(unittest.TestCase):
    def _battle(self, **overrides):
        battle = {
            "active": True,
            "verified": True,
            "type": "wild",
            "phase": "active",
            "combatants_ready": True,
            "menu": "command",
            "selected_command": "FIGHT",
            "player": {"species_name_prior": "CHARMELEON", "level": 20, "hp": 40, "max_hp": 55},
            "enemy": {"species_name_prior": "ZUBAT", "level": 12, "hp": 30, "max_hp": 30},
        }
        battle.update(overrides)
        return battle

    def test_wild_battle_defaults_to_fleeing(self):
        raw = observation(
            scene={"mode": "battle", "verified": True},
            battle=self._battle(),
            campaign={"active_objective": {"id": "cascade_badge", "intent": "到华蓝市"}},
        )
        request = build_request(raw, "Complete the story", [])
        focus = request["state"]["current_focus"]
        self.assertIn("逃跑", focus)
        self.assertIn("RUN is the bottom-right", request["questions"]["button"]["criteria"]["down"]["current_battle_advice"])
        self.assertEqual(
            request["state"]["game"]["battle"]["battle_policy"]["policy"], "run"
        )

    def test_planner_catch_policy_uses_item_when_balls_present(self):
        raw = observation(
            scene={"mode": "battle", "verified": True},
            battle=self._battle(),
            bag=[{"item_id": 3, "name_prior": "POKé BALL@", "quantity": 4, "name_verified": True}],
            campaign={
                "active_objective": {"id": "plan:catch", "intent": "抓一只"},
                "plan": {"subgoal": "catch", "intent": "抓一只备用宠", "resource_policy": {"wild_battle": "catch"}},
            },
        )
        request = build_request(raw, "Complete the story", [])
        self.assertIn("精灵球", request["state"]["current_focus"])
        self.assertIn("ITEM", request["questions"]["button"]["criteria"]["up"]["current_battle_advice"])

    def test_plan_intent_reaches_overworld_focus(self):
        raw = observation(
            campaign={
                "active_objective": {"id": "plan:buy", "intent": "去深灰商店买球"},
                "plan": {"subgoal": "buy_balls", "intent": "去深灰商店买球"},
            }
        )
        request = build_request(raw, "Complete the story", [])
        self.assertIn("去深灰商店买球", request["state"]["current_focus"])
        self.assertEqual(request["state"]["campaign"]["plan"]["subgoal"], "buy_balls")


class RunLoopPlannerTests(unittest.TestCase):
    def test_run_loop_records_plan_and_feeds_it_forward(self):
        plan = {
            "subgoal": "exit_mt_moon_3",
            "intent": "从月见山 3 层走到 2 层",
            "target_map_id": 60,
            "resource_policy": {"wild_battle": "run"},
            "expires_steps": 50,
            "model": "test-double",
        }

        def choose(_before, _goal, _history, *, on_event):
            on_event({"type": "jev_response", "attempt": 1, "latency_ms": 5, "httpStatus": 200})
            return {"answer": {"choice": "a"}, "source": "offline-test-double"}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            world = Mock()
            world.game = SimpleNamespace(frame_count=0)
            world.save.return_value = b"offline state"
            reader = Mock()
            reader.snapshot.return_value = OFFLINE_OBSERVATION
            with (
                patch.dict(os.environ, {"TYPESAFE_API_KEY": TEST_KEY}),
                patch("run.Emulator", return_value=world),
                patch("run.Reader", return_value=reader),
                patch("run.load_profile", return_value=PROFILE),
                patch("run.choose", side_effect=choose),
                patch("planning.needs_planning", side_effect=[True, False, False]),
                patch("planning.planner_configured", return_value=True),
                patch("planning.call_planner", return_value=plan) as planner,
            ):
                report = run(Path("NO-ROM.gb"), output, goal="Offline", steps=2)
            rows = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
            types = [row["type"] for row in rows]
            self.assertEqual(types.count("plan"), 1)
            self.assertIn("planning_requested", types)
            self.assertEqual(report["plans"], 1)
            self.assertEqual(report["plan_subgoal"], "exit_mt_moon_3")
            planner.assert_called_once()
            saved = json.loads((output / "last.campaign.json").read_text())["campaign"]
            self.assertEqual(saved["plan"]["subgoal"], "exit_mt_moon_3")


if __name__ == "__main__":
    unittest.main()
