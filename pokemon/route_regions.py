"""Source-labelled land regions and portals, never a controller.

Rebuild the bundled early-game topology from the pinned source checkout with
``python route_regions.py /path/to/redstarbluestar``. Tile components are not
map IDs: entering the other side of Route 2/4 can require leaving that map.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path


DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def _region(entry, x, y):
    if not entry or not (0 <= x < entry["width"] and 0 <= y < entry["height"]):
        return None
    region = entry["region_rows"][y][x]
    return region if region >= 0 else None


def build(source: Path) -> dict:
    """Offline compatibility entrypoint; the runtime only reads generated data."""
    from tools.build_route_regions import build as build_from_source

    return build_from_source(source)


@lru_cache(maxsize=1)
def load_regions():
    return json.loads(Path(__file__).with_name("redstar-route-regions.json").read_text())


def interaction_positions(map_id, target):
    """Source-advisory standing cells for an object, including real counters.

    A two-cell interaction is allowed only when the middle walking cell's
    lower-left tile matches one of this map's three tileset counter IDs.
    The caller must still compare the live map/object and let JEV select input.
    """
    entry = load_regions()["maps"].get(str(map_id))
    tx, ty = target.get("x"), target.get("y")
    if not entry or type(tx) is not int or type(ty) is not int:
        return []
    counters = {(cell["x"], cell["y"]) for cell in entry.get("counter_cells", [])}
    positions = []
    for facing, (dx, dy) in DIRECTIONS.items():
        for distance in (1, 2):
            x, y = tx - dx * distance, ty - dy * distance
            if _region(entry, x, y) is None:
                continue
            if distance == 2 and (tx - dx, ty - dy) not in counters:
                continue
            positions.append(
                {
                    "x": x,
                    "y": y,
                    "facing": facing,
                    "quality": "source_prior",
                    "over_counter": distance == 2,
                    "source": "source walking collision cells and tileset-header counter tile IDs",
                }
            )
    return positions


def plan_route(current_world, player, target_map_id, target=None, facts=None):
    """Return one portal suggestion; all actual physical inputs remain JEV's.

    ``target`` is optional coordinate/object selector or a list of selectors.
    If given, reaching another disconnected region of the same map is planned
    correctly. ``facts`` is reserved for verified ability-gated routing; this
    version does not infer CUT/SURF access from possession or source priors.
    """
    data = load_regions()
    maps = data["maps"]
    base = {
        "quality": "source_prior",
        "source_commit": data["source_commit"],
        "limitations": data["limitations"],
        "next_transition": None,
        "map_route": [],
    }
    mid = player.get("map_id")
    x, y = player.get("x"), player.get("y")
    if (
        any(type(v) is not int for v in (mid, x, y, target_map_id))
        or current_world.get("source_match") is False
        or current_world.get("player_position_valid") is False
    ):
        return {**base, "status": "needs_data", "reason": "current_map_alignment_unverified"}
    here, destination = maps.get(str(mid)), maps.get(str(target_map_id))
    if not here or not destination:
        return {**base, "status": "needs_data", "reason": "outside_early_land_topology_coverage"}
    region = _region(here, x, y)
    if region is None:
        return {**base, "status": "needs_data", "reason": "player_not_on_source_walkable_region"}
    goals = set()
    targets = target if isinstance(target, list) else [target] if isinstance(target, dict) else []
    for candidate in targets:
        tx, ty = candidate.get("x"), candidate.get("y")
        if type(tx) is not int or type(ty) is not int:
            continue
        points = (
            [
                (position["x"], position["y"])
                for position in interaction_positions(target_map_id, candidate)
            ]
            if candidate.get("kind") == "object"
            else [(tx, ty)]
        )
        if "y" in (candidate.get("trigger") or {}):
            points = [(cx, candidate["trigger"]["y"]) for cx in range(destination["width"])]
        for px, py in points:
            value = _region(destination, px, py)
            if value is not None:
                goals.add((target_map_id, value))
    if not goals and targets:
        return {**base, "status": "needs_data", "reason": "target_not_on_source_walkable_region"}
    if not targets:
        goals = {(target_map_id, r) for r in range(destination["region_count"])}
    start = (mid, region)
    if start in goals:
        return {**base, "status": "same_region", "map_route": [mid]}
    adjacency = {}
    for edge in data["edges"]:
        adjacency.setdefault(tuple(edge["from"]), []).append(edge)
    adjacency[start] = sorted(
        adjacency.get(start, []),
        key=lambda e: abs(e["transition"]["x"] - x) + abs(e["transition"]["y"] - y),
    )
    queue = deque([start])
    parents = {start: None}
    found = None
    while queue:
        at = queue.popleft()
        if at in goals:
            found = at
            break
        for edge in adjacency.get(at, []):
            nxt = tuple(edge["to"])
            if nxt not in parents:
                parents[nxt] = (at, edge)
                queue.append(nxt)
    if found is None:
        return {
            **base,
            "status": "unreachable",
            "reason": "no_source_land_portal_route_from_this_region",
            "start_region": region,
        }
    path = []
    at = found
    while parents[at] is not None:
        at, edge = parents[at]
        path.append(edge)
    path.reverse()
    route = [mid]
    for edge in path:
        if edge["to"][0] != route[-1]:
            route.append(edge["to"][0])
    transition = deepcopy(path[0]["transition"])
    # Cross-check the chosen first portal against current RAM metadata. Static
    # priors cannot override an observed portal mismatch.
    if transition["kind"] == "warp" and "warps" in current_world:
        matching = [
            w
            for w in current_world["warps"]
            if w.get("x") == transition["x"]
            and w.get("y") == transition["y"]
            and w.get("destination_map_id") == transition["destination_map_id"]
        ]
        if not matching:
            return {
                **base,
                "status": "needs_data",
                "reason": "first_warp_disagrees_with_current_world",
            }
    if transition["kind"] == "connection" and "connections" in current_world:
        matching = [
            c
            for c in current_world["connections"]
            if c.get("direction") == transition["direction"]
            and c.get("destination_map_id") == transition["destination_map_id"]
        ]
        if not matching:
            return {
                **base,
                "status": "needs_data",
                "reason": "first_connection_disagrees_with_current_world",
            }
    return {
        **base,
        "status": "planned",
        "next_transition": transition,
        "map_route": route,
        "region_route": [list(start)] + [edge["to"] for edge in path],
    }


if __name__ == "__main__":
    import sys

    result = build(Path(sys.argv[1]))
    Path(__file__).with_name("redstar-route-regions.json").write_text(
        json.dumps(result, separators=(",", ":")) + "\n"
    )
    print(
        json.dumps(
            {
                "maps": len(result["maps"]),
                "portal_edges": len(result["edges"]),
                "skipped": result["skipped_maps"],
            }
        )
    )
