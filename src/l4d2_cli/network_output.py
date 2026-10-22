"""Readable network preflight results, without changing probe or exit semantics."""
from __future__ import annotations

from collections import Counter

from .doctor_output import LABELS, STATES, text


SOURCES = {
    "online": "在线获取 SteamCMD 和游戏内容", "steamcmd_archive": "本地 SteamCMD 工具包",
    "steamcmd_path": "已有完整 SteamCMD 目录", "server_archive": "完整 Linux 游戏包",
}
MESSAGES = {
    "DNS resolved": "域名解析成功", "HTTP endpoint reachable": "HTTP 地址可访问",
    "DNS resolution failed": "域名解析失败", "Connection timed out": "连接超时",
    "Probe exceeded its deadline": "单项探测超过时限",
    "Network check total deadline exceeded.": "网络检查总时限已耗尽，后续项目未完成",
    "Probe could not produce a valid result": "探测程序未返回有效结果，本项尚未验证",
    "Network probe failed to execute or returned invalid data": "探测程序未能执行或返回了无效结果，此项检查失败",
    "SteamCMD acquisition is not needed for the selected source.": "所选来源无需下载 SteamCMD 工具包",
    "Complete local server package; strict offline installation selected.": "选择了完整本地游戏包及严格离线安装，跳过网络探测",
    "Not executed during preflight. Use a local complete game package if this phase fails.": "本次预检未执行 Steam 登录和完整内容下载",
    "No supported configured HTTP repository could be identified; inspect package manager configuration.": "未能识别受支持的 HTTP 软件源，请检查包管理器配置",
}


def reason(check: dict) -> str:
    if check.get("name") == "proxy_scope":
        return "已配置 HTTP 代理；其作用不覆盖全部 SteamCMD 协议" if check.get("configured") else "未配置 HTTP 代理；SteamCMD 内容连接仍需单独验证"
    message = text(check.get("message", "探测未提供详细信息"))
    notes = {
        "; direct DNS is diagnostic when the archive uses an HTTPS proxy.": "；此下载使用 HTTPS 代理，直连 DNS 仅供参考",
        "; Steam API reachability does not prove/disprove content access.": "；此 API 结果不能证明或否定游戏内容下载可用",
        "; original CDN DNS is diagnostic after archive fallback.": "；已改用备用 CDN，此处首选节点的解析仅供参考",
    }
    note = ""
    for suffix, translation in notes.items():
        if message.endswith(suffix):
            message, note = message[:-len(suffix)], translation
            break
    message = MESSAGES.get(message, message)
    if check.get("http_status") is not None:
        message += " (HTTP " + text(check["http_status"]) + ")"
    if check.get("status") == "pass" and len(check.get("attempts", [])) > 1:
        note += "；已切换到可用的备用 CDN"
    return message + note


def advice(check: dict) -> str:
    if check.get("status") in {"pass", "skipped"}:
        return ""
    name, category = check.get("name", ""), check.get("category")
    if name == "proxy_scope":
        return "确认代理地址和端口可达；HTTP 代理预检通过后，仍需实际验证 SteamCMD 内容下载。"
    if name == "steam_bootstrap_login_content":
        return "需通过实际下载验证；账号认证与内容访问权限由 Steam 决定。"
    if category == "dns":
        return "检查主机 DNS 解析与网络出口；若该项仅供参考，请结合实际下载地址的结果判断。"
    if category == "tls":
        return "检查系统时间、CA 证书及 HTTPS 代理设置，保留证书验证后重试。"
    if category == "timeout":
        return "检查网络出口和代理连通性，可用 l4d2 network check --timeout 60 复查。"
    if category == "http":
        return "根据 HTTP 状态检查访问限制、下载地址或代理设置，必要时使用已校验的本地包。"
    if category == "connection":
        return "检查出口、防火墙和代理地址及端口，确认目标服务可连接。"
    if category == "probe":
        return "探测未执行成功，不能据此判断网络正常；用 l4d2 network check --json 保存诊断并检查 CLI 运行环境。"
    if name.startswith("dependency_repositor"):
        return "核对本机包管理器的软件源地址、启用状态和网络连通性。"
    return "根据上述原因检查目标响应，再保留相同来源参数重试 l4d2 network check。"


def format_report(data: dict, error: dict | None = None) -> str:
    # Preflight failures wrap the same report in error.details['network'].
    report = data.get("network", data)
    checks = list(report.get("checks", []))
    lines = ["L4D2 安装网络检查", "只读预检，不安装文件或修改系统设置。"]
    if report.get("source"):
        lines.append("安装来源：" + SOURCES.get(report["source"], text(report["source"])))
    if report.get("checked_at"):
        lines.append("检查时间：" + text(report["checked_at"]))
    lines.append("")
    for check in checks:
        name, state = check.get("name", "未知检查"), check.get("status", "unknown")
        label = LABELS.get(name, text(name))
        if name.startswith("dependency_repository_"):
            label = "系统依赖软件源 " + text(name.rsplit("_", 1)[-1])
        if "required" in check:
            label += " (必要)" if check["required"] else " (参考)"
        lines.append(f"[{STATES.get(state, '未验证')}] {label}")
        if check.get("target"):
            lines.append("  目标：" + text(check["target"]))
        lines.append("  " + ("原因：" if state == "fail" else "说明：") + reason(check))
        for attempt in check.get("attempts", [])[:-1]:
            lines.append("  此前尝试：" + text(attempt.get("target", "未知地址")) +
                         " [" + STATES.get(attempt.get("status"), "未验证") + "] " + reason(attempt))
        suggestion = advice(check)
        if suggestion:
            lines.append("  建议：" + suggestion)
    if error and (error.get("code") != "network_preflight_failed" or not checks):
        lines.extend(["[失败] 检查执行", "  原因：" + text(error.get("message", "网络检查未完成")),
                      "  建议：根据错误核对运行环境、文件权限和来源参数；用 l4d2 network check --help 查看用法。"])
    if not checks and not error:
        lines.extend(["[未验证] 网络检查", "  说明：未返回检查结果",
                      "  建议：使用 l4d2 network check --json 获取完整诊断。"])

    if checks:
        counts = Counter(check.get("status") if check.get("status") in STATES else "unknown" for check in checks)
        summary = "，".join(f"{counts[state]} 项{label}" for state, label in STATES.items() if counts[state])
        lines.extend(["", "检查汇总：" + summary + "。"])
        failed = report.get("required_failures", 0)
        if failed:
            lines.append(f"结论：{failed} 项必要网络检查失败，当前安装来源的网络预检未通过。")
        elif report.get("offline"):
            lines.append("结论：离线模式未执行网络探测；这不代表游戏运行可以断网。")
        elif any(item.get("required") is True and item.get("status") not in {"pass", "skipped"} for item in checks):
            lines.append("结论：仍有必要检查未验证，不能确认安装网络可用。")
        else:
            lines.append("结论：未发现必要网络检查失败；本次结果不保证 Steam 登录或完整游戏下载成功。")
        if failed or any(item.get("required") is True and item.get("status") == "unknown" for item in checks):
            lines.append("复查：l4d2 network check（保留相同的安装来源参数）。")
            if report.get("source") == "online":
                lines.append("备用来源：在网络可用的 Linux 主机下载 SteamCMD 工具包，安装时用 --steamcmd-archive FILE；后续更新和游戏内容仍可能联网。")
            if report.get("source") in {"online", "steamcmd_archive", "steamcmd_path"}:
                lines.append("离线备用：在网络可用的 Linux 主机运行 l4d2 download server --output server.tar.gz；目标机备齐依赖后用 --server-archive FILE --offline 导入。")
    lines.extend(["", "完整机器可读结果：l4d2 network check --json（保留相同的安装来源参数）"])
    return "\n".join(lines)
