"""Owned systemd timers for fixed backup and read-only health operations."""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
import re
import signal
import threading
import time
import uuid

from .. import host, storage
from ..common import CliError, run
from ..model import systemd_quote
from . import backup, base, health, rooms


def path(identifier):
    return base.ROOT / 'schedules' / (base.identifier(identifier) + '.json')


def get(identifier):
    value = base.read(path(identifier))
    if (not value or value.get('id') != identifier or value.get('kind') not in {'backup', 'health'} or
            value.get('version') != 1 or type(value.get('keep')) is not int or not 1 <= value['keep'] <= 100):
        raise CliError('定时任务不存在或记录格式无效。', 'schedule_not_found', 4)
    base.identifier(value['room'])
    return value


def unit_names(identifier):
    name = 'l4d2-cli-' + base.identifier(identifier)
    return name + '.service', name + '.timer'


def calendar(at=None, every=None):
    if at is not None and every is not None:
        raise CliError('定时任务请选择每日时间或分钟间隔。', 'invalid_schedule', 2)
    if at is not None:
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', at):
            raise CliError('每日时间使用 HH:MM，以目标主机时区为准。', 'invalid_schedule', 2)
        return 'OnCalendar=*-*-* ' + at + ':00\nPersistent=true\n'
    if type(every) is not int or not 1 <= every <= 1440:
        raise CliError('检查间隔为 1–1440 分钟。', 'invalid_schedule', 2)
    return f'OnBootSec={every}min\nOnUnitInactiveSec={every}min\n'


def add(identifier, room, kind, at=None, every=None, keep=7, cli_path='/usr/local/bin/l4d2', dry_run=False, yes=False):
    identifier = base.identifier(identifier)
    if kind not in {'backup', 'health'} or type(keep) is not int or not 1 <= keep <= 100:
        raise CliError('任务类型为 backup/health，备份保留数为 1–100。', 'invalid_schedule', 2)
    if at is None and every is None:
        at, every = ('04:00', None) if kind == 'backup' else (None, 15)
    timer_settings = calendar(at, every)
    service, timer = unit_names(identifier)
    if path(identifier).exists() or any((base.UNITS / n).exists() for n in (service, timer)):
        raise CliError('任务 ID 或服务已存在，请检查后用新的 ID；不会覆盖。', 'schedule_conflict', 4)
    from ..web.manager import trusted_cli
    cli = trusted_cli(cli_path)
    service_text = ('# Managed by l4d2-server-cli\n[Unit]\nDescription=L4D2 ' + kind + ' ' + identifier +
                    '\n[Service]\nType=oneshot\nUser=root\nUMask=0077\nNoNewPrivileges=true\n'
                    'PrivateTmp=true\nTimeoutStartSec=1800\nExecStart=' + systemd_quote(cli) +
                    ' schedule _run --id ' + identifier + ' --json\n')
    timer_text = ('# Managed by l4d2-server-cli\n[Unit]\nDescription=L4D2 timer ' + identifier +
                  '\n[Timer]\n' + timer_settings + 'Unit=' + service + '\n[Install]\nWantedBy=timers.target\n')
    record = {'version': 1, 'id': identifier, 'room': room['id'], 'kind': kind, 'keep': keep,
              'game_dir': room['game_dir'], 'service': service, 'timer': timer, 'at': at, 'every_minutes': every,
              'timezone': list(time.tzname), 'history': [],
              'hashes': {service: hashlib.sha256(service_text.encode()).hexdigest(),
                         timer: hashlib.sha256(timer_text.encode()).hexdigest()}}
    early = base.prepare({'action': 'schedule-add', **{k: record[k] for k in
                          ('id', 'room', 'kind', 'keep', 'service', 'timer', 'at', 'every_minutes', 'timezone')},
                          'enables_timer': True, 'missed_run_catchup': at is not None, 'restarts_game': False,
                          'backup_scope': 'configuration_plugins_mods_map_source_manifest'}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        if rooms.get(room['id']) != room:
            raise CliError('房间在预览后发生变化，请重新创建计划。', 'configuration_changed', 4)
        if path(identifier).exists() or any((base.UNITS / n).exists() for n in (service, timer)):
            raise CliError('任务 ID 已被其他操作创建。', 'schedule_conflict', 4)
        base.transaction([(base.UNITS / service, service_text.encode(), 0o644, 0, 0),
                          (base.UNITS / timer, timer_text.encode(), 0o644, 0, 0),
                          (path(identifier), base.json_bytes(record), 0o600, 0, 0)])
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', timer])
    return {'saved': True, 'schedule': record, 'next_steps': ['schedule list 查看最近结果；schedule remove --id 可停用并移除。']}


def verify_units(record):
    service, timer = unit_names(record['id'])
    if record.get('service') != service or record.get('timer') != timer:
        raise CliError('任务服务名称不匹配。', 'schedule_conflict', 4)
    for name in (service, timer):
        if record.get('hashes', {}).get(name) != storage.sha256(base.regular(base.UNITS / name)):
            raise CliError('定时任务文件已被外部更改，先检查内容。', 'schedule_conflict', 4)


def listing():
    reports = []
    folder = base.ROOT / 'schedules'
    storage.reject_links(folder)
    for file in sorted(folder.glob('*.json')):
        record = get(file.stem)
        report = {k: record.get(k) for k in ('id', 'room', 'kind', 'keep', 'at', 'every_minutes', 'timezone', 'last_run')}
        report['backups'] = record.get('history', [])
        try:
            verify_units(record)
            info = run(['systemctl', 'show', record['timer'], '--no-pager',
                        '--property=ActiveState,NextElapseUSecRealtime,LastTriggerUSec'], check=False)
            report['timer'] = dict(line.split('=', 1) for line in info.stdout.splitlines() if '=' in line)
            job = run(['systemctl', 'show', record['service'], '--no-pager',
                       '--property=ActiveState,Result,ExecMainStatus,MainPID'], check=False)
            report['service'] = dict(line.split('=', 1) for line in job.stdout.splitlines() if '=' in line)
            if (report.get('last_run') or {}).get('status') == 'running' and report['service'].get('MainPID') == '0':
                report['last_run'] = {**report['last_run'], 'ok': False, 'status': 'interrupted', 'error': 'worker_interrupted'}
            report['units_match'] = True
        except (CliError, OSError):
            report['units_match'] = False
        reports.append(report)
    return {'schedules': reports}


def remove(identifier, dry_run=False, yes=False):
    record = get(identifier)
    verify_units(record)
    early = base.prepare({'action': 'schedule-remove', 'id': identifier, 'kind': record['kind'],
                          'deletes_backups': False, 'restarts_game': False}, dry_run, yes)
    if early:
        return early
    with job_lock(identifier), storage.operation_lock():
        record = get(identifier)
        verify_units(record)
        run(['systemctl', 'disable', '--now', record['timer']])
        # Do not interrupt an active private snapshot midway through a write.
        info = host.service_info(service=record['service'])
        if info.get('MainPID'):
            raise CliError('任务仍在运行，定时器已停用；任务结束后重试移除。', 'schedule_running', 4)
        base.transaction([(base.UNITS / name, None, 0o644, 0, 0) for name in
                          (record['service'], record['timer'])] + [(path(identifier), None, 0o600, 0, 0)])
        run(['systemctl', 'daemon-reload'])
    return {'removed': identifier, 'backups_retained': True}


@contextlib.contextmanager
def job_lock(identifier):
    import fcntl
    file = path(identifier).with_suffix('.lock')
    storage.reject_links(file)
    with file.open('a') as stream:
        os.chmod(file, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CliError('同一定时任务已在执行。', 'schedule_running', 4) from None
        yield


def prune(record):
    """Only delete this job's known, unchanged archives after a successful new backup."""
    folder = base.ROOT / 'scheduled-backups' / record['id']
    history = record.get('history', [])
    retained = history[-record['keep']:]
    for item in history[:-record['keep']]:
        target = Path(item['output'])
        if target.parent != folder or not re.fullmatch(r'[0-9a-f]{32}\.zip', target.name):
            raise CliError('备份历史路径不属于该任务。', 'unsafe_path', 4)
        if target.exists():
            if storage.sha256(base.regular(target)) != item['sha256']:
                raise CliError('旧备份摘要变化，保留文件供检查。', 'backup_conflict', 4)
            base.unlink(target)
    record['history'] = retained


def worker(identifier):
    host.require_root()
    previous = signal.getsignal(signal.SIGTERM)
    managed = threading.current_thread() is threading.main_thread()
    def terminate(signum, frame):
        raise KeyboardInterrupt()
    if managed:
        signal.signal(signal.SIGTERM, terminate)
    try:
        return _worker(identifier)
    finally:
        if managed:
            signal.signal(signal.SIGTERM, previous)


def _worker(identifier):
    get(identifier)
    with job_lock(identifier):
        record = get(identifier)
        verify_units(record)
        room = rooms.get(record['room'])
        if room['game_dir'] != record['game_dir']:
            raise CliError('房间已改为另一游戏目录，请重建该定时任务。', 'schedule_conflict', 4)
        started = int(time.time())
        record['last_run'] = {'started_at': started, 'ok': None, 'status': 'running'}
        with storage.operation_lock():
            base.save(path(identifier), record, private=True)
        try:
            if record['kind'] == 'backup':
                output = base.ROOT / 'scheduled-backups' / identifier / (uuid.uuid4().hex + '.zip')
                report = backup.create(room, output=str(output), yes=True)
                record.setdefault('history', []).append({'output': report['output'], 'sha256': report['sha256']})
                with storage.operation_lock():
                    # Persist first: if pruning is interrupted the newest archive is still tracked.
                    base.save(path(identifier), record, private=True)
                    prune(record)
            else:
                report = health.check(room)
                if not report['healthy']:
                    raise CliError('定时健康检查发现异常。', 'unhealthy', 4, report)
            record['last_run'] = {'started_at': started, 'finished_at': int(time.time()), 'ok': True, 'status': 'complete', 'result': report}
        except (Exception, KeyboardInterrupt) as exc:
            record['last_run'] = {'started_at': started, 'finished_at': int(time.time()), 'ok': False, 'status': 'failed',
                                  'error': getattr(exc, 'code', 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'operation_failed'),
                                  'details': getattr(exc, 'details', {})}
            with storage.operation_lock():
                base.save(path(identifier), record, private=True)
            raise
        with storage.operation_lock():
            base.save(path(identifier), record, private=True)
        return {'id': identifier, 'kind': record['kind'], **record['last_run']}
