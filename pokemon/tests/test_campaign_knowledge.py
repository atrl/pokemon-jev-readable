"""Story intent selection must not confuse source knowledge with achievement."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_knowledge import catalog, evaluate, fact, objective_for


def verified(value):
    return {"value": value, "verified": True, "quality": "verified_live_fixture", "source": "test"}


def satisfy(predicate, facts):
    if "fact" in predicate:
        value = predicate.get("value", True)
        if predicate.get("op") == "contains":
            previous = facts.get(predicate["fact"], {}).get("value", [])
            value = sorted(set(previous + [value]))
        facts[predicate["fact"]] = verified(value)
    else:
        # A single alternative is enough; all conjunction terms are necessary.
        children = predicate.get("all", predicate.get("any", [])[:1])
        for child in children:
            satisfy(child, facts)


class CampaignKnowledgeTests(unittest.TestCase):
    def test_unknown_run_targets_oak_trigger_before_lab(self):
        target = objective_for({}, {"map_id": 0x26})
        self.assertEqual(target["id"], "meet_oak")
        self.assertEqual(target["target_map_id"], 0x00)
        self.assertEqual(target["target"]["trigger"], {"y": 1})
        self.assertIsNone(target["completion"])
        self.assertIn("source_prior", json.dumps(target))

    def test_unverified_or_contradictory_source_bits_do_not_complete(self):
        for marker in (
            {"value": True, "quality": "source_profile_unverified"},
            {"value": True, "quality": "source_prior", "verified": True},
            {"value": True, "quality": "verified", "verified": False},
            {"value": 1, "quality": "verified"},
        ):
            result = objective_for({"starter_received": marker}, {"map_id": 40})
            self.assertEqual(result["id"], "meet_oak")
            self.assertNotIn("choose_starter", result["completed_ids"])

    def test_actual_party_skips_missing_early_event_flags(self):
        result = objective_for({"party_count": verified(1)}, {"map_id": 40})
        self.assertEqual(result["id"], "lab_rival")
        self.assertEqual(result["target"]["trigger"], {"y": 6})

    def test_starter_uses_three_real_object_selectors(self):
        result = objective_for({"oak_asked_to_choose_mon": verified(True)}, {"map_id": 40})
        self.assertEqual(result["id"], "choose_starter")
        self.assertEqual([(item["x"], item["y"], item["text_id"]) for item in result["interaction_selectors"]],
                         [(6, 3, 2), (7, 3, 3), (8, 3, 4)])

    def test_current_object_location_is_used_without_promoting_prior(self):
        world = {"map_id": 40, "objects": [
            {"sprite": "SPRITE_BALL", "text_id": 2, "x": 6, "y": 3, "visible": False},
            {"sprite": "SPRITE_BALL", "text_id": 3, "x": 7, "y": 4, "quality": "source_prior"},
        ]}
        result = objective_for({"oak_asked_to_choose_mon": verified(True)}, world)
        self.assertEqual((result["target"]["x"], result["target"]["y"]), (7, 4))
        self.assertEqual(result["target"]["quality"], "source_prior")
        self.assertIsNone(result["completion"])

    def test_parcel_ownership_and_delivery_are_distinct(self):
        facts = {"starter_received": True, "rival_lab_battled": True}
        self.assertEqual(objective_for(facts, {})["id"], "collect_parcel")
        facts["oak_parcel_received"] = verified(True)
        self.assertEqual(objective_for(facts, {})["id"], "deliver_parcel")
        facts["parcel_delivered"] = verified(True)
        self.assertEqual(objective_for(facts, {})["id"], "receive_pokedex")
        facts["pokedex_received"] = verified(True)
        self.assertEqual(objective_for(facts, {})["id"], "boulder_badge")

    def test_verified_badge_mask_is_usable_and_unknown_mask_is_not(self):
        facts = {"pokedex_received": True, "badge_bits": verified(1)}
        self.assertEqual(objective_for(facts, {})["id"], "cascade_badge")
        facts["badge_bits"] = {"value": 255, "quality": "source_prior"}
        self.assertEqual(objective_for(facts, {})["id"], "boulder_badge")

    def test_predicates_keep_unknown_distinct_from_false(self):
        self.assertIsNone(evaluate(fact("x"), {}))
        self.assertIs(evaluate(fact("x"), {"x": verified(False)}), False)
        self.assertIs(evaluate(fact("x"), {"x": verified(True)}), True)
        self.assertIsNone(evaluate(fact("moves", "contains", 15), {"moves": verified("CUT")}))
        self.assertIs(evaluate(fact("moves", "contains", 15), {"moves": verified([15, 33])}), True)

    def test_each_objective_can_advance_only_when_predicate_is_satisfied(self):
        facts = {}
        entries = catalog()
        for entry in entries:
            self.assertEqual(objective_for(facts, {})["id"], entry["id"])
            satisfy(entry["completion"], facts)
        result = objective_for(facts, {})
        self.assertEqual(result["id"], "main_story_complete")
        self.assertEqual(result["status"], "completed")

    def test_bag_item_does_not_prove_move_has_been_taught(self):
        facts = {}
        for entry in catalog():
            if entry["id"] == "prepare_cut":
                break
            satisfy(entry["completion"], facts)
        result = objective_for(facts, {})
        self.assertEqual(result["id"], "prepare_cut")
        self.assertIsNone(result["completion"])
        self.assertIn("party_move_ids", result["unknown_facts"])

    def test_hall_of_fame_evidence_survives_league_flag_reset(self):
        history = {"facts": {"hall_of_fame_entered": verified(True)}}
        result = objective_for({"champion_defeated": verified(False), "hall_of_fame_entered": verified(False)}, {}, history)
        self.assertEqual(result["status"], "completed")
        self.assertIs(result["completion"], True)

    def test_hall_map_or_naked_history_ids_cannot_claim_completion(self):
        result = objective_for({}, {"map_id": 0x76}, {"completed_ids": [entry["id"] for entry in catalog()]})
        self.assertNotEqual(result["status"], "completed")
        self.assertEqual(result["completed_ids"], [])

    def test_catalog_is_serializable_isolated_ordered_and_has_no_inputs(self):
        entries = catalog()
        known = set()
        for entry in entries:
            self.assertNotIn(entry["id"], known)
            self.assertTrue(set(entry["requires"]).issubset(known))
            known.add(entry["id"])
            self.assertFalse(entry["source"]["full_source_binary_match"])
            self.assertNotIn("buttons", entry)
            self.assertNotIn("sequence", entry)
            self.assertIn("completion", entry)
        json.dumps(entries)
        entries[0]["knowledge"].clear()
        self.assertTrue(catalog()[0]["knowledge"])


if __name__ == "__main__":
    unittest.main()
