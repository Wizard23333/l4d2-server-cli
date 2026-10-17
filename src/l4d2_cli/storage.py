"""Private durable configuration transactions and single-target locking."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import stat
import tempfile
try:
    import tomllib
except ModuleNotFoundError:
    from ._vendor import tomli as tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import CliError, REDACTOR
from .model import CFG_NAME, Config, SERVICE, game_path, render_cfg, render_unit, toml_value


@dataclass(frozen=True)
class Paths:
    config: Path = Path("/etc/l4d2-cli/config.toml")
    secrets: Path = Path("/etc/l4d2-cli/secrets.toml")
    state: Path = Path("/var/lib/l4d2-cli/state.json")
    hashes: Path = Path("/var/lib/l4d2-cli/private-hashes.json")
    journal: Path = Path("/var/lib/l4d2-cli/config-transaction.json")
    lock: Path = Path("/run/lock/l4d2-cli.lock")
    unit: Path = Path("/etc/systemd/system") / SERVICE
    steamcmd: Path = Path("/var/lib/l4d2-cli/steamcmd")
    cache: Path = Path("/var/cache/l4d2-cli")

    def managed_files(self, config: Config) -> set[Path]:
        return {self.config, self.secrets, self.state, self.hashes, self.unit,
                game_path(config.game_dir) / "left4dead2/cfg" / CFG_NAME}


PATHS = Paths()


def reject_links(path: Path) -> None:
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise CliError(f"Refusing symlinked managed path: {item}", "unsafe_path", 4)


def atomic_write(path: Path, data: bytes, mode: int = 0o644, gid: int = -1) -> None:
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".l4d2-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            if gid >= 0:
                os.fchown(handle.fileno(), 0, gid)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default: dict | None = None) -> dict:
    reject_links(path)
    if not path.exists():
        return {} if default is None else default.copy()
    try:
        if path.stat().st_size > 4 * 1024**2:
            raise ValueError("file too large")
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except PermissionError:
        raise CliError(f"Cannot read {path.name}; administrator permission is required.", "permission_denied", 3) from None
    except (OSError, ValueError) as exc:
        raise CliError(f"Cannot read managed state {path.name}: {exc}", "invalid_state") from exc


def save_json(path: Path, value: dict, *, private: bool = False) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False).encode(),
                 0o600 if private else 0o644)


def load_config(paths: Paths = PATHS) -> Config:
    reject_links(paths.config)
    if not paths.config.exists():
        return Config()
    try:
        with paths.config.open("rb") as handle:
            config = Config(**tomllib.load(handle))
        config.validate()
        return config
    except (OSError, TypeError, ValueError) as exc:
        raise CliError(f"Invalid managed configuration: {exc}", "invalid_config", 2) from exc


def load_secrets(paths: Paths = PATHS) -> dict[str, str]:
    reject_links(paths.secrets)
    if not paths.secrets.exists():
        return {}
    try:
        metadata = paths.secrets.stat()
        if metadata.st_mode & 0o077:
            raise CliError("Secrets file must have mode 0600.", "secret_permissions", 3)
        with paths.secrets.open("rb") as handle:
            values = tomllib.load(handle)
        if any(key not in {"join_password", "rcon_password"} or not isinstance(value, str)
               for key, value in values.items()):
            raise CliError("Invalid secrets file.", "invalid_secrets", 2)
        for value in values.values():
            REDACTOR.register(value)
        return values
    except PermissionError:
        raise CliError("Administrator permission required to read private settings.", "permission_denied", 3) from None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def operation_lock(paths: Paths = PATHS):
    import fcntl
    reject_links(paths.lock)
    fd = os.open(paths.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CliError("Another management operation is running.", "operation_busy", 4) from None
        yield
    finally:
        os.close(fd)


def check_managed_hashes(config: Config, paths: Paths = PATHS) -> None:
    if paths.journal.exists():
        raise CliError("Unfinished configuration transaction; retry a confirmed management operation to recover.",
                       "pending_transaction", 4)
    recorded = read_json(paths.hashes)
    allowed = paths.managed_files(config)
    for name, digest in recorded.items():
        path = Path(name)
        if path not in allowed:
            raise CliError("Hash registry contains an unregistered path.", "invalid_state", 4)
        reject_links(path)
        if not path.is_file() or sha256(path) != digest:
            raise CliError(f"Managed file changed outside CLI: {path}. Preserve it and resolve the conflict.",
                           "config_conflict", 4)


def recover(config: Config, paths: Paths = PATHS) -> bool:
    if not paths.journal.exists():
        return False
    transaction = read_json(paths.journal)
    if transaction.get("schema_version") != 1:
        raise CliError("Invalid configuration journal.", "invalid_journal", 4)
    entries = transaction.get("before", [])
    allowed = paths.managed_files(config)
    decoded = []
    if not isinstance(entries, list) or not entries:
        raise CliError("Incomplete transaction journal.", "invalid_journal", 4)
    for entry in entries:
        try:
            path = Path(entry["path"])
            mode, gid = entry["mode"], entry["gid"]
            if type(mode) is not int or not 0 <= mode <= 0o777 or type(gid) is not int or gid < -1:
                raise ValueError("invalid metadata")
            data = base64.b64decode(entry["data"], validate=True) if entry["data"] is not None else None
        except (KeyError, TypeError, ValueError) as exc:
            raise CliError("Invalid rollback entry; nothing restored.", "invalid_journal", 4) from exc
        if path not in allowed:
            raise CliError("Unregistered transaction path; recovery refused.", "unsafe_journal", 4)
        reject_links(path)
        decoded.append((path, data, mode, gid))
    for path, data, mode, gid in reversed(decoded):
        if data is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, data, mode, gid)
    paths.journal.unlink()
    return True


def write_configuration(config: Config, secrets: dict[str, str], state: dict,
                        gid: int, paths: Paths = PATHS) -> None:
    """Save a rollback journal before touching any of the related files."""
    config.validate()
    recover(config, paths)
    if paths.hashes.exists():
        check_managed_hashes(config, paths)
    cfg_path = game_path(config.game_dir) / "left4dead2/cfg" / CFG_NAME
    secret_text = "".join(f"{key} = {toml_value(value)}\n" for key, value in secrets.items())
    writes = [
        (paths.config, config.toml().encode(), 0o644, -1),
        (paths.secrets, secret_text.encode(), 0o600, -1),
        (cfg_path, render_cfg(config, secrets).encode(), 0o640, gid),
        (paths.unit, render_unit(config).encode(), 0o644, -1),
        (paths.state, json.dumps(state, indent=2).encode(), 0o644, -1),
    ]
    hashes = {str(path): hashlib.sha256(data).hexdigest()
              for path, data, _, _ in writes if path != paths.state}
    writes.append((paths.hashes, json.dumps(hashes).encode(), 0o600, -1))
    before = []
    for path, _, _, _ in writes:
        reject_links(path)
        metadata = path.stat() if path.exists() else None
        before.append({"path": str(path), "data": base64.b64encode(path.read_bytes()).decode() if metadata else None,
                       "mode": stat.S_IMODE(metadata.st_mode) if metadata else 0o600,
                       "gid": metadata.st_gid if metadata else -1})
    save_json(paths.journal, {"schema_version": 1, "before": before}, private=True)
    try:
        for path, data, mode, file_gid in writes:
            atomic_write(path, data, mode, file_gid)
    except BaseException:
        recover(config, paths)
        raise
    # Caller commits after systemd daemon-reload / enable has succeeded.


def commit(paths: Paths = PATHS) -> None:
    paths.journal.unlink(missing_ok=True)
