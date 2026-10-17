"""Exercise native CLI installation in isolated prefixes, without game services."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/packaging/install.sh"


@unittest.skipUnless(sys.platform == "linux", "Native Linux installer")
class GlobalInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="l4d2-global-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "extracted bundle"
        (self.bundle / "_internal").mkdir(parents=True)
        self.prefix = self.root / "installed prefix"
        self.entry = self.bundle / "l4d2"
        self.entry.write_bytes(b'#!/usr/bin/env bash\nset -eu\n'
                              b'root=$(dirname -- "$(readlink -f -- "$0")")\n'
                              b'cat "$root/_internal/current"\n')
        self.entry.chmod(0o755)
        (self.bundle / "_internal/version").write_text("l4d2-server-cli 0.1.0a1\n")
        (self.bundle / "_internal/current").symlink_to("version")
        shutil.copyfile(INSTALLER, self.bundle / "install.sh")
        self.manifest()

    def manifest(self):
        entries = []
        for path in sorted(self.bundle.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.name != "FILES.sha256":
                entries.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " +
                               path.relative_to(self.bundle).as_posix() + "\n")
        (self.bundle / "FILES.sha256").write_text("".join(entries))

    def install(self, *, check=True, default_bundle=False):
        command = ["bash", str(self.bundle / "install.sh"), "--prefix", str(self.prefix)]
        if not default_bundle:
            command.append(str(self.bundle))
        return subprocess.run(command, cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=30, check=check)

    def cli(self):
        environment = dict(os.environ, PATH=str(self.prefix / "bin") + os.pathsep + os.environ["PATH"])
        return subprocess.check_output(["l4d2", "--help"], cwd="/", env=environment, text=True, timeout=10)

    def test_command_works_from_any_directory_after_extraction_is_removed(self):
        # Unlisted files (including downloads or credentials) are not installed.
        (self.bundle / "unlisted.txt").write_text("private local addition")
        (self.bundle / "_internal/version").chmod(0o666)
        self.install(default_bundle=True)
        command = self.prefix / "bin/l4d2"
        self.assertTrue(command.is_symlink())
        installed = command.resolve().parent
        self.assertTrue((installed / "_internal/current").is_symlink())
        self.assertFalse((installed / "unlisted.txt").exists())
        self.assertEqual((installed / "_internal/version").stat().st_mode & 0o022, 0)
        self.bundle.rename(self.root / "original extraction removed")
        self.assertEqual(self.cli(), "l4d2-server-cli 0.1.0a1\n")

    def test_repeat_install_and_upgrade_switch_the_global_command(self):
        self.install()
        command = self.prefix / "bin/l4d2"
        first = command.resolve()
        self.install()
        self.assertEqual(command.resolve(), first)
        (self.bundle / "_internal/version").write_text("l4d2-server-cli 0.1.0a2\n")
        self.manifest()
        self.install()
        self.assertNotEqual(command.resolve(), first)
        self.assertTrue(first.is_file())
        self.assertEqual(self.cli(), "l4d2-server-cli 0.1.0a2\n")

    def test_checksum_failure_keeps_the_working_command(self):
        self.install()
        before = (self.prefix / "bin/l4d2").resolve()
        (self.bundle / "_internal/version").write_text("damaged\n")
        failed = self.install(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("checksum verification failed", failed.stderr)
        self.assertEqual((self.prefix / "bin/l4d2").resolve(), before)
        self.assertEqual(self.cli(), "l4d2-server-cli 0.1.0a1\n")

    def test_unusable_upgrade_does_not_replace_the_working_command(self):
        self.install()
        before = (self.prefix / "bin/l4d2").resolve()
        self.entry.write_bytes(b"#!/bin/sh\nexit 7\n")
        self.manifest()
        self.assertEqual(self.install(check=False).returncode, 7)
        self.assertEqual((self.prefix / "bin/l4d2").resolve(), before)
        self.assertFalse(list((self.prefix / "lib/l4d2-server-cli").glob(".install.*")))

    def test_existing_foreign_file_or_symlink_is_not_replaced(self):
        (self.prefix / "bin").mkdir(parents=True)
        command = self.prefix / "bin/l4d2"
        foreign = self.root / "another-tool"
        foreign.write_text("keep this")
        for kind in ("file", "link"):
            with self.subTest(kind=kind):
                if kind == "file":
                    command.write_text("keep this")
                else:
                    command.symlink_to(foreign)
                self.assertNotEqual(self.install(check=False).returncode, 0)
                self.assertEqual(command.read_text(), "keep this")
                self.assertEqual(command.is_symlink(), kind == "link")
                command.unlink()

    def test_symlink_prefix_and_world_writable_bin_are_rejected(self):
        actual = self.root / "elsewhere"
        actual.mkdir()
        self.prefix.symlink_to(actual, target_is_directory=True)
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse((actual / "bin").exists())
        self.prefix.unlink()
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "bin").chmod(0o777)
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse((self.prefix / "bin/l4d2").exists())

    def test_bundle_link_escape_and_manifest_escape_are_rejected(self):
        outside = self.root / "outside"
        outside.write_text("do not copy")
        link = self.bundle / "escape"
        link.symlink_to("../outside")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        link.unlink()
        with (self.bundle / "FILES.sha256").open("a") as output:
            output.write(hashlib.sha256(outside.read_bytes()).hexdigest() + "  ../outside\n")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse(self.prefix.exists())

    def test_unlisted_executable_is_rejected(self):
        (self.bundle / "FILES.sha256").write_text(
            hashlib.sha256((self.bundle / "_internal/version").read_bytes()).hexdigest() +
            "  _internal/version\n")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse(self.prefix.exists())

    def test_default_global_install_explains_sudo_requirement(self):
        if os.getuid() == 0:
            self.skipTest("Requires an ordinary user")
        result = subprocess.run(["bash", str(INSTALLER), str(self.bundle)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo bash install.sh", result.stderr)
