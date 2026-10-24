"""Persistent standalone task workers, independent of the optional web process."""
from __future__ import annotations

import json
import os
import signal
import re
import subprocess
import sys
import time
import uuid
import contextlib
import contextvars
from pathlib import Path

from .. import host
from ..common import CliError, REDACTOR
from . import base

ALLOWED = {'server', 'download', 'network', 'room', 'map', 'addon', 'plugin', 'transfer', 'backup',
           'admin', 'repair', 'storage', 'rcon', 'access', 'schedule'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
SERVICE_WORKERS = contextvars.ContextVar('l4d2_service_workers', default=False)


def path(task_id):
    if len(task_id) != 32 or any(c not in '0123456789abcdef' for c in task_id):
        raise CliError('Invalid task ID.', 'invalid_task', 2)
    return base.ROOT / 'tasks' / (task_id + '.json')


def ticks(pid):
    return base.process_ticks(pid)


@contextlib.contextmanager
def task_lock(task_id):
    import fcntl
    lockfile = path(task_id).with_suffix('.lock')
    base.storage.reject_links(lockfile)
    with lockfile.open('a') as handle:
        os.chmod(lockfile, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def get(task_id):
    value = base.read(path(task_id))
    if not value:
        raise CliError('Task not found.', 'task_not_found', 4)
    if value.get('status') == 'running' and value.get('start_ticks') and ticks(value['pid']) != value['start_ticks']:
        value = {**value, 'status': 'interrupted'}
    progress = {'phase': value['status']}
    logfile = path(task_id).with_suffix('.log')
    if logfile.is_file():
        with base.input_stream(logfile) as stream:
            stream.seek(max(0, logfile.stat().st_size - 65536))
            lines = stream.read(65536).decode('utf-8', errors='replace').splitlines()
        for line in lines:
            match = re.fullmatch(r'download bytes=(\d+) total=(\d+)', line)
            if match:
                downloaded, total = map(int, match.groups())
                progress.update(phase='download', download={'bytes': downloaded, 'total': total,
                                'percent': round(downloaded * 100 / total, 1) if total else None})
            elif line.startswith('{'):
                try:
                    plan = json.loads(line)
                except ValueError:
                    continue
                if isinstance(plan, dict) and isinstance(plan.get('action'), str):
                    progress['phase'] = plan['action']
                    if plan.get('filename'):
                        progress['current_item'] = plan['filename']
                    if isinstance(plan.get('parts'), list):
                        progress['parts_total'] = len(plan['parts'])
    if value['status'] in TERMINAL:
        progress['phase'] = value['status']
    value['progress'] = progress
    return value


def entry():
    return [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-B', '-m', 'l4d2_cli']


def submit(arguments, yes=False, dry_run=False, detached=False):
    if not arguments or arguments[0] not in ALLOWED or '--background' in arguments:
        raise CliError('Tasks accept noninteractive mutating CLI operations, without shell commands or nested tasks.', 'invalid_task', 2)
    from ..cli import parser
    parsed = parser().parse_args(arguments)
    if getattr(parsed, 'dry_run', False):
        raise CliError('Run previews directly; submit the reviewed operation.', 'invalid_task', 2)
    if not hasattr(parsed, 'yes'):
        raise CliError('Only mutating operations can be submitted.', 'invalid_task', 2)
    early = base.prepare({'action': 'submit-task', 'arguments': arguments, 'runs_without_web': True}, dry_run, yes)
    if early:
        return early
    task_id = uuid.uuid4().hex
    arguments = [*arguments, *([] if '--yes' in arguments else ['--yes']), *([] if '--json' in arguments else ['--json'])]
    document = {'id': task_id, 'arguments': arguments, 'status': 'queued', 'created_at': time.time()}
    folder = path(task_id).parent
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    base.save(path(task_id), document, private=True)
    try:
        worker_entry = [*entry(), 'task', '_run', task_id]
        if detached or SERVICE_WORKERS.get():
            from ..common import run
            run(['systemd-run', '--quiet', '--collect', '--unit=l4d2-cli-task-' + task_id,
                 '--setenv=PYTHONPATH=' + str(Path(__file__).resolve().parents[2]), *worker_entry], timeout=30)
            return {'task_id': task_id, 'status': 'queued', 'worker_service': 'l4d2-cli-task-' + task_id}
        process = subprocess.Popen(worker_entry, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                   env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])})
    except (OSError, CliError):
        document.update(status='failed', error='worker_launch_failed')
        base.save(path(task_id), document, private=True)
        raise CliError('Task worker could not be started.', 'task_launch_failed', 4) from None
    return {'task_id': task_id, 'status': 'queued', 'worker_pid': process.pid}


def worker(task_id):
    host.require_root()
    with task_lock(task_id):
        document = get(task_id)
        if document['status'] != 'queued':
            raise CliError('Task is no longer queued.', 'task_conflict', 4)
        document.update(status='running', pid=os.getpid(), start_ticks=ticks(os.getpid()), started_at=time.time())
        base.save(path(task_id), document, private=True)
    log = path(task_id).with_suffix('.log')
    def terminate(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, terminate)
    import contextlib
    from ..cli import main
    try:
        with log.open('w', encoding='utf-8', buffering=1) as output:
            os.chmod(log, 0o600)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(document['arguments'])
        document.update(status='succeeded' if code == 0 else ('cancelled' if code == 130 else 'failed'), exit_code=code)
    except BaseException as exc:
        document.update(status='cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error=type(exc).__name__)
    finally:
        document['finished_at'] = time.time()
        base.save(path(task_id), document, private=True)
    return {'id': task_id, 'status': document['status']}


def cancel(task_id, dry_run=False, yes=False):
    document = get(task_id)
    early = base.prepare({'action': 'cancel-task', 'id': task_id, 'status': document['status']}, dry_run, yes)
    if early:
        return early
    with task_lock(task_id):
        document = get(task_id)
        if document['status'] == 'queued':
            document.update(status='cancelled', finished_at=time.time())
            base.save(path(task_id), document, private=True)
        elif document['status'] == 'running':
            if ticks(document['pid']) != document.get('start_ticks'):
                raise CliError('Worker identity changed; signal refused.', 'task_identity_changed', 4)
            os.kill(document['pid'], signal.SIGTERM)
    return {'id': task_id, 'cancellation_requested': True}


def delete(task_id, dry_run=False, yes=False):
    document = get(task_id)
    if document['status'] not in TERMINAL:
        raise CliError('Cancel/wait for task completion before deletion.', 'task_running', 4)
    early = base.prepare({'action': 'delete-task', 'id': task_id}, dry_run, yes)
    if early:
        return early
    path(task_id).unlink()
    path(task_id).with_suffix('.log').unlink(missing_ok=True)
    return {'deleted': task_id}


def list_tasks():
    host.require_root()
    return {'tasks': [get(p.stem) for p in sorted((base.ROOT / 'tasks').glob('*.json'))]}


def logs(task_id, lines=100):
    get(task_id)
    if not 1 <= lines <= 10000:
        raise CliError('Lines must be 1..10000.', 'invalid_lines', 2)
    from collections import deque
    logfile = path(task_id).with_suffix('.log')
    if not logfile.exists():
        return {'lines': []}
    with logfile.open(encoding='utf-8') as stream:
        return {'lines': list(deque((REDACTOR.clean(line.rstrip()) for line in stream), maxlen=lines))}
