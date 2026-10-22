"""Bootstrap workflow regressions; no real downloads, system services or credentials."""
import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from l4d2_cli import cli, onboarding as guide
from l4d2_cli.common import CliError
from l4d2_cli.model import Config
from test_install_recovery import directory


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(directory()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.mocks = {}
        for owner, name in [(guide.host, 'require_platform'), (guide.host, 'require_root'),
                            (guide.host, 'check_udp_available'), (guide.host, 'service_info'),
                            (guide.installer, 'registered_config'), (guide.installer, 'install'),
                            (guide.manager, 'installed'), (guide.manager, 'configure'),
                            (guide.manager, 'lifecycle'), (guide.manager, 'status'),
                            (guide.storage, 'load_secrets')]:
            self.mocks[name] = self.stack.enter_context(patch.object(owner, name))
        self.mocks['registered_config'].return_value = self.config()
        self.mocks['installed'].return_value = (self.config(), {'status': 'installed'})
        self.mocks['service_info'].return_value = {'ActiveState': 'inactive', 'MainPID': 0}
        self.mocks['load_secrets'].return_value = {}
        self.plan = {'installed': False, 'source': 'online', 'free_bytes': 30 * 1024**3,
                     'minimum_free_bytes': 20 * 1024**3, 'missing_dependencies': [],
                     'network': {'required_failures': 0, 'checks': []}}
        self.mocks['install'].return_value = {'plan': self.plan}
        self.mocks['status'].return_value = {'game_ready': True}

    def run_setup(self, *arguments):
        return guide.setup(cli.parser().parse_args(['setup', *arguments]))

    def config(self, **values):
        return Config(game_dir=str(self.tmp / 'game'), **values)

    def test_preview_is_readonly_and_uses_requested_settings(self):
        data = self.run_setup('--dry-run', '--name', 'Friday Team', '--port', '27018', '--no-start')
        self.assertEqual(data['plan']['configuration']['port'], 27018)
        self.assertEqual(data['plan']['configuration']['name'], 'Friday Team')
        self.assertFalse(data['plan']['starts_server'])
        self.assertFalse(data['plan']['opens_firewall'])
        self.assertEqual(self.mocks['install'].call_count, 1)
        self.assertTrue(self.mocks['install'].call_args.kwargs['dry_run'])
        for name in ('configure', 'lifecycle', 'require_root'):
            self.mocks[name].assert_not_called()

    def test_success_uses_existing_operations_and_preserves_secrets(self):
        self.mocks['load_secrets'].return_value = {'rcon_password': 'SYNTHETIC_PRIVATE_SECRET'}
        data = self.run_setup('--yes', '--name', 'Friday Team')
        self.assertEqual(data['completed_steps'], ['install', 'configure', 'start'])
        self.assertEqual(data['client_join'], 'unverified')
        self.assertEqual(self.mocks['configure'].call_args.args[1], self.mocks['load_secrets'].return_value)
        self.assertNotIn('SYNTHETIC_PRIVATE_SECRET', json.dumps(data))
        self.assertEqual(self.mocks['lifecycle'].call_args.args, ('start',))

    def test_first_install_applies_chosen_port_without_false_conflict(self):
        chosen = self.config(port=27018)
        self.plan['configuration'] = asdict(chosen)
        self.mocks['installed'].return_value = (chosen, {'status': 'installed'})
        self.run_setup('--yes', '--port', '27018')
        self.assertEqual(self.mocks['install'].call_args.kwargs['initial_config'].port, 27018)
        self.mocks['lifecycle'].assert_called_once()

    def test_install_plan_uses_first_choices_but_preserves_registered_retry(self):
        chosen = self.config(port=27018, name='Friday Team')
        with patch.object(guide.storage, 'read_json', return_value={}) as state, \
                patch.object(guide.installer, 'validate_target'), \
                patch.object(guide.host, 'missing_dependencies', return_value=[]), \
                patch.object(guide.host, 'platform_report', return_value={'supported': True}), \
                patch.object(guide.network, 'check', return_value={'required_failures': 0}):
            plan, selected, _ = guide.installer.install_plan(chosen.game_dir, guide.Source(), 30, chosen)
            self.assertEqual(selected.port, 27018)
            self.assertEqual(plan['configuration']['name'], 'Friday Team')
            state.return_value = {'status': 'failed'}
            _, selected, _ = guide.installer.install_plan(chosen.game_dir, guide.Source(), 30, chosen)
            self.assertEqual(selected.port, 27015)

    def test_no_start_saves_config_without_starting(self):
        self.mocks['status'].return_value = {'game_ready': False, 'installation': 'installed'}
        data = self.run_setup('--yes', '--no-start')
        self.assertFalse(data['started'])
        self.mocks['lifecycle'].assert_not_called()
        self.assertIn('sudo l4d2 server start', guide.format_report(data))

    def test_installed_retry_keeps_existing_configuration(self):
        current = self.config(name='Saved Team', port=27019, map='c2m1_highway')
        self.mocks['registered_config'].return_value = current
        self.mocks['installed'].return_value = (current, {'status': 'installed'})
        self.plan['installed'] = True
        self.run_setup('--yes')
        self.assertEqual(self.mocks['configure'].call_args.args[0].name, 'Saved Team')
        self.assertEqual(self.mocks['configure'].call_args.args[0].port, 27019)
        self.assertTrue(self.mocks['configure'].call_args_list[0].kwargs['dry_run'])

    def test_running_room_is_never_modified(self):
        self.mocks['service_info'].return_value = {'MainPID': 42, 'ActiveState': 'active'}
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.code, 'setup_running')
        self.mocks['install'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_room_started_during_download_is_not_reconfigured(self):
        self.mocks['service_info'].side_effect = [{'MainPID': 0}, {'MainPID': 42}]
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.details['completed_steps'], ['install'])
        self.mocks['configure'].assert_not_called()

    def test_configuration_changed_during_download_is_preserved(self):
        self.mocks['installed'].return_value = (self.config(name='Changed by another operator'), {})
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.code, 'configuration_changed')
        self.mocks['configure'].assert_not_called()

    def test_bad_input_fails_before_installation(self):
        for arguments in [('--name', '中文'), ('--map', 'unknown_custom_map'), ('--port', '99999'),
                          ('--visibility', 'group'), ('--offline',)]:
            with self.subTest(arguments=arguments), self.assertRaises(CliError):
                self.run_setup('--yes', *arguments)
        self.mocks['install'].assert_not_called()

    def test_network_failure_stops_before_mutation(self):
        self.plan['network'] = {'required_failures': 1, 'checks': [], 'next_steps': ['Check the download network.']}
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.details['stage'], 'preflight')
        self.assertEqual(self.mocks['install'].call_count, 1)
        self.mocks['configure'].assert_not_called()

    def test_space_and_offline_dependency_failures_are_preflight(self):
        self.plan['free_bytes'] = 1
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.code, 'insufficient_space')
        self.plan['free_bytes'] = 30 * 1024**3
        self.plan['missing_dependencies'] = ['lib32gcc-s1']
        archive = self.tmp / 'server.tar.gz'
        archive.write_bytes(b'synthetic fixture; not extracted by these tests')
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes', '--server-archive', str(archive), '--offline')
        self.assertEqual(raised.exception.code, 'missing_dependencies')
        self.mocks['configure'].assert_not_called()

    def test_auth_failure_explains_normal_user_recovery(self):
        self.mocks['install'].side_effect = [{'plan': self.plan}, CliError('No subscription', 'steam_auth_required')]
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.details['stage'], 'install')
        self.assertTrue(any('普通用户' in item for item in raised.exception.details['next_steps']))
        self.mocks['configure'].assert_not_called()

    def test_failed_final_readiness_is_not_reported_as_success(self):
        self.mocks['status'].return_value = {'game_ready': False}
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.code, 'startup_not_ready')
        self.assertEqual(raised.exception.details['stage'], 'status')

    def test_cancel_during_install_reports_retained_steps(self):
        self.mocks['install'].side_effect = [{'plan': self.plan}, KeyboardInterrupt()]
        with self.assertRaises(CliError) as raised:
            self.run_setup('--yes')
        self.assertEqual(raised.exception.exit_code, 130)
        self.assertEqual(raised.exception.details['stage'], 'install')

    def test_json_without_yes_never_prompts_or_mutates(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('builtins.input') as prompt:
            code = cli.main(['setup', '--json'])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], 'confirmation_required')
        prompt.assert_not_called()
        self.assertEqual(self.mocks['install'].call_count, 1)
        self.mocks['configure'].assert_not_called()

    def test_json_success_is_a_single_envelope(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['setup', '--yes', '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_declined_confirmation_has_no_mutations(self):
        with patch.object(guide.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['', '', '', '', '', '', 'online', 'y', 'n']):
            with self.assertRaises(CliError) as raised:
                self.run_setup()
        self.assertEqual(raised.exception.code, 'declined')
        self.mocks['configure'].assert_not_called()
        self.assertEqual(self.mocks['install'].call_count, 1)

    def test_interactive_campaign_selection_and_defaults(self):
        arguments = cli.parser().parse_args(['setup'])
        with patch.object(guide.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['', 'Friday', '27018', '2', 'hard', 'public', 'online', 'n']):
            config = guide.collect(arguments, self.config())
        self.assertEqual((config.map, config.port, config.name), ('c2m1_highway', 27018, 'Friday'))
        self.assertTrue(arguments.no_start)


if __name__ == '__main__':
    unittest.main()
