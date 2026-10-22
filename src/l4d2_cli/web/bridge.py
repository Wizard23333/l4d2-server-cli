"""Root-side adapters. Every operation runs the public CLI contract, no shell."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from ..common import CliError, REDACTOR
from ..ops import base
from . import manager, schema


def run_cli(arguments):
    from ..cli import main
    from ..ops.tasks import SERVICE_WORKERS
    output, errors = io.StringIO(), io.StringIO()
    token = SERVICE_WORKERS.set(True)
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(arguments)
    finally:
        SERVICE_WORKERS.reset(token)
    lines = output.getvalue().splitlines()
    result = None
    for line in reversed(lines):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and 'ok' in candidate and 'data' in candidate:
                result = candidate
                break
        except ValueError:
            continue
    if result is None:
        if code:
            raise CliError(REDACTOR.clean(errors.getvalue()[-4000:] or 'CLI operation failed.'), 'cli_failed', code)
        return {'lines': REDACTOR.clean(output.getvalue()).splitlines()}
    if code or not result['ok']:
        error = result.get('error') or {}
        raise CliError(error.get('message', 'CLI operation failed.'), error.get('code', 'cli_failed'), code or 4, result.get('data', {}))
    if arguments[:2] in (['room', 'logs'], ['server', 'logs']):
        return {'lines': [line for line in lines if not line.startswith('{"schema_version"')]}
    return result['data']


def cli(*args):
    return run_cli([str(v) for v in args] + ['--json'])


def room_id(value=None):
    return base.identifier(value or manager.configuration().get('room', 'main'))


def package_record(item):
    item = dict(item)
    ids = sorted(set(item.get('install_ids') or []))
    group = hashlib.sha256('|'.join(ids).encode()).hexdigest()[:12] if len(ids) > 1 else ''
    return {**item, 'state': 'remote' if item.get('state') in {'removed', 'superseded'} else item.get('state'),
            'maps': item.get('maps', []), 'missions': item.get('missions', []), 'catalog_id': item.get('id', ''), 'size': item.get('bytes', 0),
            'modified_at': item.get('installed_at', 0), 'package_status': 'removed' if item.get('state') == 'removed' else 'installed',
            'reinstallable': bool(item.get('source') and item.get('id')), 'url': item.get('url') or (
                'https://steamcommunity.com/sharedfiles/filedetails/?id=' + str(item.get('id')) if item.get('source') == 'workshop' else ''),
            'title': item.get('title') or item['filename'], 'group_id': group,
            'group_title': re.sub(r'\s*\(?\d+\s*(?:of|/)\s*\d+\)?\s*$', '', item.get('title', '')), 'group_members': ids}


def job_record(item):
    args = item.get('arguments', [])
    progress = item.get('progress') or {}
    download = progress.get('download') or {}
    record = {**item, 'type': 'export' if args[:2] == ['transfer', 'export'] else ' '.join(args[:2]),
              'title': ' '.join(args[:4]), 'stage': progress.get('phase', item['status']),
              'progress': 100 if item['status'] == 'succeeded' else download.get('percent', 0),
              'downloaded_bytes': download.get('bytes', 0), 'total_bytes': download.get('total', 0),
              'message': item.get('error', '')}
    if args[:2] == ['transfer', 'export'] and '--output' in args:
        path = Path(args[args.index('--output') + 1])
        if path.is_file() and item['status'] == 'succeeded':
            record.update(download_url='/api/export/download?job_id=' + item['id'], export_filename=path.name)
    if item['status'] == 'failed':
        try:
            logs = cli('task', 'logs', item['id'], '--lines', 5)
            record['message'] = '\n'.join(logs.get('lines', []))[-2000:]
        except CliError:
            pass
    return record


def campaigns_for(contents):
    campaigns = list(contents.get('campaigns', []))
    covered = {m['name'] for c in campaigns for m in c['maps']}
    others = [m for m in contents.get('maps', []) if m not in covered]
    if others:
        campaigns.append({'id': 'other', 'title': 'Other installed maps', 'source': 'other',
                          'maps': [{'name': name, 'display_name': name, 'chapter': i + 1} for i, name in enumerate(others)]})
    return campaigns


def snapshot(selected=None):
    registered = cli('room', 'list')['rooms']
    selected = room_id(selected)
    if selected not in {r['id'] for r in registered}:
        if not registered:
            raise CliError('No room is registered.', 'room_not_found', 4)
        selected = next((r['id'] for r in registered if r['id'] == manager.configuration().get('room')), registered[0]['id'])
    reports = []
    for room in registered:
        report = cli('room', 'status', '--room', room['id'])
        service = report.get('service') or {}
        reports.append({'id': room['id'], 'label': room.get('name', room['id']), 'service': room['service'],
                        'port': room['port'], 'active': service.get('ActiveState', 'unknown'),
                        'sub_state': service.get('SubState', ''), 'restarts': str(service.get('NRestarts', 0)),
                        'exit_status': str(service.get('ExecMainStatus', 0)), 'started_at': service.get('ExecMainStartTimestamp', ''),
                        'default_map': room['map'], 'default_campaign_id': '', 'port_listening': bool(report.get('game_ready')),
                        'query': report.get('query'), 'configuration_pending': report.get('configuration_pending')})
    contents = cli('map', 'list', '--room', selected)
    campaigns = campaigns_for(contents)
    selected_profile = next(r for r in registered if r['id'] == selected)
    by_directory = {selected_profile['game_dir']: campaigns}
    for room in reports:
        profile = next(r for r in registered if r['id'] == room['id'])
        if profile['game_dir'] not in by_directory:
            by_directory[profile['game_dir']] = campaigns_for(cli('map', 'list', '--room', room['id']))
        room['campaigns'] = by_directory[profile['game_dir']]
        room['default_campaign_id'] = next((c['id'] for c in room['campaigns'] if room['default_map'] in {m['name'] for m in c['maps']}), '')
    stack = cli('plugin', 'list', '--room', selected)
    from .metrics import snapshot as system_snapshot
    return {'generated_at': time.time(), 'selected_room': selected, 'rooms': reports,
            'maps': contents['maps'], 'campaigns': campaigns,
            'addons': [package_record(p) for p in contents['packages']] + [package_record({**p, 'state': 'removed'})
                       for p in contents['missing_records'] if p.get('state') != 'superseded'],
            'jobs': [job_record(t) for t in cli('task', 'list')['tasks']], 'content_errors': contents.get('errors', []),
            'server_plugins': {'metamod': stack['core_installed'], 'sourcemod': stack['core_installed'], 'affected_rooms': stack['affected_rooms'],
                               'plugins': [{**p, 'id': p['filename'], 'label': p['filename'], 'size': p['bytes']} for p in stack['plugins']]},
            'system': system_snapshot(registered)}


def token_for(command, values, selected, expires):
    raw = json.dumps([command, values, selected, expires], sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(manager.credentials()['session_secret'].encode(), raw, hashlib.sha256).hexdigest()


def operation(request):
    command = request.get('command')
    values = request.get('values', {})
    selected = room_id(request.get('room'))
    if isinstance(command, str) and command.startswith('server ') and selected != 'main':
        raise CliError('Server commands target main. Select main or use the room commands.', 'invalid_room_target', 2)
    preview = request.get('preview', True)
    if type(preview) is not bool:
        raise CliError('Invalid preview flag.', 'invalid_arguments', 2)
    values = prepare_files(values)
    argv, spec = schema.arguments(command, values, selected, preview)
    if spec['mutating'] and not preview:
        expires = request.get('expires', 0)
        if not isinstance(expires, int) or not time.time() < expires <= time.time() + 301 or not hmac.compare_digest(
                str(request.get('confirmation', '')), token_for(command, values, selected, expires)):
            raise CliError('Preview the current operation before confirming it.', 'confirmation_required', 5)
    result = cli('task', 'submit', '--detach-service', '--yes', '--', *argv) if spec['mutating'] and not preview and request.get('background') else run_cli(argv)
    if preview and spec['mutating']:
        expires = int(time.time()) + 300
        return {'ok': True, 'data': result, 'expires': expires,
                'confirmation': token_for(command, values, selected, expires)}
    response = {'ok': True, 'data': result}
    if command in {'backup create', 'transfer export', 'plugin compile'} and result.get('output') and Path(result['output']).is_file():
        published = publish(result['output'])
        response['download_url'] = '/api/files/download?file=' + published['download']
    return response


def prepare_files(values):
    if not isinstance(values, dict):
        raise CliError('Fields must be an object.', 'invalid_arguments', 2)
    result = dict(values)
    for key in schema.FILE_FIELDS:
        if key in result and result[key]:
            name = str(result[key])
            if not re.fullmatch(r'[0-9a-f]{32}\.[a-z0-9]{1,8}', name):
                raise CliError('Select an uploaded file for file fields.', 'invalid_upload', 2)
            source = base.inside(manager.STATE / 'uploads', name)
            base.regular(source)
            result[key] = str(source)
    # Outputs/extraction stay within a private CLI-controlled workspace.
    for key in ('output',):
        if key in result and result[key]:
            name = base.filename(str(result[key]))
            folder = manager.STATE / 'exports'
            folder.mkdir(parents=True, exist_ok=True, mode=0o750)
            result[key] = str(folder / name)
    return result


def submit(argv):
    result = cli('task', 'submit', '--detach-service', '--yes', '--', *argv)
    return {'ok': True, 'job': job_record(cli('task', 'show', result['task_id'])), 'message': 'Task submitted'}


def upload_path(fields):
    name = str(fields.get('upload_id', ''))
    if not re.fullmatch(r'[0-9a-f]{32}\.[a-z0-9]{1,8}', name):
        raise CliError('Invalid staged upload.', 'invalid_upload', 2)
    return base.regular(base.inside(manager.STATE / 'uploads', name))


def export_path(suffix):
    folder = manager.STATE / 'exports'
    folder.mkdir(parents=True, exist_ok=True, mode=0o750)
    return folder / (uuid.uuid4().hex + suffix)


def publish(path):
    import grp
    path = base.regular(path)
    target = manager.STATE / 'downloads' / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o750)
    os.chown(target.parent, 0, grp.getgrnam(manager.USER).gr_gid)
    base.replace_file(target, path, 0o640, 0, grp.getgrnam(manager.USER).gr_gid)
    return {'download': target.name}


def http(request):
    path = request.get('path', '')
    fields = request.get('fields', {})
    if not isinstance(fields, dict):
        raise CliError('Invalid request fields.', 'invalid_arguments', 2)
    selected = room_id(fields.get('room') or request.get('room'))
    kind = fields.get('kind', 'map')
    if kind not in {'map', 'mod'}:
        raise CliError('Kind must be map or mod.', 'invalid_kind', 2)
    group = 'addon' if kind == 'mod' else 'map'
    filename = fields.get('filename', '')
    if path == '/api/state':
        return snapshot(selected)
    if path == '/api/commands':
        return {'commands': schema.commands()}
    if path == '/api/operation':
        return operation(request)
    if path == '/api/catalog/search':
        return cli(group, 'search', fields.get('query', ''))
    if path in {'/api/workshop/resolve', '/api/workshop/parts'}:
        resolved = cli(group, 'resolve', fields.get('workshop_id', ''), '--download-links')
        if path.endswith('/resolve'):
            parts = resolved.get('parts', [])
            return {**resolved, 'items': parts, 'collection': len(parts) != 1, **(parts[0] if len(parts) == 1 else {})}
        return resolved
    if path == '/api/restart':
        cli('room', 'restart', '--room', selected, '--yes')
    elif path == '/api/default-map':
        cli('map', 'default', fields.get('map', ''), '--room', selected, '--yes')
        if fields.get('restart') == '1':
            cli('room', 'restart', '--room', selected, '--yes')
    elif path in {'/api/workshop/install', '/api/catalog/install'}:
        source = fields.get('source', 'workshop')
        item_id = fields.get('id') or fields.get('workshop_id', '')
        if source not in {'workshop', 'gamemaps'} or not re.fullmatch(r'[0-9]{1,20}', str(item_id)):
            raise CliError('Invalid catalog source or ID.', 'invalid_catalog_id', 2)
        if source == 'gamemaps' and group == 'addon':
            raise CliError('GameMaps supports maps only.', 'invalid_source', 2)
        return submit([group, 'install', item_id, '--source', source, '--room', selected])
    elif path == '/api/addon/state':
        state = fields.get('state')
        if state not in {'enabled', 'disabled'}:
            raise CliError('Invalid addon state.', 'invalid_state', 2)
        item = cli('map', 'info', filename, '--room', selected)
        cli('map' if item['kind'] == 'map' else 'addon', 'enable' if state == 'enabled' else 'disable', filename, '--room', selected, '--yes')
    elif path == '/api/map-package/delete':
        mode = fields.get('mode')
        if mode not in {'soft', 'purge'}:
            raise CliError('Invalid delete mode.', 'invalid_arguments', 2)
        try:
            cli('map', 'remove', filename, '--room', selected, *(['--purge'] if mode == 'purge' else []), '--yes')
        except CliError as exc:
            if mode != 'purge' or exc.code != 'package_not_found':
                raise
            cli('map', 'forget', filename, '--room', selected, '--yes')
    elif path == '/api/map-package/reinstall':
        base.filename(filename, '.vpk')
        return submit(['map', 'reinstall', filename, '--room', selected])
    elif path == '/api/manifest/remove-record':
        cli('map', 'forget', filename, '--room', selected, '--yes')
    elif path == '/api/manifest/install':
        base.filename(filename, '.vpk')
        return submit(['transfer', 'install', '--room', selected, '--filename', filename])
    elif path == '/api/map-package/regroup':
        return {'ok': True, 'message': 'Groups refreshed from CLI source records'}
    elif path in {'/api/job/cancel', '/api/job/delete', '/api/job/rerun'}:
        action = {'cancel': 'cancel', 'delete': 'delete', 'rerun': 'retry'}[path.rsplit('/', 1)[-1]]
        result = cli('task', action, fields.get('job_id', ''), '--yes')
        return {'ok': True, **result}
    elif path == '/api/server-plugins/install':
        return submit(['plugin', 'stack', '--component', 'all', '--room', selected])
    elif path == '/api/server-plugins/state':
        state = fields.get('state')
        if state not in {'enabled', 'disabled'}:
            raise CliError('Invalid plugin state.', 'invalid_state', 2)
        cli('plugin', 'enable' if state == 'enabled' else 'disable', fields.get('plugin', ''), '--room', selected, '--yes')
    elif path == '/api/server-plugins/restart':
        target = fields.get('target')
        registered = cli('room', 'list')['rooms']
        origin = next((r for r in registered if r['id'] == selected), None)
        shared = [r['id'] for r in registered if origin and r['game_dir'] == origin['game_dir']]
        targets = shared if target == 'all' else [room_id(target)]
        if any(target not in shared for target in targets):
            raise CliError('Select a room sharing this plugin directory.', 'invalid_room_target', 2)
        for target in targets:
            cli('room', 'restart', '--room', target, '--yes')
    elif path in {'/api/map-package/export-job', '/api/manifest/export'}:
        names = filename if isinstance(filename, list) else [filename]
        if not names or not all(names):
            raise CliError('Select packages to export.', 'invalid_arguments', 2)
        if path.endswith('/manifest/export'):
            inventory = cli('map', 'list', '--room', selected)
            records = {p['filename']: p for p in inventory['packages'] + inventory['missing_records']}
            if any(name not in records or records[name].get('source') not in {'workshop', 'gamemaps'}
                   or not str(records[name].get('id', '')).isdigit() for name in names):
                raise CliError('Source manifests require remote IDs. Export local-only packages as ZIP.', 'manifest_source_required', 2)
        output = export_path('.json' if path.endswith('/export') else '.zip')
        argv = ['transfer', 'export', '--room', selected, '--format', 'manifest' if output.suffix == '.json' else 'zip', '--output', str(output)]
        for name in names:
            argv += ['--filename', base.filename(name, '.vpk')]
        if output.suffix == '.zip':
            return submit(argv)
        run_cli(argv + ['--yes', '--json'])
        return publish(output)
    elif path == '/api/export/download':
        task = cli('task', 'show', fields.get('job_id', ''))
        args = task.get('arguments', [])
        if task['status'] != 'succeeded' or args[:2] != ['transfer', 'export'] or '--output' not in args:
            raise CliError('Export is not ready.', 'export_not_ready', 4)
        output = Path(args[args.index('--output') + 1])
        if output.parent != manager.STATE / 'exports':
            raise CliError('Not a web export.', 'invalid_export', 4)
        return publish(output)
    elif path in {'/api/upload', '/api/upload/workshop', '/api/manifest/import'}:
        source = upload_path(fields)
        original = base.filename(fields.get('original', source.name))
        if path == '/api/manifest/import':
            return submit(['transfer', 'import', '--format', 'manifest', '--file', str(source), '--room', selected])
        if original.lower().endswith('.zip'):
            return submit(['transfer', 'import', '--format', 'zip', '--file', str(source), '--room', selected])
        if not original.lower().endswith('.vpk'):
            raise CliError('Upload a VPK or transfer ZIP.', 'invalid_upload', 2)
        argv = [group, 'import', '--room', selected, '--file', str(source), '--name', original]
        if path == '/api/upload/workshop':
            resolved = cli(group, 'resolve', fields.get('workshop_id', ''))
            matches = [p for p in resolved.get('parts', []) if p.get('ok') and int(p.get('file_size') or 0) == source.stat().st_size
                       and (not fields.get('expected_id') or p.get('workshop_id') == fields['expected_id'])]
            if len(matches) != 1:
                raise CliError('Upload does not uniquely match the selected Workshop part size.', 'workshop_size_mismatch', 2)
            argv += ['--workshop-id', str(matches[0]['workshop_id'])]
        return submit(argv)
    else:
        raise CliError('Unknown panel operation.', 'invalid_operation', 2)
    return {'ok': True, 'message': 'Operation completed'}


def handle(request):
    operation_name = request.get('operation')
    if operation_name == 'snapshot':
        return snapshot(request.get('room'))
    if operation_name == 'http':
        return http(request)
    raise CliError('Unknown bridge operation.', 'invalid_operation', 2)
