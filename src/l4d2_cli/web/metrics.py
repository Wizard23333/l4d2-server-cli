"""Read-only host metrics for the optional dashboard."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def snapshot(registered):
    mem = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            mem[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    def usage(total, available):
        return {'total': total, 'used': total - available, 'available': available,
                'free': available, 'percent': round((total - available) * 100 / total, 1) if total else 0}
    disks = []
    for directory in sorted({r['game_dir'] for r in registered}):
        info = shutil.disk_usage(directory)
        disks.append({'id': directory, 'label': 'Game filesystem', 'path': directory,
                      **usage(info.total, info.free)})
    try:
        seconds = int(float(Path('/proc/uptime').read_text().split()[0]))
    except (OSError, ValueError):
        seconds = 0
    load = list(os.getloadavg()) if hasattr(os, 'getloadavg') else []
    percent = None
    try:
        def sample():
            values = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
            return sum(values), values[3] + values[4]
        before, idle_before = sample()
        time.sleep(0.1)
        after, idle_after = sample()
        if after > before:
            percent = round(100 * (1 - (idle_after - idle_before) / (after - before)), 1)
    except (OSError, ValueError, IndexError):
        pass
    processes = []
    from ..common import CliError, run
    for room in registered:
        try:
            response = run(['systemctl', 'show', room['service'], '--no-pager',
                            '--property=ActiveState,SubState,MemoryCurrent,CPUUsageNSec'], check=False)
            values = dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)
        except CliError:
            values = {}
        def number(value):
            return int(value) if value and value.isdigit() else None
        processes.append({'service': room['service'], 'label': room.get('name', room['id']),
                          'active': values.get('ActiveState', 'unknown'), 'sub_state': values.get('SubState', ''),
                          'memory_current': number(values.get('MemoryCurrent')), 'cpu_usage_nsec': number(values.get('CPUUsageNSec'))})
    return {'cpu': {'cores': os.cpu_count(), 'load_average': load, 'percent': percent},
            'memory': usage(mem.get('MemTotal', 0), mem.get('MemAvailable', 0)),
            'swap': usage(mem.get('SwapTotal', 0), mem.get('SwapFree', 0)), 'disk': disks,
            'uptime': {'seconds': seconds, 'display': '%dd %dh %dm' % (seconds // 86400, seconds // 3600 % 24, seconds // 60 % 60)},
            'processes': processes}
