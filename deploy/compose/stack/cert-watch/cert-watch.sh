#!/bin/sh
# Runs a server that reads its TLS certificate only at start (oauth2-proxy, whoami: checked in their
# source, 2026-11-09), and restarts it when the renewer has replaced the certificate
# (docs/architecture/compose-base.md, "Gate G3's test relying party", as built in PR 6b).
#
#   cert-watch.sh CERT_FILE PROGRAM [ARGUMENTS...]
#
# Every CERT_WATCH_SECONDS (default 60; the tests use less) it compares the certificate's checksum
# with the one the server started with. On a change it stops the server (TERM, then waits) and
# starts it again; the restart takes well under a second. TERM to this script stops the server and
# ends the script with the server's exit code. If the server ends by itself, so does the script, and
# Docker's restart policy decides what happens next.
set -eu
cert=$1
shift
period="${CERT_WATCH_SECONDS:-60}"
log() { echo "cert-watch: $*" >&2; }
sum() { sha256sum "$cert" 2>/dev/null | cut -d' ' -f1; }

stopping=0
pid=""
trap 'stopping=1; [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null' TERM INT

start() {
    started="$(sum)"
    "$@" &
    pid=$!
}

start "$@"
while :; do
    # Sleep in short steps, so TERM and the server's own end are noticed at once
    waited=0
    while [ "$waited" -lt "$period" ]; do
        if [ "$stopping" = 1 ] || ! kill -0 "$pid" 2>/dev/null; then
            code=0
            wait "$pid" || code=$?
            log "server ended (exit $code)"
            exit "$code"
        fi
        sleep 1
        waited=$((waited + 1))
    done
    now="$(sum)"
    if [ -n "$now" ] && [ "$now" != "$started" ]; then
        log "new certificate: restarting the server"
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        start "$@"
    fi
done
