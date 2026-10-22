"""Bounded downloads, manual safe extraction and versioned vanilla bundles."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
import shutil
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import CliError, deadline_remaining, progress
from .model import APP_ID, CFG_NAME, OFFICIAL_MAPS, STEAMCMD_URLS
from .storage import reject_links, sha256

BUNDLE_FORMAT = "l4d2-server-bundle"
MAX_ARCHIVE_BYTES = 50 * 1024**3
MAX_FILES = 150000
STEAM_HOSTS = {"steamcdn-a.akamaihd.net", "media.steampowered.com", "cdn.cloudflare.steamstatic.com"}


@contextmanager
def open_archive(path: Path, deadline: float | None = None):
    # Check while gzip scans/skips payloads too, not only between tar members.
    with path.open("rb") as handle:
        class TimedInput:
            def read(self, size=-1):
                if deadline:
                    deadline_remaining(deadline)
                return handle.read(size)

            def __getattr__(self, name):
                return getattr(handle, name)
        with tarfile.open(fileobj=TimedInput(), mode="r:gz") as archive:
            yield archive


class SteamRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in STEAM_HOSTS or parsed.username or parsed.password:
            raise CliError("Refusing unexpected download redirect.", "unsafe_download")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, output: Path, timeout: float, max_bytes: int = 512 * 1024**2) -> dict:
    reject_links(output)
    if output.exists():
        raise CliError(f"Output exists: {output}", "output_exists", 4)
    if urlparse(url).hostname not in STEAM_HOSTS:
        raise CliError("Download source is not an approved official endpoint.", "unsafe_download", 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    reject_links(partial)
    deadline = time.monotonic() + timeout
    opener = build_opener(SteamRedirect())
    last_error = ""
    # Only the exact SteamCMD bootstrap URLs participate in CDN fallback.
    candidates = [url] + [item for item in STEAMCMD_URLS if item != url] if url in STEAMCMD_URLS else [url]
    for attempt in range(1, 4):
        selected = candidates[(attempt - 1) % len(candidates)]
        try:
            deadline_remaining(deadline)
            request = Request(selected, headers={"User-Agent": "l4d2-server-cli/0.1"})
            with opener.open(request, timeout=min(10, deadline_remaining(deadline))) as response:
                final_source = response.geturl()
                length = response.headers.get("Content-Length")
                expected = int(length) if length and length.isdigit() else None
                if expected is not None and expected > max_bytes:
                    raise CliError("Download exceeds the allowed size.", "download_too_large")
                descriptor = os.open(partial, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                written = 0
                with os.fdopen(descriptor, "wb") as handle:
                    while True:
                        deadline_remaining(deadline)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        written += len(block)
                        if written > max_bytes:
                            raise CliError("Download exceeds the allowed size.", "download_too_large")
                        handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not written or (expected is not None and written != expected):
                    raise OSError("Download length does not match response")
            with partial.open("rb") as handle:
                if handle.read(2) != b"\x1f\x8b":
                    raise CliError("Endpoint returned non-gzip data, possibly an HTML verification page.", "invalid_download")
            # link is an atomic no-overwrite commit, unlike replace.
            digest = file_hash(partial, deadline)
            os.link(partial, output)
            partial.unlink()
            return {"path": str(output), "bytes": written, "sha256": digest, "source": final_source}
        except (CliError, OSError, ValueError) as exc:
            if isinstance(exc, CliError) and exc.code != "invalid_download":
                raise
            last_error = str(exc)
            progress(f"SteamCMD archive attempt {attempt}/3 ({selected}) failed: {last_error}")
            if attempt < 3:
                time.sleep(min(attempt, deadline_remaining(deadline)))
    raise CliError(f"Archive download failed: {last_error}. Supply --steamcmd-archive FILE instead.", "archive_download_failed")


def normalized_name(name: str) -> str:
    if "\\" in name or ":" in name or "\x00" in name:
        raise CliError("Unsafe archive member name.", "unsafe_archive", 2)
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise CliError("Archive member escapes its root.", "unsafe_archive", 2)
    return str(path)


def _safe_link(name: str, target: str, known: set[str]) -> str:
    if "\\" in target or ":" in target or PurePosixPath(target).is_absolute():
        raise CliError("Absolute/foreign archive symlink rejected.", "unsafe_archive", 2)
    parts = list(PurePosixPath(name).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                raise CliError("Archive symlink escapes root.", "unsafe_archive", 2)
            parts.pop()
        elif part != ".":
            parts.append(part)
    resolved = "/".join(parts)
    if resolved not in known:
        raise CliError("Dangling archive symlink rejected.", "unsafe_archive", 2)
    return resolved


def inventory(handle: tarfile.TarFile, deadline: float | None = None) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    total = 0
    for member in handle:
        if deadline:
            deadline_remaining(deadline)
        name = normalized_name(member.name)
        if name == "." and member.isdir():
            continue
        if len(members) >= MAX_FILES or name in members:
            raise CliError("Too many or duplicate archive members.", "unsafe_archive", 2)
        if not (member.isfile() or member.isdir() or member.issym()):
            raise CliError("Archive special files/hardlinks are not supported.", "unsafe_archive", 2)
        if member.size < 0:
            raise CliError("Invalid archive file size.", "unsafe_archive", 2)
        total += member.size
        if total > MAX_ARCHIVE_BYTES:
            raise CliError("Archive expansion exceeds quota.", "archive_too_large", 2)
        members[name] = member
    links = {name for name, member in members.items() if member.issym()}
    for name, member in members.items():
        for parent in PurePosixPath(name).parents:
            parent_member = members.get(str(parent))
            if parent_member and not parent_member.isdir():
                raise CliError("Archive file has a non-directory parent.", "unsafe_archive", 2)
        if member.issym():
            resolved = _safe_link(name, member.linkname, set(members))
            if resolved in links:
                raise CliError("Symlink chains are unsupported.", "unsafe_archive", 2)
    return members


def checksum_input(path: Path, expected: str | None, deadline: float | None = None) -> str:
    digest = file_hash(path, deadline)
    if expected and digest.lower() != expected.lower():
        raise CliError("Input SHA256 mismatch; installation refused.", "checksum_mismatch", 2)
    return digest


def file_hash(path: Path, deadline: float | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            if deadline:
                deadline_remaining(deadline)
            digest.update(block)
    return digest.hexdigest()


def private_payload(relative: str) -> bool:
    path = PurePosixPath(relative.lower())
    return (CFG_NAME in str(path) or any(part in {"logs", "userdata", "config", "addons", "ems", ".depotdownloader"} for part in path.parts)
            or path.name.endswith((".log", ".key", ".pem")) or path.name.startswith("ssfn")
            or path.name in {"server.cfg", "secrets.toml", "account.config", ".l4d2-qr-auth.json", ".qr-content.json"})


def validate_game_tree(root: Path) -> dict:
    script, binary = root / "srcds_run", root / "srcds_linux"
    for path in (script, binary):
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise CliError(f"Missing Linux server runtime: {path.name}", "incomplete_game")
    with binary.open("rb") as handle:
        header = handle.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] != 1 or int.from_bytes(header[18:20], "little") not in {3, 62}:
        raise CliError("Server executable is not a Linux x86 ELF binary.", "wrong_game_platform", 2)
    maps = installed_maps(root)
    if "c1m1_hotel" not in maps:
        raise CliError("Default official map c1m1_hotel is missing.", "incomplete_game")
    if not any((root / "left4dead2").glob("pak*_dir.vpk")) or not (root / "bin").is_dir():
        raise CliError("Official game resources or bin runtime directory missing.", "incomplete_game")
    return {"maps": maps, "game_build": game_build(root)}


def installed_maps(root: Path, *, custom: bool = False) -> list[str]:
    """Official DLC campaigns can live outside the base game's maps directory."""
    folders = [root / "left4dead2", root / "update", *root.glob("left4dead2_dlc[0-9]*")]
    maps = {path.stem for folder in folders for path in (folder / "maps").glob("*.bsp")
            if path.is_file() and not path.is_symlink() and (custom or path.stem in OFFICIAL_MAPS)}
    if not custom:
        return sorted(maps)
    from .ops import vpk
    for path in (root / "left4dead2/addons").glob("*.vpk"):
        if vpk.is_archive_part(path):
            continue
        for item in vpk.entries(path):
            if item['name'].lower().startswith('maps/') and item['name'].lower().endswith('.bsp'):
                maps.add(Path(item['name']).stem)
    return sorted(maps)


def validate_bootstrap(path: Path, expected: str | None = None, deadline: float | None = None) -> dict:
    digest = checksum_input(path, expected, deadline)
    try:
        with open_archive(path, deadline) as archive:
            members = inventory(archive, deadline)
            if sum(item.size for item in members.values()) > 512 * 1024**2:
                raise CliError("SteamCMD expansion exceeds its quota.", "archive_too_large", 2)
            for name in ("steamcmd.sh", "linux32/steamcmd"):
                entry = members.get(name)
                if not entry or not entry.isfile() or not entry.size:
                    raise CliError("Not a complete official Linux SteamCMD bootstrap archive.", "invalid_steamcmd", 2)
            with archive.extractfile(members["linux32/steamcmd"]) as binary:
                if binary.read(4) != b"\x7fELF":
                    raise CliError("SteamCMD bootstrap is not a Linux executable.", "invalid_steamcmd", 2)
    except (tarfile.TarError, OSError) as exc:
        raise CliError(f"Cannot inspect SteamCMD archive: {exc}", "invalid_steamcmd", 2) from exc
    return {"sha256": digest}


def game_build(root: Path) -> str | None:
    manifest = root / "steamapps" / f"appmanifest_{APP_ID}.acf"
    if manifest.is_file():
        import re
        match = re.search(r'"buildid"\s+"([0-9]+)"', manifest.read_text(errors="replace"))
        if match:
            return match[1]
    return None


def validate_bundle(path: Path, expected: str | None = None, deadline: float | None = None) -> dict:
    digest = checksum_input(path, expected, deadline)
    try:
        with open_archive(path, deadline) as handle:
            members = inventory(handle, deadline)
            entry = members.get("manifest.json")
            if not entry or not entry.isfile() or entry.size > 16 * 1024**2:
                raise CliError("Missing/oversized bundle manifest.", "invalid_bundle", 2)
            manifest = json.load(handle.extractfile(entry))
            if (manifest.get("format") != BUNDLE_FORMAT or manifest.get("schema_version") != 1 or
                    manifest.get("app_id") != APP_ID or manifest.get("os") != "linux" or
                    manifest.get("host_arch") != "x86_64"):
                raise CliError("Bundle platform/app/format is unsupported.", "invalid_bundle", 2)
            records = manifest.get("files")
            if not isinstance(records, list):
                raise CliError("Bundle file manifest is invalid.", "invalid_bundle", 2)
            recorded = set()
            total_bytes = 0
            for record in records:
                if deadline:
                    deadline_remaining(deadline)
                if private_payload(record["path"]):
                    raise CliError("Bundle includes private or third-party resource paths.", "unsafe_bundle", 2)
                name = "serverfiles/" + normalized_name(record["path"])
                if name in recorded:
                    raise CliError("Duplicate manifest path.", "invalid_bundle", 2)
                recorded.add(name)
                member = members.get(name)
                if not member:
                    raise CliError(f"Bundle is missing {name}", "incomplete_bundle", 2)
                if record.get("type") == "symlink":
                    if not member.issym() or member.linkname != record.get("target"):
                        raise CliError("Bundle symlink mismatch.", "invalid_bundle", 2)
                    if not _safe_link(name, member.linkname, set(members)).startswith("serverfiles/"):
                        raise CliError("Bundle symlink escapes content root.", "unsafe_bundle", 2)
                elif record.get("type") == "file" and member.isfile():
                    if member.size != record.get("size") or (member.mode & 0o777) != record.get("mode"):
                        raise CliError("Bundle file size/mode mismatch.", "invalid_bundle", 2)
                    total_bytes += member.size
                    file_digest = hashlib.sha256()
                    with handle.extractfile(member) as input_file:
                        while block := input_file.read(1024 * 1024):
                            if deadline:
                                deadline_remaining(deadline)
                            file_digest.update(block)
                    if file_digest.hexdigest() != record.get("sha256"):
                        raise CliError(f"Bundle file checksum mismatch: {name}", "checksum_mismatch", 2)
                else:
                    raise CliError("Bundle entry type mismatch.", "invalid_bundle", 2)
            actual = {name for name, member in members.items() if not member.isdir() and name != "manifest.json"}
            if actual != recorded or any(not name.startswith("serverfiles/") for name in actual):
                raise CliError("Bundle contains unlisted or foreign files.", "invalid_bundle", 2)
            required = {"serverfiles/srcds_run", "serverfiles/srcds_linux", "serverfiles/left4dead2/maps/c1m1_hotel.bsp"}
            if not required.issubset(recorded):
                raise CliError("Bundle is missing required Linux server files.", "incomplete_bundle", 2)
            return {"sha256": digest, "bytes": total_bytes, "manifest": manifest}
    except (tarfile.TarError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CliError(f"Invalid server archive: {exc}", "invalid_bundle", 2) from exc


def extract(path: Path, destination: Path, *, prefix: str = "", max_bytes: int = MAX_ARCHIVE_BYTES,
            deadline: float | None = None) -> None:
    reject_links(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with open_archive(path, deadline) as handle:
            members = inventory(handle, deadline)
            selected = [(name, member) for name, member in members.items() if name.startswith(prefix) and name != prefix.rstrip("/")]
            total = sum(member.size for _, member in selected)
            if total > max_bytes or shutil.disk_usage(destination).free < total + 1024**3:
                raise CliError("Not enough space or extraction quota exceeded.", "insufficient_space")
            for name, member in selected:
                if deadline:
                    deadline_remaining(deadline)
                if member.issym() and not _safe_link(name, member.linkname, set(members)).startswith(prefix):
                    raise CliError("Symlink escapes selected archive subtree.", "unsafe_archive", 2)
                relative = name[len(prefix):]
                target = destination / relative
                reject_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                elif member.isfile():
                    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as output_file, handle.extractfile(member) as input_file:
                        while block := input_file.read(1024 * 1024):
                            if deadline:
                                deadline_remaining(deadline)
                            output_file.write(block)
                        output_file.flush()
                        os.fsync(output_file.fileno())
                    if target.stat().st_size != member.size:
                        raise CliError("Truncated archive payload.", "invalid_archive", 2)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
            # Only create checked internal links after ordinary files exist.
            for name, member in selected:
                if member.issym():
                    target = destination / name[len(prefix):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(member.linkname)
    except (tarfile.TarError, OSError) as exc:
        raise CliError(f"Archive extraction failed: {exc}", "archive_extract_failed") from exc


def create_bundle(root: Path, output: Path, deadline: float | None = None, *,
                  source_label: str = "SteamCMD official public dedicated content") -> dict:
    metadata = validate_game_tree(root)
    reject_links(output)
    if output.exists():
        raise CliError("Output already exists.", "output_exists", 4)
    records = []
    paths = []
    for path in sorted(root.rglob("*")):
        if deadline:
            deadline_remaining(deadline)
        relative = path.relative_to(root).as_posix()
        if not path.is_symlink() and path.is_dir():
            continue
        if private_payload(relative):
            continue
        if path.is_symlink():
            records.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            records.append({"path": relative, "type": "file", "size": path.stat().st_size,
                            "mode": path.stat().st_mode & 0o777, "sha256": file_hash(path, deadline)})
        else:
            raise CliError("Special file in prepared content.", "unsafe_bundle")
        paths.append(path)
    manifest = {"format": BUNDLE_FORMAT, "schema_version": 1, "app_id": APP_ID,
                "os": "linux", "host_arch": "x86_64", "created_at": datetime.now(timezone.utc).isoformat(),
                "game_build": metadata["game_build"], "source": source_label,
                "files": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    reject_links(partial)
    if partial.exists():
        raise CliError("Partial bundle exists; preserve it or choose a different output name.", "output_exists", 4)
    import io
    try:
        with tarfile.open(partial, "x:gz") as archive:
            data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
            for path in paths:
                if deadline:
                    deadline_remaining(deadline)
                info = archive.gettarinfo(str(path), "serverfiles/" + path.relative_to(root).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.pax_headers = {}
                if info.isfile():
                    with path.open("rb") as handle:
                        class TimedReader:
                            def read(self, size=-1):
                                if deadline:
                                    deadline_remaining(deadline)
                                return handle.read(size)
                        archive.addfile(info, TimedReader())
                else:
                    archive.addfile(info)
        validated = validate_bundle(partial, deadline=deadline)
        os.link(partial, output)
        partial.unlink()
    except BaseException:
        # Retain an explicitly named .part file for diagnosis, never publish it.
        raise
    return {"path": str(output), "sha256": validated["sha256"], "game_build": metadata["game_build"],
            "file_count": len(records), "source": "fresh_official_download"}
