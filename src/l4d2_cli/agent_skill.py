"""Optional, local-only Agent Skill distribution; no server or network required."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from . import __version__
from .common import CliError, confirm

NAME = 'l4d2-manager'
RESOURCE = Path(__file__).parent / 'skills' / NAME / 'SKILL.md'
OWNER = 'l4d2-server-cli'
MAX_FILE = 4 * 1024 * 1024


@dataclass(frozen=True)
class Adapter:
    label: str
    project: str
    user: str | None
    rule: bool = False


ADAPTERS = {
    'codex': Adapter('Codex', '.agents/skills', '.agents/skills'),
    'claude-code': Adapter('Claude Code', '.claude/skills', '.claude/skills'),
    'cursor': Adapter('Cursor', '.cursor/skills', '.cursor/skills'),
    'windsurf': Adapter('Windsurf Cascade', '.windsurf/skills', '.codeium/windsurf/skills'),
    'cline': Adapter('Cline', '.cline/skills', '.cline/skills'),
    'copilot': Adapter('GitHub Copilot', '.github/skills', '.copilot/skills'),
    'continue': Adapter('Continue (project rule)', '.continue/rules', None, True),
    'generic': Adapter('Agent Skills compatible tools', '.agents/skills', '.agents/skills'),
}


def bundled() -> bytes:
    try:
        return RESOURCE.read_bytes()
    except OSError as exc:
        raise CliError('Bundled SKILL.md is missing; reinstall a complete CLI package.', 'skill_resource_missing', 4) from exc


def rendered(adapter: Adapter) -> bytes:
    content = bundled()
    if adapter.rule:
        # Continue accepts Markdown rules, with relevance-based application.
        text = content.decode('utf-8')
        header, body = text.split('\n---\n', 1)
        return (header + '\nalwaysApply: false\n---\n' + body).encode('utf-8')
    return content


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def checked(path: Path) -> Path:
    """Keep the lexical path: resolving first would hide links and junctions."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise CliError('Refusing a symlink or junction in the skill target: ' + str(item), 'unsafe_skill_path', 4)
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise CliError('Skill target parent is not a directory: ' + str(item), 'unsafe_skill_path', 4)
    return path


def read_file(path: Path) -> bytes | None:
    checked(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise CliError('Skill target must be a regular file smaller than 4 MiB: ' + str(path), 'skill_conflict', 4)
    return path.read_bytes()


def destination(agent: str, scope=None, project=None, target_dir=None) -> tuple[Path, Path, str]:
    if agent not in ADAPTERS:
        raise CliError('Unknown agent.', 'invalid_arguments', 2)
    adapter = ADAPTERS[agent]
    scope = scope or ('project' if project else 'user')
    if scope not in {'user', 'project'} or (project and scope != 'project'):
        raise CliError('--project requires --scope project.', 'invalid_arguments', 2)
    if scope == 'user' and adapter.user is None:
        raise CliError('This adapter supports project scope only; use --scope project.', 'unsupported_skill_scope', 2)
    if scope == 'project':
        root = checked(Path(project) if project else Path.cwd())
        if not root.is_dir():
            raise CliError('Project directory must already exist.', 'invalid_skill_project', 2)
    else:
        root = checked(Path.home())
    base = checked(Path(target_dir)) if target_dir else checked(root / (adapter.project if scope == 'project' else adapter.user))
    path = checked(base / (NAME + '.md')) if adapter.rule else checked(base / NAME / 'SKILL.md')
    receipt = path.with_name('.l4d2-manager.install.json')
    return path, receipt, scope


def inspect(path: Path, receipt: Path, expected: bytes) -> dict:
    content, raw = read_file(path), read_file(receipt)
    record = None
    if raw is not None:
        try:
            record = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            pass
        if (not isinstance(record, dict) or record.get('schema_version') != 1 or
                record.get('managed_by') != OWNER or record.get('skill_name') != NAME or
                not isinstance(record.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise CliError('Installation receipt is invalid; preserve it and inspect the target manually.', 'invalid_skill_receipt', 4)
    if content is None:
        state = 'missing' if record else 'not_installed'
    elif record is None:
        state = 'unmanaged'
    elif digest(content) != record['sha256']:
        state = 'modified'
    else:
        state = 'current' if content == expected else 'outdated'
    return {'path': str(path), 'receipt': str(receipt), 'status': state,
            'managed': record is not None, 'matches_bundled': content == expected,
            'installed_cli_version': record.get('cli_version') if record else None,
            'bundled_cli_version': __version__, 'bundled_sha256': digest(expected)}


def status(agent, scope=None, project=None, target_dir=None):
    path, receipt, selected_scope = destination(agent, scope, project, target_dir)
    return {'agent': agent, 'scope': selected_scope, **inspect(path, receipt, rendered(ADAPTERS[agent]))}


def list_agents(scope=None, project=None):
    selected_scope = scope or ('project' if project else 'user')
    if selected_scope not in {'user', 'project'} or (project and selected_scope != 'project'):
        raise CliError('--project requires --scope project.', 'invalid_arguments', 2)
    entries = []
    for agent, adapter in ADAPTERS.items():
        entry = {'agent': agent, 'label': adapter.label, 'format': 'rule' if adapter.rule else 'skill',
                 'scopes': ['project', 'user'] if adapter.user else ['project']}
        if selected_scope == 'user' and adapter.user is None:
            entry['status'] = 'unsupported_scope'
        else:
            try:
                entry.update(status(agent, selected_scope, project))
            except CliError as exc:
                entry.update(status='conflict', error={'code': exc.code, 'message': str(exc)})
        entries.append(entry)
    return {'skill': NAME, 'scope': selected_scope, 'agents': entries, 'server_required': False}


def atomic_write(path: Path, content: bytes):
    checked(path)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        checked(path)
        os.replace(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def target_lock(path: Path):
    checked(path)
    lock = path.with_name('.' + path.name + '.install.lock')
    try:
        descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CliError('Another skill operation holds the target lock: ' + str(lock), 'skill_locked', 4) from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def preserve(path: Path, content: bytes) -> str:
    backup = path.with_name(path.name + '.bak-' + uuid.uuid4().hex)
    descriptor = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)
    return str(backup)


def install(agent, scope=None, project=None, target_dir=None, *, update=False, force=False, dry_run=False, yes=False):
    path, receipt, selected_scope = destination(agent, scope, project, target_dir)
    expected = rendered(ADAPTERS[agent])

    def plan():
        report = inspect(path, receipt, expected)
        if update and not report['managed']:
            raise CliError('Update requires a managed installation; use skill install first.', 'skill_not_managed', 4)
        if report['status'] in {'modified', 'unmanaged'} and not report['matches_bundled'] and not force:
            raise CliError('Existing skill differs; use --force to preserve a backup and replace this file.', 'skill_conflict', 4, report)
        return report

    report = plan()
    report.update(agent=agent, scope=selected_scope, action='update' if update else 'install',
                  backup_required=report['status'] in {'modified', 'unmanaged'} and not report['matches_bundled'])
    if dry_run:
        return {'dry_run': True, 'plan': report}
    if report['status'] == 'current':
        return {'unchanged': True, **report}
    confirm('Install the local ' + agent + ' skill at ' + str(path) + '; no server changes.', yes)
    path.parent.mkdir(parents=True, exist_ok=True)
    checked(path)
    backups = []
    with target_lock(path):
        latest = plan()
        old_content, old_receipt = read_file(path), read_file(receipt)
        if latest['status'] in {'modified', 'unmanaged'} and old_content != expected:
            backups.append(preserve(path, old_content))
        record = {'schema_version': 1, 'managed_by': OWNER, 'skill_name': NAME,
                  'sha256': digest(expected), 'cli_version': __version__}
        try:
            atomic_write(path, expected)
            atomic_write(receipt, (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('utf-8'))
        except BaseException:
            if old_content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old_content)
            if old_receipt is None:
                receipt.unlink(missing_ok=True)
            else:
                atomic_write(receipt, old_receipt)
            raise
    return {'installed': True, 'backups': backups, **status(agent, selected_scope, project, target_dir)}


def uninstall(agent, scope=None, project=None, target_dir=None, *, force=False, dry_run=False, yes=False):
    path, receipt, selected_scope = destination(agent, scope, project, target_dir)
    expected = rendered(ADAPTERS[agent])

    def plan():
        report = inspect(path, receipt, expected)
        if report['status'] == 'unmanaged':
            raise CliError('Refusing to uninstall a file without a valid CLI installation receipt.', 'skill_not_managed', 4)
        if report['status'] == 'modified' and not force:
            raise CliError('Installed skill was edited; --force preserves a backup before uninstalling.', 'skill_conflict', 4, report)
        return report

    report = plan()
    report.update(agent=agent, scope=selected_scope, action='uninstall', backup_required=report['status'] == 'modified')
    if dry_run:
        return {'dry_run': True, 'plan': report}
    if report['status'] == 'not_installed':
        return {'unchanged': True, **report}
    confirm('Remove only the CLI-managed skill and receipt at ' + str(path) + '.', yes)
    backups = []
    with target_lock(path):
        latest = plan()
        content = read_file(path)
        if latest['status'] == 'modified':
            backups.append(preserve(path, content))
        path.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
    # Do not recursively remove skill directories: preserve backups and user files.
    return {'uninstalled': True, 'path': str(path), 'backups': backups}


def export(output, *, force=False, dry_run=False, yes=False):
    path = checked(Path(output))
    expected = bundled()
    previous = read_file(path)
    if previous is not None and previous != expected and not force:
        raise CliError('Export output exists; choose another path or use --force to back it up.', 'skill_conflict', 4)
    plan = {'path': str(path), 'sha256': digest(expected), 'backup_required': previous is not None and previous != expected}
    if dry_run:
        return {'dry_run': True, 'plan': plan}
    if previous == expected:
        return {'unchanged': True, **plan}
    confirm('Export the standalone SKILL.md to ' + str(path) + '.', yes)
    path.parent.mkdir(parents=True, exist_ok=True)
    checked(path)
    backups = []
    with target_lock(path):
        previous = read_file(path)
        if previous is not None and previous != expected:
            if not force:
                raise CliError('Export output changed; inspect before retrying.', 'skill_conflict', 4)
            backups.append(preserve(path, previous))
        atomic_write(path, expected)
    return {'exported': True, 'backups': backups, **plan}


def register(groups):
    from .cli import output_flags
    sub = groups.add_parser('skill', help='Optional local Agent Skill installation; no game or Linux required').add_subparsers(dest='action', required=True)
    output_flags(sub.add_parser('show', help='Print the bundled standalone SKILL.md'))
    exported = sub.add_parser('export', help='Export the standalone SKILL.md to a file')
    exported.add_argument('--output', required=True, metavar='FILE')
    exported.add_argument('--force', action='store_true', help='Back up conflicting content before replacement')
    output_flags(exported, changes=True, confirmation=True)
    for action in ('list', 'status', 'install', 'update', 'uninstall'):
        item = sub.add_parser(action)
        if action != 'list':
            item.add_argument('--agent', required=True, choices=tuple(ADAPTERS))
            item.add_argument('--target-dir', help='Override the skills/rules root; the managed filename is appended')
        item.add_argument('--scope', choices=('user', 'project'), help='Default user; --project implies project scope')
        item.add_argument('--project', metavar='DIR', help='Existing project directory (defaults to current directory for project scope)')
        mutation = action in {'install', 'update', 'uninstall'}
        if mutation:
            item.add_argument('--force', action='store_true', help='Preserve edited/conflicting content in a backup before replacement/removal')
        output_flags(item, changes=mutation, confirmation=mutation)


def dispatch(args):
    if args.action == 'show':
        return {'name': NAME, 'sha256': digest(bundled()), 'content': bundled().decode('utf-8'), 'server_required': False}
    if args.action == 'export':
        return export(args.output, force=args.force, dry_run=args.dry_run, yes=args.yes)
    if args.action == 'list':
        return list_agents(args.scope, args.project)
    options = dict(agent=args.agent, scope=args.scope, project=args.project, target_dir=args.target_dir)
    if args.action == 'status':
        return status(**options)
    options.update(force=args.force, dry_run=args.dry_run, yes=args.yes)
    if args.action == 'uninstall':
        return uninstall(**options)
    return install(**options, update=args.action == 'update')
