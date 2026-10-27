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
[ "$(stat -c %u .)" = 0 ] || die "the checkout must belong to root, so no other account can change what root runs"
# The container test has no tags; it sets AS_ROLE_UNSIGNED=1
if [ "${AS_ROLE_UNSIGNED:-}" != 1 ]; then
    tag=$(git describe --exact-match --tags HEAD 2>/dev/null) || die "HEAD is not at a release tag"
    [ -z "$(git status --porcelain)" ] || die "the checkout has local changes"
    git -c gpg.ssh.allowedSignersFile=/etc/aetherspark/allowed_signers verify-tag "$tag" \
        || die "tag $tag is not signed with the owner's key"
fi

export UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never
# Once host mode is on, the internet is only reachable through the ops proxy (just egress-ops up)
if [ -e /etc/aetherspark/host-mode ]; then
    export HTTPS_PROXY=http://172.30.20.2:3128 HTTP_PROXY=http://172.30.20.2:3128
fi
cd deploy/ansible
exec uv run --frozen --only-group host ansible-playbook spark.yml "$@"
