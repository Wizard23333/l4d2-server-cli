"""Registered rooms, explicit adoption and headless lifecycle operations."""
from __future__ import annotations

import dataclasses
import re
import shlex
import time
from pathlib import Path

from .. import host, manager, model, storage
from ..common import CliError, REDACTOR, run
from . import base


def registry():
    data = base.read(base.ROOMS, {'version': 1, 'rooms': {}})
    if data.get('version') != 1 or not isinstance(data.get('rooms'), dict):
        raise CliError('Invalid room registry.', 'invalid_state', 4)
    return data


def all_rooms():
    result = registry()['rooms'].copy()
    state = storage.read_json(storage.PATHS.state)
    if state.get('status') == 'installed':
        cfg = storage.load_config()
        result['main'] = {**dataclasses.asdict(cfg), 'id': 'main', 'service': model.SERVICE,
                          'user': model.USER, 'group': model.USER, 'kind': 'main',
                          'cfg': str(Path(cfg.game_dir) / 'left4dead2/cfg' / model.CFG_NAME),
                          'unit': str(storage.PATHS.unit)}
    return result


def get(room_id):
    base.identifier(room_id)
    room = all_rooms().get(room_id)
    if not room:
        raise CliError('Unknown registered room: ' + room_id, 'room_not_found', 4)
    model.game_path(room['game_dir'])
    if not re.fullmatch(r'l4d2[A-Za-z0-9_-]*\.service', room['service']):
        raise CliError('Room service must be an L4D2 service.', 'invalid_state', 4)
    for key in ('cfg', 'unit', 'script'):
        if room.get(key):
            storage.reject_links(Path(room[key]))
    if Path(room['unit']) != base.UNITS / room['service']:
        raise CliError('Room unit must match its registered service.', 'invalid_state', 4)
    if Path(room['cfg']).parent != game(room) / 'cfg' or Path(room['cfg']).suffix != '.cfg':
        raise CliError('Room configuration must be a cfg file in the game cfg directory.', 'invalid_state', 4)
    return room


def game(room):
    return Path(room['game_dir']) / 'left4dead2'


def affected(room):
    return sorted(key for key, value in all_rooms().items() if value['game_dir'] == room['game_dir'])


def cfg_values(text):
    values = {}
    for line in text.splitlines():
        try:
            parts = shlex.split(line, comments=False)
        except ValueError:
            continue
        if len(parts) >= 2 and not parts[0].startswith('//'):
            values[parts[0]] = parts[1]
    return values


def secrets(room):
    text = base.text(room['cfg'])
    values = cfg_values(text)
    for key in ('rcon_password', 'sv_password', 'sv_setsteamaccount'):
        REDACTOR.register(values.get(key, ''))
    return values


def check(room):
    for path, digest in room.get('hashes', {}).items():
        if storage.sha256(base.regular(path)) != digest:
            raise CliError('Room configuration changed outside CLI; inspect and re-adopt explicitly.', 'config_conflict', 4)
    info = host.service_info(service=room['service'])
    if (info.get('FragmentPath') != room['unit'] or info.get('DropInPaths') or
            info.get('User') != room['user'] or (info.get('Group') or room['user']) != room['group']):
        raise CliError('Room service identity no longer matches registration.', 'service_conflict', 4)
    return info


def status(room):
    if room['kind'] == 'main':
        return {'room': room['id'], **manager.status()}
    info = host.service_info(service=room['service'])
    query, error, ownership = None, None, None
    live = (room.get('running') if room.get('running_invocation') == info.get('InvocationID') else None) or room
    if info.get('MainPID'):
        ownership = host.udp_owner(live['port'], info['MainPID'])
        try:
            address = manager.query_address(model.Config(bind=live['bind']))
            query = manager.a2s.query(address, live['port'], 3)
        except CliError as exc:
            error = {'code': exc.code, 'message': str(exc)}
    return {'room': room['id'], 'configuration': {k: v for k, v in room.items() if k != 'hashes'},
            'service': info, 'query': query, 'query_error': error, 'port_owned_by_service': ownership,
            'running_endpoint': {'bind': live['bind'], 'port': live['port']},
            'configuration_pending': bool(info.get('MainPID') and room.get('configuration_pending')),
            'game_ready': bool(info.get('ActiveState') == 'active' and query and ownership is True),
            'public_reachability': 'unverified', 'affected_rooms': affected(room)}


def scan(directory):
    root = model.game_path(str(Path(directory).absolute()))
    candidates = []
    for unit in sorted(base.UNITS.glob('l4d2*.service')):
        storage.reject_links(unit)
        text = unit.read_text(encoding='utf-8', errors='replace')
        if str(root) in text:
            candidates.append({'service': unit.name, 'unit': str(unit),
                               'scripts': [str(p) for p in root.glob('start_l4d2*.sh') if p.is_file()],
                               'configs': [str(p) for p in (root / 'left4dead2/cfg').glob('server*.cfg') if p.is_file()]})
    return {'candidates': candidates, 'registers_automatically': False}


def launch_argument(text, names, default=None, required=False):
    text = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    match = re.search(r'(?:^|\s)["\']?(?:' + names + r')["\']?\s+["\']?([A-Za-z0-9_.-]+)["\']?(?=\s|$)', text)
    if match:
        return match[1]
    if required or re.search(r'(?:' + names + r')["\']?\s', text):
        raise CliError('Startup arguments must be explicit literals; dynamic scripts need manual normalization before adoption.', 'unsupported_launch_script', 4)
    return default


def adopt(room_id, directory, service, script, cfg, user, dry_run=False, yes=False):
    base.identifier(room_id)
    if room_id == 'main' or room_id in all_rooms():
        raise CliError('Room ID is already registered or reserved.', 'room_conflict', 4)
    directory = model.game_path(str(Path(directory).absolute()))
    base.regular(directory / 'srcds_run')
    service = service if service.endswith('.service') else service + '.service'
    if not re.fullmatch(r'l4d2[A-Za-z0-9_-]*\.service', service):
        raise CliError('Only explicit L4D2 services can be adopted.', 'invalid_service', 2)
    if any(r['service'] == service for r in all_rooms().values()):
        raise CliError('Service is already registered.', 'room_conflict', 4)
    script, cfg = base.regular(script), base.regular(cfg)
    if directory not in script.parents or directory not in cfg.parents:
        raise CliError('Script and configuration must be inside the game installation.', 'unsafe_path', 4)
    if cfg.parent != directory / 'left4dead2/cfg' or cfg.suffix != '.cfg':
        raise CliError('Choose the actual server cfg in left4dead2/cfg.', 'invalid_config', 2)
    info = host.service_info(service=service)
    unit = base.regular(base.UNITS / service)
    if info.get('FragmentPath') != str(unit) or info.get('DropInPaths') or info.get('User') != user:
        raise CliError('Service fragment/user/drop-ins differ from the requested adoption.', 'service_conflict', 4)
    uid, gid = base.account(user, info.get('Group') or user)
    script_text = base.text(script)
    if str(script) not in base.text(unit) or 'srcds_run' not in script_text:
        raise CliError('Cannot associate service, script and game executable.', 'service_conflict', 4)
    def argument(names, default):
        return launch_argument(script_text, names, default, required=names in {r'\+map', r'\+hostport|\+port|-port'})
    if launch_argument(script_text, r'\+servercfgfile', 'server.cfg') != cfg.name:
        raise CliError('Startup servercfgfile differs from the selected room configuration.', 'config_conflict', 4)
    values = cfg_values(base.text(cfg))
    room = {'id': room_id, 'game_dir': str(directory), 'service': service, 'unit': str(unit),
            'user': user, 'group': info.get('Group') or user, 'kind': 'adopted',
            'script': str(script), 'cfg': str(cfg), 'bind': argument(r'\+ip|-ip', '0.0.0.0'),
            'port': int(argument(r'\+hostport|\+port|-port', '27015')),
            'map': argument(r'\+map', 'c1m1_hotel'), 'mode': values.get('mp_gamemode', 'coop'),
            'difficulty': values.get('z_difficulty', 'Normal').lower(),
            'name': values.get('hostname', room_id), 'steam_group_id': values.get('sv_steamgroup', ''),
            'visibility': 'group' if values.get('sv_steamgroup_exclusive') == '1' else 'public',
            'autostart': info.get('UnitFileState') == 'enabled'}
    validate(room)
    if info.get('MainPID') and host.udp_owner(room['port'], info['MainPID']) is not True:
        raise CliError('Running service does not verifiably own the parsed game port; inspect or stop it before adoption.', 'query_ownership_unknown', 4)
    if any(r['port'] == room['port'] and r['bind'] == room['bind'] for r in all_rooms().values()):
        raise CliError('Another registered room uses this endpoint.', 'port_conflict', 4)
    plan = {'action': 'adopt', 'room': room_id, 'service': service, 'directory': str(directory), 'restarts_server': False}
    early = base.prepare(plan, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        base.recover()
        data = registry()
        room['hashes'] = {str(p): storage.sha256(p) for p in (script, cfg, unit)}
        data['rooms'][room_id] = room
        base.save(base.ROOMS, data, private=True)
    return {'adopted': room_id, 'restarts_server': False}


def validate(room):
    fields = {field.name for field in dataclasses.fields(model.Config)}
    config = model.Config(**{key: value for key, value in room.items() if key in fields})
    config.validate()
    if not config.name.isascii():
        raise CliError('Use an ASCII room name.', 'unsupported_server_name', 2)


def render(room, existing='', password=None):
    values = {'hostname': room['name'], 'sv_lan': '0', 'mp_gamemode': room['mode'],
              'z_difficulty': room['difficulty'].capitalize(), 'sv_hibernate_when_empty': '0',
              'sv_steamgroup': room.get('steam_group_id', ''),
              'sv_steamgroup_exclusive': '1' if room['visibility'] == 'group' else '0',
              'sv_allow_lobby_connect_only': '1' if room['visibility'] == 'group' else '0'}
    if password is not None:
        values['rcon_password'] = password
    output = []
    for line in existing.splitlines():
        if not any(re.match(r'^\s*' + re.escape(key) + r'(?:\s|$)', line) for key in values):
            output.append(line)
    output.extend(key + ' ' + model.cfg_quote(value) for key, value in values.items())
    return ('\n'.join(output) + '\n').encode()


def unit_text(room):
    args = [str(Path(room['game_dir']) / 'srcds_run'), '-game', 'left4dead2', '-console', '-strictportbind',
            '-ip', room['bind'], '-port', str(room['port']), '+servercfgfile', Path(room['cfg']).name,
            '+sv_hibernate_when_empty', '0', '+map', room['map']]
    return ('[Unit]\nDescription=L4D2 room ' + room['id'] + '\nAfter=network-online.target\n'
            '[Service]\nType=simple\nUser=' + room['user'] + '\nGroup=' + room['group'] +
            '\nWorkingDirectory=' + room['game_dir'].replace('%', '%%') + '/\nExecStart=' +
            ' '.join(model.systemd_quote(a) for a in args) + '\nRestart=on-failure\nRestartSec=5\n'
            'KillSignal=SIGINT\nKillMode=control-group\nTimeoutStopSec=45\nNoNewPrivileges=true\n'
            'PrivateTmp=true\nProtectSystem=full\nProtectHome=read-only\nUMask=0027\n'
            '[Install]\nWantedBy=multi-user.target\n').encode()


def save_room(room, writes):
    data = registry()
    room = room.copy()
    hashes = room.get('hashes', {}).copy()
    import hashlib
    for target, contents, *_ in writes:
        if contents is not None:
            hashes[str(target)] = hashlib.sha256(contents).hexdigest()
    room['hashes'] = hashes
    data['rooms'][room['id']] = room
    base.transaction([*writes, (base.ROOMS, base.json_bytes(data), 0o600, -1, -1)])
    run(['systemctl', 'daemon-reload'])


def create(room_id, source, port, name=None, map_name=None, dry_run=False, yes=False):
    base.identifier(room_id)
    if room_id == 'main' or room_id in all_rooms():
        raise CliError('Room ID is already registered or reserved.', 'room_conflict', 4)
    origin = get(source)
    room = {key: value for key, value in origin.items() if key not in {'hashes', 'script', 'running', 'running_invocation', 'configuration_pending'}}
    room.update(id=room_id, port=port, name=name or room_id, map=map_name or origin['map'], kind='created',
                autostart=False, service='l4d2-' + room_id + '.service',
                cfg=str(game(origin) / 'cfg' / ('l4d2-' + room_id + '.cfg')),
                unit=str(base.UNITS / ('l4d2-' + room_id + '.service')))
    validate(room)
    from .content import inventory
    if room['map'] not in inventory(origin)['maps']:
        raise CliError('Default map is not installed.', 'map_not_installed', 2)
    for path in (room['cfg'], room['unit']):
        if Path(path).exists():
            raise CliError('Room configuration or unit already exists.', 'target_conflict', 4)
    if any(r['port'] == port for r in all_rooms().values()):
        raise CliError('Registered port is already in use.', 'port_conflict', 4)
    host.check_udp_available(room['bind'], port)
    early = base.prepare({'action': 'create-room', 'room': room_id, 'port': port,
                          'shared_with': affected(origin), 'starts_server': False}, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    with storage.operation_lock():
        if room_id in all_rooms() or Path(room['cfg']).exists() or Path(room['unit']).exists():
            raise CliError('Room was created while the plan was pending.', 'room_conflict', 4)
        save_room(room, [(Path(room['cfg']), render(room), 0o640, 0, gid),
                         (Path(room['unit']), unit_text(room), 0o644, 0, 0)])
    return {'created': room_id, 'starts_server': False}


def configure(room_id, settings, password=None, dry_run=False, yes=False):
    allowed = {'name', 'bind', 'map', 'port', 'visibility', 'steam_group_id', 'mode', 'difficulty', 'autostart'}
    if set(settings) - allowed:
        raise CliError('Unsupported room configuration fields.', 'invalid_config', 2)
    room = get(room_id)
    updated = {**room, **settings}
    validate(updated)
    if 'difficulty' in settings and updated['mode'] == 'versus':
        raise CliError('Explicit difficulty is only supported for coop.', 'difficulty_mode_mismatch', 2)
    if room['kind'] == 'main':
        config, _ = manager.installed()
        private = storage.load_secrets()
        if password is not None:
            private['rcon_password'] = password
        return manager.configure(dataclasses.replace(config, **settings), private, dry_run=dry_run, yes=yes)
    from .content import inventory
    if updated['map'] not in inventory(room)['maps']:
        raise CliError('Selected map is not installed.', 'map_not_installed', 2)
    info = check(room)
    if info.get('MainPID'):
        updated['running'] = (room.get('running') if room.get('running_invocation') == info.get('InvocationID') else None) or {
            key: room[key] for key in ('bind', 'port', 'map')}
        updated['running_invocation'] = info.get('InvocationID')
        updated['configuration_pending'] = True
    if updated['port'] != room['port'] or updated['bind'] != room['bind']:
        live = updated.get('running') or room
        owned_live_endpoint = (info.get('MainPID') and updated['port'] == live['port'] and
                               updated['bind'] == live['bind'] and
                               host.udp_owner(live['port'], info['MainPID']) is True)
        if not owned_live_endpoint:
            host.check_udp_available(updated['bind'], updated['port'])
    if any(r['id'] != room_id and r['port'] == updated['port'] for r in all_rooms().values()):
        raise CliError('Registered port is already in use.', 'port_conflict', 4)
    plan = {'action': 'configure-room', 'room': room_id, 'changes': settings,
            'changes_rcon_password': password is not None, 'restarts_server': False}
    early = base.prepare(plan, dry_run, yes)
    if early:
        return early
    uid, gid = base.account(room['user'], room.get('group'))
    writes = [(Path(room['cfg']), render(updated, base.text(room['cfg']), password), 0o640, 0, gid)]
    if room['kind'] == 'created':
        writes.append((Path(room['unit']), unit_text(updated), 0o644, 0, 0))
    else:
        text = base.text(room['script'])
        for names, value in ((r'\+map', updated['map']), (r'\+hostport|\+port|-port', str(updated['port'])),
                             (r'\+ip|-ip', updated['bind'])):
            text = re.sub(r'(?P<key>(?:^|\s)(?:' + names + r'))\s+["\']?[A-Za-z0-9_.-]+["\']?',
                          lambda m: m['key'] + ' ' + value, text, count=1)
        writes.append((Path(room['script']), text.encode(), 0o750, uid, gid))
    with storage.operation_lock():
        check(get(room_id))
        save_room(updated, writes)
        run(['systemctl', 'enable' if updated['autostart'] else 'disable', room['service']])
    return {'saved': room_id, 'restart_required': bool(info.get('MainPID'))}


def lifecycle(room_id, action, timeout=180, dry_run=False, yes=False, as_json=False):
    room = get(room_id)
    if room['kind'] == 'main':
        return manager.lifecycle(action, timeout=timeout, dry_run=dry_run, yes=yes, as_json=as_json)
    current = status(room)
    early = base.prepare({'action': action, 'room': room_id, 'service': room['service'],
                          'disconnects_players': action != 'start', 'query': current['query']}, dry_run, yes, as_json)
    if early:
        return early
    with storage.operation_lock():
        base.recover()
        room = get(room_id)
        check(room)
        current = status(room)
        secrets(room)
        deadline = time.monotonic() + timeout
        if action == 'start' and current['game_ready']:
            return {'already_running': True, **current}
        if action in {'stop', 'restart'}:
            run(['systemctl', 'stop', room['service']], timeout=max(1, deadline - time.monotonic()))
            info = host.service_info(service=room['service'])
            if info.get('MainPID') or host.service_pids(info):
                raise CliError('Room processes remain after stop.', 'stop_incomplete', 4)
            endpoint = current.get('running_endpoint', room)
            host.check_udp_available(endpoint['bind'], endpoint['port'])
        if action in {'start', 'restart'}:
            from .content import inventory
            if room['map'] not in inventory(room)['maps']:
                raise CliError('Default map is missing.', 'map_not_installed', 2)
            if not current['game_ready'] or action == 'restart':
                host.check_udp_available(room['bind'], room['port'])
                run(['systemctl', 'start', room['service']], timeout=max(1, deadline - time.monotonic()))
            while time.monotonic() < deadline:
                report = status(room)
                if report['game_ready']:
                    data = registry()
                    data['rooms'][room_id].update(running={key: room[key] for key in ('bind', 'port', 'map')},
                                                  running_invocation=report['service'].get('InvocationID'),
                                                  configuration_pending=False)
                    base.save(base.ROOMS, data, private=True)
                    report['configuration_pending'] = False
                    return report
                if report['service'].get('ActiveState') == 'failed':
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            raise CliError('Room did not become ready; inspect room status/logs.', 'startup_not_ready', 4)
    return status(room)


def unregister(room_id, dry_run=False, yes=False):
    room = get(room_id)
    if room['kind'] == 'main':
        raise CliError('The original main registration is retained.', 'protected_room', 4)
    info = check(room)
    if info.get('MainPID') or info.get('ActiveState') not in {'inactive', 'failed'}:
        raise CliError('Stop the room before removing its registration.', 'room_running', 4)
    early = base.prepare({'action': 'remove-room', 'room': room_id, 'removes_game_files': False,
                          'removes_unit': room['kind'] == 'created'}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        data = registry()
        del data['rooms'][room_id]
        writes = [(base.ROOMS, base.json_bytes(data), 0o600, -1, -1)]
        if room['kind'] == 'created':
            run(['systemctl', 'disable', room['service']])
            writes.extend((Path(room[key]), None, 0o644, -1, -1) for key in ('unit', 'cfg'))
        base.transaction(writes)
        run(['systemctl', 'daemon-reload'])
    return {'removed': room_id}


def logs(room_id, lines=100, since=None, follow=False):
    room = get(room_id)
    if room['kind'] == 'main':
        return manager.logs(lines, since, follow)
    host.require_root()
    secrets(room)
    if not 1 <= lines <= 10000:
        raise CliError('Lines must be 1..10000.', 'invalid_lines', 2)
    args = ['journalctl', '-u', room['service'], '-n', str(lines), '--no-pager', '--output=short-iso']
    if since:
        from datetime import datetime
        datetime.fromisoformat(since)
        args.extend(['--since', since])
    if follow:
        args.append('--follow')
    run(args, stream=True, stream_stdout=True, timeout=None if follow else 30)
    return {}


def refresh(room_id, dry_run=False, yes=False):
    room = get(room_id)
    if room['kind'] == 'main':
        raise CliError('Main uses private configuration transactions; preserve and resolve its recorded conflict.', 'protected_room', 4)
    info = host.service_info(service=room['service'])
    if info.get('FragmentPath') != room['unit'] or info.get('DropInPaths') or info.get('User') != room['user']:
        raise CliError('Service identity changed; refresh refused.', 'service_conflict', 4)
    values = secrets(room)
    updated = room.copy()
    for key, cvar in (('name', 'hostname'), ('mode', 'mp_gamemode'), ('difficulty', 'z_difficulty'), ('steam_group_id', 'sv_steamgroup')):
        if cvar in values:
            updated[key] = values[cvar].lower() if key == 'difficulty' else values[cvar]
    updated['visibility'] = 'group' if values.get('sv_steamgroup_exclusive') == '1' else 'public'
    launch = base.text(room.get('script') or room['unit'])
    for key, pattern in (('map', r'\+map'), ('port', r'\+hostport|\+port|-port'), ('bind', r'\+ip|-ip')):
        match = re.search(r'(?:^|\s|\")(' + pattern + r')["\']?\s+["\']?([A-Za-z0-9_.-]+)', launch)
        if match:
            updated[key] = int(match[2]) if key == 'port' else match[2]
    validate(updated)
    early = base.prepare({'action': 'refresh-room-registration', 'room': room_id,
                          'accepts_external_edits': True, 'restarts_server': False}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        updated['hashes'] = {str(Path(room[key])): storage.sha256(base.regular(room[key]))
                             for key in ('cfg', 'unit', 'script') if room.get(key)}
        data = registry()
        data['rooms'][room_id] = updated
        base.save(base.ROOMS, data, private=True)
    return {'refreshed': room_id, 'restarts_server': False}
