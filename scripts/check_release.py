"""Check publishable Git files and optionally built distributions; never publish."""
from __future__ import annotations

import argparse
from email.parser import Parser as MetadataParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile

ROOT_FILES = {
    '.gitignore', '.gitattributes', 'MANIFEST.in', 'pyproject.toml', 'README.md',
    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
}
PUBLIC_DIRS = {'.github', 'bin', 'docs', 'scripts', 'src', 'tests'}
PRIVATE_PARTS = {'.git', '.local-docs', '.workbuddy', '.codex', '.agents', '.ssh', '.venv', '.test-tmp',
                 '__pycache__', 'test-results', 'backups', 'serverfiles', 'steamcmd', '.DepotDownloader', 'IsolatedStorage'}
PRIVATE_NAMES = {'config.vdf', 'loginusers.vdf', 'account.config', 'secrets.toml',
                 'id_rsa', 'id_ed25519', 'id_ecdsa'}
PRIVATE_SUFFIXES = {'.vpk', '.deb', '.log', '.key', '.pem', '.pfx', '.p12',
                    '.exe', '.dll', '.so', '.zip', '.gz', '.tar', '.tgz'}
RULES = {
    'github_credential': r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b',
    'private_key': r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----',
    'url_credential': r'https?://[^\s/@]+:[^\s/@]+@',
    'local_user_path': r'(?i)[A-Z]:[\\/]+Users[\\/]+[^\s"\x27]+',
    'personal_ssh_alias': r'\bmy' + r'ubuntu\d*\b',
    'credential_assignment': r'(?i)(?:rcon_password|sv_setsteamaccount|refresh_token|access_token|password|token)\s*[=:]\s*["\x27]?[A-Za-z0-9_+/=-]{16,}',
}


def forbidden_path(name):
    path = PurePosixPath(name)
    return (path.is_absolute() or '..' in path.parts or bool(set(path.parts) & PRIVATE_PARTS)
            or path.name in PRIVATE_NAMES or path.name == '.env' or path.name.startswith('.env.')
            or path.suffix.lower() in PRIVATE_SUFFIXES or path.name.endswith('-password.txt'))


def scan_text(name, text):
    """Return locations and rule names only, never a matching secret value."""
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, expression in RULES.items():
            for match in re.finditer(expression, line):
                # Existing redaction regressions intentionally use conspicuous
                # synthetic values. Real GitHub tokens/keys never get this exception.
                if rule in {'url_credential', 'credential_assignment'} and 'SYNTHETIC_' in match.group():
                    continue
                issues.append({'path': name, 'line': number, 'rule': rule})
    return issues


def public_files(root):
    completed = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others',
                                '--exclude-standard', '-z'], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=True)
    return sorted(set(filter(None, completed.stdout.decode('utf-8').split('\0'))))


def inspect_source(root):
    files = public_files(root)
    issues = []
    for name in files:
        path = root / name
        first = PurePosixPath(name).parts[0]
        if forbidden_path(name) or (first not in PUBLIC_DIRS and name not in ROOT_FILES):
            issues.append({'path': name, 'rule': 'unexpected_publish_path'})
            continue
        if path.is_symlink() or not path.is_file():
            issues.append({'path': name, 'rule': 'non_regular_source'})
            continue
        if path.stat().st_size > 1024 * 1024:
            issues.append({'path': name, 'rule': 'oversized_source_file'})
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            issues.append({'path': name, 'rule': 'non_utf8_source'})
            continue
        issues.extend(scan_text(name, text))
        if path.suffix == '.md':
            if sum(line.strip().startswith('```') for line in text.splitlines()) % 2:
                issues.append({'path': name, 'rule': 'unbalanced_code_fence'})
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if '://' not in target and not target.startswith('#'):
                    if 'test-results' in PurePosixPath(target).parts:
                        issues.append({'path': name, 'rule': 'unpublished_local_link'})
                    elif not (path.parent / target.split('#')[0]).exists():
                        issues.append({'path': name, 'rule': 'broken_local_link'})
    for required in ['LICENSE', 'README.md', 'CONTRIBUTING.md', 'SECURITY.md',
                     'THIRD_PARTY_NOTICES.md', '.github/workflows/ci.yml']:
        if required not in files:
            issues.append({'path': required, 'rule': 'missing_release_file'})
    source = json.loads((root / 'src/l4d2_cli/_vendor/tomli/SOURCE.json').read_text(encoding='utf-8'))
    for record in source['files']:
        path = root / 'src/l4d2_cli/_vendor/tomli' / record['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            issues.append({'path': path.relative_to(root).as_posix(), 'rule': 'vendor_hash_changed'})
    return files, issues


def inspect_distribution(path, root, version):
    problems = []
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            contents = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
        runtime = [name for name in contents if name.startswith('l4d2_cli/')]
        expected = {p.relative_to(root / 'src').as_posix() for p in (root / 'src/l4d2_cli').rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'}}
        if set(runtime) != expected:
            problems.append({'path': path.name, 'rule': 'wheel_runtime_inventory_mismatch'})
        for name in runtime:
            if name in expected and contents[name] != (root / 'src' / name).read_bytes():
                problems.append({'path': name, 'rule': 'wheel_runtime_bytes_mismatch'})
        metadata = [data.decode('utf-8') for name, data in contents.items() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1 or MetadataParser().parsestr(metadata[0]).get_all('Version') != [version]:
            problems.append({'path': path.name, 'rule': 'wheel_version_mismatch'})
        if not any(name.endswith('/LICENSE') for name in contents):
            problems.append({'path': path.name, 'rule': 'wheel_license_missing'})
        if not any(name.endswith('/THIRD_PARTY_NOTICES.md') for name in contents):
            problems.append({'path': path.name, 'rule': 'wheel_notices_missing'})
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            contents = {}
            for entry in archive.getmembers():
                relative = PurePosixPath(entry.name)
                if relative.is_absolute() or '..' in relative.parts or not (entry.isdir() or entry.isfile()):
                    problems.append({'path': entry.name, 'rule': 'unsafe_sdist_entry'})
                if entry.isfile():
                    name = '/'.join(relative.parts[1:])
                    contents[name] = archive.extractfile(entry).read()
        expected = set(ROOT_FILES) | {'bin/l4d2'}
        for directory in ['src/l4d2_cli', 'tests', 'scripts', '.github', 'docs']:
            expected.update(p.relative_to(root).as_posix() for p in (root / directory).rglob('*')
                            if p.is_file() and '__pycache__' not in p.parts
                            and p.suffix not in {'.pyc', '.pyo'}
                            and not forbidden_path(p.relative_to(root).as_posix()))
        for name in sorted(expected):
            if name not in contents or contents[name] != (root / name).read_bytes():
                problems.append({'path': name, 'rule': 'sdist_source_mismatch'})
    else:
        return [{'path': path.name, 'rule': 'unsupported_artifact'}]
    for name, payload in contents.items():
        if forbidden_path(name):
            problems.append({'path': name, 'rule': 'private_artifact_entry'})
        try:
            problems.extend(scan_text(name, payload.decode('utf-8')))
        except UnicodeError:
            problems.append({'path': name, 'rule': 'non_text_artifact_entry'})
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, action='append', default=[])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files, issues = inspect_source(root)
    namespace = {}
    exec((root / 'src/l4d2_cli/__init__.py').read_text(encoding='utf-8'), namespace)
    version = namespace['__version__']
    for artifact in args.artifact:
        issues.extend(inspect_distribution(artifact, root, version))
    report = {'version': version, 'public_files': len(files), 'artifacts': len(args.artifact),
              'passed': not issues, 'findings': issues,
              'scope': 'Heuristic source/artifact checks; manual review remains required.'}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('Release checks: ' + ('PASS' if not issues else 'FAIL'))
        print('Public files: %d; artifacts: %d' % (len(files), len(args.artifact)))
        for issue in issues:
            print('%s:%s %s' % (issue['path'], issue.get('line', '-'), issue['rule']))
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
