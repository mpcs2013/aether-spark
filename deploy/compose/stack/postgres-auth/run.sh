#!/usr/bin/env bash
# postgres-auth's start (docs/architecture/compose-base.md, "postgres-auth"): Keycloak's own
# database server. Runs as the stack user in the official image, instead of the image's entrypoint:
# that one reads the superuser password into an environment variable on every start and leaves it
# in the server's environment, and no secret may sit in a process environment (test C5).
#
#   1. first start only: initdb with the superuser password from its secret file (nss_wrapper
#      gives the stack user the passwd entry initdb insists on, for that one call), with Postgres'
#      own builtin C.UTF-8 locale, the same whatever C library the image has;
#   2. the server, with TLS from the renewer's volume and the reviewed pg_hba.conf;
#   3. every start: the keycloak role (its password from its secret file, so a rotation applies
#      at the next start) and the keycloak database;
#   4. a reload every AETHERSPARK_PG_RELOAD_SECONDS (default an hour): Postgres re-reads its
#      certificate files only on a reload, and the renewer replaces them a day at a time.
#
# No value is printed, logged, placed in an argument or in an environment variable.
set -euo pipefail
umask 077
secrets=/run/secrets
socket=/var/run/postgresql
reload="${AETHERSPARK_PG_RELOAD_SECONDS:-3600}"
data="${PGDATA:?the image sets PGDATA}"
log() { echo "postgres-auth: $*"; }

if [ ! -s "$data/PG_VERSION" ]; then
    mkdir -p "$data"
    chmod 0700 "$data"
    wrapper=""
    for candidate in /usr/lib/libnss_wrapper.so /usr/lib/*/libnss_wrapper.so; do
        if [ -f "$candidate" ]; then wrapper=$candidate; break; fi
    done
    [ -n "$wrapper" ] || { log "libnss_wrapper.so not found: initdb needs it"; exit 1; }
    printf 'postgres:x:%s:%s:postgres:%s:/bin/false\n' "$(id -u)" "$(id -g)" "$data" >/tmp/passwd
    printf 'postgres:x:%s:\n' "$(id -g)" >/tmp/group
    LD_PRELOAD="$wrapper" NSS_WRAPPER_PASSWD=/tmp/passwd NSS_WRAPPER_GROUP=/tmp/group \
        initdb -D "$data" --username=postgres --pwfile="$secrets/postgres-auth-superuser-password" \
        --auth=scram-sha-256 --encoding=UTF8 --locale=C \
        --locale-provider=builtin --builtin-locale=C.UTF-8 >/dev/null
    rm -f /tmp/passwd /tmp/group
    log "data folder initialised"
fi

postgres -D "$data" \
    -c listen_addresses='*' \
    -c unix_socket_directories="$socket" \
    -c hba_file=/postgres-auth/pg_hba.conf \
    -c password_encryption=scram-sha-256 \
    -c ssl=on \
    -c ssl_cert_file=/tls/tls.crt \
    -c ssl_key_file=/tls/tls.key \
    -c ssl_min_protocol_version=TLSv1.2 \
    -c log_connections=on \
    -c shared_buffers=128MB \
    -c max_connections=50 &
server=$!
stopping=0
# docker stop sends TERM: a fast shutdown, then this script ends with the server's exit code
trap 'stopping=1; kill -INT "$server" 2>/dev/null || true' TERM INT

until pg_isready -q -h "$socket" -d postgres -U postgres; do
    kill -0 "$server" 2>/dev/null || { wait "$server" || exit $?; exit 1; }
    sleep 1
done

# The superuser's password reaches psql through a password file in this container's in-memory /tmp,
# removed right after; the keycloak password goes from its file into a psql variable (psql reads it
# with cat, so it is in no argument). This session logs no statement, not even a failing one.
{ printf '*:*:*:postgres:'; cat "$secrets/postgres-auth-superuser-password"; printf '\n'; } >/tmp/pgpass
PGPASSFILE=/tmp/pgpass psql -h "$socket" -U postgres -d postgres -X -q -v ON_ERROR_STOP=1 <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SELECT 'CREATE ROLE keycloak LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak') \gexec
\set password `cat /run/secrets/keycloak-db-password`
SELECT format('ALTER ROLE keycloak WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'password') \gexec
\unset password
SELECT 'CREATE DATABASE keycloak OWNER keycloak' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak') \gexec
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
SQL
rm -f /tmp/pgpass
touch /tmp/ready
log "ready: role and database keycloak in place; reload every ${reload} s"

while :; do
    sleep "$reload" &
    nap=$!
    code=0
    wait -n "$server" "$nap" || code=$?
    if [ "$stopping" = 1 ] || ! kill -0 "$server" 2>/dev/null; then
        kill "$nap" 2>/dev/null || true
        if [ "$stopping" = 1 ]; then wait "$server" || code=$?; fi
        log "server stopped (exit $code)"
        exit "$code"
    fi
    kill -HUP "$server"
    log "configuration reloaded (certificate files re-read)"
done
