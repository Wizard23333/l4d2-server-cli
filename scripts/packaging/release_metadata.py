"""Canonical version/tag validation and release text for maintainer tooling."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[2]
NUMBER = r"(?:0|[1-9][0-9]*)"
BASE = r"(" + NUMBER + r"\." + NUMBER + r"\." + NUMBER + r")"
TAG = re.compile(r"v" + BASE + r"-(alpha|beta|rc)\.([1-9][0-9]*)")
VERSION = re.compile(BASE + r"(a|b|rc)([1-9][0-9]*)")


def version_for_tag(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Use vMAJOR.MINOR.PATCH-alpha.N, -beta.N or -rc.N; N starts at 1.")
    return match[1] + {"alpha": "a", "beta": "b", "rc": "rc"}[match[2]] + match[3]


def preview_tag(version):
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("Preview package version must be MAJOR.MINOR.PATCH followed by aN, bN or rcN.")
    return "v" + match[1] + "-" + {"a": "alpha", "b": "beta", "rc": "rc"}[match[2]] + "." + match[3]


def project_version(root=ROOT):
    try:
        import tomllib
    except ModuleNotFoundError:
        sys.path.insert(0, str(root / "src"))
        from l4d2_cli._vendor import tomli as tomllib
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tree = ast.parse((root / "src/l4d2_cli/__init__.py").read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if len(values) != 1 or values[0] != metadata["project"]["version"]:
        raise ValueError("pyproject.toml and l4d2_cli.__version__ must match.")
    preview_tag(values[0])
    return values[0]


def validate_tag(tag, root=ROOT):
    version = project_version(root)
    if version_for_tag(tag) != version:
        raise ValueError("Release tag does not match both project version declarations.")
    return version


def basename(tag):
    version_for_tag(tag)
    return "l4d2-server-cli-" + tag + "-linux-x86_64"


def release_notes(tag, repository, root=ROOT):
    version_for_tag(tag)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository name.")
    base = "https://github.com/" + repository
    path = root / "docs/releases" / (tag + ".md")
    if path.exists():
        text = path.read_text(encoding="utf-8")
        def absolute(match):
            label, target = match.groups()
            if "://" in target or target.startswith("#"):
                return match.group()
            return "[" + label + "](" + urljoin(base + "/blob/" + tag + "/docs/releases/", target) + ")"
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", absolute, text)
    name = basename(tag)
    download = base + "/releases/download/" + tag + "/"
    return ("# " + tag + " preview\n\n"
            "Linux x86_64 directory bundle with an embedded Python runtime; requires glibc 2.31+.\n\n"
            "- [Executable package](" + download + name + ".tar.gz)\n"
            "- [SHA256 checksum](" + download + name + ".sha256)\n\n"
            "Download both files, verify with sha256sum -c, and extract the full directory.\n"
            "[Installation and known limitations](" + base + "/blob/" + tag + "/README.md).\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    tag = args.tag or preview_tag(project_version())
    data = {"tag": tag, "version": validate_tag(tag), "basename": basename(tag)}
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in data.items():
                output.write(key + "=" + value + "\n")
    print(json.dumps(data))


if __name__ == "__main__":
    main()
