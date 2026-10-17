"""Read-only diagnosis and explicit scoped repairs/network/storage maintenance."""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import stat
import time
from pathlib import Path

from .. import host, storage
from ..common import CliError
from . import base, content, plugins, rooms, transport


def usage(room):
    game = rooms.game(room)
    sections = {}
    for name in ('maps', 'addons', 'addons_disabled', 'missions', 'downloads'):
        total = 0
        for directory, dirs, files in os.walk(game / name, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for name_file in files:
                path = Path(directory) / name_file
                if not path.is_symlink():
                    total += path.stat().st_size
        sections[name] = total
    disk = shutil.disk_usage(game)
    return {'room': room['id'], 'disk': dict(total=disk.total, used=disk.used, free=disk.free), 'resources': sections}


def diagnose(room, lines=100):
    if not 1 <= lines <= 10000:
        raise CliError('Lines must be 1..10000.', 'invalid_lines', 2)
    report = rooms.status(room)
    checks = []
    game = rooms.game(room)
    for path in sorted((game / 'missions').glob('*.txt')):
        base.regular(path)
        if path.stat().st_size > 1024**2:
            checks.append({'file': path.name, 'issue': 'mission_too_large'})
            continue
        data = path.read_bytes()
        if data.startswith(b'\xef\xbb\xbf'):
            checks.append({'file': path.name, 'issue': 'utf8_bom'})
        from .missions import strip_keyvalues_comments
        text = strip_keyvalues_comments(data.decode('utf-8-sig', errors='replace'))
        unquoted = re.sub(r'"(?:\\.|[^"\\])*"', '', text)
        depth = 0
        valid = True
        for char in unquoted:
            depth += 1 if char == '{' else (-1 if char == '}' else 0)
            valid &= depth >= 0
        if depth != 0 or not valid:
            checks.append({'file': path.name, 'issue': 'unbalanced_keyvalues'})
    errors = []
    logs = sorted((plugins.directory(room) / 'logs').glob('errors_*.log'))
    if logs:
        from collections import deque
        with base.regular(logs[-1]).open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if 'error session started' in line.lower():
                    errors.clear()
                errors.append(line.rstrip())
                errors = errors[-lines:]
    return {'server': report, 'storage': usage(room), 'mission_checks': checks,
            'plugins': plugins.inventory(room), 'sourcemod_errors': errors,
            'content_errors': content.inventory(room)['errors'],
            'external_player_join': 'unverified'}


def repair_mission(room, name, dry_run=False, yes=False):
    path = base.inside(rooms.game(room) / 'missions', base.filename(name, '.txt'))
    data = base.regular(path).read_bytes()
    if not data.startswith(b'\xef\xbb\xbf'):
        return {'unchanged': True}
    early = base.prepare({'action': 'remove-mission-bom', 'file': name, 'affected_rooms': rooms.affected(room)}, dry_run, yes)
    if early:
        return early
    metadata = path.stat()
    with storage.operation_lock():
        base.transaction([(path, data[3:], metadata.st_mode & 0o777, metadata.st_uid, metadata.st_gid)])
    return {'repaired': name, 'repair': 'utf8_bom_removed'}


def repair_permissions(room, scope, dry_run=False, yes=False):
    allowed = {'maps', 'missions', 'addons', 'addons_disabled'}
    if scope not in allowed:
        raise CliError('Unsupported repair scope.', 'invalid_scope', 2)
    root = rooms.game(room) / scope
    uid, gid = base.account(room['user'], room.get('group'))
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            storage.reject_links(path)
            paths.append(path)
    early = base.prepare({'action': 'repair-permissions', 'scope': scope, 'entries': len(paths),
                          'owner': room['user'], 'affected_rooms': rooms.affected(room)}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        for path in [root, *paths]:
            storage.reject_links(path)
            with base.parent_fd(path) as parent:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    info = os.fstat(descriptor)
                    is_directory = stat.S_ISDIR(info.st_mode)
                    if not is_directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1):
                        raise CliError('Refusing linked or nonregular repair target.', 'unsafe_path', 4)
                    os.fchown(descriptor, uid, gid)
                    os.fchmod(descriptor, 0o755 if is_directory or path.name in {'spcomp', 'spcomp64'} else 0o644)
                finally:
                    os.close(descriptor)
    return {'repaired_entries': len(paths) + 1}


def network_writes(values):
    from .backup import NETWORK
    writes = []
    if 'hosts' in values:
        block = values['hosts']
        for line in block.splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) != 2:
                raise CliError('CDN block must contain IP/domain pairs.', 'invalid_hosts', 2)
            ipaddress.ip_address(parts[0])
            if not any(parts[1] == d or parts[1].endswith('.' + d) for d in ('steamusercontent.com', 'akamaihd.net', 'steampowered.com')):
                raise CliError('CDN host is outside supported Steam domains.', 'invalid_hosts', 2)
        path = NETWORK['hosts']
        text = path.read_text()
        text = re.sub(r'(?ms)^# L4D2-STEAM-CDN-BEGIN\s*$.*?^# L4D2-STEAM-CDN-END[^\n]*\n?', '', text)
        if block:
            body = '\n'.join(line for line in block.splitlines() if line.strip() and not line.startswith('#'))
            text = text.rstrip() + '\n# L4D2-STEAM-CDN-BEGIN\n' + body + '\n# L4D2-STEAM-CDN-END\n'
        writes.append((path, text.encode(), 0o644, 0, 0))
    if 'gai' in values:
        path = NETWORK['gai']
        line = 'precedence ::ffff:0:0/96 100'
        text = path.read_text() if path.exists() else ''
        text = '\n'.join(s for s in text.splitlines() if s.strip() != line) + '\n'
        if values['gai']:
            text += line + '\n'
        writes.append((path, text.encode(), 0o644, 0, 0))
    return writes


def network_configure(proxy_file=None, api_key_file=None, clear_proxy=False, hosts_file=None,
                      ipv4=None, dry_run=False, yes=False):
    values = transport.settings()
    if proxy_file:
        path = base.regular(proxy_file)
        if path.stat().st_mode & 0o077:
            raise CliError('Proxy file must be private (0600).', 'secret_permissions', 3)
        proxy = path.read_text().strip()
        parsed = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(proxy)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise CliError('Use an HTTP/HTTPS proxy URL.', 'invalid_proxy', 2)
        values['proxy'] = proxy
    if clear_proxy:
        values['proxy'] = ''
    if api_key_file:
        path = base.regular(api_key_file)
        if path.stat().st_mode & 0o077:
            raise CliError('API key file must be private (0600).', 'secret_permissions', 3)
        key = path.read_text().strip()
        if not re.fullmatch(r'[A-Fa-f0-9]{32}', key):
            raise CliError('Invalid Steam Web API key.', 'invalid_api_key', 2)
        values['steam_web_api_key'] = key
    network = {}
    if hosts_file:
        path = base.regular(hosts_file)
        if path.stat().st_size > 65536:
            raise CliError('Hosts input too large.', 'invalid_hosts', 2)
        network['hosts'] = path.read_text()
    if ipv4 is not None:
        network['gai'] = 'precedence ::ffff:0:0/96 100' if ipv4 else ''
    writes = network_writes(network)
    early = base.prepare({'action': 'configure-network', 'proxy_configured': bool(values.get('proxy')),
                          'api_key_configured': bool(values.get('steam_web_api_key')),
                          'system_files': [str(w[0]) for w in writes]}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        base.transaction([*writes, (transport.CONFIG, base.json_bytes(values), 0o600, 0, 0)])
    return {'saved': True}


def network_status():
    values = transport.settings()
    proxy = values.get('proxy')
    reachable = None
    if proxy:
        import socket
        from urllib.parse import urlsplit
        parsed = urlsplit(proxy)
        try:
            with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=3):
                reachable = True
        except OSError:
            reachable = False
    from .backup import NETWORK
    return {'proxy_configured': bool(proxy), 'proxy_tcp_reachable': reachable,
            'steam_web_api_key_configured': bool(values.get('steam_web_api_key')),
            'ipv4_preferred': NETWORK['gai'].exists() and 'precedence ::ffff:0:0/96 100' in NETWORK['gai'].read_text(),
            'steam_protocol_verified': False}


def cleanup(room, scope, dry_run=False, yes=False):
    scopes = {'cache': storage.PATHS.cache, 'workshop': storage.PATHS.steamcmd / 'steamapps/workshop/content/550',
              'temporary': base.ROOT}
    if scope not in scopes:
        raise CliError('Cleanup scope must be cache, workshop or temporary.', 'invalid_scope', 2)
    root = scopes[scope]
    storage.reject_links(root)
    roots = [root]
    if scope == 'workshop':
        import pwd
        home = Path(pwd.getpwnam(room['user']).pw_dir)
        roots.extend([home / 'Steam/steamapps/workshop/content/550',
                      home / '.local/share/Steam/steamapps/workshop/content/550'])
        roots = list(dict.fromkeys(roots))
        for path in roots:
            storage.reject_links(path)
    if scope == 'temporary':
        roots = []
        for folder in root.iterdir() if root.exists() else []:
            if not folder.is_dir() or not folder.name.startswith(('download-', 'extract-', 'stack-', 'restore-', 'zip-', 'compile-')):
                continue
            storage.reject_links(folder)
            owner = base.read(folder / '.l4d2-owner.json')
            if not owner or type(owner.get('pid')) is not int:
                continue
            current_ticks = base.process_ticks(owner['pid'])
            if current_ticks is None or current_ticks != owner.get('ticks'):
                roots.append(folder)
    files = []
    for selected in roots:
        for directory, dirs, names in os.walk(selected, followlinks=False):
            for name in [*dirs, *names]:
                storage.reject_links(Path(directory) / name)
            files.extend(Path(directory) / name for name in names)
    early = base.prepare({'action': 'cleanup', 'scope': scope, 'files': len(files),
                          'bytes': sum(p.stat().st_size for p in files), 'directories': [str(p) for p in roots],
                          'game_resources_deleted': False}, dry_run, yes)
    if early:
        return early
    with storage.operation_lock():
        # Caches are regenerable; duplicating them for rollback would prevent cleanup on a full disk.
        for path in files:
            storage.reject_links(path)
            base.unlink(path)
        for selected in roots:
            for directory, dirs, names in os.walk(selected, topdown=False):
                path = Path(directory)
                if path != root and not any(path.iterdir()):
                    path.rmdir()
    return {'removed_files': len(files)}
