"""Operations command contract. Optional frontends use these same functions."""
from __future__ import annotations

import json
from pathlib import Path

from .. import host, storage
from ..common import CliError, positive
from . import access, base, backup, catalog, content, health, joining, maintenance, plugins, rcon, rooms, schedules, sharing, tasks, transport, updates, vpk

GROUPS = {'room', 'map', 'addon', 'plugin', 'admin', 'rcon', 'task', 'transfer', 'backup', 'storage', 'repair', 'capabilities', 'access', 'schedule'}


def flags(parser, *, mutate=False, room=True, timeout=False):
    parser.add_argument('--json', action='store_true', help='输出供自动化使用的 JSON')
    if room:
        parser.add_argument('--room', default='main', help='已登记房间 ID，默认 main')
    if mutate:
        parser.add_argument('--dry-run', action='store_true', help='预览计划，确认影响范围')
        parser.add_argument('--yes', action='store_true', help='确认执行已审阅的计划')
    if timeout:
        parser.add_argument('--timeout', type=positive, default=1800, help='Operation time budget in seconds')
    return parser


def register(groups, network):
    caps = groups.add_parser('capabilities', help='查询当前版本实际支持的能力')
    flags(caps, room=False)
    sub = groups.add_parser('room', help='房间管理、邀请好友、连接诊断与游戏更新').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('list'), room=False)
    scan = flags(sub.add_parser('scan'), room=False)
    scan.add_argument('--dir', default='/opt/l4d2')
    adopt = flags(sub.add_parser('adopt'), mutate=True, room=False)
    for key in ('id', 'dir', 'service', 'script', 'cfg', 'user'):
        adopt.add_argument('--' + key, required=True)
    create = flags(sub.add_parser('create'), mutate=True, room=False)
    create.add_argument('--id', required=True)
    create.add_argument('--from-room', default='main')
    create.add_argument('--port', required=True, type=int)
    create.add_argument('--name')
    create.add_argument('--map')
    flags(sub.add_parser('status'))
    config = flags(sub.add_parser('configure'), mutate=True)
    for key in ('name', 'bind', 'map', 'steam-group-id'):
        config.add_argument('--' + key)
    config.add_argument('--port', type=int)
    config.add_argument('--visibility', choices=('public', 'group'))
    config.add_argument('--mode', choices=('coop', 'versus'))
    config.add_argument('--difficulty', choices=('easy', 'normal', 'hard', 'impossible'))
    auto = config.add_mutually_exclusive_group()
    auto.add_argument('--autostart', action='store_true', default=None)
    auto.add_argument('--no-autostart', dest='autostart', action='store_false')
    password = config.add_mutually_exclusive_group()
    password.add_argument('--rcon-password-file')
    password.add_argument('--clear-rcon-password', action='store_true')
    for key in ('start', 'stop', 'restart'):
        flags(sub.add_parser(key), mutate=True, timeout=True)
    logs = flags(sub.add_parser('logs'))
    logs.add_argument('--lines', type=int, default=100)
    logs.add_argument('--since')
    logs.add_argument('--follow', action='store_true')
    flags(sub.add_parser('doctor'))
    flags(sub.add_parser('remove'), mutate=True)
    flags(sub.add_parser('refresh'), mutate=True)
    for action in ('invite', 'connect-check'):
        item = flags(sub.add_parser(action, help='好友加入说明' if action == 'invite' else '连接诊断与下一步'))
        item.add_argument('--address', help='实际 IPv4/域名；不自动探测公网地址')
    item = flags(sub.add_parser('health', help='只读健康检查；异常时返回非零退出码'))
    item.add_argument('--minimum-free-gib', type=positive, default=2)
    item = flags(sub.add_parser('update', help='备份配置并更新官方游戏；共享房间需先停服'), mutate=True, timeout=True)
    item.set_defaults(timeout=3600)
    item.add_argument('--steamcmd-path', help='可选：已验证账号缓存中的 steamcmd.sh')
    item.add_argument('--steam-user', default='anonymous')
    item.add_argument('--steam-tcp', action='store_true')
    for group in ('map', 'addon'):
        sub = groups.add_parser(group, help='地图、战役与分享清单' if group == 'map' else 'Mod 安装与启停').add_subparsers(dest='action', required=True)
        flags(sub.add_parser('list'))
        info = flags(sub.add_parser('info'))
        info.add_argument('filename')
        search = flags(sub.add_parser('search'), room=False)
        search.add_argument('query')
        resolve = flags(sub.add_parser('resolve'), room=False)
        resolve.add_argument('id')
        resolve.add_argument('--download-links', action='store_true', help='Include resolved public content URLs for client-side downloads')
        install = flags(sub.add_parser('install'), mutate=True, timeout=True)
        install.add_argument('id')
        install.add_argument('--source', choices=('workshop', 'gamemaps'), default='workshop')
        imported = flags(sub.add_parser('import'), mutate=True)
        imported.add_argument('--file', required=True)
        imported.add_argument('--name')
        imported.add_argument('--workshop-id', help='Verify a local VPK against this Workshop part and retain its source')
        remove = flags(sub.add_parser('remove'), mutate=True)
        remove.add_argument('filename')
        remove.add_argument('--purge', action='store_true')
        forget = flags(sub.add_parser('forget'), mutate=True)
        forget.add_argument('filename')
        reinstall = flags(sub.add_parser('reinstall'), mutate=True, timeout=True)
        reinstall.add_argument('filename')
        if group in {'map', 'addon'}:
            for state in ('enable', 'disable'):
                item = flags(sub.add_parser(state), mutate=True)
                item.add_argument('filename')
        if group == 'map':
            item = flags(sub.add_parser('share', help='生成好友订阅清单、分包和依赖核对结果'))
            selected = item.add_mutually_exclusive_group()
            selected.add_argument('--id', help='指定 Workshop 条目/合集；省略时按启用地图生成')
            selected.add_argument('--filename', action='append', help='选择启用的地图/Mod 包，可重复')
            item.add_argument('--offline', action='store_true', help='仅按本地来源记录生成，标记依赖未验证')
            for action in ('default', 'change'):
                item = flags(sub.add_parser(action), mutate=True, timeout=action == 'change')
                item.add_argument('map')
            verify = flags(sub.add_parser('verify'), room=False)
            verify.add_argument('--file', required=True)
            extract = flags(sub.add_parser('extract'), mutate=True, room=False)
            extract.add_argument('--file', required=True)
            extract.add_argument('--output', required=True)
    sub = groups.add_parser('plugin', help='SourceMod 框架、插件与编译').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('sources'), room=False)
    flags(sub.add_parser('list'))
    install = flags(sub.add_parser('install'), mutate=True)
    install.add_argument('--file', required=True)
    install.add_argument('--name')
    for state in ('enable', 'disable'):
        item = flags(sub.add_parser(state), mutate=True)
        item.add_argument('filename')
    stack = flags(sub.add_parser('stack'), mutate=True, timeout=True)
    stack.add_argument('--component', choices=('core', 'left4dhooks', 'all'), default='core')
    stack.add_argument('--metamod-archive')
    stack.add_argument('--sourcemod-archive')
    compile_parser = flags(sub.add_parser('compile'), mutate=True)
    source = compile_parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--file')
    source.add_argument('--builtin', choices=('infected_counter', 'jiangmen_changelevel_fix', 'l4d2_cli_access'))
    compile_parser.add_argument('--name')
    compile_parser.add_argument('--deploy', action='store_true')
    sub = groups.add_parser('admin', help='游戏管理员名单').add_subparsers(dest='action', required=True)
    for action in ('list', 'add', 'remove'):
        item = flags(sub.add_parser(action), mutate=action != 'list')
        item.add_argument('--backend', choices=('sourcemod', 'left4bots', 'admin-system'), default='sourcemod')
        if action != 'list':
            item.add_argument('steam_id')
        if action == 'add':
            item.add_argument('--flags', default='99:z')
    sub = groups.add_parser('rcon', help='以私有文件执行游戏控制台命令').add_subparsers(dest='action', required=True)
    item = flags(sub.add_parser('exec'), mutate=True, timeout=True)
    item.add_argument('--command-file', required=True, help='One console command in a private UTF-8 file')
    sub = groups.add_parser('task', help='独立后台任务与进度').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('list'), room=False)
    submit = flags(sub.add_parser('submit'), mutate=True, room=False)
    submit.add_argument('--detach-service', action='store_true', help='Use an independent transient systemd worker (for web/service callers)')
    import argparse
    submit.add_argument('arguments', nargs=argparse.REMAINDER, help='-- followed by a mutating CLI operation')
    for action in ('show', 'logs', 'cancel', 'retry', 'delete', '_run'):
        item = flags(sub.add_parser(action), room=False, mutate=action in {'cancel', 'retry', 'delete'})
        item.add_argument('id')
        if action == 'logs':
            item.add_argument('--lines', type=int, default=100)
    sub = groups.add_parser('transfer', help='资源来源清单与离线 ZIP 传输').add_subparsers(dest='action', required=True)
    for action in ('export', 'import'):
        item = flags(sub.add_parser(action), mutate=True)
        item.add_argument('--format', choices=('manifest', 'zip') if action == 'export' else ('manifest', 'zip', 'legacy'), default='manifest')
        item.add_argument('--output' if action == 'export' else '--file', required=True)
        if action == 'export':
            item.add_argument('--filename', action='append')
    install_manifest = flags(sub.add_parser('install'), mutate=True, timeout=True)
    install_manifest.add_argument('--filename', action='append', help='Select imported records; default all removed records')
    sub = groups.add_parser('backup', help='配置、插件和来源记录的私有备份与恢复').add_subparsers(dest='action', required=True)
    item = flags(sub.add_parser('create'), mutate=True)
    item.add_argument('--output')
    item.add_argument('--scope', choices=('full', 'plugins'), default='full')
    item = flags(sub.add_parser('inspect'), room=False)
    item.add_argument('--file', required=True)
    item.add_argument('--sha256')
    item = flags(sub.add_parser('restore'), mutate=True)
    item.add_argument('--file', required=True)
    item.add_argument('--sha256')
    item.add_argument('--network', action='store_true')
    item.add_argument('--panel-state', action='store_true')
    sub = groups.add_parser('storage', help='磁盘占用与指定范围清理').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('usage'))
    item = flags(sub.add_parser('cleanup'), mutate=True)
    item.add_argument('--scope', choices=('cache', 'workshop', 'temporary'), required=True)
    sub = groups.add_parser('repair', help='按指定范围修复资源和中断事务').add_subparsers(dest='action', required=True)
    item = flags(sub.add_parser('mission-bom'), mutate=True)
    item.add_argument('filename')
    item = flags(sub.add_parser('permissions'), mutate=True)
    item.add_argument('--scope', choices=('maps', 'missions', 'addons', 'addons_disabled'), required=True)
    flags(sub.add_parser('transaction'), mutate=True, room=False)
    flags(network.add_parser('status'), room=False)
    item = flags(network.add_parser('probe', help='从当前电脑查询游戏 UDP，可在 Windows/Linux/macOS 使用'), room=False)
    item.add_argument('--address', required=True)
    item.add_argument('--port', type=int, default=27015)
    item.add_argument('--timeout', type=positive, default=3)
    item = flags(network.add_parser('configure'), mutate=True, room=False)
    proxy = item.add_mutually_exclusive_group()
    proxy.add_argument('--proxy-file')
    proxy.add_argument('--clear-proxy', action='store_true')
    downloads = item.add_mutually_exclusive_group()
    downloads.add_argument('--direct-downloads', dest='direct_downloads', action='store_true', default=None,
                           help='Use the proxy for source metadata, download installation files directly')
    downloads.add_argument('--proxy-downloads', dest='direct_downloads', action='store_false',
                           help='Use the configured proxy for installation files too (default)')
    item.add_argument('--steam-api-key-file')
    item.add_argument('--hosts-file')
    ip = item.add_mutually_exclusive_group()
    ip.add_argument('--prefer-ipv4', dest='ipv4', action='store_true', default=None)
    ip.add_argument('--reset-ipv4', dest='ipv4', action='store_false')
    sub = groups.add_parser('access', help='Steam 身份好友名单与插件运行检查').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('status'))
    item = flags(sub.add_parser('configure'), mutate=True)
    item.add_argument('--mode', choices=('public', 'friends'), required=True)
    item.add_argument('--allow-file', help='每行一个个人 SteamID64，包含服主；不使用昵称或组 ID')
    sub = groups.add_parser('schedule', help='定时备份与健康检查').add_subparsers(dest='action', required=True)
    flags(sub.add_parser('list'), room=False)
    item = flags(sub.add_parser('add'), mutate=True)
    item.add_argument('--id', required=True)
    item.add_argument('--kind', choices=('backup', 'health'), required=True)
    when = item.add_mutually_exclusive_group()
    when.add_argument('--at', help='目标服务器时区的每日 HH:MM')
    when.add_argument('--every-minutes', type=int)
    item.add_argument('--keep', type=int, default=7, help='该备份任务保留 1–100 份，默认 7')
    item.add_argument('--cli-path', default='/usr/local/bin/l4d2', help='root 拥有的全局 CLI 入口')
    for action in ('remove', 'run', '_run'):
        item = flags(sub.add_parser(action), room=False, mutate=action != '_run')
        item.add_argument('--id', required=True)


def dispatch(args):
    if args.group in {'map', 'addon', 'plugin', 'transfer', 'network'}:
        with transport.budget(getattr(args, 'timeout', 60)):
            return _dispatch(args)
    return _dispatch(args)


def _dispatch(args):
    group, action = args.group, getattr(args, 'action', None)
    if group == 'capabilities':
        return {'contract_version': 1, 'groups': sorted(GROUPS - {'capabilities'}),
                'network': ['check', 'probe', 'status', 'configure'], 'onboarding': ['setup'], 'web_required': False,
                'friends': ['room invite', 'room connect-check', 'network probe', 'map share', 'access configure', 'access status'],
                'maintenance': ['room update', 'room health', 'schedule add', 'schedule list', 'schedule run', 'schedule remove'],
                'optional_frontends': {'web': ['install', 'configure', 'start', 'stop', 'restart', 'status', 'check', 'logs', 'uninstall'],
                                       'skill': ['show', 'export', 'list', 'status', 'install', 'update', 'uninstall']},
                'mutation_contract': ['dry_run', 'confirmation', 'operation_lock', 'secret_redaction']}
    if group == 'network' and action == 'probe':
        report = joining.probe(args.address, args.port, args.timeout)
        if not report['query_reachable']:
            raise CliError('当前机器未能查询到游戏。', 'query_unavailable', 4, report)
        return report
    host.require_platform()
    options = {'dry_run': getattr(args, 'dry_run', False), 'yes': getattr(args, 'yes', False)}
    if group == 'schedule':
        host.require_root()
        if action == 'list':
            return schedules.listing()
        if action == 'remove':
            return schedules.remove(args.id, **options)
        if action in {'run', '_run'}:
            if action == 'run':
                early = base.prepare({'action': 'run-schedule', 'id': args.id, 'kind': schedules.get(args.id)['kind']}, **options)
                if early:
                    return early
            return schedules.worker(args.id)
        return schedules.add(args.id, rooms.get(args.room), args.kind, args.at, args.every_minutes,
                             args.keep, args.cli_path, **options)
    if group == 'task':
        host.require_root()
        if action == 'list':
            return tasks.list_tasks()
        if action == 'submit':
            return tasks.submit(args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments,
                                detached=args.detach_service, **options)
        if action == '_run':
            return tasks.worker(args.id)
        if action == 'show':
            return tasks.get(args.id)
        if action == 'logs':
            return tasks.logs(args.id, args.lines)
        if action == 'retry':
            item = tasks.get(args.id)
            if item['status'] not in tasks.TERMINAL:
                raise CliError('Only terminal tasks can be retried.', 'task_running', 4)
            return tasks.submit(item['arguments'], **options)
        return (tasks.cancel if action == 'cancel' else tasks.delete)(args.id, **options)
    if group == 'network':
        host.require_root()
        if action == 'status':
            return maintenance.network_status()
        return maintenance.network_configure(args.proxy_file, args.steam_api_key_file, args.clear_proxy,
                                              args.hosts_file, args.ipv4, direct_downloads=args.direct_downloads, **options)
    if group == 'room':
        if action == 'list':
            return {'rooms': [{key: value for key, value in room.items() if key != 'hashes'} for room in rooms.all_rooms().values()]}
        if action == 'scan':
            return rooms.scan(args.dir)
        if action == 'adopt':
            return rooms.adopt(args.id, args.dir, args.service, args.script, args.cfg, args.user, **options)
        if action == 'create':
            return rooms.create(args.id, args.from_room, args.port, args.name, args.map, **options)
    if group in {'map', 'addon'}:
        kind = 'map' if group == 'map' else 'mod'
        if action == 'search':
            catalog.initialize()
            return catalog.search_catalog(args.query, kind)
        if action == 'resolve':
            catalog.initialize()
            result = catalog.workshop_expected_parts(args.id, kind)
            # Direct links are used internally; public metadata is sufficient for frontends.
            for item in result.get('parts', []):
                if not args.download_links:
                    item.pop('file_url', None)
            return result
        if action == 'verify':
            return vpk.verify(args.file)
        if action == 'extract':
            early = base.prepare({'action': 'extract-vpk', 'file': args.file, 'output': args.output}, **options)
            if early:
                return early
            output = Path(args.output).absolute()
            storage.reject_links(output)
            if output.exists():
                raise CliError('Choose a new extraction directory.', 'output_exists', 4)
            output.parent.mkdir(parents=True, exist_ok=True)
            return {'files': vpk.extract(args.file, output)}
    if group == 'backup' and action == 'inspect':
        host.require_root()
        document = backup.inspect(args.file, args.sha256)
        return {'format': document['format'], 'scope': document['scope'], 'files': len(document['files']),
                'rooms': list(document.get('rooms', {})), 'contains_secrets': True}
    if group == 'plugin' and action == 'sources':
        return plugins.sources()
    if group == 'repair' and action == 'transaction':
        pending = base.read(base.ROOT / 'transaction.json')
        early = base.prepare({'action': 'recover-transaction', 'pending': bool(pending),
                              'files': len(pending.get('entries', []))}, **options)
        if early:
            return early
        with storage.operation_lock():
            recovered = base.recover()
            from ..common import run
            run(['systemctl', 'daemon-reload'])
            return {'recovered': recovered, 'systemd_reloaded': True}
    room = rooms.get(args.room)
    if group == 'access':
        host.require_root()
        return access.status(room) if action == 'status' else access.configure(room, args.mode, args.allow_file, **options)
    if group == 'room':
        if action == 'invite':
            return joining.invite(room, args.address)
        if action == 'connect-check':
            report = joining.check(room, args.address)
            if not report['healthy']:
                raise CliError('连接诊断发现本机检查异常。', 'unhealthy', 4, report)
            return report
        if action == 'health':
            report = health.check(room, args.minimum_free_gib)
            if not report['healthy']:
                raise CliError('健康检查发现异常。', 'unhealthy', 4, report)
            return report
        if action == 'update':
            return updates.update(room, args.steamcmd_path, args.steam_user, args.steam_tcp, args.timeout, **options)
        if action == 'status':
            return rooms.status(room)
        if action == 'doctor':
            host.require_root()
            rooms.secrets(room)
            return maintenance.diagnose(room)
        if action == 'configure':
            from ..cli import password_file
            password = password_file(args.rcon_password_file) if args.rcon_password_file else ('' if args.clear_rcon_password else None)
            settings = {key: getattr(args, key) for key in ('name', 'bind', 'map', 'port', 'visibility', 'steam_group_id', 'mode', 'difficulty', 'autostart')
                        if getattr(args, key) is not None}
            return rooms.configure(args.room, settings, password, **options)
        if action in {'start', 'stop', 'restart'}:
            return rooms.lifecycle(args.room, action, args.timeout, as_json=args.json, **options)
        if action == 'remove':
            return rooms.unregister(args.room, **options)
        if action == 'refresh':
            return rooms.refresh(args.room, **options)
        return rooms.logs(args.room, args.lines, args.since, args.follow)
    if group in {'map', 'addon'}:
        if action == 'share':
            return sharing.share(room, args.id, args.filename, args.offline)
        if action == 'list':
            report = content.inventory(room)
            if group == 'addon':
                report['packages'] = [p for p in report['packages'] if p['kind'] == 'mod']
            return report
        if action == 'info':
            return content.package(room, args.filename)
        if action == 'install':
            return content.install(room, args.source, args.id, kind, args.timeout, **options)
        if action == 'import':
            if args.workshop_id:
                return content.import_workshop(room, args.file, kind, args.workshop_id, final_name=args.name, **options)
            return content.install_local(room, args.file, kind, final_name=args.name, **options)
        if action == 'forget':
            return content.forget(room, args.filename, **options)
        if action in {'enable', 'disable'}:
            return content.state(room, args.filename, action == 'enable', **options)
        if action == 'remove':
            return content.remove(room, args.filename, args.purge, **options)
        if action == 'reinstall':
            return content.reinstall(room, args.filename, timeout=args.timeout, **options)
        if action == 'default':
            return rooms.configure(args.room, {'map': args.map}, **options)
        return rcon.changelevel(room, args.map, args.timeout, **options)
    if group == 'plugin':
        if action == 'list':
            return plugins.inventory(room)
        if action == 'install':
            return plugins.install(room, args.file, args.name, **options)
        if action in {'enable', 'disable'}:
            return plugins.set_state(room, args.filename, action == 'enable', **options)
        if action == 'stack':
            return plugins.stack(room, args.metamod_archive, args.sourcemod_archive, args.component, timeout=args.timeout, **options)
        source = args.file or str(Path(plugins.__file__).parent / 'assets' / (args.builtin + '.sp'))
        return plugins.compile_source(room, source, args.name, args.deploy, **options)
    if group == 'admin':
        host.require_root()
        if action == 'list':
            return plugins.admins(room, args.backend)
        return plugins.set_admin(room, args.backend, args.steam_id, getattr(args, 'flags', '99:z'), action == 'remove', **options)
    if group == 'rcon':
        host.require_root()
        path = base.regular(args.command_file)
        if path.stat().st_size > 4096 or path.stat().st_mode & 0o077:
            raise CliError('RCON command file must be private and bounded.', 'secret_permissions', 3)
        return rcon.command(room, path.read_text(encoding='utf-8').strip(), args.timeout, **options)
    if group == 'transfer':
        if action == 'export':
            early = base.prepare({'action': 'export-content', 'room': room['id'], 'format': args.format,
                                  'output': args.output, 'filenames': args.filename}, **options)
            if early:
                return early
            return (content.export_manifest if args.format == 'manifest' else content.export_zip)(room, args.output, args.filename)
        if action == 'import':
            if args.format == 'legacy':
                return content.import_legacy_registry(room, args.file, **options)
            return (content.import_manifest if args.format == 'manifest' else content.import_zip)(room, args.file, **options)
        records = content.registry(room)['packages']
        selected = [name for name, rec in records.items() if rec.get('state') == 'removed' and (not args.filename or name in args.filename)]
        early = base.prepare({'action': 'install-manifest', 'room': room['id'], 'packages': selected}, **options)
        if early:
            return early
        return {'results': [content.reinstall(room, name, timeout=args.timeout, yes=True) for name in selected]}
    if group == 'backup':
        if action == 'create':
            return backup.create(room, args.output, args.scope, **options)
        return backup.restore(room, args.file, args.sha256, args.network, args.panel_state, **options)
    if group == 'storage':
        return maintenance.usage(room) if action == 'usage' else maintenance.cleanup(room, args.scope, **options)
    if group == 'repair':
        return (maintenance.repair_mission(room, args.filename, **options) if action == 'mission-bom'
                else maintenance.repair_permissions(room, args.scope, **options))
    raise CliError('Unknown operations command.', 'invalid_arguments', 2)
