#!/usr/bin/env bash
# Desktop proof for the secrets mechanism (docs/architecture/compose-base.md, "Secrets"): a secret
# decrypted into the WSL2 user's tmpfs reaches a Docker Desktop container memory-backed, readable
# only by its owner, absent from Docker's metadata, logs and writable layers, and gone after cleanup.
# Runs inside WSL2 (Ubuntu) with Docker Desktop's WSL integration; never prints the secret value.
#
#   bash tests/secret-store/desktop-proof.sh                 steps 1-5a, then removes everything
#   bash tests/secret-store/desktop-proof.sh --after-restart step 5b, after `wsl --shutdown` and a restart
#
# Exit 0 when every step passes, 1 on the first failure (fail closed), 2 on a usage error.
set -uo pipefail

IMAGE='busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec'
OTHER_UID=4242
runtime="${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set: run this inside WSL2}"
root="${runtime%/}/aetherspark"
dir="$root/proof"
sentinel="$root/proof-sentinel"                      # in the tmpfs: must not survive a WSL restart
record="${XDG_STATE_HOME:-$HOME/.local/state}/aetherspark/secrets-proof-run"   # on disk: no value
project="aetherspark-secrets-proof"

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; cleanup; exit 1; }
cleanup() {
    docker compose -p "$project" down --remove-orphans >/dev/null 2>&1 || true
    docker rm -f "$project-other" >/dev/null 2>&1 || true
    rm -rf "$dir" "${work:-}"
}

if [ "${1:-}" = "--after-restart" ]; then
    [ -f "$record" ] || { echo "no earlier run recorded ($record): run the proof first"; exit 2; }
    # After `wsl --shutdown` the user's tmpfs is new: nothing from the earlier run may remain
    if [ -e "$sentinel" ] || [ -e "$dir" ]; then
        echo "FAIL  5b the tmpfs survived the restart (run of $(cat "$record"))"
        rm -rf "$sentinel" "$dir" "$record"
        exit 1
    fi
    echo "PASS  5b after the restart nothing of the run of $(cat "$record") is left in the tmpfs"
    rm -f "$record"
    exit 0
fi
[ $# -eq 0 ] || { echo "usage: $0 [--after-restart]"; exit 2; }

command -v docker >/dev/null || { echo "docker not found: enable Docker Desktop's WSL integration"; exit 2; }
work="$(mktemp -d)"
trap cleanup INT TERM

# 1. Memory-backed at the source
mkdir -p "$dir" && chmod 700 "$root" "$dir" || fail "1 cannot create $dir"
fstype="$(findmnt -no FSTYPE -T "$dir")"
[ "$fstype" = tmpfs ] || fail "1 $dir is on $fstype, not tmpfs"
marker="proof-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
want="$(printf '%s' "$marker" | sha256sum | cut -d' ' -f1)"
( umask 077 && printf '%s' "$marker" > "$dir/proof-secret" ) || fail "1 cannot write the secret"
chmod 400 "$dir/proof-secret"
[ "$(stat -c %a "$dir")" = 700 ] && [ "$(stat -c %a "$dir/proof-secret")" = 400 ] \
    || fail "1 modes are $(stat -c %a "$dir") / $(stat -c %a "$dir/proof-secret"), want 700 / 400"
pass "1 the secret is on tmpfs ($dir), folder 700, file 400"

# 2. Memory-backed in the container, as Compose mounts a file secret, and never copied
uid="$(id -u)"
cat > "$work/compose.yaml" <<EOF
name: $project
services:
  reader:
    image: $IMAGE
    user: "$uid:$uid"
    command: ["sleep", "300"]
    read_only: true
    cap_drop: [ALL]
    network_mode: none
    secrets: [proof-secret]
secrets:
  proof-secret:
    file: $dir/proof-secret
EOF
docker compose -f "$work/compose.yaml" up -d --quiet-pull >/dev/null 2>&1 || fail "2 the reader did not start"
reader="$(docker compose -p "$project" ps -q reader)"
mount_line="$(docker exec "$reader" grep ' /run/secrets/proof-secret ' /proc/mounts)" \
    || fail "2 the secret is not mounted in the container"
case "$mount_line" in
    *" tmpfs "*) pass "2a the container's mount of the secret is tmpfs (the same in-memory folder)" ;;
    *) fail "2a the container sees the secret on: $(echo "$mount_line" | cut -d' ' -f3)" ;;
esac
got="$(docker exec "$reader" sha256sum /run/secrets/proof-secret | cut -d' ' -f1)"
[ "$got" = "$want" ] || fail "2a the container reads a different value"
pass "2a the container reads the value (compared by hash)"
hits="$(docker export "$reader" | grep -a -c -F "$marker" || true)"
[ "$hits" = 0 ] || fail "2b the value is in the container's filesystem export ($hits hits)"
pass "2b the value is not in the container's writable layer (docker export)"
sync
disk_hits="$(grep -rIl --binary-files=text -F "$marker" / --exclude-dir=proc --exclude-dir=sys \
    --exclude-dir=run --exclude-dir=mnt --exclude-dir=dev 2>/dev/null | wc -l)"
[ "$disk_hits" = 0 ] || fail "2b the value is in $disk_hits file(s) on WSL2's disk"
pass "2b the value is in no file on WSL2's disk"

# 3. Permissions: the owner reads, another user does not, another container has nothing
docker exec --user "$OTHER_UID:$OTHER_UID" "$reader" cat /run/secrets/proof-secret >/dev/null 2>&1 \
    && fail "3 uid $OTHER_UID can read the secret"
pass "3 the owner (uid $uid) reads the secret, uid $OTHER_UID cannot"
docker run -d --name "$project-other" --network none "$IMAGE" sleep 60 >/dev/null 2>&1 \
    || fail "3 the second container did not start"
docker exec "$project-other" test -e /run/secrets/proof-secret && fail "3 a second container sees the secret"
pass "3 a container without the secret does not see it"

# 4. Not in metadata: inspect, environment, Compose's rendered file, logs
for c in "$reader" "$project-other"; do
    docker inspect "$c" | grep -q -F "$marker" && fail "4 the value is in docker inspect of $c"
    docker exec "$c" env | grep -q -F "$marker" && fail "4 the value is in the environment of $c"
    docker logs "$c" 2>&1 | grep -q -F "$marker" && fail "4 the value is in the logs of $c"
done
docker compose -f "$work/compose.yaml" config 2>/dev/null | grep -q -F "$marker" \
    && fail "4 the value is in docker compose config"
pass "4 the value is in no docker inspect, environment, log or docker compose config output"

# 5a. Cleanup: nothing left in the folder or on disk
cleanup
[ -e "$dir" ] && fail "5a $dir still exists after cleanup"
docker ps -a --format '{{.Names}}' | grep -q "^$project" && fail "5a a proof container is left"
pass "5a after cleanup the secrets folder and the containers are gone"
touch "$sentinel"
mkdir -p "$(dirname "$record")" && date -u +%FT%TZ > "$record"
echo "Next: run 'wsl --shutdown' in PowerShell, open WSL again, and run: bash $0 --after-restart"
