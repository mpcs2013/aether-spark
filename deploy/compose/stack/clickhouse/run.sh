#!/usr/bin/env bash
# ClickHouse's start (docs/architecture/data-plane.md, decisions 6-8), instead of the image's
# entrypoint, which takes its password from an environment variable (test C5 forbids it).
#
#   1. every start: users.xml from its template, with each password's SHA-256 from its secret file,
#      into the in-memory /tmp (so a rotation applies at the next start);
#   2. the server, with this folder's config.xml only (TLS ports only, the renewer's certificate);
#   3. every start, as the migration owner from loopback: the migrations not applied yet
#      (migrate.sh). Healthy only after them, so the services that need the tables start after them;
#   4. `SYSTEM RELOAD CONFIG` every AETHERSPARK_CH_RELOAD_SECONDS (default an hour), which makes the
#      server read the renewed certificate files.
#
# The migration owner's client connects to 127.0.0.1 over TLS without checking the certificate: the
# connection never leaves the container (as postgres-data's owner on its socket), and the
# certificate's name is the service's, not loopback's. No value is printed, logged, placed in an
# argument or in an environment variable.
set -euo pipefail
umask 077
secrets=/run/secrets
own=/tmp/clickhouse
reload="${AETHERSPARK_CH_RELOAD_SECONDS:-3600}"
log() { echo "clickhouse: $*"; }
hash() { sha256sum "$secrets/$1" | cut -d' ' -f1; }

mkdir -p "$own"
sed -e "s/@DATA_OWNER@/$(hash clickhouse-owner-password)/" \
    -e "s/@PIPELINE@/$(hash clickhouse-pipeline-password)/" \
    /clickhouse/users.xml >"$own/users.xml"
grep -q '@[A-Z_]*@' "$own/users.xml" && { log "users.xml: a placeholder is left"; exit 1; }

# The owner's client settings, with its password, in the same in-memory folder (mode 0600)
{
    printf '<clickhouse>\n  <host>127.0.0.1</host>\n  <port>9440</port>\n  <secure>true</secure>\n'
    printf '  <user>data_owner</user>\n  <password>'
    cat "$secrets/clickhouse-owner-password"
    printf '</password>\n'
    printf '  <openSSL><client><loadDefaultCAFile>false</loadDefaultCAFile><verificationMode>none</verificationMode>'
    printf '<invalidCertificateHandler><name>AcceptCertificateHandler</name></invalidCertificateHandler></client></openSSL>\n'
    printf '</clickhouse>\n'
} >"$own/owner-client.xml"
owner() { clickhouse-client --config-file "$own/owner-client.xml" "$@"; }

# Without the watchdog the server is this script's own child, so its exit code is the one waited for
CLICKHOUSE_WATCHDOG_ENABLE=0 clickhouse-server --config-file=/clickhouse/config.xml &
server=$!
stopping=0
# docker stop sends TERM: the server shuts down, then this script ends with its exit code
trap 'stopping=1; kill -TERM "$server" 2>/dev/null || true' TERM INT

until owner -q "SELECT 1" >/dev/null 2>&1; do
    kill -0 "$server" 2>/dev/null || { wait "$server" || exit $?; exit 1; }
    sleep 1
done

bash /clickhouse/migrate.sh || { log "the migrations failed: not ready"; kill -TERM "$server"; wait "$server" || true; exit 1; }
touch /tmp/ready
log "ready: users and the migrations in place; configuration reloaded every ${reload} s"

while :; do
    sleep "$reload" &
    nap=$!
    code=0
    wait -n "$server" "$nap" || code=$?
    if [ "$stopping" = 1 ] || ! kill -0 "$server" 2>/dev/null; then
        kill "$nap" 2>/dev/null || true
        # After TERM, `wait -n` returned for the signal, not for the server: wait for the server
        if [ "$stopping" = 1 ]; then code=0; wait "$server" || code=$?; fi
        log "server stopped (exit $code)"
        exit "$code"
    fi
    if owner -q "SYSTEM RELOAD CONFIG"; then
        log "configuration reloaded (certificate files re-read)"
    else
        log "configuration reload failed"
    fi
done
