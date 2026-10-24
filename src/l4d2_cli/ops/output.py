"""Chinese terminal views; JSON remains the versioned automation contract."""
from __future__ import annotations

import json

COMMANDS = {'l4d2 room invite', 'l4d2 room connect-check', 'l4d2 network probe', 'l4d2 map share',
            'l4d2 room health', 'l4d2 room update', 'l4d2 access status', 'l4d2 access configure',
            'l4d2 schedule add', 'l4d2 schedule list', 'l4d2 schedule remove', 'l4d2 schedule run',
            'l4d2 room list', 'l4d2 room status', 'l4d2 map list', 'l4d2 addon list', 'l4d2 plugin list'}


def format_report(command, data, error=None):
    if data.get('share_text') and not error:
        return data['share_text']
    lines = [command]
    if error:
        lines.append('[未完成] ' + error['message'] + ' (' + error['code'] + ')')
    if data.get('dry_run'):
        lines.append('[预览] 尚未执行变更')
        data = data.get('plan') or {}
    if data.get('schedule'):
        lines.append('[已保存] 定时任务')
        data = data['schedule']
    if data.get('status') in {'complete', 'updating', 'failed', 'interrupted'}:
        lines.append('更新状态：' + {'complete': '已完成', 'updating': '进行中', 'failed': '失败', 'interrupted': '中断'}[data['status']])
    if command == 'l4d2 room status' and ('service' in data or 'desired_config' in data):
        if 'desired_config' in data:
            from ..status_output import format_report as main_status
            return main_status(data, error)
        lines += ['房间：' + str(data.get('room')), '本机游戏：' + ('就绪' if data.get('game_ready') else '未就绪'),
                  '服务：' + str(data.get('service', {}).get('ActiveState')),
                  '当前端口：' + str(data.get('running_endpoint', {}).get('port'))]
        if (data.get('access') or {}).get('configured'):
            lines.append('访问插件回执：' + ('已核对' if data['access'].get('runtime_verified') else '未核对；请检查 access status'))
        if (data.get('update') or {}).get('status') in {'updating', 'failed', 'interrupted'}:
            lines.append('游戏更新未完成；请先检查并重跑 room update。')
    for item in data.get('checks', []):
        lines.append(('[通过] ' if item.get('passed') else '[需处理] ') + item.get('detail', item['code']))
        if not item.get('passed') and item.get('next_step'):
            lines.append('  下一步：' + item['next_step'])
    if 'query_reachable' in data:
        lines.append('当前机器查询：' + ('可达；仍需游戏内加入' if data['query_reachable'] else '未响应；请检查地址/端口/网络'))
    if command.startswith('l4d2 access'):
        modes = {'friends': '好友名单', 'public': '公开', 'unmanaged': '尚未配置', 'missing_policy': '缺少本房间策略，需要配置'}
        lines += ['访问方式：' + modes.get(data.get('mode'), str(data.get('mode', ''))),
                  '名单人数：' + str(data.get('allow_count', 0))]
        if 'runtime_verified' in data:
            lines.append('插件运行回执：' + ('已核对' if data['runtime_verified'] else '未核对'))
        lines.append('真实玩家加入验收：待实机验证')
    for key, label in (('room', '房间'), ('affected_rooms', '共享目录房间'), ('before_build', '更新前构建'),
                       ('after_build', '更新后构建'), ('id', '任务'), ('kind', '类型'), ('at', '每日时间'),
                       ('every_minutes', '间隔分钟'), ('keep', '备份保留份数'), ('timezone', '服务器时区')):
        if key in data:
            lines.append(label + '：' + str(data[key]))
    for item in data.get('rooms', []):
        lines.append(str(item.get('id')) + ' | ' + str(item.get('name')) + ' | UDP ' + str(item.get('port')))
    for item in data.get('packages', data.get('plugins', [])):
        state = {'enabled': '启用', 'disabled': '停用'}.get(item.get('state'), item.get('state', ''))
        lines.append(item['filename'] + ' | ' + state)
    for item in data.get('schedules', []):
        last = item.get('last_run') or {}
        result = '成功' if last.get('ok') is True else ('失败' if last.get('ok') is False else
                   ('正在运行' if last.get('status') == 'running' else '尚未运行'))
        lines.append(item['id'] + ' | ' + item['kind'] + ' | 房间 ' + item['room'] + ' | 最近执行：' + result)
        if last.get('error'):
            lines.append('  原因：' + last['error'])
        if item.get('backups'):
            lines.append('  最新保留备份：' + item['backups'][-1]['output'])
        if item.get('units_match') is False:
            lines.append('  任务文件发生变化，请检查。')
    if data.get('backup'):
        lines.append('更新前备份：' + str(data['backup'].get('output')))
    if data.get('restart_required'):
        lines.append('生效需要单独启动/重启，请先安排房间停服时间。')
    lines += ['下一步：' + str(step) for step in data.get('next_steps', [])]
    if len(lines) == 1:
        lines.append(json.dumps(data, ensure_ascii=False, indent=2))
    return '\n'.join(lines)
