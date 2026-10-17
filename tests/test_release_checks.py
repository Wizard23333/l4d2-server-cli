"""Checks for accidentally published credentials and stale/mixed release files."""
import importlib.util
import json
from pathlib import Path
import unittest
import zipfile

from test_install_recovery import directory

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_checks', ROOT / 'scripts/check_release.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class PublicationChecksTests(unittest.TestCase):
    def test_tokens_are_detected_without_printing_the_token(self):
        token = 'ghp_' + 'x' * 36
        findings = checks.scan_text('docs/example.md', 'A private value: ' + token)
        self.assertEqual(findings[0]['rule'], 'github_credential')
        self.assertNotIn(token, json.dumps(findings))

    def test_real_looking_url_is_rejected_and_synthetic_redaction_fixture_is_allowed(self):
        unsafe = 'https://' + 'example-user:example-password' + '@proxy.example/'
        self.assertEqual(checks.scan_text('docs/example.md', unsafe)[0]['rule'], 'url_credential')
        self.assertEqual(checks.scan_text('tests/example.py',
                                         'https://user:SYNTHETIC_PASSWORD@proxy.example/'), [])

    def test_ignored_or_forced_tracked_private_paths_and_resources_are_rejected(self):
        for name in ['../outside', '.workbuddy/memory.md', 'docs/.env', 'config.vdf',
                     'cache/map.vpk', 'backups/state.json', 'src/cache.zip', 'private.key', 'docs/test-results/report.json', '.local-docs/requirements.md']:
            with self.subTest(name=name):
                self.assertTrue(checks.forbidden_path(name))
        for name in ['README.md', 'tests/test_auth.py', 'src/l4d2_cli/model.py']:
            self.assertFalse(checks.forbidden_path(name))

    def test_wheel_must_match_source_and_must_not_include_a_login_cache(self):
        with directory() as temporary:
            root = Path(temporary)
            source = root / 'src/l4d2_cli/__init__.py'
            source.parent.mkdir(parents=True)
            source.write_text("__version__ = '1.0'\n", encoding='utf-8')
            wheel = root / 'test.whl'

            def build(extra=False, stale=False, newline="\n"):
                with zipfile.ZipFile(wheel, 'w') as archive:
                    archive.writestr('l4d2_cli/__init__.py', b'STALE' if stale else source.read_bytes())
                    archive.writestr('test-1.0.dist-info/METADATA', 'Version: 1.0' + newline)
                    archive.writestr('test-1.0.dist-info/licenses/LICENSE', 'MIT fixture')
                    archive.writestr('test-1.0.dist-info/licenses/THIRD_PARTY_NOTICES.md', 'fixture')
                    if extra:
                        archive.writestr('l4d2_cli/config.vdf', 'SYNTHETIC_CACHE')
            build()
            self.assertEqual(checks.inspect_distribution(wheel, root, '1.0'), [])
            build(newline="\r\n")
            self.assertEqual(checks.inspect_distribution(wheel, root, '1.0'), [])
            build(stale=True)
            self.assertIn('wheel_runtime_bytes_mismatch',
                          [issue['rule'] for issue in checks.inspect_distribution(wheel, root, '1.0')])
            build(extra=True)
            self.assertIn('private_artifact_entry',
                          [issue['rule'] for issue in checks.inspect_distribution(wheel, root, '1.0')])
