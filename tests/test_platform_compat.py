"""Offline compatibility regressions; never invoke a real package manager/service."""
from __future__ import annotations

import ast
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from l4d2_cli import cgroups, host, network, repositories, storage
from l4d2_cli.common import CliError
from l4d2_cli.model import Config, Source, render_unit
from l4d2_cli.platforms import detect_adapter, read_os_release
from l4d2_cli._vendor import tomli


def adapter(identity, available, **release):
    return detect_adapter({"ID": identity, **release}, lambda name: name if name in available else None)


def temporary_directory():
    root = ROOT / ".test-tmp"
    root.mkdir(exist_ok=True)
    directory = tempfile.TemporaryDirectory(dir=root)
    if pathlib.Path(directory.name).resolve().parent != root.resolve():
        raise RuntimeError("Unexpected temporary test directory")
    return directory


class DistributionTests(unittest.TestCase):
    def test_common_families_and_like_derivatives(self):
        cases = [
            ("ubuntu", "20.04", ["apt-get", "dpkg-query"], "debian", "apt-get", {}),
            ("ubuntu", "22.04", ["apt-get", "dpkg-query"], "debian", "apt-get", {}),
            ("ubuntu", "24.04", ["apt-get", "dpkg-query"], "debian", "apt-get", {}),
            ("debian", "12", ["apt-get", "dpkg-query"], "debian", "apt-get", {}),
            ("fedora", "43", ["dnf", "rpm"], "rpm", "dnf", {}),
            ("rhel", "9", ["dnf", "rpm"], "rpm", "dnf", {}),
            ("rocky", "9", ["dnf", "rpm"], "rpm", "dnf", {}),
            ("almalinux", "9", ["yum", "rpm"], "rpm", "yum", {}),
            ("opensuse-leap", "15.6", ["zypper", "rpm"], "suse", "zypper", {}),
            ("opensuse-tumbleweed", "", ["zypper", "rpm"], "suse", "zypper", {}),
            ("arch", "", ["pacman"], "arch", "pacman", {}),
            ("my-derivative", "1", ["dnf", "rpm"], "rpm", "dnf", {"ID_LIKE": "rhel fedora"}),
        ]
        for identity, version, programs, family, manager, extra in cases:
            with self.subTest(identity=identity, version=version):
                selected = adapter(identity, programs, VERSION_ID=version, **extra)
                self.assertEqual((selected.family, selected.manager), (family, manager))

    def test_unknown_or_incomplete_adapter_is_rejected(self):
        for identity, programs in (("alpine", ["apk"]), ("unknown", ["apt-get", "dpkg-query"]),
                                   ("ubuntu", ["dnf", "rpm"]), ("fedora", ["dnf"])):
            with self.subTest(identity=identity):
                self.assertIsNone(adapter(identity, programs))

    def test_os_release_is_data_not_shell(self):
        with temporary_directory() as directory:
            path = pathlib.Path(directory) / "os-release"
            path.write_text('ID="ubuntu"\nID_LIKE="debian"\nVERSION_ID="20.04"\n'
                            'PRETTY_NAME="Example $(touch NEVER)"\nBROKEN="unterminated\n', encoding="utf-8")
            release = read_os_release(path)
            self.assertEqual(release["VERSION_ID"], "20.04")
            self.assertIn("$(touch NEVER)", release["PRETTY_NAME"])
            self.assertNotIn("BROKEN", release)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_ubuntu_20_with_python_38_and_hybrid_is_eligible(self):
        with patch.object(host, "read_os_release", return_value={"ID": "ubuntu", "VERSION_ID": "20.04"}), \
                patch.object(host.shutil, "which", return_value="/bin/tool"), \
                patch.object(host.platform, "system", return_value="Linux"), \
                patch.object(host.platform, "machine", return_value="x86_64"), \
                patch.object(host.platform, "libc_ver", return_value=("glibc", "2.31")), \
                patch.object(pathlib.Path, "is_dir", return_value=True), \
                patch.object(sys, "version_info", (3, 8, 10)), \
                patch.object(cgroups, "mounts", return_value=[{"kind": "cgroup"}, {"kind": "cgroup2"}]):
            report = host.platform_report()
            self.assertTrue(report["supported"])
            self.assertEqual(report["cgroup"], "hybrid")

    def test_musl_arm_and_missing_systemd_are_reported(self):
        with patch.object(host.platform, "system", return_value="Linux"), \
                patch.object(host.platform, "machine", return_value="aarch64"), \
                patch.object(host.platform, "libc_ver", return_value=("musl", "1.2")), \
                patch.object(pathlib.Path, "is_dir", return_value=False), \
                patch.object(cgroups, "mounts", return_value=[]):
            report = host.platform_report()
            self.assertFalse(report["supported"])
            self.assertTrue({"x86_64", "glibc", "systemd", "systemd_cgroup"}.issubset(report["unmet_requirements"]))

    def test_32_bit_packages_are_architecture_specific(self):
        rpm = adapter("rhel", ["dnf", "rpm"])
        self.assertIn("glibc.i686", rpm.runtime_packages)
        self.assertIn("libstdc++.i686", rpm.runtime_packages)
        arch = adapter("arch", ["pacman"])
        self.assertIn("lib32-glibc", arch.runtime_packages)
        suse = adapter("opensuse-leap", ["zypper", "rpm"])
        self.assertIn("glibc-32bit", suse.runtime_packages)

    def test_install_only_known_packages_with_signatures_enabled(self):
        for identity, programs in (("ubuntu", ["apt-get", "dpkg-query"]), ("rhel", ["dnf", "rpm"]),
                                   ("centos", ["yum", "rpm"]), ("opensuse-leap", ["zypper", "rpm"]),
                                   ("arch", ["pacman"])):
            selected = adapter(identity, programs)
            commands = selected.install_commands([selected.runtime_packages[-1]])
            flat = [part for command in commands for part in command]
            self.assertFalse(set(flat) & {"upgrade", "dist-upgrade", "dup", "-Sy", "-Syu",
                                         "--nogpgcheck", "--no-gpg-checks", "--allow-unauthenticated"})
            self.assertEqual(commands[-1][-1], selected.runtime_packages[-1])
            with self.assertRaises(CliError):
                selected.install_commands(["--disable-signatures"])

    def test_rpm_package_presence_uses_return_code(self):
        selected = adapter("fedora", ["dnf", "rpm"])
        def query(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if args[-1] == "glibc.i686" else 0, stdout="")
        with patch.object(host, "dependency_adapter", return_value=selected), \
                patch.object(host.shutil, "which", return_value="/bin/tool"), patch.object(host, "run", side_effect=query):
            self.assertEqual(host.missing_dependencies(), ["glibc.i686"])

    def test_apt_partial_package_state_is_missing(self):
        selected = adapter("debian", ["apt-get", "dpkg-query"])
        def query(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="install ok unpacked" if args[-1] == "lib32z1"
                                               else "install ok installed")
        with patch.object(host, "dependency_adapter", return_value=selected), \
                patch.object(host.shutil, "which", return_value="/bin/tool"), patch.object(host, "run", side_effect=query):
            self.assertEqual(host.missing_dependencies(), ["lib32z1"])

    def test_nologin_uses_available_distribution_path(self):
        with patch.object(pathlib.Path, "is_file", side_effect=lambda: True), \
                patch.object(host.os, "access", side_effect=lambda name, mode: name == "/usr/bin/nologin"):
            self.assertEqual(host.nologin_shell(), "/usr/bin/nologin")


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory()
        self.root = pathlib.Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_apt_list_and_deb822_ignore_disabled_and_source_only(self):
        self.write("etc/apt/sources.list", "# deb https://disabled.example stable main\n"
                   "deb [signed-by=/path/key] https://apt.example stable main\n")
        self.write("etc/apt/sources.list.d/test.sources",
                   "Types: deb\nURIs:\n https://deb822.example\nSuites: bookworm\n bookworm-updates\n\n"
                   "Types: deb\nEnabled: no\nURIs: https://disabled.example\nSuites: stable\n\n"
                   "Types: deb-src\nURIs: https://source.example\nSuites: stable\n")
        found = repositories.targets("debian", self.root)
        self.assertEqual(len(found), 3)
        self.assertTrue(all(item["kind"] == "repository" for item in found))
        self.assertNotIn("disabled.example", json.dumps(found))
        self.assertNotIn("source.example", json.dumps(found))

    def test_rpm_static_metadata_and_unresolved_releasever(self):
        self.write("etc/yum.repos.d/example.repo",
                   "[static]\nbaseurl=https://rpm.example/os/$basearch\n"
                   "[templated]\nmetalink=https://mirror.example/metalink?release=$releasever\n"
                   "[disabled]\nenabled=0\nbaseurl=https://disabled.example\n")
        found = repositories.targets("rpm", self.root)
        self.assertEqual(found[0]["target"], "https://rpm.example/os/x86_64/repodata/repomd.xml")
        self.assertEqual(found[1]["kind"], "dns")
        self.assertIn("unverified", found[1]["scope"])
        self.assertNotIn("disabled.example", json.dumps(found))

    def test_suse_uses_zypp_repository_directory(self):
        self.write("etc/zypp/repos.d/base.repo", "[oss]\nenabled=1\nbaseurl=https://suse.example/repo/oss\n")
        self.assertEqual(repositories.targets("suse", self.root)[0]["target"],
                         "https://suse.example/repo/oss/repodata/repomd.xml")

    def test_arch_reuses_mirror_include_for_each_enabled_repository(self):
        self.write("etc/pacman.conf", "[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"
                   "[multilib]\nInclude = /etc/pacman.d/mirrorlist\n")
        self.write("etc/pacman.d/mirrorlist", "Server = https://arch.example/$repo/os/$arch\n")
        found = repositories.targets("arch", self.root)
        self.assertEqual(found[0]["target"], "https://arch.example/multilib/os/x86_64/multilib.db")
        self.assertEqual(len(found), 2)

    def test_probe_count_is_bounded_and_file_urls_are_not_contacted(self):
        self.write("etc/apt/sources.list", "".join("deb https://mirror%d.example stable main\n" % n
                                                  for n in range(8)) + "deb file:///mnt/repo stable main\n")
        self.assertEqual(len(repositories.targets("debian", self.root)), 4)

    def test_offline_check_has_no_network_or_repository_access(self):
        self.write("bundle.tar.gz", "source-validation-only")
        with patch.object(network, "probe", side_effect=AssertionError("Unexpected network")), \
                patch.object(network, "repository_targets", side_effect=AssertionError("Unexpected repository")):
            report = network.check(Source(server_archive=str(self.root / "bundle.tar.gz"), offline=True),
                                   needs_dependencies=True)
        self.assertEqual(report["checks"][0]["status"], "skipped")
        self.assertEqual(report["required_failures"], 0)


class CgroupTests(unittest.TestCase):
    def test_mount_detection_and_escaped_paths(self):
        with temporary_directory() as directory:
            path = pathlib.Path(directory) / "mountinfo"
            path.write_text("23 1 0:20 / /sys/fs/cgroup/unified rw - cgroup2 cgroup rw\n"
                            "24 1 0:21 / /sys/fs/cgroup/systemd rw - cgroup cgroup rw,name=systemd\n"
                            "25 1 0:22 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
                            "26 1 0:23 / /cgroup\\040space rw - cgroup2 cgroup rw\n", encoding="utf-8")
            found = cgroups.mounts(path)
            self.assertEqual(cgroups.mode(found), "hybrid")
            self.assertEqual(found[0]["kind"], "cgroup")
            self.assertTrue(any(item["mount"] == "/cgroup space" for item in found))
            self.assertEqual(len(found), 3)

    def test_process_tree_on_both_hierarchies_and_retained_stop_group(self):
        with temporary_directory() as directory:
            root = pathlib.Path(directory)
            group = root / "system.slice/l4d2-main.service"
            child = group / "engine"
            child.mkdir(parents=True)
            (group / "cgroup.procs").write_text("100\n", encoding="utf-8")
            (child / "cgroup.procs").write_text("101\n102\n", encoding="utf-8")
            for kind in ("cgroup", "cgroup2"):
                entries = [{"kind": kind, "root": "/", "mount": str(root)}]
                info = {"ControlGroup": "/system.slice/l4d2-main.service", "MainPID": 100}
                self.assertEqual(cgroups.service_pids(info, entries), [100, 101, 102])
                info["MainPID"] = 0
                self.assertEqual(cgroups.service_pids(info, entries), [100, 101, 102])

    def test_namespaced_mount_root(self):
        with temporary_directory() as directory:
            root = pathlib.Path(directory)
            (root / "service").mkdir()
            (root / "service/cgroup.procs").write_text("20\n", encoding="utf-8")
            self.assertEqual(cgroups.service_pids({"ControlGroup": "/scope/service", "MainPID": 20},
                             [{"kind": "cgroup2", "root": "/scope", "mount": str(root)}]), [20])

    def test_hybrid_does_not_hide_children_in_other_hierarchy(self):
        with temporary_directory() as directory:
            root = pathlib.Path(directory)
            entries = []
            for name, kind, pids in (("v1", "cgroup", ""), ("v2", "cgroup2", "31\n32\n")):
                group = root / name / "service"
                group.mkdir(parents=True)
                (group / "cgroup.procs").write_text(pids, encoding="utf-8")
                entries.append({"kind": kind, "root": "/", "mount": str(root / name)})
            self.assertEqual(cgroups.service_pids({"ControlGroup": "/service", "MainPID": 0}, entries), [31, 32])

    def test_stopped_missing_group_is_empty_but_running_missing_group_fails(self):
        entries = [{"kind": "cgroup2", "root": "/", "mount": "NONEXISTENT_TEST_MOUNT"}]
        self.assertEqual(cgroups.service_pids({"ControlGroup": "/gone", "MainPID": 0}, entries), [])
        with self.assertRaises(CliError):
            cgroups.service_pids({"ControlGroup": "/gone", "MainPID": 500}, entries)

    def test_unsafe_or_uninspectable_group_is_not_success(self):
        for info, entries in (({"ControlGroup": "/../elsewhere", "MainPID": 0}, []),
                              ({"ControlGroup": "/service", "MainPID": 0}, []),
                              ({"ControlGroup": "", "MainPID": 20}, [])):
            with self.assertRaises(CliError):
                cgroups.service_pids(info, entries)


class RuntimeTests(unittest.TestCase):
    def test_all_sources_parse_using_python_38_grammar(self):
        for path in (ROOT / "src").rglob("*.py"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_bundled_toml_configuration_roundtrip_and_invalid_input(self):
        config = Config(name='中文 "Server"', game_dir=str(ROOT / "test-install"))
        self.assertEqual(Config(**tomli.loads(config.toml())), config)
        with self.assertRaises(tomli.TOMLDecodeError):
            tomli.loads('port=27015\nport=27016\n')

    def test_storage_parser_can_load_existing_toml(self):
        config = Config(name="Unicode 中文", game_dir=str(ROOT / "test-install"))
        self.assertEqual(storage.tomllib.loads(config.toml()), tomli.loads(config.toml()))

    def test_service_template_is_available_without_resources_files_api(self):
        unit = render_unit(Config(game_dir=str(ROOT / "test-install")))
        self.assertIn("User=l4d2", unit)
        self.assertIn("+servercfgfile", unit)

    def test_full_cli_help_on_current_interpreter(self):
        completed = subprocess.run([sys.executable, str(ROOT / "bin/l4d2"), "server", "install", "--help"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--offline", completed.stdout)


class NativeUnitSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and shutil.which("systemd-analyze"),
                         "Native Linux systemd unit validator")
    def test_generated_unit_with_special_path_passes_native_parser(self):
        with temporary_directory() as temporary:
            root = pathlib.Path(temporary)
            for name in ("room with space %", "room trailing "):
                with self.subTest(name=name):
                    game = root / name
                    game.mkdir()
                    executable = game / "srcds_run"
                    executable.write_text("#!/bin/sh\nexit 0\n")
                    executable.chmod(0o700)
                    unit = root / "l4d2-cli-syntax-test.service"
                    unit.write_text(render_unit(Config(game_dir=str(game))), encoding="utf-8")
                    result = subprocess.run(
                        [shutil.which("systemd-analyze"), "verify", "--man=no", str(unit)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
