"""Fixed single-instance model and pure validation/rendering."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .common import CliError

APP_ID = 222860
SERVICE = "l4d2-main.service"
USER = "l4d2"
CFG_NAME = "l4d2-cli.cfg"
STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
MIN_FREE = 20 * 1024**3  # Conservative provisional budget; measure in M0.

# Explicit vanilla catalog; no prefix matching of arbitrary custom map names.
CAMPAIGNS = {
    1: ("hotel", "streets", "mall", "atrium"),
    2: ("highway", "fairgrounds", "coaster", "barns", "concert"),
    3: ("plankcountry", "swamp", "shantytown", "plantation"),
    4: ("milltown_a", "sugarmill_a", "sugarmill_b", "milltown_b", "milltown_escape"),
    5: ("waterfront", "park", "cemetery", "quarter", "bridge"),
    6: ("riverbank", "bedlam", "port"),
    7: ("docks", "barge"),
    8: ("apartment", "subway", "sewers", "interior", "rooftop"),
    9: ("alleys", "lots"),
    10: ("caves", "drainage", "ranchhouse", "mainstreet", "houseboat"),
    11: ("greenhouse", "offices", "garage", "terminal", "runway"),
    12: ("hilltop", "traintunnel", "bridge", "barn", "cornfield"),
    13: ("alpinecreek", "southpinestream", "memorialbridge", "cutthroatcreek"),
    14: ("junkyard", "lighthouse"),
}
OFFICIAL_MAPS = {f"c{campaign}m{chapter}_{name}"
                 for campaign, names in CAMPAIGNS.items()
                 for chapter, name in enumerate(names, 1)}


@dataclass
class Config:
    schema_version: int = 1
    game_dir: str = "/opt/l4d2"
    name: str = "My L4D2 Server"
    bind: str = "0.0.0.0"
    port: int = 27015
    map: str = "c1m1_hotel"
    mode: str = "coop"
    difficulty: str = "normal"
    autostart: bool = False
    revision: int = 1
    visibility: str = "public"
    steam_group_id: str = ""

    def validate(self) -> None:
        if any(not isinstance(getattr(self, key), str) for key in
               ("game_dir", "name", "bind", "map", "mode", "difficulty", "visibility", "steam_group_id")):
            raise CliError("Invalid configuration field types.", "invalid_config", 2)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CliError("Unsupported config version.", "config_version", 2)
        if self.visibility not in {"public", "group"}:
            raise CliError("Visibility must be public or group.", "invalid_visibility", 2)
        if self.visibility == "group" and not self.steam_group_id:
            raise CliError("Steam group mode requires --steam-group-id with your group's short numeric ID.",
                           "steam_group_required", 2)
        if self.steam_group_id and (not re.fullmatch(r"[1-9][0-9]{0,9}", self.steam_group_id)
                                    or int(self.steam_group_id) > 4294967295):
            raise CliError("Use a short numeric Steam group ID (1..4294967295), not a groupID64, URL or list.",
                           "invalid_steam_group_id", 2)
        game_path(self.game_dir)
        if not self.name or len(self.name) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in self.name):
            raise CliError("Server name must contain 1–128 printable characters.", "invalid_name", 2)
        try:
            ipaddress.IPv4Address(self.bind)
        except ValueError:
            raise CliError("--bind must be an IPv4 address.", "invalid_bind", 2) from None
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            raise CliError("Port must be between 1024 and 65535.", "invalid_port", 2)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", self.map):
            raise CliError("Use a safe installed map name.", "invalid_map", 2)
        if self.mode not in {"coop", "versus"}:
            raise CliError("Mode must be coop or versus.", "invalid_mode", 2)
        if self.map.startswith("c14") and self.mode == "versus":
            raise CliError("The Last Stand is limited to coop in this version.", "map_mode_mismatch", 2)
        if self.difficulty not in {"easy", "normal", "hard", "impossible"}:
            raise CliError("Invalid difficulty.", "invalid_difficulty", 2)
        if type(self.autostart) is not bool or type(self.revision) is not int or self.revision < 1:
            raise CliError("Invalid config field types.", "invalid_config", 2)

    def toml(self) -> str:
        self.validate()
        return "# Managed by l4d2-server-cli\n" + "".join(
            f"{key} = {toml_value(value)}\n" for key, value in asdict(self).items())


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def game_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or any(c in value for c in ("\n", "\r", "\x00")) or ".." in path.parts:
        raise CliError("Game directory must be a safe absolute path.", "invalid_path", 2)
    if str(path) in {"/", "/opt", "/usr", "/etc", "/var", "/home", "/tmp", "/root"} or len(path.parts) < 3:
        raise CliError("Refusing a system root as the game directory.", "unsafe_path", 2)
    # Do not resolve and silently follow a link into a different target.
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise CliError(f"Symlink in game target: {parent}", "unsafe_path", 2)
    return path


def cfg_quote(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CliError("Configuration values cannot contain control characters.", "invalid_text", 2)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_vanilla_secrets(secrets: dict[str, str]) -> None:
    # L4D2's native sv_password blocks Steam queries/registration in the real
    # vanilla server. Do not advertise a broken privacy control or bypass A2S.
    if secrets.get("join_password"):
        raise CliError("Vanilla L4D2 join passwords are unsupported: sv_password can block Steam queries and joining. "
                       "Run server configure --clear-join-password, then restart. RCON passwords remain supported.",
                       "unsupported_join_password", 2)
    for value in secrets.values():
        cfg_quote(value)


def render_cfg(config: Config, secrets: dict[str, str]) -> str:
    config.validate()
    validate_vanilla_secrets(secrets)
    difficulty = {"easy": "Easy", "normal": "Normal", "hard": "Hard", "impossible": "Impossible"}[config.difficulty]
    values = {
        "hostname": config.name, "sv_lan": "0", "sv_password": secrets.get("join_password", ""),
        "rcon_password": secrets.get("rcon_password", ""), "sv_steamgroup": config.steam_group_id,
        "sv_steamgroup_exclusive": "1" if config.visibility == "group" else "0",
        "sv_allow_lobby_connect_only": "1" if config.visibility == "group" else "0",
        "mp_gamemode": config.mode, "sv_hibernate_when_empty": "0",
    }
    if config.mode == "coop":
        values["z_difficulty"] = difficulty
    return "// Managed by l4d2-server-cli; contains private settings.\n" + "".join(
        f"{key} {cfg_quote(value)}\n" for key, value in values.items())


def systemd_quote(value: str, *, command: bool = True) -> str:
    if any(ord(c) < 32 for c in value):
        raise CliError("Invalid systemd argument.", "invalid_text", 2)
    value = value.replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_unit(config: Config) -> str:
    # Both the source entry and installed wheel ship this file beside the module.
    template = (Path(__file__).parent / "templates/l4d2-main.service.in").read_text(encoding="utf-8")
    args = [str(Path(config.game_dir) / "srcds_run"), "-game", "left4dead2", "-console",
            "-strictportbind", "-ip", config.bind, "-port", str(config.port),
            "+servercfgfile", CFG_NAME, "+mp_gamemode", config.mode,
            "+sv_hibernate_when_empty", "0", "+map", config.map]
    # WorkingDirectory is a single literal path in systemd 245, unlike the
    # quoted word lists in ExecStart/ReadWritePaths. Escape specifiers only.
    # A final slash preserves paths ending in whitespace or a backslash.
    return template.replace("@WORKDIR@", systemd_quote(config.game_dir, command=False)).replace(
        "@DIRECTORY@", config.game_dir.replace("%", "%%") + "/").replace(
        "@EXEC@", " ".join(systemd_quote(arg) for arg in args))


@dataclass(frozen=True)
class Source:
    steamcmd_archive: str | None = None
    steamcmd_path: str | None = None
    server_archive: str | None = None
    offline: bool = False
    sha256: str | None = None

    def validate(self) -> None:
        if sum(bool(item) for item in (self.steamcmd_archive, self.steamcmd_path, self.server_archive)) > 1:
            raise CliError("Installation sources are mutually exclusive.", "source_conflict", 2)
        if self.offline and not self.server_archive:
            raise CliError("--offline requires --server-archive.", "invalid_offline_source", 2)
        if self.sha256 and (not (self.steamcmd_archive or self.server_archive) or
                            not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256)):
            raise CliError("--sha256 requires an archive and a 64-digit SHA256.", "invalid_checksum", 2)
        for filename in (self.steamcmd_archive, self.server_archive, self.steamcmd_path):
            if filename:
                path = Path(filename)
                if not path.is_file() or path.is_symlink():
                    raise CliError(f"Input must be a regular file: {filename}", "invalid_source", 2)

    @property
    def kind(self) -> str:
        if self.server_archive:
            return "server_archive"
        if self.steamcmd_archive:
            return "steamcmd_archive"
        return "steamcmd_path" if self.steamcmd_path else "online"
