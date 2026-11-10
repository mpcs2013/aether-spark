#!/usr/bin/env bash
# The stack test (tests/stack/run.sh) with the egress host rules in place (layer 3), as on the
# Spark: compose-base.md test C6, in CI. It installs the rules and changes Docker's settings, so it
# runs only on a throw-away Linux host with Docker Engine and systemd (the CI runner), never on the
# desktop. Runs as a user with Docker access and sudo without a password (the runner's user).
set -uo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd -P)" || exit 1
if docker info --format '{{.OperatingSystem}}' | grep 'Docker Desktop' >/dev/null; then
    echo "Docker Desktop: no layer 3 (see the egress design); run tests/stack/run.sh instead"
    exit 1
fi

# Build and pull first: with the rules in place, build steps get no network, not even DNS. Later
# builds of the same files are served from the cache. Image pulls run in the Docker daemon on the
# host, which the rules do not restrict here (only the Spark's host mode does).
COMPOSE_PROJECT_NAME=as-stack-test AETHERSPARK_PLACE=localhost \
    docker compose -f "$repo/deploy/compose/stack/compose.yaml" build step-ca postgres-auth keycloak forward-auth whoami \
    || exit 1
for f in tests/egress/compose.layer3.yaml tests/egress/compose.test.yaml; do
    for image in $(sed -n 's/^ *image: *\([^ ]*@sha256:[0-9a-f]*\) *$/\1/p' "$repo/$f" | sort -u); do
        docker pull -q "$image" >/dev/null || exit 1
    done
done

# The rules with test settings: the runner's resolvers; a Home subnet and NAS that do not exist here
# (the stack is published on 127.0.0.1 only)
resolvers() {
    local f=/etc/resolv.conf
    grep -q '^nameserver 127\.0\.0\.53' "$f" && f=/run/systemd/resolve/resolv.conf
    awk '/^nameserver/ && $2 !~ /:/ {print $2}' "$f" | tr '\n' ' '
}
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
cat >"$env_file" <<EOF
AS_RESOLVERS="$(resolvers)"
AS_HOME_SUBNETS="192.168.77.0/24"
AS_NAS_ADDR="192.168.77.20"
AS_NAS_PORT="8002"
EOF
cat "$env_file"
# install.sh applies the rules, restarts Docker with its new settings and ends with the rules' check
sudo "$repo/deploy/host/install.sh" "$env_file" || exit 1
echo "--- layer 3 in place"

AETHERSPARK_LAYER3=1 bash "$repo/tests/stack/run.sh"
