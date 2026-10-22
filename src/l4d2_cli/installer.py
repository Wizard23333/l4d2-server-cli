"""Single-instance installation, with explicit provenance and resumable phases."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from . import host, network, packages, steam, storage
from .common import CliError, confirm, deadline_remaining, progress, run
from .model import Config, MIN_FREE, SERVICE, Source, game_path
from .storage import PATHS

MARKER = ".l4d2-cli-install.json"


def registered_config() -> Config:
    state = storage.read_json(PATHS.state)
    if state and state.get("schema_version") != 1:
        raise CliError("Unsupported installation state.", "invalid_state", 4)
    if state and state.get("config"):
        config = Config(**state["config"])
        config.validate()
        return config
    return storage.load_config()


def nearest_existing(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def validate_target(config: Config, state: dict) -> None:
    target = game_path(config.game_dir)
    for path in (PATHS.state, PATHS.config, PATHS.secrets, PATHS.unit, PATHS.steamcmd, PATHS.cache):
        storage.reject_links(path)
    if state:
        try:
            if str(uuid.UUID(state["installation_id"])) != state["installation_id"]:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise CliError("Invalid installation identity.", "invalid_state", 4) from None
        if state.get("game_dir") != str(target):
            raise CliError("An installation is registered at a different path; migration is outside v0.1.", "target_conflict", 4)
        if target.exists():
            marker = storage.read_json(target / MARKER)
            interrupted_commit = (state.get("import_ready") and state.get("import_stage")
                                  and target.is_dir() and not any(target.iterdir()))
            if marker.get("installation_id") != state.get("installation_id") and not interrupted_commit:
                raise CliError("Target does not match the registered installation marker.", "target_conflict", 4)
    else:
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise CliError("Refusing to adopt a nonempty game target.", "target_conflict", 4)
        for path in (PATHS.config, PATHS.secrets, PATHS.unit, PATHS.hashes, PATHS.journal, PATHS.steamcmd):
            if path.exists():
                raise CliError(f"Unregistered managed file/directory already exists: {path}", "target_conflict", 4)
        service = host.service_info()
        if service.get("LoadState") != "not-found":
            raise CliError("A service with this name already exists; adoption is unsupported.", "service_conflict", 4)
    host.game_account(target)


def allocated_content_bytes(target: Path, uid: int) -> int:
    """Credit only owned regular files on the target filesystem; links never count."""
    import stat
    device = target.stat().st_dev
    total, seen = 0, set()
    for directory, directories, files in os.walk(target, followlinks=False):
        directories[:] = [name for name in directories
                          if not (Path(directory) / name).is_symlink()
                          and (Path(directory) / name).stat().st_dev == device]
        for name in files:
            if name == MARKER:
                continue
            item = (Path(directory) / name).lstat()
            identity = item.st_dev, item.st_ino
            if (not stat.S_ISREG(item.st_mode) or item.st_dev != device or
                    item.st_uid != uid or item.st_nlink != 1 or identity in seen):
                continue
            seen.add(identity)
            # Linux st_blocks measures allocated data, not an inflated sparse logical size.
            total += getattr(item, "st_blocks", 0) * 512
    return total


def space_budget(config: Config, state: dict, source: Source) -> dict:
    budget = {"initial_budget_bytes": MIN_FREE, "minimum_free_bytes": MIN_FREE,
              "reusable_partial_bytes": 0, "reusable_tool_bytes": 0,
              "resume_runtime_validation": False}
    if not state:
        return budget
    target = game_path(config.game_dir)
    if source.server_archive:
        if state.get("bundle_sha256") and (state.get("import_committed") or state.get("import_ready")):
            # These phases validate/rename existing content, without a second extraction.
            budget["minimum_free_bytes"] = 64 * 1024**2
        return budget
    # All three Steam acquisition choices reuse the same official partial game directory.
    if state.get("source") not in {"online", "steamcmd_path", "steamcmd_archive"} or not target.is_dir():
        return budget
    if state.get("phase") == "runtime_validation":
        try:
            packages.validate_game_tree(target)
        except CliError:
            pass
        else:
            budget.update(minimum_free_bytes=64 * 1024**2, resume_runtime_validation=True)
            return budget
    uid, _gid, exists = host.game_account(target)
    if exists:
        credit = min(allocated_content_bytes(target, uid), MIN_FREE)
        tool_credit = 0
        # A retry can reuse the tool already allocated inside the original
        # installation budget. Explicit refreshes still need additional space.
        if (state.get("game_dir") == str(target) and
                not (source.steamcmd_path or source.steamcmd_archive) and
                PATHS.steamcmd.is_dir() and not PATHS.steamcmd.is_symlink() and
                PATHS.steamcmd.stat().st_dev == target.stat().st_dev):
            tool_credit = min(allocated_content_bytes(PATHS.steamcmd, uid), MIN_FREE - credit)
        budget.update(reusable_partial_bytes=credit, reusable_tool_bytes=tool_credit,
                      minimum_free_bytes=max(2 * 1024**3, MIN_FREE - credit - tool_credit))
    return budget


def refresh_tool_requested(source: Source, state: dict) -> bool:
    return bool(state and state.get("status") != "installed" and (source.steamcmd_path or source.steamcmd_archive)
                and PATHS.steamcmd.exists())


def prepare_managed_tool(source: Source, state: dict, uid: int, gid: int,
                         deadline: float) -> Path:
    # Recover a power loss between the two directory renames before replacing a cache.
    pending = state.get("pending_tool_backup")
    if pending:
        backup = Path(pending)
        expected = f"{PATHS.steamcmd.name}.previous-{state['installation_id']}-"
        storage.reject_links(backup)
        if backup.parent != PATHS.steamcmd.parent or not backup.name.startswith(expected):
            raise CliError("Unregistered SteamCMD backup path.", "unsafe_tool", 4)
        if not PATHS.steamcmd.exists() and backup.exists():
            steam.validate_tool(backup)
            os.rename(backup, PATHS.steamcmd)
            host.own_tree(PATHS.steamcmd, uid, gid)
        elif backup.exists() and PATHS.steamcmd.exists():
            steam.validate_tool(PATHS.steamcmd)
            if str(backup) not in state.setdefault("previous_tools", []):
                state["previous_tools"].append(str(backup))
        state.pop("pending_tool_backup", None)
        storage.save_json(PATHS.state, state)
    refresh = refresh_tool_requested(source, state)
    if PATHS.steamcmd.exists() and not refresh:
        return steam.validate_tool(PATHS.steamcmd)
    if source.steamcmd_path and Path(source.steamcmd_path).resolve().parent == PATHS.steamcmd:
        raise CliError("Prepare the input SteamCMD in a separate directory, outside the managed cache.",
                       "tool_conflict", 4)
    stage = Path(tempfile.mkdtemp(prefix="steamcmd-", dir=PATHS.cache))
    state["tool_stage"] = str(stage)
    storage.save_json(PATHS.state, state)
    # Finish copying and validating before moving the old cache.
    steam.prepare_tool(source, stage, deadline_remaining(deadline))
    host.own_tree(stage, uid, gid)
    stage.chmod(0o700)
    PATHS.steamcmd.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if refresh:
        generation = int(state.get("tool_generation", 0)) + 1
        backup = PATHS.steamcmd.with_name(
            f"{PATHS.steamcmd.name}.previous-{state['installation_id']}-{generation}")
        storage.reject_links(backup)
        if backup.exists():
            raise CliError("Previous SteamCMD backup already exists; preserve it before retrying.",
                           "tool_conflict", 4)
        state.update(tool_generation=generation, pending_tool_backup=str(backup))
        storage.save_json(PATHS.state, state)
    try:
        if backup:
            os.rename(PATHS.steamcmd, backup)
            backup.chmod(0o700)
            os.chown(backup, 0, 0)
        os.rename(stage, PATHS.steamcmd)
    except BaseException:
        if backup and backup.exists() and not PATHS.steamcmd.exists():
            os.rename(backup, PATHS.steamcmd)
            host.own_tree(PATHS.steamcmd, uid, gid)
        state.pop("pending_tool_backup", None)
        storage.save_json(PATHS.state, state)
        raise
    if backup:
        state.setdefault("previous_tools", []).append(str(backup))
        progress(f"Preserved previous private SteamCMD cache at {backup}")
    state.pop("pending_tool_backup", None)
    state.pop("tool_stage", None)
    storage.save_json(PATHS.state, state)
    return steam.validate_tool(PATHS.steamcmd)


def install_plan(directory: str | None, source: Source, timeout: float,
                 initial_config: Config | None = None) -> tuple[dict, Config, dict]:
    host.require_platform()
    source.validate()
    state = storage.read_json(PATHS.state)
    config = registered_config()
    # The setup wizard can choose a free port before the first installation.
    # Registered retries always retain the original installation configuration.
    if not state and initial_config is not None:
        config = replace(initial_config)
    if directory:
        config.game_dir = str(game_path(directory))
    config.validate()
    validate_target(config, state)
    missing = host.missing_dependencies()
    target = game_path(config.game_dir)
    free = shutil.disk_usage(nearest_existing(target)).free
    budget = space_budget(config, state, source)
    # A registered retry with an existing tool does not fetch the bootstrap
    # archive again. Keep Steam/API checks, but probe only the needed source.
    network_source = source
    if (state and source.kind == "online" and
            (PATHS.steamcmd / "steamcmd.sh").is_file()):
        network_source = Source(steamcmd_path=str(PATHS.steamcmd / "steamcmd.sh"))
    plan = {"target": str(target), "service": SERVICE, "source": source.kind, "offline": source.offline,
            "configuration": asdict(config),
            "installed": state.get("status") == "installed", "missing_dependencies": missing,
            "refreshes_private_steamcmd": not budget["resume_runtime_validation"] and refresh_tool_requested(source, state),
            "preserves_previous_tool": not budget["resume_runtime_validation"] and refresh_tool_requested(source, state),
            "free_bytes": free, **budget, "requires_root": True,
            "platform": host.platform_report(),
            "files": [str(PATHS.config), str(PATHS.secrets), str(PATHS.unit), str(PATHS.state)],
            "starts_server": False, "opens_firewall": False,
            "network": network.check(network_source, timeout=min(30, timeout), needs_dependencies=bool(missing),
                                     needs_steam=not budget["resume_runtime_validation"])
            if state.get("status") != "installed" else {"checks": [], "required_failures": 0, "scope": "Already installed; no download needed."}}
    if source.server_archive and state and target.is_dir() and any(item.name != MARKER for item in target.iterdir()) and not state.get("import_committed") and not state.get("import_ready"):
        plan["preserves_partial_content_at"] = str(target.parent / f".{target.name}.partial-{state['installation_id']}")
    return plan, config, state


def marker(target: Path, state: dict) -> None:
    storage.save_json(target / MARKER, {"schema_version": 1, "installation_id": state["installation_id"]}, private=True)


def commit_import(target: Path, state: dict) -> None:
    stage = Path(state["import_stage"])
    storage.reject_links(stage)
    if stage.parent != target.parent or not stage.name.startswith(".l4d2-import-"):
        raise CliError("Unregistered import stage.", "unsafe_path", 4)
    if not stage.exists():
        # Atomic rename completed, but its following state write was interrupted.
        packages.validate_game_tree(target)
        if storage.read_json(target / MARKER).get("installation_id") != state["installation_id"]:
            raise CliError("Imported target marker mismatch.", "target_conflict", 4)
    else:
        if storage.read_json(stage / MARKER).get("installation_id") != state["installation_id"]:
            raise CliError("Import stage marker mismatch.", "target_conflict", 4)
        packages.validate_game_tree(stage)
        content = [item for item in target.iterdir() if item.name != MARKER]
        if content:
            backup = target.parent / f".{target.name}.partial-{state['installation_id']}"
            storage.reject_links(backup)
            if backup.exists():
                raise CliError("Partial-content backup already exists; refusing to replace it.", "target_conflict", 4)
            state["content_backup"] = str(backup)
            storage.save_json(PATHS.state, state)
            os.rename(target, backup)
            backup.chmod(0o700)
            os.chown(backup, 0, 0)
            progress(f"Preserved partial content at {backup}")
        else:
            (target / MARKER).unlink(missing_ok=True)
        os.replace(stage, target)
    state["import_committed"] = True
    state["import_ready"] = False
    state.pop("import_stage", None)
    storage.save_json(PATHS.state, state)


def runtime_check(target: Path, timeout: float) -> dict:
    metadata = packages.validate_game_tree(target)
    # ldd is run only as the game account, including when content came from a local bundle.
    args, env = host.as_game_user(["ldd", str(target / "srcds_linux")], target)
    libraries = run(args, env=env, timeout=min(15, timeout), check=False)
    if libraries.returncode or "not found" in libraries.stdout:
        raise CliError("Linux runtime dependencies are unresolved: " + libraries.stdout[-1500:], "missing_runtime")
    return metadata


def apply_configuration(config: Config, secrets: dict, state: dict, gid: int, deadline: float | None = None) -> None:
    deadline = deadline or time.monotonic() + 180
    deadline_remaining(deadline)
    old = storage.load_config()
    storage.write_configuration(config, secrets, state, gid)
    try:
        host.systemctl("daemon-reload", min(60, deadline_remaining(deadline)))
        host.systemctl("enable" if config.autostart else "disable", min(60, deadline_remaining(deadline)))
    except BaseException:
        storage.recover(config)
        try:
            host.systemctl("daemon-reload")
            host.systemctl("enable" if old.autostart else "disable")
        except CliError:
            progress("Configuration was restored; systemd synchronization needs a confirmed retry.")
        raise
    storage.commit()


def install(directory: str | None, source: Source, *, dry_run: bool, yes: bool,
            timeout: float, steam_user: str = "anonymous", steam_tcp: bool = False,
            initial_config: Config | None = None) -> dict:
    steam.validate_login_name(steam_user)
    deadline = time.monotonic() + timeout
    plan, config, state = install_plan(directory, source, deadline_remaining(deadline), initial_config)
    if dry_run:
        return {"dry_run": True, "plan": plan}
    host.require_root()
    with storage.operation_lock():
        # Re-read target registration after obtaining the mutation lock.
        state = storage.read_json(PATHS.state)
        validate_target(config, state)
        if state.get("status") == "installed":
            if PATHS.journal.exists():
                confirm("Recover the interrupted configuration transaction for main; no service restart.", yes)
                storage.recover(config)
                host.systemctl("daemon-reload")
            storage.check_managed_hashes(config)
            metadata = packages.validate_game_tree(game_path(config.game_dir))
            return {"already_installed": True, "target": config.game_dir, **metadata}
        network.require_network(plan["network"])
        missing = host.missing_dependencies()
        if source.offline and missing:
            raise CliError("Offline installation requires preinstalled dependencies: " + ", ".join(missing), "missing_dependencies")
        # Recompute after the operation lock: another writer may have used the disk since planning.
        budget = space_budget(config, state, source)
        free = shutil.disk_usage(nearest_existing(game_path(config.game_dir))).free
        if free < budget["minimum_free_bytes"]:
            raise CliError("Insufficient free space for this installation phase; partial content is retained.",
                           "insufficient_space", details={"free_bytes": free, **budget})
        bundle = None
        if source.server_archive:
            progress("Validating complete Linux game bundle before system changes.")
            bundle = packages.validate_bundle(Path(source.server_archive), source.sha256, deadline)
            if state.get("import_committed") and state.get("bundle_sha256") != bundle["sha256"]:
                raise CliError("Resume with the identical imported bundle; updates are outside v0.1.", "source_conflict", 4)
            if state.get("import_ready") and state.get("bundle_sha256") != bundle["sha256"]:
                raise CliError("Resume the interrupted commit with the identical bundle.", "source_conflict", 4)
        elif source.steamcmd_archive:
            packages.validate_bootstrap(Path(source.steamcmd_archive), source.sha256, deadline)
        host.check_udp_available(config.bind, config.port)
        if host.service_info().get("MainPID"):
            raise CliError("Installation cannot replace content while the service is running.", "running_install_conflict", 4)
        if plan.get("preserves_partial_content_at"):
            progress("Partial content will be preserved at " + plan["preserves_partial_content_at"])
        confirm(f"Install main at {config.game_dir}, source={source.kind}, offline={source.offline}. "
                "Create the game account and systemd unit; unknown Steam phases remain unverified. Server stays stopped.", yes)
        deadline_remaining(deadline)
        target = game_path(config.game_dir)
        # These application directories contain public config/state plus
        # individually protected private files. A root umask of 077 must not
        # prevent the game account from traversing to its private tool.
        for public_parent in (PATHS.state.parent, PATHS.config.parent):
            storage.reject_links(public_parent)
            public_parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            public_parent.chmod(0o755)
        if not state:
            state = {"schema_version": 1, "installation_id": str(uuid.uuid4()), "game_dir": str(target),
                     "config": asdict(config), "status": "installing", "phase": "dependencies"}
            storage.save_json(PATHS.state, state)
        if state.get("source") == "server_archive" and not source.server_archive:
            raise CliError("Resume the registered game-bundle import; switching imported content to an online update is unsupported.",
                           "source_conflict", 4)
        state.update(source=source.kind, offline=source.offline, status="installing", last_error=None)
        storage.save_json(PATHS.state, state)
        try:
            state["phase"] = "dependencies"
            storage.save_json(PATHS.state, state)
            if missing:
                host.install_dependencies(missing, deadline_remaining(deadline))
            state["phase"] = "game_account"
            storage.save_json(PATHS.state, state)
            uid, gid = host.create_account(target, deadline_remaining(deadline))
            target.mkdir(parents=True, exist_ok=True)
            os.chown(target, uid, gid)
            if not (target / MARKER).exists():
                marker(target, state)
            PATHS.cache.mkdir(parents=True, exist_ok=True)
            state["phase"] = "content"
            storage.save_json(PATHS.state, state)
            if bundle:
                if state.get("import_stage") and state.get("import_ready"):
                    commit_import(target, state)
                if state.get("import_committed"):
                    packages.validate_game_tree(target)
                else:
                    state["phase"] = "bundle_extract"
                    stage = Path(tempfile.mkdtemp(prefix=".l4d2-import-", dir=target.parent))
                    state["import_stage"] = str(stage)
                    state["bundle_sha256"] = bundle["sha256"]
                    storage.save_json(PATHS.state, state)
                    packages.extract(Path(source.server_archive), stage, prefix="serverfiles/", deadline=deadline)
                    packages.validate_game_tree(stage)
                    host.own_tree(stage, uid, gid)
                    stage.chmod(0o755)
                    marker(stage, state)
                    state["phase"] = "import_commit"
                    state["import_ready"] = True
                    storage.save_json(PATHS.state, state)
                    commit_import(target, state)
                state["bundle_sha256"] = bundle["sha256"]
            elif not budget["resume_runtime_validation"]:
                state["phase"] = "steamcmd"
                storage.save_json(PATHS.state, state)
                tool = prepare_managed_tool(source, state, uid, gid, deadline)
                state["phase"] = "steam_content"
                storage.save_json(PATHS.state, state)
                steam.download_game(tool, target, deadline_remaining(deadline), home=PATHS.steamcmd,
                                    steam_user=steam_user, steam_tcp=steam_tcp)
            state["phase"] = "runtime_validation"
            storage.save_json(PATHS.state, state)
            metadata = runtime_check(target, deadline_remaining(deadline))
            state.update(status="installed", phase="configured", config=asdict(config), **metadata)
            apply_configuration(config, storage.load_secrets(), state, gid, deadline)
            return {"installed": True, "target": str(target), "service": SERVICE, "source": source.kind,
                    "offline": source.offline, "started": False, **metadata,
                    "next_steps": ["l4d2 server configure", "l4d2 server start", "l4d2 doctor"]}
        except BaseException as exc:
            state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                         last_error=getattr(exc, "code", "cancelled" if isinstance(exc, KeyboardInterrupt) else "operation_failed"))
            storage.save_json(PATHS.state, state)
            raise
