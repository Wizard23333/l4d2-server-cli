"""Bounded content transport with explicit source domains and private proxy settings."""
from __future__ import annotations

import json
import contextlib
import contextvars
import http.client
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..common import CliError, REDACTOR, progress
from . import base

CONFIG = Path('/etc/l4d2-cli/network.json')
DEADLINE = contextvars.ContextVar('l4d2_content_deadline', default=None)
DOMAINS = ('steampowered.com', 'steamcommunity.com', 'steamusercontent.com', 'steamstatic.com',
           'akamaihd.net', 'gamemaps.com', 'alliedmods.net', 'github.com', 'githubusercontent.com')
MAX_DOWNLOAD_BYTES = 32 * 1024**3


@contextlib.contextmanager
def budget(seconds):
    old = DEADLINE.get()
    token = DEADLINE.set(min(old or float('inf'), time.monotonic() + seconds))
    try:
        yield
    finally:
        DEADLINE.reset(token)


def remaining(seconds):
    deadline = DEADLINE.get()
    value = min(seconds, deadline - time.monotonic()) if deadline else seconds
    if value <= 0:
        raise CliError('Operation time budget exhausted.', 'timeout', 124)
    return value


def settings():
    values = base.read(CONFIG)
    for key in ('proxy', 'steam_web_api_key'):
        REDACTOR.register(values.get(key, ''))
    return values


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or
            parsed.port not in {None, 80, 443} or
            not any(host == domain or host.endswith('.' + domain) for domain in DOMAINS)):
        raise CliError('Download URL is outside supported source domains.', 'unsafe_url', 4)
    return parsed


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def opener(download=False):
    config = settings()
    proxy = '' if download and config.get('direct_downloads', False) else config.get('proxy', '')
    return urllib.request.build_opener(Redirects(), urllib.request.ProxyHandler(
        {'http': proxy, 'https': proxy} if proxy else {}))


def fetch_bytes(url, data=None, timeout=30, limit=8 * 1024**2):
    validate_url(url)
    request = urllib.request.Request(url, data=data, headers={'User-Agent': 'L4D2ServerCLI/0.1'})
    try:
        with opener().open(request, timeout=remaining(timeout)) as response:
            result = response.read(limit + 1)
            if len(result) > limit:
                raise CliError('Source response exceeds size limit.', 'response_too_large', 4)
            return result
    except (OSError, urllib.error.HTTPError, http.client.HTTPException) as exc:
        raise CliError('Content request failed: ' + type(exc).__name__, 'download_failed', 4) from None


def download(url, destination, timeout=600, expected=0):
    validate_url(url)
    destination = Path(destination)
    base.storage.reject_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    base.storage.reject_links(partial)
    deadline = min(DEADLINE.get() or float('inf'), time.monotonic() + timeout)
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {'User-Agent': 'L4D2ServerCLI/0.1'}
        if offset:
            headers['Range'] = 'bytes=%d-' % offset
        try:
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise CliError('Download timed out.', 'timeout', 124)
            with opener(download=True).open(urllib.request.Request(url, headers=headers), timeout=min(30, budget)) as response:
                resume = offset and response.status == 206 and response.headers.get('Content-Range', '').startswith('bytes %d-' % offset)
                if not resume:
                    offset = 0
                header = response.headers.get('Content-Length')
                try:
                    length = int(header) if header is not None else None
                except ValueError:
                    raise CliError('Invalid download size.', 'invalid_download', 4) from None
                total = expected or (length + offset if length is not None else 0)
                if total < 0 or total > MAX_DOWNLOAD_BYTES or (length is not None and length < 0):
                    raise CliError('Invalid download size.', 'invalid_download', 4)
                import shutil
                if total - offset > shutil.disk_usage(destination.parent).free:
                    raise CliError('Insufficient download space.', 'insufficient_space', 4)
                with partial.open('ab' if resume else 'wb') as output:
                    while True:
                        if time.monotonic() >= deadline:
                            raise CliError('Download timed out.', 'timeout', 124)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        offset += len(block)
                        if offset > MAX_DOWNLOAD_BYTES or (total and offset > total):
                            raise CliError('Download exceeds expected size.', 'invalid_download', 4)
                        if not total and len(block) > shutil.disk_usage(destination.parent).free:
                            raise CliError('Insufficient download space.', 'insufficient_space', 4)
                        output.write(block)
                        progress('download bytes=%d total=%d' % (offset, total))
            if not offset:
                raise CliError('Downloaded file is empty.', 'invalid_download', 4)
            if total and partial.stat().st_size != total:
                raise OSError('incomplete download')
            partial.replace(destination)
            return destination
        except (OSError, urllib.error.HTTPError, http.client.HTTPException):
            if attempt == 2 or time.monotonic() >= deadline:
                raise CliError('Download failed after bounded retries; partial content retained.', 'download_failed', 4) from None
    raise CliError('Download failed.', 'download_failed', 4)
