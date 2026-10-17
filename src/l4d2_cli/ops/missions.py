"""Mission grouping migrated from L4D2 Server Manager (MIT, Wizard23333, 2026)."""

import re

def strip_keyvalues_comments(text):
    cleaned = []
    for line in text.splitlines():
        in_quote = False
        escaped = False
        result = []
        index = 0
        while index < len(line):
            char = line[index]
            if char == "\\" and in_quote and not escaped:
                escaped = True
                result.append(char)
                index += 1
                continue
            if char == '"' and not escaped:
                in_quote = not in_quote
            escaped = False
            if not in_quote and line[index:index + 2] == "//":
                break
            result.append(char)
            index += 1
        cleaned.append("".join(result))
    return "\n".join(cleaned)

def keyvalues_tokens(text):
    pattern = re.compile(r'"([^"]*)"|([{}])')
    for match in pattern.finditer(strip_keyvalues_comments(text)):
        yield match.group(1) if match.group(1) is not None else match.group(2)

def parse_keyvalues(text):
    tokens = list(keyvalues_tokens(text))
    index = 0

    def parse_object():
        nonlocal index
        data = {}
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "}":
                break
            if token == "{":
                continue
            if index < len(tokens) and tokens[index] == "{":
                index += 1
                data[token] = parse_object()
            elif index < len(tokens):
                data[token] = tokens[index]
                index += 1
        return data

    parsed = {}
    while index < len(tokens):
        key = tokens[index]
        index += 1
        if index < len(tokens) and tokens[index] == "{":
            index += 1
            parsed[key] = parse_object()
        elif index < len(tokens):
            parsed[key] = tokens[index]
            index += 1
    return parsed

def campaign_id(title, fallback):
    value = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return value or fallback

def natural_map_key(value):
    parts = re.split(r"(\d+)", value)
    return [int(part) if part.isdigit() else part.lower() for part in parts]

def dict_get_ci(data, key):
    if not isinstance(data, dict):
        return None
    key_lower = key.lower()
    for item_key, value in data.items():
        if isinstance(item_key, str) and item_key.lower() == key_lower:
            return value
    return None

def mission_root(parsed):
    mission = dict_get_ci(parsed, "mission")
    if isinstance(mission, dict):
        return mission
    if not isinstance(parsed, dict):
        return None
    for value in parsed.values():
        if not isinstance(value, dict):
            continue
        modes = dict_get_ci(value, "modes")
        coop = dict_get_ci(modes, "coop") if isinstance(modes, dict) else None
        if isinstance(coop, dict):
            return value
    return None

def campaign_from_mission_text(text, fallback, installed_maps):
    try:
        parsed = parse_keyvalues(text)
    except Exception:
        return None
    mission = mission_root(parsed)
    if not isinstance(mission, dict):
        return None
    modes = dict_get_ci(mission, "modes")
    coop = dict_get_ci(modes, "coop") if isinstance(modes, dict) else None
    if not isinstance(coop, dict):
        return None
    title = dict_get_ci(mission, "DisplayTitle") or dict_get_ci(mission, "Name") or fallback
    installed_by_lower = {name.lower(): name for name in installed_maps}
    maps = []
    for chapter_key in sorted(coop, key=lambda value: int(value) if value.isdigit() else 9999):
        entry = coop[chapter_key]
        if not isinstance(entry, dict):
            continue
        map_name = dict_get_ci(entry, "Map")
        if not map_name:
            continue
        canonical_map_name = installed_by_lower.get(str(map_name).lower())
        if not canonical_map_name:
            continue
        display_name = str(dict_get_ci(entry, "DisplayName") or "").strip() or canonical_map_name
        maps.append(
            {
                "name": canonical_map_name,
                "display_name": display_name,
                "chapter": int(chapter_key) if chapter_key.isdigit() else len(maps) + 1,
            }
        )
    if not maps:
        return None
    return {
        "id": f"mission_{campaign_id(title, fallback)}",
        "title": title,
        "source": "mission",
        "maps": maps,
    }
