"""Bounded Workshop dependency graph with explicit incomplete evidence."""
from __future__ import annotations

import re
from urllib.parse import urlencode

from ..common import CliError
from . import catalog, content, transport

MAX_ITEMS = 128


def workshop_id(value):
    value = str(value)
    if not re.fullmatch(r'[1-9][0-9]{3,19}', value):
        raise CliError('Workshop ID 应为数字条目 ID。', 'invalid_catalog_id', 2)
    return value


def details(item_id):
    """Never interpret an unavailable dependency endpoint as an empty graph."""
    if catalog.STEAM_WEB_API_KEY:
        url = catalog.STEAM_GETDETAILS_URL + '?' + urlencode({
            'key': catalog.STEAM_WEB_API_KEY, 'publishedfileids[0]': item_id,
            'includechildren': 'true', 'return_children': 'true'})
        payload = catalog.http_json(url)
    else:
        payload = catalog.http_json(catalog.STEAM_DETAILS_URL, {'itemcount': '1', 'publishedfileids[0]': item_id})
    values = payload.get('response', {}).get('publishedfiledetails') or []
    if not values or str(values[0].get('result')) != '1':
        raise CliError('条目不可用或已隐藏。', 'catalog_unavailable', 4)
    item = values[0]
    if str(item.get('consumer_app_id', item.get('consumer_appid'))) != '550':
        raise CliError('条目不属于求生之路 2。', 'wrong_workshop_game', 4)
    children, verified = [], bool(catalog.STEAM_WEB_API_KEY)
    collection = str(item.get('file_type')) == '2'
    if collection:
        response = catalog.http_json(catalog.STEAM_COLLECTION_URL,
                                     {'collectioncount': '1', 'publishedfileids[0]': item_id})
        values = response.get('response', {}).get('collectiondetails') or []
        if not values or str(values[0].get('result')) != '1':
            raise CliError('合集子条目无法读取。', 'collection_unavailable', 4)
        children = values[0].get('children') or []
        verified = True
    else:
        children = item.get('children') or []
        if int(item.get('num_children') or 0) > len(children):
            verified = False
    ids = [workshop_id(c['publishedfileid']) for c in children]
    return {'title': str(item.get('title') or item_id), 'children': list(dict.fromkeys(ids)),
            'collection': collection, 'dependencies_verified': verified,
            'updated': item.get('time_updated'),
            'references': list(dict.fromkeys(re.findall(r'(?:filedetails/\?id=|[?&]id=)([0-9]{4,20})',
                                                        item.get('description') or item.get('file_description') or '')))[:16]}


def share(room, item_id=None, filenames=None, offline=False):
    if item_id and filenames:
        raise CliError('请选择 Workshop 条目/合集，或本地文件列表。', 'invalid_arguments', 2)
    inventory = content.inventory(room)
    selected = [p for p in inventory['packages'] if p['state'] == 'enabled' and
                (p['filename'] in filenames if filenames else p['kind'] == 'map')]
    if filenames and set(filenames) - {p['filename'] for p in selected}:
        raise CliError('选中的包不存在或未启用。', 'package_not_found', 2)
    roots, local, notes = [], [], []
    if item_id:
        roots.append(workshop_id(item_id))
    for item in selected if not item_id else []:
        if item.get('source') == 'workshop':
            roots.extend(workshop_id(v) for v in [item.get('id'), *item.get('install_ids', [])] if v)
        else:
            local.append({'filename': item['filename'], 'sha256': item.get('sha256'),
                          'source': item.get('source', 'unknown'),
                          'url': ('https://www.gamemaps.com/details/' + str(item['id']))
                          if item.get('source') == 'gamemaps' and str(item.get('id', '')).isdigit() else None})
    roots = list(dict.fromkeys(roots))
    pending, seen, items, edges = list(roots), set(), [], []
    if not offline:
        catalog.initialize()
    with transport.budget(120):
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            if len(seen) >= MAX_ITEMS:
                notes.append('依赖超过 128 项，已停止展开；清单不完整。')
                break
            seen.add(current)
            item = {'id': current, 'url': catalog.STEAM_WORKSHOP_URL.format(id=current),
                    'title': current, 'dependencies_verified': False, 'collection': False}
            if not offline:
                try:
                    item.update(details(current))
                    for child in item.pop('children'):
                        edges.append({'from': current, 'to': child})
                        if child not in seen and child not in pending:
                            pending.append(child)
                except (CliError, OSError, ValueError, KeyError, TypeError, IndexError):
                    notes.append('无法完整核对条目 ' + current + '，请打开其 Workshop 页面。')
            items.append(item)
    if any(not i['dependencies_verified'] for i in items):
        notes.append('依赖未全部验证；配置 Steam Web API key 后重新生成，可递归核对必需条目。')
    if local:
        notes.append('存在本地/GameMaps 包，需服主提供同版本文件或原始下载页，并人工核对依赖。')
    if inventory['errors']:
        notes.append('本地资源有读取错误；不能确认已安装包清单完整。')
    complete = not notes and not offline
    lines = ['求生之路 2 地图分享清单', '核对结果：' + ('已展开来源声明的全部依赖' if complete else '仍有待核对项'),
             '所有玩家订阅下面全部非合集条目，等待下载完成；不要同时安装其他版本。']
    for i in items:
        title = re.sub(r'[\x00-\x1f\x7f]', ' ', i['title'])[:200]
        lines.append(('[合集] ' if i['collection'] else '[订阅] ') + title + '\n' + i['url'])
        for ref in i.get('references', []):
            if ref not in seen:
                lines.append('作者描述中的参考链接（未确认必需）：' + catalog.STEAM_WORKSHOP_URL.format(id=ref))
    for item in local:
        lines.append('[本地文件] ' + item['filename'] + ('\n' + item['url'] if item['url'] else ''))
        if item['sha256']:
            lines.append('SHA256：' + item['sha256'])
    if not items and not local:
        lines.append('未发现启用的三方地图包；默认仅列地图，也可用 --filename 选择 Mod。')
    lines += ['注意：' + note for note in notes]
    lines.append('此清单核对来源声明，不能证明作者未漏写依赖，也不能证明客户端已经下载。')
    return {'room': room['id'], 'items': items, 'dependencies': edges, 'local_files': local,
            'complete': complete, 'warnings': notes, 'share_text': '\n'.join(lines)}
