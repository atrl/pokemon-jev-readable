"""Regression checks for source-labelled, region-aware opening routes."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_regions import build, interaction_positions, load_regions, plan_route
from world_data import map_prior


def route(mid, x, y, destination, target=None):
    world = deepcopy(map_prior(mid))
    world.update(source_match=True, player_position_valid=True)
    return plan_route(world, {'map_id': mid, 'x': x, 'y': y}, destination, target)


class RegionRouteTests(unittest.TestCase):
    def test_route_two_south_to_pewter_must_cross_viridian_forest(self):
        plan = route(13, 3, 45, 54)
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['map_route'], [13, 50, 51, 47, 13, 2, 54])
        transition = plan['next_transition']
        self.assertEqual((transition['kind'], transition['x'], transition['y'], transition['destination_map_id']),
                         ('warp', 3, 43, 50))
        self.assertEqual(plan['quality'], 'source_prior')

    def test_route_four_west_to_cerulean_must_cross_mt_moon(self):
        plan = route(15, 11, 7, 65)
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['map_route'], [15, 59, 60, 61, 60, 15, 3, 65])
        transition = plan['next_transition']
        self.assertEqual((transition['kind'], transition['x'], transition['y'], transition['destination_map_id']),
                         ('warp', 18, 5, 59))
        # Re-entering both Mt Moon B1F and Route 4 is essential: map-ID BFS
        # cannot represent these paths because it marks the whole map visited.
        self.assertEqual(plan['map_route'].count(15), 2)
        self.assertEqual(plan['map_route'].count(60), 2)

    def test_same_map_destination_in_another_region_requires_an_excursion(self):
        plan = route(13, 3, 45, 13, {'kind': 'coordinate', 'x': 3, 'y': 10})
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['map_route'], [13, 50, 51, 47, 13])
        self.assertEqual(plan['next_transition']['destination_map_id'], 50)

    def test_connection_targets_a_real_aligned_boundary_cell(self):
        plan = route(2, 20, 17, 65)
        edge = plan['next_transition']
        self.assertEqual(edge['kind'], 'connection')
        self.assertEqual(edge['direction'], 'east')
        self.assertEqual(edge['x'], 39)
        self.assertEqual(edge['arrival'], [0, edge['y'] - 8])

    def test_ledge_is_directed_and_disclosed_instead_of_making_wall_walkable(self):
        plan = route(15, 24, 6, 65)
        edge = plan['next_transition']
        self.assertEqual(edge['kind'], 'ledge')
        self.assertEqual(edge['button'], 'down')
        self.assertEqual(edge['arrival'], [edge['x'], edge['y'] + 2])
        self.assertEqual(edge['quality'], 'source_prior')
        edges = load_regions()['edges']
        selected = next(e for e in edges if e['transition'] == edge)
        self.assertFalse(any(e['from'] == selected['to'] and e['to'] == selected['from'] for e in edges))

    def test_source_route_never_overrides_a_live_warp_mismatch(self):
        world = deepcopy(map_prior(13)); world['warps'] = []
        plan = plan_route(world, {'map_id': 13, 'x': 3, 'y': 45}, 54)
        self.assertEqual(plan['status'], 'needs_data')
        self.assertEqual(plan['reason'], 'first_warp_disagrees_with_current_world')
        self.assertIsNone(plan['next_transition'])

    def test_source_route_never_overrides_a_live_connection_mismatch(self):
        world = deepcopy(map_prior(2)); world['connections'] = []
        plan = plan_route(world, {'map_id': 2, 'x': 20, 'y': 17}, 65)
        self.assertEqual(plan['status'], 'needs_data')
        self.assertEqual(plan['reason'], 'first_connection_disagrees_with_current_world')

    def test_unaligned_and_uncovered_maps_do_not_fall_back_to_map_connectivity(self):
        world = deepcopy(map_prior(13)); world['source_match'] = False
        plan = plan_route(world, {'map_id': 13, 'x': 3, 'y': 45}, 54)
        self.assertEqual(plan['status'], 'needs_data')
        outside = route(13, 3, 45, 245)
        self.assertEqual(outside['status'], 'needs_data')
        self.assertEqual(outside['reason'], 'outside_early_land_topology_coverage')
        self.assertIsNone(outside['next_transition'])

    def test_object_in_same_region_is_left_to_local_navigation(self):
        plan = route(40, 5, 3, 40, {'kind': 'object', 'x': 6, 'y': 3})
        self.assertEqual(plan['status'], 'same_region')
        self.assertIsNone(plan['next_transition'])

    def test_nurse_across_verified_source_counter_is_reachable_from_lobby(self):
        target = {'kind': 'object', 'x': 3, 'y': 1}
        positions = interaction_positions(41, target)
        lobby = next(position for position in positions if (position['x'], position['y']) == (3, 3))
        self.assertEqual(lobby['facing'], 'up')
        self.assertTrue(lobby['over_counter'])
        self.assertEqual(lobby['quality'], 'source_prior')
        plan = route(41, 3, 3, 41, target)
        self.assertEqual(plan['status'], 'same_region')
        regions = load_regions()['maps']['41']['region_rows']
        self.assertNotEqual(regions[1][3], regions[3][3])
        self.assertEqual(regions[2][3], -1)  # Counter stays nonwalkable.

    def test_mart_clerk_can_be_addressed_across_its_real_counter(self):
        target = {'kind': 'object', 'x': 0, 'y': 5}
        positions = interaction_positions(42, target)
        east = next(position for position in positions if (position['x'], position['y']) == (2, 5))
        self.assertEqual(east['facing'], 'left')
        self.assertTrue(east['over_counter'])
        self.assertEqual(route(42, 2, 5, 42, target)['status'], 'same_region')

    def test_ordinary_npc_does_not_gain_two_cell_interaction_range(self):
        positions = interaction_positions(40, {'kind': 'object', 'x': 5, 'y': 2})
        self.assertTrue(positions)
        self.assertTrue(all(not position['over_counter'] for position in positions))
        self.assertTrue(all(abs(position['x'] - 5) + abs(position['y'] - 2) == 1 for position in positions))

    def test_every_counter_interaction_has_an_actual_counter_between_actor_and_object(self):
        for mid, target in ((41, {'x': 3, 'y': 1}), (42, {'x': 0, 'y': 5})):
            counters = {(cell['x'], cell['y']) for cell in load_regions()['maps'][str(mid)]['counter_cells']}
            for position in interaction_positions(mid, target):
                if position['over_counter']:
                    self.assertIn(((position['x'] + target['x']) // 2,
                                   (position['y'] + target['y']) // 2), counters)

    def test_all_edges_join_declared_regions_and_never_claim_live_verification(self):
        data = load_regions()
        self.assertFalse(data['source_binary_match'])
        for edge in data['edges']:
            transition = edge['transition']
            for node, coordinates in ((edge['from'], [transition['x'], transition['y']]),
                                      (edge['to'], transition['arrival'])):
                entry = data['maps'][str(node[0])]; x, y = coordinates
                self.assertEqual(entry['region_rows'][y][x], node[1])
            self.assertEqual(transition['quality'], 'source_prior')

    @unittest.skipUnless(Path('/tmp/jev-redstar-observation-source/.git').exists(), 'pinned source checkout not available')
    def test_bundled_topology_rebuilds_identically_from_pinned_source(self):
        self.assertEqual(build(Path('/tmp/jev-redstar-observation-source')), load_regions())


if __name__ == '__main__':
    unittest.main()
