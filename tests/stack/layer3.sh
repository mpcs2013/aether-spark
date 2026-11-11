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
    docker compose -f "$repo/deploy/compose/stack/compose.yaml" -f "$repo/tests/stack/compose.test.yaml" \
    build step-ca postgres-auth keycloak forward-auth whoami backup backup-standin \
    || exit 1
for f in tests/egress/compose.layer3.yaml tests/egress/compose.test.yaml tests/stack/compose.test.yaml deploy/compose/stack/compose.yaml; do
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

# The backup target's stand-in (backup-job.md, tests B3, B4, B7), outside Docker's bridges: the
# pinned rest-server in a network namespace at the test NAS address and port, so the job's traffic
# is routed through the host rules as it will be on the Spark. Append-only, private repositories,
# a login made here; run.sh gets the login in a file and a command to stop and start it.
standin="$(mktemp -d)"
trap 'rm -f "$env_file"; [ -x "$standin/ctl" ] && "$standin/ctl" stop; sudo ip netns del t3home 2>/dev/null; rm -rf "$standin"' EXIT
standin_image=aetherspark/rest-server:local  # built above (tests/stack/standin)
cid="$(docker create "$standin_image")" && docker cp -q "$cid:/usr/bin/rest-server" "$standin/rest-server" && docker rm "$cid" >/dev/null || exit 1
standin_name=stack-test
standin_password="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf '%s\n' "$standin_password" | docker run --rm -i --network none --entrypoint htpasswd "$standin_image" -niB "$standin_name" \
    >"$standin/.htpasswd" || exit 1
( umask 077; printf '%s\n%s\n' "$standin_name" "$standin_password" >"$standin/login" )
mkdir -p "$standin/data"
sudo ip netns add t3home
sudo ip link add t3home type veth peer name eth0 netns t3home
sudo ip addr add 192.168.77.1/24 dev t3home && sudo ip link set t3home up
sudo ip -n t3home addr add 192.168.77.20/24 dev eth0
sudo ip -n t3home link set lo up && sudo ip -n t3home link set eth0 up
sudo ip -n t3home route add default via 192.168.77.1
cat >"$standin/ctl" <<EOF
#!/usr/bin/env bash
# start | stop the stand-in rest-server in its network namespace, as $(id -un)
case \$1 in
    start)
        sudo ip netns exec t3home sudo -u $(id -un) "$standin/rest-server" --path "$standin/data" \
            --listen 192.168.77.20:8002 --append-only --private-repos --htpasswd-file "$standin/.htpasswd" \
            >>"$standin/log" 2>&1 &
        for _ in \$(seq 1 50); do curl -s -o /dev/null -m 1 http://192.168.77.20:8002/ && exit 0; sleep 0.2; done
        echo "stand-in did not start"; tail -5 "$standin/log"; exit 1 ;;
    stop) sudo pkill -f "$standin/rest-server" ; sleep 0.5 ;;
esac
EOF
chmod 0755 "$standin/ctl"
"$standin/ctl" start || exit 1

AETHERSPARK_LAYER3=1 AETHERSPARK_BACKUP_STANDIN=192.168.77.20:8002 AETHERSPARK_STANDIN_LOGIN_FILE="$standin/login" \
    AETHERSPARK_STANDIN_CTL="$standin/ctl" bash "$repo/tests/stack/run.sh"
