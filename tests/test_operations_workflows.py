"""Cross-module migration, task, framework and protocol workflows."""
import contextlib
import dataclasses
import io
import json
from pathlib import Path
import struct
import tarfile
import os
import unittest
from unittest.mock import patch
import zipfile

from test_operations import Sandbox, make_vpk
from l4d2_cli import cli, storage
from l4d2_cli.common import CliError
from l4d2_cli.ops import base, backup, catalog, content, maintenance, plugins, rcon, rooms, tasks, transport


class MigrationTests(Sandbox):
    def test_adopt_existing_service_records_state_without_restarting(self):
        script = self.game / 'start_l4d2.sh'
        script.write_text('./srcds_run -game left4dead2 +hostport 27016 +map c1m1_hotel +servercfgfile server.cfg')
        unit = base.UNITS / 'l4d2_2.service'; unit.write_text('[Service]\nUser=steam\nExecStart=' + str(script))
        self.service.update(FragmentPath=str(unit))
        result = rooms.adopt('room2', str(self.game), 'l4d2_2', str(script), self.room['cfg'], 'steam', yes=True)
        self.assertFalse(result['restarts_server'])
        self.assertEqual(rooms.get('room2')['port'], 27016)
        self.assertEqual(rooms.get('room2')['kind'], 'adopted')

    def test_adoption_refuses_unrelated_service_and_script(self):
        script = self.game / 'script.sh'; script.write_text('anything')
        with self.assertRaises(CliError):
            rooms.adopt('room2', str(self.game), 'sshd', str(script), self.room['cfg'], 'steam', yes=True)

    def test_legacy_provenance_import_enables_safe_removal(self):
        source = self.local({'maps/custom.bsp': b'custom', 'materials/untracked.vmt': b'material'})
        content.install_local(self.room, source, 'map', yes=True)
        content.registry_path(self.room).unlink()
        old = self.tmp / 'packages.json'
        old.write_text(json.dumps({'packages': {'test.vpk': {'source': 'workshop', 'id': '12345', 'kind': 'map'}}}))
        content.import_legacy_registry(self.room, old, yes=True)
        content.remove(self.room, 'test.vpk', yes=True)
        self.assertFalse((rooms.game(self.room) / 'maps/custom.bsp').exists())
        self.assertTrue((rooms.game(self.room) / 'materials/untracked.vmt').exists())

    def test_full_backup_restore_preserves_configuration_admins_and_disabled_mod(self):
        Path(self.room['cfg']).write_text('hostname "Original"\nrcon_password "SYNTHETIC_PRIVATE"\n')
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:100', yes=True)
        content.install_local(self.room, self.local({'scripts/mod.nut': b'mod'}), 'mod', yes=True)
        content.state(self.room, 'test.vpk', False, yes=True)
        report = backup.create(self.room, yes=True)
        Path(self.room['cfg']).write_text('changed')
        plugins.admin_path(self.room, 'sourcemod').unlink()
        (rooms.game(self.room) / 'addons_disabled/test.vpk').unlink()
        with patch.object(backup, 'run'):
            backup.restore(self.room, report['output'], yes=True)
        self.assertIn('SYNTHETIC_PRIVATE', Path(self.room['cfg']).read_text())
        self.assertTrue((rooms.game(self.room) / 'addons_disabled/test.vpk').exists())
        self.assertEqual(len(plugins.admins(self.room, 'sourcemod')['entries']), 1)
        self.assertFalse(rooms.get('room1')['autostart'])

    def test_backup_does_not_include_map_payloads(self):
        content.install_local(self.room, self.local({'maps/custom.bsp': b'custom'}), 'map',
                              {'source': 'workshop', 'id': '12345'}, yes=True)
        report = backup.create(self.room, yes=True)
        with zipfile.ZipFile(report['output']) as archive:
            self.assertFalse(any(name.endswith(('.vpk', '.bsp')) for name in archive.namelist()))
            document = json.loads(archive.read('manifest.json'))
        self.assertEqual(document['packages']['packages']['test.vpk']['id'], '12345')

    def test_backup_rejects_unregistered_destination_even_with_valid_checksum(self):
        source = self.tmp / 'unsafe.zip'
        import hashlib
        payload = b'unsafe'
        document = {'format': 'l4d2-state-backup', 'version': 1, 'scope': 'plugins',
                    'files': {'game/cfg/server.cfg': {'sha256': hashlib.sha256(payload).hexdigest(), 'mode': 0o644}}, 'rooms': {}}
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(document))
            archive.writestr('game/cfg/server.cfg', payload)
        with self.assertRaises(CliError) as caught:
            backup.restore(self.room, source, yes=True)
        self.assertEqual(caught.exception.code, 'unsafe_backup')
        self.assertEqual(Path(self.room['cfg']).read_text(), '')

    def test_pending_room_port_is_not_used_to_query_the_running_process(self):
        self.service.update(MainPID=123, ActiveState='active')
        with patch.object(rooms.host, 'udp_owner', return_value=True), \
             patch.object(rooms.host, 'check_udp_available'), \
             patch.object(rooms.manager, 'query_address', return_value='10.0.0.1'), \
             patch.object(rooms.manager.a2s, 'query', return_value={'map': 'c1m1_hotel'}) as query:
            rooms.configure('room1', {'port': 27016}, yes=True)
            report = rooms.status(rooms.get('room1'))
        self.assertTrue(report['configuration_pending'])
        self.assertEqual(report['running_endpoint']['port'], 27015)
        self.assertEqual(query.call_args.args[1], 27015)

    def test_refresh_requires_explicit_acceptance_and_registers_external_edit(self):
        rooms.configure('room1', {'name': 'Before'}, yes=True)
        Path(self.room['cfg']).write_text('hostname "After"\n')
        preview = rooms.refresh('room1', dry_run=True)
        self.assertTrue(preview['dry_run'])
        rooms.refresh('room1', yes=True)
        self.assertEqual(rooms.get('room1')['name'], 'After')

    def test_pending_port_can_be_reverted_to_the_owned_live_endpoint(self):
        self.service.update(MainPID=123, ActiveState='active')
        with patch.object(rooms.host, 'udp_owner', return_value=True), \
             patch.object(rooms.host, 'check_udp_available') as available:
            rooms.configure('room1', {'port': 27016}, yes=True)
            rooms.configure('room1', {'port': 27015}, yes=True)
        self.assertEqual(rooms.get('room1')['port'], 27015)
        available.assert_called_once_with('0.0.0.0', 27016)

    def test_reverting_port_requires_verified_live_ownership(self):
        self.service.update(MainPID=123, ActiveState='active')
        rooms.configure('room1', {'port': 27016}, yes=True)
        with patch.object(rooms.host, 'udp_owner', return_value=False), \
             patch.object(rooms.host, 'check_udp_available', side_effect=CliError('Occupied', 'port_conflict', 4)):
            with self.assertRaises(CliError) as caught:
                rooms.configure('room1', {'port': 27015}, yes=True)
        self.assertEqual(caught.exception.code, 'port_conflict')
        self.assertEqual(rooms.get('room1')['port'], 27016)


class FrameworkTests(Sandbox):
    def archive(self, name, files):
        output = self.tmp / name
        with tarfile.open(output, 'w') as archive:
            for path, contents in files.items():
                info = tarfile.TarInfo(path); info.size = len(contents)
                archive.addfile(info, io.BytesIO(contents))
        return output

    def test_framework_install_keeps_votes_disabled_and_selects_32bit_loader(self):
        metamod = self.archive('metamod.tar', {'addons/metamod/bin/server.so': b'loader', 'addons/metamod_x64.vdf': b'bad'})
        sourcemod = self.archive('sourcemod.tar', {'addons/sourcemod/plugins/mapchooser.smx': b'FFPS-example',
                                                'cfg/sourcemod/sourcemod.cfg': b'cfg'})
        with patch.object(plugins, 'run'):
            result = plugins.stack(self.room, metamod, sourcemod, yes=True)
        self.assertTrue((plugins.directory(self.room) / 'plugins/disabled/mapchooser.smx').exists())
        self.assertFalse((rooms.game(self.room) / 'addons/metamod_x64.vdf').exists())
        self.assertIn('../left4dead2/addons/metamod/bin/server', (rooms.game(self.room) / 'addons/metamod.vdf').read_text())
        self.assertTrue((rooms.game(self.room) / 'cfg/sourcemod/sourcemod.cfg').exists())
        self.assertTrue(result['restart_required'])

    def test_tar_link_is_rejected(self):
        source = self.tmp / 'unsafe.tar'
        with tarfile.open(source, 'w') as archive:
            item = tarfile.TarInfo('addons/link'); item.type = tarfile.SYMTYPE; item.linkname = '/etc'
            archive.addfile(item)
        with self.assertRaises(CliError):
            plugins.unpack_tar(source, self.tmp / 'unpacked')

    def test_admin_removal_does_not_remove_an_id_with_the_same_prefix(self):
        for steam_id in ('STEAM_1:0:10', 'STEAM_1:0:100'):
            plugins.set_admin(self.room, 'sourcemod', steam_id, yes=True)
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:10', remove=True, yes=True)
        self.assertIn('STEAM_1:0:100', plugins.admins(self.room, 'sourcemod')['entries'][0])

    def test_bundled_source_assets_are_available_without_web(self):
        sources = plugins.sources()['sources']
        self.assertEqual({item['id'] for item in sources}, {'infected_counter', 'jiangmen_changelevel_fix'})
        self.assertTrue(all(Path(item['path']).is_file() for item in sources))


class BackgroundTests(Sandbox):
    def test_task_show_exposes_current_download_progress(self):
        task_id = 'd' * 32
        base.save(tasks.path(task_id), {'id': task_id, 'status': 'running', 'pid': 123, 'start_ticks': '10'})
        tasks.path(task_id).with_suffix('.log').write_text('{"action":"install-content","parts":["one","two"]}\ndownload bytes=50 total=100\n')
        with patch.object(tasks, 'ticks', return_value='10'):
            progress = tasks.get(task_id)['progress']
        self.assertEqual(progress['phase'], 'download')
        self.assertEqual(progress['parts_total'], 2)
        self.assertEqual(progress['download']['percent'], 50.0)

    def test_worker_persists_failure_and_keeps_redacted_logs(self):
        task_id = 'b' * 32
        base.save(tasks.path(task_id), {'id': task_id, 'status': 'queued', 'arguments': ['map', 'import', '--file', 'missing.vpk']})
        with patch.object(tasks.signal, 'signal'), patch.object(cli, 'main', return_value=4):
            result = tasks.worker(task_id)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(tasks.get(task_id)['exit_code'], 4)
        self.assertTrue(tasks.path(task_id).with_suffix('.log').exists())

    def test_cancelled_queue_never_executes(self):
        task_id = 'c' * 32
        base.save(tasks.path(task_id), {'id': task_id, 'status': 'queued', 'arguments': []})
        tasks.cancel(task_id, yes=True)
        with patch.object(cli, 'main') as execute, self.assertRaises(CliError):
            tasks.worker(task_id)
        execute.assert_not_called()

    def test_submit_has_no_shell_and_uses_a_separate_worker(self):
        with patch.object(tasks.subprocess, 'Popen') as popen:
            result = tasks.submit(['map', 'import', '--file', 'map.vpk', '--room', 'room1'], yes=True)
        self.assertIn('_run', popen.call_args.args[0])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertNotIn('shell', popen.call_args.kwargs)
        self.assertEqual(tasks.get(result['task_id'])['status'], 'queued')

    def test_live_staging_is_retained_and_dead_staging_is_cleaned(self):
        live = base.ROOT / 'download-live'; dead = base.ROOT / 'download-dead'
        for path, pid in ((live, 100), (dead, 200)):
            path.mkdir(); (path / 'data.part').write_bytes(b'data')
            base.save(path / '.l4d2-owner.json', {'pid': pid, 'ticks': '1'})
        with patch.object(base, 'process_ticks', side_effect=lambda pid: '1' if pid == 100 else None):
            maintenance.cleanup(self.room, 'temporary', yes=True)
        self.assertTrue(live.exists())
        self.assertFalse(dead.exists())


class NetworkWorkflowTests(Sandbox):
    def test_split_mod_state_and_full_backup_include_archive_parts(self):
        source = self.local({'scripts/custom.nut': b'custom'}, 'mod_dir.vpk')
        data = bytearray(source.read_bytes())
        entry = data.index(b'custom\0') + len(b'custom\0')
        tree_size = struct.unpack_from('<I', data, 8)[0]
        struct.pack_into('<H', data, entry + 6, 0)
        struct.pack_into('<I', data, entry + 8, 0)
        source.write_bytes(data[:12 + tree_size])
        (source.parent / 'mod_000.vpk').write_bytes(b'custom')
        content.install_local(self.room, source, 'mod', yes=True)
        content.state(self.room, source.name, False, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons_disabled/mod_000.vpk').exists())
        report = backup.create(self.room, yes=True)
        with zipfile.ZipFile(report['output']) as archive:
            self.assertIn('game/addons_disabled/mod_000.vpk', archive.namelist())
        content.state(self.room, source.name, True, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons/mod_000.vpk').exists())

    def test_split_vpk_survives_offline_transfer_and_custom_map_discovery(self):
        source = self.local({'maps/custom.bsp': b'custom'}, 'campaign_dir.vpk')
        data = bytearray(source.read_bytes())
        entry = data.index(b'custom\0') + len(b'custom\0')
        tree_size = struct.unpack_from('<I', data, 8)[0]
        struct.pack_into('<H', data, entry + 6, 0)
        struct.pack_into('<I', data, entry + 8, 0)
        source.write_bytes(data[:12 + tree_size])
        (source.parent / 'campaign_000.vpk').write_bytes(b'custom')
        content.install_local(self.room, source, 'map', yes=True)
        from l4d2_cli import packages
        self.assertIn('custom', packages.installed_maps(self.game, custom=True))
        exported = self.tmp / 'split.zip'
        content.export_zip(self.room, exported)
        content.remove(self.room, source.name, purge=True, yes=True)
        content.import_zip(self.room, exported, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons/campaign_000.vpk').exists())
        self.assertIn('custom', content.inventory(self.room)['maps'])

    def test_partial_multipart_install_can_be_retried(self):
        data = {}
        for item_id in ('12345', '67890'):
            data[item_id] = self.local({'maps/map_' + item_id + '.bsp': item_id.encode()}, item_id + '.vpk').read_bytes()
        parts = [{'ok': True, 'workshop_id': key, 'file_url': 'https://cdn.steamusercontent.com/' + key,
                  'file_size': len(value)} for key, value in data.items()]
        failed = [True]
        def download(url, destination, *args):
            item_id = url.rsplit('/', 1)[-1]
            if item_id == '67890' and failed[0]:
                failed[0] = False
                raise CliError('Synthetic download failure', 'download_failed')
            Path(destination).write_bytes(data[item_id])
            return Path(destination)
        with patch.object(catalog, 'workshop_expected_parts', return_value={'ok': True, 'parts': parts}), \
             patch.object(transport, 'download', side_effect=download):
            with self.assertRaises(CliError):
                content.install(self.room, 'workshop', '12345', yes=True)
            result = content.install(self.room, 'workshop', '12345', yes=True)
        self.assertTrue(result['installed'][0]['unchanged'])
        self.assertIn('map_67890', content.inventory(self.room)['maps'])

    def test_wrong_game_workshop_item_is_not_installable(self):
        details = {'response': {'publishedfiledetails': [{'result': 1, 'consumer_app_id': 440,
                                                         'file_url': 'https://cdn.steamusercontent.com/file'}]}}
        with patch.object(catalog, 'http_json', return_value=details):
            self.assertFalse(catalog.resolve_single_workshop_download('12345', 'map')['ok'])

    def test_transport_deadline_prevents_network_request(self):
        with patch.object(transport.time, 'monotonic', return_value=10), transport.budget(1):
            with patch.object(transport.time, 'monotonic', return_value=12), patch.object(transport, 'opener') as opener:
                with self.assertRaises(CliError) as caught:
                    transport.fetch_bytes('https://steamcommunity.com/path')
        self.assertEqual(caught.exception.code, 'timeout')
        opener.return_value.open.assert_not_called()

    def test_download_accepts_stream_without_content_length(self):
        response = io.BytesIO(b'chunked archive payload')
        response.status, response.headers = 200, {}
        with patch.object(transport, 'opener') as opener:
            opener.return_value.open.return_value = response
            destination = transport.download('https://github.com/example/archive.zip', self.tmp/'download.zip')
        self.assertEqual(destination.read_bytes(), b'chunked archive payload')

    def test_unknown_download_length_still_enforces_size_limit(self):
        response = io.BytesIO(b'too much data')
        response.status, response.headers = 200, {}
        destination = self.tmp/'oversized.zip'
        with patch.object(transport, 'opener') as opener, patch.object(transport, 'MAX_DOWNLOAD_BYTES', 4):
            opener.return_value.open.return_value = response
            with self.assertRaises(CliError) as caught:
                transport.download('https://github.com/example/archive.zip', destination)
        self.assertEqual(caught.exception.code, 'invalid_download')
        self.assertFalse(destination.exists())

    def test_known_download_size_cannot_be_truncated(self):
        def response(*args, **kwargs):
            stream = io.BytesIO(b'short')
            stream.status, stream.headers = 200, {'Content-Length': '10'}
            return stream
        with patch.object(transport, 'opener') as opener:
            opener.return_value.open.side_effect = response
            with self.assertRaises(CliError) as caught:
                transport.download('https://github.com/example/archive.zip', self.tmp/'truncated.zip')
        self.assertEqual(caught.exception.code, 'download_failed')

    def test_rcon_handles_fragmented_frames_and_multi_packet_reply(self):
        Path(self.room['cfg']).write_text('rcon_password "SYNTHETIC_RCON"')
        def frame(request, kind, data):
            body = struct.pack('<ii', request, kind) + data + b'\0\0'
            return struct.pack('<i', len(body)) + body
        response = bytearray(frame(1, 0, b'') + frame(1, 2, b'') + frame(2, 0, b'first') +
                             frame(2, 0, b'second') + frame(3, 0, b'L4D2_CLI_RCON_END'))
        def receive(size):
            size = min(size, 3)
            data = bytes(response[:size]); del response[:size]
            return data
        with patch.object(rcon.manager, 'query_address', return_value='127.0.0.1'), \
             patch.object(rcon.socket, 'create_connection') as socket:
            socket.return_value.__enter__.return_value.recv.side_effect = receive
            result = rcon.command(self.room, 'status', yes=True)
        self.assertEqual(result['response'], 'firstsecond')

    def test_rcon_rejects_failed_authentication(self):
        Path(self.room['cfg']).write_text('rcon_password "SYNTHETIC_RCON"')
        payload = struct.pack('<iii', 10, -1, 2) + b'\0\0'
        response = bytearray(payload)
        def receive(size):
            data = bytes(response[:size]); del response[:size]
            return data
        with patch.object(rcon.manager, 'query_address', return_value='127.0.0.1'), \
             patch.object(rcon.socket, 'create_connection') as socket:
            socket.return_value.__enter__.return_value.recv.side_effect = receive
            with self.assertRaises(CliError) as caught:
                rcon.command(self.room, 'status', yes=True)
        self.assertEqual(caught.exception.code, 'rcon_auth_failed')

    def test_rcon_uses_live_endpoint_while_saved_port_is_pending(self):
        Path(self.room['cfg']).write_text('rcon_password "SYNTHETIC_RCON"')
        self.room.update(bind='10.0.0.3', port=27027)
        def frame(request, kind, data):
            body = struct.pack('<ii', request, kind) + data + b'\0\0'
            return struct.pack('<i', len(body)) + body
        response = bytearray(frame(1, 2, b'') + frame(2, 0, b'live room') + frame(3, 0, b'end'))
        def receive(size):
            data = bytes(response[:size]); del response[:size]
            return data
        with patch.object(rooms, 'status', return_value={'running_endpoint': {'bind': '127.0.0.1', 'port': 27025}}), \
             patch.object(rcon.socket, 'create_connection') as socket:
            socket.return_value.__enter__.return_value.recv.side_effect = receive
            report = rcon.command(self.room, 'status', yes=True)
        self.assertEqual(report['response'], 'live room')
        socket.assert_called_once_with(('127.0.0.1', 27025), timeout=15)


@unittest.skipUnless(os.name == 'posix', 'Linux descriptor-relative file operations')
class PosixBoundaryTests(Sandbox):
    def test_durable_write_read_and_unlink(self):
        path = self.tmp / 'new/nested/config.cfg'
        base.replace_file(path, b'configuration', 0o600)
        self.assertEqual(base.text(path), 'configuration')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        base.unlink(path)
        self.assertFalse(path.exists())

    def test_directory_link_cannot_redirect_a_root_write_after_preflight(self):
        outside = self.tmp / 'outside'; outside.mkdir()
        link = self.tmp / 'link'; link.symlink_to(outside, target_is_directory=True)
        # Model a link introduced after the initial path check; the descriptor walk still rejects it.
        with patch.object(base.storage, 'reject_links'), self.assertRaises(OSError):
            base.replace_file(link / 'config.cfg', b'unsafe')
        self.assertFalse((outside / 'config.cfg').exists())

    def test_nonregular_input_never_blocks_the_reader(self):
        pipe = self.tmp / 'input-pipe'; os.mkfifo(pipe)
        with self.assertRaises(CliError):
            with base.input_stream(pipe):
                self.fail('opened a FIFO')
