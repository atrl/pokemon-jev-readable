"""High-level planning bridge: deterministic stall signals + an optional
external planner model. The planner is advisory only.

The local runtime stays the source of verified facts and physical tools. When a
deterministic stall signal fires, this module builds a compact situation report
and (if ``DEEPSEEK_API_KEY`` is configured) asks a planner model for one
structured plan. The plan is stored as guidance and surfaced to JEV, which
still chooses every physical input. Any network/parse failure degrades to the
existing deterministic behaviour; nothing here presses buttons or invents
facts.
"""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

DEFAULT_PLAN_TTL = 80
DEFAULT_MIN_INTERVAL = 60
DEFAULT_NO_TILE_TRIGGER = 120
DEFAULT_RECENT_HEAL_TRIGGER = 2
DEFAULT_RECENT_WINDOW = 300

PLAN_ACTIONS = ("fight", "run", "catch", "heal", "buy_balls", "explore", "navigate")
WILD_POLICIES = ("fight", "run", "catch")

POKE_BALL_ITEM_ID = 3  # pinned Red Star item table; POKé BALL@


def pokeball_count(bag) -> int:
    """Count only balls whose current inventory identity is verified."""
    if not isinstance(bag, list):
        return 0
    total = 0
    for row in bag:
        if not isinstance(row, dict):
            continue
        quantity = row.get("quantity")
        if not isinstance(quantity, int) or quantity <= 0:
            continue
        name = str(row.get("name_prior") or row.get("name") or "").upper()
        if row.get("name_verified") is True and (
            row.get("item_id") == POKE_BALL_ITEM_ID or "BALL" in name
        ):
            total += quantity
    return total


def _recent_heal_count(objective_history, window: int) -> int:
    entries = objective_history if isinstance(objective_history, list) else []
    latest = max((row.get("step", 0) for row in entries if isinstance(row, dict)), default=0)
    return sum(
        1
        for row in entries
        if isinstance(row, dict)
        and row.get("to") == "heal_party"
        and latest - row.get("step", 0) <= window
    )


def _party_summary(party) -> list:
    result = []
    if not isinstance(party, list):
        return result
    for mon in party:
        if not isinstance(mon, dict):
            continue
        moves = [
            {"name": (m.get("knowledge") or {}).get("name"), "pp": m.get("pp")}
            for m in (mon.get("moves") or [])
            if isinstance(m, dict)
        ]
        result.append(
            {
                "species": mon.get("species_name_prior") or mon.get("nickname"),
                "level": mon.get("level"),
                "hp": mon.get("hp"),
                "max_hp": mon.get("max_hp"),
                "status_bits": mon.get("status_bits"),
                "moves": moves,
            }
        )
    return result


def _bag_summary(bag) -> list:
    if not isinstance(bag, list):
        return []
    return [
        {"name": row.get("name_prior") or row.get("name"), "quantity": row.get("quantity")}
        for row in bag
        if isinstance(row, dict) and isinstance(row.get("quantity"), int) and row["quantity"] > 0
    ]


def _battle_summary(battle) -> dict | None:
    if not isinstance(battle, dict) or battle.get("active") is not True:
        return None
    player = battle.get("player") or {}
    enemy = battle.get("enemy") or {}
    return {
        "type": battle.get("type"),
        "phase": battle.get("phase"),
        "menu": battle.get("menu"),
        "player": {"species": player.get("species_name_prior"), "level": player.get("level"),
                   "hp": player.get("hp"), "max_hp": player.get("max_hp")},
        "enemy": {"species": enemy.get("species_name_prior"), "level": enemy.get("level"),
                  "hp": enemy.get("hp"), "max_hp": enemy.get("max_hp")},
    }


def build_situation(
    observation: dict,
    campaign: dict,
    progress: dict,
    *,
    recent_window: int = DEFAULT_RECENT_WINDOW,
) -> dict:
    """Compact, verified-only view for stall detection and the planner."""
    observation = observation if isinstance(observation, dict) else {}
    campaign = campaign if isinstance(campaign, dict) else {}
    progress = progress if isinstance(progress, dict) else {}
    world = observation.get("world") or {}
    player = observation.get("player") or {}
    objective = campaign.get("active_objective") or {}
    navigation = campaign.get("navigation") or {}
    history = campaign.get("objective_history") or []
    milestones = observation.get("milestones") or {}
    badges = None
    badge_count = milestones.get("badge_count")
    if isinstance(badge_count, dict) and badge_count.get("verified") is True:
        badges = badge_count.get("value")
    plan = campaign.get("plan") if isinstance(campaign.get("plan"), dict) else None
    return {
        "step": progress.get("total_steps"),
        "objective": {"id": objective.get("id"), "intent": objective.get("intent"),
                      "why": objective.get("why"), "target_map_id": objective.get("target_map_id")},
        "map": {"id": world.get("map_id"), "name": world.get("name"),
                "width": world.get("width"), "height": world.get("height")},
        "position": [player.get("x"), player.get("y")],
        "visited_map_ids": campaign.get("visited_map_ids", []),
        "route_map_names": campaign.get("route_map_names", []),
        "route_status": navigation.get("status"),
        "loop_detected": bool(progress.get("loop_detected")),
        "loop_kind": progress.get("loop_kind"),
        "steps_since_new_tile": progress.get("steps_since_new_tile"),
        "same_position_steps": progress.get("same_position_steps"),
        "visited_tiles": progress.get("visited_tiles"),
        "recent_heal_count": _recent_heal_count(campaign.get("objective_history"), recent_window),
        "party": _party_summary(observation.get("party")),
        "bag": _bag_summary(observation.get("bag")),
        "pokeballs": pokeball_count(observation.get("bag")),
        "badges": badges,
        "battle": _battle_summary(observation.get("battle")),
        "recent_actions": progress.get("recent_effects", [])[-8:],
        "plan_active": bool(plan),
        "plan_subgoal": (plan or {}).get("subgoal"),
    }


def planning_reasons(situation: dict) -> list[str]:
    """Deterministic stall signals. Empty list means no escalation needed.

    ``no_escape_items`` is informational (it is added to the planner report) and
    does not by itself request a plan; a real stall signal must also be present.
    """
    situation = situation if isinstance(situation, dict) else {}
    reasons = []
    if (situation.get("steps_since_new_tile") or 0) >= DEFAULT_NO_TILE_TRIGGER:
        reasons.append("no_new_tile")
    if situation.get("loop_detected"):
        reasons.append("loop_detected")
    if (situation.get("recent_heal_count") or 0) >= DEFAULT_RECENT_HEAL_TRIGGER:
        reasons.append("repeated_healing")
    if (situation.get("pokeballs") or 0) == 0 and (situation.get("party") or []) and len(
        situation.get("party") or []
    ) < 2:
        reasons.append("no_escape_items")
    return reasons


STALL_REASONS = ("no_new_tile", "loop_detected", "repeated_healing")


def needs_planning(situation: dict, *, min_interval: int = DEFAULT_MIN_INTERVAL) -> bool:
    if situation.get("plan_active"):
        return False
    since = situation.get("steps_since_plan")
    if isinstance(since, int) and since < min_interval:
        return False
    return any(reason in STALL_REASONS for reason in planning_reasons(situation))


def planner_configured() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def valid_plan(data) -> dict:
    """Normalize a planner response or raise ValueError. Never guesses IDs."""
    if not isinstance(data, dict):
        raise ValueError("plan is not an object")
    subgoal = data.get("subgoal")
    intent = data.get("intent")
    if not isinstance(subgoal, str) or not subgoal.strip():
        raise ValueError("plan.subgoal is required")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("plan.intent is required")
    target_map_id = data.get("target_map_id")
    if target_map_id is not None and type(target_map_id) is not int:
        raise ValueError("plan.target_map_id must be an int or null")
    resource = data.get("resource_policy") or {}
    if not isinstance(resource, dict):
        raise ValueError("plan.resource_policy must be an object")
    wild = resource.get("wild_battle")
    if wild is not None and wild not in WILD_POLICIES:
        raise ValueError("plan.resource_policy.wild_battle is invalid")
    expires = data.get("expires_steps", DEFAULT_PLAN_TTL)
    if type(expires) is not int:
        raise ValueError("plan.expires_steps must be an int")
    expires = max(10, min(1000, expires))
    selectors = []
    for row in data.get("selectors") or []:
        if not isinstance(row, dict):
            raise ValueError("plan.selectors entries must be objects")
        selector = {"kind": row.get("kind", "object")}
        if row.get("sprite") is not None:
            selector["sprite"] = row["sprite"]
        if row.get("text_id") is not None:
            selector["text_id"] = row["text_id"]
        if row.get("x") is not None:
            selector["x"] = row["x"]
        if row.get("y") is not None:
            selector["y"] = row["y"]
        selectors.append(selector)
    return {
        "subgoal": subgoal.strip()[:80],
        "intent": intent.strip()[:400],
        "reasoning": str(data.get("reasoning") or "").strip()[:600],
        "target_map_id": target_map_id,
        "selectors": selectors,
        "resource_policy": {
            "wild_battle": wild,
            "catch_species": resource.get("catch_species"),
            "heal_hp_ratio": resource.get("heal_hp_ratio"),
        },
        "milestones": [str(x)[:120] for x in (data.get("milestones") or [])][:6],
        "expires_steps": expires,
    }


PLANNER_SYSTEM_PROMPT = (
    "You are the high-level planner for a read-only Pokemon Red Star agent. "
    "A deterministic RAM reader supplies verified facts; a separate model (JEV) "
    "chooses every physical button. You never output button sequences. "
    "Given a compact situation report, choose ONE concrete, short-horizon subgoal "
    "and a resource policy that will get the agent unstuck and closer to the main "
    "story (defeat the League Champion, enter the Hall of Fame). "
    "Prefer cheap, robust actions: flee wild battles to save time, buy Poké Balls "
    "at a Mart (target_map_id 42 VIRIDIAN_MART or 56 PEWTER_MART) and catch 1-2 "
    "backup Pokemon when the party is tiny, heal only when necessary. "
    "When stuck in a multi-floor cave, pick the specific next map or floor as "
    "target_map_id and describe the route hint. Use only map ids present in the "
    "situation report; do not invent ids. "
    "Return ONLY JSON with keys: subgoal (short id), intent (one sentence), "
    "reasoning (brief), target_map_id (int or null), selectors (array of "
    "{kind,sprite,text_id,x,y}, optional), resource_policy "
    "({wild_battle: fight|run|catch, catch_species, heal_hp_ratio}), "
    "milestones (array of short strings), expires_steps (int 10..1000)."
)


def call_planner(situation: dict, goal: str, *, timeout: int = 40) -> dict:
    """Ask the configured planner model. Raises on any failure (caller degrades)."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    base = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"overall_goal": goal, "situation": situation},
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        raise ValueError("planner returned no message content")
    plan = valid_plan(json.loads(content))
    plan["model"] = payload.get("model", model)
    plan["usage"] = payload.get("usage")
    return plan
