"""Doctor's human output and JSON contract, without contacting a real host."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from l4d2_cli import cli
from l4d2_cli.common import CliError, REDACTOR, result


def healthy_report():
    return {
        "platform": {"supported": True, "distribution": "ubuntu", "version": "20.04", "architecture": "x86_64"},
        "checks": [
            {"name": "dependencies", "status": "pass", "missing": []},
            {"name": "network", "status": "warn"},
            {"name": "registered_unit", "status": "pass"},
            {"name": "service", "status": "pass"},
            {"name": "local_game_query", "status": "pass"},
            {"name": "query_ownership", "status": "pass"},
            {"name": "private_configuration", "status": "unknown"},
            {"name": "external_client", "status": "unknown"},
        ],
        "network": {"required_failures": 0, "checks": [
            {"name": "steamcmd_dns", "status": "pass", "message": "DNS resolved"},
            {"name": "steam_bootstrap_login_content", "status": "unknown", "message": "Not executed during preflight."},
        ]},
        "server": {"installation": "installed", "service": {"ActiveState": "active", "SubState": "running"},
                   "query": {"map": "c1m1_hotel"}, "connection": {"join_method": "direct"}},
        "public_reachability": "unverified", "client_join": "unverified",
    }


class DoctorOutputTests(unittest.TestCase):
    def invoke(self, report=None, *, healthy=True, as_json=False, error=None):
        output, errors = io.StringIO(), io.StringIO()
        report = healthy_report() if report is None else report
        original = deepcopy(report)
        with patch.object(cli.manager, "doctor", return_value=(report, healthy), side_effect=error), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(["doctor"] + (["--json"] if as_json else []))
        self.assertEqual(report, original, "Presentation must not mutate diagnostic data")
        return code, output.getvalue(), errors.getvalue()

    def test_default_is_a_checklist_and_does_not_claim_unverified_checks_passed(self):
        code, output, errors = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("[通过] 系统环境", output)
        self.assertIn("[通过] 服务端安装", output)
        self.assertIn("c1m1_hotel", output)
        self.assertIn("[未验证] 私有配置完整性", output)
        self.assertIn("sudo l4d2 doctor", output)
        self.assertIn("[未验证] 外部客户端加入", output)
        self.assertNotIn("[通过] 外部客户端加入", output)
        self.assertIn("主检查汇总：", output)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(output)

    def test_failures_explain_missing_dependencies_and_stopped_service(self):
        report = healthy_report()
        report["checks"][0].update(status="fail", missing=["lib32gcc-s1"])
        report["checks"][3]["status"] = "fail"
        report["checks"][4]["status"] = "fail"
        report["server"]["service"] = {"ActiveState": "inactive", "SubState": "dead"}
        report["server"]["query"] = None
        code, output, errors = self.invoke(report, healthy=False)
        self.assertEqual(code, 1)
        self.assertIn("[失败] 运行依赖", output)
        self.assertIn("原因：缺少依赖：lib32gcc-s1", output)
        self.assertIn("建议：使用本机包管理器", output)
        self.assertIn("inactive/dead", output)
        self.assertIn("服务未运行，无法确认游戏就绪", output)
        self.assertIn("sudo l4d2 server logs --lines 50", output)
        self.assertNotIn("Doctor found failed checks", output + errors)

    def test_query_failure_preserves_the_actual_reason(self):
        report = healthy_report()
        report["checks"][4]["status"] = "fail"
        report["server"].update(query=None, query_error={"code": "query_timeout", "message": "No A2S reply after timeout"})
        code, output, _ = self.invoke(report, healthy=False)
        self.assertEqual(code, 1)
        self.assertIn("原因：No A2S reply after timeout", output)
        self.assertIn("sudo l4d2 server status", output)

    def test_network_failures_show_targets_and_category_specific_advice(self):
        for category, advice in [("dns", "DNS 解析"), ("timeout", "--timeout 60"), ("tls", "CA 证书"), ("http", "HTTP 状态")]:
            with self.subTest(category=category):
                report = healthy_report()
                report["network"] = {"required_failures": 1, "checks": [
                    {"name": "steamcmd_archive", "status": "fail", "category": category,
                     "message": "SYNTHETIC_PROBE_FAILURE", "target": "https://download.example.invalid/steamcmd"}]}
                code, output, _ = self.invoke(report)
                self.assertEqual(code, 0, "Download problems do not change an installed service's exit status")
                self.assertIn("[警告] 安装网络", output)
                self.assertIn("[失败] SteamCMD 工具下载", output)
                self.assertIn("SYNTHETIC_PROBE_FAILURE", output)
                self.assertIn("https://download.example.invalid/steamcmd", output)
                self.assertIn(advice, output)
                self.assertIn("已安装服务的运行情况见下方检查", output)

    def test_unsupported_platform_and_incomplete_install_have_actionable_output(self):
        platform = {"platform": {"supported": False, "unmet_requirements": ["linux", "systemd"]},
                    "checks": [{"name": "platform", "status": "fail"}]}
        code, output, _ = self.invoke(platform, healthy=False)
        self.assertEqual(code, 1)
        self.assertIn("缺少运行条件：Linux, 运行中的 systemd", output)
        self.assertIn("建议：使用受支持的 Linux", output)
        incomplete = {"checks": [{"name": "installation", "status": "fail", "message": "steam_content"}]}
        code, output, _ = self.invoke(incomplete, healthy=False)
        self.assertEqual(code, 1)
        self.assertIn("当前阶段：steam_content", output)
        self.assertIn("sudo l4d2 server install", output)

    def test_configuration_failure_and_group_client_guidance(self):
        report = healthy_report()
        report["checks"][6] = {"name": "managed_configuration", "status": "fail", "message": "Managed file changed: config.toml"}
        report["server"]["connection"]["join_method"] = "steam_group_lobby"
        code, output, _ = self.invoke(report, healthy=False)
        self.assertEqual(code, 1)
        self.assertIn("Managed file changed: config.toml", output)
        self.assertIn("备份受管配置", output)
        self.assertIn("Steam 组服务器列表/大厅加入", output)
        self.assertNotIn("使用 connect", output)

    def test_diagnosis_aborted_before_a_report_still_has_reason_and_advice(self):
        code, output, _ = self.invoke(error=CliError("Permission denied reading state", "permission_denied", 3))
        self.assertEqual(code, 3)
        self.assertIn("[失败] 诊断执行", output)
        self.assertIn("原因：Permission denied reading state", output)
        self.assertIn("sudo l4d2 doctor", output)

    def test_json_success_and_failure_keep_the_exact_existing_envelope(self):
        for healthy in (True, False):
            with self.subTest(healthy=healthy):
                report = healthy_report()
                code, output, errors = self.invoke(report, healthy=healthy, as_json=True)
                self.assertEqual(code, 0 if healthy else 1)
                self.assertEqual(errors, "")
                self.assertEqual(len(output.splitlines()), 1)
                self.assertEqual(json.loads(output), {
                    "schema_version": 1, "command": "l4d2 doctor", "ok": healthy,
                    "data": report, "warnings": [],
                    "error": None if healthy else {"code": "unhealthy", "message": "Doctor found failed checks."},
                })

    def test_output_redacts_secrets_before_rendering_reason_and_network_target(self):
        previous = REDACTOR.values.copy()
        self.addCleanup(setattr, REDACTOR, "values", previous)
        secret = "SYNTHETIC_DOCTOR_SECRET"
        REDACTOR.register(secret)
        report = healthy_report()
        report["network"]["checks"] = [{"name": "steamcmd_archive", "status": "fail",
            "message": "Connection rejected: " + secret, "target": "https://user:" + secret + "@proxy.example.invalid/"}]
        for as_json in (False, True):
            _, output, errors = self.invoke(report, as_json=as_json)
            self.assertNotIn(secret, output + errors)
            self.assertNotIn("https://user:", output + errors)
            self.assertIn("REDACTED", output)

    def test_other_commands_keep_their_existing_output(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result("l4d2 server configure", {"game_ready": False})
        self.assertEqual(json.loads(output.getvalue()), {"game_ready": False})
