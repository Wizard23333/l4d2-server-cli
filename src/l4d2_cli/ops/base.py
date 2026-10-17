"""Durable operations state, validated paths and recoverable file transactions."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
import contextlib
from pathlib import Path, PurePosixPath

from .. import host, storage
from ..common import CliError, confirm

ROOT = Path('/var/lib/l4d2-cli/operations')
ROOMS = Path('/etc/l4d2-cli/rooms.json')
UNITS = Path('/etc/systemd/system')
ID = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
MAP = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}\Z')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise CliError('Use a lowercase identifier (1..48 characters).', 'invalid_identifier', 2)
    return value


def filename(value, suffix=None):
    if (not isinstance(value, str) or not value or len(value) > 180 or
            value in {'.', '..'} or any(c in value for c in '/\\\x00\n\r') or
            not re.fullmatch(r'[A-Za-z0-9_. -]+', value) or (suffix and not value.endswith(suffix))):
        raise CliError('Invalid resource filename.', 'invalid_filename', 2)
    return value


def inside(root, relative):
    root = Path(root)
    relative = PurePosixPath(str(relative).replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or ':' in str(relative):
        raise CliError('Unsafe relative resource path.', 'unsafe_path', 4)
    target = root.joinpath(*relative.parts)
    storage.reject_links(target)
    return target


def regular(path):
    path = Path(path).absolute()
    storage.reject_links(path)
    if not path.is_file():
        raise CliError('Input must be an existing regular file.', 'invalid_source', 2)
    return path


@contextlib.contextmanager
def parent_fd(path, create=False):
    """Pin each Linux directory component without following links, including during races."""
    path = Path(os.path.abspath(path))
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:-1]:
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def input_stream(path):
    path = regular(path)
    if os.name != 'posix':
        with path.open('rb') as stream:
            yield stream
        return
    with parent_fd(path) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CliError('Refusing a nonregular input.', 'unsafe_path', 4)
            yield stream


def text(path, limit=4 * 1024**2, encoding='utf-8-sig'):
    with input_stream(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CliError('Text input exceeds size limit.', 'input_too_large', 2)
    return data.decode(encoding)


def unlink(path):
    path = Path(path)
    storage.reject_links(path)
    if os.name != 'posix':
        path.unlink(missing_ok=True)
        return
    try:
        with parent_fd(path) as parent:
            try:
                os.unlink(path.name, dir_fd=parent)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def read(path, default=None):
    path = Path(path)
    storage.reject_links(path)
    if not path.exists():
        return {} if default is None else default.copy()
    if path.stat().st_size > 64 * 1024**2:
        raise CliError('Operations state exceeds size limit.', 'invalid_state', 4)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise CliError('Invalid operations state JSON.', 'invalid_state', 4) from None


def save(path, value, private=False):
    storage.save_json(Path(path), value, private=private)


def prepare(plan, dry_run=False, yes=False, as_json=False):
    host.require_platform()
    if dry_run:
        return {'dry_run': True, 'plan': plan}
    host.require_root()
    confirm(json.dumps(plan, ensure_ascii=False), yes, as_json=as_json)
    return None


def replace_file(target, source, mode=0o644, uid=-1, gid=-1):
    """Stream large files; never follow a destination symlink."""
    target = Path(target)
    storage.reject_links(target)
    if os.name == 'posix':
        temporary = '.l4d2-ops-' + uuid.uuid4().hex
        with parent_fd(target, create=True) as parent:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    os.fchmod(output.fileno(), mode)
                    if uid >= 0 or gid >= 0:
                        os.fchown(output.fileno(), uid, gid)
                    if isinstance(source, bytes):
                        output.write(source)
                    else:
                        with input_stream(source) as incoming:
                            shutil.copyfileobj(incoming, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / ('.l4d2-ops-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as output:
            os.chmod(temporary, mode)
            if isinstance(source, bytes):
                output.write(source)
            else:
                with regular(source).open('rb') as incoming:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if hasattr(os, 'chown') and (uid >= 0 or gid >= 0):
            os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def recover():
    """Called under the CLI's cross-process operation lock before every mutation."""
    journal = ROOT / 'transaction.json'
    pending = read(journal)
    if not pending:
        return False
    folder = inside(ROOT / 'transactions', filename(pending['id']))
    entries = pending.get('entries', [])
    for item in reversed(entries):
        target = Path(item['target'])
        storage.reject_links(target)
        if item['existed']:
            source = inside(folder, item['backup'])
            replace_file(target, source, item['mode'], item['uid'], item['gid'])
        else:
            unlink(target)
    journal.unlink()
    shutil.rmtree(folder)
    return True


def transaction(writes):
    """writes: (target, bytes/path/None, mode, uid, gid). Caller holds lock."""
    recover()
    task_id = uuid.uuid4().hex
    folder = ROOT / 'transactions' / task_id
    folder.mkdir(parents=True, mode=0o700)
    os.chmod(folder, 0o700)
    entries = []
    try:
        required = sum(Path(target).stat().st_size if Path(target).is_file() else 0 for target, *_ in writes)
        required += sum(len(data) if isinstance(data, bytes) else (Path(data).stat().st_size if data is not None and Path(data).is_file() else 0)
                        for _, data, *_ in writes)
        if required > shutil.disk_usage(ROOT).free:
            raise CliError('Insufficient space for staged writes and rollback copies.', 'insufficient_space', 4)
        for index, (target, data, mode, uid, gid) in enumerate(writes):
            target = Path(target)
            storage.reject_links(target)
            if target.exists() and not target.is_file():
                raise CliError('Transaction target is not a regular file.', 'unsafe_path', 4)
            metadata = target.stat() if target.exists() else None
            backup = str(index)
            if metadata:
                replace_file(folder / backup, target, 0o600)
            entries.append({'target': str(target), 'backup': backup, 'existed': metadata is not None,
                            'mode': metadata.st_mode & 0o777 if metadata else mode,
                            'uid': metadata.st_uid if metadata else uid, 'gid': metadata.st_gid if metadata else gid})
        save(ROOT / 'transaction.json', {'id': task_id, 'entries': entries}, private=True)
    except BaseException:
        (ROOT / 'transaction.json').unlink(missing_ok=True)
        shutil.rmtree(folder)
        raise
    try:
        for target, data, mode, uid, gid in writes:
            if data is None:
                unlink(target)
            else:
                replace_file(target, data, mode, uid, gid)
    except BaseException:
        recover()
        raise
    (ROOT / 'transaction.json').unlink()
    shutil.rmtree(folder)


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False).encode('utf-8')


def account(user, group=None):
    import pwd
    import grp
    item = pwd.getpwnam(user)
    gid = grp.getgrnam(group).gr_gid if group else item.pw_gid
    if item.pw_uid == 0 or gid == 0:
        raise CliError('Game account must be unprivileged.', 'unsafe_game_user', 4)
    return item.pw_uid, gid


def process_ticks(pid):
    try:
        text = Path('/proc/%d/stat' % pid).read_text()
        return text[text.rfind(')') + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


@contextlib.contextmanager
def staging(prefix):
    import tempfile
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=ROOT) as folder:
        path = Path(folder)
        save(path / '.l4d2-owner.json', {'pid': os.getpid(), 'ticks': process_ticks(os.getpid())}, private=True)
        yield path
