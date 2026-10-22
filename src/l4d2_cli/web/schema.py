"""Derive browser forms from the public parser, keeping one command contract."""
from __future__ import annotations

import argparse

from ..common import CliError

# Interactive authentication and bootstrap are terminal workflows. Everything in
# the registered server's operations contract is available in the browser.
GROUPS = {'room', 'map', 'addon', 'plugin', 'admin', 'rcon', 'task', 'transfer',
          'backup', 'storage', 'repair', 'network', 'capabilities', 'server', 'doctor'}
HIDDEN = {'_run', '_bridge', '_serve', 'install'}
CONTROL = {'help', 'json', 'yes', 'dry_run'}


def commands():
    from ..cli import parser
    found = []

    def visit(current, prefix):
        children = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
        if children:
            for name, child in children.choices.items():
                if name.startswith('_') or (not prefix and name not in GROUPS):
                    continue
                if prefix == ['server'] and name == 'install':
                    continue
                visit(child, prefix + [name])
            return
        fields = []
        for action in current._actions:
            if action.dest in CONTROL or action.dest in {'follow', 'arguments'}:
                continue
            flag = next((s for s in action.option_strings if s.startswith('--')), None)
            boolean = isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))
            name = flag[2:].replace('-', '_') if boolean and flag else action.dest
            fields.append({'name': name, 'flag': flag,
                           'required': action.required, 'choices': list(action.choices) if action.choices else None,
                           'boolean': boolean,
                           'repeat': isinstance(action, argparse._AppendAction),
                           'default': action.default if isinstance(action.default, (str, int, float, bool)) else None,
                           'help': action.help or '', 'file': action.dest in FILE_FIELDS})
        # Task submission has a dedicated validated command form/endpoint.
        if prefix == ['task', 'submit']:
            return
        has_yes = any(a.dest == 'yes' for a in current._actions)
        has_preview = any(a.dest == 'dry_run' for a in current._actions)
        found.append({'id': ' '.join(prefix), 'command': prefix, 'fields': fields,
                      'mutating': has_yes or has_preview, 'confirmation_flag': has_yes,
                      'background': has_yes and prefix[0] != 'task', 'preview': has_preview})
    visit(parser(), [])
    return found


FILE_FIELDS = {'file', 'rcon_password_file', 'proxy_file', 'steam_api_key_file',
               'hosts_file', 'command_file', 'metamod_archive', 'sourcemod_archive'}


def arguments(command_id, values, room, preview=False):
    spec = next((s for s in commands() if s['id'] == command_id), None)
    if not spec or not isinstance(values, dict):
        raise CliError('Unknown browser operation.', 'invalid_operation', 2)
    allowed = {field['name'] for field in spec['fields']}
    if set(values) - allowed:
        raise CliError('Unknown browser field.', 'invalid_arguments', 2)
    argv = list(spec['command'])
    for field in spec['fields']:
        value = values.get(field['name'])
        if field['name'] in {'room', 'from_room'}:
            value = value or room
        if value is None or value == '':
            continue
        if field['boolean']:
            if value is True or value == 'true':
                argv.append(field['flag'])
            continue
        items = value if field['repeat'] and isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, (str, int, float)) or isinstance(item, bool):
                raise CliError('Invalid field value.', 'invalid_arguments', 2)
            if field['flag']:
                argv.append(field['flag'])
            argv.append(str(item))
    if spec['mutating'] and preview and not spec['preview']:
        raise CliError('This operation has no preview.', 'invalid_operation', 2)
    if spec['mutating']:
        if preview:
            argv.append('--dry-run')
        elif spec['confirmation_flag']:
            argv.append('--yes')
    argv.append('--json')
    return argv, spec
