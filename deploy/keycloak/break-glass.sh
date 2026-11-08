#!/usr/bin/env bash
# Break-glass (docs/runbooks/keycloak-admin.md): when no administrator can sign in, Keycloak's own
# `bootstrap-admin user` writes a new temporary administrator straight into its database. Keycloak
# stops for about half a minute and starts again whatever happens. The password is typed at the
# prompt (twice), never passed in an argument or a file. The new account must register a security
# key at its first sign-in, like every administrator. Needs the stack up (its secrets unsealed).
#
#   bash deploy/keycloak/break-glass.sh USERNAME [compose arguments, as for up.sh]
#
# Tests only: AETHERSPARK_BREAK_GLASS_PASSWORD_ENV names a variable that holds a throw-away password,
# handed to the one-off container by name (docker run -e NAME), since the tests have no terminal.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd -P)"
user="${1:?usage: break-glass.sh USERNAME [compose arguments]}"
shift
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
extra=()
for arg in "$@"; do
    if [ -f "$repo/$arg" ]; then extra+=("$repo/$arg"); else extra+=("$arg"); fi
done
dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${extra[@]}" "$@"; }

run=()
password=()
if [ -n "${AETHERSPARK_BREAK_GLASS_PASSWORD_ENV:-}" ]; then
    run+=(-e "$AETHERSPARK_BREAK_GLASS_PASSWORD_ENV")
    password+=("--password:env" "$AETHERSPARK_BREAK_GLASS_PASSWORD_ENV")
fi
# A terminal for the password prompt when there is one; none in the tests
[ -t 0 ] || run+=(-T)

echo "break-glass: stopping Keycloak; it starts again at the end"
dc stop keycloak
trap 'echo "break-glass: starting Keycloak"; dc up -d --wait keycloak' EXIT
dc run --rm --no-deps "${run[@]}" keycloak --config-file=/run/secrets/keycloak.conf \
    bootstrap-admin user --username "$user" "${password[@]}" --optimized
echo "break-glass: $user created; sign in at the admin name, register a security key (runbook)"
