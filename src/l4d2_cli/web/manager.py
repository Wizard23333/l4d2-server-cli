"""Idempotent optional service deployment; game services are never restarted."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
import urllib.request
from pathlib import Path

from .. import host, storage
from ..common import CliError, confirm, run
from ..ops import base, rooms

CONFIG = Path('/etc/l4d2-cli/web.json')
SECRETS = Path('/etc/l4d2-cli/web-secrets.json')
STATE = Path('/var/lib/l4d2-cli/web')
UNIT = Path('/etc/systemd/system/l4d2-cli-web.service')
SUDOERS = Path('/etc/sudoers.d/l4d2-cli-web')
SERVICE = 'l4d2-cli-web.service'
USER = 'l4d2web'
STATIC = Path(__file__).parent / 'static'


def configuration():
    return base.read(CONFIG)


def credentials():
    return base.read(SECRETS)


def digest(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 210000).hex()


def valid_password(value, private):
    try:
        return hmac.compare_digest(digest(value, private['salt']), private['password_hash'])
    except (KeyError, ValueError, TypeError):
        return False


def trusted_cli(filename):
    path = Path(filename)
    if not path.is_absolute() or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)):
        raise CliError('CLI path must be a safe absolute executable path.', 'unsafe_cli', 2)
    target = path.resolve(strict=True)
    if not target.is_file() or not os.access(target, os.X_OK):
        raise CliError('Install an executable CLI before installing the web panel.', 'missing_cli', 4)
    for item in {path, target, *target.parents, *path.parents}:
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise CliError('CLI executable and parents must be root-owned and not group/world writable.', 'unsafe_cli', 4)
    return str(path)


def service_info():
    response = run(['systemctl', 'show', SERVICE, '--no-pager', '--property=ActiveState,SubState,MainPID,FragmentPath'], check=False)
    return dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)


def check_port_available(config):
    import socket
    try:
        with socket.socket() as probe:
            # Match PanelServer: closed connections may remain after a cutover.
            # An active listener still prevents this bind on Linux.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((config['host'], config['port']))
    except OSError:
        raise CliError('Web listen address/port is unavailable.', 'port_in_use', 4) from None


def install(args, configure_only=False):
    host.require_platform()
    old = configuration()
    if configure_only and not old:
        raise CliError('Install the panel first.', 'web_not_installed', 4)
    config = {**{'host': '127.0.0.1', 'port': 8081, 'room': 'main', 'username': 'admin',
                 'cli_path': '/usr/local/bin/l4d2', 'max_upload_bytes': 2 * 1024**3}, **old}
    for key in ('host', 'port', 'room', 'username', 'cli_path'):
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    try:
        ipaddress.IPv4Address(config['host'])
    except ValueError:
        raise CliError('Listen host must be an IPv4 address.', 'invalid_bind', 2) from None
    if type(config['port']) is not int or not 1024 <= config['port'] <= 65535:
        raise CliError('Web port must be 1024..65535.', 'invalid_port', 2)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', config['username']):
        raise CliError('Invalid web username.', 'invalid_username', 2)
    rooms.get(config['room'])
    config['cli_path'] = trusted_cli(config['cli_path'])
    if not (STATIC / 'index.html').is_file():
        raise CliError('Built frontend is missing from this CLI package.', 'missing_frontend', 4)
    password = None
    if args.password_file:
        from ..cli import password_file
        password = password_file(args.password_file)
        if len(password) < 12:
            raise CliError('Use a web password of at least 12 characters.', 'weak_password', 2)
    if not old and password is None:
        raise CliError('First install requires --password-file (private file, mode 0600).', 'password_required', 2)
    if old and not SECRETS.is_file():
        raise CliError('Panel secrets are missing; restore them before reconfiguration.', 'web_secrets_missing', 4)
    plan = {'action': 'configure-web' if configure_only else 'install-web', 'configuration': config,
            'service': SERVICE, 'restarts_web': True, 'restarts_game_rooms': False,
            'source_branch': 'frontend-redesign', 'source_commit': '6d4817f', 'changes_password': password is not None}
    if args.dry_run:
        return {'dry_run': True, 'plan': plan}
    host.require_root()
    if not shutil.which('sudo') or not shutil.which('visudo'):
        raise CliError('Install sudo/visudo to enable the restricted web bridge.', 'missing_dependency', 4)
    if not old or (config['host'], config['port']) != (old['host'], old['port']):
        check_port_available(config)
    confirm(json.dumps(plan), args.yes)
    import pwd
    try:
        user = pwd.getpwnam(USER)
    except KeyError:
        run(['useradd', '--system', '--home-dir', str(STATE), '--shell', host.nologin_shell(), USER])
        user = pwd.getpwnam(USER)
    if user.pw_uid == 0 or user.pw_gid == 0:
        raise CliError('Unsafe web service account.', 'unsafe_web_user', 4)
    private = credentials() if old else {'session_secret': secrets.token_hex(32)}
    if password is not None:
        private['salt'] = secrets.token_hex(16)
        private['password_hash'] = digest(password, private['salt'])
    cli = config['cli_path']
    unit = ('[Unit]\nDescription=L4D2 CLI optional web panel\nAfter=network.target\n\n[Service]\n'
            f'User={USER}\nGroup={USER}\nExecStart={cli} web _serve\nRestart=on-failure\nRestartSec=3\n'
            'UMask=0077\nPrivateTmp=true\nProtectHome=true\n\n[Install]\nWantedBy=multi-user.target\n')
    # Exact argv: authenticated requests travel through stdin, never a shell or
    # sudo wildcard. The bridge validates operations again as root.
    sudoers = f'{USER} ALL=(root) NOPASSWD: {cli} web _bridge\n'
    with storage.operation_lock():
        STATE.mkdir(parents=True, exist_ok=True)
        os.chown(STATE, 0, user.pw_gid)
        os.chmod(STATE, 0o750)
        uploads = STATE / 'uploads'
        storage.reject_links(uploads)
        uploads.mkdir(exist_ok=True)
        os.chown(uploads, user.pw_uid, user.pw_gid)
        os.chmod(uploads, 0o700)
        candidate = STATE / 'sudoers.check'
        storage.atomic_write(candidate, sudoers.encode(), 0o440)
        try:
            run(['visudo', '-cf', str(candidate)])
        finally:
            candidate.unlink(missing_ok=True)
        base.transaction([(CONFIG, base.json_bytes(config), 0o644, 0, 0),
                          (SECRETS, base.json_bytes(private), 0o640, 0, user.pw_gid),
                          (UNIT, unit.encode(), 0o644, 0, 0), (SUDOERS, sudoers.encode(), 0o440, 0, 0)])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', SERVICE])
    run(['systemctl', 'restart', SERVICE])
    wait_ready(config)
    return {'installed': True, 'configuration': config, 'service': SERVICE, 'restarts_game_rooms': False}


def wait_ready(config):
    for _ in range(20):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(local_url(config) + '/healthz', timeout=2) as response:
                if json.load(response).get('service') == 'l4d2-cli-web':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise CliError('Web service did not become ready; inspect web logs.', 'web_not_ready', 4)


def local_url(config):
    return 'http://%s:%d' % ('127.0.0.1' if config['host'] == '0.0.0.0' else config['host'], config['port'])


def status():
    host.require_platform()
    config = configuration()
    return {'installed': bool(config and UNIT.is_file()), 'configuration': config,
            'service': service_info(), 'url': local_url(config) if config else None,
            'game_services_required_by_web': False}


def check():
    host.require_platform()
    host.require_root()
    config = configuration()
    checks = []
    def add(name, passed, detail=''):
        checks.append({'name': name, 'status': 'pass' if passed else 'fail', 'detail': detail})
    add('installed', bool(config and UNIT.is_file()))
    if not config:
        return {'healthy': False, 'checks': checks}
    add('service_active', service_info().get('ActiveState') == 'active')
    try:
        trusted_cli(config['cli_path'])
        add('trusted_cli', True)
    except CliError as exc:
        add('trusted_cli', False, str(exc))
    add('secrets_permissions', SECRETS.is_file() and SECRETS.stat().st_mode & 0o137 == 0)
    add('sudoers', SUDOERS.is_file() and run(['visudo', '-cf', str(SUDOERS)], check=False).returncode == 0)
    add('frontend', (STATIC / 'index.html').is_file() and bool(list((STATIC / 'assets').glob('*.js'))))
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(local_url(config) + '/healthz', timeout=5) as response:
            add('http_health', json.load(response).get('service') == 'l4d2-cli-web')
    except (OSError, ValueError) as exc:
        add('http_health', False, type(exc).__name__)
    from .server import invoke_bridge
    try:
        result = invoke_bridge({'operation': 'snapshot'}, config, user=USER)
        add('cli_bridge', isinstance(result.get('rooms'), list) and bool(result.get('rooms')))
    except (CliError, ValueError, OSError) as exc:
        add('cli_bridge', False, str(exc))
    return {'healthy': all(item['status'] == 'pass' for item in checks), 'checks': checks,
            'configuration': config, 'functional_acceptance': 'Run the web acceptance suite; self-check is read-only.'}


def lifecycle(action, dry_run=False, yes=False):
    host.require_platform()
    if not configuration():
        raise CliError('Panel is not installed.', 'web_not_installed', 4)
    plan = {'action': action + '-web', 'service': SERVICE, 'restarts_game_rooms': False,
            'retains_settings_and_uploads': True}
    if dry_run:
        return {'dry_run': True, 'plan': plan}
    host.require_root()
    confirm(json.dumps(plan), yes)
    if action == 'uninstall':
        run(['systemctl', 'disable', '--now', SERVICE])
        for path in (UNIT, SUDOERS):
            storage.reject_links(path)
            path.unlink(missing_ok=True)
        run(['systemctl', 'daemon-reload'])
    else:
        run(['systemctl', action, SERVICE])
        if action in {'start', 'restart'}:
            wait_ready(configuration())
    return plan


def logs(lines):
    host.require_platform()
    host.require_root()
    if not 1 <= lines <= 10000:
        raise CliError('Lines must be 1..10000.', 'invalid_lines', 2)
    return {'lines': run(['journalctl', '-u', SERVICE, '-n', str(lines), '--no-pager', '--output=short-iso']).stdout.splitlines()}
