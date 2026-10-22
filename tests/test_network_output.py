"""Readable network output and JSON compatibility; no live network required."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from l4d2_cli import cli
from l4d2_cli.common import CliError, REDACTOR, result


def report():
    return {"source": "online", "offline": False, "checked_at": "2026-10-17T12:00:00+00:00",
            "checks": [
                {"name": "steamcmd_dns", "status": "pass", "required": True,
                 "target": "download.example.invalid", "message": "DNS resolved"},
                {"name": "steamcmd_archive", "status": "pass", "required": True,
                 "target": "https://download.example.invalid/steamcmd.tar.gz",
                 "message": "HTTP endpoint reachable", "http_status": 200},
                {"name": "steam_bootstrap_login_content", "status": "unknown",
                 "message": "Not executed during preflight. Use a local complete game package if this phase fails."},
            ], "required_failures": 0, "timed_out": False, "download_guaranteed": False,
            "next_steps": ["Run: l4d2 network check (or the same source options) to retry diagnosis."]}


class NetworkOutputTests(unittest.TestCase):
    def invoke(self, data=None, *, as_json=False, arguments=(), error=None):
        data = report() if data is None else data
        before = deepcopy(data)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli.host, "require_platform", side_effect=error), \
                patch.object(cli.host, "missing_dependencies", return_value=[]), \
                patch.object(cli.network, "check", return_value=data), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(["network", "check", *arguments] + (["--json"] if as_json else []))
        self.assertEqual(data, before, "Formatting must not modify the network report")
        return code, output.getvalue(), errors.getvalue()

    def test_default_is_a_readable_checklist_with_targets_and_scope(self):
        code, output, errors = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("L4D2 安装网络检查", output)
        self.assertIn("安装来源：在线获取", output)
        self.assertIn("[通过] SteamCMD 域名解析 (必要)", output)
        self.assertIn("目标：download.example.invalid", output)
        self.assertIn("HTTP 地址可访问 (HTTP 200)", output)
        self.assertIn("[未验证] Steam 登录与完整内容下载", output)
        self.assertIn("不保证 Steam 登录或完整游戏下载成功", output)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(output)

    def test_required_failure_shows_reason_suggestions_and_fallback_sources(self):
        data = report()
        data["checks"][1].update(status="fail", message="HTTP 403", category="http")
        data["required_failures"] = 1
        code, output, errors = self.invoke(data)
        self.assertEqual(code, 1)
        self.assertEqual(errors, "")
        self.assertIn("[失败] SteamCMD 工具下载 (必要)", output)
        self.assertIn("原因：HTTP 403", output)
        self.assertIn("建议：根据 HTTP 状态", output)
        self.assertIn("1 项必要网络检查失败", output)
        self.assertIn("--steamcmd-archive FILE", output)
        self.assertIn("--server-archive FILE --offline", output)
        self.assertNotIn("Required network preflight failed", output)

    def test_timeout_exit_code_and_advice_are_preserved(self):
        data = report()
        data["checks"][0].update(status="fail", category="timeout", message="Network check total deadline exceeded.")
        data.update(required_failures=1, timed_out=True)
        code, output, _ = self.invoke(data)
        self.assertEqual(code, 124)
        self.assertIn("网络检查总时限已耗尽", output)
        self.assertIn("--timeout 60", output)

    def test_failed_reference_probe_is_distinct_from_a_required_failure(self):
        data = report()
        data["checks"][0].update(status="warn", required=False, category="dns",
            message="DNS resolution failed; direct DNS is diagnostic when the archive uses an HTTPS proxy.")
        code, output, _ = self.invoke(data)
        self.assertEqual(code, 0)
        self.assertIn("[警告] SteamCMD 域名解析 (参考)", output)
        self.assertIn("直连 DNS 仅供参考", output)
        self.assertIn("未发现必要网络检查失败", output)

    def test_unknown_required_probe_is_not_reported_as_a_pass(self):
        data = report()
        data["checks"][0].update(status="unknown", category="probe", message="Probe could not produce a valid result")
        code, output, _ = self.invoke(data)
        self.assertEqual(code, 0, "Presentation must not change existing unknown-probe exit semantics")
        self.assertIn("[未验证] SteamCMD 域名解析 (必要)", output)
        self.assertIn("探测程序未返回有效结果", output)
        self.assertIn("不能确认安装网络可用", output)
        self.assertNotIn("结论：未发现必要网络检查失败", output)

    def test_offline_source_explicitly_skips_network_and_avoids_online_advice(self):
        data = {"source": "server_archive", "offline": True, "required_failures": 0, "checks": [
            {"name": "installation_network", "status": "skipped",
             "message": "Complete local server package; strict offline installation selected."}]}
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "server.tar.gz"
            archive.write_bytes(b"source validation fixture")
            code, output, _ = self.invoke(data, arguments=("--server-archive", str(archive), "--offline"))
        self.assertEqual(code, 0)
        self.assertIn("安装来源：完整 Linux 游戏包", output)
        self.assertIn("[跳过] 安装网络", output)
        self.assertIn("离线模式未执行网络探测", output)
        self.assertNotIn("备用来源：", output)

    def test_json_success_and_wrapped_failure_match_existing_contract(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                data = report()
                if failed:
                    data["checks"][0].update(status="fail", category="dns", message="DNS resolution failed")
                    data["required_failures"] = 1
                code, output, errors = self.invoke(data, as_json=True)
                self.assertEqual(code, 1 if failed else 0)
                self.assertEqual(errors, "")
                self.assertEqual(len(output.splitlines()), 1)
                payload = json.loads(output)
                self.assertEqual(set(payload), {"schema_version", "command", "ok", "data", "warnings", "error"})
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["command"], "l4d2 network check")
                self.assertEqual(payload["ok"], not failed)
                self.assertEqual(payload["data"], {"network": data} if failed else data)
                self.assertEqual(payload["error"]["code"] if failed else payload["error"], "network_preflight_failed" if failed else None)

    def test_fatal_error_before_probes_is_readable_and_keeps_its_exit_code(self):
        failure = CliError("SYNTHETIC_PLATFORM_FAILURE", "unsupported_platform", 4, {"platform": {"supported": False}})
        code, output, _ = self.invoke(error=failure)
        self.assertEqual(code, 4)
        self.assertIn("[失败] 检查执行", output)
        self.assertIn("原因：SYNTHETIC_PLATFORM_FAILURE", output)
        self.assertIn("l4d2 network check --help", output)

    def test_output_redacts_credentials_in_targets_and_failure_messages(self):
        previous = REDACTOR.values.copy()
        self.addCleanup(setattr, REDACTOR, "values", previous)
        secret = "SYNTHETIC_NETWORK_SECRET"
        REDACTOR.register(secret)
        data = report()
        data["checks"][0].update(status="fail", message="Connection failed: " + secret,
            target="https://user:" + secret + "@proxy.example.invalid/")
        data["required_failures"] = 1
        for as_json in (False, True):
            _, output, errors = self.invoke(data, as_json=as_json)
            self.assertNotIn(secret, output + errors)
            self.assertNotIn("https://user:", output + errors)
            self.assertIn("REDACTED", output)

    def test_network_status_and_other_commands_keep_existing_output(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result("l4d2 network status", {"configured": False})
        self.assertEqual(json.loads(output.getvalue()), {"configured": False})
