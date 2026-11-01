"""Player-facing sharing, authenticated policies and supervised maintenance."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import unittest
from unittest.mock import patch

from test_operations import Sandbox
from l4d2_cli import cli, manager, storage
from l4d2_cli.common import CliError, result
from l4d2_cli.ops import access, backup, base, catalog, content, health, joining, plugins, rooms, schedules, sharing, updates

REAL_JOB_LOCK = schedules.job_lock


class JoiningTests(Sandbox):
    def test_uses_running_port_and_does_not_share_secrets(self):
        report = {'game_ready': True, 'running_endpoint': {'port': 27025},
                  'query': {'map': 'c2m1_highway'}, 'configuration_pending': True}
        with patch.object(rooms, 'status', return_value=report):
            invite = joining.invite(self.room, 'play.example.com')
        self.assertIn('connect play.example.com:27025', invite['share_text'])
        self.assertIn('c2m1_highway', invite['share_text'])
        self.assertNotIn('27015', invite['share_text'])
        self.assertEqual(invite['client_join'], 'unverified')
        self.assertEqual(invite['public_reachability'], 'unverified')

    def test_group_instructions_do_not_promise_direct_join_or_whitelist(self):
        room = {**self.room, 'visibility': 'group', 'steam_group_id': '12345'}
        with patch.object(rooms, 'status', return_value={}):
            report = joining.invite(room, 'play.example.com')
        self.assertNotIn('connect play', report['share_text'])
        self.assertIn('不等于严格好友白名单', report['share_text'])

    def test_endpoint_rejects_console_injection_credentials_and_wildcard(self):
        for value in ['0.0.0.0', '224.0.0.1', '255.255.255.255', 'a;quit', 'https://x',
                      'user:secret@x', 'a:27015', 'a\nquit', '-option', '999.1.1.1', 'a..b']:
            with self.subTest(value=value), self.assertRaises(CliError):
                joining.address(value)

    def test_remote_query_never_proves_client_join(self):
        with patch.object(joining.a2s, 'query', return_value={'app_id': 550}):
            report = joining.probe('203.0.113.8', 27025)
        self.assertTrue(report['query_reachable'])
        self.assertEqual(report['client_join'], 'unverified')

    def test_probe_failure_is_actionable_one_json_envelope_without_linux(self):
        with patch.object(joining.a2s, 'query', side_effect=CliError('Timeout', 'query_timeout')), \
                patch.object(cli.host, 'require_platform', side_effect=AssertionError('Linux not required')), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['network', 'probe', '--address', 'play.example.com', '--json'])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 4)
        self.assertFalse(report['ok'])
        self.assertFalse(report['data']['query_reachable'])
        self.assertGreater(len(report['data']['next_steps']), 1)

    def test_address_failure_does_not_hide_local_service_success(self):
        report = {'game_ready': True, 'service': {'ActiveState': 'active'}, 'query': {'app_id': 550},
                  'port_owned_by_service': True, 'configuration_pending': False}
        with patch.object(rooms, 'status', return_value=report), \
                patch.object(joining, 'probe', return_value={'query_reachable': False}):
            data = joining.check(self.room, 'play.example.com')
        self.assertFalse(data['healthy'])
        self.assertTrue(data['checks'][0]['passed'])
        self.assertEqual(data['public_reachability'], 'unverified')

    def test_human_invitation_is_copyable_plain_text(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result('l4d2 room invite', {'share_text': '好友加入\nconnect play.example.com:27015'})
        self.assertEqual(output.getvalue(), '好友加入\nconnect play.example.com:27015\n')


class SharingTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(catalog, 'initialize'))
        self.stack.enter_context(patch.object(content, 'inventory', return_value={'packages': [], 'errors': []}))

    def node(self, title, children=(), verified=True, collection=False):
        return {'title': title, 'children': list(children), 'dependencies_verified': verified, 'collection': collection}

    def test_recursive_parts_dependencies_cycles_and_deduplication(self):
        nodes = {'1000': self.node('合集', ['2000', '3000'], collection=True),
                 '2000': self.node('分包 1', ['3000', '4000']), '3000': self.node('分包 2', ['4000']),
                 '4000': self.node('依赖', ['2000'])}
        with patch.object(sharing, 'details', side_effect=lambda key: nodes[key].copy()) as fetch:
            report = sharing.share(self.room, '1000')
        self.assertEqual([i['id'] for i in report['items']], ['1000', '2000', '3000', '4000'])
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(report['complete'])
        self.assertIn('https://steamcommunity.com/sharedfiles/filedetails/?id=4000', report['share_text'])

    def test_hidden_dependency_and_graph_limit_are_not_complete(self):
        with patch.object(sharing, 'details', side_effect=[self.node('root', ['2000']), CliError('hidden')]):
            report = sharing.share(self.room, '1000')
        self.assertFalse(report['complete'])
        self.assertIn('2000', report['share_text'])
        with patch.object(sharing, 'MAX_ITEMS', 1), patch.object(sharing, 'details', return_value=self.node('root', ['2000'])):
            self.assertFalse(sharing.share(self.room, '1000')['complete'])

    def test_offline_uses_recorded_all_parts_without_network(self):
        inventory = {'errors': [], 'packages': [{'filename': 'part1.vpk', 'kind': 'map', 'state': 'enabled',
                      'source': 'workshop', 'id': '1000', 'install_ids': ['2000', '3000']}]}
        with patch.object(content, 'inventory', return_value=inventory), patch.object(sharing, 'details') as fetch:
            report = sharing.share(self.room, offline=True)
        fetch.assert_not_called()
        self.assertEqual({i['id'] for i in report['items']}, {'1000', '2000', '3000'})
        self.assertFalse(report['complete'])

    def test_missing_api_key_does_not_claim_dependencies_verified(self):
        payload = {'response': {'publishedfiledetails': [{'result': 1, 'consumer_app_id': 550, 'title': 'Map'}]}}
        with patch.object(catalog, 'STEAM_WEB_API_KEY', ''), patch.object(catalog, 'http_json', return_value=payload):
            self.assertFalse(sharing.share(self.room, '1000')['complete'])

    def test_other_game_is_rejected(self):
        payload = {'response': {'publishedfiledetails': [{'result': 1, 'consumer_app_id': 730}]}}
        with patch.object(catalog, 'STEAM_WEB_API_KEY', ''), patch.object(catalog, 'http_json', return_value=payload):
            with self.assertRaises(CliError) as caught:
                sharing.details('1000')
        self.assertEqual(caught.exception.code, 'wrong_workshop_game')

    def test_collection_items_then_each_required_child_are_queried(self):
        def fetch(url, data=None):
            if url == catalog.STEAM_COLLECTION_URL:
                return {'response': {'collectiondetails': [{'result': 1, 'children': [{'publishedfileid': '2000'}]}]}}
            key = '1000' if '1000' in url else '2000'
            return {'response': {'publishedfiledetails': [{'result': 1, 'consumer_app_id': 550,
                      'file_type': 2 if key == '1000' else 0, 'title': key, 'children': []}]}}
        with patch.object(catalog, 'STEAM_WEB_API_KEY', 'SYNTHETIC'), patch.object(catalog, 'http_json', side_effect=fetch):
            report = sharing.share(self.room, '1000')
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['items']), 2)
        self.assertNotIn('SYNTHETIC', json.dumps(report))

    def test_authenticated_field_names_and_truncated_children(self):
        item = {'result': 1, 'consumer_appid': 550, 'title': 'Map', 'num_children': 2,
                'children': [{'publishedfileid': '2000'}]}
        with patch.object(catalog, 'STEAM_WEB_API_KEY', 'SYNTHETIC'), \
                patch.object(catalog, 'http_json', return_value={'response': {'publishedfiledetails': [item]}}):
            report = sharing.details('1000')
        self.assertFalse(report['dependencies_verified'])
        self.assertEqual(report['children'], ['2000'])

    def test_id_and_file_selection_are_mutually_exclusive(self):
        with self.assertRaises(CliError):
            cli.parser().parse_args(['map', 'share', '--id', '1000', '--filename', 'one.vpk'])

    def test_local_gamemaps_and_disabled_files(self):
        inventory = {'errors': [], 'packages': [{'filename': 'custom.vpk', 'kind': 'map', 'state': 'enabled',
                      'source': 'gamemaps', 'id': '1234', 'sha256': 'abc'},
                     {'filename': 'off.vpk', 'kind': 'map', 'state': 'disabled'}]}
        with patch.object(content, 'inventory', return_value=inventory):
            report = sharing.share(self.room, offline=True)
            self.assertIn('https://www.gamemaps.com/details/1234', report['share_text'])
            self.assertNotIn('off.vpk', report['share_text'])
            with self.assertRaises(CliError):
                sharing.share(self.room, filenames=['off.vpk'])
        self.assertFalse(report['complete'])


class AccessTests(Sandbox):
    def configure(self):
        compiler = plugins.directory(self.room) / 'scripting/spcomp64'
        compiler.parent.mkdir(parents=True, exist_ok=True)
        compiler.write_bytes(b'compiler')
        output = self.tmp / 'access.smx'
        output.write_bytes(b'FFPScompiled')
        ids = self.tmp / 'friends.txt'
        ids.write_text('76561198000000001\n76561198000000002\n')
        with patch.object(access, 'stopped'), patch.object(plugins, 'compile_source', return_value={'output': str(output)}):
            return access.configure(self.room, 'friends', str(ids), yes=True)

    def test_steam_ids_and_empty_or_group_input(self):
        ids = self.tmp / 'ids.txt'
        ids.write_text('# friends\n76561198000000001\n76561198000000001\n')
        self.assertEqual(access.ids_from_file(ids), ['76561198000000001'])
        for invalid in ['', '12345', 'https://steamcommunity.com/id/person', 'STEAM_1:0:5', '99999999999999999']:
            ids.write_text(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(CliError):
                access.ids_from_file(ids)

    def test_policy_receipt_changed_hash_stale_and_port_mismatch(self):
        self.configure()
        self.assertTrue(access.status(self.room)['files_match'])
        self.assertFalse(access.ready(self.room))
        record = base.read(access.state_path(self.room))
        receipt = plugins.directory(self.room) / 'data/l4d2_cli_access_27015.txt'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(record['generation'] + '\nfriends\n' + str(int(time.time())) + '\n')
        self.assertTrue(access.ready(self.room))
        self.assertFalse(access.ready(self.room, time.time() + 1))
        self.assertFalse(access.status({**self.room, 'port': 27025})['files_match'])
        access.policy_path(self.room).write_text('public\nwrong\n')
        with self.assertRaises(CliError):
            access.guard(self.room)

    def test_shared_plugin_without_policy_cannot_start(self):
        plugin = plugins.directory(self.room) / 'plugins' / access.PLUGIN
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b'FFPS')
        with self.assertRaises(CliError):
            access.guard(self.room)

    def test_no_framework_is_actionable_and_read_only(self):
        with self.assertRaises(CliError) as caught:
            access.configure(self.room, 'public', dry_run=True)
        self.assertEqual(caught.exception.code, 'sourcemod_required')
        self.assertFalse(access.state_path(self.room).exists())

    def test_any_shared_running_room_blocks_changes(self):
        self.service.update(MainPID=123, ActiveState='active')
        with self.assertRaises(CliError) as caught:
            access.stopped(self.room)
        self.assertEqual(caught.exception.code, 'room_running')

    def test_access_plugin_cannot_be_disabled(self):
        with self.assertRaises(CliError) as caught:
            plugins.set_state(self.room, access.PLUGIN, False, dry_run=True)
        self.assertEqual(caught.exception.code, 'protected_plugin')

    def test_shared_rooms_get_explicit_independent_policies(self):
        other = {**self.room, 'id': 'room2', 'port': 27025, 'service': 'l4d2-room2.service',
                 'unit': str(base.UNITS / 'l4d2-room2.service'),
                 'cfg': str(rooms.game(self.room) / 'cfg/room2.cfg')}
        for field in ('cfg', 'unit'):
            Path(other[field]).write_text('')
        base.save(base.ROOMS, {'version': 1, 'rooms': {'room1': self.room, 'room2': other}})
        self.configure()
        self.assertEqual(access.status(self.room)['mode'], 'friends')
        self.assertEqual(access.status(other)['mode'], 'public')
        self.assertIn('76561198000000001', access.policy_path(self.room).read_text())
        self.assertNotIn('76561198000000001', access.policy_path(other).read_text())
        access.policy_path(other).write_text('public\nchanged\n')
        with self.assertRaises(CliError):
            self.configure()

    def test_backup_restore_requires_policy_reconciliation(self):
        self.configure()
        snapshot = backup.create(self.room, yes=True)
        with patch.object(backup, 'run'):
            report = backup.restore(self.room, snapshot['output'], yes=True)
        self.assertTrue(report['access_requires_reconfigure'])
        self.assertFalse(access.status(self.room)['files_match'])
        with self.assertRaises(CliError):
            access.guard(self.room)


class UpdateTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(access, 'stopped', return_value=['room1']))
        self.stack.enter_context(patch.object(updates.packages, 'game_build', return_value='old'))
        self.stack.enter_context(patch.object(updates.host, 'own_tree'))
        self.stack.enter_context(patch('l4d2_cli.common.run', return_value=subprocess.CompletedProcess([], 0, 'all found')))
        def prepare(source, home, timeout):
            home.mkdir(parents=True)
            return home / 'steamcmd.sh'
        self.stack.enter_context(patch.object(updates.steam, 'prepare_tool', side_effect=prepare))

    def test_preview_does_not_backup_or_download(self):
        with patch.object(backup, 'create') as snapshot, patch.object(updates.steam, 'download_game') as download:
            plan = updates.update(self.room, dry_run=True)
        snapshot.assert_not_called()
        download.assert_not_called()
        self.assertFalse(plan['plan']['starts_server'])

    def test_success_preserves_configuration_and_stays_stopped(self):
        with patch.object(updates.steam, 'download_game', return_value={'game_build': 'new'}) as download:
            report = updates.update(self.room, yes=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['after_build'], 'new')
        self.assertFalse(report['started'])
        self.assertEqual(download.call_args.kwargs['run_user'], 'steam')
        self.assertTrue(Path(report['backup']['output']).is_file())
        updates.guard(self.room['game_dir'])

    def test_failed_update_blocks_start_and_retains_backup(self):
        with patch.object(updates.steam, 'download_game', side_effect=CliError('failed', 'steam_download_failed')):
            with self.assertRaises(CliError):
                updates.update(self.room, yes=True)
        record = base.read(updates.state_path(self.room['game_dir']))
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(Path(record['backup']['output']).is_file())
        with self.assertRaises(CliError) as caught:
            updates.guard(self.room['game_dir'])
        self.assertEqual(caught.exception.code, 'update_incomplete')
        with patch.object(updates.steam, 'download_game', return_value={'game_build': 'new'}):
            updates.update(self.room, yes=True)
        updates.guard(self.room['game_dir'])

    def test_main_start_blocks_incomplete_update_before_runtime_or_service_calls(self):
        base.save(updates.state_path(self.room['game_dir']), {'status': 'failed'})
        with patch.object(manager.storage, 'load_secrets', return_value={}), \
                patch.object(manager, 'runtime_check') as runtime, patch.object(manager.host, 'systemctl') as service:
            with self.assertRaises(CliError) as caught:
                manager._start(manager.Config(game_dir=self.room['game_dir']), {}, time.monotonic() + 30)
        self.assertEqual(caught.exception.code, 'update_incomplete')
        runtime.assert_not_called()
        service.assert_not_called()

    def test_detects_steam_overwriting_private_configuration(self):
        def download(*args, **kwargs):
            Path(self.room['cfg']).write_text('changed')
            return {'game_build': 'new'}
        with patch.object(updates.steam, 'download_game', side_effect=download):
            with self.assertRaises(CliError) as caught:
                updates.update(self.room, yes=True)
        self.assertEqual(caught.exception.code, 'update_state_changed')


class ScheduleTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch('l4d2_cli.web.manager.trusted_cli', return_value='/usr/local/bin/l4d2'))
        self.stack.enter_context(patch.object(schedules, 'run', return_value=subprocess.CompletedProcess([], 0, 'ActiveState=active')))
        self.stack.enter_context(patch.object(schedules, 'job_lock', side_effect=lambda _: contextlib.nullcontext()))

    def test_calendar_rejects_injection_and_invalid_intervals(self):
        for value in ['24:00', '4:00', '04:60', '04:00\nExecStart=/bin/sh']:
            with self.assertRaises(CliError):
                schedules.calendar(value)
        for value in [0, -1, 1441, True]:
            with self.assertRaises(CliError):
                schedules.calendar(every=value)

    def test_schedule_add_preview_and_owned_timer(self):
        report = schedules.add('nightly', self.room, 'backup', dry_run=True)
        self.assertTrue(report['dry_run'])
        self.assertFalse(schedules.path('nightly').exists())
        schedules.add('nightly', self.room, 'backup', yes=True)
        record = schedules.get('nightly')
        schedules.verify_units(record)
        text = (base.UNITS / record['service']).read_text()
        self.assertIn('schedule _run --id nightly --json', text)
        self.assertNotIn('/bin/sh', text)
        self.assertIn('Persistent=true', (base.UNITS / record['timer']).read_text())
        with self.assertRaises(CliError):
            schedules.add('nightly', self.room, 'health', yes=True)

    def test_worker_failure_is_saved_and_not_claimed_success(self):
        schedules.add('healthcheck', self.room, 'health', yes=True)
        with patch.object(health, 'check', return_value={'healthy': False, 'checks': []}):
            with self.assertRaises(CliError):
                schedules.worker('healthcheck')
        last = schedules.get('healthcheck')['last_run']
        self.assertFalse(last['ok'])
        self.assertEqual(last['error'], 'unhealthy')

    def test_cancelled_job_records_failure(self):
        schedules.add('healthcheck', self.room, 'health', yes=True)
        with patch.object(health, 'check', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                schedules.worker('healthcheck')
        last = schedules.get('healthcheck')['last_run']
        self.assertFalse(last['ok'])
        self.assertEqual(last['error'], 'cancelled')

    def test_snapshot_conflict_never_rotates_an_old_backup(self):
        target = self.tmp / 'snapshot.zip'
        original = backup.zipfile.ZipFile.write
        def changed(archive, filename, arcname=None, *args, **kwargs):
            if str(arcname).endswith('cfg/server.cfg'):
                Path(filename).write_text('concurrent change')
            return original(archive, filename, arcname, *args, **kwargs)
        with patch.object(backup.zipfile.ZipFile, 'write', changed):
            with self.assertRaises(CliError) as caught:
                backup.create(self.room, str(target), yes=True)
        self.assertEqual(caught.exception.code, 'snapshot_conflict')
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name('.snapshot.zip.partial').exists())

    @unittest.skipUnless(os.name == 'posix', 'Real Linux flock semantics')
    def test_real_lock_rejects_overlapping_workers(self):
        schedules.add('nightly', self.room, 'backup', yes=True)
        with REAL_JOB_LOCK('nightly'):
            with self.assertRaises(CliError) as caught:
                with REAL_JOB_LOCK('nightly'):
                    self.fail('overlapping lock acquired')
        self.assertEqual(caught.exception.code, 'schedule_running')

    @unittest.skipUnless(os.name == 'posix' and shutil.which('systemd-analyze'), 'Linux systemd unit parser')
    def test_generated_units_pass_real_systemd_verification(self):
        with patch('l4d2_cli.web.manager.trusted_cli', return_value='/bin/true'):
            schedules.add('nightly', self.room, 'backup', yes=True)
        record = schedules.get('nightly')
        checked = subprocess.run(['systemd-analyze', 'verify', str(base.UNITS / record['service']),
                                  str(base.UNITS / record['timer'])], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_retention_deletes_only_this_jobs_unchanged_backups(self):
        schedules.add('nightly', self.room, 'backup', keep=1, yes=True)
        first = schedules.worker('nightly')
        output = Path(first['result']['output'])
        self.assertTrue(output.exists())
        second = schedules.worker('nightly')
        self.assertFalse(output.exists())
        self.assertTrue(Path(second['result']['output']).exists())
        self.assertEqual(len(schedules.get('nightly')['history']), 1)

    def test_changed_backup_is_preserved(self):
        folder = base.ROOT / 'scheduled-backups/nightly'
        folder.mkdir(parents=True)
        one, two = folder / ('a' * 32 + '.zip'), folder / ('b' * 32 + '.zip')
        one.write_bytes(b'changed')
        two.write_bytes(b'new')
        record = {'id': 'nightly', 'keep': 1, 'history': [{'output': str(one), 'sha256': 'wrong'},
                                                        {'output': str(two), 'sha256': storage.sha256(two)}]}
        with self.assertRaises(CliError):
            schedules.prune(record)
        self.assertTrue(one.exists())

    def test_changed_timer_is_not_removed(self):
        schedules.add('nightly', self.room, 'backup', yes=True)
        record = schedules.get('nightly')
        (base.UNITS / record['timer']).write_text('foreign')
        with self.assertRaises(CliError):
            schedules.remove('nightly', yes=True)
        self.assertTrue(schedules.path('nightly').exists())

    def test_remove_keeps_archives(self):
        schedules.add('nightly', self.room, 'backup', yes=True)
        report = schedules.worker('nightly')
        schedules.remove('nightly', yes=True)
        self.assertTrue(Path(report['result']['output']).exists())
        self.assertFalse(schedules.path('nightly').exists())

    def test_health_access_failure_and_disk_checks(self):
        with patch.object(rooms, 'status', return_value={'game_ready': True}), \
                patch.object(access, 'status', return_value={'configured': True, 'runtime_verified': False}):
            report = health.check(self.room)
        self.assertFalse(report['healthy'])
        self.assertFalse(next(i for i in report['checks'] if i['code'] == 'access')['passed'])
