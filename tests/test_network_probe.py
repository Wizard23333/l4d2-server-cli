"""Source/frozen network workers and process failures without external services."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from l4d2_cli import network, network_probe, cli
from l4d2_cli.common import CliError
from l4d2_cli.model import Source


def frozen_entry():
    spec = importlib.util.spec_from_file_location("probe_frozen_entry", ROOT / "scripts/packaging/frozen_entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NetworkProbeTests(unittest.TestCase):
    def test_source_worker_really_resolves_localhost_in_a_child_process(self):
        result = network.probe("dns", "localhost", 5)
        self.assertEqual(result["status"], "pass", result)
        self.assertTrue(result["addresses"])

    def test_frozen_parent_uses_internal_entry_and_restores_only_its_child_library_path(self):
        response = subprocess.CompletedProcess([], 0, '{"status":"pass","message":"DNS resolved"}')
        with patch.object(network.sys, "frozen", True, create=True), \
                patch.object(network.sys, "_MEIPASS", "/synthetic/bundle", create=True), \
                patch.object(network.sys, "platform", "linux"), \
                patch.dict(os.environ, {"LD_LIBRARY_PATH": "/original/libs"}), \
                patch.object(network.subprocess, "run", return_value=response) as run:
            self.assertEqual(network.probe("dns", "localhost", 3)["status"], "pass")
            args = run.call_args[0][0]
            self.assertEqual(args, [sys.executable, network_probe.WORKER_FLAG, "dns", "localhost", "3"])
            self.assertNotIn("-c", args)
            self.assertEqual(run.call_args[1]["env"]["LD_LIBRARY_PATH"], str(Path("/synthetic/bundle")) + os.pathsep + "/original/libs")
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/original/libs")
            self.assertEqual(run.call_args[1]["timeout"], 3)

    def test_frozen_worker_bypasses_cli_parser_and_keeps_bundle_libraries(self):
        entry = frozen_entry()
        with patch.object(network_probe, "main", return_value=0) as worker, \
                patch.object(cli, "main", side_effect=AssertionError("Worker reached public CLI")), \
                patch.object(entry, "restore_external_library_path", side_effect=AssertionError("Worker lost runtime libraries")):
            self.assertEqual(entry.main([network_probe.WORKER_FLAG, "dns", "localhost", "2"]), 0)
            worker.assert_called_once_with(["dns", "localhost", "2"])

    def test_normal_frozen_entry_still_restores_environment_for_external_tools(self):
        entry = frozen_entry()
        with patch.object(entry.sys, "frozen", True, create=True), patch.object(entry.sys, "platform", "linux"), \
                patch.object(entry, "restore_external_library_path") as restore, \
                patch.object(cli, "main", return_value=0) as public:
            self.assertEqual(entry.main(["--help"]), 0)
            restore.assert_called_once_with(os.environ)
            public.assert_called_once_with(["--help"])

    def test_worker_failures_and_invalid_output_are_failed_checks(self):
        for result in [subprocess.CompletedProcess([], 2, "usage"),
                       subprocess.CompletedProcess([], 0, "not json"),
                       subprocess.CompletedProcess([], 0, "[]"),
                       subprocess.CompletedProcess([], 0, '{"status":"pass"}')]:
            with self.subTest(result=result), patch.object(network.subprocess, "run", return_value=result):
                entry = network.probe("dns", "localhost", 1)
                self.assertEqual(entry["status"], "fail")
                self.assertEqual(entry["category"], "probe")
        with patch.object(network.subprocess, "run", side_effect=OSError("SYNTHETIC_SPAWN_FAILURE")):
            self.assertEqual(network.probe("dns", "localhost", 1)["status"], "fail")

    def test_unusable_workers_block_required_installation_preflight(self):
        with patch.object(network.subprocess, "run", side_effect=OSError("SYNTHETIC_SPAWN_FAILURE")), \
                patch.object(network, "getproxies", return_value={}):
            report = network.check(Source())
        self.assertGreater(report["required_failures"], 0)
        with self.assertRaises(CliError) as caught:
            network.require_network(report)
        self.assertEqual(caught.exception.code, "network_preflight_failed")

    def test_parent_retains_a_hard_probe_timeout(self):
        with patch.object(network.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 1)):
            entry = network.probe("dns", "localhost", 1)
        self.assertEqual((entry["status"], entry["category"]), ("fail", "timeout"))

    def test_worker_rejects_invalid_arguments_and_python_code(self):
        for arguments in [[], ["-c", "print(1)", "1"], ["dns", "localhost", "nan"],
                          ["dns", "localhost", "0"], ["dns", "localhost", "infinity"]]:
            with self.subTest(arguments=arguments), patch.object(network_probe, "check") as check:
                output = io.StringIO()
                with redirect_stdout(output):
                    code = network_probe.main(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output.getvalue())["category"], "probe")
                check.assert_not_called()

    def test_dns_and_tls_errors_keep_their_real_categories(self):
        with patch.object(network_probe.socket, "getaddrinfo", side_effect=socket.gaierror("synthetic")):
            self.assertEqual(network_probe.check("dns", "example.invalid", 1)["category"], "dns")
        with patch.object(network_probe.urllib.request, "build_opener", side_effect=ssl.SSLError("synthetic")):
            self.assertEqual(network_probe.check("http", "https://example.invalid", 1)["category"], "tls")

    def test_response_checks_and_redirect_allowlist_are_preserved(self):
        for kind, body, expected in [("archive", b"\x1f\x8bdata", "pass"),
                                     ("archive", b"<html>challenge</html>", "fail"),
                                     ("repository", b"<!doctype html>", "fail"),
                                     ("http", b"ok", "pass")]:
            response = MagicMock(status=200)
            response.read.return_value = body
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with self.subTest(kind=kind, body=body), \
                    patch.object(network_probe.urllib.request, "build_opener", return_value=opener) as build:
                result = network_probe.check(kind, "https://example.invalid", 1)
                self.assertEqual(result["status"], expected)
                if kind == "archive":
                    redirect = build.call_args[0][0]
                    with self.assertRaises(ValueError):
                        redirect.redirect_request(None, None, 302, "", {}, "https://untrusted.example.invalid/file")
        self.assertEqual(network_probe.check("http", "file:///synthetic", 1)["status"], "fail")
