#!/usr/bin/env bash
# `just down`: stop the stack and empty the in-memory secrets folder; volumes stay. Extra arguments
# are added to the Compose call, as for up.sh.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../.." && pwd -P)"
extra=()
for arg in "$@"; do
    if [ -f "$repo/$arg" ]; then extra+=("$repo/$arg"); else extra+=("$arg"); fi
done
docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${extra[@]}" --profile '*' down
python3 -I "$repo/deploy/secret-store/secret_store.py" clear
