"""Status presentation, readiness distinctions, and the existing JSON contract."""
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
from l4d2_cli.common import CliError, REDACTOR


def report():
    return {
        "installation": "installed", "phase": "configured",
        "service": {"ActiveState": "active", "SubState": "running", "MainPID": 1234,
                    "UnitFileState": "disabled", "NRestarts": 0, "ExecMainStatus": 0},
        "desired_config": {"game_dir": "/opt/l4d2", "name": "Vanilla", "mode": "coop",
                           "difficulty": "normal", "map": "c1m1_hotel"},
        "running_endpoint": {"bind": "0.0.0.0", "port": 27015},
        "query": {"name": "Vanilla", "map": "c1m2_streets", "players": 0, "bots": 0,
                  "human_players": 0, "max_players": 4, "version": "2.2.4.3", "password_required": False},
        "query_error": None, "port_owned_by_service": True, "game_ready": True, "readiness": "ready",
        "desired_revision": 2, "applied_revision": 2, "configuration_pending": False,
        "pending_transaction": False,
        "connection": {"local_query": "10.0.0.3:27015", "visibility": "public", "lobby_only": False,
                       "player_command": "connect YOUR_SERVER_IP:27015", "public_reachability": "unverified",
                       "client_join": "unverified"},
    }


class StatusOutputTests(unittest.TestCase):
    def invoke(self, data=None, *, as_json=False, error=None):
        data = report() if data is None else data
        original = deepcopy(data)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli.manager, "status", return_value=data, side_effect=error), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(["server", "status"] + (["--json"] if as_json else []))
        self.assertEqual(data, original, "Rendering must not mutate status data")
        return code, output.getvalue(), errors.getvalue()

    def test_ready_default_shows_process_game_and_player_information(self):
        code, output, errors = self.invoke()
        self.assertEqual((code, errors), (0, ""))
        for expected in ("L4D2 服务器状态 (main)", "[就绪] 本机游戏服务", "目录：/opt/l4d2",
                         "[运行中] 系统服务：l4d2-main.service", "主进程：1234", "开机启动：已禁用",
                         "自动重启次数：0", "查询地图：c1m2_streets", "人数：0 / 4（真人 0，机器人 0）",
                         "加入密码：不需要", "游戏版本：2.2.4.3", "[通过] 游戏端口归属",
                         "[未验证] 公网可达性与客户端加入"):
            self.assertIn(expected, output)
        self.assertNotIn('"game_ready":', output)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(output)

    def test_query_success_with_unknown_ownership_is_not_marked_ready(self):
        data = report()
        data.update(game_ready=False, readiness="ownership_unknown", port_owned_by_service=None)
        code, output, errors = self.invoke(data)
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("[未验证] 本机游戏就绪状态", output)
        self.assertIn("[通过] 本地游戏查询", output)
        self.assertIn("sudo l4d2 server status", output)
        self.assertNotIn("[就绪]", output)

    def test_wrong_port_owner_does_not_promote_an_unrelated_query_to_ready(self):
        data = report()
        data.update(game_ready=False, port_owned_by_service=False)
        _, output, _ = self.invoke(data)
        self.assertIn("[失败] 游戏端口归属", output)
        self.assertIn("查询有响应仍不能认定托管服务就绪", output)
        self.assertNotIn("[就绪]", output)

    def test_query_failure_explains_reason_and_next_step(self):
        data = report()
        data.update(game_ready=False, query=None, query_error={"code": "query_timeout", "message": "A2S query timed out."})
        code, output, _ = self.invoke(data)
        self.assertEqual(code, 0, "A successfully retrieved not-ready status keeps its existing exit code")
        self.assertIn("[失败] 本地游戏查询", output)
        self.assertIn("原因：A2S query timed out.", output)
        self.assertIn("server logs --lines 50", output)
        self.assertNotIn("查询地图：", output)

    def test_stopped_service_does_not_show_saved_default_as_live_map(self):
        data = report()
        data["service"].update(ActiveState="inactive", SubState="dead", MainPID=0)
        data.update(game_ready=False, query=None, port_owned_by_service=None,
                    configuration_pending="unknown", applied_revision=None)
        _, output, _ = self.invoke(data)
        self.assertIn("[已停止] 系统服务", output)
        self.assertIn("主进程：无", output)
        self.assertIn("未执行游戏查询", output)
        self.assertIn("默认地图 c1m1_hotel", output)
        self.assertNotIn("查询地图：", output)
        self.assertNotIn("[通过] 游戏端口归属", output)

    def test_failed_service_shows_exit_status_and_logs_advice(self):
        data = report()
        data["service"].update(ActiveState="failed", SubState="failed", MainPID=0, ExecMainStatus=1)
        data.update(game_ready=False, query=None, port_owned_by_service=None)
        _, output, _ = self.invoke(data)
        self.assertIn("[运行失败] 系统服务", output)
        self.assertIn("主进程退出状态：1", output)
        self.assertIn("sudo l4d2 server logs --lines 50", output)

    def test_pending_config_is_distinct_from_running_query_and_transaction(self):
        data = report()
        data.update(desired_revision=3, configuration_pending=True, pending_transaction=True)
        _, output, _ = self.invoke(data)
        self.assertIn("[待应用] 配置生效状态", output)
        self.assertIn("已保存 3 / 已应用 2", output)
        self.assertIn("查询地图：c1m2_streets", output)
        self.assertIn("默认地图 c1m1_hotel", output)
        self.assertIn("[警告] 存在未完成的配置事务", output)
        self.assertNotIn("保存的配置版本与运行记录一致", output)

    def test_unknown_applied_revision_does_not_claim_config_is_synced(self):
        data = report()
        data.update(applied_revision=None, configuration_pending="unknown")
        _, output, _ = self.invoke(data)
        self.assertIn("[未验证] 配置生效状态", output)
        self.assertIn("已保存 2 / 已应用 未知", output)
        self.assertNotIn("[通过] 配置生效状态", output)

    def test_group_room_explains_lobby_join_without_a_direct_connect_command(self):
        data = report()
        data["connection"].update(visibility="group", lobby_only=True, steam_group_id="123456", player_command=None)
        _, output, _ = self.invoke(data)
        self.assertIn("房间可见性（配置）：Steam 组", output)
        self.assertIn("Steam 组：123456", output)
        self.assertIn("通过大厅加入", output)
        self.assertIn("本地查询地址：10.0.0.3:27015（用于本机检查）", output)
        self.assertNotIn("connect ", output)

    def test_public_room_uses_a_public_address_placeholder(self):
        _, output, _ = self.invoke()
        self.assertIn("玩家命令：connect YOUR_SERVER_IP:27015", output)
        self.assertNotIn("connect 0.0.0.0", output)
        self.assertNotIn("connect 10.0.0.3", output)

    def test_not_installed_is_an_explicit_successful_status_result(self):
        data = {"installation": "not_installed", "service": None, "query": None, "game_ready": False}
        code, output, errors = self.invoke(data)
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("安装状态：未安装", output)
        self.assertIn("server install --help", output)
        self.assertNotIn("[就绪]", output)

    def test_missing_data_cannot_look_healthy(self):
        for data in ({}, {"installation": "installed", "service": None}):
            with self.subTest(data=data):
                _, output, _ = self.invoke(data)
                self.assertIn("[未验证]", output)
                self.assertNotIn("[就绪]", output)

    def test_json_keeps_the_full_envelope_and_unmodified_status(self):
        data = report()
        code, output, errors = self.invoke(data, as_json=True)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(len(output.splitlines()), 1)
        self.assertEqual(json.loads(output), {"schema_version": 1, "command": "l4d2 server status",
                         "ok": True, "data": data, "warnings": [], "error": None})

    def test_error_human_and_json_preserve_the_existing_failure_code(self):
        error = CliError("Permission denied.", "permission_denied", 3)
        code, output, errors = self.invoke(error=error)
        self.assertEqual((code, errors), (3, ""))
        self.assertIn("[失败] 状态查询", output)
        self.assertIn("原因：Permission denied.", output)
        self.assertIn("sudo l4d2 server status", output)
        code, output, _ = self.invoke(error=error, as_json=True)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["error"], {"code": "permission_denied", "message": "Permission denied."})

    def test_missing_player_counts_are_unknown_instead_of_fabricated_zeroes(self):
        data = report()
        data["query"] = {"map": "c1m2_streets"}
        _, output, _ = self.invoke(data)
        self.assertIn("人数：未知 / 未知（真人 未知，机器人 未知）", output)

    def test_redaction_precedes_rendering_and_game_strings_cannot_inject_rows(self):
        previous = REDACTOR.values.copy()
        self.addCleanup(setattr, REDACTOR, "values", previous)
        secret = "SYNTHETIC_STATUS_SECRET"
        REDACTOR.register(secret)
        data = report()
        data["query"]["name"] = secret + "\n[失败] fabricated"
        for as_json in (False, True):
            _, output, errors = self.invoke(data, as_json=as_json)
            self.assertNotIn(secret, output + errors)
            self.assertIn("REDACTED", output)
            self.assertNotIn("\n[失败] fabricated", output)
