"""Bounded Source RCON framing; secrets never travel in CLI arguments."""
from __future__ import annotations

import socket
import struct
import time

from .. import host, manager, storage
from ..common import CliError
from . import base, rooms


def command(room, text, timeout=15, dry_run=False, yes=False):
    if not text or len(text) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in text):
        raise CliError('RCON command must be one ASCII line (1..4096 bytes).', 'invalid_rcon_command', 2)
    host.require_root()
    password = rooms.secrets(room).get('rcon_password')
    if not password:
        raise CliError('Configure a private RCON password for this room first.', 'rcon_password_missing', 4)
    early = base.prepare({'action': 'rcon', 'room': room['id'], 'command': text,
                          'may_affect_players': True}, dry_run, yes)
    if early:
        return early
    # Saved endpoint changes take effect only after an explicit restart. Use the
    # same invocation-aware endpoint as status, including for the original main.
    endpoint = rooms.status(room).get('running_endpoint') or room
    address = manager.query_address(manager.Config(bind=endpoint['bind']))
    deadline = time.monotonic() + timeout
    try:
        with storage.operation_lock(), socket.create_connection((address, endpoint['port']), timeout=timeout) as sock:
            def exact(length):
                output = bytearray()
                while len(output) < length:
                    sock.settimeout(max(0.01, deadline - time.monotonic()))
                    if time.monotonic() >= deadline:
                        raise socket.timeout()
                    block = sock.recv(length - len(output))
                    if not block:
                        raise OSError('RCON closed')
                    output.extend(block)
                return bytes(output)

            def send(request, kind, body):
                payload = struct.pack('<ii', request, kind) + body.encode('utf-8') + b'\0\0'
                sock.sendall(struct.pack('<i', len(payload)) + payload)

            def receive():
                length, = struct.unpack('<i', exact(4))
                if not 10 <= length <= 4 * 1024 * 1024:
                    raise CliError('Invalid RCON response frame.', 'invalid_rcon_packet', 4)
                data = exact(length)
                return (*struct.unpack('<ii', data[:8]), data[8:-2])

            send(1, 3, password)
            while True:
                request, kind, body = receive()
                if request == -1:
                    raise CliError('RCON authentication failed.', 'rcon_auth_failed', 4)
                if request == 1 and kind == 2:
                    break
            send(2, 2, text)
            send(3, 2, 'echo L4D2_CLI_RCON_END')
            result = bytearray()
            while True:
                request, kind, body = receive()
                if request == 3:
                    break
                if request == 2 and kind == 0:
                    result.extend(body)
                if len(result) > 4 * 1024 * 1024:
                    raise CliError('RCON response too large.', 'response_too_large', 4)
    except OSError:
        raise CliError('RCON connection failed or timed out; query room status before retrying a mutation.', 'rcon_connection_failed', 4) from None
    return {'room': room['id'], 'response': result.decode('utf-8', errors='replace')}


def changelevel(room, map_name, timeout=60, dry_run=False, yes=False):
    from .content import inventory
    if not base.MAP.fullmatch(map_name) or map_name not in inventory(room)['maps']:
        raise CliError('Target map is not installed.', 'map_not_installed', 2)
    current = rooms.status(room)
    early = base.prepare({'action': 'changelevel', 'room': room['id'], 'map': map_name,
                          'query': current.get('query'), 'restarts_systemd_service': False}, dry_run, yes)
    if early:
        return early
    try:
        command(room, 'changelevel ' + map_name, min(timeout, 15), yes=True)
    except CliError as exc:
        if exc.code != 'rcon_connection_failed':
            raise
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = rooms.status(room)
        if (report.get('query') or {}).get('map') == map_name and report['game_ready']:
            return {'changed': True, 'map': map_name, 'room': room['id'], 'game_ready': True}
        time.sleep(1)
    raise CliError('Map switch was sent but could not be verified; inspect room status/logs.', 'map_switch_unverified', 4)
