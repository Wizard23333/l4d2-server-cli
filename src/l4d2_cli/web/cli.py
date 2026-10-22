"""Optional panel deployment and internal least-privilege bridge entrypoints."""
from __future__ import annotations

import json
import sys

from ..common import CliError


def register(groups):
    from ..cli import output_flags
    sub = groups.add_parser('web', help='Install and manage the optional CLI-backed web panel').add_subparsers(dest='action', required=True)
    for name in ('install', 'configure'):
        item = sub.add_parser(name)
        item.add_argument('--host', help='IPv4 listen address; first install defaults to 127.0.0.1')
        item.add_argument('--port', type=int, help='First install defaults to 8081')
        item.add_argument('--room', help='Default registered room; first install defaults to main')
        item.add_argument('--username')
        item.add_argument('--password-file', help='Private UTF-8 password file, mode 0600')
        item.add_argument('--cli-path', help='Root-owned executable CLI (default /usr/local/bin/l4d2)')
        output_flags(item, changes=True, confirmation=True)
    for name in ('start', 'stop', 'restart', 'uninstall'):
        item = sub.add_parser(name)
        output_flags(item, changes=True, confirmation=True)
    for name in ('status', 'check'):
        output_flags(sub.add_parser(name))
    item = sub.add_parser('logs')
    item.add_argument('--lines', type=int, default=100)
    item = sub.add_parser('_serve', help='Internal service entrypoint')
    item = sub.add_parser('_bridge', help='Internal validated sudo bridge')


def dispatch(args):
    from . import manager
    if args.action == '_serve':
        from .server import serve
        serve()
        return {}
    if args.action == '_bridge':
        from .. import host
        from .bridge import handle
        host.require_root()
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise CliError('Bridge request is too large.', 'request_too_large', 2)
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise CliError('Invalid bridge request.', 'invalid_arguments', 2)
        return handle(request)
    if args.action in {'install', 'configure'}:
        return manager.install(args, configure_only=args.action == 'configure')
    if args.action == 'check':
        report = manager.check()
        if not report['healthy']:
            raise CliError('Web self-check found failed checks.', 'web_unhealthy', 4, report)
        return report
    if args.action == 'status':
        return manager.status()
    if args.action == 'logs':
        return manager.logs(args.lines)
    return manager.lifecycle(args.action, args.dry_run, args.yes)
