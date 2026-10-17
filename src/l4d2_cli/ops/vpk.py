"""Bounded VPK v1/v2 reading, CRC verification and restricted extraction."""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

from ..common import CliError
from .base import inside, regular

ALLOWED = {'maps', 'missions', 'materials', 'models', 'sound', 'scripts', 'particles', 'resource'}


def is_archive_part(path):
    import re
    path = Path(path)
    return bool(re.search(r'_[0-9]{3}$', path.stem) and path.with_name(path.stem[:-4] + '_dir.vpk').is_file())


def entries(path):
    path = regular(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise CliError('Truncated VPK header.', 'invalid_vpk', 2)
        signature, version, tree_size = struct.unpack('<III', header)
        header_size = {1: 12, 2: 28}.get(version)
        if signature != 0x55AA1234 or not header_size or tree_size > 64 * 1024**2:
            raise CliError('Unsupported VPK header.', 'invalid_vpk', 2)
        if header_size + tree_size > size:
            raise CliError('Truncated VPK tree.', 'invalid_vpk', 2)
        stream.seek(header_size)
        tree = stream.read(tree_size)
    offset, result = 0, []

    def string():
        nonlocal offset
        end = tree.find(b'\0', offset)
        if end < 0 or end - offset > 4096:
            raise CliError('Invalid VPK tree string.', 'invalid_vpk', 2)
        value = tree[offset:end].decode('utf-8', errors='strict')
        offset = end + 1
        return value

    while True:
        extension = string()
        if not extension:
            break
        while True:
            directory = string()
            if not directory:
                break
            while True:
                name = string()
                if not name:
                    break
                if offset + 18 > len(tree):
                    raise CliError('Truncated VPK entry.', 'invalid_vpk', 2)
                crc, preload, archive, start, length, terminator = struct.unpack_from('<IHHIIH', tree, offset)
                offset += 18
                if terminator != 0xFFFF or offset + preload > len(tree):
                    raise CliError('Invalid VPK entry.', 'invalid_vpk', 2)
                relative = ('' if directory in {' ', '.'} else directory + '/') + name + '.' + extension
                # Validate even skipped paths: a hostile tree must never influence extraction.
                inside(Path('/vpk-root'), relative)
                source = path
                if archive != 0x7FFF:
                    if not path.stem.endswith('_dir'):
                        raise CliError('External VPK archive requires a _dir.vpk index.', 'invalid_vpk', 2)
                    source = regular(path.with_name(path.stem[:-4] + '_%03d.vpk' % archive))
                start += header_size + tree_size if archive == 0x7FFF else 0
                if start + length > source.stat().st_size:
                    raise CliError('Truncated VPK payload.', 'invalid_vpk', 2)
                result.append({'name': relative, 'source': source, 'offset': start, 'size': length,
                               'preload': tree[offset:offset + preload], 'crc': crc})
                offset += preload
                if len(result) > 500000:
                    raise CliError('Too many VPK entries.', 'invalid_vpk', 2)
    return result


def copy_entry(entry, output=None):
    crc = zlib.crc32(entry['preload'])
    if output:
        output.write(entry['preload'])
    with entry['source'].open('rb') as stream:
        stream.seek(entry['offset'])
        remaining = entry['size']
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise CliError('Truncated VPK payload.', 'invalid_vpk', 2)
            remaining -= len(block)
            crc = zlib.crc32(block, crc)
            if output:
                output.write(block)
    if crc & 0xFFFFFFFF != entry['crc']:
        raise CliError('VPK CRC mismatch: ' + entry['name'], 'invalid_vpk', 2)


def verify(path):
    items = entries(path)
    for entry in items:
        copy_entry(entry)
    return {'entries': len(items), 'bytes': Path(path).stat().st_size}


def extract(path, destination):
    items = entries(path)
    selected = [item for item in items if item['name'].split('/')[0].lower() in ALLOWED]
    required = sum(item['size'] + len(item['preload']) for item in selected)
    import shutil
    if required > shutil.disk_usage(Path(destination).parent).free:
        raise CliError('Insufficient space for VPK extraction.', 'insufficient_space', 4)
    files = []
    for entry in selected:
        target = inside(destination, entry['name'])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            copy_entry(entry, output)
        files.append(entry['name'])
    return files


def text(entry, limit=1024 * 1024):
    import io
    if entry['size'] + len(entry['preload']) > limit:
        raise CliError('Mission text is too large.', 'invalid_mission', 2)
    output = io.BytesIO()
    copy_entry(entry, output)
    return output.getvalue().decode('utf-8-sig', errors='replace')
