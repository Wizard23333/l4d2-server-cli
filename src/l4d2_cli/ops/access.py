"""Per-port authenticated Steam allowlists; client acceptance stays explicit."""
from __future__ import annotations

import re
import hashlib
import time
import uuid
from pathlib import Path

from .. import host, storage
from ..common import CliError
from . import base, plugins, rooms

PLUGIN = 'l4d2_cli_access.smx'


def state_path(room):
    return base.ROOT / 'access' / (base.identifier(room['id']) + '.json')


def policy_path(room):
    return plugins.directory(room) / 'configs' / ('l4d2_cli_access_%d.txt' % room['port'])


def stopped(room):
    affected = rooms.affected(room)
    for room_id in affected:
        profile = rooms.get(room_id)
        info = rooms.check(profile)
        if info.get('MainPID') or info.get('ActiveState') not in {'inactive', 'failed'} or host.service_pids(info):
            raise CliError('请先停止所有共享游戏目录的房间：' + ', '.join(affected), 'room_running', 4)
    return affected


def ids_from_file(filename):
    path = base.regular(filename)
    if path.stat().st_size > 65536:
        raise CliError('好友名单过大。', 'invalid_allowlist', 2)
    ids = []
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        value = raw.strip()
        if not value or value.startswith('#'):
            continue
        if (not re.fullmatch(r'[0-9]{17}', value) or
                not 76561197960265729 <= int(value) <= 76561202255233023):
            raise CliError('每行填写一个有效的个人 SteamID64，不能用昵称或组 ID。', 'invalid_allowlist', 2)
        if value not in ids:
            ids.append(value)
    if not 1 <= len(ids) <= 1024:
        raise CliError('好友名单必须包含 1–1024 个 SteamID64；记得包含服主。', 'invalid_allowlist', 2)
    return ids


def status(room, since=0):
    record = base.read(state_path(room))
    if not record:
        if (plugins.directory(room) / 'plugins' / PLUGIN).exists():
            return {'configured': True, 'mode': 'missing_policy', 'files_match': False,
                    'runtime_verified': False, 'client_join': 'unverified'}
        return {'configured': False, 'mode': 'unmanaged', 'runtime_verified': False, 'client_join': 'unverified'}
    valid = (record.get('port') == room['port'] and record.get('game_dir') == room['game_dir'] and
             record.get('mode') in {'friends', 'public'} and
             set(record.get('hashes', {})) == {str(policy_path(room)), str(plugins.directory(room) / 'plugins' / PLUGIN)})
    for path, digest in record.get('hashes', {}).items():
        try:
            valid = valid and storage.sha256(base.regular(path)) == digest
        except (CliError, OSError):
            valid = False
    receipt = plugins.directory(room) / 'data' / ('l4d2_cli_access_%d.txt' % room['port'])
    evidence = []
    try:
        if receipt.exists() and base.regular(receipt).stat().st_size <= 1024:
            evidence = receipt.read_text(encoding='utf-8').splitlines()
    except (CliError, OSError, UnicodeError):
        evidence = []
    fresh = False
    if len(evidence) == 3 and evidence[2].isdigit():
        timestamp = int(evidence[2])
        fresh = (since <= timestamp <= time.time() + 1 and time.time() - timestamp <= 20 and
                 evidence[0] == record.get('generation') and evidence[1] == record.get('mode'))
    return {'configured': True, 'mode': record.get('mode'), 'allow_count': record.get('allow_count', 0),
            'files_match': bool(valid), 'runtime_verified': bool(valid and fresh), 'client_join': 'unverified'}


def guard(room):
    report = status(room)
    if report['configured'] and not report['files_match']:
        raise CliError('好友访问配置或插件发生变化，请重新检查 access configure；禁止按旧策略启动。',
                       'access_configuration_changed', 4)


def ready(room, since=0):
    report = status(room, since)
    return not report['configured'] or report['runtime_verified']


def configure(room, mode, filename=None, dry_run=False, yes=False):
    if mode not in {'public', 'friends'} or (mode == 'public' and filename):
        raise CliError('使用 public，或 friends 配合 --allow-file。', 'invalid_access_mode', 2)
    ids = ids_from_file(filename) if mode == 'friends' and filename else []
    if mode == 'friends' and not ids:
        raise CliError('好友模式需要 --allow-file，文件中包含服主和好友的 SteamID64。', 'allowlist_required', 2)
    affected = rooms.affected(room)
    compiler = plugins.directory(room) / 'scripting/spcomp64'
    if not compiler.is_file():
        compiler = plugins.directory(room) / 'scripting/spcomp'
    if not compiler.is_file():
        raise CliError('先使用 plugin stack 安装 SourceMod 框架，再配置好友名单。', 'sourcemod_required', 4)
    early = base.prepare({'action': 'access-configure', 'room': room['id'], 'mode': mode,
                          'allow_count': len(ids), 'affected_rooms': affected, 'requires_stopped_rooms': True,
                          'client_join': 'unverified', 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    stopped(room)
    from .backup import create
    snapshot = create(room, scope='plugins', yes=True)
    source = Path(__file__).parent / 'assets/l4d2_cli_access.sp'
    compiled = plugins.compile_source(room, source, name=PLUGIN, yes=True)
    target = plugins.directory(room) / 'plugins' / PLUGIN
    binary = base.regular(compiled['output']).read_bytes()
    _, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        stopped(room)
        if rooms.get(room['id']) != room or rooms.affected(room) != affected:
            raise CliError('编译期间房间配置改变，请重新预览。', 'configuration_changed', 4)
        writes = [(target, binary, 0o644, 0, gid)]
        # Shared plugin requires an explicit policy for every port; missing files deny humans.
        for room_id in affected:
            profile = rooms.get(room_id)
            old = base.read(state_path(profile))
            selected = room_id == room['id']
            current_mode = mode if selected else old.get('mode', 'public')
            generation = uuid.uuid4().hex
            if selected or not old:
                content = '\n'.join([current_mode, generation, *(ids if selected else [])]) + '\n'
            else:
                guard(profile)
                previous = base.text(policy_path(profile)).splitlines()
                if len(previous) < 2 or previous[0] != current_mode:
                    raise CliError('其他共享房间的名单已改变，请先恢复其策略。', 'access_configuration_changed', 4)
                content = '\n'.join([current_mode, generation, *previous[2:]]) + '\n'
            data = content.encode('utf-8')
            record = {'mode': current_mode, 'generation': generation, 'port': profile['port'],
                      'game_dir': profile['game_dir'], 'allow_count': len(content.splitlines()) - 2,
                      'hashes': {str(target): hashlib.sha256(binary).hexdigest(),
                                 str(policy_path(profile)): hashlib.sha256(data).hexdigest()}}
            writes += [(policy_path(profile), data, 0o640, 0, gid),
                       (state_path(profile), base.json_bytes(record), 0o600, 0, 0)]
        base.transaction(writes)
    return {'saved': True, 'room': room['id'], 'mode': mode, 'allow_count': len(ids), 'backup': snapshot,
            'affected_rooms': affected, 'restart_required': True, 'client_join': 'unverified',
            'next_steps': ['用 CLI 启动房间，检查 access status 的插件运行回执。',
                           '安排名单内/外两位玩家实际加入，并测试重连和换图；框架失效时不能保证访问限制。']}
