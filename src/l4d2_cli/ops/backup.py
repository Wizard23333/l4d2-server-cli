"""Private state snapshots with checksummed, allowlisted restore destinations."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .. import host, storage
from ..common import CliError, run
from . import base, content, rooms

NETWORK = {'hosts': Path('/etc/hosts'), 'gai': Path('/etc/gai.conf'),
           'cli-network': Path('/etc/l4d2-cli/network.json'),
           'panel-env': Path('/etc/l4d2-manager-web.env'),
           'panel-packages': Path('/var/lib/l4d2-manager-web/packages.json'),
           'cli-web-config': Path('/etc/l4d2-cli/web.json'),
           'cli-web-secrets': Path('/etc/l4d2-cli/web-secrets.json')}
PLUGIN_ROOTS = ('addons/metamod', 'addons/sourcemod', 'cfg/sourcemod')


def files_under(root):
    if not root.exists():
        return []
    storage.reject_links(root)
    if root.is_file():
        return [root]
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            storage.reject_links(Path(directory) / name)
        result.extend(Path(directory) / name for name in files)
    return result


def create(room, output=None, scope='full', dry_run=False, yes=False):
    if scope not in {'full', 'plugins'}:
        raise CliError('Invalid backup scope.', 'invalid_scope', 2)
    target = Path(output).absolute() if output else base.ROOT / 'snapshots' / (uuid.uuid4().hex + '.zip')
    storage.reject_links(target)
    if target.exists():
        raise CliError('Backup output already exists.', 'output_exists', 4)
    early = base.prepare({'action': 'backup', 'scope': scope, 'room': room['id'], 'output': str(target),
                          'contains_secrets': True, 'maps': 'source-manifest-only'}, dry_run, yes)
    if early:
        return early
    game = rooms.game(room)
    selected = {}
    for relative in (*PLUGIN_ROOTS, 'addons/metamod.vdf', 'addons/metamod_x64.vdf'):
        for path in files_under(base.inside(game, relative)):
            selected['game/' + path.relative_to(game).as_posix()] = path
    profiles = {}
    if scope == 'full':
        for path in files_under(game / 'cfg'):
            selected['game/' + path.relative_to(game).as_posix()] = path
        for room_id in rooms.affected(room):
            profile = rooms.get(room_id)
            profiles[room_id] = {key: value for key, value in profile.items() if key != 'hashes'}
            selected['game/' + Path(profile['cfg']).relative_to(game).as_posix()] = base.regular(profile['cfg'])
            if profile.get('script'):
                selected['scripts/' + Path(profile['script']).name] = base.regular(profile['script'])
            selected['units/' + profile['service']] = base.regular(profile['unit'])
        for path in files_under(game / 'ems'):
            selected['game/' + path.relative_to(game).as_posix()] = path
        for item in content.inventory(room)['packages']:
            if item['kind'] == 'mod':
                path = base.regular(item['path'])
                selected['game/' + path.relative_to(game).as_posix()] = path
                for archive_name in item.get('archive_files', {}):
                    part = base.inside(path.parent, base.filename(archive_name, '.vpk'))
                    selected['game/' + part.relative_to(game).as_posix()] = base.regular(part)
        for key, path in NETWORK.items():
            if path.exists() and key in {'cli-network', 'panel-env', 'panel-packages', 'cli-web-config', 'cli-web-secrets'}:
                selected['optional/' + key] = base.regular(path)
    sources = content.registry(room)
    manifest = {'format': 'l4d2-state-backup', 'version': 1, 'scope': scope, 'primary_room': room['id'],
                'game_dir': room['game_dir'], 'rooms': profiles, 'packages': sources, 'files': {}}
    for name, path in selected.items():
        metadata = path.stat()
        manifest['files'][name] = {'sha256': storage.sha256(path), 'mode': metadata.st_mode & 0o777,
                                   'size': metadata.st_size}
    manifest['network'] = {}
    for key in ('hosts', 'gai'):
        path = NETWORK[key]
        if path.exists():
            text = path.read_text()
            if key == 'hosts':
                import re
                blocks = re.findall(r'(?ms)^# L4D2-STEAM-CDN-BEGIN\s*$.*?^# L4D2-STEAM-CDN-END\s*$', text)
                manifest['network'][key] = '\n'.join(blocks)
            else:
                manifest['network'][key] = '\n'.join(line for line in text.splitlines()
                                                    if line.strip() == 'precedence ::ffff:0:0/96 100')
    size = sum(path.stat().st_size for path in selected.values())
    target.parent.mkdir(parents=True, exist_ok=True)
    if size > shutil.disk_usage(target.parent).free:
        raise CliError('Insufficient backup space.', 'insufficient_space', 4)
    with storage.operation_lock():
        base.recover()
        # Check again while locked; do not claim a coherent snapshot if files changed during enumeration.
        for name, path in selected.items():
            if storage.sha256(path) != manifest['files'][name]['sha256']:
                raise CliError('State changed while preparing the snapshot; retry.', 'snapshot_conflict', 4)
        temporary = target.with_name('.' + target.name + '.partial')
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, 0o600)
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
                    for name, path in selected.items():
                        archive.write(path, name)
                    archive.writestr('manifest.json', json.dumps(manifest))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {'output': str(target), 'sha256': storage.sha256(target), 'files': len(selected),
            'contains_secrets': True, 'scope': scope}


def inspect(source, expected=None):
    source = base.regular(source)
    if expected and storage.sha256(source) != expected.lower():
        raise CliError('Backup checksum mismatch.', 'checksum_mismatch', 4)
    with zipfile.ZipFile(source) as archive:
        info = archive.getinfo('manifest.json')
        if info.file_size > 4 * 1024**2:
            raise CliError('Backup manifest too large.', 'invalid_backup', 2)
        document = json.loads(archive.read(info))
        if document.get('format') != 'l4d2-state-backup' or document.get('version') != 1:
            raise CliError('Unsupported state backup.', 'invalid_backup', 2)
        if document.get('scope') not in {'full', 'plugins'}:
            raise CliError('Unknown state backup scope.', 'invalid_backup', 2)
        if not isinstance(document.get('files'), dict) or len(document['files']) > 100000:
            raise CliError('Invalid backup file inventory.', 'invalid_backup', 2)
        for record in document['files'].values():
            if not isinstance(record, dict) or type(record.get('mode')) is not int or not 0 <= record['mode'] <= 0o777:
                raise CliError('Invalid backup file permissions.', 'invalid_backup', 2)
        return document


def restore(room, source, expected=None, network=False, panel=False, dry_run=False, yes=False):
    source = base.regular(source)
    document = inspect(source, expected)
    affected = rooms.affected(room)
    for room_id in affected:
        report = rooms.status(rooms.get(room_id))
        if report['service'].get('MainPID') or report['service'].get('ActiveState') not in {'inactive', 'failed'}:
            raise CliError('Stop every room sharing this installation before restore.', 'room_running', 4)
    if document['scope'] == 'full':
        desired = {}
        for old_id, profile in document.get('rooms', {}).items():
            mapped = room['id'] if old_id == document.get('primary_room', 'main') else old_id
            if mapped in desired:
                raise CliError('Backup room mapping is ambiguous.', 'room_conflict', 4)
            desired[mapped] = profile['port']
        retained = {key: value['port'] for key, value in rooms.all_rooms().items() if key not in desired}
        values = [*desired.values(), *retained.values()]
        if len(values) != len(set(values)):
            raise CliError('Restored ports conflict with another registered room.', 'port_conflict', 4)
        for port in desired.values():
            host.check_udp_available(room['bind'], port)
    early = base.prepare({'action': 'restore', 'room': room['id'], 'scope': document['scope'],
                          'affected_rooms': affected, 'network': network, 'panel_state': panel,
                          'restarts_server': False, 'files': len(document['files'])}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    base.ROOT.mkdir(parents=True, exist_ok=True)
    with base.staging('restore-') as tmp:
        stage = Path(tmp)
        with zipfile.ZipFile(source) as archive:
            content.safe_unpack_zip(archive, stage)
        for name, record in document['files'].items():
            path = base.inside(stage, name)
            if storage.sha256(base.regular(path)) != record['sha256']:
                raise CliError('Backup member checksum mismatch.', 'checksum_mismatch', 4)
        writes = []
        game = rooms.game(room)
        full = document['scope'] == 'full'
        permitted = ('addons/', 'addons_disabled/', 'cfg/', 'ems/') if full else ('addons/metamod/', 'addons/sourcemod/', 'cfg/sourcemod/', 'addons/metamod.vdf', 'addons/metamod_x64.vdf')
        for name, record in document['files'].items():
            if name.startswith('game/'):
                relative = name[5:]
                if not relative.startswith(permitted):
                    raise CliError('Unregistered backup resource.', 'unsafe_backup', 4)
                # Main CLI configuration is regenerated through its transaction system below.
                if room['kind'] == 'main' and relative == 'cfg/l4d2-cli.cfg':
                    continue
                writes.append((base.inside(game, relative), stage / name, record['mode'], uid, gid))
            elif name.startswith('scripts/') and full:
                script_name = base.filename(name[8:])
                declared = set()
                for profile in document.get('rooms', {}).values():
                    if profile.get('script'):
                        original = Path(profile['script'])
                        if Path(document['game_dir']) not in original.parents:
                            raise CliError('Backup script is outside its registered game directory.', 'unsafe_backup', 4)
                        declared.add(original.name)
                if script_name not in declared or not script_name.endswith('.sh'):
                    raise CliError('Unregistered backup script.', 'unsafe_backup', 4)
                writes.append((base.inside(Path(room['game_dir']), script_name), stage / name, record['mode'], uid, gid))
            elif name.startswith('optional/'):
                key = name[9:]
                if key not in {'cli-network', 'panel-env', 'panel-packages', 'cli-web-config', 'cli-web-secrets'}:
                    raise CliError('Unregistered optional state.', 'unsafe_backup', 4)
                if (key == 'cli-network' and network) or (key != 'cli-network' and panel):
                    gid, mode = 0, 0o644 if key == 'panel-packages' else 0o600
                    if key.startswith('cli-web-'):
                        import grp
                        try:
                            gid = grp.getgrnam('l4d2web').gr_gid
                        except KeyError:
                            raise CliError('Install the optional web service before restoring its settings.', 'web_not_installed', 4) from None
                        mode = 0o640
                    writes.append((NETWORK[key], stage / name, mode, 0, gid))
            elif not name.startswith('units/'):
                raise CliError('Unregistered backup member.', 'unsafe_backup', 4)
        # Remove old plugin files absent from snapshot, preserving its enabled/disabled states exactly.
        saved_game = {name[5:] for name in document['files'] if name.startswith('game/')}
        for relative in PLUGIN_ROOTS:
            for path in files_under(base.inside(game, relative)):
                if path.relative_to(game).as_posix() not in saved_game:
                    writes.append((path, None, 0o644, uid, gid))
        data = rooms.registry()
        if full:
            mapped_ids = set()
            for old_id, profile in document.get('rooms', {}).items():
                base.identifier(old_id)
                primary = old_id == document.get('primary_room', 'main')
                if primary and room['kind'] == 'main':
                    continue
                restored_id = room['id'] if primary else old_id
                if restored_id in mapped_ids:
                    raise CliError('Primary room remapping collides with another backup room.', 'room_conflict', 4)
                mapped_ids.add(restored_id)
                if restored_id == 'main':
                    raise CliError('Restore this snapshot through its original primary main room.', 'room_conflict', 4)
                existing = rooms.all_rooms().get(restored_id)
                if existing and existing['game_dir'] != room['game_dir']:
                    raise CliError('Backup room ID conflicts with another installation.', 'room_conflict', 4)
                cfg_name = Path(profile['cfg']).name
                base.filename(cfg_name, '.cfg')
                restored = {key: value for key, value in profile.items() if key not in {'hashes', 'script'}}
                restored.update(id=restored_id, game_dir=room['game_dir'], user=room['user'], group=room['group'],
                                bind=room['bind'],
                                kind='created', service='l4d2-' + restored_id + '.service',
                                unit=str(base.UNITS / ('l4d2-' + restored_id + '.service')),
                                cfg=str(game / 'cfg' / cfg_name), autostart=False)
                if existing:
                    restored.update(service=existing['service'], unit=existing['unit'])
                rooms.validate(restored)
                unit = rooms.unit_text(restored)
                restored['hashes'] = {}
                data['rooms'][restored_id] = restored
                writes.append((Path(restored['unit']), unit, 0o644, 0, 0))
            writes.append((base.ROOMS, base.json_bytes(data), 0o600, 0, 0))
            package_data = document.get('packages', {'version': 1, 'packages': {}})
            for rec in package_data['packages'].values():
                if rec.get('kind') == 'map':
                    rec.update(state='removed', files={})
            writes.append((content.registry_path(room), base.json_bytes(package_data), 0o644, 0, 0))
        if network:
            from .maintenance import network_writes
            writes.extend(network_writes(document.get('network', {})))
        with storage.operation_lock():
            base.transaction(writes)
            # Rebuild hashes using restored files; no sensitive contents enter the registry.
            for profile in data['rooms'].values():
                if profile['game_dir'] == room['game_dir']:
                    profile['hashes'] = {profile[key]: storage.sha256(Path(profile[key])) for key in ('cfg', 'unit')
                                         if Path(profile[key]).is_file()}
            if full:
                base.save(base.ROOMS, data, private=True)
            if full:
                run(['systemctl', 'daemon-reload'])
                for restored_id in mapped_ids:
                    run(['systemctl', 'disable', data['rooms'][restored_id]['service']])
        primary_profile = document.get('rooms', {}).get(document.get('primary_room', 'main'))
        if full and room['kind'] == 'main' and primary_profile:
            from .. import manager
            import dataclasses
            config, _ = manager.installed()
            profile = primary_profile
            fields = {field.name for field in dataclasses.fields(config)} - {'game_dir', 'schema_version', 'revision'}
            settings = {key: profile[key] for key in fields if key in profile}
            settings['autostart'] = False
            settings['bind'] = room['bind']
            # Keep a valid official fallback until ID-only maps have been reinstalled.
            from .content import inventory
            if settings.get('map') not in inventory(room)['maps']:
                settings['map'] = config.map
            private = storage.load_secrets()
            cfg_file = stage / 'game/cfg' / Path(profile['cfg']).name
            if cfg_file.exists():
                values = rooms.cfg_values(cfg_file.read_text())
                if 'rcon_password' in values:
                    private['rcon_password'] = values['rcon_password']
            manager.configure(dataclasses.replace(config, **settings), private, dry_run=False, yes=True)
    return {'restored': True, 'scope': document['scope'], 'restarts_server': False,
            'maps_require_reinstall': full, 'rooms_require_explicit_start': True,
            'saved_defaults': {(room['id'] if key == document.get('primary_room', 'main') else key):
                               {'map': value['map'], 'autostart': value.get('autostart', False)}
                               for key, value in document.get('rooms', {}).items()}}
