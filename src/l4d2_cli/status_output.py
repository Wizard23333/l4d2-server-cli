"""Readable server status; presentation does not perform management operations."""
from __future__ import annotations

from .doctor_output import text
from .model import SERVICE


INSTALLATION = {"installed": "已安装", "not_installed": "未安装", "installing": "安装中", "failed": "安装失败"}
ACTIVE = {"active": "运行中", "inactive": "已停止", "failed": "运行失败", "activating": "启动中",
          "deactivating": "停止中", "reloading": "重载中"}
AUTOSTART = {"enabled": "已启用", "enabled-runtime": "临时启用", "disabled": "已禁用",
             "masked": "已屏蔽", "masked-runtime": "临时屏蔽", "static": "静态单元", "not-found": "服务不存在"}
MODES = {"coop": "合作", "versus": "对抗"}
DIFFICULTY = {"easy": "简单", "normal": "普通", "hard": "困难", "impossible": "专家"}


def value(data: dict, key: str) -> str:
    return text(data[key]) if data.get(key) is not None else "未知"


def error_advice(code: str) -> str:
    if code == "permission_denied":
        return "使用 sudo l4d2 server status 复查。"
    if code == "unsupported_platform":
        return "在满足要求的 Linux 服务器运行；用 l4d2 doctor 查看缺少的运行条件。"
    return "用 sudo l4d2 doctor 查看具体问题，核对安装记录和服务状态。"


def format_report(data: dict, error: dict | None = None) -> str:
    lines = ["L4D2 服务器状态 (main)", "只读查询，不修改配置或启停服务。", ""]
    if error:
        lines.extend(["[失败] 状态查询", "  原因：" + text(error.get("message", "查询未完成")),
                      "  建议：" + error_advice(error.get("code", ""))])
    if not data:
        if not error:
            lines.extend(["[未验证] 服务器状态", "  说明：未返回状态数据；用 l4d2 server status --json 获取诊断。"])
        lines.extend(["", "完整机器可读结果：l4d2 server status --json"])
        return "\n".join(lines)

    installation = data.get("installation", "unknown")
    installed = installation == "installed"
    lines.append(("[通过] " if installed else "[未完成] ") + "安装状态：" +
                 INSTALLATION.get(installation, text(installation)))
    desired = data.get("desired_config") or {}
    if desired.get("game_dir"):
        lines.append("  目录：" + text(desired["game_dir"]))
    if not installed:
        if data.get("phase"):
            lines.append("  当前阶段：" + text(data["phase"]))
        lines.append("  建议：" + ("用 l4d2 server install --help 查看安装方式。" if installation == "not_installed" else
                                  "用 l4d2 doctor 核对安装记录和失败阶段。"))
    service = data.get("service") or {}
    if not service:
        if installed:
            lines.extend(["[未验证] 系统服务", "  说明：未返回服务状态，无法确认是否运行。"])
        lines.extend(["", "完整机器可读结果：l4d2 server status --json"])
        return "\n".join(lines)

    active = service.get("ActiveState")
    query = data.get("query") or {}
    ownership = data.get("port_owned_by_service")
    if data.get("game_ready") is True:
        lines.extend(["[就绪] 本机游戏服务", "  说明：服务运行、本地游戏查询和端口归属检查均通过。"])
    elif active == "active" and query and ownership is None:
        lines.extend(["[未验证] 本机游戏就绪状态", "  说明：游戏已响应，但端口归属尚未确认。"])
    elif data.get("game_ready") is False:
        lines.extend(["[未就绪] 本机游戏服务", "  说明：尚未满足全部本机就绪条件，具体原因见下方。"])
    else:
        lines.extend(["[未验证] 本机游戏就绪状态", "  说明：没有完整的本机就绪检查结果。"])

    lines.extend(["", "[" + ACTIVE.get(active, "未验证") + "] 系统服务：" + SERVICE,
                  "  状态：" + value(service, "ActiveState") + " / " + value(service, "SubState"),
                  "  主进程：" + (value(service, "MainPID") if service.get("MainPID") else "无"),
                  "  开机启动：" + AUTOSTART.get(service.get("UnitFileState"), value(service, "UnitFileState"))])
    if service.get("ExecMainStartTimestamp"):
        lines.append("  进程启动时间：" + text(service["ExecMainStartTimestamp"]))
    if service.get("NRestarts") is not None:
        lines.append("  自动重启次数：" + text(service["NRestarts"]))
    if active == "failed":
        lines.extend(["  主进程退出状态：" + value(service, "ExecMainStatus"),
                      "  建议：用 sudo l4d2 server logs --lines 50 和 sudo l4d2 doctor 查看启动错误。"])
    elif active == "inactive":
        lines.append("  说明：服务当前已停止。")
    endpoint = data.get("running_endpoint") or {}
    if endpoint:
        lines.append("  绑定配置：" + value(endpoint, "bind") + ":" + value(endpoint, "port") + " / UDP")

    lines.append("")
    if query:
        lines.extend(["[通过] 本地游戏查询", "  查询名称：" + value(query, "name"),
                      "  查询地图：" + value(query, "map"),
                      "  人数：" + value(query, "players") + " / " + value(query, "max_players") +
                      "（真人 " + value(query, "human_players") + "，机器人 " + value(query, "bots") + "）"])
        if query.get("version"):
            lines.append("  游戏版本：" + text(query["version"]))
        if isinstance(query.get("password_required"), bool):
            lines.append("  加入密码：" + ("需要" if query["password_required"] else "不需要"))
    else:
        failure = data.get("query_error") or {}
        if failure:
            lines.extend(["[失败] 本地游戏查询", "  原因：" + text(failure.get("message", "查询未成功")),
                          "  建议：用 sudo l4d2 doctor 和 sudo l4d2 server logs --lines 50 复查服务与 UDP 端口。"])
        else:
            lines.extend(["[未验证] 本地游戏查询", "  说明：尚无游戏查询结果。" if service.get("MainPID") else
                          "  说明：服务未报告运行进程，未执行游戏查询。"])
    if ownership is True:
        lines.append("[通过] 游戏端口归属：属于托管服务进程")
    elif ownership is False:
        lines.extend(["[失败] 游戏端口归属：未确认属于托管服务进程",
                      "  建议：用 sudo l4d2 doctor 检查端口占用；查询有响应仍不能认定托管服务就绪。"])
    elif service.get("MainPID"):
        lines.extend(["[未验证] 游戏端口归属", "  建议：用 sudo l4d2 server status 复查；若仍未确认，用 sudo l4d2 doctor 排查。"])

    lines.append("")
    pending = data.get("configuration_pending")
    if pending is True:
        lines.extend(["[待应用] 配置生效状态", "  说明：保存的配置尚未全部应用到当前进程；需要时在允许中断游戏后安排重启。"])
    elif pending is False:
        lines.extend(["[通过] 配置生效状态", "  说明：保存的配置版本与运行记录一致。"])
    else:
        lines.extend(["[未验证] 配置生效状态", "  说明：无法确认当前进程使用的配置版本。"])
    if data.get("desired_revision") is not None or data.get("applied_revision") is not None:
        lines.append("  配置版本：已保存 " + value(data, "desired_revision") + " / 已应用 " + value(data, "applied_revision"))
    settings = [MODES.get(desired["mode"], text(desired["mode"]))] if desired.get("mode") else []
    if desired.get("difficulty") and desired.get("mode") == "coop":
        settings.append(DIFFICULTY.get(desired["difficulty"], text(desired["difficulty"])))
    if desired.get("map"):
        settings.append("默认地图 " + text(desired["map"]))
    if settings:
        lines.append("  已保存设置：" + " / ".join(settings))
    if data.get("pending_transaction"):
        lines.extend(["[警告] 存在未完成的配置事务", "  建议：用 sudo l4d2 doctor 核对状态，处理后再执行配置或启停操作。"])

    connection = data.get("connection") or {}
    if connection:
        lines.extend(["", "[提示] 连接方式"])
        if connection.get("local_query"):
            lines.append("  本地查询地址：" + text(connection["local_query"]) + "（用于本机检查）")
        if connection.get("visibility") == "group" or connection.get("lobby_only"):
            lines.extend(["  房间可见性（配置）：Steam 组", "  Steam 组：" + value(connection, "steam_group_id"),
                          "  加入方式：L4D2 → Steam 组服务器 → 通过大厅加入",
                          "  说明：空房间优先组成员，组列表和实际加入效果需用游戏客户端确认。"])
        elif connection.get("visibility") == "public":
            lines.append("  房间可见性（配置）：公开")
            if connection.get("player_command"):
                lines.extend(["  玩家命令：" + text(connection["player_command"]),
                              "  将 YOUR_SERVER_IP 替换为服务器公网地址，并确认游戏 UDP 端口已放行。"])
        lines.extend(["[未验证] 公网可达性与客户端加入", "  说明：本机查询结果不能确认外部玩家可加入，需用外部游戏客户端验证。"])
    lines.extend(["", "完整机器可读结果：l4d2 server status --json"])
    return "\n".join(lines)
