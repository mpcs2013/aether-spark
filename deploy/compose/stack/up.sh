#!/usr/bin/env bash
# `just up` (compose-base.md, "`just up` and `just down`"): secrets into memory, the CA, missing or
# expired certificates, then every service until healthy. Runs in WSL2 or on the Spark. Extra
# arguments are added to every Compose call (the tests add their overlay with -f, relative to the
# repository). Paths are absolute: on WSL2's /mnt/c the working folder can briefly vanish.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../.." && pwd -P)"
# Keycloak's admin route admits one source. Its default is the ingress gateway, which is "this
# machine" on the desktop; on the Spark it must name the owner's desktop (compose-base.md, PR 5a)
if [ "${AETHERSPARK_PLACE:-localhost}" != localhost ] && [ -z "${AETHERSPARK_ADMIN_SOURCE:-}" ]; then
    echo "up: set AETHERSPARK_ADMIN_SOURCE to the owner's desktop address (.local.ref/) first" >&2
    exit 2
fi
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
extra=()
for arg in "$@"; do
    if [ -f "$repo/$arg" ]; then extra+=("$repo/$arg"); else extra+=("$arg"); fi
done
dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${extra[@]}" "$@"; }

# The raw SEC downloads' folder (data-plane.md, decision 9; policy.json raw_dirs): E: on the
# desktop (WSL2), /srv/raw on the Spark (made by its host role, WP6.4). Never made under /srv here
if [ -z "${AETHERSPARK_RAW_DIR:-}" ]; then
    if [ -n "${WSL_DISTRO_NAME:-}" ]; then AETHERSPARK_RAW_DIR=/mnt/e/aetherspark/raw; else AETHERSPARK_RAW_DIR=/srv/raw; fi
fi
export AETHERSPARK_RAW_DIR
case "$AETHERSPARK_RAW_DIR" in /mnt/*) mkdir -p "$AETHERSPARK_RAW_DIR" ;; esac
[ -d "$AETHERSPARK_RAW_DIR" ] && [ -w "$AETHERSPARK_RAW_DIR" ] || {
    echo "up: the raw folder $AETHERSPARK_RAW_DIR must exist and be writable by the stack user" >&2
    exit 2
}

# The local images: step (step-ca and the step CLI, built from source; the first build takes
# minutes), postgres-auth's and Keycloak's, the data stores', the runtime egress proxy's, the ingester's
dc build step-ca postgres-auth keycloak forward-auth whoami backup postgres-data clickhouse egress-runtime ingester
python3 -I "$repo/deploy/secret-store/secret_store.py" generate
python3 -I "$repo/deploy/secret-store/secret_store.py" unseal
python3 -I "$repo/deploy/ca/ca.py" prepare
# The ingester waits for the SEC contact, an entered secret (docs/runbooks/secrets.md): without its
# file Docker would make a root-owned folder in its place, so the ingester is left out until then
secrets_dir="${AETHERSPARK_SECRETS_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/aetherspark/secrets}"
without=()
if [ ! -f "$secrets_dir/sec-user-agent-contact" ]; then
    echo "up: the ingester is not started until the SEC contact is entered: just secrets enter sec-user-agent-contact" >&2
    without=(--scale ingester=0)
fi
# Always new containers: Docker Desktop mounts a secret file as it was when the container was
# created, and unseal has just written new files (after a restart of WSL the old ones are gone)
dc up -d --wait --force-recreate step-ca
dc run --rm cert-bootstrap
dc up -d --wait --force-recreate "${without[@]}"
# The aetherspark realm as the repository says (deploy/keycloak/admin.py apply): Keycloak imports
# the realm file only into an empty database. Skipped before `just keycloak setup` has run
python3 -I "$repo/deploy/keycloak/admin.py" apply --if-ready \
    || echo "up: the realm does not match the repository; see docs/runbooks/keycloak-admin.md" >&2
# Once a day, a snapshot to the backup target (docs/architecture/backup-job.md). A missing login or
# an unreachable target is a warning: the stack is up either way
bash "$repo/deploy/backup/backup.sh" run --if-due "$@" \
    || echo "up: the backup failed; see docs/runbooks/restore-drill.md" >&2
# The intermediate's expiry is watched, not remembered (exit 1 below 60 days: renew it)
python3 -I "$repo/deploy/ca/ca.py" status || echo "up: see docs/runbooks/certificates.md" >&2
