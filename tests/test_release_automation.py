"""Preview release boundaries: versions, input provenance and safe retries."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

from test_install_recovery import directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/packaging"))
sys.path.insert(0, str(ROOT / "src"))
import release_metadata as metadata
import prepare_runtime as inputs
import publish_preview as publish


class ReleaseAutomationTests(unittest.TestCase):
    def test_preview_tag_roundtrip_for_supported_channels(self):
        for version, tag in [("0.1.0a2", "v0.1.0-alpha.2"), ("1.2.3b12", "v1.2.3-beta.12"),
                             ("2.0.0rc1", "v2.0.0-rc.1")]:
            self.assertEqual(metadata.preview_tag(version), tag)
            self.assertEqual(metadata.version_for_tag(tag), version)

    def test_stable_malformed_and_shell_like_tags_are_rejected(self):
        for tag in ["v1.2.3", "v1.2.3a1", "v01.2.3-alpha.1", "v1.2.3-alpha.0",
                    "v1.2.3-alpha.01", "v1.2.3-alpha.1/extra", "v1.2.3-alpha.1;echo bad", " v1.2.3-alpha.1"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                metadata.version_for_tag(tag)

    def test_tag_and_both_version_declarations_must_agree(self):
        with directory() as temporary:
            root = Path(temporary)
            (root / "src/l4d2_cli").mkdir(parents=True)
            (root / "src/l4d2_cli/__init__.py").write_text('__version__ = "0.2.0a3"\n')
            (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0a3"\n')
            self.assertEqual(metadata.validate_tag("v0.2.0-alpha.3", root), "0.2.0a3")
            with self.assertRaises(ValueError):
                metadata.validate_tag("v0.2.0-alpha.4", root)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0a4"\n')
            with self.assertRaises(ValueError):
                metadata.validate_tag("v0.2.0-alpha.3", root)

    def test_release_notes_resolve_relative_links_and_fallback_uses_new_tag(self):
        with directory() as temporary:
            root = Path(temporary)
            (root / "docs/releases").mkdir(parents=True)
            tag = "v0.2.0-alpha.1"
            fallback = metadata.release_notes(tag, "example/project", root)
            self.assertIn("releases/download/" + tag + "/l4d2-server-cli-" + tag, fallback)
            (root / "docs/releases" / (tag + ".md")).write_text("[Install](../../README.md)\n")
            self.assertIn("https://github.com/example/project/blob/" + tag + "/README.md",
                          metadata.release_notes(tag, "example/project", root))

    def test_runtime_archive_rejects_escape_devices_and_members_under_links(self):
        def member(name, kind=tarfile.REGTYPE, target=""):
            item = tarfile.TarInfo(name)
            item.type, item.linkname = kind, target
            return item
        safe = [member("python", tarfile.DIRTYPE), member("python/bin", tarfile.DIRTYPE),
                member("python/bin/python3.12"), member("python/bin/python3", tarfile.SYMTYPE, "python3.12")]
        inputs.validate_members(safe)
        for unsafe in [member("../outside"), member("/absolute"), member("other/file"),
                       member("python/device", tarfile.CHRTYPE),
                       member("python/escape", tarfile.SYMTYPE, "../../outside"),
                       member("python/bin/python3/hidden")]:
            with self.subTest(name=unsafe.name), self.assertRaises(ValueError):
                inputs.validate_members(safe + [unsafe])

    def test_corrupt_download_is_not_committed_as_a_build_input(self):
        with directory() as temporary:
            target = Path(temporary) / "runtime.tar.gz"
            with patch.object(inputs, "urlopen", return_value=io.BytesIO(b"corrupt")):
                with self.assertRaises(ValueError):
                    inputs.download_verified("https://example.invalid/runtime", target, "0" * 64)
            self.assertFalse(target.exists())

    def test_release_assets_must_be_exact_and_have_matching_remote_digests(self):
        expected = {"cli.tar.gz": "1" * 64, "cli.sha256": "2" * 64}
        assets = [{"name": name, "state": "uploaded", "digest": "sha256:" + digest}
                  for name, digest in expected.items()]
        publish.verify_assets({"assets": assets}, expected)
        for changed in [assets[:1], assets + [{"name": "test-results.json"}],
                        [{**assets[0], "digest": "sha256:" + "3" * 64}, assets[1]]]:
            with self.assertRaises(ValueError):
                publish.verify_assets({"assets": changed}, expected)

    def test_manual_workflow_cannot_call_release_api(self):
        tag = metadata.preview_tag(metadata.project_version())
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}), \
                patch.object(sys, "argv", ["publish_preview", "--tag", tag, "--assets", "unused"]), \
                patch.object(publish, "gh") as api, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                publish.main()
            self.assertEqual(raised.exception.code, 2)
            api.assert_not_called()

    def test_retry_of_published_identical_release_performs_no_writes(self):
        tag = metadata.preview_tag(metadata.project_version())
        commit = "1" * 40
        with directory() as temporary:
            assets = Path(temporary)
            name = metadata.basename(tag)
            archive = assets / (name + ".tar.gz")
            archive.write_bytes(b"fixture")
            checksum = assets / (name + ".sha256")
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
            release = {"body": "<!-- l4d2-cli-preview commit=" + commit + " -->", "draft": False,
                       "prerelease": True, "html_url": "https://github.com/example/project/releases/tag/" + tag,
                       "assets": [{"name": path.name, "state": "uploaded",
                                   "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
                                  for path in [archive, checksum]]}
            environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/" + tag,
                           "GITHUB_REPOSITORY": "example/project", "GITHUB_SHA": commit}
            with patch.dict(os.environ, environment), \
                    patch.object(sys, "argv", ["publish_preview", "--tag", tag, "--assets", str(assets)]), \
                    patch.object(publish.subprocess, "check_output", return_value=commit + "\n"), \
                    patch.object(publish, "gh", return_value=json.dumps({"sha": commit})) as api, \
                    patch.object(publish, "find_release", return_value=release), redirect_stdout(io.StringIO()):
                publish.main()
                self.assertEqual(api.call_count, 1)
                self.assertEqual(api.call_args[0][0], "api")

    def test_new_release_is_published_only_after_both_uploads_verify(self):
        tag = metadata.preview_tag(metadata.project_version())
        commit = "2" * 40
        with directory() as temporary:
            assets = Path(temporary)
            name = metadata.basename(tag)
            archive = assets / (name + ".tar.gz")
            archive.write_bytes(b"native fixture")
            checksum = assets / (name + ".sha256")
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
            remote_assets = [{"name": path.name, "state": "uploaded",
                              "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
                             for path in [archive, checksum]]
            draft = {"assets": [], "draft": True, "prerelease": True}
            complete = {**draft, "assets": remote_assets}
            published = {**complete, "draft": False, "html_url": "https://example.invalid/preview"}
            writes = []

            def api(*arguments):
                if arguments[0] == "api":
                    return json.dumps({"sha": commit} if "/commits/" in arguments[1] else published)
                writes.append(arguments[:2])
                if arguments[:2] == ("release", "create"):
                    notes = Path(arguments[arguments.index("--notes-file") + 1]).read_text(encoding="utf-8")
                    self.assertIn("<!-- l4d2-cli-preview commit=" + commit + " -->", notes)
                    self.assertIn("--draft", arguments)
                    self.assertIn("--prerelease", arguments)
                if arguments[:2] == ("release", "edit"):
                    self.assertEqual(writes.count(("release", "upload")), 2)
                return ""

            environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/" + tag,
                           "GITHUB_REPOSITORY": "example/project", "GITHUB_SHA": commit,
                           "GITHUB_STEP_SUMMARY": ""}
            with patch.dict(os.environ, environment), \
                    patch.object(sys, "argv", ["publish_preview", "--tag", tag, "--assets", str(assets)]), \
                    patch.object(publish.subprocess, "check_output", return_value=commit + "\n"), \
                    patch.object(publish, "gh", side_effect=api), \
                    patch.object(publish, "find_release", side_effect=[None, draft, complete]), \
                    redirect_stdout(io.StringIO()):
                publish.main()
            self.assertEqual(writes, [("release", "create"), ("release", "upload"),
                                      ("release", "upload"), ("release", "edit")])

    def test_moved_tag_fails_before_draft_or_upload(self):
        tag = metadata.preview_tag(metadata.project_version())
        environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/" + tag,
                       "GITHUB_REPOSITORY": "example/project", "GITHUB_SHA": "1" * 40}
        with patch.dict(os.environ, environment), \
                patch.object(sys, "argv", ["publish_preview", "--tag", tag, "--assets", "unused"]), \
                patch.object(publish.subprocess, "check_output", return_value="1" * 40 + "\n"), \
                patch.object(publish, "gh", return_value=json.dumps({"sha": "2" * 40})) as api, \
                patch.object(publish, "find_release") as lookup:
            with self.assertRaisesRegex(ValueError, "Tag moved"):
                publish.main()
            lookup.assert_not_called()
            self.assertEqual(api.call_count, 1)

    def test_upload_digest_failure_never_publishes_draft(self):
        tag = metadata.preview_tag(metadata.project_version())
        commit = "3" * 40
        with directory() as temporary:
            assets = Path(temporary)
            name = metadata.basename(tag)
            archive = assets / (name + ".tar.gz")
            archive.write_bytes(b"fixture")
            checksum = assets / (name + ".sha256")
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
            draft = {"body": "<!-- l4d2-cli-preview commit=" + commit + " -->",
                     "draft": True, "prerelease": True, "assets": []}
            bad = {**draft, "assets": [{"name": path.name, "state": "uploaded", "digest": "sha256:" + "0" * 64}
                                      for path in [archive, checksum]]}
            environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/" + tag,
                           "GITHUB_REPOSITORY": "example/project", "GITHUB_SHA": commit}
            with patch.dict(os.environ, environment), \
                    patch.object(sys, "argv", ["publish_preview", "--tag", tag, "--assets", str(assets)]), \
                    patch.object(publish.subprocess, "check_output", return_value=commit + "\n"), \
                    patch.object(publish, "gh", return_value=json.dumps({"sha": commit})) as api, \
                    patch.object(publish, "find_release", side_effect=[draft, bad]):
                with self.assertRaisesRegex(ValueError, "SHA256 differs"):
                    publish.main()
                self.assertFalse(any(call.args[:2] == ("release", "edit") for call in api.call_args_list))
