"""Offline mechanics/menu fixtures; no JEV, emulator input or battle outcome claims."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from battle_strategy import _damage_distribution, plan_battle
from world_data import move_prior, type_effectiveness


def move(move_id, slot, defender_types, pp=20):
    knowledge = move_prior(move_id)
    knowledge["numeric_data_verified"] = True  # Explicit offline verified-input fixture.
    return {
        "slot": slot,
        "move_id": move_id,
        "pp": pp,
        "knowledge": knowledge,
        "effectiveness": type_effectiveness(knowledge["type_id"], defender_types, verified=True),
    }


def battle(defender_types=(4,), defense=14, special=13, hp=40):
    return {
        "active": True,
        "verified": True,
        "type": "trainer",
        "menu": "move",
        "selected_move_slot": 0,
        "selected_command": None,
        "player": {
            "hp": 25,
            "max_hp": 30,
            "level": 12,
            "attack": 20,
            "defense": 18,
            "special": 20,
            "verified_stats": True,
            "types_verified": True,
            "types": [{"id": 20}],
            "moves": [
                move(10, 0, defender_types),
                move(45, 1, defender_types),
                move(52, 2, defender_types),
            ],
        },
        "enemy": {
            "hp": hp,
            "max_hp": hp,
            "level": 12,
            "attack": 18,
            "defense": defense,
            "special": special,
            "status_bits": 0,
            "verified_stats": True,
            "types_verified": True,
            "types": [{"id": typ} for typ in defender_types],
        },
    }


class BattleStrategyTests(unittest.TestCase):
    def test_diglett_uses_ember_special_and_stab_instead_of_repeated_scratch(self):
        result = plan_battle(battle())
        self.assertEqual(result["recommended_move"], "EMBER")
        self.assertEqual(result["recommended_slot"], 2)
        self.assertEqual(result["next_button"], "down")
        ember, scratch = [row for row in result["ranked_moves"] if row["eligible"]]
        self.assertEqual(ember["category"], "special")
        self.assertEqual(ember["stab"], 1.5)
        self.assertEqual(ember["type_effectiveness"], 1)
        self.assertEqual(scratch["category"], "physical")
        self.assertGreater(ember["expected_damage"], scratch["expected_damage"])

    def test_geodude_resists_both_but_low_special_still_favors_ember(self):
        result = plan_battle(battle((5, 4), defense=35, special=13))
        self.assertEqual(result["recommended_slot"], 2)
        damage = {row["name"]: row for row in result["ranked_moves"]}
        self.assertEqual(damage["EMBER"]["type_effectiveness"], 0.5)
        self.assertEqual(damage["SCRATCH"]["type_effectiveness"], 0.5)
        self.assertGreater(damage["EMBER"]["damage_min"], damage["SCRATCH"]["damage_max"])

    def test_read_only_observed_diglett_stat_values_preserve_the_narrow_ember_advantage(self):
        # Copied numeric values from the immutable trainer-intro checkpoint;
        # this is a fixture, not execution of that battle or proof of a win.
        state = battle((4,), defense=12, special=16, hp=17)
        state["player"].update(hp=21, max_hp=28, level=10, attack=16, special=15)
        result = plan_battle(state)
        values = {row["name"]: row for row in result["ranked_moves"]}
        self.assertEqual(result["recommended_move"], "EMBER")
        self.assertEqual((values["EMBER"]["damage_min"], values["EMBER"]["damage_max"]), (7, 9))
        self.assertEqual((values["SCRATCH"]["damage_min"], values["SCRATCH"]["damage_max"]), (6, 8))
        self.assertAlmostEqual(values["EMBER"]["expected_damage"], 7.7389, places=4)
        self.assertIn("not battle win probability", values["EMBER"]["probability_scope"])

    def test_brock_onix_high_defense_is_not_treated_as_special_defense(self):
        result = plan_battle(battle((5, 4), defense=50, special=15))
        self.assertEqual(result["recommended_move"], "EMBER")
        ember = result["ranked_moves"][0]
        self.assertEqual(ember["defense_stat"], "special")
        self.assertEqual(ember["defense"], 15)
        self.assertIn("special 20 / enemy special 15", ember["explanation"])

    def test_recommendation_is_computed_not_a_hardcoded_preference_for_ember(self):
        state = battle()
        state["player"].update(attack=200, special=1)
        self.assertEqual(plan_battle(state)["recommended_move"], "SCRATCH")

    def test_empty_pp_and_status_moves_are_excluded(self):
        state = battle()
        state["player"]["moves"][2]["pp"] = 0
        result = plan_battle(state)
        self.assertEqual(result["recommended_slot"], 0)
        self.assertEqual(result["next_button"], "a")
        reasons = {row["name"]: row.get("exclusion_reason") for row in result["ranked_moves"]}
        self.assertEqual(reasons["EMBER"], "no_pp")
        self.assertEqual(reasons["GROWL"], "no_direct_damage")
        state["player"]["moves"][0]["pp"] = 0
        result = plan_battle(state)
        self.assertIsNone(result["recommended_slot"])
        self.assertIsNone(result["next_button"])
        self.assertEqual(result["status"], "no_usable_estimated_damage_move")

    def test_type_immunity_never_receives_positive_damage_estimate(self):
        state = battle((8,))
        state["player"]["moves"] = [state["player"]["moves"][0]]
        result = plan_battle(state)
        self.assertIsNone(result["recommended_slot"])
        self.assertEqual(result["ranked_moves"][0]["exclusion_reason"], "target_type_immunity")

    def test_unknown_move_numbers_chart_stats_or_attacker_type_do_not_invent_neutral_damage(self):
        for missing in ("numbers", "chart", "stats", "types"):
            state = battle()
            state["player"]["moves"] = [state["player"]["moves"][2]]
            if missing == "numbers":
                state["player"]["moves"][0]["knowledge"]["numeric_data_verified"] = False
            elif missing == "chart":
                state["player"]["moves"][0]["effectiveness"]["verified"] = False
            elif missing == "stats":
                state["enemy"]["verified_stats"] = False
            elif missing == "types":
                state["player"]["types_verified"] = False
            result = plan_battle(state)
            self.assertEqual(result["status"], "needs_data", missing)
            self.assertIsNone(result["recommended_slot"], missing)
            self.assertIsNone(result["next_button"], missing)
            self.assertTrue(result["missing_evidence"], missing)

    def test_normalized_cursor_changes_one_step_and_does_not_read_raw_cursor(self):
        for selected, expected in ((0, "down"), (1, "down"), (2, "a")):
            state = battle()
            state["selected_move_slot"] = selected
            self.assertEqual(plan_battle(state)["next_button"], expected)
        state["selected_move_slot"] = None
        state["menu_cursor_raw"] = 3
        result = plan_battle(state)
        self.assertEqual(result["recommended_slot"], 2)
        self.assertIsNone(result["next_button"])

    def test_visible_command_labels_navigate_toward_fight_without_raw_index_assumptions(self):
        for command, expected in (
            ("FIGHT", "a"),
            ("PKMN", "left"),
            ("ITEM", "up"),
            ("PACK", "up"),
            ("RUN", "up"),
            (None, None),
        ):
            state = battle()
            state.update(menu="command", selected_command=command, menu_cursor_raw=0)
            self.assertEqual(plan_battle(state)["next_button"], expected, command)

    def test_text_unknown_menu_and_unready_battle_do_not_invent_an_input(self):
        for menu in ("text_or_animation", "unknown", None):
            state = battle()
            state["menu"] = menu
            self.assertIsNone(plan_battle(state)["next_button"])
        state["verified"] = False
        self.assertIsNone(plan_battle(state)["recommended_slot"])

    def test_fainted_combatants_do_not_request_another_attack(self):
        for side in ("player", "enemy"):
            state = battle()
            state[side]["hp"] = 0
            result = plan_battle(state)
            self.assertIsNone(result["recommended_slot"])
            self.assertIsNone(result["next_button"])

    def test_conditional_hit_requirement_is_checked_and_unknown_is_not_assumed(self):
        state = battle()
        state["player"]["moves"] = [move(138, 0, [4])]
        self.assertEqual(
            plan_battle(state)["ranked_moves"][0]["exclusion_reason"],
            "dream_eater_requires_sleeping_target",
        )
        del state["enemy"]["status_bits"]
        self.assertEqual(plan_battle(state)["status"], "needs_data")
        state["enemy"]["status_bits"] = 2
        self.assertEqual(plan_battle(state)["recommended_slot"], 0)

    def test_noncritical_integer_formula_stat_scaling_and_type_order(self):
        base, damages, attack, defense = _damage_distribution(50, 50, 300, 400, False, [1])
        self.assertEqual((attack, defense, base), (75, 100, 18))
        self.assertEqual((min(damages), max(damages)), (15, 18))
        base, ordered, *_ = _damage_distribution(10, 30, 20, 20, False, [0.5, 2])
        _, combined, *_ = _damage_distribution(10, 30, 20, 20, False, [1])
        self.assertEqual(base, 5)
        self.assertEqual(max(ordered), 4)
        self.assertEqual(max(combined), 5)

    def test_hit_probability_includes_gen_one_255_over_256_limit(self):
        result = plan_battle(battle())
        self.assertAlmostEqual(
            result["ranked_moves"][0]["base_hit_probability"], 255 / 256, places=6
        )

    def test_disabled_and_complex_mechanics_are_not_ranked_as_simple_free_damage(self):
        state = battle()
        state["player"]["moves"] = [move(120, 0, [4])]
        self.assertEqual(
            plan_battle(state)["ranked_moves"][0]["exclusion_reason"],
            "conditional_mechanic_not_estimated",
        )
        state = battle()
        state["player"]["moves"][2]["disabled"] = True
        self.assertEqual(plan_battle(state)["recommended_slot"], 0)

    def test_advice_is_pure_and_only_input_request_is_an_advisory_value(self):
        state = battle()
        original = deepcopy(state)
        advice = plan_battle({"battle": state})
        self.assertEqual(state, original)
        self.assertEqual(advice["role"], "advisory_only; JEV chooses every physical input")


if __name__ == "__main__":
    unittest.main()
