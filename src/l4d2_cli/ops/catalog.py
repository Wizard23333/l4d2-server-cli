"""Catalog parsing migrated from L4D2 Server Manager (MIT, Wizard23333, 2026)."""

from __future__ import annotations

import re, json, time, urllib.request, urllib.parse, urllib.error

from pathlib import Path

from ..common import CliError

from . import transport

STEAM_WEB_API_KEY = ""

PART_SUFFIX_RE = re.compile(r"[\s_\-]*[\(（]?\s*part\s*\d+\s*[\)）]?\s*$", re.I)
WORKSHOP_ID_RE = re.compile(r"^[0-9]{4,20}$")

CATALOG_FORBIDDEN_CHARS = set('/\\<>[]{}$;|`')

STEAM_DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

STEAM_COLLECTION_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"

STEAM_GETDETAILS_URL = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"

STEAM_QUERY_URL = "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/"

STEAM_BROWSE_URL = "https://steamcommunity.com/workshop/browse/"

STEAM_WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={id}"

GAMEMAPS_DETAILS_URL = "https://www.gamemaps.com/details/{id}"

KNOWN_CATALOG_ITEMS = [
    {
        "source": "workshop",
        "id": "2232584588",
        "title": "Run to the Hills (L4D2)",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="2232584588"),
        "size": "317.9 MB",
        "summary": "5-map campaign. Steam API currently returns a downloadable VPK.",
        "installable": True,
        "reason": "",
        "aliases": ["run to the hills", "runtothehills", "run hills"],
    },
    {
        "source": "gamemaps",
        "id": "2559",
        "title": "Run To The Hills",
        "kind": "map",
        "url": GAMEMAPS_DETAILS_URL.format(id="2559"),
        "size": "131.3 MB",
        "summary": "5-map GameMaps campaign package: runtothehills.vpk.",
        "installable": True,
        "reason": "",
        "aliases": ["run to the hills", "runtothehills", "run hills"],
    },
    {
        "source": "workshop",
        "id": "3583374422",
        "install_ids": ["3583374422", "3583375624", "3583381403", "3583382507"],
        "title": "广西灵山 V2.9 / Lingshan-Guangxi V2.9",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="3583374422"),
        "size": "4 packages",
        "summary": "Multi-part campaign package. Installs the main Workshop item plus Pack-2, Pack-3, and Pack-4.",
        "installable": True,
        "reason": "",
        "aliases": ["广西灵山", "灵山", "lingshan", "guangxi lingshan", "lingshan-guangxi"],
    },
    {
        "source": "gamemaps",
        "id": "34721",
        "title": "Lingshan-Guangxi / 广西灵山",
        "kind": "map",
        "url": GAMEMAPS_DETAILS_URL.format(id="34721"),
        "size": "",
        "summary": "GameMaps mirror. Steam Workshop is preferred for this multi-part campaign.",
        "installable": True,
        "reason": "",
        "aliases": ["广西灵山", "灵山", "lingshan", "guangxi lingshan", "lingshan-guangxi"],
    },
    {
        "source": "workshop",
        "id": "767999000",
        "install_ids": ["767999000", "170360252", "169801737", "1127584577"],
        "title": "Yama",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="767999000"),
        "size": "4 packages",
        "summary": "Multi-part campaign package: Yama part 1, part 2, part 3, and Yama Finale fix.",
        "installable": True,
        "reason": "",
        "aliases": ["yama", "l4d_yama", "yama japan"],
    },
    {
        "source": "workshop",
        "id": "3743921409",
        "install_ids": ["3743921409", "3743875149", "3743877283"],
        "title": "江门新会 0.51 / Jiangmen Xinhuai 0.51",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="3743921409"),
        "size": "3 packages",
        "summary": "Multi-package campaign. Installs the main package plus resource packs B and C.",
        "installable": True,
        "reason": "",
        "aliases": ["江门新会", "jiangmen", "xinhuai", "jiangmen xinhuai", "jiangmen-xinhuai"],
    },
    {
        "source": "workshop",
        "id": "1713617501",
        "install_ids": ["1713617501", "1713621105"],
        "title": "Left 4 SGC Beta v2.2",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="1713617501"),
        "size": "2 packages",
        "summary": "Multi-package campaign. Part 1 of 2 holds materials/models; part 2 of 2 holds the maps. Installs both.",
        "installable": True,
        "reason": "",
        "aliases": ["left 4 sgc", "sgc", "l4sgc", "left4sgc"],
    },
    {
        "source": "workshop",
        "id": "2396847377",
        "title": "广州增城 （Zengcheng）Lv7.3",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="2396847377"),
        "size": "",
        "summary": "5-map Guangzhou Zengcheng campaign.",
        "installable": True,
        "reason": "",
        "aliases": ["广州增城", "增城", "zengcheng", "guangzhou zengcheng", "gzzc"],
    },
    {
        "source": "workshop",
        "id": "3526529688",
        "title": "地心引力 / The Gravitation",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="3526529688"),
        "size": "411.3 MB",
        "summary": "Campaign package. The author notes that servers only need to upload part 1.",
        "installable": True,
        "reason": "",
        "aliases": ["地心引力", "the gravitation", "gravitation", "dxyl"],
    },
    {
        "source": "workshop",
        "id": "2459037122",
        "title": "Glubtastic 4",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="2459037122"),
        "size": "",
        "summary": "Installed map package candidate.",
        "installable": True,
        "reason": "",
        "aliases": ["glubtastic", "glubtastic 4"],
    },
    {
        "source": "workshop",
        "id": "3366491323",
        "title": "Glubtastic 5",
        "kind": "map",
        "url": STEAM_WORKSHOP_URL.format(id="3366491323"),
        "size": "",
        "summary": "Installed map package candidate.",
        "installable": True,
        "reason": "",
        "aliases": ["glubtastic", "glubtastic 5"],
    },
]


def fetch_collection_children(collection_id):
    try:
        payload = http_json(
            STEAM_COLLECTION_URL,
            {"collectioncount": "1", "publishedfileids[0]": collection_id},
        )
        details = payload.get("response", {}).get("collectiondetails", [{}])[0]
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError):
        return []
    if str(details.get("result")) != "1":
        return []
    children = details.get("children") or []
    ordered = sorted(children, key=lambda child: int(child.get("sortorder") or 0))
    ids = []
    for child in ordered:
        child_id = str(child.get("publishedfileid") or "")
        if WORKSHOP_ID_RE.match(child_id):
            ids.append(child_id)
    return ids

def fetch_required_children(workshop_id):
    """Resolve multi-part workshop maps (e.g. ContraZ part1/part2).

    A single installable workshop item can declare sibling parts through the
    ``children`` field exposed by IPublishedFileService/GetDetails (this needs a
    Steam Web API key; the anonymous GetPublishedFileDetails endpoint does not
    return it). Returns the full ordered list of part ids including the queried
    item itself, or [] when there are no extra parts / no key available.
    """
    if not STEAM_WEB_API_KEY:
        return []
    if not WORKSHOP_ID_RE.match(str(workshop_id)):
        return []
    params = {
        "key": STEAM_WEB_API_KEY,
        "publishedfileids[0]": str(workshop_id),
        "includechildren": "true",
        "return_children": "true",
    }
    url = STEAM_GETDETAILS_URL + "?" + urllib.parse.urlencode(params)
    try:
        payload = http_json(url)
        details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError):
        return []
    children = details.get("children") or []
    if not children:
        return []
    ordered = sorted(children, key=lambda child: int(child.get("sortorder") or 0))
    ids = [str(workshop_id)]
    for child in ordered:
        child_id = str(child.get("publishedfileid") or "")
        if WORKSHOP_ID_RE.match(child_id) and child_id not in ids:
            ids.append(child_id)
    return ids if len(ids) > 1 else []

def strip_part_suffix(title):
    """Remove a trailing part marker (e.g. ``(part1)`` / ``- part 2``) to get the base map name."""
    if not title:
        return title
    stripped = PART_SUFFIX_RE.sub("", title).strip()
    return stripped or title

def fetch_workshop_meta(workshop_id):
    """Return ``{"title": str, "children": [ids]}`` for a workshop item.

    Prefers the authenticated GetDetails endpoint (exposes sibling parts via
    ``children``); falls back to the anonymous GetPublishedFileDetails for the
    title only. Always returns a dict (empty fields on failure)."""
    meta = {"title": "", "children": []}
    if not WORKSHOP_ID_RE.match(str(workshop_id)):
        return meta
    if STEAM_WEB_API_KEY:
        params = {
            "key": STEAM_WEB_API_KEY,
            "publishedfileids[0]": str(workshop_id),
            "includechildren": "true",
            "return_children": "true",
        }
        url = STEAM_GETDETAILS_URL + "?" + urllib.parse.urlencode(params)
        try:
            payload = http_json(url)
            details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
            meta["title"] = details.get("title") or ""
            children = details.get("children") or []
            ordered = sorted(children, key=lambda child: int(child.get("sortorder") or 0))
            child_ids = []
            for child in ordered:
                child_id = str(child.get("publishedfileid") or "")
                if WORKSHOP_ID_RE.match(child_id) and child_id not in child_ids:
                    child_ids.append(child_id)
            meta["children"] = child_ids
        except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError):
            pass
    if not meta["title"]:
        try:
            payload = http_json(STEAM_DETAILS_URL, {"itemcount": "1", "publishedfileids[0]": str(workshop_id)})
            details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
            meta["title"] = details.get("title") or ""
        except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError):
            pass
    return meta

def fetch_description_sibling_ids(workshop_id, kind):
    """Discover sibling package ids referenced only in a workshop item's description.

    Some multi-part maps declare the package that actually contains the ``.bsp``
    solely in the human-readable description text (e.g. "Part 2 NEEDED TOO ...
    id=1713621105") rather than through collection/GetDetails ``children``. This
    parses those ``id=<digits>`` references from the anonymous
    GetPublishedFileDetails endpoint (no ``STEAM_WEB_API_KEY`` required), validates
    each candidate resolves to a real downloadable ``file_url``, and returns the
    ordered id list ``[workshop_id, *validated_siblings]``; returns [] when nothing
    valid is found so callers fall back to the previous single-package behaviour."""
    if not WORKSHOP_ID_RE.match(str(workshop_id)):
        return []
    try:
        payload = http_json(STEAM_DETAILS_URL, {"itemcount": "1", "publishedfileids[0]": str(workshop_id)})
        details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError):
        return []
    description = details.get("description") or ""
    matches = re.findall(r"(?:filedetails/\?id=|[?&]id=|\bid=)(\d{4,20})", description, re.I)
    candidates = []
    for candidate_id in matches:
        if candidate_id == str(workshop_id):
            continue
        if candidate_id not in candidates:
            candidates.append(candidate_id)
        if len(candidates) >= 8:
            break
    if not candidates:
        return []
    validated = []
    for candidate_id in candidates:
        if not WORKSHOP_ID_RE.match(candidate_id):
            continue
        result = resolve_single_workshop_download(candidate_id, kind)
        if result.get("ok"):
            validated.append(candidate_id)
    if not validated:
        return []
    return [str(workshop_id)] + validated

def format_bytes(value):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return ""

def catalog_item(source, item_id, title, kind, url, size="", summary="", installable=True, reason="", install_ids=None):
    return {
        "source": source,
        "id": str(item_id),
        "title": title or str(item_id),
        "kind": kind,
        "url": url,
        "size": size,
        "summary": summary,
        "installable": bool(installable),
        "reason": reason,
        "install_ids": [str(value) for value in (install_ids or [])],
    }

def known_catalog_results(query, kind):
    normalized = normalize_catalog_query(query)
    results = []
    for item in KNOWN_CATALOG_ITEMS:
        if item["kind"] != kind:
            continue
        haystack = normalize_catalog_query(" ".join([item["title"], *item.get("aliases", [])]))
        aliases = [normalize_catalog_query(alias) for alias in item.get("aliases", [])]
        ids = {str(item["id"]), *[str(value) for value in item.get("install_ids", [])]}
        if normalized in ids or normalized in haystack or any(alias and alias in normalized for alias in aliases):
            results.append(catalog_item(
                item["source"],
                item["id"],
                item["title"],
                item["kind"],
                item["url"],
                item["size"],
                item["summary"],
                item["installable"],
                item["reason"],
                item.get("install_ids"),
            ))
    return results

def known_catalog_by_id(source, item_id, kind):
    for item in KNOWN_CATALOG_ITEMS:
        ids = {str(item["id"]), *[str(value) for value in item.get("install_ids", [])]}
        if item["kind"] == kind and item["source"] == source and str(item_id) in ids:
            return item
    return None

def enrich_catalog_result(item):
    known = known_catalog_by_id(item["source"], item["id"], item["kind"])
    if not known:
        return item
    enriched = dict(item)
    enriched["title"] = enriched.get("title") or known["title"]
    enriched["url"] = enriched.get("url") or known["url"]
    enriched["summary"] = known.get("summary") or enriched.get("summary", "")
    enriched["size"] = enriched.get("size") or known.get("size", "")
    enriched["install_ids"] = [str(value) for value in known.get("install_ids", [])]
    if known.get("reason"):
        enriched["reason"] = known["reason"]
    return enriched

def normalize_catalog_query(value):
    return re.sub(r"\s+", " ", value.casefold()).strip()

def catalog_query_error(query):
    if len(query) < 3:
        return "Search query must be at least 3 characters"
    if len(query) > 80:
        return "Search query must be 80 characters or fewer"
    if query[0].isspace():
        return "Search query must not start with whitespace"
    for char in query:
        if char in CATALOG_FORBIDDEN_CHARS or ord(char) < 32 or ord(char) == 127:
            return "Search query contains unsupported characters"
    return ""

def workshop_detail_result(workshop_id, kind):
    try:
        payload = http_json(STEAM_DETAILS_URL, {"itemcount": "1", "publishedfileids[0]": workshop_id})
        details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return catalog_item(
            "workshop",
            workshop_id,
            f"Workshop {workshop_id}",
            kind,
            STEAM_WORKSHOP_URL.format(id=workshop_id),
            "",
            "",
            False,
            f"Steam lookup failed: {exc}",
        )
    title = details.get("title") or f"Workshop {workshop_id}"
    installable = str(details.get("result")) == "1" and bool(details.get("file_url"))
    if not installable:
        children = fetch_collection_children(workshop_id)
        if children:
            return catalog_item(
                "workshop",
                workshop_id,
                title,
                kind,
                STEAM_WORKSHOP_URL.format(id=workshop_id),
                f"{len(children)} packages",
                f"Workshop collection: installs {len(children)} child items.",
                True,
                "",
                children,
            )
    reason = "" if installable else f"Steam API returned result {details.get('result', 'unknown')} without file_url"
    return catalog_item(
        "workshop",
        workshop_id,
        title,
        kind,
        STEAM_WORKSHOP_URL.format(id=workshop_id),
        format_bytes(details.get("file_size")),
        (details.get("description") or "").replace("\r", " ").replace("\n", " ")[:220],
        installable,
        reason,
    )

def proxy_suggested_filename(kind, workshop_id, title):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (title or "").strip()).strip("._-")
    safe = (safe or "workshop_item")[:80]
    prefix = "map" if kind == "map" else "mod"
    return f"{prefix}_{workshop_id}_{safe}.vpk"

def resolve_single_workshop_download(workshop_id, kind):
    """Resolve one workshop item to a direct CDN url + suggested filename.

    Returns ``{"ok": True, ...}`` on success or ``{"ok": False, "message"}``.
    """
    try:
        payload = http_json(STEAM_DETAILS_URL, {"itemcount": "1", "publishedfileids[0]": workshop_id})
        details = payload.get("response", {}).get("publishedfiledetails", [{}])[0]
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError) as exc:
        return {"ok": False, "workshop_id": workshop_id, "message": f"Steam lookup failed: {exc}"}
    title = details.get("title") or f"Workshop {workshop_id}"
    if str(details.get("consumer_app_id", "550")) != "550":
        return {"ok": False, "message": "Workshop item belongs to another game"}
    file_url = details.get("file_url")
    if str(details.get("result")) != "1" or not file_url or not file_url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "workshop_id": workshop_id,
            "title": title,
            "message": "Steam API did not return a downloadable file_url (may be a collection or restricted item)",
        }
    try:
        file_size = int(details.get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    return {
        "ok": True,
        "workshop_id": workshop_id,
        "kind": kind,
        "title": title,
        "file_url": file_url,
        "file_size": file_size,
        "suggested_filename": proxy_suggested_filename(kind, workshop_id, title),
    }

def resolve_workshop_download(workshop_id, kind):
    """Resolve a workshop id (single item or collection) to direct download(s)
    for browser-side ("proxy") downloading. Returns either a single resolved
    item or, for collections, a list of resolved children under ``items``.
    """
    kind = "mod" if kind == "mod" else "map"
    if not WORKSHOP_ID_RE.match(str(workshop_id)):
        return {"ok": False, "message": "Invalid workshop id"}
    single = resolve_single_workshop_download(workshop_id, kind)
    if single.get("ok"):
        return single
    children = fetch_collection_children(workshop_id)
    if children:
        items = []
        for child_id in children:
            items.append(resolve_single_workshop_download(child_id, kind))
        return {
            "ok": True,
            "workshop_id": workshop_id,
            "kind": kind,
            "collection": True,
            "items": items,
        }
    return single

def workshop_expected_parts(workshop_id, kind):
    """Resolve the full set of downloadable parts that belong to a workshop id.

    Expands collections and multi-part maps the same way the install pipeline
    does (collection children, then sibling parts via GetDetails), then resolves
    each part's direct url + ``file_size`` + suggested filename. Returns
    ``{"ok": True, "workshop_id", "kind", "collection", "title", "parts": [...]}``
    where each part mirrors :func:`resolve_single_workshop_download`.
    """
    kind = "mod" if kind == "mod" else "map"
    workshop_id = str(workshop_id)
    if not WORKSHOP_ID_RE.match(workshop_id):
        return {"ok": False, "message": "Invalid workshop id"}
    ids = known_install_ids("workshop", kind, workshop_id)
    if not ids:
        ids = fetch_collection_children(workshop_id)
    collection = bool(ids)
    title = ""
    if not ids:
        # A single fetch_workshop_meta call returns both the title and any
        # sibling parts (children); reuse it instead of issuing a second,
        # identical GetDetails request via fetch_required_children. Each extra
        # serial round-trip to Steam is a separate chance to stall on this host.
        meta = fetch_workshop_meta(workshop_id)
        title = meta.get("title", "")
        children = meta.get("children") or []
        if children:
            ids = [workshop_id] + [cid for cid in children if cid != workshop_id]
    if not ids:
        siblings = fetch_description_sibling_ids(workshop_id, kind)
        if len(siblings) > 1:
            ids = siblings
            collection = True
    if not ids:
        ids = [workshop_id]
    parts = [resolve_single_workshop_download(cid, kind) for cid in ids]
    if not title:
        title = fetch_workshop_meta(workshop_id).get("title", "")
    if not title:
        for part in parts:
            if part.get("title"):
                title = part["title"]
                break
    if not any(part.get("ok") for part in parts):
        message = next((part.get("message") for part in parts if part.get("message")), "No downloadable items found for this id")
        return {"ok": False, "workshop_id": workshop_id, "kind": kind, "message": message}
    return {
        "ok": True,
        "workshop_id": workshop_id,
        "kind": kind,
        "collection": collection,
        "title": title,
        "parts": parts,
    }

def workshop_search_results(query, kind):
    results = []
    if WORKSHOP_ID_RE.match(query):
        results.append(workshop_detail_result(query, kind))
        return results
    # QueryFiles usually requires a Steam Web API key. Keep this best-effort,
    # and rely on the curated fallback for known campaigns when it is unavailable.
    params = {
        "query_type": "12",
        "cursor": "*",
        "numperpage": "5",
        "creator_appid": "550",
        "appid": "550",
        "search_text": query,
        "filetype": "0",
        "return_tags": "1",
        "return_short_description": "1",
    }
    if STEAM_WEB_API_KEY:
        params["key"] = STEAM_WEB_API_KEY
    try:
        url = f"{STEAM_QUERY_URL}?{urllib.parse.urlencode(params)}"
        payload = http_json(url, timeout=5)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        pass
    else:
        for details in payload.get("response", {}).get("publishedfiledetails", [])[:5]:
            item_id = details.get("publishedfileid")
            if not item_id:
                continue
            results.append(catalog_item(
                "workshop",
                item_id,
                details.get("title") or f"Workshop {item_id}",
                kind,
                STEAM_WORKSHOP_URL.format(id=item_id),
                format_bytes(details.get("file_size")),
                details.get("short_description") or "",
                True,
                "",
            ))
    results.extend(workshop_public_search_results(query, kind))
    return results

def workshop_public_search_results(query, kind):
    params = {
        "appid": "550",
        "searchtext": query,
        "browsesort": "textsearch",
        "section": "readytouseitems",
        "actualsort": "textsearch",
        "p": "1",
    }
    try:
        url = f"{STEAM_BROWSE_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html_text = transport.fetch_bytes(url, timeout=5, limit=768*1024).decode("utf-8", errors="replace")
    except (CliError, OSError, urllib.error.URLError):
        return []
    item_ids = []
    for match in re.finditer(r'(?:publishedfileid["\']?\s*[:=]\s*["\']?|[?&]id=)(\d{4,20})', html_text):
        item_id = match.group(1)
        if item_id not in item_ids:
            item_ids.append(item_id)
        if len(item_ids) >= 8:
            break
    results = []
    for item_id in item_ids:
        results.append(workshop_detail_result(item_id, kind))
    return results

def gamemaps_search_results(query, kind):
    if kind != "map":
        return []
    results = []
    if WORKSHOP_ID_RE.match(query):
        if len(query) > 8:
            return results
        results.append(catalog_item(
            "gamemaps",
            query,
            f"GameMaps {query}",
            "map",
            GAMEMAPS_DETAILS_URL.format(id=query),
            "",
            "GameMaps numeric details id.",
            True,
            "",
        ))
        return results
    results.extend([item for item in known_catalog_results(query, "map") if item["source"] == "gamemaps"])
    # Best-effort HTML search. GameMaps may block automated requests, so this
    # must never be the only way a known result can appear.
    try:
        search_url = "https://www.gamemaps.com/search?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        html_text = transport.fetch_bytes(search_url, timeout=5, limit=256*1024).decode("utf-8", errors="replace")
    except (CliError, OSError, urllib.error.URLError):
        return results
    for match in re.finditer(r'href=["\'](?:https://www\.gamemaps\.com)?/details/(\d+)["\'][^>]*>([^<]+)', html_text):
        item_id, raw_title = match.groups()
        title = re.sub(r"\s+", " ", raw_title).strip()
        if not title:
            continue
        item = catalog_item("gamemaps", item_id, title, "map", GAMEMAPS_DETAILS_URL.format(id=item_id), "", "", True, "")
        if all(existing["source"] != "gamemaps" or existing["id"] != item_id for existing in results):
            results.append(item)
        if len(results) >= 6:
            break
    return results

def search_catalog(query, kind):
    query = query.strip()
    if kind not in {"map", "mod"}:
        return {"ok": False, "message": "Kind must be map or mod"}
    error = catalog_query_error(query)
    if error and not WORKSHOP_ID_RE.match(query):
        return {"ok": False, "message": error}
    results = []
    known_results = known_catalog_results(query, kind)
    if known_results and not WORKSHOP_ID_RE.match(query) and not STEAM_WEB_API_KEY:
        return {"ok": True, "query": query, "kind": kind, "results": known_results[:10]}
    # Keep curated exact/alias matches ahead of best-effort online results so
    # a full page of Workshop hits cannot hide a known GameMaps alternative.
    results.extend(known_results)
    results.extend(workshop_search_results(query, kind))
    results.extend(gamemaps_search_results(query, kind))
    deduped = []
    seen = set()
    for item in results:
        item = enrich_catalog_result(item)
        key = (item["source"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return {"ok": True, "query": query, "kind": kind, "results": deduped[:10]}

def known_install_ids(source, kind, item_id):
    for item in KNOWN_CATALOG_ITEMS:
        if item["source"] == source and item["kind"] == kind and item["id"] == item_id:
            return [str(value) for value in item.get("install_ids", [])]
    return []


def initialize():
    global STEAM_WEB_API_KEY
    STEAM_WEB_API_KEY = transport.settings().get('steam_web_api_key', '')


# Optional VPK mods documented by the original skill; never installed by default.
for _item_id, _title, _ids in (
    ('2279814689', 'Left 4 Bots 2', ['2634208272', '2279814689']),
    ('2634208272', 'Left 4 Lib', ['2634208272']),
    ('213591107', 'Admin System', ['213591107']),
):
    KNOWN_CATALOG_ITEMS.append({'source': 'workshop', 'id': _item_id, 'kind': 'mod',
                               'title': _title, 'url': STEAM_WORKSHOP_URL.format(id=_item_id),
                               'install_ids': _ids, 'aliases': [_title.replace(' ', '')],
                               'size': '', 'summary': 'Optional VPK mod; required known packages are included.',
                               'installable': True, 'reason': ''})

def http_json(url, data=None, timeout=12, attempts=4):
    payload = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        return json.loads(transport.fetch_bytes(url, payload, timeout=timeout, limit=8*1024*1024))
    except (CliError, ValueError):
        return {}
