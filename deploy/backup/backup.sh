#!/usr/bin/env bash
# AetherSpark's backup job (docs/architecture/backup-job.md; ADR-0013 note of 2026-11-11).
# Runs in WSL2 or on the Spark, with the stack up. Commands:
#
#   run [--if-due]    one snapshot; --if-due: only if none was made today (UTC), and a missing
#                     login or an unreachable target is a warning, not an error (`just up`)
#   key-add           add AetherSpark's own key; restic asks for the repository's existing
#                     password on the terminal (the owner's, from the password manager)
#   snapshots         this machine's snapshots (host and tag)
#   prove-append-only one HTTP DELETE of the newest snapshot of this host and tag: must be refused
#
# Other arguments are added to every Compose call, as up.sh does (the tests add their overlay with
# -f, relative to the repository). Every value comes from a file in the in-memory secrets folder;
# nothing secret is printed, passed as an argument or placed in a variable of a process.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd -P)"
cmd="${1:-}"
shift || true
if_due=0
extra=()
for arg in "$@"; do
    case $arg in
        --if-due) if_due=1 ;;
        *) if [ -f "$repo/$arg" ]; then extra+=("$repo/$arg"); else extra+=("$arg"); fi ;;
    esac
done
dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${extra[@]}" "$@"; }

export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
secrets_dir="${AETHERSPARK_SECRETS_DIR:-${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set}/aetherspark/secrets}"
export AETHERSPARK_SECRETS_STORE="${AETHERSPARK_SECRETS_STORE:-$HOME/.aetherspark/secrets/dev}"
export AETHERSPARK_BACKUP_DIR="${AETHERSPARK_BACKUP_DIR:-$(dirname "$secrets_dir")/backup}"
host="${AETHERSPARK_BACKUP_HOST:-aetherspark-desktop}"
tag=aetherspark-stack
marker="${AETHERSPARK_BACKUP_MARKER:-$HOME/.aetherspark/backup-last}"
paths=(/backup/ca /backup/keycloak /backup/sealed /backup/feed)

say() { echo "backup: $*"; }
warn_or_fail() { # message: a warning under --if-due (just up goes on), else an error
    if [ "$if_due" = 1 ]; then say "skipped: $1" >&2; exit 0; fi
    say "$1" >&2
    exit 1
}
# restic in the job's container: the repository and the key's password from files only
restic_() { dc --profile backup run --rm --no-deps -T backup "$@"; }

ready() { [ -s "$secrets_dir/backup-repository" ] && [ -s "$secrets_dir/backup-key-password" ]; }

# The target's port within 5 seconds, from the job's own network: restic itself retries a silent
# target for many minutes, which would hold `just up`. The address is read inside the container
# from the repository file and never printed.
answers() {
    dc --profile backup run --rm --no-deps -T --entrypoint sh backup -c '
        hp="$(sed -E "s#^rest:https?://[^@/]*@([^/]+).*#\1#" /run/secrets/backup-repository)"
        nc -z -w 5 "${hp%:*}" "${hp##*:}"' >/dev/null 2>&1
}

stage() { # the in-memory staging folder, empty, 0700, on a tmpfs
    rm -rf "$AETHERSPARK_BACKUP_DIR"
    # sealed: the store's mount point inside (made here, so Docker never makes it as root)
    mkdir -p "$AETHERSPARK_BACKUP_DIR"
    chmod 0700 "$AETHERSPARK_BACKUP_DIR"
    mkdir "$AETHERSPARK_BACKUP_DIR"/{ca,keycloak,sealed,feed,restore}
    python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import secret_store, pathlib
sys.exit(0 if secret_store.is_tmpfs(pathlib.Path(sys.argv[2])) else 1)' "$repo/deploy/secret-store" "$AETHERSPARK_BACKUP_DIR" \
        || { say "$AETHERSPARK_BACKUP_DIR is not on a tmpfs: nothing plaintext goes to a disk" >&2; exit 1; }
}

step_ca_stopped=0
cleanup() { # however the job ends: step-ca running again, no staging folder
    if [ "$step_ca_stopped" = 1 ]; then dc up -d --wait step-ca >/dev/null 2>&1 || say "start step-ca by hand: just up" >&2; fi
    rm -rf "$AETHERSPARK_BACKUP_DIR"
}

dump_keycloak() { # pg_dump inside postgres-auth, as the keycloak role, over TLS to its own address
    dc exec -T postgres-auth sh -ceu '
        umask 077
        { printf "*:*:keycloak:keycloak:"; cat /run/secrets/keycloak-db-password; printf "\n"; } > /tmp/backup.pgpass
        trap "rm -f /tmp/backup.pgpass" EXIT
        PGPASSFILE=/tmp/backup.pgpass pg_dump --format=custom --no-password \
            "host=$(hostname -i | cut -d" " -f1) dbname=keycloak user=keycloak sslmode=require connect_timeout=10"
    ' > "$AETHERSPARK_BACKUP_DIR/keycloak/keycloak.dump"
    [ -s "$AETHERSPARK_BACKUP_DIR/keycloak/keycloak.dump" ]
}

copy_ca() { # step-ca stopped only for the copy: its database is not safe to copy while it writes
    step_ca_stopped=1
    dc stop step-ca >/dev/null
    dc --profile backup run --rm --no-deps -T --entrypoint cp backup-files -a /ca/. /backup/ca/
    dc up -d --wait step-ca >/dev/null
    step_ca_stopped=0
}

# Every container of the job mounts the staging folder, so it exists before any of them starts
# (Docker would create a missing one as root, compose.yaml: create_host_path false) and is gone after
staged() { trap cleanup EXIT; stage; }

run() {
    if [ "$if_due" = 1 ] && [ "$(cat "$marker" 2>/dev/null)" = "$(date -u +%F)" ]; then exit 0; fi
    ready || warn_or_fail "no backup login yet (just secrets enter; docs/runbooks/restore-drill.md)"
    staged
    answers || warn_or_fail "the backup target does not answer"
    restic_ cat config >/dev/null 2>&1 \
        || warn_or_fail "the repository does not open with AetherSpark's key (just backup key-add first)"
    dump_keycloak || { say "the Keycloak dump failed" >&2; exit 1; }
    copy_ca
    local out id
    out="$(restic_ backup --json --host "$host" --tag "$tag" "${paths[@]}")"
    id="$(printf '%s\n' "$out" | python3 -I -c 'import json, sys
ids = [m["snapshot_id"] for m in map(json.loads, filter(None, sys.stdin.read().splitlines())) if m.get("message_type") == "summary"]
print(ids[-1] if ids else "")')"
    [ -n "$id" ] || { say "restic reported no snapshot" >&2; exit 1; }
    # The new snapshot holds exactly the four paths
    restic_ snapshots --json "$id" | python3 -I -c 'import json, sys
s = json.load(sys.stdin)
sys.exit(0 if len(s) == 1 and sorted(s[0]["paths"]) == sorted(sys.argv[1:]) else 1)' "${paths[@]}" \
        || { say "snapshot ${id:0:8} does not hold the expected paths" >&2; exit 1; }
    mkdir -p "$(dirname "$marker")"
    date -u +%F > "$marker"
    say "snapshot ${id:0:8} (host $host, tag $tag)"
}

case $cmd in
    run) run ;;
    key-add)
        ready || { say "enter the backup login first (just secrets enter)" >&2; exit 1; }
        staged
        # Interactive: restic asks for the existing password itself; the new one is a file
        dc --profile backup run --rm --no-deps -e RESTIC_PASSWORD_FILE= backup \
            key add --new-password-file /run/secrets/backup-key-password ;;
    snapshots) staged; restic_ snapshots --host "$host" --tag "$tag" ;;
    prove-append-only)
        # From the host, which the backup target admits in the desktop phase (A-012 requirement 8):
        # restic has no plain delete, and `forget` is never run against the NAS (A-015 condition a)
        ready || { say "enter the backup login first" >&2; exit 1; }
        staged
        latest="$(restic_ snapshots --json --host "$host" --tag "$tag" --latest 1 \
            | python3 -I -c 'import json, sys; s = json.load(sys.stdin); print(s[-1]["id"] if s else "")')"
        [ -n "$latest" ] || { say "no snapshot of host $host and tag $tag yet" >&2; exit 1; }
        python3 -I "$repo/deploy/backup/append_only.py" "$secrets_dir/backup-repository" "$latest" ;;
    *) echo "usage: backup.sh run [--if-due] | key-add | snapshots | prove-append-only [compose files]" >&2; exit 2 ;;
esac
