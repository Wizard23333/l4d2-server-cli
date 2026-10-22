"""Optional panel contract, HTTP/session boundary and real CLI filesystem flows."""
import contextlib
import http.client
import io
import json
import os
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from l4d2_cli import cli
from l4d2_cli.common import CliError
from l4d2_cli.web import bridge, manager, schema, server
from test_operations import Sandbox


@unittest.skipUnless(sys.platform == 'linux', 'Linux listener and TIME_WAIT binding semantics')
class PortAvailabilityTests(unittest.TestCase):
    def test_active_listener_remains_protected(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaises(CliError) as caught:
                manager.check_port_available({'host': '0.0.0.0', 'port': listener.getsockname()[1]})
        self.assertEqual(caught.exception.code, 'port_in_use')

    def test_closed_connection_does_not_block_panel_cutover(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            address = listener.getsockname()
            listener.listen()
            with socket.create_connection(address, timeout=3) as client:
                peer, _ = listener.accept()
                with peer:
                    peer.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b'')
        # The accepted connection leaves server-side TCP state behind.
        with socket.socket() as plain_probe, self.assertRaises(OSError):
            plain_probe.bind(address)
        manager.check_port_available({'host': '0.0.0.0', 'port': address[1]})


class ContractTests(unittest.TestCase):
    def test_exact_sudo_bridge_argv_always_returns_the_json_envelope(self):
        output = io.StringIO()
        with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(b'{"operation":"snapshot"}'))), \
             patch('l4d2_cli.host.require_root'), patch.object(bridge, 'handle', return_value={'rooms': []}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['web', '_bridge']), 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_schema_covers_registered_operations_and_excludes_internal_workers(self):
        specs = {s['id']: s for s in schema.commands()}
        for name in ['room create', 'room configure', 'map install', 'map change', 'addon import',
                     'plugin compile', 'admin add', 'rcon exec', 'backup restore', 'network configure',
                     'transfer export', 'task cancel', 'repair permissions', 'storage cleanup']:
            self.assertIn(name, specs)
        self.assertFalse(any('_run' in name or name.startswith('web ') for name in specs))
        self.assertNotIn('server install', specs)
        self.assertTrue(specs['transfer export']['preview'])
        self.assertTrue(specs['server start']['mutating'])
        self.assertFalse(specs['server start']['background'])
        for spec in specs.values():
            self.assertEqual(len(spec['fields']), len({f['name'] for f in spec['fields']}), spec['id'])

    def test_schema_uses_parser_types_choices_and_rejects_extra_fields(self):
        argv, _ = schema.arguments('room configure', {'name': 'Example', 'port': '27025'}, 'room1', True)
        parsed = cli.parser().parse_args(argv)
        self.assertEqual(parsed.port, 27025)
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.room, 'room1')
        with self.assertRaises(CliError):
            schema.arguments('web _bridge', {}, 'room1')
        with self.assertRaises(CliError):
            schema.arguments('room logs', {'follow': True}, 'room1')
        argv, _ = schema.arguments('room configure', {'no_autostart': True}, 'room1', True)
        self.assertFalse(cli.parser().parse_args(argv).autostart)
        argv, _ = schema.arguments('network configure', {'reset_ipv4': True}, 'room1', True)
        self.assertFalse(cli.parser().parse_args(argv).ipv4)

    def test_password_hash_and_no_plaintext_storage(self):
        private = {'salt': 'ab' * 16}
        private['password_hash'] = manager.digest('test-only-password', private['salt'])
        self.assertTrue(manager.valid_password('test-only-password', private))
        self.assertFalse(manager.valid_password('incorrect', private))
        self.assertNotIn('test-only-password', json.dumps(private))

    def test_web_start_waits_for_http_listener(self):
        with patch.object(manager.host, 'require_platform'), patch.object(manager.host, 'require_root'), \
             patch.object(manager, 'configuration', return_value={'host': '127.0.0.1', 'port': 8081}), \
             patch.object(manager, 'confirm'), patch.object(manager, 'run') as command, \
             patch.object(manager.time, 'sleep'), patch.object(manager.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = [ConnectionRefusedError(), io.BytesIO(b'{"service":"l4d2-cli-web"}')]
            manager.lifecycle('start', yes=True)
        command.assert_called_once_with(['systemctl', 'start', manager.SERVICE])
        self.assertEqual(opener.return_value.open.call_count, 2)


class CliWorkflowTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(manager, 'STATE', self.root / 'web'))
        self.stack.enter_context(patch.object(manager, 'configuration', return_value={'room': 'room1'}))
        self.stack.enter_context(patch.object(manager, 'credentials', return_value={'session_secret': 'unit-test-secret'}))

    def test_preview_confirm_apply_admin_through_public_cli(self):
        values = {'steam_id': 'STEAM_1:0:12345678', 'backend': 'sourcemod'}
        request = {'command': 'admin add', 'values': values, 'room': 'room1', 'preview': True}
        preview = bridge.operation(request)
        self.assertTrue(preview['data']['dry_run'])
        applied = bridge.operation({**request, 'preview': False, **{k: preview[k] for k in ('confirmation', 'expires')}})
        self.assertTrue(applied['ok'])
        listed = bridge.operation({'command': 'admin list', 'values': {}, 'room': 'room1'})
        self.assertIn('STEAM_1:0:12345678', json.dumps(listed))
        with self.assertRaises(CliError):
            bridge.operation({**request, 'values': {**values, 'steam_id': 'STEAM_1:0:99999999'}, 'preview': False,
                              **{k: preview[k] for k in ('confirmation', 'expires')}})

    def test_unconfirmed_and_expired_changes_do_not_execute(self):
        request = {'command': 'admin add', 'values': {'steam_id': 'STEAM_1:0:12345678'}, 'room': 'room1', 'preview': False}
        with self.assertRaises(CliError):
            bridge.operation(request)
        with self.assertRaises(CliError):
            bridge.operation({**request, 'expires': int(time.time()) - 1, 'confirmation': 'bad'})

    def test_main_server_commands_refuse_a_different_selected_room(self):
        with patch.object(bridge, 'run_cli') as command:
            with self.assertRaises(CliError) as caught:
                bridge.operation({'command': 'server restart', 'room': 'room1', 'values': {}})
        self.assertEqual(caught.exception.code, 'invalid_room_target')
        command.assert_not_called()

    def test_snapshot_uses_registered_rooms_cli_inventory_and_real_campaigns(self):
        source = self.local({'maps/web_test.bsp': b'fixture',
                             'missions/web_test.txt': b'"mission" { "DisplayTitle" "Web Test" "modes" { "coop" { "1" { "Map" "web_test" } } } }'})
        from l4d2_cli.ops import content
        content.install_local(self.room, source, 'map', yes=True)
        snapshot = bridge.snapshot('room1')
        self.assertEqual(snapshot['rooms'][0]['id'], 'room1')
        self.assertIn('web_test', snapshot['maps'])
        self.assertTrue(any(c['title'] == 'Web Test' for c in snapshot['campaigns']))
        self.assertEqual(snapshot['addons'][0]['size'], source.stat().st_size)

    def test_legacy_invalid_actions_fail_before_background_submission(self):
        for path, fields in [('/api/catalog/install', {'id': '../bad'}),
                             ('/api/catalog/install', {'id': '12345', 'source': 'gamemaps', 'kind': 'mod'}),
                             ('/api/map-package/delete', {'filename': '../bad.vpk', 'mode': 'soft'}),
                             ('/api/addon/state', {'filename': '../bad.vpk', 'state': 'disabled'}),
                             ('/api/server-plugins/state', {'plugin': 'left4dhooks.smx', 'state': 'disabled'})]:
            with self.subTest(path=path), self.assertRaises(CliError):
                bridge.http({'path': path, 'fields': fields, 'room': 'room1'})

    def test_uploaded_file_paths_and_outputs_are_confined(self):
        with self.assertRaises(CliError):
            bridge.prepare_files({'file': '/etc/shadow'})
        with self.assertRaises(CliError):
            bridge.prepare_files({'output': '../escape.zip'})

    def test_map_toggle_tracks_loose_resources_and_protects_defaults(self):
        from l4d2_cli.ops import content
        source = self.local({'maps/web_map.bsp': b'map', 'materials/shared.vmt': b'material'})
        content.install_local(self.room, source, 'map', yes=True)
        content.state(self.room, 'test.vpk', False, yes=True)
        self.assertFalse((self.game / 'left4dead2/maps/web_map.bsp').exists())
        self.assertTrue((self.game / 'left4dead2/addons_disabled/test.vpk').is_file())
        content.state(self.room, 'test.vpk', True, yes=True)
        self.assertEqual((self.game / 'left4dead2/maps/web_map.bsp').read_bytes(), b'map')
        self.room['map'] = 'web_map'
        from l4d2_cli.ops import base
        base.save(base.ROOMS, {'version': 1, 'rooms': {'room1': self.room}})
        with self.assertRaises(CliError) as error:
            content.state(self.room, 'test.vpk', False, yes=True)
        self.assertEqual(error.exception.code, 'map_in_use')

    def test_shared_plugin_restart_never_targets_an_unrelated_game(self):
        calls = []
        registered = {'rooms': [{'id': 'main', 'game_dir': '/other'}, {'id': 'room1', 'game_dir': '/test'}, {'id': 'room2', 'game_dir': '/test'}]}
        def command(*args):
            calls.append(args)
            return registered if args[:2] == ('room', 'list') else {}
        with patch.object(bridge, 'cli', side_effect=command):
            bridge.http({'path': '/api/server-plugins/restart', 'fields': {'target': 'all'}, 'room': 'room1'})
            self.assertEqual([c[3] for c in calls if c[:2] == ('room', 'restart')], ['room1', 'room2'])
            with self.assertRaises(CliError):
                bridge.http({'path': '/api/server-plugins/restart', 'fields': {'target': 'main'}, 'room': 'room1'})


class MultipartTests(unittest.TestCase):
    def test_streams_binary_payload_and_fields_in_any_order(self):
        root = Path(__file__).resolve().parents[1] / '.test-tmp'
        root.mkdir(exist_ok=True)
        payload = b'\0\xff' * 70000
        body = (b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.vpk"\r\n'
                b'Content-Type: application/octet-stream\r\n\r\n' + payload +
                b'\r\n--test-boundary\r\nContent-Disposition: form-data; name="kind"\r\n\r\nmap\r\n--test-boundary--\r\n')
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            result = server.multipart(io.BytesIO(body), b'test-boundary', len(body), Path(tmp), len(payload))
            self.assertEqual(result['kind'], 'map')
            self.assertEqual((Path(tmp) / result['upload_id']).read_bytes(), payload)

    def test_truncated_or_overlimit_upload_is_removed(self):
        root = Path(__file__).resolve().parents[1] / '.test-tmp'
        body = b'--x\r\nContent-Disposition: form-data; name="file"; filename="t.vpk"\r\n\r\n12345\r\n--x--\r\n'
        for content, limit in [(body, 2), (body[:-8], 100)]:
            with tempfile.TemporaryDirectory(dir=root) as tmp:
                with self.assertRaises(CliError):
                    server.multipart(io.BytesIO(content), b'x', len(content), Path(tmp), limit)
                self.assertFalse(list(Path(tmp).iterdir()))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(__file__).resolve().parents[1] / '.test-tmp'
        root.mkdir(exist_ok=True)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=root)))
        (self.tmp / 'index.html').write_text('<html>panel</html>')
        (self.tmp / 'uploads').mkdir()
        self.stack.enter_context(patch.object(manager, 'STATIC', self.tmp))
        self.stack.enter_context(patch.object(manager, 'STATE', self.tmp))
        private = {'salt': 'cd' * 16}
        private['password_hash'] = manager.digest('test-only-password', private['salt'])
        self.calls = []
        def fake_bridge(request, config):
            self.calls.append(request)
            return {'ok': True, 'rooms': [], 'jobs': []}
        self.http = server.PanelServer(('127.0.0.1', 0), {'username': 'admin', 'max_upload_bytes': 1024**2}, private, fake_bridge)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join(5)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def login(self):
        code, headers, _ = self.request('POST', '/api/login', urllib.parse.urlencode({'username': 'admin', 'password': 'test-only-password'}),
                                        {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(code, 303)
        token, session = next(iter(self.http.sessions.items()))
        return {'Cookie': 'l4d2_session=' + token, 'X-CSRF-Token': session['csrf']}

    def test_login_logout_and_unauthorized_state(self):
        _, login_headers, _ = self.request('GET', '/login')
        self.assertEqual(login_headers['Referrer-Policy'], 'same-origin')
        self.assertEqual(self.request('GET', '/api/state')[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/logout', '', headers)[0], 200)
        self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 401)

    def test_csrf_cross_origin_and_hidden_static_paths(self):
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/restart', '', {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/restart', '', {**headers, 'Origin': 'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/../web-secrets.json', headers=headers)[0], 409)
        self.assertEqual(self.calls, [])

    def test_generic_operation_cannot_override_bridge_route(self):
        headers = {**self.login(), 'Content-Type': 'application/json'}
        self.assertEqual(self.request('POST', '/api/operation', json.dumps({'operation': 'snapshot'}), headers)[0], 400)
        self.assertFalse(self.calls)

    def test_readonly_endpoints_and_mutations_forward_selected_room(self):
        headers = {**self.login(), 'X-L4D2-Room': 'room2'}
        self.assertEqual(self.request('GET', '/api/commands', headers=headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/default-map', 'room=room2&map=example', headers)[0], 200)
        self.assertEqual(self.calls[-1]['room'], 'room2')
        self.assertEqual(self.calls[-1]['fields']['map'], 'example')


if __name__ == '__main__':
    unittest.main()
