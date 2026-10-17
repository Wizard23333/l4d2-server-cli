#!/usr/bin/env bash
# Install the entire native bundle and register l4d2 on PATH.
set -euo pipefail
umask 022

fail() { printf 'Installation failed: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: sudo bash install.sh [--prefix /usr/local] [BUNDLE_DIR]' \
        'Installs the full CLI bundle and registers PREFIX/bin/l4d2.' \
        'BUNDLE_DIR defaults to the directory containing this script.' \
        'A custom --prefix also supports an ordinary-user installation.'
}

prefix=/usr/local
custom_prefix=false
bundle=
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --prefix)
            (($# >= 2)) || fail '--prefix requires an absolute directory.'
            prefix=$2; custom_prefix=true; shift 2 ;;
        -*) fail "Unknown option: $1" ;;
        *) [[ -z "$bundle" ]] || fail 'Supply only one bundle directory.'
            bundle=$1; shift ;;
    esac
done
[[ $(uname -s) == Linux ]] || fail 'Use this installer on Linux.'
if ! "$custom_prefix" && [[ $(id -u) != 0 ]]; then
    fail 'Global installation requires sudo: sudo bash install.sh'
fi
[[ "$prefix" == /* && "$prefix" != / ]] || fail 'Use an absolute prefix other than /.'
prefix=$(realpath -ms -- "$prefix")
[[ "$prefix" != / ]] || fail 'The filesystem root is not an installation prefix.'
bundle=$(cd -- "${bundle:-$(dirname -- "${BASH_SOURCE[0]}")}" && pwd -P)
[[ -f "$bundle/l4d2" && ! -L "$bundle/l4d2" && -x "$bundle/l4d2" &&
   -d "$bundle/_internal" && ! -L "$bundle/_internal" &&
   -f "$bundle/FILES.sha256" && ! -L "$bundle/FILES.sha256" ]] ||
    fail 'Select a complete extracted native bundle (l4d2, _internal/ and FILES.sha256).'

# Reject links escaping the bundle and special files before copying as root.
files=(FILES.sha256)
while IFS= read -r -d '' link; do
    target=$(readlink -- "$link")
    [[ "$target" != /* ]] || fail 'Absolute links are not supported inside a bundle.'
    resolved=$(realpath -e -- "$link") || fail 'The bundle contains a broken link.'
    [[ "$resolved" == "$bundle/"* ]] || fail 'A bundle link escapes its directory.'
    files+=("${link#"$bundle/"}")
done < <(find "$bundle" -type l -print0)
[[ -z $(find "$bundle" ! -type f ! -type d ! -type l -print -quit) ]] ||
    fail 'The bundle contains a special file.'
entry_listed=false
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[0-9a-f]{64}\ \ (.+)$ ]] || fail 'Invalid FILES.sha256 entry.'
    member=${BASH_REMATCH[1]}
    [[ "$member" != /* && "/$member/" != *'/../'* && "$member" != *\\* &&
       -f "$bundle/$member" && ! -L "$bundle/$member" ]] || fail 'Unsafe bundle manifest path.'
    files+=("$member")
    [[ "$member" != l4d2 ]] || entry_listed=true
done < "$bundle/FILES.sha256"
"$entry_listed" || fail 'The CLI executable is missing from FILES.sha256.'
(cd -- "$bundle" && sha256sum --strict --check --status FILES.sha256) ||
    fail 'Bundle checksum verification failed; extract a verified download again.'

bin=$prefix/bin
library=$prefix/lib/l4d2-server-cli
# Never follow an existing installation-directory link or write into a directory
# writable by a different account. Walk ancestors before creating anything.
current=$library
while [[ "$current" != / ]]; do
    [[ ! -L "$current" ]] || fail "Installation directory is a symlink: $current"
    if [[ -e "$current" ]]; then
        [[ -d "$current" ]] || fail "Not a directory: $current"
        owner=$(stat -c '%u' -- "$current")
        [[ "$owner" == 0 || "$owner" == "$(id -u)" ]] || fail "Installation directory belongs to another account: $current"
        mode=$(stat -c '%a' -- "$current")
        # A sticky ancestor such as /tmp is safe for a privately owned test prefix.
        if (( (8#$mode & 0022) && !(8#$mode & 01000) )); then
            fail "Installation directory is writable by other accounts: $current"
        fi
    fi
    current=$(dirname -- "$current")
done
if [[ -e "$bin" || -L "$bin" ]]; then
    [[ -d "$bin" && ! -L "$bin" ]] || fail "Unsafe command directory: $bin"
    owner=$(stat -c '%u' -- "$bin")
    [[ "$owner" == 0 || "$owner" == "$(id -u)" ]] || fail "Command directory belongs to another account: $bin"
    mode=$(stat -c '%a' -- "$bin")
    (( !(8#$mode & 0022) )) || fail "Command directory is writable by other accounts: $bin"
fi
command=$bin/l4d2
if [[ -e "$command" || -L "$command" ]]; then
    [[ -L "$command" ]] || fail "Existing command is not managed by this installer: $command"
    previous=$(readlink -- "$command")
    [[ "$previous" == "$library/"*/l4d2 ]] ||
        fail "Existing command points outside this CLI installation: $command"
fi

mkdir -p -- "$library" "$bin"
stage=$(mktemp -d "$library/.install.XXXXXXXX")
link_stage=
cleanup() {
    [[ -z "$stage" ]] || rm -rf -- "$stage"
    [[ -z "$link_stage" ]] || rm -rf -- "$link_stage"
}
trap cleanup EXIT
(cd -- "$bundle" && cp --parents --no-dereference --no-preserve=ownership -- "${files[@]}" "$stage/")
chmod -R a-s,u+rwX,go+rX,go-w -- "$stage"
(cd -- "$stage" && sha256sum --strict --check --status FILES.sha256) ||
    fail 'Copied bundle did not pass checksum verification.'
timeout 20 "$stage/l4d2" --version
timeout 20 "$stage/l4d2" --help >/dev/null
digest=$(sha256sum "$stage/FILES.sha256")
destination=$library/${digest:0:64}
if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -d "$destination" && ! -L "$destination" ]] || fail 'Existing bundle directory is unsafe.'
    cmp --silent -- "$stage/FILES.sha256" "$destination/FILES.sha256" || fail 'Existing bundle differs.'
    (cd -- "$destination" && sha256sum --strict --check --status FILES.sha256) ||
        fail 'Existing installed bundle is damaged; the command was left unchanged.'
else
    mv -T -- "$stage" "$destination"
    stage=
fi
link_stage=$(mktemp -d "$bin/.l4d2.XXXXXXXX")
ln -s -- "$destination/l4d2" "$link_stage/l4d2"
mv -Tf -- "$link_stage/l4d2" "$command"
printf 'Installed CLI: %s\nCommand: %s\n' "$destination" "$command"
if [[ ":$PATH:" != *":$bin:"* ]]; then
    printf 'Add %s to PATH to run l4d2 by name.\n' "$bin"
fi
printf '%s\n' 'Run: l4d2 --help'
