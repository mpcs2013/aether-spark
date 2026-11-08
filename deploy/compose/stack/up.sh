#!/usr/bin/env bash
# `just up` (compose-base.md, "`just up` and `just down`"): secrets into memory, the CA, missing or
# expired certificates, then every service until healthy. Runs in WSL2 or on the Spark. Extra
# arguments are added to every Compose call (the tests add their overlay with -f, relative to the
# repository). Paths are absolute: on WSL2's /mnt/c the working folder can briefly vanish.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../.." && pwd -P)"
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
extra=()
for arg in "$@"; do
    if [ -f "$repo/$arg" ]; then extra+=("$repo/$arg"); else extra+=("$arg"); fi
done
dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${extra[@]}" "$@"; }

# The local images: step (step-ca and the step CLI, built from source; the first build takes
# minutes) and postgres-auth's
dc build step-ca postgres-auth
python3 -I "$repo/deploy/secret-store/secret_store.py" generate
python3 -I "$repo/deploy/secret-store/secret_store.py" unseal
python3 -I "$repo/deploy/ca/ca.py" prepare
# Always new containers: Docker Desktop mounts a secret file as it was when the container was
# created, and unseal has just written new files (after a restart of WSL the old ones are gone)
dc up -d --wait --force-recreate step-ca
dc run --rm cert-bootstrap
dc up -d --wait --force-recreate
# The intermediate's expiry is watched, not remembered (exit 1 below 60 days: renew it)
python3 -I "$repo/deploy/ca/ca.py" status || echo "up: see docs/runbooks/certificates.md" >&2
