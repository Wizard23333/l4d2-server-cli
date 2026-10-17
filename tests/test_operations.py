"""Filesystem workflow and hostile-input regressions for headless operations."""
import contextlib
import dataclasses
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from l4d2_cli import cli, model, packages, storage
from l4d2_cli.common import CliError
from l4d2_cli.ops import base, backup, catalog, content, maintenance, missions, plugins, rooms, tasks, transport, vpk


def make_vpk(path, files, version=1):
    tree = bytearray()
    groups = {}
    payload = bytearray()
    for name, data in files.items():
        p = Path(name)
        groups.setdefault(p.suffix[1:], {}).setdefault(p.parent.as_posix(), []).append((p.stem, data))
    for extension, folders in groups.items():
        tree.extend(extension.encode() + b'\0')
        for folder, values in folders.items():
            tree.extend(folder.encode() + b'\0')
            for name, data in values:
                tree.extend(name.encode() + b'\0')
                tree.extend(struct.pack('<IHHIIH', zlib.crc32(data), 0, 0x7FFF, len(payload), len(data), 0xFFFF))
                payload.extend(data)
            tree.extend(b'\0')
        tree.extend(b'\0')
    tree.extend(b'\0')
    header = struct.pack('<III', 0x55AA1234, version, len(tree))
    if version == 2:
        header += struct.pack('<IIII', len(payload), 0, 0, 0)
    path.write_bytes(header + tree + payload)
    return path


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        # Keep test files in the repository's declared writable root.
        root = Path(__file__).resolve().parents[1] / '.test-tmp'
        root.mkdir(exist_ok=True)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=root)))
        self.game = self.tmp / 'game'
        (self.game / 'left4dead2/maps').mkdir(parents=True)
        (self.game / 'left4dead2/maps/c1m1_hotel.bsp').write_bytes(b'official')
        (self.game / 'srcds_run').write_text('# game')
        self.root = self.tmp / 'state'
        self.root.mkdir()
        self.room = dict(id='room1', game_dir=str(self.game), kind='created', service='l4d2-room1.service',
                         user='steam', group='steam', bind='0.0.0.0', port=27015, map='c1m1_hotel',
                         name='Room 1', visibility='public', steam_group_id='', mode='coop', difficulty='normal',
                         autostart=False, cfg=str(self.game / 'left4dead2/cfg/server.cfg'),
                         unit=str(self.tmp / 'units/l4d2-room1.service'))
        for key in ('cfg', 'unit'):
            p = Path(self.room[key]); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('')
        self.stack.enter_context(patch.object(base, 'ROOT', self.root))
        self.stack.enter_context(patch.object(base, 'ROOMS', self.tmp / 'rooms.json'))
        self.stack.enter_context(patch.object(base, 'UNITS', self.tmp / 'units'))
        self.stack.enter_context(patch.object(transport, 'CONFIG', self.tmp / 'network.json'))
        self.stack.enter_context(patch.object(backup, 'NETWORK', {key: self.tmp / key for key in backup.NETWORK}))
        self.stack.enter_context(patch.object(storage, 'PATHS', storage.Paths(state=self.tmp / 'absent.json')))
        self.stack.enter_context(patch.object(storage, 'operation_lock', side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(tasks, 'task_lock', side_effect=lambda _: contextlib.nullcontext()))
        self.stack.enter_context(patch.object(storage, 'atomic_write', side_effect=self.atomic_write))
        self.stack.enter_context(patch.object(base, 'account', return_value=(-1, -1)))
        if hasattr(base.os, 'chown'):
            self.stack.enter_context(patch.object(base.os, 'chown'))
        if hasattr(base.os, 'fchown'):
            self.stack.enter_context(patch.object(base.os, 'fchown'))
        self.stack.enter_context(patch.object(base.host, 'require_platform'))
        self.stack.enter_context(patch.object(base.host, 'require_root'))
        self.service = dict(MainPID=0, ActiveState='inactive', FragmentPath=self.room['unit'], User='steam',
                            Group='steam', DropInPaths='', InvocationID='test')
        self.stack.enter_context(patch.object(rooms.host, 'service_info', side_effect=lambda **kw: self.service.copy()))
        self.stack.enter_context(patch.object(rooms.host, 'check_udp_available'))
        self.stack.enter_context(patch.object(rooms, 'run'))
        base.save(base.ROOMS, {'version': 1, 'rooms': {'room1': self.room}})

    def atomic_write(self, path, data, mode=0o644, gid=-1):
        base.replace_file(path, data, mode)

    def local(self, files, name='test.vpk'):
        return make_vpk(self.tmp / name, files)


class VpkTests(Sandbox):
    def test_v1_v2_crc_and_restricted_extraction(self):
        for version in (1, 2):
            source = make_vpk(self.tmp / ('v%d.vpk' % version), {'maps/custom.bsp': b'map', 'cfg/server.cfg': b'forbidden'}, version)
            self.assertEqual(vpk.verify(source)['entries'], 2)
            output = self.tmp / ('extract%d' % version)
            files = vpk.extract(source, output)
            self.assertEqual(files, ['maps/custom.bsp'])
            self.assertFalse((output / 'cfg').exists())

    def test_corrupt_crc_rejected_before_install(self):
        source = self.local({'maps/custom.bsp': b'abc'})
        data = source.read_bytes(); source.write_bytes(data[:-1] + b'x')
        with self.assertRaises(CliError):
            content.install_local(self.room, source, 'map', yes=True)
        self.assertFalse((rooms.game(self.room) / 'addons/test.vpk').exists())

    def test_truncated_tree_and_payload_are_rejected(self):
        source = self.local({'maps/custom.bsp': b'abc'})
        source.write_bytes(source.read_bytes()[:-2])
        with self.assertRaises(CliError):
            vpk.entries(source)

    def test_path_traversal_is_rejected(self):
        for path in ('../escape', '/absolute', 'maps/../../escape', 'C:/escape'):
            with self.subTest(path=path), self.assertRaises(CliError):
                base.inside(self.tmp, path)

    def test_preload_bytes_are_verified(self):
        source = self.local({'maps/custom.bsp': b'abc'})
        self.assertEqual(vpk.text(vpk.entries(source)[0]), 'abc')


class ContentTests(Sandbox):
    def test_import_inventory_default_and_remove_workflow(self):
        source = self.local({'maps/custom.bsp': b'custom', 'missions/campaign.txt': b'"other" { "DisplayTitle" "Campaign" "modes" { "coop" { "1" { "Map" "custom" } } } }'})
        content.install_local(self.room, source, 'map', {'source': 'workshop', 'id': '12345'}, yes=True)
        report = content.inventory(self.room)
        self.assertIn('custom', report['maps'])
        self.assertEqual(report['campaigns'][0]['maps'][0]['name'], 'custom')
        self.assertIn('custom', packages.installed_maps(self.game, custom=True))
        content.remove(self.room, 'test.vpk', yes=True)
        self.assertFalse((rooms.game(self.room) / 'maps/custom.bsp').exists())
        self.assertEqual(content.registry(self.room)['packages']['test.vpk']['state'], 'removed')

    def test_shared_resources_survive_first_package_removal(self):
        for name, map_name in [('one.vpk', 'map_one'), ('two.vpk', 'map_two')]:
            source = self.local({'maps/' + map_name + '.bsp': map_name.encode(), 'materials/shared.vmt': b'shared'}, name)
            content.install_local(self.room, source, 'map', yes=True)
        content.remove(self.room, 'one.vpk', yes=True)
        self.assertTrue((rooms.game(self.room) / 'materials/shared.vmt').exists())
        content.remove(self.room, 'two.vpk', yes=True)
        self.assertFalse((rooms.game(self.room) / 'materials/shared.vmt').exists())

    def test_untracked_equal_loose_file_is_not_claimed_or_deleted(self):
        loose = rooms.game(self.room) / 'maps/untracked.bsp'; loose.write_bytes(b'same')
        content.install_local(self.room, self.local({'maps/untracked.bsp': b'same'}), 'map', yes=True)
        content.remove(self.room, 'test.vpk', yes=True)
        self.assertTrue(loose.exists())

    def test_conflicting_loose_resource_rejects_complete_install(self):
        (rooms.game(self.room) / 'maps/custom.bsp').write_bytes(b'old')
        with self.assertRaises(CliError):
            content.install_local(self.room, self.local({'maps/custom.bsp': b'new'}), 'map', yes=True)
        self.assertFalse((rooms.game(self.room) / 'addons/test.vpk').exists())

    def test_changed_loose_file_blocks_removal(self):
        content.install_local(self.room, self.local({'maps/custom.bsp': b'new'}), 'map', yes=True)
        (rooms.game(self.room) / 'maps/custom.bsp').write_bytes(b'edited')
        with self.assertRaises(CliError):
            content.remove(self.room, 'test.vpk', yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons/test.vpk').exists())

    def test_default_map_package_cannot_be_removed(self):
        content.install_local(self.room, self.local({'maps/custom.bsp': b'new'}), 'map', yes=True)
        self.room['map'] = 'custom'
        base.save(base.ROOMS, {'version': 1, 'rooms': {'room1': self.room}})
        with self.assertRaises(CliError):
            content.remove(self.room, 'test.vpk', yes=True)

    def test_map_cannot_be_imported_as_mod(self):
        with self.assertRaises(CliError):
            content.install_local(self.room, self.local({'maps/custom.bsp': b'x'}), 'mod', yes=True)

    def test_mod_enable_disable_roundtrip(self):
        content.install_local(self.room, self.local({'scripts/mod.nut': b'x'}), 'mod', yes=True)
        content.state(self.room, 'test.vpk', False, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons_disabled/test.vpk').exists())
        content.state(self.room, 'test.vpk', True, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons/test.vpk').exists())

    def test_dry_run_creates_no_resource_or_registry(self):
        source = self.local({'maps/custom.bsp': b'x'})
        result = content.install_local(self.room, source, 'map', dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertFalse(content.registry_path(self.room).exists())
        self.assertFalse((rooms.game(self.room) / 'addons').exists())

    def test_manifest_export_import_does_not_download(self):
        content.install_local(self.room, self.local({'maps/custom.bsp': b'x'}), 'map', {'source': 'workshop', 'id': '12345'}, yes=True)
        target = self.tmp / 'manifest.json'
        content.export_manifest(self.room, target)
        content.remove(self.room, 'test.vpk', purge=True, yes=True)
        with patch.object(content.transport, 'download') as download:
            content.import_manifest(self.room, target, yes=True)
        download.assert_not_called()
        self.assertEqual(content.registry(self.room)['packages']['test.vpk']['state'], 'removed')

    def test_zip_export_import_preserves_disabled_mod(self):
        content.install_local(self.room, self.local({'scripts/mod.nut': b'x'}), 'mod', yes=True)
        content.state(self.room, 'test.vpk', False, yes=True)
        target = self.tmp / 'transfer.zip'; content.export_zip(self.room, target)
        content.remove(self.room, 'test.vpk', purge=True, yes=True)
        content.import_zip(self.room, target, yes=True)
        self.assertTrue((rooms.game(self.room) / 'addons_disabled/test.vpk').exists())

    def test_zip_traversal_rejected_before_any_extraction(self):
        archive_path = self.tmp / 'evil.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('normal.txt', 'normal'); archive.writestr('../escape', 'evil')
        with zipfile.ZipFile(archive_path) as archive, self.assertRaises(CliError):
            content.safe_unpack_zip(archive, self.tmp / 'out')
        self.assertFalse((self.tmp / 'out/normal.txt').exists())

    def test_zip_duplicate_destinations_rejected(self):
        archive_path = self.tmp / 'duplicate.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('File.txt', 'one'); archive.writestr('file.txt', 'two')
        with zipfile.ZipFile(archive_path) as archive, self.assertRaises(CliError):
            content.safe_unpack_zip(archive, self.tmp / 'out')


class StateTests(Sandbox):
    def test_transaction_failure_restores_every_previous_file(self):
        first = self.tmp / 'first'; first.write_bytes(b'before')
        absent = self.tmp / 'new'
        with self.assertRaises(CliError):
            base.transaction([(first, b'after', 0o600, -1, -1),
                              (absent, self.tmp / 'missing', 0o600, -1, -1)])
        self.assertEqual(first.read_bytes(), b'before')
        self.assertFalse(absent.exists())

    def test_pending_transaction_recovers_after_interruption(self):
        target = self.tmp / 'target'; target.write_bytes(b'changed')
        folder = base.ROOT / 'transactions/abc'; folder.mkdir(parents=True)
        (folder / '0').write_bytes(b'before')
        base.save(base.ROOT / 'transaction.json', {'id': 'abc', 'entries': [
            dict(target=str(target), backup='0', existed=True, mode=0o600, uid=-1, gid=-1)]})
        self.assertTrue(base.recover())
        self.assertEqual(target.read_bytes(), b'before')

    def test_room_config_preserves_unknown_cvars_and_does_not_restart(self):
        Path(self.room['cfg']).write_text('custom_setting "keep"\nrcon_password "SYNTHETIC_RCON"\n')
        result = rooms.configure('room1', {'name': 'Changed'}, yes=True)
        text = Path(self.room['cfg']).read_text()
        self.assertIn('custom_setting "keep"', text)
        self.assertIn('rcon_password "SYNTHETIC_RCON"', text)
        self.assertFalse(result['restart_required'])
        self.assertEqual(rooms.get('room1')['name'], 'Changed')

    def test_room_config_detects_external_changes(self):
        rooms.configure('room1', {'name': 'Changed'}, yes=True)
        Path(self.room['cfg']).write_text('manual change')
        with self.assertRaises(CliError) as caught:
            rooms.configure('room1', {'name': 'Changed again'}, yes=True)
        self.assertEqual(caught.exception.code, 'config_conflict')

    def test_room_can_keep_steam_group_in_public_mode(self):
        rooms.configure('room1', {'steam_group_id': '123', 'visibility': 'public'}, yes=True)
        text = Path(self.room['cfg']).read_text()
        self.assertIn('sv_steamgroup "123"', text)
        self.assertIn('sv_steamgroup_exclusive "0"', text)

    def test_room_dry_run_is_read_only(self):
        before = Path(self.room['cfg']).read_bytes()
        result = rooms.configure('room1', {'name': 'Changed'}, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(Path(self.room['cfg']).read_bytes(), before)

    def test_room_creation_checks_ports_and_stays_stopped(self):
        with patch.object(rooms.host, 'check_udp_available'):
            result = rooms.create('room2', 'room1', 27016, yes=True)
        self.assertFalse(result['starts_server'])
        self.assertIn('room2', rooms.all_rooms())
        with self.assertRaises(CliError):
            rooms.create('room3', 'room1', 27016, yes=True)


class PluginAndBackupTests(Sandbox):
    def test_core_dependency_cannot_be_disabled(self):
        with self.assertRaises(CliError) as caught:
            plugins.set_state(self.room, 'left4dhooks.smx', False, yes=True)
        self.assertEqual(caught.exception.code, 'protected_plugin')

    def test_plugin_toggle_preserves_bytes(self):
        path = plugins.directory(self.room) / 'plugins/example.smx'; path.parent.mkdir(parents=True); path.write_bytes(b'FFPS-example')
        plugins.set_state(self.room, path.name, False, yes=True)
        plugins.set_state(self.room, path.name, True, yes=True)
        self.assertEqual(path.read_bytes(), b'FFPS-example')

    def test_plugin_restore_removes_new_plugins_and_restores_disabled_state(self):
        path = plugins.directory(self.room) / 'plugins/disabled/example.smx'; path.parent.mkdir(parents=True); path.write_bytes(b'FFPS-example')
        report = backup.create(self.room, scope='plugins', yes=True)
        path.unlink()
        other = path.parent.parent / 'unexpected.smx'; other.write_bytes(b'FFPS-new')
        backup.restore(self.room, report['output'], yes=True)
        self.assertEqual(path.read_bytes(), b'FFPS-example')
        self.assertFalse(other.exists())

    def test_backup_checksum_prevents_any_restore(self):
        report = backup.create(self.room, scope='plugins', yes=True)
        with self.assertRaises(CliError):
            backup.restore(self.room, report['output'], expected='0' * 64, yes=True)

    def test_backup_inspect_never_returns_private_file_contents(self):
        Path(self.room['cfg']).write_text('rcon_password "SYNTHETIC_PRIVATE"')
        report = backup.create(self.room, yes=True)
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(report))
        manifest = backup.inspect(report['output'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(manifest))

    def test_restore_requires_all_shared_rooms_stopped(self):
        report = backup.create(self.room, scope='plugins', yes=True)
        self.service['MainPID'] = 123
        with patch.object(rooms, 'status', return_value={'service': self.service}), self.assertRaises(CliError):
            backup.restore(self.room, report['output'], yes=True)

    def test_admin_update_is_idempotent_and_removal_preserves_other_admin(self):
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:100', yes=True)
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:100', flags='b', yes=True)
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:200', yes=True)
        plugins.set_admin(self.room, 'sourcemod', 'STEAM_1:0:100', remove=True, yes=True)
        entries = plugins.admins(self.room, 'sourcemod')['entries']
        self.assertEqual(entries, ['"STEAM_1:0:200" "99:z"'])


class ContractAndNetworkTests(Sandbox):
    def test_json_confirmation_never_prompts_even_on_a_terminal(self):
        source = self.local({'scripts/mod.nut': b'mod'})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=AssertionError('JSON prompted')):
            code = cli.main(['addon', 'import', '--room', 'room1', '--file', str(source), '--json'])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], 'confirmation_required')
        self.assertFalse((rooms.game(self.room) / 'addons/test.vpk').exists())

    def test_independent_vpk_with_numeric_suffix_is_indexed(self):
        content.install_local(self.room, self.local({'maps/custom.bsp': b'map'}, 'campaign_001.vpk'), 'map', yes=True)
        self.assertEqual(content.inventory(self.room)['packages'][0]['filename'], 'campaign_001.vpk')

    def test_every_operations_help_parses_without_host_or_web(self):
        commands = [['capabilities', '--json'], ['room', 'list', '--json'], ['map', 'list'],
                    ['addon', 'list'], ['plugin', 'list'], ['admin', 'list'], ['network', 'status'],
                    ['task', 'list'], ['transfer', 'export', '--output', 'manifest.json'],
                    ['backup', 'inspect', '--file', 'backup.zip'], ['storage', 'usage'],
                    ['repair', 'mission-bom', 'mission.txt'], ['rcon', 'exec', '--command-file', 'command.txt']]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(cli.parser().parse_args(command))

    def test_capabilities_does_not_require_linux_or_a_server(self):
        args = cli.parser().parse_args(['capabilities', '--json'])
        with patch.object(base.host, 'require_platform', side_effect=AssertionError):
            result = cli.dispatch(args)
        self.assertFalse(result['web_required'])

    def test_supported_urls_and_redirects_reject_untrusted_destinations(self):
        transport.validate_url('https://cdn.steamusercontent.com/path')
        for url in ('file:///etc/passwd', 'http://localhost/file', 'https://steamcommunity.com.evil.test/file',
                    'https://SYNTHETIC_USER:SYNTHETIC_PASS@steamcommunity.com/file', 'https://steamcommunity.com:8080/file'):
            with self.subTest(url=url), self.assertRaises(CliError):
                transport.validate_url(url)

    def test_catalog_parts_and_collection_resolve_without_web_globals(self):
        self.assertEqual(catalog.strip_part_suffix('Campaign Part 2'), 'Campaign')
        with patch.object(catalog, 'known_install_ids', return_value=['12345', '67890']), \
             patch.object(catalog, 'resolve_single_workshop_download', side_effect=lambda item, kind: {'ok': True, 'workshop_id': item, 'file_size': 3}), \
             patch.object(catalog, 'fetch_workshop_meta', return_value={'title': 'Campaign'}):
            result = catalog.workshop_expected_parts('12345', 'map')
        self.assertEqual(len(result['parts']), 2)

    def test_mission_bom_repair_preserves_other_bytes(self):
        path = rooms.game(self.room) / 'missions/test.txt'; path.parent.mkdir(); path.write_bytes(b'\xef\xbb\xbf"mission" {}')
        maintenance.repair_mission(self.room, 'test.txt', yes=True)
        self.assertEqual(path.read_bytes(), b'"mission" {}')

    def test_hosts_repair_is_scoped_and_rejects_unrelated_domains(self):
        path = backup.NETWORK['hosts']; path.write_text('127.0.0.1 localhost\n')
        writes = maintenance.network_writes({'hosts': '1.2.3.4 cloud-3.steamusercontent.com'})
        self.assertIn(b'127.0.0.1 localhost', writes[0][1])
        with self.assertRaises(CliError):
            maintenance.network_writes({'hosts': '1.2.3.4 example.com'})

    def test_task_refuses_shell_and_recursive_task_commands(self):
        for args in (['sh', '-c', 'anything'], ['task', 'submit'], ['map', 'list']):
            with self.subTest(args=args), self.assertRaises(CliError):
                tasks.submit(args, yes=True)

    def test_dead_worker_is_interrupted_and_cannot_signal_reused_pid(self):
        task_id = 'a' * 32
        base.save(tasks.path(task_id), {'id': task_id, 'status': 'running', 'pid': 123, 'start_ticks': '10'})
        with patch.object(tasks, 'ticks', return_value='11'), patch.object(tasks.os, 'kill') as kill:
            self.assertEqual(tasks.get(task_id)['status'], 'interrupted')
            tasks.cancel(task_id, yes=True)
        kill.assert_not_called()

    def test_custom_map_validation_does_not_allow_cfg_injection(self):
        model.Config(game_dir=str(self.game), map='glubtastic5_1').validate()
        for name in ('map;quit', '../map', 'map\nquit', 'map/part'):
            with self.subTest(name=name), self.assertRaises(CliError):
                model.Config(game_dir=str(self.game), map=name).validate()


if __name__ == '__main__':
    unittest.main()
