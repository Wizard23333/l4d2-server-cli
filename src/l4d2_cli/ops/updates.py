"""Explicit stopped-installation Steam updates with durable failure blocking."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import time
import uuid

from .. import host, packages, steam, storage
from ..common import CliError, deadline_remaining, progress
from ..model import Source
from . import access, backup, base, rooms


def state_path(game_dir):
    key = hashlib.sha256(str(game_dir).encode('utf-8')).hexdigest()[:24]
    return base.ROOT / 'updates' / (key + '.json')


def guard(game_dir):
    record = base.read(state_path(game_dir))
    if record.get('status') in {'updating', 'failed', 'interrupted'}:
        raise CliError('上次游戏更新未完成，请用同一来源重跑 room update，完成校验后再启动。',
                       'update_incomplete', 4, {'update': record})


def update(room, tool_path=None, steam_user='anonymous', steam_tcp=False, timeout=3600, dry_run=False, yes=False):
    steam.validate_login_name(steam_user)
    source = Source(steamcmd_path=tool_path)
    source.validate()
    affected = rooms.affected(room)
    before = packages.game_build(Path(room['game_dir']))
    plan = {'action': 'game-update', 'room': room['id'], 'game_dir': room['game_dir'],
            'affected_rooms': affected, 'requires_stopped_rooms': True, 'before_build': before,
            'source': 'official_steamcmd', 'backup': 'private_configuration_and_plugins',
            'game_binary_rollback': False, 'starts_server': False,
            'minimum_free_bytes': 5 * 1024**3}
    early = base.prepare(plan, dry_run, yes)
    if early:
        return early
    host.require_root()
    access.stopped(room)
    if shutil.disk_usage(room['game_dir']).free < plan['minimum_free_bytes']:
        raise CliError('更新至少预留 5 GiB；大型更新可能需要更多空间。', 'insufficient_space', 4)
    snapshot = backup.create(room, yes=True)
    deadline = time.monotonic() + timeout
    record = {'status': 'preparing', 'room': room['id'], 'game_dir': room['game_dir'],
              'before_build': before, 'backup': snapshot, 'affected_rooms': affected}
    with storage.operation_lock():
        access.stopped(room)
        if rooms.get(room['id']) != room or rooms.affected(room) != affected:
            raise CliError('备份期间房间配置已改变，请重新预览更新。', 'configuration_changed', 4)
        base.recover()
        uid, gid = base.account(room['user'], room.get('group'))
        # New private tool home can clone an explicitly selected authenticated cache.
        home = base.ROOT / 'update-tools' / uuid.uuid4().hex
        home.parent.mkdir(parents=True, exist_ok=True)
        cached = storage.PATHS.steamcmd / 'steamcmd.sh'
        if not tool_path and cached.is_file():
            source = Source(steamcmd_path=str(cached))
        try:
            tool = steam.prepare_tool(source, home, deadline_remaining(deadline))
            home.chmod(0o700)
            host.own_tree(home, uid, gid)
            # Refuse a stale snapshot instead of silently overwriting newer private settings.
            document = backup.inspect(snapshot['output'], snapshot['sha256'])
            for name, item in document['files'].items():
                if name.startswith('game/') and storage.sha256(base.regular(rooms.game(room) / name[5:])) != item['sha256']:
                    raise CliError('备份后配置或插件改变，请重新执行更新。', 'snapshot_conflict', 4)
            record.update(status='updating', tool_home=str(home), started_at=int(time.time()))
            base.save(state_path(room['game_dir']), record, private=True)
            progress('已保存配置/插件备份，正在通过 SteamCMD 更新官方游戏文件……')
            metadata = steam.download_game(tool, Path(room['game_dir']), deadline_remaining(deadline),
                                           home=home, steam_user=steam_user, steam_tcp=steam_tcp,
                                           run_user=room['user'])
            # Validate runtime as the registered unprivileged account.
            from ..common import run
            libs = run(['runuser', '-u', room['user'], '--', 'ldd', str(Path(room['game_dir']) / 'srcds_linux')],
                       timeout=min(15, deadline_remaining(deadline)), check=False)
            if libs.returncode or 'not found' in libs.stdout:
                raise CliError('更新后缺少游戏运行依赖，请先修复再重试。', 'missing_runtime', 4)
            for name, item in document['files'].items():
                if name.startswith('game/') and storage.sha256(base.regular(rooms.game(room) / name[5:])) != item['sha256']:
                    raise CliError('SteamCMD 改变了受备份保护的配置/插件，请检查备份并恢复后重试。', 'update_state_changed', 4)
            record.update(status='complete', after_build=metadata.get('game_build'), finished_at=int(time.time()))
            base.save(state_path(room['game_dir']), record, private=True)
        except BaseException as exc:
            if record['status'] == 'updating':
                record.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                              error=getattr(exc, 'code', 'operation_failed'))
                base.save(state_path(room['game_dir']), record, private=True)
            raise
    return {**record, 'started': False, 'next_steps': ['检查版本和插件兼容性，再单独启动房间。',
                                                     '该备份包含配置/插件，不包含旧游戏二进制。']}
