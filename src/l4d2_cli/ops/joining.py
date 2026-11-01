"""Shareable joining instructions and evidence-scoped connection checks."""
from __future__ import annotations

import ipaddress
import re

from .. import a2s
from ..common import CliError
from . import rooms


def address(value):
    value = str(value or '').strip()
    try:
        ip = ipaddress.IPv4Address(value)
        if ip.is_unspecified or ip.is_multicast or ip == ipaddress.IPv4Address('255.255.255.255'):
            raise ValueError()
        return str(ip)
    except ValueError:
        if (not value or len(value) > 253 or re.fullmatch(r'[0-9.]+', value) or
                any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p)
                    for p in value.split('.'))):
            raise CliError('填写实际 IPv4 或域名，不要包含协议、端口、密码或控制台命令。', 'invalid_address', 2)
        return value.lower()


def probe(target, port=27015, timeout=3):
    target = address(target)
    if type(port) is not int or not 1 <= port <= 65535:
        raise CliError('游戏端口必须在 1–65535 之间。', 'invalid_port', 2)
    try:
        query = a2s.query(target, port, min(timeout, 30))
        return {'address': target, 'port': port, 'query_reachable': True, 'query': query,
                'client_join': 'unverified', 'probe_scope': 'current_machine',
                'next_steps': ['此机器能够查询 L4D2；仍需在游戏内实际加入。']}
    except (CliError, OSError) as exc:
        return {'address': target, 'port': port, 'query_reachable': False,
                'query_error': getattr(exc, 'code', 'query_unavailable'),
                'client_join': 'unverified', 'probe_scope': 'current_machine',
                'next_steps': ['核对地址及实际游戏 UDP 端口。',
                               '服主检查服务、云安全组、本机防火墙及路由器端口转发。',
                               '查询超时无法单独区分 NAT、防火墙、服务未启动或网络故障。']}


def invite(room, target=None):
    report = rooms.status(room)
    live = report.get('running_config') or report.get('configuration') or room
    if room.get('running') and report.get('configuration_pending'):
        live = {**live, **room['running']}
    endpoint = report.get('running_endpoint') or live
    port = endpoint.get('port', room['port'])
    target = address(target) if target else 'YOUR_SERVER_IP'
    group = live.get('visibility', room.get('visibility')) == 'group'
    lines = ['一起玩求生之路 2', '房间：' + live.get('name', room.get('name', room['id'])),
             '地图：' + ((report.get('query') or {}).get('map') or live.get('map', room['map'])),
             '游戏 UDP 端口：' + str(port)]
    if group:
        lines += ['加入方式：在 L4D2 的 Steam 组服务器中寻找房间，再通过大厅加入。',
                  'Steam 组短 ID：' + live.get('steam_group_id', room.get('steam_group_id', '')),
                  'Steam 组优先不等于严格好友白名单。']
    else:
        lines += ['在游戏设置中启用开发者控制台，输入：', f'connect {target}:{port}',
                  f'Steam 快捷链接：steam://connect/{target}:{port}']
    if target == 'YOUR_SERVER_IP':
        lines.append('服主发送前请把 YOUR_SERVER_IP 替换为实际公网地址。')
    lines += ['三方图：所有玩家先按服主的地图分享清单订阅全部分包与依赖，等下载完成后再进服。',
              '连不上：将游戏错误原文、发生时间和使用的地址发给服主；不要发送账号或 RCON 密码。']
    if (report.get('access') or {}).get('mode') == 'friends':
        lines.append('好友名单模式：请先让服主将你的个人 SteamID64 加入名单，再使用已登录 Steam 的正版客户端加入。')
    if not report.get('game_ready'):
        lines.append('服主提示：当前房间尚未确认就绪，发送邀请前先运行 room connect-check。')
    if report.get('configuration_pending'):
        lines.append('服主提示：存在待生效配置，请核对当前实际加入方式与端口。')
    return {'room': room['id'], 'share_text': '\n'.join(lines), 'port': port,
            'local_ready': report.get('game_ready') is True, 'client_join': 'unverified',
            'public_reachability': 'unverified', 'address_supplied': target != 'YOUR_SERVER_IP'}


def check(room, target=None):
    report = rooms.status(room)
    checks = []
    def add(code, passed, detail, next_step):
        checks.append({'code': code, 'passed': passed, 'detail': detail, 'next_step': next_step})
    service = report.get('service') or {}
    add('service', service.get('ActiveState') == 'active', '游戏服务运行状态',
        f"sudo l4d2 room status --room {room['id']}；未启动时先检查日志再启动。")
    add('ownership', report.get('port_owned_by_service') is True, '游戏端口属于已登记服务',
        '检查端口冲突；不要将其他进程的查询响应当作本房间。')
    add('query', bool(report.get('query')), '本机 L4D2 查询',
        f"sudo l4d2 room logs --room {room['id']} --lines 80")
    add('configuration', not report.get('configuration_pending'), '配置已生效',
        '配置保存后需单独重启；先安排停服时间。')
    external = None
    if target:
        port = (report.get('running_endpoint') or room)['port']
        external = probe(target, port)
        add('address_query', external['query_reachable'], '从当前主机查询提供的地址',
            '请外网好友另行运行 network probe；服务器自测不能证明外网可达。')
    return {'room': room['id'], 'healthy': report.get('game_ready') is True and
            (external is None or external['query_reachable']),
            'checks': checks, 'address_probe': external,
            'public_reachability': 'unverified', 'client_join': 'unverified',
            'next_steps': [f"云安全组和防火墙放行 UDP {(report.get('running_endpoint') or room)['port']}。",
                           '家庭主机检查端口转发与运营商 NAT；域名应指向实际公网地址。',
                           '游戏版本不匹配时更新客户端和服务端；缺图时核对 map share 清单。',
                           '允许名单拒绝时请服主核对 SteamID64；不要使用 RCON 密码加入。']}
