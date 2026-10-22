"""Exercise a deployed CLI-backed panel; mutation tests require an isolated room.

Run on the Linux server as an administrator. Credentials and test outputs stay
in the supplied private work directory. Browser rendering is verified separately.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import secrets
import struct
import time
import urllib.parse
import uuid
import zlib


def vpk(files):
    groups, tree, payload = {}, bytearray(), bytearray()
    for name, data in files.items():
        path = Path(name)
        groups.setdefault(path.suffix[1:], {}).setdefault(path.parent.as_posix(), []).append((path.stem, data))
    for extension, folders in groups.items():
        tree.extend(extension.encode() + b'\0')
        for folder, entries in folders.items():
            tree.extend(folder.encode() + b'\0')
            for name, data in entries:
                tree.extend(name.encode() + b'\0')
                tree.extend(struct.pack('<IHHIIH', zlib.crc32(data), 0, 0x7fff, len(payload), len(data), 0xffff))
                payload.extend(data)
            tree.extend(b'\0')
        tree.extend(b'\0')
    tree.extend(b'\0')
    return struct.pack('<III', 0x55aa1234, 1, len(tree)) + tree + payload


class Client:
    def __init__(self, url, username, password):
        self.url = urllib.parse.urlsplit(url)
        if self.url.scheme != 'http' or self.url.hostname not in {'127.0.0.1', 'localhost'}:
            raise ValueError('Run acceptance against a loopback HTTP endpoint on the server.')
        self.cookies = {}
        code, _, _ = self.request('/api/login', {'username': username, 'password': password}, auth=False)
        if code != 303:
            raise AssertionError('Login failed: ' + str(code))

    def request(self, path, fields=None, *, json_body=None, raw=None, content_type=None, auth=True, expected=None, extra=None):
        headers = dict(extra or {})
        if auth:
            headers['Cookie'] = '; '.join(k + '=' + v for k, v in self.cookies.items())
            headers['X-CSRF-Token'] = self.cookies.get('l4d2_csrf', '')
        method = 'GET'
        body = None
        if fields is not None:
            body = urllib.parse.urlencode(fields, doseq=True).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            method = 'POST'
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers['Content-Type'] = 'application/json'
            method = 'POST'
        if raw is not None:
            body = raw
            headers['Content-Type'] = content_type
            method = 'POST'
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=180)
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        for key, value in response.getheaders():
            if key.lower() == 'set-cookie':
                cookie = value.split(';', 1)[0]
                name, value = cookie.split('=', 1)
                self.cookies[name] = value
        code = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        try:
            decoded = json.loads(data)
        except ValueError:
            decoded = data
        if expected is not None and code != expected:
            raise AssertionError('Unexpected HTTP %s for %s: %s' % (code, path, str(decoded)[:1500]))
        return code, decoded, response_headers

    def get(self, path):
        return self.request(path, expected=200)[1]

    def post(self, path, values):
        return self.request(path, values, expected=200)[1]

    def operation(self, command, values, room, mutate=False, background=False):
        request = {'command': command, 'values': values, 'room': room, 'preview': True}
        result = self.request('/api/operation', json_body=request, expected=200)[1]
        if mutate:
            request.update(preview=False, background=background, confirmation=result['confirmation'], expires=result['expires'])
            result = self.request('/api/operation', json_body=request, expected=200)[1]
        return result

    def upload(self, filename, data, path='/api/files', fields=None):
        boundary = 'l4d2-acceptance-' + uuid.uuid4().hex
        parts = []
        for key, value in (fields or {}).items():
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, key, value)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode() + data + b'\r\n')
        parts.append(('--%s--\r\n' % boundary).encode())
        return self.request(path, raw=b''.join(parts), content_type='multipart/form-data; boundary=' + boundary, expected=200)[1]

    def task(self, task_id, room, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            document = self.operation('task show', {'id': task_id}, room)['data']
            if document['status'] not in {'queued', 'running'}:
                if document['status'] != 'succeeded':
                    log = self.operation('task logs', {'id': task_id}, room)['data']
                    raise AssertionError('Task ' + document['status'] + ': ' + str(log)[-2000:])
                return document
            time.sleep(0.5)
        raise AssertionError('Task did not finish before acceptance deadline.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--username', default='admin')
    parser.add_argument('--password-file', required=True, type=Path)
    parser.add_argument('--room', required=True)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--mutations', action='store_true', help='Explicitly authorize disposable fixture changes in the selected isolated room')
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    password = args.password_file.read_text().strip()
    client = Client(args.url, args.username, password)
    results = []
    def case(name, function):
        started = time.monotonic()
        try:
            function()
            results.append({'case': name, 'status': 'pass', 'seconds': round(time.monotonic() - started, 2)})
            print('PASS', name, flush=True)
        except Exception as exc:
            message = str(exc).replace(password, 'REDACTED')
            results.append({'case': name, 'status': 'fail', 'error': message})
            print('FAIL', name, message[:1500], flush=True)
            raise
        finally:
            (args.work_dir / 'web-acceptance.json').write_text(json.dumps({'cases': results}, indent=2))
    room = args.room
    case('authentication_required', lambda: client.request('/api/state', auth=False, expected=401))
    case('health', lambda: client.get('/healthz'))
    case('dashboard_state', lambda: client.get('/api/state'))
    case('static_frontend', lambda: client.get('/'))
    case('operation_schema', lambda: client.get('/api/commands'))
    case('cross_origin_rejected', lambda: client.request('/api/restart', {}, extra={'Origin': 'https://invalid.example'}, expected=403))
    case('invalid_workshop_id', lambda: client.request('/api/workshop/install', {'workshop_id': '../invalid', 'room': room}, expected=400))
    case('invalid_addon_path', lambda: client.request('/api/addon/state', {'filename': '../invalid.vpk', 'state': 'disabled', 'room': room}, expected=400))
    for command in ['room status', 'map list', 'addon list', 'plugin list', 'storage usage', 'network status', 'task list', 'room doctor', 'room logs']:
        case(command, lambda command=command: client.operation(command, {}, room))
    case('catalog_search', lambda: client.get('/api/catalog/search?query=Run%20to%20the%20Hills&kind=map'))
    if not args.mutations:
        return 0
    state = client.get('/api/state')
    if not any(r['id'] == room for r in state['rooms']):
        raise AssertionError('Selected test room is not registered.')
    tag = uuid.uuid4().hex[:8]
    map_name, mod_name = 'webmap_' + tag + '.vpk', 'webmod_' + tag + '.vpk'
    map_id = 'web_fixture_' + tag
    map_bytes = vpk({'maps/' + map_id + '.bsp': b'acceptance fixture; not a playable map',
                     'missions/web_' + tag + '.txt': ('"mission" { "DisplayTitle" "Web Acceptance" "modes" { "coop" { "1" { "Map" "' + map_id + '" } } } }').encode()})
    mod_bytes = vpk({'scripts/web_' + tag + '.nut': b'// disposable web acceptance fixture'})
    def upload_task(name, payload, kind):
        result = client.upload(name, payload, '/api/upload', {'room': room, 'kind': kind})
        client.task(result['job']['id'], room)
    case('map_upload_and_task', lambda: upload_task(map_name, map_bytes, 'map'))
    case('map_disable', lambda: client.post('/api/addon/state', {'room': room, 'filename': map_name, 'state': 'disabled'}))
    case('map_enable', lambda: client.post('/api/addon/state', {'room': room, 'filename': map_name, 'state': 'enabled'}))
    case('mod_upload_and_task', lambda: upload_task(mod_name, mod_bytes, 'mod'))
    for state in ['disabled', 'enabled']:
        case('mod_' + state, lambda state=state: client.post('/api/addon/state', {'room': room, 'filename': mod_name, 'state': state}))
    case('default_map_save', lambda: client.post('/api/default-map', {'room': room, 'map': map_id, 'restart': '0'}))
    case('protect_default_map', lambda: client.request('/api/map-package/delete', {'room': room, 'filename': map_name, 'mode': 'soft'}, expected=409))
    case('restore_official_default', lambda: client.post('/api/default-map', {'room': room, 'map': 'c1m1_hotel', 'restart': '0'}))
    for backend in ['sourcemod', 'left4bots', 'admin-system']:
        values = {'steam_id': 'STEAM_1:0:12345678', 'backend': backend}
        case('admin_add_' + backend, lambda values=values: client.operation('admin add', values, room, True))
        case('admin_list_' + backend, lambda backend=backend: client.operation('admin list', {'backend': backend}, room))
        case('admin_remove_' + backend, lambda values=values: client.operation('admin remove', values, room, True))
    export_job = {}
    def export_zip():
        export_job.update(client.post('/api/map-package/export-job', {'room': room, 'filename': [map_name, mod_name]}))
        client.task(export_job['job']['id'], room)
    case('background_zip_export', export_zip)
    exported = client.get('/api/export/download?job_id=' + export_job['job']['id'])
    case('export_zip_bytes', lambda: exported.startswith(b'PK') or (_ for _ in ()).throw(AssertionError('Not a ZIP')))
    case('remove_map', lambda: client.post('/api/map-package/delete', {'room': room, 'filename': map_name, 'mode': 'purge'}))
    case('remove_mod', lambda: client.operation('addon remove', {'filename': mod_name, 'purge': True}, room, True))
    case('zip_import_roundtrip', lambda: upload_task('web-transfer.zip', exported, 'map'))
    case('regroup_sources', lambda: client.post('/api/map-package/regroup', {'room': room}))
    case('task_logs', lambda: client.operation('task logs', {'id': export_job['job']['id']}, room))
    case('delete_completed_task', lambda: client.post('/api/job/delete', {'job_id': export_job['job']['id']}))
    case('cleanup_map_fixture', lambda: client.post('/api/map-package/delete', {'room': room, 'filename': map_name, 'mode': 'purge'}))
    case('cleanup_mod_fixture', lambda: client.operation('addon remove', {'filename': mod_name, 'purge': True}, room, True))
    case('logout', lambda: client.post('/api/logout', {}))
    case('logged_out_access_denied', lambda: client.request('/api/state', expected=401))
    print(json.dumps({'passed': len(results), 'report': str(args.work_dir / 'web-acceptance.json')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
