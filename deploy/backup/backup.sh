#!/usr/bin/env bash
# AetherSpark's backup job (docs/architecture/backup-job.md; ADR-0013 note of 2026-11-11).
# Runs in WSL2 or on the Spark, with the stack up. Commands:
#
#   run [--if-due]    one snapshot; --if-due: only if none was made today (UTC), and a missing
#                     login or an unreachable target is a warning, not an error (`just up`)
#   key-add           add AetherSpark's own key; restic asks for the repository's existing
#                     password on the terminal (the owner's, from the password manager)
#   init              create a repository with AetherSpark's key as its first (the scratch
#                     repository during the build, A-015 point 2; never the desktop-phase one)
#   keys              the repository's keys (their IDs, for the home infrastructure's register)
#   snapshots         this machine's snapshots (host and tag)
#   prove-append-only one HTTP DELETE of the newest snapshot of this host and tag: must be refused
#   restore-secrets [--snapshot ID]
#                     a machine without its sealed store: brings it back from a snapshot, opened
#                     with the repository's original password (restic asks); files already in the
#                     store (the login just entered) are kept
#   restore [--snapshot ID] [--replace] [--expect-root FINGERPRINT]
#                     the stack down: the CA volume and Keycloak's database from a snapshot (the
#                     newest of this host and tag, never one dated in the future; after an incident
#                     name one from before it). Refuses volumes that are not empty; --replace keeps
#                     them aside as <volume>-before-restore. Then `just up`.
#
# Other arguments are added to every Compose call, as up.sh does (the tests add their overlay with
# -f, relative to the repository). Every value comes from a file in the in-memory secrets folder;
# nothing secret is printed, passed as an argument or placed in a variable of a process.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd -P)"
cmd="${1:-}"
shift || true
if_due=0
replace=0
snapshot=""
expect_root=""
extra=()
while [ $# -gt 0 ]; do
    case $1 in
        --if-due) if_due=1 ;;
        --replace) replace=1 ;;
        --snapshot) snapshot="${2:?--snapshot needs an id}"; shift ;;
        --expect-root) expect_root="${2:?--expect-root needs a fingerprint}"; shift ;;
        *) if [ -f "$repo/$1" ]; then extra+=("$repo/$1"); else extra+=("$1"); fi ;;
    esac
    shift
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
# restic as the repository's owner: restic asks for the original password on the terminal. The
# tests give it a file instead (AETHERSPARK_BACKUP_OWNER_PASSWORD_FILE), as they give entered
# secrets through a file descriptor; nothing else sets it.
owner_restic() {
    if [ -n "${AETHERSPARK_BACKUP_OWNER_PASSWORD_FILE:-}" ]; then
        dc --profile backup run --rm --no-deps -T -e RESTIC_PASSWORD_FILE=/run/owner-password \
            -v "$AETHERSPARK_BACKUP_OWNER_PASSWORD_FILE:/run/owner-password:ro" backup-owner "$@"
    else
        dc --profile backup run --rm --no-deps backup-owner "$@"
    fi
}

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

# --- restore (backup-job.md, Restore) ---------------------------------------------------------

project() { dc config --format json | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["name"])'; }

choose() { # runner (restic_ or owner_restic): the snapshot to restore, printed; the candidates on stderr
    "$1" snapshots --json --host "$host" --tag "$tag" | python3 -I -c '
import datetime as dt, json, re, sys
wanted = sys.argv[1]
def when(s):  # restic writes nanoseconds; Python reads microseconds
    return dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s["time"]).replace("Z", "+00:00"))
snaps = sorted(json.load(sys.stdin), key=when)
late = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
for s in snaps:
    short, stamp = s["id"][:8], when(s).isoformat(timespec="seconds")
    print(f"  {short}  {stamp}" + ("  (in the future)" if when(s) > late else ""), file=sys.stderr)
if wanted:
    hits = [s for s in snaps if len(wanted) >= 8 and s["id"].startswith(wanted)]
    if len(hits) != 1:
        sys.exit(f"backup: snapshot {wanted} is not one of these")
    pick = hits[0]
else:
    usable = [s for s in snaps if when(s) <= late]
    if not usable:
        sys.exit("backup: no snapshot of this host and tag that is not dated in the future")
    pick = usable[-1]
short = pick["id"][:8]
if when(pick) > late:
    sys.exit(f"backup: snapshot {short} is dated in the future: refused (a forged date?)")
print(pick["id"])' "$snapshot"
}

volume_empty() { # volume suffix: true when it does not exist or holds nothing
    local v
    v="$(project)_$1"
    docker volume inspect "$v" >/dev/null 2>&1 || return 0
    [ -z "$(docker run --rm --network none -v "$v:/v:ro" --entrypoint sh aetherspark/restic:local -c 'find /v -mindepth 1 | head -1')" ]
}

set_aside() { # volume suffix: copied to <volume>-before-restore, then removed
    local v to
    v="$(project)_$1"
    to="${v}-before-restore"
    if docker volume inspect "$to" >/dev/null 2>&1; then
        say "$to exists already: keep or remove it (docker volume rm $to), then try again" >&2
        exit 1
    fi
    docker volume create "$to" >/dev/null
    docker run --rm --network none -v "$v:/from:ro" -v "$to:/to" --entrypoint sh aetherspark/restic:local -c 'cp -a /from/. /to/'
    docker volume rm "$v" >/dev/null
    say "the current $1 is kept as volume $to"
}

restore_secrets() {
    # Only the repository's file: the store holds nothing but the login just entered, and
    # `generate` would make new values where the restored ones belong
    python3 -I "$repo/deploy/secret-store/secret_store.py" unseal --only backup-repository >/dev/null \
        || { say "enter the backup login first (just secrets enter, docs/runbooks/restore-drill.md)" >&2; exit 1; }
    staged
    local id store=$AETHERSPARK_SECRETS_STORE n=0 f
    id="$(choose owner_restic)"
    owner_restic restore "$id" --target /backup/restore --include /backup/sealed >/dev/null
    mkdir -p "$store"
    chmod 0700 "$store"
    for f in "$AETHERSPARK_BACKUP_DIR/restore/backup/sealed/"*.age; do
        [ -e "$f" ] || continue
        [ -e "$store/$(basename "$f")" ] && continue
        cp "$f" "$store/" && chmod 0600 "$store/$(basename "$f")" && n=$((n + 1))
    done
    say "snapshot ${id:0:8}: $n sealed files restored into the store (files already there kept)"
    say "next: put the dev key's off-line copy in place, then just secrets unseal and just backup restore"
}

restore() {
    # `just down` emptied the in-memory folder; the store has every value (restore-secrets first on
    # a machine that lost it)
    python3 -I "$repo/deploy/secret-store/secret_store.py" unseal >/dev/null \
        || { say "the secrets do not unseal: on a machine without its store, restore-secrets first" >&2; exit 1; }
    ready || { say "no backup login, or no AetherSpark key in the store (restore-secrets first)" >&2; exit 1; }
    [ -z "$(dc ps -q 2>/dev/null)" ] || { say "the stack is running: just down first" >&2; exit 1; }
    staged
    local id v
    id="$(choose restic_)"
    for v in step-ca postgres-auth-data; do
        volume_empty "$v" && continue
        if [ "$replace" = 1 ]; then set_aside "$v"; else
            say "the volume $v is not empty: a restore never writes over live data; --replace keeps it aside" >&2
            exit 1
        fi
    done
    restic_ restore "$id" --target /backup/restore --include /backup/ca --include /backup/keycloak >/dev/null
    [ -s "$AETHERSPARK_BACKUP_DIR/restore/backup/keycloak/keycloak.dump" ] || { say "the snapshot has no Keycloak dump" >&2; exit 1; }
    # The volumes as `just up` makes them (the stack user's), then the CA's files into its own
    python3 -I "$repo/deploy/ca/ca.py" volumes >/dev/null
    dc --profile backup run --rm --no-deps -T --entrypoint cp backup-files -a /backup/restore/backup/ca/. /ca/
    # postgres-auth with what it needs (step-ca, the bootstrap step, its renewer), as up.sh starts them
    dc up -d --wait --force-recreate step-ca >/dev/null
    dc run --rm cert-bootstrap >/dev/null
    dc up -d --wait postgres-auth >/dev/null
    # The dump into the new, empty keycloak database, inside postgres-auth as the keycloak role
    dc exec -T postgres-auth sh -ceu '
        umask 077
        { printf "*:*:keycloak:keycloak:"; cat /run/secrets/keycloak-db-password; printf "\n"; } > /tmp/restore.pgpass
        trap "rm -f /tmp/restore.pgpass" EXIT
        PGPASSFILE=/tmp/restore.pgpass pg_restore --no-owner --no-acl --single-transaction --exit-on-error --no-password \
            --dbname "host=$(hostname -i | cut -d" " -f1) dbname=keycloak user=keycloak sslmode=require connect_timeout=10"
    ' < "$AETHERSPARK_BACKUP_DIR/restore/backup/keycloak/keycloak.dump"
    local fingerprint
    fingerprint="$(python3 -I "$repo/deploy/ca/ca.py" status 2>/dev/null | sed -n 's/.*root fingerprint \([0-9a-f]*\).*/\1/p')"
    say "snapshot ${id:0:8} restored: the CA and Keycloak's database"
    if [ -n "$expect_root" ]; then
        [ "$fingerprint" = "$expect_root" ] || { say "the root fingerprint $fingerprint is not the expected one: stop" >&2; exit 1; }
        say "the root fingerprint matches the expected one"
    else
        say "root fingerprint $fingerprint: compare it with your record before you trust it"
    fi
    say "next: just up"
}

case $cmd in
    run) run ;;
    restore-secrets) restore_secrets ;;
    restore) restore ;;
    key-add)
        ready || { say "enter the backup login first (just secrets enter)" >&2; exit 1; }
        staged
        # Interactive: restic asks for the existing password itself; the new one is a file
        dc --profile backup run --rm --no-deps -e RESTIC_PASSWORD_FILE= backup \
            key add --new-password-file /run/secrets/backup-key-password ;;
    init)
        ready || { say "enter the backup login first (just secrets enter)" >&2; exit 1; }
        staged
        restic_ init ;;
    keys) staged; restic_ key list ;;
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
    *) echo "usage: backup.sh run [--if-due] | init | key-add | keys | snapshots | prove-append-only | restore-secrets | restore [options] [compose files]" >&2; exit 2 ;;
esac
