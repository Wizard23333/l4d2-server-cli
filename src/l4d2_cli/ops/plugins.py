"""SourceMod stack, plugin compilation, protected states and administrator files."""
from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .. import storage
from ..common import CliError, run
from . import base, content, rooms, transport

PROTECTED = {'left4dhooks.smx'}
UNSAFE_DEFAULTS = {'mapchooser.smx', 'nominations.smx', 'rockthevote.smx', 'nextmap.smx', 'randomcycle.smx',
                   'advertisements.smx', 'l4d_kill.smx', 'sql-admin-manager.smx',
                   'admin-sql-prefetch.smx', 'admin-sql-threaded.smx'}


def sources():
    return {'sources': [{'id': p.stem, 'path': str(p)} for p in sorted((Path(__file__).parent / 'assets').glob('*.sp'))]}


def directory(room):
    return rooms.game(room) / 'addons/sourcemod'


def inventory(room):
    root = directory(room) / 'plugins'
    items = []
    for state, folder in (('enabled', root), ('disabled', root / 'disabled')):
        for path in sorted(folder.glob('*.smx')):
            base.regular(path)
            items.append({'filename': path.name, 'state': state, 'protected': path.name in PROTECTED,
                          'sha256': storage.sha256(path), 'bytes': path.stat().st_size})
    return {'plugins': items, 'core_installed': directory(room).is_dir(), 'affected_rooms': rooms.affected(room)}


def set_state(room, name, enabled, dry_run=False, yes=False):
    base.filename(name, '.smx')
    if name in PROTECTED and not enabled:
        raise CliError('Core dependency cannot be disabled.', 'protected_plugin', 4)
    root = directory(room) / 'plugins'
    source = base.inside(root / ('disabled' if enabled else ''), name)
    target = base.inside(root / ('' if enabled else 'disabled'), name)
    if target.exists() and not source.exists():
        return {'unchanged': True}
    base.regular(source)
    if target.exists():
        raise CliError('Plugin exists in both states.', 'plugin_conflict', 4)
    early = base.prepare({'action': 'plugin-state', 'plugin': name, 'enabled': enabled,
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        base.transaction([(target, source, 0o644, uid, gid), (source, None, 0o644, uid, gid)])
    return {'plugin': name, 'enabled': enabled, 'restart_required': True}


def install(room, source, name=None, dry_run=False, yes=False):
    source = base.regular(source)
    name = base.filename(name or source.name, '.smx')
    with source.open('rb') as stream:
        signature = stream.read(4)
    if signature != b'FFPS':
        raise CliError('Not a SourcePawn SMX binary.', 'invalid_plugin', 2)
    early = base.prepare({'action': 'install-plugin', 'plugin': name, 'sha256': storage.sha256(source),
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    from .backup import create
    snapshot = create(room, scope='plugins', yes=True)
    target = base.inside(directory(room) / 'plugins', name)
    uid, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        base.transaction([(target, source, 0o644, uid, gid)])
    return {'installed': name, 'backup': snapshot['output'], 'restart_required': True}


def compile_source(room, source, name=None, deploy=False, dry_run=False, yes=False):
    source = base.regular(source)
    if source.suffix != '.sp' or source.stat().st_size > 4 * 1024**2:
        raise CliError('Source must be a bounded .sp file.', 'invalid_source', 2)
    name = base.filename(name or source.stem + '.smx', '.smx')
    compiler = directory(room) / 'scripting/spcomp64'
    if not compiler.is_file():
        compiler = directory(room) / 'scripting/spcomp'
    base.regular(compiler)
    early = base.prepare({'action': 'compile-plugin', 'source': str(source), 'plugin': name, 'deploy': deploy,
                          'affected_rooms': rooms.affected(room)}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    base.ROOT.mkdir(parents=True, exist_ok=True)
    with base.staging('compile-') as tmp:
        stage = Path(tmp)
        os.chmod(stage, 0o700)
        os.chown(stage, uid, gid)
        compiler.chmod(compiler.stat().st_mode | 0o111)
        base.replace_file(stage / 'source.sp', source, 0o600, uid, gid)
        output = stage / name
        response = run(['runuser', '-u', room['user'], '--', str(compiler), str(stage / 'source.sp'),
                        '-i', str(directory(room) / 'scripting/include'), '-o', str(output)],
                       cwd=directory(room) / 'scripting', timeout=120)
        if not output.is_file():
            raise CliError('Compiler produced no SMX.', 'compile_failed', 4)
        if deploy:
            report = install(room, output, name, yes=True)
            with storage.operation_lock():
                base.transaction([(directory(room) / 'scripting' / (Path(name).stem + '.sp'), source, 0o644, uid, gid)])
        else:
            target = base.ROOT / 'compiled' / name
            base.replace_file(target, output, 0o600)
            report = {'output': str(target)}
    return {**report, 'compiler_output': response.stdout}


def unpack_tar(source, destination):
    with tarfile.open(base.regular(source)) as archive:
        items = archive.getmembers()
        if len(items) > 100000 or sum(i.size for i in items) > 2 * 1024**3:
            raise CliError('Plugin archive exceeds limits.', 'invalid_archive', 2)
        for item in items:
            target = base.inside(destination, item.name)
            if not (item.isdir() or item.isfile()):
                raise CliError('Plugin archive links/devices are forbidden.', 'unsafe_archive', 4)
            if item.isfile():
                if PurePosixPath(item.name).parts[0] not in {'addons', 'cfg'}:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as incoming, target.open('wb') as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)


def stack(room, metamod=None, sourcemod=None, component='core', dry_run=False, yes=False, timeout=1800):
    if component == 'all':
        early = base.prepare({'action': 'install-plugin-stack', 'components': ['core', 'left4dhooks'],
                              'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
        if early:
            return early
        return {'components': [stack(room, metamod, sourcemod, 'core', yes=True, timeout=timeout),
                               stack(room, component='left4dhooks', yes=True, timeout=timeout)], 'restarts_server': False}
    if component not in {'core', 'left4dhooks'}:
        raise CliError('Unknown plugin stack component.', 'invalid_component', 2)
    early = base.prepare({'action': 'install-plugin-stack', 'component': component,
                          'affected_rooms': rooms.affected(room), 'enables_incompatible_votes': False,
                          'restarts_server': False}, dry_run, yes)
    if early:
        return early
    from .backup import create
    snapshot = create(room, scope='plugins', yes=True)
    uid, gid = base.account(room['user'], room.get('group'))
    base.ROOT.mkdir(parents=True, exist_ok=True)
    with base.staging('stack-') as tmp:
        stage = Path(tmp)
        extracted = stage / 'extracted'
        extracted.mkdir()
        if component == 'core':
            for key, local, prefix in (('metamod', metamod, 'https://mms.alliedmods.net/mmsdrop/1.12/'),
                                        ('sourcemod', sourcemod, 'https://sm.alliedmods.net/smdrop/1.12/')):
                if local:
                    archive = base.regular(local)
                else:
                    listing = 'mmsource-latest-linux' if key == 'metamod' else 'sourcemod-latest-linux'
                    latest = transport.fetch_bytes(prefix + listing, limit=512).decode().strip()
                    base.filename(latest)
                    archive = transport.download(prefix + latest, stage / (key + '.tar.gz'), timeout)
                unpack_tar(archive, extracted)
            # This server runtime is 32 bit; x64 loaders must not be activated.
            (extracted / 'addons/metamod_x64.vdf').unlink(missing_ok=True)
            (extracted / 'addons/metamod.vdf').parent.mkdir(parents=True, exist_ok=True)
            (extracted / 'addons/metamod.vdf').write_text('"Plugin"\n{\n"file" "../left4dead2/addons/metamod/bin/server"\n}\n')
        else:
            archive = transport.download('https://codeload.github.com/SilvDev/Left4DHooks/zip/refs/heads/main', stage / 'hooks.zip', timeout)
            with zipfile.ZipFile(archive) as zipped:
                paths = content.safe_unpack_zip(zipped, stage / 'hooks')
            for path in paths:
                parts = path.relative_to(stage / 'hooks').parts
                if 'addons' in parts:
                    relative = Path(*parts[parts.index('addons'):])
                elif len(parts) >= 3 and parts[1] == 'sourcemod':
                    relative = Path('addons', *parts[1:])
                else:
                    continue
                destination = base.inside(extracted, relative.as_posix())
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
            required = ['plugins/left4dhooks.smx', 'gamedata/left4dhooks.l4d2.txt',
                        'scripting/include/left4dhooks.inc']
            if any(not (extracted / 'addons/sourcemod' / name).is_file() for name in required):
                raise CliError('Left4DHooks archive is missing required runtime files.', 'invalid_archive', 4)
        writes = []
        game = rooms.game(room)
        for path in extracted.rglob('*'):
            if not path.is_file():
                continue
            relative = path.relative_to(extracted)
            target = base.inside(game, relative.as_posix())
            if path.name in UNSAFE_DEFAULTS and 'plugins' in relative.parts:
                target = game / 'addons/sourcemod/plugins/disabled' / path.name
            if path.suffix == '.smx' and any((directory(room) / 'plugins' / folder / path.name).exists()
                                           for folder in ('', 'disabled')):
                continue
            # Keep deployed configs and custom plugin states on framework upgrades.
            if target.exists() and (relative.parts[0] == 'cfg' or 'configs' in relative.parts or path.suffix == '.smx'):
                continue
            writes.append((target, path, 0o755 if path.name in {'spcomp', 'spcomp64'} else 0o644, uid, gid))
        if component == 'core':
            writes.append((game / 'addons/metamod_x64.vdf', None, 0o644, uid, gid))
        with storage.operation_lock():
            base.transaction(writes)
    return {'component': component, 'files': len(writes), 'backup': snapshot['output'], 'restart_required': True}


def admin_path(room, backend):
    paths = {'sourcemod': 'addons/sourcemod/configs/admins_simple.ini',
             'left4bots': 'ems/left4bots/cfg/admins.txt', 'admin-system': 'ems/admin system/admins.txt'}
    if backend not in paths:
        raise CliError('Unknown admin backend.', 'invalid_backend', 2)
    return base.inside(rooms.game(room), paths[backend])


def admins(room, backend):
    path = admin_path(room, backend)
    text = base.text(path) if path.exists() else ''
    return {'backend': backend, 'entries': [line for line in text.splitlines()
                                           if line.strip() and not line.strip().startswith(('//', ';'))]}


def set_admin(room, backend, steam_id, flags='99:z', remove=False, dry_run=False, yes=False):
    if not re.fullmatch(r'STEAM_[01]:[01]:[0-9]{1,12}', steam_id):
        raise CliError('Use a Steam2 ID.', 'invalid_steam_id', 2)
    if not re.fullmatch(r'(?:[0-9]{1,3}:)?[a-z]+', flags):
        raise CliError('Invalid SourceMod flags.', 'invalid_admin_flags', 2)
    path = admin_path(room, backend)
    early = base.prepare({'action': 'remove-admin' if remove else 'set-admin', 'backend': backend,
                          'steam_id': steam_id, 'affected_rooms': rooms.affected(room)}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        text = base.text(path) if path.exists() else ''
        identity = re.compile(r'(?<![A-Za-z0-9_:])' + re.escape(steam_id) + r'(?![A-Za-z0-9_:])')
        lines = [line for line in text.splitlines() if not identity.search(line.split('//')[0])]
        if not remove:
            lines.append('"%s" "%s"' % (steam_id, flags) if backend == 'sourcemod' else steam_id)
        base.transaction([(path, ('\n'.join(lines) + '\n').encode(), 0o640, uid, gid)])
    return {'updated': True, 'restart_or_admin_reload_required': True}
