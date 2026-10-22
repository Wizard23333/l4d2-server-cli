"""Local Agent Skill lifecycle; never access real agent profiles or a server."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from l4d2_cli import agent_skill as skill, cli, host
from l4d2_cli.common import CliError
from test_install_recovery import directory


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = directory()
        self.root = Path(self.temp.__enter__())
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.temp.__exit__(None, None, None)

    def options(self, agent='codex'):
        return dict(agent=agent, project=str(self.project))

    def test_all_native_adapters_install_the_same_single_skill(self):
        for agent, adapter in skill.ADAPTERS.items():
            with self.subTest(agent=agent):
                result = skill.install(**self.options(agent), yes=True)
                path = Path(result['path'])
                self.assertEqual(result['status'], 'current')
                self.assertEqual(result['scope'], 'project')
                self.assertTrue(path.is_relative_to(self.project) if hasattr(path, 'is_relative_to')
                                else str(path).startswith(str(self.project)))
                expected = skill.rendered(adapter)
                self.assertEqual(path.read_bytes(), expected)
                if adapter.rule:
                    self.assertEqual(path.suffix, '.md')
                    self.assertIn(b'alwaysApply: false\n---\n', expected)
                else:
                    self.assertEqual(path.name, 'SKILL.md')
                self.assertEqual(skill.status(**self.options(agent))['status'], 'current')

    def test_user_project_defaults_and_override(self):
        path, _, scope = skill.destination('codex')
        self.assertEqual((path, scope), (self.home / '.agents/skills/l4d2-manager/SKILL.md', 'user'))
        path, _, scope = skill.destination('claude-code', project=str(self.project))
        self.assertEqual((path, scope), (self.project / '.claude/skills/l4d2-manager/SKILL.md', 'project'))
        override = self.root / 'custom skills'
        path, _, _ = skill.destination('codex', target_dir=str(override))
        self.assertEqual(path, override / 'l4d2-manager/SKILL.md')
        with self.assertRaises(CliError) as caught:
            skill.destination('continue')
        self.assertEqual(caught.exception.code, 'unsupported_skill_scope')
        with self.assertRaises(CliError):
            skill.destination('codex', scope='user', project=str(self.project))
        with self.assertRaises(CliError):
            skill.list_agents(scope='user', project=str(self.project))

    def test_user_install_and_custom_root_stay_in_isolated_profile(self):
        installed = skill.install('cursor', yes=True)
        self.assertEqual(Path(installed['path']), self.home / '.cursor/skills/l4d2-manager/SKILL.md')
        custom = self.root / 'legacy root'
        installed = skill.install('codex', target_dir=str(custom), yes=True)
        self.assertEqual(Path(installed['path']), custom / 'l4d2-manager/SKILL.md')
        self.assertFalse((self.home / '.agents').exists())

    def test_dry_run_status_and_missing_project_never_create_files(self):
        self.assertEqual(skill.status(**self.options())['status'], 'not_installed')
        result = skill.install(**self.options(), dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(list(self.project.iterdir()), [])
        with self.assertRaises(CliError):
            skill.destination('codex', project=str(self.root / 'missing'))
        self.assertFalse((self.root / 'missing').exists())

    def test_no_confirmation_does_not_write(self):
        with patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(CliError) as caught:
                skill.install(**self.options())
        self.assertEqual(caught.exception.code, 'confirmation_required')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_install_is_idempotent_and_update_uses_content_not_cli_version(self):
        result = skill.install(**self.options(), yes=True)
        original = Path(result['path']).read_bytes()
        self.assertTrue(skill.install(**self.options(), yes=True)['unchanged'])
        with patch.object(skill, 'bundled', return_value=original + b'\nNew operational guidance.\n'):
            self.assertEqual(skill.status(**self.options())['status'], 'outdated')
            self.assertEqual(skill.install(**self.options(), update=True, yes=True)['status'], 'current')
        self.assertEqual(skill.status(**self.options())['status'], 'outdated')

    def test_unmanaged_conflict_preserves_content_and_force_makes_backup(self):
        path, receipt, _ = skill.destination(**self.options())
        path.parent.mkdir(parents=True)
        original = b'My independently maintained instructions.\n'
        path.write_bytes(original)
        with self.assertRaises(CliError) as caught:
            skill.install(**self.options(), yes=True)
        self.assertEqual(caught.exception.code, 'skill_conflict')
        self.assertFalse(receipt.exists())
        self.assertEqual(path.read_bytes(), original)
        result = skill.install(**self.options(), force=True, yes=True)
        self.assertEqual(Path(result['backups'][0]).read_bytes(), original)
        self.assertEqual(path.read_bytes(), skill.bundled())

    def test_update_and_uninstall_do_not_claim_unmanaged_files(self):
        path, _, _ = skill.destination(**self.options())
        path.parent.mkdir(parents=True)
        path.write_bytes(skill.bundled())
        for operation in (lambda: skill.install(**self.options(), update=True, force=True, yes=True),
                          lambda: skill.uninstall(**self.options(), force=True, yes=True)):
            with self.assertRaises(CliError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'skill_not_managed')
        # Explicit install can register an identical manual copy without replacement.
        self.assertEqual(skill.install(**self.options(), yes=True)['status'], 'current')

    def test_modified_uninstall_preserves_backup_and_other_files(self):
        result = skill.install(**self.options(), yes=True)
        path = Path(result['path'])
        path.write_bytes(b'User edits\n')
        other = path.parent / 'notes.txt'
        other.write_bytes(b'User notes')
        self.assertEqual(skill.status(**self.options())['status'], 'modified')
        with self.assertRaises(CliError):
            skill.uninstall(**self.options(), yes=True)
        result = skill.uninstall(**self.options(), force=True, yes=True)
        self.assertEqual(Path(result['backups'][0]).read_bytes(), b'User edits\n')
        self.assertEqual(other.read_bytes(), b'User notes')
        self.assertFalse(path.exists())
        self.assertTrue(skill.uninstall(**self.options(), yes=True)['unchanged'])

    def test_invalid_receipt_is_not_an_authorization_to_overwrite(self):
        result = skill.install(**self.options(), yes=True)
        receipt = Path(result['receipt'])
        receipt.write_text('{"managed_by":"someone_else"}', encoding='utf-8')
        with self.assertRaises(CliError) as caught:
            skill.uninstall(**self.options(), force=True, yes=True)
        self.assertEqual(caught.exception.code, 'invalid_skill_receipt')
        self.assertTrue(Path(result['path']).exists())

    def test_missing_content_can_be_repaired_by_install(self):
        result = skill.install(**self.options(), yes=True)
        Path(result['path']).unlink()
        self.assertEqual(skill.status(**self.options())['status'], 'missing')
        self.assertEqual(skill.install(**self.options(), yes=True)['status'], 'current')

    def test_failed_receipt_write_restores_previous_file_and_receipt(self):
        result = skill.install(**self.options(), yes=True)
        path, receipt = Path(result['path']), Path(result['receipt'])
        original, record = path.read_bytes(), receipt.read_bytes()
        write = skill.atomic_write
        failures = []
        def fail_once(target, content):
            if target == receipt and not failures:
                failures.append(True)
                raise OSError('Simulated receipt failure')
            return write(target, content)
        with patch.object(skill, 'bundled', return_value=original + b'New version\n'), \
                patch.object(skill, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                skill.install(**self.options(), update=True, yes=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(receipt.read_bytes(), record)
        self.assertFalse(list(path.parent.glob('*.lock')))

    def test_export_is_self_contained_and_conflicts_are_backed_up(self):
        output = self.root / 'export' / 'SKILL.md'
        skill.export(str(output), dry_run=True)
        self.assertFalse(output.parent.exists())
        skill.export(str(output), yes=True)
        self.assertEqual(output.read_bytes(), skill.bundled())
        self.assertEqual(list(output.parent.iterdir()), [output])
        output.write_bytes(b'Previous document\n')
        with self.assertRaises(CliError):
            skill.export(str(output), yes=True)
        result = skill.export(str(output), force=True, yes=True)
        self.assertEqual(Path(result['backups'][0]).read_bytes(), b'Previous document\n')

    def test_symlink_target_is_rejected_without_writing_outside(self):
        link = self.project / '.agents'
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks not permitted on this host')
        with self.assertRaises(CliError) as caught:
            skill.install(**self.options(), yes=True)
        self.assertEqual(caught.exception.code, 'unsafe_skill_path')
        self.assertEqual(list(outside.iterdir()), [])

    def test_windows_reparse_parent_is_rejected_before_mkdir(self):
        original = Path.lstat
        marked = self.project / '.agents'
        def reparse_info(path):
            if path == marked:
                return SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, 'lstat', reparse_info):
            with self.assertRaises(CliError) as caught:
                skill.install(**self.options(), yes=True)
        self.assertEqual(caught.exception.code, 'unsafe_skill_path')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_existing_lock_prevents_overlapping_writes(self):
        installed = skill.install(**self.options(), yes=True)
        path = Path(installed['path'])
        original = path.read_bytes()
        lock = path.with_name('.' + path.name + '.install.lock')
        lock.write_bytes(b'Existing operation')
        with patch.object(skill, 'bundled', return_value=original + b'Updated\n'):
            with self.assertRaises(CliError) as caught:
                skill.install(**self.options(), update=True, yes=True)
        self.assertEqual(caught.exception.code, 'skill_locked')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(lock.read_bytes(), b'Existing operation')

    def test_cli_show_json_and_local_install_do_not_require_server(self):
        with patch.object(host, 'require_platform', side_effect=AssertionError('Server accessed')), \
                patch.object(host, 'require_root', side_effect=AssertionError('Root requested')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['skill', 'show']), 0)
            self.assertEqual(output.getvalue(), skill.bundled().decode('utf-8'))
            for arguments in (['skill', 'show', '--json'], ['skill', 'list', '--json'],
                              ['skill', 'install', '--agent', 'codex', '--project', str(self.project), '--yes', '--json'],
                              ['skill', 'status', '--agent', 'codex', '--project', str(self.project), '--json'],
                              ['skill', 'uninstall', '--agent', 'codex', '--project', str(self.project), '--yes', '--json']):
                with self.subTest(arguments=arguments):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(arguments), 0)
                    response = json.loads(output.getvalue())
                    self.assertTrue(response['ok'])
                    if arguments[1] == 'show':
                        self.assertEqual(response['data']['content'], skill.bundled().decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
