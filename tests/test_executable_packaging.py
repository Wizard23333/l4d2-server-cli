"""Native packaging protections that can be tested without freezing Python."""
import importlib.util
import os
from pathlib import Path
import unittest

from test_install_recovery import directory

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/packaging" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecutablePackagingTests(unittest.TestCase):
    def test_external_tools_receive_original_library_path(self):
        restore = load("frozen_entry").restore_external_library_path
        for original in [None, "", "/opt/user-libraries"]:
            environment = {"LD_LIBRARY_PATH": "/bundle/_internal", "PATH": "/usr/bin"}
            if original is not None:
                environment["LD_LIBRARY_PATH_ORIG"] = original
            restore(environment)
            self.assertEqual(environment.get("LD_LIBRARY_PATH"), original)
            self.assertNotIn("LD_LIBRARY_PATH_ORIG", environment)
            self.assertEqual(environment["PATH"], "/usr/bin")

    def test_bundle_cannot_include_local_test_results_or_game_files(self):
        build = load("build_linux")
        with directory() as temp:
            bundle = Path(temp)
            (bundle / "test-results").mkdir()
            with self.assertRaises(ValueError):
                build.validate_bundle(bundle)

    @unittest.skipUnless(os.name == "posix", "Linux symlink policy")
    def test_bundle_symlinks_must_stay_inside_the_package(self):
        build = load("build_linux")
        with directory() as temp:
            bundle = Path(temp) / "bundle"
            bundle.mkdir()
            (bundle / "runtime").write_text("fixture")
            (bundle / "library").symlink_to("runtime")
            self.assertEqual(len(build.validate_bundle(bundle)), 1)
            (bundle / "escape").symlink_to("../../outside")
            with self.assertRaises(ValueError):
                build.validate_bundle(bundle)

    def test_host_preload_libraries_are_not_redistributed(self):
        build = load("build_linux")
        with directory() as temp:
            runtime = Path(temp) / "runtime"
            entries = [("libpython.so", str(runtime / "lib/libpython.so"), "BINARY"),
                       ("_ssl.so", str(runtime / "lib/_ssl.so"), "EXTENSION"),
                       ("host-preload.so", str(Path(temp) / "host/host-preload.so"), "BINARY"),
                       ("neighbor.so", str(Path(temp) / "runtime-neighbor/neighbor.so"), "BINARY"),
                       ("python-link.so", "libpython.so", "SYMLINK"),
                       ("host-link.so", "host-preload.so", "SYMLINK")]
            accepted = build.runtime_libraries(entries, runtime)
            self.assertEqual([item[0] for item in accepted], ["libpython.so", "_ssl.so", "python-link.so"])
