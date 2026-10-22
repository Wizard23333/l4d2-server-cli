"""Human-readable doctor presentation; does not run or change any checks."""
from __future__ import annotations

from collections import Counter


STATES = {"pass": "通过", "fail": "失败", "warn": "警告", "unknown": "未验证", "skipped": "跳过"}
LABELS = {
    "platform": "系统环境", "dependencies": "运行依赖", "installation": "服务端安装",
    "network": "安装网络", "registered_unit": "服务登记", "service": "游戏服务",
    "local_game_query": "本机游戏查询", "query_ownership": "端口进程归属",
    "managed_configuration": "配置完整性", "private_configuration": "私有配置完整性",
    "external_client": "外部客户端加入", "diagnosis": "诊断执行",
    "steamcmd_dns": "SteamCMD 域名解析", "steamcmd_archive": "SteamCMD 工具下载",
    "steam_entry_dns": "Steam API 域名解析", "steam_http_entry": "Steam API 访问",
    "proxy_scope": "代理适用范围", "steam_bootstrap_login_content": "Steam 登录与完整内容下载",
    "dependency_repositories": "系统依赖软件源", "installation_network": "安装网络",
}
REQUIREMENTS = {
    "linux": "Linux", "x86_64": "x86_64 架构", "glibc": "glibc",
    "python_3_8": "Python 3.8+", "systemd": "运行中的 systemd",
    "package_adapter": "受支持的包管理器", "systemd_cgroup": "systemd cgroup",
}


def text(value) -> str:
    # Diagnostics and server responses must not introduce fake checklist lines.
    return " ".join(str(value).split())


def reason(check: dict, report: dict) -> str:
    name, state = check.get("name"), check.get("status", "unknown")
    platform, server = report.get("platform", {}), report.get("server", {})
    service = server.get("service") or {}
    if name == "platform":
        if state == "pass":
            return " / ".join(text(platform[key]) for key in ("distribution", "version", "architecture") if platform.get(key)) or "主机满足运行要求"
        missing = platform.get("unmet_requirements", [])
        return "缺少运行条件：" + ", ".join(REQUIREMENTS.get(item, text(item)) for item in missing) if missing else text(check.get("message", "主机不满足运行要求"))
    if name == "dependencies":
        return "缺少依赖：" + ", ".join(map(text, check["missing"])) if check.get("missing") else "必需依赖已安装"
    if name == "installation":
        return "CLI 登记的 main 实例已安装" if state == "pass" else "安装未完成；当前阶段：" + text(check.get("message", "未知"))
    if name == "network":
        failed = report.get("network", {}).get("required_failures", 0)
        if failed:
            suffix = "；已安装服务的运行情况见下方检查" if server.get("installation") == "installed" else ""
            return f"{failed} 项必要下载网络检查失败" + suffix
        return "轻量网络预检不能证明 Steam 登录及完整内容下载可用"
    if name == "service":
        return "服务状态：" + text(service.get("ActiveState", "未知")) + "/" + text(service.get("SubState", "未知"))
    if name == "local_game_query":
        if state == "pass":
            query = server.get("query") or {}
            return "游戏已响应；地图：" + text(query.get("map", "未知"))
        if service.get("ActiveState") != "active":
            return "服务未运行，无法确认游戏就绪"
        failure = server.get("query_error") or {}
        return text(failure.get("message", "未收到有效的游戏查询响应"))
    if name == "query_ownership":
        return {"pass": "查询端口属于受管游戏进程", "fail": "未确认查询端口由受管游戏进程占用"}.get(state, "当前权限或进程信息不足，无法确认端口归属")
    if name == "private_configuration":
        return "普通用户未检查私有配置文件"
    if name == "external_client":
        return "尚未通过外部游戏客户端验证实际加入与游玩"
    if check.get("message"):
        return text(check["message"])
    return {"registered_unit": "服务定义和运行账号符合登记信息", "managed_configuration": "受管配置与记录的摘要一致"}.get(name, "检查通过" if state == "pass" else "检查未提供更多信息")


def advice(check: dict, report: dict) -> str:
    if check.get("status") in {"pass", "skipped"}:
        return ""
    name = check.get("name")
    if name == "platform":
        return "使用受支持的 Linux x86_64/glibc 主机，补齐上列运行条件后重试。"
    if name == "dependencies":
        return "使用本机包管理器补齐列出的依赖，再运行 l4d2 doctor。"
    if name == "installation":
        return "确认目标目录和安装来源，再用 sudo l4d2 server install 安装或续装。"
    if name == "network":
        return "根据下方网络详情排查；可用 l4d2 network check 单独复查下载网络。"
    if name == "registered_unit":
        return "核对 /etc/systemd/system/l4d2-main.service、运行账号及额外覆盖配置；先备份再处理外部修改。"
    if name == "service":
        return "先看 sudo l4d2 server logs --lines 50；确认需要启动后执行 sudo l4d2 server start。"
    if name == "local_game_query":
        return "检查 sudo l4d2 server status 和 sudo l4d2 server logs --lines 50，核对启动错误及游戏 UDP 端口。"
    if name == "query_ownership":
        return "运行 sudo l4d2 doctor 复查权限与端口归属，并核对服务状态及端口占用。"
    if name == "managed_configuration":
        return "备份受管配置，核对提示中的外部修改或未完成事务；用 CLI 管理配置后再复查。"
    if name == "private_configuration":
        return "运行 sudo l4d2 doctor，检查私有配置完整性。"
    if name == "external_client":
        connection = report.get("server", {}).get("connection") or {}
        join = "通过 Steam 组服务器列表/大厅加入" if connection.get("join_method") == "steam_group_lobby" else "使用 connect YOUR_SERVER_IP:游戏端口 直连"
        return "确认游戏 UDP 端口已在防火墙和云安全组放行，再从外部客户端" + join + "并实际游玩。"
    if name == "proxy_scope":
        return "HTTP 代理只覆盖 HTTP 请求；SteamCMD 内容连接需另行验证。"
    if name == "steam_bootstrap_login_content":
        return "需通过实际下载验证；失败时可在网络可用的 Linux 主机制包，再离线导入。"
    category = check.get("category")
    if category == "dns":
        return "检查 DNS 解析和网络出口，再运行 l4d2 network check。"
    if category == "tls":
        return "检查系统时间、CA 证书和 HTTPS 代理配置，再重试网络检查。"
    if category == "timeout":
        return "检查网络出口及代理连通性；可用 l4d2 network check --timeout 60 复查。"
    if category == "http":
        return "检查该地址的 HTTP 状态及代理设置；必要时使用已校验的本地安装包。"
    if name == "diagnosis":
        if check.get("code") == "permission_denied":
            return "使用有权限的账号运行 sudo l4d2 doctor。"
        return "检查上述错误，运行 l4d2 doctor --json 获取完整诊断；参数用法见 l4d2 doctor --help。"
    return "检查上述原因；用 l4d2 network check 复查网络，或 l4d2 doctor --json 获取完整详情。"


def format_report(report: dict, error: dict | None = None) -> str:
    """Format already-redacted data without changing the machine-readable report."""
    checks = list(report.get("checks", []))
    names = {check.get("name") for check in checks}
    if report.get("platform") and "platform" not in names:
        checks.insert(0, {"name": "platform", "status": "pass" if report["platform"].get("supported") else "fail"})
    if report.get("server", {}).get("installation") == "installed" and "installation" not in names:
        index = next((i for i, check in enumerate(checks) if check.get("name") == "registered_unit"), len(checks))
        checks.insert(index, {"name": "installation", "status": "pass"})
    if "external_client" not in names and (report.get("client_join") == "unverified" or report.get("public_reachability") == "unverified"):
        checks.append({"name": "external_client", "status": "unknown"})
    if error and (error.get("code") != "unhealthy" or not report.get("checks")):
        checks.append({"name": "diagnosis", "status": "fail", "code": error.get("code"), "message": error.get("message", "诊断未完成")})
    if not checks:
        checks.append({"name": "diagnosis", "status": "unknown", "message": "未返回检查结果"})

    lines = ["L4D2 健康检查 (main)", "只读检查，不修改配置或启停服务。", ""]

    def append(check, indent=""):
        state = check.get("status", "unknown")
        label = LABELS.get(check.get("name"), text(check.get("name", "未知检查")))
        if str(check.get("name", "")).startswith("dependency_repository_"):
            label = "系统依赖软件源 " + text(check["name"].rsplit("_", 1)[-1])
        description = reason(check, report)
        lines.append(f"{indent}[{STATES.get(state, '未验证')}] {label}" + ("：" + description if state == "pass" else ""))
        if state != "pass":
            lines.append(indent + "  " + ("原因：" if state == "fail" else "说明：") + description)
            if check.get("target"):
                lines.append(indent + "  目标：" + text(check["target"]))
            suggestion = advice(check, report)
            if suggestion:
                lines.append(indent + "  建议：" + suggestion)

    for check in checks:
        append(check)
        if check.get("name") == "network":
            for entry in report.get("network", {}).get("checks", []):
                append(entry, "  ")
    counts = Counter(check.get("status", "unknown") if check.get("status") in STATES else "unknown" for check in checks)
    summary = "，".join(f"{counts[state]} 项{label}" for state, label in STATES.items() if counts[state])
    lines.extend(["", "主检查汇总：" + summary + "。", "完整机器可读结果：l4d2 doctor --json"])
    return "\n".join(lines)
