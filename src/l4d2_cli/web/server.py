"""Unprivileged HTTP/session/static adapter; privileged work uses exact sudo argv."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.cookies
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from email.parser import BytesHeaderParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..common import CliError
from ..ops import base
from . import manager


def invoke_bridge(request, config, user=None):
    argv = ['sudo', '-n', config['cli_path'], 'web', '_bridge']
    if user:
        argv = ['runuser', '-u', user, '--', *argv]
    try:
        response = subprocess.run(argv, input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=180, check=False)
        payload = json.loads(response.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        raise CliError('CLI bridge did not return a valid result.', 'bridge_unavailable', 4) from None
    if response.returncode or not payload.get('ok'):
        error = payload.get('error') or {}
        raise CliError(error.get('message', 'CLI bridge failed.'), error.get('code', 'bridge_failed'),
                       response.returncode or 4, payload.get('data', {}))
    return payload['data']


def multipart(stream, boundary, total, folder, limit):
    """Stream one browser upload with a bounded rolling boundary buffer."""
    if not re.fullmatch(rb'[A-Za-z0-9\x27()+_,./:=? -]{1,100}', boundary):
        raise CliError('Invalid multipart boundary.', 'invalid_upload', 2)
    remaining = total
    def read(size):
        nonlocal remaining
        result = stream.read(min(size, remaining))
        remaining -= len(result)
        if not result and remaining:
            raise CliError('Truncated upload.', 'invalid_upload', 2)
        return result
    buffer = b''
    marker = b'\r\n--' + boundary
    fields, file_info, created = {}, None, []
    try:
        while b'\r\n' not in buffer and remaining:
            buffer += read(4096)
        first, buffer = buffer.split(b'\r\n', 1)
        if first != b'--' + boundary:
            raise CliError('Invalid multipart opening.', 'invalid_upload', 2)
        for _ in range(32):
            while b'\r\n\r\n' not in buffer and remaining and len(buffer) <= 16384:
                buffer += read(4096)
            if b'\r\n\r\n' not in buffer:
                raise CliError('Invalid multipart headers.', 'invalid_upload', 2)
            headers_raw, buffer = buffer.split(b'\r\n\r\n', 1)
            if len(headers_raw) > 16384:
                raise CliError('Upload headers too large.', 'invalid_upload', 2)
            headers = BytesHeaderParser().parsebytes(headers_raw)
            name = headers.get_param('name', header='content-disposition')
            original = headers.get_filename()
            sink, written = None, 0
            if original is not None:
                if file_info or name != 'file':
                    raise CliError('Upload exactly one file.', 'invalid_upload', 2)
                original = Path(original.replace('\\', '/')).name
                original = re.sub(r'[^A-Za-z0-9_. -]', '_', original)
                suffix = Path(original).suffix.lower()
                if suffix not in {'.vpk', '.zip', '.json', '.sp', '.smx', '.txt', '.gz', '.tar', '.cfg'}:
                    raise CliError('Unsupported file extension.', 'invalid_upload', 2)
                upload_id = uuid.uuid4().hex + suffix
                target = base.inside(folder, upload_id)
                sink = target.open('xb')
                os.chmod(target, 0o600)
                created.append(target)
                file_info = {'upload_id': upload_id, 'original': original}
            value = bytearray()
            try:
                while True:
                    position = buffer.find(marker)
                    if position >= 0:
                        chunk, buffer = buffer[:position], buffer[position + len(marker):]
                        final = True
                    elif remaining:
                        keep = len(marker) + 4
                        if len(buffer) > keep:
                            chunk, buffer = buffer[:-keep], buffer[-keep:]
                        else:
                            chunk = b''
                        final = False
                    else:
                        raise CliError('Truncated multipart body.', 'invalid_upload', 2)
                    written += len(chunk)
                    if written > (limit if sink else 65536):
                        raise CliError('Upload field exceeds size limit.', 'request_too_large', 2)
                    if sink:
                        sink.write(chunk)
                    else:
                        value.extend(chunk)
                    if final:
                        break
                    buffer += read(65536)
                if not sink:
                    if not name or name in fields:
                        raise CliError('Invalid duplicate field.', 'invalid_upload', 2)
                    fields[name] = bytes(value).decode('utf-8')
            finally:
                if sink:
                    sink.close()
            while len(buffer) < 2 and remaining:
                buffer += read(4096)
            if buffer.startswith(b'--'):
                if not file_info:
                    raise CliError('No file uploaded.', 'invalid_upload', 2)
                return {**fields, **file_info}
            if not buffer.startswith(b'\r\n'):
                raise CliError('Invalid multipart separator.', 'invalid_upload', 2)
            buffer = buffer[2:]
        raise CliError('Too many multipart fields.', 'invalid_upload', 2)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config, private, bridge=invoke_bridge):
        self.config, self.private, self.bridge = config, private, bridge
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.login_attempts = {}
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'L4D2CLIWeb/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, fmt, *args):
        # Query strings/POST bodies may contain secrets. Only the route/status
        # are useful for diagnosis; never record Authorization or login bodies.
        print('%s %s %s' % (self.command, urllib.parse.urlsplit(self.path).path, args[1] if len(args) > 1 else ''), flush=True)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        # Native form POSTs can serialize Origin as "null" under no-referrer.
        # Preserve the same-origin signal needed by the login CSRF boundary.
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def send_data(self, code, data, content_type='application/json; charset=utf-8', cookies=None):
        data = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store' if '/api/' in self.path or '/login' in self.path else 'no-cache')
        for cookie in cookies or []:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def cookie(self, name):
        try:
            value = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
            return value[name].value if name in value else ''
        except http.cookies.CookieError:
            return ''

    def authenticated(self):
        token = self.cookie('l4d2_session')
        with self.server.session_lock:
            now = time.time()
            self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
            session = self.server.sessions.get(token)
        return session

    def same_origin(self):
        origin = self.headers.get('Origin')
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            return parsed.scheme in {'http', 'https'} and parsed.netloc == self.headers.get('Host')
        return self.headers.get('Sec-Fetch-Site') not in {'cross-site', 'same-site'}

    def csrf(self, session):
        return self.same_origin() and bool(self.headers.get('X-CSRF-Token')) and hmac.compare_digest(
            self.headers.get('X-CSRF-Token', ''), session['csrf'])

    def bridge(self, path, fields=None, **extra):
        return self.server.bridge({'operation': 'http', 'path': path, 'fields': fields or {},
                                   'room': self.headers.get('X-L4D2-Room') or None, **extra}, self.server.config)

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except CliError as exc:
            self.send_data(400 if exc.exit_code in {2, 5} else 409, {'ok': False, 'message': str(exc), 'code': exc.code, 'data': exc.details})
        except (ValueError, OSError):
            self.send_data(500, {'ok': False, 'message': 'Request failed; inspect web logs.'})

    def get(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == '/healthz':
            self.send_data(200, {'service': 'l4d2-cli-web', 'frontend': 'frontend-redesign', 'contract_version': 1})
            return
        if path == '/login':
            body = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>L4D2 Server Manager · Login</title><style>body{font:16px system-ui;background:#0d1119;color:#e6edf7;display:grid;place-items:center;min-height:95vh}"
                    "form{padding:36px;background:#171f2d;border:1px solid #34405a;border-radius:16px;width:300px}label,input,button{display:block;width:100%;box-sizing:border-box;margin:12px 0;padding:10px}"
                    "button{background:#86d38e;border:0;border-radius:6px;cursor:pointer}input{background:#0d1119;color:white;border:1px solid #34405a;border-radius:6px}</style></head>"
                    "<body><form method='post' action='/api/login'><h1>Server Manager</h1><p>Sign in to manage your L4D2 server.</p>"
                    "<label>Username<input name='username' autocomplete='username' required></label>"
                    "<label>Password<input name='password' type='password' autocomplete='current-password' required></label>"
                    "<button type='submit'>Sign in</button></form></body></html>")
            self.send_data(200, body.encode(), 'text/html; charset=utf-8')
            return
        session = self.authenticated()
        if not session:
            if path.startswith('/api/'):
                self.send_data(401, {'ok': False, 'message': 'Sign in required'})
            else:
                self.send_response(303)
                self.send_header('Location', '/login')
                self.end_headers()
            return
        if path.startswith('/api/'):
            if path == '/api/files/download':
                fields = urllib.parse.parse_qs(parsed.query)
                self.download(fields.get('file', [''])[0])
                return
            if path not in {'/api/state', '/api/commands', '/api/catalog/search', '/api/workshop/resolve', '/api/workshop/parts', '/api/export/download'}:
                raise CliError('Unknown API route.', 'invalid_operation', 2)
            fields = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            data = self.bridge(path, fields)
            if 'download' in data:
                self.download(data['download'])
            else:
                self.send_data(200, data)
            return
        relative = path.lstrip('/') or 'index.html'
        target = base.inside(manager.STATIC, relative)
        if not target.is_file() or target.suffix not in {'.html', '.js', '.css', '.svg', '.ico'}:
            self.send_data(404, {'ok': False, 'message': 'Not found'})
            return
        self.send_data(200, target.read_bytes(), mimetypes.guess_type(str(target))[0] or 'application/octet-stream',
                       cookies=['l4d2_csrf=%s; Path=/; SameSite=Strict' % session['csrf']])

    def do_POST(self):
        try:
            self.post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except CliError as exc:
            self.send_data(400 if exc.exit_code in {2, 5} else 409, {'ok': False, 'message': str(exc), 'code': exc.code, 'data': exc.details})
        except (ValueError, UnicodeError, OSError):
            self.send_data(400, {'ok': False, 'message': 'Invalid request'})

    def post(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.same_origin():
            self.send_data(403, {'ok': False, 'message': 'Cross-origin request rejected'})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise CliError('Invalid content length.', 'invalid_arguments', 2) from None
        if length < 0 or length > self.server.config.get('max_upload_bytes', 2 * 1024**3) + 65536:
            raise CliError('Invalid request size.', 'request_too_large', 2)
        if path == '/api/login':
            if length > 4096:
                raise CliError('Login request too large.', 'request_too_large', 2)
            fields = urllib.parse.parse_qs(self.rfile.read(length).decode())
            now = time.time()
            address = self.client_address[0]
            attempts = [t for t in self.server.login_attempts.get(address, []) if t > now - 60]
            self.server.login_attempts[address] = attempts
            if len(attempts) >= 10:
                self.send_data(429, {'ok': False, 'message': 'Wait before trying again'})
                return
            username, password = fields.get('username', [''])[0], fields.get('password', [''])[0]
            if not hmac.compare_digest(username.encode('utf-8'), self.server.config['username'].encode('utf-8')) or not manager.valid_password(password, self.server.private):
                attempts.append(now)
                self.send_data(401, {'ok': False, 'message': 'Invalid username or password'})
                return
            token = secrets.token_urlsafe(32)
            session = {'expires': now + 8 * 3600, 'csrf': secrets.token_urlsafe(32)}
            with self.server.session_lock:
                if len(self.server.sessions) >= 1000:
                    self.server.sessions.clear()
                self.server.sessions[token] = session
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Set-Cookie', 'l4d2_session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800' % token)
            self.send_header('Set-Cookie', 'l4d2_csrf=%s; SameSite=Strict; Path=/' % session['csrf'])
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        session = self.authenticated()
        if not session:
            self.send_data(401, {'ok': False, 'message': 'Sign in required'})
            return
        if not self.csrf(session):
            self.send_data(403, {'ok': False, 'message': 'CSRF validation failed'})
            return
        if path == '/api/logout':
            with self.server.session_lock:
                self.server.sessions.pop(self.cookie('l4d2_session'), None)
            self.send_data(200, {'ok': True}, cookies=['l4d2_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'])
            return
        content_type = self.headers.get('Content-Type', '')
        if content_type.startswith('multipart/form-data'):
            from email.message import Message
            header = Message()
            header['content-type'] = content_type
            boundary = header.get_param('boundary')
            if not boundary:
                raise CliError('Missing multipart boundary.', 'invalid_upload', 2)
            available = shutil.disk_usage(manager.STATE).free
            if length > available - 128 * 1024**2:
                raise CliError('Insufficient upload disk space.', 'insufficient_space', 4)
            fields = multipart(self.rfile, boundary.encode('ascii'), length, manager.STATE / 'uploads', self.server.config['max_upload_bytes'])
            if path == '/api/files':
                self.send_data(200, {'ok': True, **fields})
                return
            data = self.bridge(path, fields)
        else:
            if length > 1024 * 1024:
                raise CliError('Request too large.', 'request_too_large', 2)
            body = self.rfile.read(length)
            if content_type.startswith('application/json'):
                request = json.loads(body)
                if not isinstance(request, dict):
                    raise CliError('Invalid JSON object.', 'invalid_arguments', 2)
                if path != '/api/operation':
                    raise CliError('JSON is only accepted for operations.', 'invalid_operation', 2)
                if set(request) - {'command', 'values', 'room', 'preview', 'confirmation', 'expires', 'background'}:
                    raise CliError('Unknown operation field.', 'invalid_arguments', 2)
                data = self.bridge(path, **request)
            else:
                parsed = urllib.parse.parse_qs(body.decode('utf-8'))
                fields = {k: v if k == 'filename' and len(v) > 1 else v[-1] for k, v in parsed.items()}
                data = self.bridge(path, fields)
        if 'download' in data:
            self.download(data['download'])
        else:
            self.send_data(200, data)

    def download(self, name):
        name = base.filename(name)
        path = base.inside(manager.STATE / 'downloads', name)
        with base.input_stream(path) as source:
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
            self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            shutil.copyfileobj(source, self.wfile, 1024 * 1024)


def serve():
    config = manager.configuration()
    if not config:
        raise CliError('Panel is not installed.', 'web_not_installed', 4)
    private = manager.credentials()
    with PanelServer((config['host'], config['port']), config, private) as server:
        server.serve_forever()
