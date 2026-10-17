"""Publish verified tag artifacts; never replace an existing published asset."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from release_metadata import basename, release_notes, validate_tag


def gh(*arguments):
    result = subprocess.run(["gh", *arguments], stdout=subprocess.PIPE, text=True, check=True)
    return result.stdout.strip()


def verify_assets(release, expected):
    assets = release["assets"]
    if {asset["name"] for asset in assets} != set(expected) or len(assets) != len(expected):
        raise ValueError("Release must contain exactly the executable and its checksum file.")
    for asset in assets:
        if asset.get("state") != "uploaded" or asset.get("digest") != "sha256:" + expected[asset["name"]]:
            raise ValueError("Published asset SHA256 differs; existing assets will not be overwritten.")


def find_release(repository, tag):
    pages = json.loads(gh("api", "--paginate", "--slurp", "repos/" + repository + "/releases?per_page=100"))
    found = [release for page in pages for release in page if release["tag_name"] == tag]
    if len(found) > 1:
        raise ValueError("Multiple releases with the same tag.")
    return found[0] if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", required=True, type=Path)
    args = parser.parse_args()
    validate_tag(args.tag)
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF") != "refs/tags/" + args.tag:
        parser.error("Publishing is allowed only for the matching tag push event.")
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote_commit = json.loads(gh("api", "repos/" + repository + "/commits/" + args.tag))["sha"]
    if commit != remote_commit or commit != os.environ.get("GITHUB_SHA"):
        raise ValueError("Tag moved or checked-out commit differs from the release event.")
    name = basename(args.tag)
    paths = [args.assets / (name + suffix) for suffix in [".tar.gz", ".sha256"]]
    if {path.name for path in args.assets.iterdir()} != {path.name for path in paths}:
        raise ValueError("Unexpected release attachment.")
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    if paths[1].read_text().strip() != expected[paths[0].name] + "  " + paths[0].name:
        raise ValueError("Checksum attachment does not describe this executable.")
    marker = "<!-- l4d2-cli-preview commit=" + commit + " -->"
    release = find_release(repository, args.tag)
    if release and (marker not in (release.get("body") or "") or not release.get("prerelease")):
        raise ValueError("Existing release is not owned by this preview run; it will not be modified.")
    if release and not release["draft"]:
        verify_assets(release, expected)
        print("Preview already published with identical assets: " + release["html_url"])
        return
    if release is None:
        with tempfile.TemporaryDirectory() as temporary:
            notes = Path(temporary) / "notes.md"
            notes.write_text(release_notes(args.tag, repository) + "\n\n" + marker + "\n", encoding="utf-8")
            gh("release", "create", args.tag, "--repo", repository, "--verify-tag", "--draft", "--prerelease",
               "--latest=false", "--target", commit, "--title", args.tag, "--notes-file", str(notes))
        release = find_release(repository, args.tag)
    if not release:
        raise ValueError("Preview draft not found after creation.")
    present = {asset["name"]: asset for asset in release["assets"]}
    if set(present) - set(expected):
        raise ValueError("Unexpected existing draft attachment; refusing to publish it.")
    for path in paths:
        if path.name in present:
            if present[path.name].get("digest") != "sha256:" + expected[path.name]:
                raise ValueError("Draft asset differs; refusing to overwrite it.")
        else:
            gh("release", "upload", args.tag, str(path), "--repo", repository)
    release = find_release(repository, args.tag)
    verify_assets(release, expected)
    gh("release", "edit", args.tag, "--repo", repository, "--draft=false", "--prerelease", "--latest=false")
    release = json.loads(gh("api", "repos/" + repository + "/releases/tags/" + args.tag))
    verify_assets(release, expected)
    if release["draft"] or not release["prerelease"]:
        raise ValueError("Release did not become a public preview.")
    print(release["html_url"])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("Published [" + args.tag + "](" + release["html_url"] + ") as a pre-release.\n")


if __name__ == "__main__":
    main()
