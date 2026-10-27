#!/usr/bin/env bash
# Runs the Spark host role from this checkout, on the Spark, as root (docs/runbooks/spark-host.md):
#   sudo deploy/ansible/run.sh                  apply
#   sudo deploy/ansible/run.sh --check --diff   drift check: changes nothing, reports what differs
# Other arguments go to ansible-playbook. Only a root-owned checkout of a release tag signed with the
# owner's key runs; the allowed key is in /etc/aetherspark/allowed_signers. Ansible itself is pinned
# in uv.lock and comes, like every other download once host mode is on, through the ops proxy.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
die() { echo "run.sh: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
# Every file root's and writable by root only, so no other account can change what root runs:
# not writable by others, nor by a group other than root. A symbolic link's own mode bits mean
# nothing (its owner still counts); uv's empty lock file is writable by all on purpose and holds
# no code.
bad=$(find . -path ./.venv/.lock -prune -o \
    \( ! -user root -o \( ! -type l \( -perm /002 -o \( -perm /020 ! -group root \) \) \) \) -print -quit)
[ -z "$bad" ] || die "the checkout must belong to root and be writable by root only ($bad is $(stat -c '%U:%G %a' "$bad"))"
# The container test has no tags and sets AS_ROLE_UNSIGNED=1; anywhere else the setting is refused
if [ "${AS_ROLE_UNSIGNED:-}" = 1 ]; then
    systemd-detect-virt --container --quiet || die "AS_ROLE_UNSIGNED is for the container test only"
else
    tag=$(git describe --exact-match --tags HEAD 2>/dev/null) || die "HEAD is not at a release tag"
    [ -z "$(git status --porcelain)" ] || die "the checkout has local changes"
    # A signed tag carries its own name: a validly signed tag under another name is refused
    [ "$(git cat-file tag "$tag" 2>/dev/null | sed -n 's/^tag //p')" = "$tag" ] \
        || die "$tag is not a signed release tag of that name"
    git -c gpg.ssh.allowedSignersFile=/etc/aetherspark/allowed_signers verify-tag "$tag" \
        || die "tag $tag is not signed with the owner's key"
fi

export UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never
# Once host mode is on, the internet is only reachable through the ops proxy (just egress-ops up)
if [ -e /etc/aetherspark/host-mode ]; then
    export HTTPS_PROXY=http://172.30.20.2:3128 HTTP_PROXY=http://172.30.20.2:3128
fi
cd deploy/ansible
exec uv run --frozen --only-group host ansible-playbook -i localhost, spark.yml "$@"
