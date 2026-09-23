"""Build the exact JEV input: verified state, current focus, and nine choices.

This module is pure request construction. It never calls the API or presses a
button. The static control instructions live in prompts/button.txt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from battle_strategy import plan_battle
from controls import BUTTONS
from planning import pokeball_count

BUTTON_INSTRUCTIONS = " ".join(
    (Path(__file__).parent / "prompts/button.txt").read_text().splitlines()
)

DEFAULT_GAME_GOAL = (
    "Complete Pokemon Red Star's main story: defeat the Pokemon League Champion "
    "and reach the Hall of Fame. Treat exploration, navigation, team preparation "
    "and battles as subgoals serving that objective. Claim completion only from "
    "verified in-game evidence, never from action count, visited coordinates or confidence."
)


def verified_player(player: dict | None) -> dict | None:
    if not isinstance(player, dict):
        return None
    result = {key: player[key] for key in ("name", "map_id", "x", "y") if key in player}
    if player.get("facing_quality") == "verified_direction_response":
        result["facing"] = player.get("facing")
    return result


def observation_for_model(observation: dict) -> dict:
    """Only expose facts with an explicit validation boundary; blank text is not a mode."""
    raw_text = observation.get("screen_text") or {}
    rows = [
        row.rstrip() for row in raw_text.get("rows", []) if isinstance(row, str) and row.strip()
    ]
    scene = observation.get("scene") or {}
    verified_scene = scene.get("verified") is True and scene.get("mode") in (
        "overworld",
        "dialog",
        "main_menu",
        "battle",
        "name_entry",
        "species_preview",
    )
    mode = scene["mode"] if verified_scene else "unknown"
    dialog = observation.get("dialog") or {}
    local_map = observation.get("local_map") or {}
    if mode == "overworld":
        rows = []  # Background tile IDs share the font namespace; they are not prose.
    elif mode == "dialog" and isinstance(dialog.get("text"), str):
        rows = [line for line in dialog["text"].splitlines() if line.strip()]
    main_menu_visible = mode == "main_menu" or (
        not observation.get("scene") and "PACK" in "\n".join(rows) and "SAVE" in "\n".join(rows)
    )
    facts = observation.get("milestones") or {}
    party_fact = facts.get("party_count") or {}
    party = observation.get("party")
    party_verified = (
        isinstance(party, list)
        and party_fact.get("verified") is True
        and party_fact.get("value") == len(party)
    )
    bag = observation.get("bag")
    bag_verified = isinstance(bag, list) and (
        not bag or all(row.get("name_verified") is True for row in bag)
    )
    battle = observation.get("battle") or {}
    if battle.get("verified"):
        battle = dict(battle)
        if battle.get("active") is True:
            battle["strategy"] = plan_battle(observation)
        if isinstance(battle.get("enemy"), dict):
            battle["enemy"] = {k: v for k, v in battle["enemy"].items() if k != "moves"}
    else:
        battle = {
            **{
                key: battle.get(key)
                for key in (
                    "active",
                    "type",
                    "phase",
                    "phase_verified",
                    "combatants_ready",
                    "menu",
                    "visible_text",
                    "awaiting_input",
                )
            },
            "verified": False,
            "quality": "needs_data",
        }
    return {
        "game": observation.get("game"),
        "scene": {
            "mode": mode,
            "verified": verified_scene,
            "source": scene.get("source"),
            "quality": scene.get("quality", "needs_data"),
            "validation_scope": scene.get("validation_scope"),
        },
        "dialog": {key: dialog.get(key) for key in ("open", "awaiting_input", "text", "quality")}
        if verified_scene
        else {"open": None, "awaiting_input": None, "text": None, "quality": "needs_data"},
        "screen_text": {
            "rows": rows,
            "source": raw_text.get("source", "RAM wTileMap"),
            "limitations": "Text may be partial. Overworld background is excluded; in unknown scenes tile IDs may resemble letters. Blank text does not mean loading.",
        },
        "player": verified_player(observation.get("player")),
        "local_map": {
            key: local_map.get(key)
            for key in (
                "rows",
                "player_cell",
                "neighbors",
                "legend",
                "quality",
                "source",
                "validation_scope",
                "limitations",
            )
        }
        if mode == "overworld"
        and local_map.get("verified") is True
        and local_map.get("quality") == "advisory_background_only"
        else None,
        "party": party if party_verified else ([] if party == [] else None),
        "party_state": observation.get("party_state"),
        "bag": bag if bag_verified else None,
        "world": observation.get("world"),
        "milestones": facts,
        "battle": battle,
        "main_menu_cursor": observation.get("menu_cursor_raw") if main_menu_visible else None,
        "progress": observation.get("progress"),
        "unavailable": (
            ["party details: not verified in this observation"] if not party_verified else []
        )
        + (["inventory identities: not verified in this observation"] if not bag_verified else [])
        + [
            "Source-prior story/map facts are labelled separately; unverified event flags do not prove progress.",
            "NPCs may move; inferred paths must be checked after each input.",
        ],
    }


def compact_campaign(game: dict, campaign: dict) -> dict:
    """Keep the current task and relevant world fields in the model budget."""
    if campaign:
        objective = campaign.get("active_objective") or {}
        wanted = set((objective.get("completion_evidence") or {})) | {
            "party_count",
            "badge_count",
            "game_completed",
        }
        game["milestones"] = {
            k: v for k, v in (game.get("milestones") or {}).items() if k in wanted
        }
        world = game.get("world") or {}
        game["world"] = {
            k: world.get(k)
            for k in (
                "map_id",
                "name",
                "width",
                "height",
                "quality",
                "source_match",
                "player_position_valid",
                "input_lock",
            )
        }
        game["world"]["warps"] = [
            {k: w.get(k) for k in ("x", "y", "destination_map_id", "destination_name", "quality")}
            for w in world.get("warps", [])
        ]
        game["world"]["objects"] = [
            {k: o.get(k) for k in ("object_id", "sprite", "x", "y", "text_id", "active", "quality")}
            for o in world.get("objects", [])
            if o.get("active") is not False
        ]
        game["world"]["connections"] = world.get("connections", [])
        campaign = {
            **campaign,
            "active_objective": {
                k: objective.get(k)
                for k in (
                    "id",
                    "intent",
                    "why",
                    "status",
                    "knowledge",
                    "completion",
                    "completion_evidence",
                    "unknown_facts",
                    "target_map_id",
                    "milestones",
                    "resource_policy",
                )
            },
            "observed_map_connections": campaign.get("observed_map_connections", [])[-8:],
        }
    return campaign


def recent_actions(progress: dict, history: list[dict]) -> list[dict]:
    """Prefer the tracker; accept caller-provided history for standalone API use."""
    recent = progress.get("recent_effects")
    if not isinstance(recent, list):
        recent = [
            {
                "button": row.get("button"),
                "before": verified_player(row.get("before")),
                "after": verified_player(row.get("after")),
                "text_after": " ".join(
                    str(x).strip() for x in (row.get("text_after") or []) if str(x).strip()
                )[-180:],
            }
            for row in history[-12:]
        ]
    if isinstance(progress.get("recent_effects"), list):
        recent = [
            {
                "button": row.get("button"),
                "from": row.get("before"),
                "to": row.get("after"),
                "result": "new_tile"
                if row.get("new_tile")
                else "moved_known_tile"
                if row.get("position_changed")
                else "no_coordinate_change",
                "ui_effect": "dialog_opened"
                if row.get("dialog_opened")
                else "dialog_closed"
                if row.get("dialog_closed")
                else "text_changed"
                if row.get("text_changed")
                else "unchanged",
            }
            for row in progress["recent_effects"][-8:]
        ]
    return recent


def _battle_policy(game: dict, campaign: dict) -> tuple[str, bool, int]:
    """Return (policy, disengage, pokeballs).

    Wild battles default to fleeing to save real time; a planner plan may ask to
    catch or fight instead. Trainer battles never flee.
    """
    battle = game.get("battle") or {}
    wild = battle.get("type") == "wild"
    balls = pokeball_count(game.get("bag"))
    plan = campaign.get("plan") if isinstance(campaign.get("plan"), dict) else {}
    policy = (plan.get("resource_policy") or {}).get("wild_battle")
    if not wild:
        return "fight", False, balls
    if policy == "catch" and balls > 0:
        return "catch", True, balls
    if policy in ("run", "catch"):
        return "run", True, balls
    if policy == "fight":
        return "fight", False, balls
    return "run", True, balls


def focus_and_choices(game: dict, campaign: dict, progress: dict, feedback: dict):
    """Overlay task intent, battle text, then a concrete battle-menu suggestion.

    Return the final current_focus and all nine physical input descriptions.
    This produces advice only; the API still selects the executed button.
    """
    criteria = dict(BUTTONS)
    criteria["a"] = (
        "Press A once. Confirm/advance an OPEN dialog or menu; in OVERWORLD this starts another interaction with the faced object. A does not walk. Reopening a completed repeated interaction is not exploration."
    )
    criteria["wait"] = (
        "Release all buttons and advance a short time for observed printing/animation/transition. In a verified OVERWORLD this stays still; blank text alone is not evidence that waiting is needed."
    )
    neighbors = (game.get("local_map") or {}).get("neighbors") or {}
    battle_active = (game.get("battle") or {}).get("active") is True
    plan = campaign.get("plan") if isinstance(campaign.get("plan"), dict) else {}
    current_focus = (campaign.get("active_objective") or {}).get("intent") or progress.get(
        "current_focus", "Use verified observations to advance the goal."
    )
    if plan and not battle_active:
        current_focus = f"模型规划的子目标：{plan.get('intent')}。" + current_focus
    disengage = False
    if battle_active:
        current_focus = "Resolve the current battle UI first. Advancing battle introduction/text usually needs A; choose FIGHT and a usable damaging move when its menu appears. Resume the story objective after battle."
        criteria["a"] = (
            "Press A to acknowledge battle text or confirm the selected battle command/move. Battle text uses its own text box: overworld dialog.open may be null. Combatants not yet initialized does not mean that A is unavailable."
        )
        criteria["wait"] = (
            "Wait only for a visibly changing battle animation or text being printed. Waiting does not acknowledge completed challenge, encounter or send-out text; those need A."
        )
        battle = game["battle"]
        text = " ".join(str(battle.get("visible_text") or "").split())
        if (
            battle.get("phase_verified") is True
            and battle.get("phase") == "text_before_combatants_ready"
            and "wants to fight!" in text.lower()
        ):
            battle["input_guidance"] = {
                "suggested_button": "a",
                "reason": "The complete trainer challenge is waiting for acknowledgement. Combatant data is initialized after this text advances; waiting for those fields first can deadlock the controller.",
                "source": "Exact-ROM trainer-intro save regression; advisory game-control knowledge, not a physical input override",
            }
            current_focus = "Acknowledge the visible trainer challenge with A, then inspect the newly initialized battle. Repeating WAIT on this completed message has no effect."
            criteria["a"] += (
                " CURRENT STATE: the complete trainer challenge says wants to fight! A advances this acknowledgement before combatant initialization."
            )
            criteria["wait"] = (
                "CURRENT STATE: completed trainer challenge awaiting acknowledgement. WAIT leaves this message unchanged; missing combatant data is not a reason to keep waiting. A is the relevant acknowledgement input."
            )
        policy, disengage, balls = _battle_policy(game, campaign)
        if disengage:
            if policy == "catch":
                current_focus = (
                    f"这是野外战斗，可以捕获且背包有 {balls} 个精灵球：在指令菜单中选择 ITEM（左下），"
                    "选中 POKé BALL 并用 A 确认；投球后再按提示推进文本。"
                )
            else:
                current_focus = (
                    "这是野外战斗，默认逃跑以节省时间：把指令光标移到 RUN（右下）并用 A 确认。"
                    "遇到训练家战斗不能逃跑，改为选择 FIGHT + 有效伤害招式。"
                )
            if battle.get("menu") == "move":
                current_focus += " 当前在招式列表：先按 B 返回指令菜单，再移动到目标指令。"
            battle["battle_policy"] = {
                "policy": policy,
                "pokeballs": balls,
                "source": "deterministic wild-battle time policy plus planner resource_policy; advisory, JEV selects the input",
            }
    visits = feedback.get("neighbor_visits") or {}
    for direction in ("up", "down", "left", "right"):
        criteria[direction] = {
            "input": BUTTONS[direction],
            "background_neighbor": neighbors.get(direction),
            "observed_neighbor_visits": visits.get(direction),
            "meaning": "One physical directional input; may first turn, move if possible, or move a menu cursor. Background is advisory, not proof of a clear path.",
        }
    if disengage:
        def _note(name, text):
            if isinstance(criteria[name], dict):
                criteria[name] = {**criteria[name], "current_battle_advice": text}
            else:
                criteria[name] += " " + text

        if (game.get("battle") or {}).get("menu") == "move":
            _note("b", "CURRENT MENU: B leaves the move list and returns to the command menu.")
        elif policy == "catch":
            _note("up", "CURRENT MENU: ITEM is the bottom-left battle command; move there to open the bag.")
            _note("left", "CURRENT MENU: ITEM is the bottom-left battle command; move there to open the bag.")
        else:
            _note("down", "CURRENT MENU: RUN is the bottom-right battle command; move there to flee.")
            _note("right", "CURRENT MENU: RUN is the bottom-right battle command; move there to flee.")
    strategy = (game.get("battle") or {}).get("strategy") or {}
    recommended = strategy.get("next_button")
    if recommended in BUTTONS and not disengage:
        selected = (game.get("battle") or {}).get("selected_move_slot")
        current_focus = f"Current battle menu: use the disclosed damage estimate to select {strategy.get('recommended_move')} (slot {strategy.get('recommended_slot')}); visible selected move slot is {selected}. Suggested next input is {recommended}; inspect the result before another input."
        advice = {
            "source": "verified combatants and ROM move/type data; conditional noncritical damage estimate",
            "recommended_move": strategy.get("recommended_move"),
            "recommended_slot": strategy.get("recommended_slot"),
            "suggested_input": recommended,
            "actual_input_owner": "JEV",
        }
        if isinstance(criteria[recommended], dict):
            criteria[recommended] = {**criteria[recommended], "current_battle_advice": advice}
        else:
            criteria[recommended] += " CURRENT BATTLE ADVICE: " + json.dumps(
                advice, ensure_ascii=False
            )
        if (game.get("battle") or {}).get("menu") == "move" and selected != strategy.get(
            "recommended_slot"
        ):
            criteria["a"] += (
                " CURRENT MENU: A would use the currently selected move; move the cursor to the recommended move first if the estimate applies."
            )
    return current_focus, criteria


def build_request(observation: dict, goal: str, history: list[dict]) -> dict:
    game = observation_for_model(observation)
    campaign = observation.get("campaign") or {}
    progress = game.pop("progress", None) or {}
    campaign = compact_campaign(game, campaign)
    recent = recent_actions(progress, history)
    temporal = progress.get("recent_transitions", [])[-3:]
    feedback = {
        key: value
        for key, value in progress.items()
        if key not in ("recent_effects", "recent_transitions", "current_focus")
    }
    current_focus, criteria = focus_and_choices(game, campaign, progress, feedback)
    return {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "goal": goal,
            "campaign": campaign,
            "current_focus": current_focus,
            "game": game,
            "feedback": feedback,
            "recent_actions": recent,
            "temporal_context": {
                "order": "oldest_to_newest",
                "meaning": "Up to three actual action-aligned before/after observations, not video frames. Null means not observed/validated.",
                "frame_scope": "Frame counters may reset on checkpoint load; order by transition step and compare frames only within one before/after pair.",
                "transitions": temporal,
            },
        },
        "questions": {
            "button": {
                "type": "choice",
                "criteria": criteria,
                "instructions": BUTTON_INSTRUCTIONS,
            }
        },
    }
