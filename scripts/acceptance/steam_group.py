"""Real public -> Steam group -> public -> Steam group roundtrip for a test room.

Requires an installed running main room, zero human players, configured private
RCON and explicit permission to reconfigure/restart it. Final mode is group.
Does not modify old rooms, game resources, firewall or Steam account caches.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli', type=Path, default=Path(__file__).resolve().parents[2] / 'bin/l4d2')
parser.add_argument('--expected-game-dir', required=True)
parser.add_argument('--steam-group-id', required=True)
parser.add_argument('--evidence-dir', type=Path, required=True, help='New private result directory')
parser.add_argument('--allow-service-changes', action='store_true')
args = parser.parse_args()
if os.name != 'posix' or os.geteuid() != 0 or not args.allow_service_changes:
    parser.error('Requires Linux root and --allow-service-changes on the main test room')
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from l4d2_cli import storage
from l4d2_cli.model import Config

Config(visibility='group', steam_group_id=args.steam_group_id).validate()
os.umask(0o077)
evidence = args.evidence_dir.resolve()
evidence.mkdir(mode=0o700, exist_ok=False)
cli = [sys.executable, str(args.cli.resolve())]
report = {'version': subprocess.check_output(cli + ['--version'], text=True).strip(),
          'steps': [], 'client_join': 'not_verified', 'steam_group_listing': 'not_verified'}


def save():
    (evidence / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def call(label, command, expected_error=None):
    started = time.monotonic()
    completed = subprocess.run(cli + command + ['--json'], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=230)
    payload = json.loads(completed.stdout)
    passed = (completed.returncode == 2 and (payload.get('error') or {}).get('code') == expected_error
              if expected_error else completed.returncode == 0 and payload.get('ok') is True)
    report['steps'].append({'case': label, 'passed': passed, 'exit_code': completed.returncode,
                            'seconds': round(time.monotonic() - started, 3), 'result': payload})
    save()
    print(json.dumps({'case': label, 'passed': passed, 'exit_code': completed.returncode}), flush=True)
    if not passed:
        raise RuntimeError('CLI case failed: ' + label)
    return payload.get('data', {})


def snapshot():
    files = [Path('/etc/l4d2-cli/config.toml'), Path('/etc/l4d2-cli/secrets.toml'),
             Path('/var/lib/l4d2-cli/state.json'), Path('/etc/systemd/system/l4d2-main.service'),
             Path(args.expected_game_dir) / 'left4dead2/cfg/l4d2-cli.cfg']
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]


def rcon_values(status):
    password = storage.load_secrets().get('rcon_password')
    if not password:
        raise RuntimeError('Configure private RCON before this engine-level acceptance test')
    bind = status['running_endpoint']['bind']
    address = status['connection']['local_query'].rsplit(':', 1)[0] if bind == '0.0.0.0' else bind
    port = status['running_endpoint']['port']

    def packet(request, kind, text):
        body = struct.pack('<ii', request, kind) + text.encode() + b'\0\0'
        return struct.pack('<i', len(body)) + body

    def read_exact(sock, length):
        value = b''
        while len(value) < length:
            chunk = sock.recv(length - len(value))
            if not chunk:
                raise RuntimeError('RCON connection closed')
            value += chunk
        return value

    def receive(sock):
        length, = struct.unpack('<i', read_exact(sock, 4))
        if not 10 <= length <= 65536:
            raise RuntimeError('Unexpected RCON packet size')
        body = read_exact(sock, length)
        return struct.unpack('<ii', body[:8]), body[8:-2].decode(errors='replace')

    result = {}
    with socket.create_connection((address, port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(packet(1, 3, password))
        header, text = receive(sock)
        if header == (1, 0):
            header, text = receive(sock)
        if header != (1, 2):
            raise RuntimeError('Private RCON authentication failed')
        for request, name in enumerate(['sv_steamgroup', 'sv_steamgroup_exclusive', 'sv_allow_lobby_connect_only'], 2):
            sock.sendall(packet(request, 2, name))
            header, text = receive(sock)
            match = re.search(r'^"' + re.escape(name) + r'" = "([^"]*)"', text, re.MULTILINE)
            if header != (request, 0) or not match:
                raise RuntimeError('No engine value for ' + name)
            result[name] = match[1]
    return result


def verify(label, visibility):
    current = call(label, ['server', 'status'])
    assert current['game_ready'] and current['port_owned_by_service'] is True
    assert current['configuration_pending'] is False
    assert current['desired_config']['visibility'] == visibility
    assert current['running_config']['visibility'] == visibility
    group = visibility == 'group'
    expected = {'sv_steamgroup': args.steam_group_id if group else '',
                'sv_steamgroup_exclusive': '1' if group else '0',
                'sv_allow_lobby_connect_only': '1' if group else '0'}
    assert rcon_values(current) == expected
    report.setdefault('engine_checks', []).append({'case': label, 'passed': True, 'values': expected})
    save()
    return current


try:
    initial = call('GRP-01-initial', ['server', 'status'])
    assert initial['installation'] == 'installed'
    assert initial['desired_config']['game_dir'] == args.expected_game_dir, 'Registered target differs'
    assert initial['game_ready'] and initial['query']['human_players'] == 0, 'Test room must be ready and empty'
    assert initial['configuration_pending'] is False
    assert initial['desired_config']['visibility'] == 'public', 'Start this roundtrip from public mode'
    rcon_values(initial)  # Verify the private engine probe works before changing configuration.
    before = snapshot()
    call('GRP-02-dry-run', ['server', 'configure', '--visibility', 'group', '--steam-group-id', args.steam_group_id, '--dry-run'])
    assert snapshot() == before
    for label, flags, code in [
            ('GRP-03-missing-group', ['--visibility', 'group'], 'steam_group_required'),
            ('GRP-04-reject-group64', ['--steam-group-id', '103582791441867086'], 'invalid_steam_group_id'),
            ('GRP-05-reject-conflict', ['--visibility', 'public', '--steam-group-id', args.steam_group_id], 'invalid_group_settings')]:
        call(label, ['server', 'configure', *flags, '--yes'], expected_error=code)
        assert snapshot() == before
    call('GRP-06-save-group', ['server', 'configure', '--visibility', 'group', '--steam-group-id', args.steam_group_id, '--yes'])
    pending = call('GRP-07-pending', ['server', 'status'])
    assert pending['service']['MainPID'] == initial['service']['MainPID']
    assert pending['configuration_pending'] is True
    assert pending['running_config']['visibility'] == 'public'
    assert rcon_values(pending)['sv_steamgroup'] == ''
    call('GRP-08-restart-group', ['server', 'restart', '--yes', '--timeout', '180'])
    group = verify('GRP-09-engine-group', 'group')
    assert group['connection']['player_command'] is None
    call('GRP-10-save-public', ['server', 'configure', '--visibility', 'public', '--yes'])
    call('GRP-11-restart-public', ['server', 'restart', '--yes', '--timeout', '180'])
    public = verify('GRP-12-engine-public', 'public')
    assert public['desired_config']['steam_group_id'] == ''
    assert public['connection']['player_command']
    call('GRP-13-group-id-shorthand', ['server', 'configure', '--steam-group-id', args.steam_group_id, '--yes'])
    call('GRP-14-restart-final-group', ['server', 'restart', '--yes', '--timeout', '180'])
    final = verify('GRP-15-final-engine-group', 'group')
    again = call('GRP-16-idempotent', ['server', 'configure', '--visibility', 'group', '--steam-group-id', args.steam_group_id, '--yes'])
    assert again['unchanged'] is True
    after = call('GRP-17-final-status', ['server', 'status'])
    assert after['service']['MainPID'] == final['service']['MainPID']
    report['final_ready'] = True
    report['final_visibility'] = 'group'
    report['join_method'] = 'steam_group_lobby'
    save()
    print('STEAM_GROUP_ROOM_READY', flush=True)
except BaseException as error:
    report['failure'] = type(error).__name__ + ': ' + str(error)
    save()
    raise
