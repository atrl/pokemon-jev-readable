import copy
from collections import deque
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from team_strategy import assess_recovery, plan_support


def fact(value):
    return {"value": value, "verified": True, "quality": "verified_test"}


def mon(hp=30, maximum=30, status=0, pp=20, max_pp=20, power=40):
    move = {
        "move_id": 33,
        "pp": pp,
        "knowledge": {"power": power, "base_pp": 20, "quality": "source_prior"},
    }
    if max_pp is not None:
        move.update(max_pp=max_pp, max_pp_verified=True)
    return {"hp": hp, "max_hp": maximum, "status_bits": status, "level": 5, "moves": [move]}


def snapshot(party=None, map_id=0):
    party = party if party is not None else [mon()]
    return {
        "frame": 100,
        "party": party,
        "party_state": {"ready": True, "quality": "verified"},
        "milestones": {"party_count": fact(len(party))},
        "player": {"map_id": map_id, "x": 4, "y": 4},
        "scene": {"mode": "overworld", "verified": True},
        "battle": {"active": False, "verified": True},
        "world": {
            "map_id": map_id,
            "source_match": True,
            "player_position_valid": True,
            "input_lock": {"ignored_buttons_mask": 0, "scripted_movement_remaining": 0},
        },
    }


MAPS = {
    "maps": {
        "0": {"name": "PALLET_TOWN", "connections": [{"destination_map_id": 1}]},
        "1": {
            "name": "VIRIDIAN_CITY",
            "warps": [{"destination_map_id": 41}],
            "connections": [{"destination_map_id": 0}, {"destination_map_id": 2}],
        },
        "2": {
            "name": "PEWTER_CITY",
            "warps": [{"destination_map_id": 58}],
            "connections": [{"destination_map_id": 1}],
        },
        "41": {
            "name": "VIRIDIAN_POKECENTER",
            "warps": [{"destination_map_id": -1}],
            "objects": [{"sprite": "SPRITE_NURSE", "x": 3, "y": 1, "text_id": 1}],
        },
        "58": {
            "name": "PEWTER_POKECENTER",
            "warps": [{"destination_map_id": -1}],
            "objects": [{"sprite": "SPRITE_NURSE", "x": 3, "y": 1, "text_id": 1}],
        },
    }
}


def fixture_router(current_world, player, target_map_id, target=None, facts=None):
    # An explicit synthetic graph: fake (map 0, 4, 4) coordinates must never be
    # interpreted as real pinned-source terrain during these unit tests.
    adjacency = {0: [1], 1: [0, 2, 41], 2: [1, 58], 41: [1], 58: [2]}
    start = player["map_id"]
    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        at, route = queue.popleft()
        if at == target_map_id:
            return {
                "status": "same_region" if at == start else "planned",
                "map_route": route,
                "region_route": [[mid, 0] for mid in route],
                "quality": "test_fixture",
            }
        for nxt in adjacency.get(at, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, route + [nxt]))
    return {"status": "unreachable", "reason": "explicit_fixture_graph_has_no_route"}


def fixture_support(*args, **kwargs):
    kwargs.setdefault("route_planner", fixture_router)
    return plan_support(*args, **kwargs)


class TeamStrategyTests(unittest.TestCase):
    def test_low_level_or_moderate_damage_does_not_interrupt_story(self):
        self.assertIsNone(fixture_support(snapshot([mon(hp=15)]), world_data=MAPS))
        self.assertIsNone(fixture_support(snapshot(), world_data=MAPS))

    def test_severe_hp_selects_nearest_source_clinic(self):
        result = fixture_support(snapshot([mon(hp=10)]), world_data=MAPS)
        self.assertEqual(result["id"], "heal_party")
        self.assertEqual(result["target_map_id"], 41)
        self.assertEqual(result["target"]["sprite"], "SPRITE_NURSE")
        self.assertEqual(result["target"]["quality"], "source_prior")
        self.assertIs(result["completion"], False)
        self.assertTrue(result["ready_to_navigate"])

    def test_exact_threshold_is_not_below_threshold(self):
        self.assertIsNone(fixture_support(snapshot([mon(hp=50, maximum=100)]), world_data=MAPS))

    def test_eight_of_eighteen_hp_now_requests_treatment(self):
        result = fixture_support(snapshot([mon(hp=8, maximum=18)]), world_data=MAPS)
        self.assertEqual(result["id"], "heal_party")
        self.assertIn("50%", result["reason"])
        self.assertLess(result["evidence"]["hp_ratio"], 0.50)

    def test_last_viable_member_and_combined_health(self):
        single = assess_recovery(snapshot([mon(hp=0), mon(hp=9)]))
        self.assertIn("last_viable_member_low_hp", single["trigger_reasons"])
        combined = assess_recovery(snapshot([mon(hp=9), mon(hp=10)]))
        self.assertIn("combined_viable_hp_low", combined["trigger_reasons"])
        safe = assess_recovery(snapshot([mon(hp=1), mon(hp=30)]))
        self.assertFalse(safe["needs_recovery"])

    def test_poison_at_full_hp_requires_recovery(self):
        result = fixture_support(snapshot([mon(status=8)]), world_data=MAPS)
        self.assertIn("poison_risk", result["evidence"]["trigger_reasons"])

    def test_all_damage_pp_zero_even_if_status_move_has_pp(self):
        member = mon(pp=0)
        member["moves"].append(
            {
                "move_id": 45,
                "pp": 40,
                "max_pp": 40,
                "max_pp_verified": True,
                "knowledge": {"power": 0, "effect": "ATTACK_DOWN1_EFFECT"},
            }
        )
        result = fixture_support(snapshot([member]), world_data=MAPS)
        self.assertIn("damage_pp_exhausted", result["evidence"]["trigger_reasons"])
        member["moves"][1]["knowledge"] = {}
        self.assertIsNone(fixture_support(snapshot([member]), world_data=MAPS))

    def test_fixed_damage_zero_power_move_still_counts_as_damage(self):
        member = mon(pp=3, power=0)
        member["moves"][0]["knowledge"]["effect"] = "SPECIAL_DAMAGE_EFFECT"
        self.assertIsNone(fixture_support(snapshot([member]), world_data=MAPS))

    def test_unknown_or_initializing_party_does_not_trigger(self):
        for change in ["count", "ready", "ready_quality", "hp", "battle", "lock", "map"]:
            snap = snapshot([mon(hp=1)])
            if change == "count":
                snap["milestones"]["party_count"]["verified"] = False
            if change == "ready":
                snap["party_state"]["ready"] = False
            if change == "ready_quality":
                snap["party_state"]["quality"] = "source_prior"
            if change == "hp":
                snap["party"][0]["hp"] = -1
            if change == "battle":
                snap["battle"]["active"] = None
            if change == "lock":
                snap["world"]["input_lock"]["ignored_buttons_mask"] = 255
            if change == "map":
                snap["world"]["source_match"] = False
            self.assertIsNone(fixture_support(snap, world_data=MAPS), change)

    def test_no_new_support_during_battle_or_dialog(self):
        for mode in ("battle", "dialog", "unknown"):
            snap = snapshot([mon(hp=1)])
            snap["scene"]["mode"] = mode
            self.assertIsNone(fixture_support(snap, world_data=MAPS))

    def test_active_support_latches_until_full_hp_pp_and_status(self):
        active = fixture_support(snapshot([mon(hp=1)]), world_data=MAPS)
        for member in (mon(hp=20), mon(pp=19), mon(status=8)):
            active = fixture_support(snapshot([member], map_id=2), active, world_data=MAPS)
            self.assertEqual(active["target_map_id"], 41)  # do not switch clinics every step
            self.assertIs(active["completion"], False)
        self.assertIsNone(fixture_support(snapshot(), active, world_data=MAPS))

    def test_active_support_persists_through_unknown_battle_and_healing_dialog(self):
        active = fixture_support(snapshot([mon(hp=1)]), world_data=MAPS)
        for mode in ("battle", "dialog", "unknown"):
            snap = snapshot()
            snap["scene"]["mode"] = mode
            pending = fixture_support(snap, active, world_data=MAPS)
            self.assertEqual(pending["id"], "heal_party")
            self.assertFalse(pending["ready_to_navigate"])
        snap["party"] = None
        self.assertTrue(fixture_support(snap, active, world_data=MAPS)["needs_recovery"])

    def test_source_base_pp_cannot_prove_full_recovery(self):
        active = fixture_support(snapshot([mon(hp=1, max_pp=None)]), world_data=MAPS)
        pending = fixture_support(snapshot([mon(max_pp=None)]), active, world_data=MAPS)
        self.assertIsNotNone(pending)
        self.assertIsNone(pending["evidence"]["full_recovery"])
        self.assertTrue(any("verified_max_pp" in key for key in pending["unknown_facts"]))

    def test_contradictory_max_pp_quality_cannot_certify_healing(self):
        snap = snapshot()
        snap["party"][0]["moves"][0]["max_pp_quality"] = "source_prior"
        self.assertIsNone(assess_recovery(snap)["full_recovery"])

    def test_fresh_verified_healing_marker_cannot_be_reused_from_old_frame(self):
        active = fixture_support(snapshot([mon(hp=1, max_pp=None)]), world_data=MAPS)
        snap = snapshot([mon(max_pp=None)])
        snap["milestones"]["party_fully_healed"] = {**fact(True), "frame": 99}
        self.assertIsNotNone(fixture_support(snap, active, world_data=MAPS))
        snap["milestones"]["party_fully_healed"]["frame"] = 100
        self.assertIsNone(fixture_support(snap, active, world_data=MAPS))

    def test_no_route_retains_need_without_inventing_destination_or_healing(self):
        result = fixture_support(
            snapshot([mon(hp=1)]), world_data={"maps": {"0": {"name": "PALLET"}}}
        )
        self.assertEqual(result["id"], "heal_party")
        self.assertIsNone(result["target_map_id"])
        self.assertIn("reachable_pokecenter", result["unknown_facts"])

    def test_region_coverage_missing_never_falls_back_to_map_hops(self):
        def unsupported(*args, **kwargs):
            return {"status": "needs_data", "reason": "outside_region_coverage"}

        result = fixture_support(snapshot([mon(hp=1)]), world_data=MAPS, route_planner=unsupported)
        self.assertIsNone(result["target_map_id"])
        self.assertEqual(result["route_status"], "needs_data")
        self.assertFalse(result["ready_to_navigate"])
        self.assertIn("clinic_region_route", result["unknown_facts"])

    def test_active_unknown_coverage_preserves_target_without_claiming_route(self):
        active = fixture_support(snapshot([mon(hp=1)]), world_data=MAPS)

        def unsupported(*args, **kwargs):
            return {"status": "needs_data", "reason": "outside_region_coverage"}

        result = fixture_support(
            snapshot([mon(hp=20)], map_id=2), active, world_data=MAPS, route_planner=unsupported
        )
        self.assertEqual(result["target_map_id"], 41)
        self.assertEqual(result["route_status"], "needs_data")
        self.assertFalse(result["ready_to_navigate"])
        self.assertEqual(result.get("rejected_targets", []), [])

    def test_real_route4_east_uses_cerulean_center_not_unreachable_mt_moon_center(self):
        # Real coordinates, real pinned region topology, no synthetic map graph.
        snap = snapshot([mon(hp=1)], map_id=15)
        snap["player"].update(x=70, y=10)
        result = plan_support(snap)
        self.assertEqual(result["target_map_id"], 64)
        self.assertEqual(result["route_map_ids"], [15, 3, 64])
        self.assertTrue(result["ready_to_navigate"])
        old = next(
            row for row in result["routing"]["clinic_candidates"] if row["target_map_id"] == 68
        )
        self.assertEqual(old["status"], "unreachable")

    def test_actual_unreachable_latched_clinic_is_reselected_with_evidence(self):
        snap = snapshot([mon(hp=1)], map_id=15)
        snap["player"].update(x=70, y=10)
        active = {
            "id": "heal_party",
            "target_map_id": 68,
            "target_map_name": "MT_MOON_POKECENTER",
            "target": {"kind": "object", "sprite": "SPRITE_NURSE", "x": 3, "y": 1, "text_id": 1},
            "reason": "old low HP request",
        }
        result = plan_support(snap, active)
        self.assertEqual(result["target_map_id"], 64)
        self.assertEqual(result["rejected_targets"][-1]["target_map_id"], 68)
        self.assertEqual(result["rejected_targets"][-1]["route_status"], "unreachable")
        self.assertIn("no_source_land_portal_route", result["rejected_targets"][-1]["reason"])
        self.assertEqual(active["target_map_id"], 68)

    def test_real_unsupported_region_reports_needs_data(self):
        snap = snapshot([mon(hp=1)], map_id=133)
        snap["player"].update(x=3, y=4)
        result = plan_support(snap)
        self.assertIsNone(result["target_map_id"])
        self.assertEqual(result["route_status"], "needs_data")
        self.assertFalse(result["ready_to_navigate"])

    def test_module_does_not_mutate_inputs_or_emit_a_button(self):
        snap = snapshot([mon(hp=1)])
        saved = copy.deepcopy(snap)
        active = fixture_support(snap, world_data=MAPS)
        saved_active = copy.deepcopy(active)
        fixture_support(snapshot([mon(hp=20)]), active, world_data=MAPS)
        self.assertEqual(snap, saved)
        self.assertEqual(active, saved_active)
        self.assertNotIn("next_button", active)


if __name__ == "__main__":
    unittest.main()
