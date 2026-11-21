#!/usr/bin/env bash
# `just taxonomies` (data-plane.md, section 4; docs/runbooks/taxonomies.md): one ops session that
# fills the taxonomy store from the committed entry list (deploy/taxonomies/entry-points.txt). The
# ops proxy starts for it and is removed afterwards, whatever the outcome. Runs in WSL2 or on the
# Spark, after `just up` (the SEC contact is read from the in-memory secrets folder, never printed).
# Files already in the store are kept, so a second run fetches only what is new or failed before.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../.." && pwd -P)"
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "$@"; }

# The store (policy.json taxonomy_dirs): E: on the desktop (WSL2), /srv/taxonomies on the Spark
# (made by its host role). Never made under /srv here
if [ -z "${AETHERSPARK_TAXONOMY_DIR:-}" ]; then
    if [ -n "${WSL_DISTRO_NAME:-}" ]; then AETHERSPARK_TAXONOMY_DIR=/mnt/e/aetherspark/taxonomies; else AETHERSPARK_TAXONOMY_DIR=/srv/taxonomies; fi
fi
export AETHERSPARK_TAXONOMY_DIR
case "$AETHERSPARK_TAXONOMY_DIR" in /mnt/*) mkdir -p "$AETHERSPARK_TAXONOMY_DIR" ;; esac
[ -d "$AETHERSPARK_TAXONOMY_DIR" ] && [ -w "$AETHERSPARK_TAXONOMY_DIR" ] || {
    echo "taxonomies: the store $AETHERSPARK_TAXONOMY_DIR must exist and be writable by the stack user" >&2
    exit 2
}
secrets_dir="${AETHERSPARK_SECRETS_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/aetherspark/secrets}"
[ -f "$secrets_dir/sec-user-agent-contact" ] || {
    echo "taxonomies: the SEC contact is not unsealed: just up (or just secrets enter sec-user-agent-contact) first" >&2
    exit 2
}

dc build -q pipeline
trap 'dc --profile egress-ops rm -sf egress-ops >/dev/null 2>&1 || true' EXIT
status=0
dc --profile egress-ops --profile taxonomies run --rm taxonomy-job || status=$?
echo "taxonomies: the store holds $(find "$AETHERSPARK_TAXONOMY_DIR" -type f ! -name '.*' ! -name MANIFEST.sha256 | wc -l) files, $(du -sh "$AETHERSPARK_TAXONOMY_DIR" | cut -f1)"
exit "$status"
