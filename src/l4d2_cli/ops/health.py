"""Read-only health results suitable for humans and supervised periodic jobs."""
from __future__ import annotations

import shutil

from . import access, base, content, rooms, updates


def check(room, minimum_free_gib=2):
    report = rooms.status(room)
    disk = shutil.disk_usage(room['game_dir'])
    resources = content.inventory(room)
    control = access.status(room)
    update = base.read(updates.state_path(room['game_dir']))
    checks = [
        {'code': 'game_ready', 'passed': report.get('game_ready') is True, 'detail': '服务、本机查询与端口归属'},
        {'code': 'disk_space', 'passed': disk.free >= minimum_free_gib * 1024**3, 'detail': '磁盘可用空间', 'free_bytes': disk.free},
        {'code': 'content', 'passed': not resources['errors'], 'detail': '已安装资源可读取', 'errors': resources['errors']},
        {'code': 'configuration', 'passed': not report.get('configuration_pending'), 'detail': '房间没有待生效配置'},
        {'code': 'update_complete', 'passed': update.get('status') not in {'updating', 'failed', 'interrupted'}, 'detail': '游戏更新完整'},
        {'code': 'access', 'passed': not control['configured'] or control['runtime_verified'], 'detail': '访问插件策略与运行回执'},
    ]
    return {'room': room['id'], 'healthy': all(c['passed'] for c in checks), 'checks': checks,
            'access': control, 'game_build': update.get('after_build'),
            'client_join': 'unverified', 'public_reachability': 'unverified',
            'next_steps': ['失败时先运行 room connect-check、room logs 和 storage usage。',
                           '定时健康检查记录结果并返回失败状态；不会自动重启或更新游戏。']}
