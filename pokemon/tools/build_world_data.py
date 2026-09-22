"""Rebuild world priors from the pinned disassembly; no emulator or API calls."""

from pathlib import Path
import re

from world_data import SOURCE_COMMIT


def build(source: Path) -> dict:
    def read(relative):
        return (source / relative).read_text()

    def number(value):
        value = value.strip()
        return int(value[1:], 16) if value.startswith("$") else int(value)

    def constants(relative):
        result = {}
        index = 0
        for line in read(relative).splitlines():
            code = line.split(";")[0].strip()
            reset = re.match(r"const_value\s*=\s*(\$[\da-fA-F]+|\d+)$", code)
            if reset:
                index = number(reset[1])
            match = re.match(r"const\s+(\w+)$", code)
            if match:
                result[match[1]] = index
                index += 1
        return result

    maps = {}
    symbols = {}
    for i, match in enumerate(
        re.finditer(
            r"^\s*mapconst\s+(\w+),\s*(\d+),\s*(\d+)", read("constants/map_constants.asm"), re.M
        )
    ):
        symbol, height, width = match.groups()
        symbols[symbol] = i
        maps[str(i)] = {
            "map_id": i,
            "name": symbol,
            "width": int(width) * 2,
            "height": int(height) * 2,
            "warps": [],
            "objects": [],
            "signs": [],
            "connections": [],
            "quality": "source_prior",
        }
    sprite_ids = constants("constants/sprite_constants.asm")
    for header in sorted((source / "data/mapHeaders").glob("*.asm")):
        code = header.read_text()
        dimensions = re.search(r"db\s+(\w+)_HEIGHT,\s*(\w+)_WIDTH", code)
        if not dimensions or dimensions[1] not in symbols:
            continue
        entry = maps[str(symbols[dimensions[1]])]
        entry["source_header"] = str(header.relative_to(source))
        for connection in re.finditer(r"(NORTH|SOUTH|WEST|EAST)_MAP_CONNECTION\s+([^\n;]+)", code):
            args = [a.strip() for a in connection[2].split(",")]
            entry["connections"].append(
                {
                    "direction": connection[1].lower(),
                    "destination_map_id": symbols.get(args[1]),
                    "destination_name": args[1],
                    "offset_blocks": number(args[2]),
                    "quality": "source_prior",
                }
            )
        obj_ref = re.search(r"dw\s+(\w+Object)\b", code)
        if not obj_ref:
            continue
        object_file = next(
            (
                p
                for p in (source / "data/mapObjects").glob("*.asm")
                if re.search(r"^" + re.escape(obj_ref[1]) + ":", p.read_text(), re.M)
            ),
            None,
        )
        if not object_file:
            continue
        entry["source_objects"] = str(object_file.relative_to(source))
        for raw in object_file.read_text().splitlines():
            content, _, comment = raw.partition(";")
            line = content.strip()
            if line.startswith("warp "):
                x, y, warp_id, dst = [p.strip() for p in line[5:].split(",")]
                destination = symbols[dst] if dst in symbols else number(dst)
                entry["warps"].append(
                    {
                        "warp_id": len(entry["warps"]),
                        "x": number(x),
                        "y": number(y),
                        "destination_warp_id": number(warp_id),
                        "destination_map_id": destination,
                        "destination_name": maps.get(str(destination), {}).get(
                            "name", "LAST_OUTDOOR_MAP" if destination == -1 else dst
                        ),
                        "description": comment.strip(),
                        "quality": "source_prior",
                    }
                )
            elif line.startswith("sign "):
                x, y, text_id = [number(p) for p in line[5:].split(",")]
                entry["signs"].append(
                    {
                        "x": x,
                        "y": y,
                        "text_id": text_id,
                        "description": comment.strip(),
                        "quality": "source_prior",
                    }
                )
            elif line.startswith("object "):
                args = [p.strip() for p in line[7:].split(",")]
                entry["objects"].append(
                    {
                        "object_id": len(entry["objects"]) + 1,
                        "sprite": args[0],
                        "sprite_id": sprite_ids.get(args[0]),
                        "x": number(args[1]),
                        "y": number(args[2]),
                        "movement": args[3],
                        "facing_or_range": args[4],
                        "text_id": number(args[5]),
                        "kind": "trainer"
                        if len(args) > 7
                        else "item"
                        if len(args) > 6
                        else "npc_or_interactive",
                        "extra": args[6:],
                        "description": comment.strip(),
                        "quality": "source_prior",
                    }
                )
    # These are script conditions, not recommended keypress sequences.
    maps["0"]["script_triggers"] = [
        {
            "condition": "player.y == 1 and EVENT_FOLLOWED_OAK_INTO_LAB is unset",
            "effect": "Oak appears and leads the player into his lab before choosing a starter",
            "source": "scripts/pallettown.asm:PalletTownScript0",
            "quality": "source_prior",
        }
    ]
    moves = {}
    for match in re.finditer(r"^\s*move\s+([^\n;]+)", read("data/moves.asm"), re.M):
        args = [a.strip() for a in match[1].split(",")]
        if len(args) != 6:
            continue
        moves[str(len(moves) + 1)] = {
            "name": args[0],
            "effect": args[1],
            "power": number(args[2]),
            "type": args[3],
            "accuracy_percent": number(args[4]),
            "base_pp": number(args[5]),
            "quality": "source_prior",
        }
    effects = constants("constants/move_effect_constants.asm")
    types = {
        m[1]: int(m[2], 16)
        for m in re.finditer(
            r"^(\w+)\s+EQU\s+\$([0-9a-fA-F]+)", read("constants/type_constants.asm"), re.M
        )
    }
    move_bytes = bytearray()
    for index, move in moves.items():
        move["type_id"] = types[move["type"]]
        move_bytes.extend(
            (
                int(index),
                effects[move["effect"]],
                move["power"],
                types[move["type"]],
                move["accuracy_percent"] * 255 // 100,
                move["base_pp"],
            )
        )
    effects_table = [
        {"attack_type_id": types[m[1]], "defender_type_id": types[m[2]], "factor_tenths": int(m[3])}
        for m in re.finditer(
            r"^\s*db\s+(\w+),\s*(\w+),\s*(\d+)", read("data/type_effects.asm"), re.M
        )
    ]
    effects_bytes = bytes(
        [
            value
            for row in effects_table
            for value in (row["attack_type_id"], row["defender_type_id"], row["factor_tenths"])
        ]
        + [255]
    )
    events = {}
    for name, index in constants("constants/event_constants.asm").items():
        if name.startswith("EVENT_") and not re.fullmatch(r"EVENT_[0-9A-F]{3}", name):
            events[name] = {"offset": index // 8, "bit": index % 8}
    # Entire name tables can be matched against the user's ROM independently
    # of the non-identical source build. Table index determines the public ID.
    alphabet = {
        m[1]: int(m[2], 16)
        for m in re.finditer(
            r'^\s*charmap\s+"([^"\n]+)",\s*\$([0-9a-fA-F]+)', read("charmap.asm"), re.M
        )
    }

    def name_table(relative):
        names = re.findall(r'^\s*db\s+"([^"\n]*)"', read(relative), re.M)
        encoded = bytearray()
        for name in names:
            while name:
                token = next(
                    (
                        key
                        for key in sorted(alphabet, key=len, reverse=True)
                        if name.startswith(key)
                    ),
                    None,
                )
                if token is None:
                    raise ValueError("Unknown source character: " + name)
                encoded.append(alphabet[token])
                name = name[len(token) :]
        return {
            "source": relative,
            "names": names,
            "bytes_hex": encoded.hex(),
            "quality": "source_prior_until_complete_table_matches_exact_rom",
        }

    return {
        "schema_version": 1,
        "source_repository": "https://github.com/Rangi42/redstarbluestar",
        "source_commit": SOURCE_COMMIT,
        "rom_sha1": "e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9",
        "source_binary_match": False,
        "coordinate_units": "walking tile (16 screen pixels); block dimensions multiplied by 2",
        "limitations": "Source priors require live map/warp/occupancy cross-checks. No prior proves an event occurred.",
        "maps": maps,
        "moves": moves,
        "events": events,
        "types": {
            str(value): {
                "name": name,
                "name_quality": "source_prior",
                "source": "constants/type_constants.asm",
            }
            for name, value in types.items()
        },
        "type_chart": {
            "source": "data/type_effects.asm",
            "entries": effects_table,
            "bytes_hex": effects_bytes.hex(),
            "quality": "source_prior_until_entire_table_matches_exact_rom",
        },
        "move_data_table": {
            "source": "data/moves.asm",
            "stride": 6,
            "count": len(moves),
            "fields": [
                "move_id",
                "effect_id",
                "power",
                "type_id",
                "accuracy_times_255_div_100",
                "base_pp",
            ],
            "bytes_hex": move_bytes.hex(),
            "quality": "source_prior_until_complete_table_matches_exact_rom",
        },
        "name_tables": {
            "items": name_table("text/item_names.asm"),
            "moves": name_table("text/move_names.asm"),
        },
        "species": {
            str(i): name for name, i in constants("constants/pokemon_constants.asm").items()
        },
        "items": {str(i): name for name, i in constants("constants/item_constants.asm").items()},
    }
