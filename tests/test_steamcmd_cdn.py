"""SteamCMD CDN failover stays bounded and consistent with real downloads."""
import gzip
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from l4d2_cli import network, packages
from l4d2_cli.common import CliError
from l4d2_cli.model import Source, STEAMCMD_URL, STEAMCMD_URLS
from l4d2_cli.network_output import format_report


class CdnPreflightTests(unittest.TestCase):
    def run_check(self, *, primary_dns=True, all_fail=False):
        calls = []
        def probe(kind, target, timeout):
            calls.append((kind, target, timeout))
            passed = (kind == "dns" and primary_dns) or (kind == "archive" and target == STEAMCMD_URLS[1] and not all_fail)
            return {"status": "pass" if passed else "fail", "target": target,
                    "message": "HTTP endpoint reachable" if passed else "No route to host", "category": "connection"}
        with patch.object(network, "probe", side_effect=probe), patch.object(network, "getproxies", return_value={}):
            return network.check(Source(), archive_only=True), calls

    def test_primary_unreachable_but_backup_gzip_probe_passes(self):
        report, calls = self.run_check()
        archive = next(item for item in report["checks"] if item["name"] == "steamcmd_archive")
        self.assertEqual(report["required_failures"], 0)
        self.assertEqual(report["steamcmd_url"], STEAMCMD_URLS[1])
        self.assertEqual(archive["status"], "pass")
        self.assertEqual(len(archive["attempts"]), 2)
        self.assertEqual([item[1] for item in calls if item[0] == "archive"], list(STEAMCMD_URLS[:2]))
        human = format_report(report)
        self.assertIn("已切换到可用的备用 CDN", human)
        self.assertIn("此前尝试：" + STEAMCMD_URL, human)
        network.require_network(report)

    def test_primary_dns_failure_is_reference_only_after_a_backup_succeeds(self):
        report, _ = self.run_check(primary_dns=False)
        dns = next(item for item in report["checks"] if item["name"] == "steamcmd_dns")
        self.assertEqual(dns["status"], "warn")
        self.assertIs(dns["required"], False)
        self.assertEqual(report["required_failures"], 0)

    def test_all_trusted_nodes_fail_without_becoming_a_pass(self):
        report, calls = self.run_check(all_fail=True)
        self.assertGreater(report["required_failures"], 0)
        self.assertNotIn("steamcmd_url", report)
        self.assertEqual([item[1] for item in calls if item[0] == "archive"], list(STEAMCMD_URLS))
        with self.assertRaises(CliError):
            network.require_network(report)

    def test_primary_success_does_not_probe_extra_nodes(self):
        def probe(kind, target, timeout):
            return {"status": "pass", "message": "reachable", "target": target}
        with patch.object(network, "probe", side_effect=probe) as run, patch.object(network, "getproxies", return_value={}):
            report = network.check(Source(), archive_only=True)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report["steamcmd_url"], STEAMCMD_URL)
        self.assertNotIn("attempts", next(item for item in report["checks"] if item["name"] == "steamcmd_archive"))

    def test_fallback_respects_the_same_total_deadline(self):
        now, calls = [0.0], []
        def probe(kind, target, timeout):
            calls.append((target, timeout))
            now[0] += timeout
            return {"status": "fail", "target": target, "category": "timeout", "message": "timed out"}
        with patch.object(network.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(network, "probe", side_effect=probe), patch.object(network, "getproxies", return_value={}):
            report = network.check(Source(), timeout=2, archive_only=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(now[0], 2)
        self.assertTrue(report["timed_out"])


class Response(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}
        self.url = url

    def geturl(self):
        return self.url


@unittest.skipUnless(sys.platform == "linux", "Actual Linux atomic download paths")
class CdnDownloadTests(unittest.TestCase):
    def download(self, action, *, url=STEAMCMD_URL):
        with tempfile.TemporaryDirectory(prefix="l4d2-cdn-test-") as temporary:
            output = Path(temporary) / "steamcmd.tar.gz"
            opener = Mock()
            opener.open.side_effect = action
            with patch.object(packages, "build_opener", return_value=opener), patch.object(packages.time, "sleep"):
                data = packages.download(url, output, 30)
            return data, output.read_bytes(), [call.args[0].full_url for call in opener.open.call_args_list]

    def test_download_uses_same_backup_after_primary_connection_failure(self):
        content = gzip.compress(b"synthetic bootstrap content")
        def action(request, **kwargs):
            if request.full_url == STEAMCMD_URL:
                raise OSError("No route to host")
            return Response(content, request.full_url)
        data, downloaded, calls = self.download(action)
        self.assertEqual(calls, list(STEAMCMD_URLS[:2]))
        self.assertEqual(data["source"], STEAMCMD_URLS[1])
        self.assertEqual(downloaded, content)

    def test_html_verification_page_is_not_committed_before_backup(self):
        content = gzip.compress(b"synthetic bootstrap content")
        def action(request, **kwargs):
            return Response(b"<html>challenge</html>" if request.full_url == STEAMCMD_URL else content, request.full_url)
        data, downloaded, _ = self.download(action)
        self.assertEqual(downloaded, content)
        self.assertEqual(data["source"], STEAMCMD_URLS[1])

    def test_unsafe_redirect_does_not_trigger_fallback(self):
        calls = []
        def action(request, **kwargs):
            calls.append(request.full_url)
            raise CliError("Unsafe redirect", "unsafe_download")
        with self.assertRaises(CliError) as error:
            self.download(action)
        self.assertEqual(error.exception.code, "unsafe_download")
        self.assertEqual(calls, [STEAMCMD_URL])

    def test_unrelated_artifact_urls_are_not_replaced_with_steamcmd(self):
        url = "https://cdn.cloudflare.steamstatic.com/other.tar.gz"
        calls = []
        def action(request, **kwargs):
            calls.append(request.full_url)
            raise OSError("synthetic failure")
        with self.assertRaises(CliError):
            self.download(action, url=url)
        self.assertEqual(calls, [url] * 3)
