"""Pure, persistent-intent team recovery planning. Never emits physical inputs.

Pass the previous returned objective as ``active`` on the next observation and
persist it with campaign state. A started recovery intent only releases after
verified full HP, PP, and cleared status in stable overworld. Source move/map
knowledge can guide a detour but cannot certify that healing succeeded.
"""

from __future__ import annotations

from copy import deepcopy

from world_data import load_world_data
from route_regions import plan_route

LOW_HP_RATIO = 0.50
POISON_MASK = 1 << 3  # pinned constants/status_constants.asm: PSN EQU 3
SPECIAL_DAMAGE_EFFECTS = {
    "SPECIAL_DAMAGE_EFFECT",
    "BIDE_EFFECT",
    "SUPER_FANG_EFFECT",
    "OHKO_EFFECT",
}


def _verified(record):
    if not isinstance(record, dict) or record.get("verified") is False:
        return False
    quality = str(record.get("quality", ""))
    if "unverified" in quality or "prior" in quality:
        return False
    return (
        record.get("verified") is True or quality == "verified" or quality.startswith("verified_")
    )


def _value(record):
    return record.get("value") if _verified(record) else None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _stable(snapshot):
    scene = snapshot.get("scene") or {}
    battle = snapshot.get("battle") or {}
    world = snapshot.get("world") or {}
    lock = world.get("input_lock") or {}
    return (
        scene.get("verified") is True
        and scene.get("mode") == "overworld"
        and _verified(battle)
        and battle.get("active") is False
        and world.get("source_match") is True
        and world.get("player_position_valid") is True
        and lock.get("ignored_buttons_mask") == 0
        and lock.get("scripted_movement_remaining") == 0
    )


def _max_pp(move):
    """Only independently verified maximum PP, including any PP Up effects."""
    raw = move.get("max_pp")
    if isinstance(raw, dict):
        value = _value(raw)
    elif _verified(
        {"verified": move.get("max_pp_verified"), "quality": move.get("max_pp_quality", "")}
    ):
        value = raw
    else:
        return None
    return value if _integer(value, 1, 63) else None


def _damaging(move):
    evidence = move.get("is_damaging")
    if isinstance(evidence, dict) and type(_value(evidence)) is bool:
        return _value(evidence), "verified_move_classification"
    knowledge = move.get("knowledge") or {}
    power = knowledge.get("power")
    if type(power) is int and power >= 0:
        return power > 0 or knowledge.get("effect") in SPECIAL_DAMAGE_EFFECTS, "source_prior"
    return None, "needs_data"


def assess_recovery(snapshot, *, low_hp_ratio=LOW_HP_RATIO):
    """Describe current need and full recovery with explicit unknowns.

    Full-PP evidence is either per-move verified max_pp or a verified,
    current-frame ``milestones.party_fully_healed`` marker. Merely comparing PP
    to an unverified source ``knowledge.base_pp`` never releases the latch.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    facts = snapshot.get("milestones") or {}
    party = snapshot.get("party")
    party_state = snapshot.get("party_state") or {}
    count = _value(facts.get("party_count"))
    result = {
        "party_verified": False,
        "needs_recovery": None,
        "full_recovery": None,
        "trigger_reasons": [],
        "missing_evidence": [],
        "stable_overworld": _stable(snapshot),
        "hp_ratio": None,
        "viable_count": None,
        "damage_pp_exhausted": None,
        "members": [],
        "damage_classification_quality": [],
    }
    if (
        not isinstance(party, list)
        or not _integer(count, 1, 6)
        or count != len(party)
        or party_state.get("ready") is not True
        or not _verified(party_state)
    ):
        result["missing_evidence"].append("verified_ready_party")
        return result
    moves = []
    for index, mon in enumerate(party):
        if not isinstance(mon, dict) or not (
            _integer(mon.get("hp"), 0, 999)
            and _integer(mon.get("max_hp"), 1, 999)
            and mon["hp"] <= mon["max_hp"]
            and _integer(mon.get("status_bits"), 0, 255)
        ):
            result["missing_evidence"].append(f"party[{index}].hp_or_status")
            return result
        row = {
            "slot": mon.get("slot", index),
            "hp": mon["hp"],
            "max_hp": mon["max_hp"],
            "status_bits": mon["status_bits"],
            "moves": [],
        }
        mon_moves = mon.get("moves")
        if not isinstance(mon_moves, list) or not mon_moves:
            result["missing_evidence"].append(f"party[{index}].moves")
            mon_moves = []
        for move_index, move in enumerate(mon_moves):
            if not isinstance(move, dict) or not (
                _integer(move.get("move_id"), 1, 255) and _integer(move.get("pp"), 0, 63)
            ):
                result["missing_evidence"].append(f"party[{index}].moves[{move_index}].pp")
                continue
            maximum = _max_pp(move)
            damage, quality = _damaging(move)
            entry = {
                "move_id": move["move_id"],
                "pp": move["pp"],
                "max_pp": maximum,
                "damaging": damage,
                "classification_quality": quality,
            }
            row["moves"].append(entry)
            if maximum is None:
                result["missing_evidence"].append(
                    f"party[{index}].moves[{move_index}].verified_max_pp"
                )
            elif move["pp"] > maximum:
                result["missing_evidence"].append(
                    f"party[{index}].moves[{move_index}].incoherent_pp"
                )
            if mon["hp"] > 0:
                moves.append(entry)
        result["members"].append(row)
    result["party_verified"] = True
    viable = [mon for mon in result["members"] if mon["hp"] > 0]
    result["viable_count"] = len(viable)
    total_max = sum(mon["max_hp"] for mon in viable)
    result["hp_ratio"] = sum(mon["hp"] for mon in viable) / total_max if total_max else 0.0
    reasons = result["trigger_reasons"]
    if not viable:
        reasons.append("no_viable_party")
    elif result["hp_ratio"] < low_hp_ratio:
        reasons.append(
            "last_viable_member_low_hp" if len(viable) == 1 else "combined_viable_hp_low"
        )
    if any(mon["status_bits"] & POISON_MASK for mon in viable):
        reasons.append("poison_risk")
    elif any(mon["status_bits"] for mon in viable):
        reasons.append("status_condition")
    pp_missing = any(
        ".moves" in key and not key.endswith(".verified_max_pp")
        for key in result["missing_evidence"]
    )
    damaging_moves = [move for move in moves if move["damaging"] is True]
    unknown_usable = any(move["damaging"] is None and move["pp"] > 0 for move in moves)
    if moves and not pp_missing:
        if any(move["pp"] > 0 for move in damaging_moves):
            result["damage_pp_exhausted"] = False
        elif not any(move["pp"] > 0 for move in moves):
            result["damage_pp_exhausted"] = True
        elif damaging_moves and not unknown_usable:
            result["damage_pp_exhausted"] = True
    if result["damage_pp_exhausted"] is True:
        reasons.append("damage_pp_exhausted")
    result["damage_classification_quality"] = sorted(
        {move["classification_quality"] for move in moves}
    )
    result["needs_recovery"] = bool(reasons)
    result["full_recovery"] = _full_recovery(result, snapshot, pp_missing)
    return result


def _full_recovery(assessment, snapshot, pp_missing):
    """Keep a verified recovery distinct from missing PP evidence."""
    hp_full = all(mon["hp"] == mon["max_hp"] for mon in assessment["members"])
    clear_status = all(mon["status_bits"] == 0 for mon in assessment["members"])
    all_moves = [move for mon in assessment["members"] for move in mon["moves"]]
    known_short_pp = any(
        move["max_pp"] is not None and move["pp"] < move["max_pp"] for move in all_moves
    )
    current_healed = (snapshot.get("milestones") or {}).get("party_fully_healed") or {}
    fresh_marker = (
        _value(current_healed) is True
        and type(snapshot.get("frame")) is int
        and current_healed.get("frame") == snapshot["frame"]
    )
    if (
        not hp_full
        or not clear_status
        or known_short_pp
        or any(move["pp"] == 0 for move in all_moves)
    ):
        return False
    elif not assessment["missing_evidence"]:
        return True
    elif fresh_marker and not pp_missing:
        return True
    return None


def _route_to(snapshot, map_id, target, router):
    result = router(
        snapshot.get("world") or {},
        snapshot.get("player") or {},
        map_id,
        target=target,
        facts=snapshot.get("milestones") or {},
    )
    return (
        result
        if isinstance(result, dict)
        else {"status": "needs_data", "reason": "invalid_region_route_response"}
    )


def _clinic_target(snapshot, data, router):
    """Only region-reachable nurse interaction positions are candidates.

    Map adjacency alone is insufficient: one-way ledges can separate areas of
    the same map. Unknown topology never becomes a map-hop fallback.
    """
    candidates = []
    diagnostics = []
    for key, record in data.get("maps", {}).items():
        if not str(key).isdigit() or not isinstance(record, dict):
            continue
        map_id = int(key)
        if (
            "POKECENTER" not in str(record.get("name", ""))
            and record.get("name") != "INDIGO_PLATEAU_LOBBY"
        ):
            continue
        nurse = next(
            (
                obj
                for obj in record.get("objects", [])
                if obj.get("sprite") == "SPRITE_NURSE"
                and type(obj.get("x")) is int
                and type(obj.get("y")) is int
            ),
            None,
        )
        if nurse is None:
            continue
        target = {
            "kind": "object",
            "sprite": "SPRITE_NURSE",
            "x": nurse["x"],
            "y": nurse["y"],
            "text_id": nurse.get("text_id"),
            "quality": "source_prior",
        }
        route = _route_to(snapshot, map_id, target, router)
        diagnostics.append(
            {
                "target_map_id": map_id,
                "status": route.get("status", "needs_data"),
                "reason": route.get("reason"),
            }
        )
        if route.get("status") not in ("planned", "same_region"):
            continue
        hops = max(0, len(route.get("region_route") or route.get("map_route") or []) - 1)
        candidate = {
            "target_map_id": map_id,
            "target_map_name": record.get("name"),
            "route_map_ids": deepcopy(route.get("map_route", [])),
            "target": target,
            "routing": deepcopy(route),
        }
        candidates.append((hops, map_id, candidate))
    if candidates:
        candidate = min(candidates, key=lambda item: item[:2])[2]
        candidate["routing"]["clinic_candidates"] = diagnostics
        return candidate
    return {
        "target_map_id": None,
        "target": None,
        "route_map_ids": [],
        "routing": {
            "status": "needs_data"
            if not diagnostics or any(row["status"] != "unreachable" for row in diagnostics)
            else "unreachable",
            "reason": "no_supported_reachable_clinic",
            "clinic_candidates": diagnostics,
            "quality": "source_prior",
        },
    }


def _refresh_clinic_route(snapshot, objective, stable_overworld, world_data, router):
    """Recheck a latched clinic target only when the overworld is stable."""
    if stable_overworld:
        if objective.get("target"):
            routing = _route_to(
                snapshot, objective.get("target_map_id"), objective["target"], router
            )
            objective["routing"] = deepcopy(routing)
            if routing.get("status") == "unreachable":
                rejected = objective.setdefault("rejected_targets", [])
                rejected.append(
                    {
                        "target_map_id": objective.get("target_map_id"),
                        "target": deepcopy(objective["target"]),
                        "reason": routing.get("reason", "region_route_unreachable"),
                        "route_status": "unreachable",
                        "player": deepcopy(snapshot.get("player")),
                        "frame": snapshot.get("frame"),
                    }
                )
                objective["rejected_targets"] = rejected[-8:]
                for key in ("target", "target_map_id", "target_map_name", "route_map_ids"):
                    objective.pop(key, None)
            elif routing.get("status") in ("planned", "same_region"):
                objective["route_map_ids"] = deepcopy(routing.get("map_route", []))
        if not objective.get("target"):
            data = load_world_data() if world_data is None else world_data
            objective.update(_clinic_target(snapshot, data, router))
    else:
        # During battle, dialogs, or transitions, keep the intent and target;
        # transient UI state must not be mistaken for proof of unreachability.
        objective["routing"] = {
            "status": "needs_data",
            "reason": "await_stable_overworld_before_route_check",
        }


def plan_support(snapshot, active=None, *, world_data=None, route_planner=None, resource_policy=None):
    """Return a latched ``heal_party`` objective or None; never select inputs.

    None with no active support means no verified critical trigger in a safe
    scene. None with active support means current full recovery was verified.
    Use assess_recovery() if the caller also needs a standalone data-quality
    readout. ``world_data`` can be load_world_data() or a fixture of that shape.
    ``route_planner`` defaults to region-aware plan_route; isolated test maps
    must supply their own router instead of borrowing real terrain coordinates.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    active = active if isinstance(active, dict) and active.get("id") == "heal_party" else None
    threshold = (resource_policy or {}).get("heal_hp_ratio", LOW_HP_RATIO)
    if type(threshold) not in (int, float) or not 0.15 <= threshold <= 0.9:
        threshold = LOW_HP_RATIO
    assessment = assess_recovery(snapshot, low_hp_ratio=threshold)
    if active:
        if assessment["full_recovery"] is True and assessment["stable_overworld"]:
            return None
    elif not (
        assessment["party_verified"]
        and assessment["needs_recovery"] is True
        and assessment["stable_overworld"]
    ):
        return None
    objective = deepcopy(active) if active else {}
    router = route_planner or plan_route
    _refresh_clinic_route(snapshot, objective, assessment["stable_overworld"], world_data, router)
    reason_labels = {
        "no_viable_party": "队伍没有还能战斗的成员",
        "last_viable_member_low_hp": "仅剩一只能战斗的成员且 HP 低于 50%",
        "combined_viable_hp_low": "还能战斗的成员合计 HP 低于 50%",
        "poison_risk": "存在中毒成员，继续行走有掉血风险",
        "status_condition": "还能战斗的成员存在异常状态",
        "damage_pp_exhausted": "可用队员的伤害招式 PP 已枯竭",
    }
    if not active:
        objective["started_evidence"] = deepcopy(assessment)
        objective["reason"] = "；".join(reason_labels[key] for key in assessment["trigger_reasons"])
    target = objective.get("target")
    target_missing = target is None
    routing = objective.get("routing") or {}
    route_ready = routing.get("status") in ("planned", "same_region")
    objective.update(
        {
            "id": "heal_party",
            "intent": "到宝可梦中心回复全队 HP、PP 并清除异常状态",
            "why": objective.get("reason", "继续完成已开始的队伍恢复任务"),
            "status": "needs_recovery",
            "completion": False,
            "needs_recovery": assessment["full_recovery"] is not True,
            "ready_to_navigate": assessment["stable_overworld"]
            and not target_missing
            and route_ready,
            "route_status": routing.get("status", "needs_data"),
            "target_map_id": objective.get("target_map_id"),
            "target": target,
            "interaction_selectors": [deepcopy(target)] if target else [],
            "evidence": assessment,
            "unknown_facts": assessment["missing_evidence"]
            + (["reachable_pokecenter"] if target_missing else [])
            + (["clinic_region_route"] if not route_ready else []),
            "knowledge": [
                "按当前护士对话完成恢复，随后重新验证全队 HP、每个招式 PP 和状态。",
                "本目标保持到恢复得到验证；低等级本身不会改变主线目标。",
                "地图路径和护士位置来自源码先验，途中需要实际观察确认。",
            ],
            "source": {
                "quality": "source_prior",
                "method": "fewest supported source-region portals among reachable clinic interaction targets; live gates still require verification",
                "source_binary_match": False,
            },
        }
    )
    return objective
