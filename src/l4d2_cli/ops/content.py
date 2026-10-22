"""Map/Mod inventories, provenance, file ownership and portable transfers."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from .. import storage
from ..common import CliError
from . import base, missions, rooms, transport, vpk


def registry_path(room):
    key = hashlib.sha256(room['game_dir'].encode()).hexdigest()[:24]
    return base.ROOT / 'packages' / (key + '.json')


def registry(room):
    return base.read(registry_path(room), {'version': 1, 'packages': {}})


def inventory(room):
    game = rooms.game(room)
    maps = {p.stem for p in (game / 'maps').glob('*.bsp') if p.is_file() and not p.is_symlink()}
    from ..packages import installed_maps
    maps.update(installed_maps(Path(room['game_dir'])))
    packages, campaign_texts, errors = [], [], []
    records = registry(room)['packages']
    for state, directory in (('enabled', game / 'addons'), ('disabled', game / 'addons_disabled')):
        for path in sorted(directory.glob('*.vpk')):
            if vpk.is_archive_part(path):
                continue
            try:
                entries = vpk.entries(path)
                package_maps = sorted({Path(item['name']).stem for item in entries
                                       if item['name'].lower().startswith('maps/') and item['name'].lower().endswith('.bsp')})
                mission_entries = [item for item in entries if item['name'].lower().startswith('missions/') and item['name'].lower().endswith('.txt')]
                record = records.get(path.name, {})
                is_map = bool(package_maps or mission_entries or record.get('kind') == 'map')
                packages.append({**record, 'filename': path.name, 'state': state, 'bytes': path.stat().st_size,
                                 'maps': package_maps, 'kind': 'map' if is_map else 'mod',
                                 'archive_files': {entry['source'].name: record.get('archive_files', {}).get(entry['source'].name)
                                                   for entry in entries if entry['source'] != path},
                                 'missions': [item['name'] for item in mission_entries], 'path': str(path)})
                if state == 'enabled':
                    maps.update(package_maps)
                    campaign_texts.extend((vpk.text(item), Path(item['name']).stem) for item in mission_entries)
            except (CliError, ValueError, UnicodeError) as exc:
                errors.append({'filename': path.name, 'error': str(exc)})
    for path in sorted((game / 'missions').glob('*.txt')):
        if path.stat().st_size <= 1024 * 1024 and not path.is_symlink():
            campaign_texts.append((path.read_text(encoding='utf-8-sig', errors='replace'), path.stem))
    campaigns, seen = [], set()
    for text, fallback in campaign_texts:
        campaign = missions.campaign_from_mission_text(text, fallback, maps)
        if campaign and campaign['id'] not in seen:
            campaigns.append(campaign)
            seen.add(campaign['id'])
    return {'room': room['id'], 'maps': sorted(maps), 'campaigns': campaigns, 'packages': packages,
            'missing_records': [value for name, value in records.items() if name not in {p['filename'] for p in packages}],
            'errors': errors, 'affected_rooms': rooms.affected(room)}


def install_local(room, source, kind, metadata=None, final_name=None, yes=False, dry_run=False):
    source = base.regular(source)
    name = base.filename(final_name or source.name, '.vpk')
    if kind not in {'map', 'mod'}:
        raise CliError('Kind must be map or mod.', 'invalid_kind', 2)
    entries = vpk.entries(source)
    # A split index needs its archives bundled/imported together, never install a dangling index.
    split_sources = sorted({item['source'] for item in entries if item['source'] != source})
    if split_sources and name != source.name:
        raise CliError('Split VPK indexes must keep their original filename.', 'split_vpk', 2)
    has_maps = any(item['name'].lower().endswith('.bsp') for item in entries)
    if kind == 'mod' and has_maps:
        raise CliError('This VPK contains maps; import it as a map package.', 'kind_mismatch', 2)
    root = rooms.game(room)
    target = base.inside(root / 'addons', name)
    if target.exists() or (root / 'addons_disabled' / name).exists():
        raise CliError('Package already exists; remove it explicitly before reinstalling.', 'package_conflict', 4)
    early = base.prepare({'action': 'import-vpk', 'filename': name, 'kind': kind,
                          'room': room['id'], 'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    vpk.verify(source)
    base.ROOT.mkdir(parents=True, exist_ok=True)
    with base.staging('extract-') as tmp:
        stage = Path(tmp)
        extracted = vpk.extract(source, stage) if kind == 'map' else []
        uid, gid = base.account(room['user'], room.get('group'))
        with storage.operation_lock():
            base.recover()
            if target.exists() or (root / 'addons_disabled' / name).exists():
                raise CliError('Package destination changed while preparing the plan.', 'package_conflict', 4)
            data = registry(room)
            records = data['packages']
            existing = {key: digest for rec in records.values() for key, digest in rec.get('files', {}).items()}
            files, writes = {}, [(target, source, 0o644, uid, gid)]
            archive_files = {}
            for part in split_sources:
                part_target = base.inside(root / 'addons', part.name)
                if part_target.exists():
                    raise CliError('VPK archive destination already exists.', 'package_conflict', 4)
                writes.append((part_target, part, 0o644, uid, gid))
                archive_files[part.name] = storage.sha256(part)
            for relative in extracted:
                path = base.inside(root, relative)
                digest = storage.sha256(stage / relative)
                if path.exists() and storage.sha256(path) != digest:
                    raise CliError('Extracted resource conflicts with existing content: ' + relative, 'resource_conflict', 4)
                if path.exists() and relative not in existing:
                    # Do not claim ownership of official/untracked loose resources.
                    continue
                files[relative] = digest
                if not path.exists():
                    writes.append((path, stage / relative, 0o644, uid, gid))
            records[name] = {**(metadata or {}), 'filename': name, 'kind': kind, 'state': 'enabled',
                             'sha256': storage.sha256(source), 'files': files,
                             'maps': sorted({Path(i['name']).stem for i in entries if i['name'].lower().endswith('.bsp')}),
                             'archive_files': archive_files, 'installed_at': int(time.time())}
            writes.append((registry_path(room), base.json_bytes(data), 0o644, 0, 0))
            base.transaction(writes)
    return {'installed': name, 'affected_rooms': rooms.affected(room), 'restarts_server': False}


def package(room, name):
    base.filename(name, '.vpk')
    for item in inventory(room)['packages']:
        if item['filename'] == name:
            return item
    raise CliError('Installed package not found.', 'package_not_found', 4)


def import_workshop(room, source, kind, item_id, final_name=None, dry_run=False, yes=False):
    """Retain a verified source for browser/local downloads; CRC verifies payload,
    while advertised size associates a part (it is not a publisher signature)."""
    from . import catalog
    catalog.initialize()
    source = base.regular(source)
    if not catalog.WORKSHOP_ID_RE.fullmatch(str(item_id)):
        raise CliError('Invalid Workshop ID.', 'invalid_catalog_id', 2)
    details = catalog.resolve_single_workshop_download(str(item_id), kind)
    if not details.get('ok') or int(details.get('file_size') or 0) != source.stat().st_size:
        raise CliError('Local file size does not match the selected Workshop part.', 'workshop_size_mismatch', 2)
    vpk.verify(source)
    metadata = {'source': 'workshop', 'id': str(item_id), 'part_id': str(item_id),
                'install_ids': [str(item_id)], 'title': details.get('title', str(item_id))}
    return install_local(room, source, kind, metadata, final_name=final_name, dry_run=dry_run, yes=yes)


def forget(room, name, dry_run=False, yes=False):
    base.filename(name, '.vpk')
    data = registry(room)
    record = data['packages'].get(name)
    if not record or record.get('state') not in {'removed', 'superseded'}:
        raise CliError('Only removed source records can be forgotten.', 'package_installed', 4)
    early = base.prepare({'action': 'forget-package-source', 'filename': name, 'room': room['id']}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        data = registry(room)
        record = data['packages'].get(name)
        if not record or record.get('state') not in {'removed', 'superseded'}:
            raise CliError('Source record changed.', 'package_conflict', 4)
        del data['packages'][name]
        base.save(registry_path(room), data)
    return {'forgotten': name}


def state(room, name, enabled, dry_run=False, yes=False):
    item = package(room, name)
    if item['kind'] == 'map':
        return map_state(room, item, enabled, dry_run, yes)
    desired = 'enabled' if enabled else 'disabled'
    if item['state'] == desired:
        return {'unchanged': True}
    target = base.inside(rooms.game(room) / ('addons' if enabled else 'addons_disabled'), name)
    if target.exists():
        raise CliError('Destination already exists.', 'target_conflict', 4)
    early = base.prepare({'action': 'addon-state', 'filename': name, 'state': desired,
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        data = registry(room)
        if name in data['packages']:
            data['packages'][name]['state'] = desired
        writes = [(target, Path(item['path']), 0o644, uid, gid), (Path(item['path']), None, 0o644, uid, gid)]
        for archive_name in item.get('archive_files', {}):
            part_source = base.inside(Path(item['path']).parent, base.filename(archive_name, '.vpk'))
            part_target = base.inside(target.parent, archive_name)
            if part_target.exists():
                raise CliError('Split VPK state destination exists.', 'target_conflict', 4)
            writes.extend([(part_target, part_source, 0o644, uid, gid), (part_source, None, 0o644, uid, gid)])
        writes.append((registry_path(room), base.json_bytes(data), 0o644, 0, 0))
        base.transaction(writes)
    return {'filename': name, 'state': desired, 'restart_required': True}


def map_state(room, item, enabled, dry_run=False, yes=False):
    """Toggle both the VPK and its tracked loose resources in one transaction."""
    name = item['filename']
    desired = 'enabled' if enabled else 'disabled'
    if item['state'] == desired:
        return {'unchanged': True}
    if not enabled:
        ensure_map_unused(room, item['maps'])
    record = registry(room)['packages'].get(name)
    if not record:
        raise CliError('Import provenance before toggling extracted map resources.', 'untracked_package', 4)
    source = base.regular(item['path'])
    if record.get('sha256') and storage.sha256(source) != record['sha256']:
        raise CliError('Map package changed since installation.', 'resource_conflict', 4)
    target = base.inside(rooms.game(room) / ('addons' if enabled else 'addons_disabled'), name)
    if target.exists():
        raise CliError('Package state destination exists.', 'target_conflict', 4)
    early = base.prepare({'action': 'map-package-state', 'filename': name, 'state': desired,
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    vpk.verify(source)
    with base.staging('map-state-') as stage:
        extracted = vpk.extract(source, stage) if enabled else []
        with storage.operation_lock():
            data = registry(room)
            record = data['packages'].get(name)
            if not record or record.get('state') != item['state']:
                raise CliError('Map state changed while preparing.', 'package_conflict', 4)
            if not enabled:
                ensure_map_unused(room, item['maps'])
            writes = [(target, source, 0o644, uid, gid), (source, None, 0o644, uid, gid)]
            for archive_name, digest in record.get('archive_files', {}).items():
                part = base.inside(source.parent, archive_name)
                destination = base.inside(target.parent, archive_name)
                if destination.exists() or storage.sha256(base.regular(part)) != digest:
                    raise CliError('Split VPK state conflict.', 'resource_conflict', 4)
                writes += [(destination, part, 0o644, uid, gid), (part, None, 0o644, uid, gid)]
            shared = {key for other, rec in data['packages'].items() if other != name and rec.get('state') == 'enabled'
                      for key in rec.get('files', {})}
            if enabled:
                for relative in extracted:
                    resource = base.inside(rooms.game(room), relative)
                    digest = storage.sha256(stage / relative)
                    if resource.exists() and storage.sha256(resource) != digest:
                        raise CliError('Map resource conflict: ' + relative, 'resource_conflict', 4)
                    if not resource.exists():
                        writes.append((resource, stage / relative, 0o644, uid, gid))
                        record.setdefault('files', {})[relative] = digest
            else:
                for relative, digest in record.get('files', {}).items():
                    resource = base.inside(rooms.game(room), relative)
                    if relative not in shared and resource.exists():
                        if storage.sha256(resource) != digest:
                            raise CliError('Map resource changed: ' + relative, 'resource_conflict', 4)
                        writes.append((resource, None, 0o644, uid, gid))
            record['state'] = desired
            writes.append((registry_path(room), base.json_bytes(data), 0o644, 0, 0))
            base.transaction(writes)
    return {'filename': name, 'state': desired, 'restarts_server': False}


def ensure_map_unused(room, maps):
    current = {r['map'] for r in rooms.all_rooms().values() if r['game_dir'] == room['game_dir']}
    live = []
    for room_id in rooms.affected(room):
        report = rooms.status(rooms.get(room_id))
        query = report.get('query')
        if report.get('service', {}).get('MainPID') and not (query and query.get('map')):
            raise CliError('A shared room is running but its map is unknown; stop it before removal.', 'running_map_unknown', 4)
        if query:
            live.append(query['map'])
    if set(maps) & (current | set(live)):
        raise CliError('Package contains a default or currently running map.', 'map_in_use', 4)


def remove(room, name, purge=False, dry_run=False, yes=False):
    item = package(room, name)
    current = {r['map'] for r in rooms.all_rooms().values() if r['game_dir'] == room['game_dir']}
    live = []
    for room_id in rooms.affected(room):
        report = rooms.status(rooms.get(room_id))
        query = report.get('query')
        if report.get('service', {}).get('MainPID') and not (query and query.get('map')):
            raise CliError('A shared room is running but its map is unknown; stop it before removal.', 'running_map_unknown', 4)
        if query:
            live.append(query['map'])
    if set(item['maps']) & (current | set(live)):
        raise CliError('Package contains a default or currently running map.', 'map_in_use', 4)
    early = base.prepare({'action': 'remove-package', 'filename': name, 'purge_source_record': purge,
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        data = registry(room)
        record = data['packages'].get(name)
        if not record:
            raise CliError('Untracked package: import provenance before deleting extracted resources.', 'untracked_package', 4)
        shared = {key for key_name, rec in data['packages'].items() if key_name != name and rec.get('state') == 'enabled'
                  for key in rec.get('files', {})}
        writes = [(Path(item['path']), None, 0o644, -1, -1)]
        for archive_name, digest in record.get('archive_files', {}).items():
            path = base.inside(Path(item['path']).parent, base.filename(archive_name, '.vpk'))
            if storage.sha256(base.regular(path)) != digest:
                raise CliError('Split VPK archive changed.', 'resource_conflict', 4)
            writes.append((path, None, 0o644, -1, -1))
        if record.get('sha256') and storage.sha256(Path(item['path'])) != record['sha256']:
            raise CliError('Package changed since installation.', 'resource_conflict', 4)
        for relative, digest in record.get('files', {}).items():
            path = base.inside(rooms.game(room), relative)
            if relative not in shared and path.exists():
                if storage.sha256(path) != digest:
                    raise CliError('Extracted file changed; removal refused: ' + relative, 'resource_conflict', 4)
                writes.append((path, None, 0o644, -1, -1))
        if purge:
            del data['packages'][name]
        else:
            record['state'] = 'removed'
            record['files'] = {}
        writes.append((registry_path(room), base.json_bytes(data), 0o644, 0, 0))
        base.transaction(writes)
    return {'removed': name, 'source_retained': not purge}


def install(room, source, item_id, kind='map', timeout=1800, dry_run=False, yes=False, install_ids=None):
    from . import catalog
    catalog.initialize()
    if not str(item_id).isdigit() or not 1 <= len(str(item_id)) <= 20:
        raise CliError('Invalid catalog ID.', 'invalid_catalog_id', 2)
    if source == 'workshop':
        if install_ids:
            if len(install_ids) > 1000 or any(not str(i).isdigit() for i in install_ids):
                raise CliError('Invalid recorded Workshop parts.', 'invalid_manifest', 2)
            resolved = {'ok': True, 'parts': [catalog.resolve_single_workshop_download(str(i), kind) for i in install_ids]}
        else:
            resolved = catalog.workshop_expected_parts(str(item_id), kind)
        if not resolved.get('ok') or any(not p.get('ok') for p in resolved.get('parts', [])):
            raise CliError('Workshop item or required parts cannot be resolved.', 'catalog_unavailable', 4)
        parts = resolved['parts']
    elif source == 'gamemaps' and kind == 'map':
        import re
        page = transport.fetch_bytes('https://www.gamemaps.com/details/' + str(item_id)).decode('utf-8', errors='replace')
        links = re.findall(r'(?:href|src)=["\']([^"\']*(?:download|\.zip|\.vpk)[^"\']*)', page, re.I)
        urls = [__import__('urllib.parse', fromlist=['urljoin']).urljoin('https://www.gamemaps.com', link) for link in links]
        urls = [url for url in urls if '.zip' in url or '.vpk' in url or '/download/' in url]
        if not urls:
            urls = ['https://www.gamemaps.com/download/' + str(item_id)]
        parts = [{'url': urls[0], 'workshop_id': str(item_id), 'title': 'GameMaps ' + str(item_id), 'file_size': 0}]
    else:
        raise CliError('Unsupported catalog source/kind.', 'invalid_source', 2)
    early = base.prepare({'action': 'install-content', 'source': source, 'id': str(item_id), 'kind': kind,
                          'parts': [p.get('workshop_id') for p in parts], 'affected_rooms': rooms.affected(room),
                          'restarts_server': False}, dry_run, yes)
    if early:
        return early
    base.ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    deadline = time.monotonic() + timeout
    with base.staging('download-') as tmp:
        for index, part in enumerate(parts):
            url = part.get('file_url') or part.get('url') or part.get('download_url')
            if not url:
                raise CliError('Source has no downloadable URL.', 'catalog_unavailable', 4)
            path = Path(tmp) / ('part-%d.vpk' % index)
            transport.download(url, path, max(1, deadline - time.monotonic()), int(part.get('file_size') or 0))
            metadata = {'source': source, 'id': str(item_id), 'install_ids': [str(p.get('workshop_id')) for p in parts],
                        'part_id': str(part.get('workshop_id')), 'title': part.get('title', str(item_id))}
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    extracted = safe_unpack_zip(archive, Path(tmp) / ('zip-%d' % index))
                vpks = [p for p in extracted if p.suffix.lower() == '.vpk']
                if not vpks:
                    raise CliError('Downloaded ZIP has no VPK.', 'invalid_archive', 2)
            else:
                vpks = [path]
            for part_number, local in enumerate(vpks):
                name = '%s_%s_%s_%d.vpk' % (kind, source, part.get('workshop_id'), part_number)
                record = registry(room)['packages'].get(name)
                existing = rooms.game(room) / ('addons_disabled' if record and record.get('state') == 'disabled' else 'addons') / name
                if record and existing.is_file():
                    if storage.sha256(existing) != storage.sha256(local):
                        raise CliError('Installed package differs from current source; remove it explicitly before reinstalling.', 'package_conflict', 4)
                    for relative, digest in record.get('files', {}).items():
                        resource = base.inside(rooms.game(room), relative)
                        if not resource.is_file() or storage.sha256(resource) != digest:
                            raise CliError('Installed extracted resources changed; inspect before retrying.', 'resource_conflict', 4)
                    results.append({'installed': name, 'unchanged': True})
                else:
                    results.append(install_local(room, local, kind, metadata, name, yes=True))
    with storage.operation_lock():
        data = registry(room)
        names = [value['installed'] for value in results]
        for name, rec in data['packages'].items():
            if rec.get('state') == 'removed' and rec.get('source') == source and str(rec.get('id')) == str(item_id) and name not in names:
                rec.update(state='superseded', replaced_by=names)
        base.save(registry_path(room), data)
    return {'installed': results, 'restarts_server': False}


def safe_unpack_zip(archive, destination, limit=32 * 1024**3):
    infos = archive.infolist()
    if len(infos) > 500000 or sum(i.file_size for i in infos) > limit:
        raise CliError('Archive exceeds resource limits.', 'invalid_archive', 2)
    destination.mkdir(parents=True, exist_ok=True)
    if sum(i.file_size for i in infos) > shutil.disk_usage(destination).free:
        raise CliError('Insufficient archive space.', 'insufficient_space', 4)
    planned = []
    seen = set()
    for info in infos:
        path = base.inside(destination, info.filename)
        if (info.external_attr >> 16) & 0o170000 == 0o120000 or info.flag_bits & 1:
            raise CliError('Links and encrypted ZIP entries are unsupported.', 'unsafe_archive', 4)
        if not info.is_dir():
            key = str(path).casefold()
            if key in seen:
                raise CliError('Duplicate archive destination.', 'unsafe_archive', 4)
            seen.add(key)
            planned.append((info, path))
    for info, path in planned:
        path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as incoming, path.open('xb') as output:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
    return [p for _, p in planned]


def export_manifest(room, output, names=None):
    records = inventory(room)['packages'] + inventory(room)['missing_records']
    selected = [r for r in records if not names or r['filename'] in names]
    items = [{key: record[key] for key in ('filename', 'kind', 'source', 'id', 'install_ids', 'state', 'sha256') if key in record}
             for record in selected]
    path = Path(output).absolute()
    if path.exists():
        raise CliError('Output already exists.', 'output_exists', 4)
    base.replace_file(path, base.json_bytes({'format': 'l4d2-content-manifest', 'version': 1, 'items': items}), 0o600)
    return {'output': str(path), 'items': len(items)}


def import_manifest(room, source, dry_run=False, yes=False):
    document = json.loads(base.regular(source).read_text(encoding='utf-8'))
    if document.get('format') not in {'l4d2-content-manifest', 'l4d2-manager-web-manifest'} or document.get('version') != 1:
        raise CliError('Unsupported content manifest.', 'invalid_manifest', 2)
    items = document.get('items', [])
    if not isinstance(items, list) or len(items) > 10000:
        raise CliError('Invalid manifest items.', 'invalid_manifest', 2)
    for item in items:
        base.filename(item['filename'], '.vpk')
        if item.get('source') not in {'workshop', 'gamemaps'} or not str(item.get('id', '')).isdigit():
            raise CliError('Manifest needs supported source IDs.', 'invalid_manifest', 2)
        if item.get('kind') not in {'map', 'mod'} or any(not str(x).isdigit() for x in item.get('install_ids', [])):
            raise CliError('Invalid manifest kind/parts.', 'invalid_manifest', 2)
    early = base.prepare({'action': 'import-manifest', 'room': room['id'], 'items': len(items), 'downloads': False}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        data = registry(room)
        for item in items:
            if item['filename'] not in data['packages']:
                data['packages'][item['filename']] = {**item, 'state': 'removed', 'desired_state': item.get('state', 'enabled'), 'files': {}}
        base.save(registry_path(room), data)
    return {'imported': len(items), 'downloads': False}


def import_legacy_registry(room, source, dry_run=False, yes=False):
    """Import the old panel's provenance; claim only verifiable custom maps/missions."""
    document = base.read(base.regular(source))
    records = document.get('packages', {})
    if not isinstance(records, dict):
        raise CliError('Invalid legacy package registry.', 'invalid_manifest', 2)
    planned = {}
    installed = {item['filename']: item for item in inventory(room)['packages']}
    from ..model import OFFICIAL_MAPS
    import io
    for name, record in records.items():
        base.filename(name, '.vpk')
        item_id = str(record.get('catalog_id') or record.get('id') or '')
        source_name = record.get('source', '')
        if source_name not in {'workshop', 'gamemaps'} or not item_id.isdigit():
            continue
        item = installed.get(name)
        files = {}
        if item:
            for entry in vpk.entries(item['path']):
                relative = entry['name']
                if not relative.startswith(('maps/', 'missions/')) or Path(relative).stem in OFFICIAL_MAPS:
                    continue
                target = base.inside(rooms.game(room), relative)
                if target.exists():
                    class Digest:
                        def __init__(self):
                            self.value = hashlib.sha256()
                        def write(self, block):
                            self.value.update(block)
                    data = Digest()
                    if entry['size'] > 512 * 1024**2:
                        continue
                    vpk.copy_entry(entry, data)
                    if data.value.hexdigest() == storage.sha256(target):
                        files[relative] = storage.sha256(target)
        planned[name] = {'filename': name, 'source': source_name, 'id': item_id,
                         'kind': record.get('kind') if record.get('kind') in {'map', 'mod'} else ('map' if item and item['maps'] else 'mod'),
                         'install_ids': [str(i) for i in record.get('install_ids', [item_id]) if str(i).isdigit()],
                         'state': item['state'] if item else 'removed', 'files': files,
                         'sha256': storage.sha256(Path(item['path'])) if item else None,
                         'title': record.get('title', name)}
    early = base.prepare({'action': 'import-legacy-registry', 'records': len(planned),
                          'claims_only_verified_maps_and_missions': True, 'downloads': False}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        data = registry(room)
        for name, record in planned.items():
            if name not in data['packages']:
                data['packages'][name] = record
        base.save(registry_path(room), data)
    return {'imported_records': len(planned), 'downloads': False,
            'untracked_materials_preserved': True}


def reinstall(room, name, dry_run=False, yes=False, timeout=1800):
    record = registry(room)['packages'].get(name)
    if not record or not record.get('source') or not record.get('id'):
        raise CliError('Package has no reinstall source.', 'source_missing', 4)
    if record.get('state') != 'removed':
        raise CliError('Remove the installed package before reinstalling.', 'package_conflict', 4)
    result = install(room, record['source'], record['id'], record['kind'], timeout, dry_run, yes,
                     install_ids=record.get('install_ids') if record['source'] == 'workshop' else None)
    if not dry_run and record['kind'] == 'mod' and record.get('desired_state') == 'disabled':
        for item in result['installed']:
            state(room, item['installed'], False, yes=True)
    return result


def export_zip(room, output, names=None):
    selected = [r for r in inventory(room)['packages'] if not names or r['filename'] in names]
    path = Path(output).absolute()
    storage.reject_links(path)
    if path.exists():
        raise CliError('Output already exists.', 'output_exists', 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        import os
        os.chmod(path, 0o600)
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            items = []
            for item in selected:
                vpk.verify(item['path'])
                archive.write(item['path'], 'packages/' + item['filename'])
                for archive_name in item.get('archive_files', {}):
                    archive.write(base.inside(Path(item['path']).parent, archive_name), 'packages/' + archive_name)
                items.append({key: value for key, value in item.items() if key not in {'path', 'files'}})
            archive.writestr('manifest.json', json.dumps({'format': 'l4d2-content-zip', 'version': 1, 'items': items}))
    return {'output': str(path), 'packages': len(selected)}


def import_zip(room, source, dry_run=False, yes=False):
    source = base.regular(source)
    early = base.prepare({'action': 'import-content-zip', 'room': room['id'], 'source': str(source),
                          'affected_rooms': rooms.affected(room), 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    base.ROOT.mkdir(parents=True, exist_ok=True)
    with base.staging('zip-') as tmp:
        with zipfile.ZipFile(source) as archive:
            safe_unpack_zip(archive, Path(tmp))
        manifest = json.loads((Path(tmp) / 'manifest.json').read_text())
        if manifest.get('format') != 'l4d2-content-zip' or manifest.get('version') != 1:
            raise CliError('Unsupported transfer ZIP.', 'invalid_archive', 2)
        planned = []
        for item in manifest['items']:
            name = base.filename(item['filename'], '.vpk')
            local = base.inside(Path(tmp) / 'packages', name)
            if item.get('sha256') and storage.sha256(local) != item['sha256']:
                raise CliError('Transfer checksum mismatch.', 'checksum_mismatch', 4)
            vpk.verify(local)
            planned.append((item, local))
        results = []
        for item, local in planned:
            results.append(install_local(room, local, item['kind'], item, yes=True))
            if item.get('state') == 'disabled' and item['kind'] == 'mod':
                state(room, item['filename'], False, yes=True)
    return {'imported': results}
