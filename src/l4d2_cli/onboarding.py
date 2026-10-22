"""Guided bootstrap using the same validated operations as the public CLI."""
from __future__ import annotations

from dataclasses import asdict, replace
import sys
import time

from . import host, installer, manager, network, steam, storage
from .common import CliError, REDACTOR, confirm, deadline_remaining, progress
from .model import CAMPAIGNS, OFFICIAL_MAPS, SERVICE, Source, validate_vanilla_secrets


CAMPAIGN_NAMES = ('死亡中心', '黑色狂欢节', '沼泽激战', '暴风骤雨', '教区', '短暂时刻',
                  '牺牲', '毫不留情', '坠机险途', '死亡丧钟', '静寂时分', '血腥收获', '刺骨寒溪', '最后一刻')
STAGES = {'preflight': '环境与安装计划', 'install': '安装游戏', 'configure': '保存房间配置',
          'start': '启动与本机查询', 'status': '读取最终状态', 'complete': '完成'}


def ask(label, default=''):
    progress(f'{label} [{default}]：')
    try:
        return input().strip() or str(default)
    except EOFError:
        raise CliError('输入结束，已取消引导。', 'cancelled', 130) from None


def choose(label, options, default):
    while True:
        value = ask(label, default)
        if value in options:
            return value
        progress('请选择：' + ' / '.join(options))


def collect(args, current):
    """Read choices only; do not store a second installation/configuration state."""
    settings = {key: getattr(args, key) for key in
                ('name', 'port', 'map', 'difficulty', 'visibility', 'steam_group_id')
                if getattr(args, key) is not None}
    if args.dir:
        settings['game_dir'] = args.dir
    if args.steam_group_id is not None and args.visibility is None:
        settings['visibility'] = 'group'
    if settings.get('visibility') == 'public' and args.steam_group_id is None:
        settings['steam_group_id'] = ''
    config = replace(current, **settings)
    if not args.yes and not args.json and not args.dry_run:
        if not sys.stdin.isatty():
            raise CliError('引导需要终端；自动化请先 --dry-run，再使用 --yes。', 'interactive_required', 2)
        progress('首次开服引导：使用 Linux 主机，管理 main 房间。按回车保留括号内的值。')
        progress('公开房间允许其他玩家加入；Steam 组优先不是严格好友白名单。')
        if not args.dir:
            config.game_dir = ask('游戏目录（已有安装必须保持原目录）', config.game_dir)
        if args.name is None:
            config.name = ask('房间名（本版使用英文字母/数字）', config.name)
        if args.port is None:
            value = ask('游戏 UDP 端口', config.port)
            try:
                config.port = int(value)
            except ValueError:
                raise CliError('端口必须是 1024–65535 的整数。', 'invalid_port', 2) from None
        if args.map is None:
            progress('战役：' + '；'.join(f'{i} {name}' for i, name in enumerate(CAMPAIGN_NAMES, 1)))
            value = ask('输入战役编号，或保留当前地图', config.map)
            config.map = (f'c{int(value)}m1_{CAMPAIGNS[int(value)][0]}'
                          if value.isdigit() and int(value) in CAMPAIGNS else value)
        if args.difficulty is None and config.mode == 'coop':
            config.difficulty = choose('难度 easy=简单 normal=普通 hard=困难 impossible=专家',
                                       ('easy', 'normal', 'hard', 'impossible'), config.difficulty)
        if args.visibility is None and args.steam_group_id is None:
            config.visibility = choose('加入方式 public=公开直连 group=Steam 组大厅',
                                       ('public', 'group'), config.visibility)
        if config.visibility == 'group' and args.steam_group_id is None:
            config.steam_group_id = ask('Steam 组短数字 ID（不是网页地址）', config.steam_group_id)
        if config.visibility == 'public' and args.steam_group_id is None:
            config.steam_group_id = ''
        if not any((args.steamcmd_path, args.steamcmd_archive, args.server_archive)):
            source = choose('安装来源 online=官方在线 tool=已有 SteamCMD bundle=完整游戏包',
                            ('online', 'tool', 'bundle'), 'online')
            if source == 'tool':
                args.steamcmd_path = ask('已有完整工具的 steamcmd.sh 绝对路径')
                if not args.steamcmd_path:
                    raise CliError('请选择实际的 SteamCMD 路径。', 'invalid_source', 2)
                args.steam_user = ask('已验证缓存的下载账号，匿名可用时保留 anonymous', args.steam_user)
            elif source == 'bundle':
                args.server_archive = ask('由 l4d2 download server 制作的完整游戏包路径')
                if not args.server_archive:
                    raise CliError('请选择完整游戏包路径。', 'invalid_source', 2)
                args.offline = choose('是否严格离线导入（需预先装齐依赖） y/n', ('y', 'n'), 'n') == 'y'
        if not args.no_start:
            args.no_start = choose('完成后启动房间 y/n', ('y', 'n'), 'y') == 'n'
    config.validate()
    if not config.name.isascii():
        raise CliError('本版游戏房间名仅支持 ASCII，请使用英文字母/数字。', 'unsupported_server_name', 2)
    if config.map not in OFFICIAL_MAPS:
        raise CliError('首次开服请选择官方地图；三方图请在安装完成后用 map 命令管理。', 'invalid_map', 2)
    if args.difficulty is not None and config.mode != 'coop':
        raise CliError('合作难度只适用于 coop。已有对抗服请用 server configure。', 'difficulty_mode_mismatch', 2)
    return config


def next_steps(code, stage):
    if code == 'unsupported_platform':
        return ['在 Linux x86_64/glibc、运行 systemd 的目标主机执行 l4d2 setup。',
                'Windows 可通过 SSH 登录 Linux 主机；参阅 docs/getting-started.md。']
    if code == 'permission_denied':
        return ['在目标 Linux 主机使用 sudo l4d2 setup，并保留原来的选项。']
    if code in {'steam_auth_required', 'cached_login_unverified'}:
        return ['先退出 sudo，以普通用户运行 l4d2 auth login --fresh --cache <新的私有缓存目录>。',
                '验证后重跑 sudo l4d2 setup，保留房间选项，并添加 --steam-user <同一账号> --steamcmd-path <缓存目录>/steamcmd/steamcmd.sh。',
                '登录成功只证明认证；能否下载仍取决于 Steam 授权和网络。']
    if code in {'target_conflict', 'service_conflict'}:
        return ['核对登记目录及服务；已有非空服务器请先 room scan / room adopt，或使用新的空目录。']
    if code == 'setup_running':
        return ['main 已在运行；用 server status 查看加入方式，调整配置请使用 server configure。']
    if stage in {'start', 'status'}:
        return ['运行 sudo l4d2 server status 和 sudo l4d2 server logs --lines 50。',
                '排除故障后运行 sudo l4d2 server start；配置与游戏文件已经保留。']
    return ['运行 sudo l4d2 doctor 查看原因；检查下载网络、空间和依赖。',
            '修复后以相同目录、安装来源和房间选项重跑 sudo l4d2 setup；已完成的安装会复用。']


def setup(args):
    stage, completed, plan = 'preflight', [], {}
    try:
        host.require_platform()
        if not args.dry_run:
            host.require_root()
        current = installer.registered_config()
        config = collect(args, current)
        source = Source(**{key: getattr(args, key) for key in
                           ('steamcmd_archive', 'steamcmd_path', 'server_archive', 'offline', 'sha256')})
        source.validate()
        steam.validate_login_name(args.steam_user)
        if args.steam_user != 'anonymous':
            REDACTOR.register(args.steam_user)
        # Never reconfigure a running room from a first-time wizard.
        info = host.service_info()
        if info.get('MainPID') or info.get('ActiveState') in {'active', 'activating', 'deactivating', 'reloading'}:
            raise CliError('main 已在运行或切换状态，引导不会重配或重启它。', 'setup_running', 4)
        progress('[1/4] 检查主机、目录、依赖、空间与下载网络……')
        install = installer.install(config.game_dir, source, dry_run=True, yes=False,
                                    timeout=args.timeout, steam_user=args.steam_user, steam_tcp=args.steam_tcp,
                                    initial_config=config)['plan']
        plan = {'room': 'main', 'service': SERVICE, 'installation': install,
                'configuration': asdict(config), 'starts_server': not args.no_start,
                'opens_firewall': False, 'public_reachability': 'unverified', 'client_join': 'unverified'}
        network.require_network(install['network'])
        if not install['installed'] and install['free_bytes'] < install['minimum_free_bytes']:
            raise CliError('当前安装阶段的空闲空间不足。', 'insufficient_space', 4)
        if source.offline and install['missing_dependencies']:
            raise CliError('离线导入前需要预先安装系统依赖。', 'missing_dependencies', 4)
        if install['installed']:
            secrets = storage.load_secrets()
            manager.configure(config, secrets, dry_run=True, yes=False)
        else:
            host.check_udp_available(config.bind, config.port)
        if args.dry_run:
            return {'dry_run': True, 'stage': stage, 'plan': plan, 'completed_steps': []}
        progress(format_report({'dry_run': True, 'plan': plan}))
        confirm('按以上计划安装/配置 main' + ('并启动房间。' if not args.no_start else '，保持停止。'),
                args.yes, as_json=args.json)
        deadline = time.monotonic() + args.timeout
        # Each operation revalidates and takes its own existing operation lock.
        stage = 'install'
        progress('[2/4] 安装或复用游戏；下载可能耗时较长……')
        installer.install(config.game_dir, source, dry_run=False, yes=True,
                          timeout=deadline_remaining(deadline), steam_user=args.steam_user, steam_tcp=args.steam_tcp,
                          initial_config=config)
        completed.append(stage)
        stage = 'configure'
        progress('[3/4] 保存房间配置……')
        # Re-read secrets and revision; never reset a pre-existing RCON password.
        saved, _ = manager.installed()
        info = host.service_info()
        if info.get('MainPID') or info.get('ActiveState') in {'active', 'activating', 'deactivating', 'reloading'}:
            raise CliError('房间已被其他操作启动；停止引导以保留运行状态。', 'setup_running', 4)
        if saved.game_dir != config.game_dir:
            raise CliError('安装目录发生变化，请重新检查。', 'target_conflict', 4)
        expected = install.get('configuration', asdict(replace(current, game_dir=config.game_dir)))
        if asdict(saved) != expected:
            raise CliError('安装期间房间配置已改变，请重新预览，保留其他操作的更改。', 'configuration_changed', 4)
        config.revision = saved.revision
        secrets = storage.load_secrets()
        validate_vanilla_secrets(secrets)
        manager.configure(config, secrets, dry_run=False, yes=True)
        completed.append(stage)
        if not args.no_start:
            stage = 'start'
            progress('[4/4] 启动并检查本机游戏查询……')
            manager.lifecycle('start', dry_run=False, yes=True, timeout=min(120, deadline_remaining(deadline)), as_json=args.json)
            completed.append(stage)
        stage = 'status'
        status = manager.status(query_timeout=min(3, deadline_remaining(deadline)))
        if not args.no_start and status.get('game_ready') is not True:
            raise CliError('已执行启动，但最终查询未确认就绪。', 'startup_not_ready', 4, status)
        return {'stage': 'complete', 'completed_steps': completed, 'plan': plan,
                'server': status, 'started': not args.no_start,
                'public_reachability': 'unverified', 'client_join': 'unverified'}
    except (CliError, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            error = CliError('引导已取消；已完成步骤与安装半成品保留。', 'cancelled', 130)
        suggestions = list(dict.fromkeys(error.details.get('next_steps', []) + next_steps(error.code, stage)))
        details = {**error.details, 'stage': stage, 'completed_steps': completed,
                   'plan': plan, 'next_steps': suggestions}
        raise CliError(str(error), error.code, error.exit_code, details) from error


def format_report(data, error=None):
    lines = ['L4D2 首次开服引导', '']
    if error:
        lines += ['[未完成] ' + STAGES.get(data.get('stage'), '环境检查'),
                  '  原因：' + error['message']]
        if data.get('completed_steps'):
            lines.append('  已完成并保留：' + '、'.join(STAGES[s] for s in data['completed_steps']))
        lines += ['  下一步：' + item for item in data.get('next_steps', next_steps(error.get('code'), 'preflight'))]
        return '\n'.join(lines)
    plan = data.get('plan') or {}
    config = plan.get('configuration') or {}
    install = plan.get('installation') or {}
    if config:
        lines += ['[计划] main / ' + str(config.get('name')),
                  '  游戏目录：' + str(config.get('game_dir')),
                  f"  地图：{config.get('map')}；模式：{config.get('mode')}；难度：{config.get('difficulty')}",
                  f"  游戏 UDP 端口：{config.get('port')}；加入方式：{config.get('visibility')}",
                  '  开机启动：' + ('启用' if config.get('autostart') else '关闭'),
                  '  安装：' + ('复用已安装游戏' if install.get('installed') else str(install.get('source'))),
                  '  完成后启动：' + ('是' if plan.get('starts_server') else '否')]
        if install.get('missing_dependencies'):
            lines.append('  将安装系统依赖：' + ', '.join(install['missing_dependencies']))
    if data.get('dry_run'):
        lines += ['', '[预览] 未修改配置或启动游戏；本机防火墙、云安全组及 NAT 需自行配置。',
                  '使用相同选项并去掉 --dry-run 执行；非交互执行需 --yes。']
    elif data.get('stage') == 'complete':
        from .status_output import format_report as status_report
        lines += ['', '[完成] ' + ('本机游戏已就绪。' if data.get('started') else '游戏已安装并保存配置；引导未请求启动。'),
                  status_report(data.get('server') or {}), '',
                  '邀请朋友：双方启动正版 L4D2，按上面的加入方式连接，完成一局验证。',
                  '若使用 YOUR_SERVER_IP，请替换为实际公网地址；云安全组/主机防火墙放行实际游戏 UDP 端口。',
                  '路由器后还需端口转发；本机就绪不能证明公网可达。']
        if not data.get('started'):
            lines.append('准备好后启动：sudo l4d2 server start')
    return '\n'.join(lines)
