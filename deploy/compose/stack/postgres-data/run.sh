#!/usr/bin/env bash
# postgres-data's start (docs/architecture/data-plane.md, decisions 6-8): the research data's own
# server, started like postgres-auth (postgres-auth/run.sh says why not the image's entrypoint: no
# secret may sit in a process environment, test C5).
#
#   1. first start only: initdb with the superuser password from its secret file;
#   2. the server, with TLS from the renewer's volume and the reviewed pg_hba.conf;
#   3. every start, as the superuser: the roles (each password from its secret file, so a rotation
#      applies at the next start) and the research database, owned by the migration owner;
#   4. every start, as the migration owner: the migrations not applied yet (migrate.sh). Healthy
#      only after them, so the services that need the tables start after them;
#   5. a reload every AETHERSPARK_PG_RELOAD_SECONDS (default an hour), for the certificate files.
#
# The roles: data_owner owns the database and its schemas and is the only one that changes tables;
# it signs in only on this container's socket. ingester and pipeline sign in over TLS from the ingest
# network and get rows only (migrations/0001_schemas.sql). No value is printed, logged, placed in an
# argument or in an environment variable.
set -euo pipefail
umask 077
secrets=/run/secrets
socket=/var/run/postgresql
reload="${AETHERSPARK_PG_RELOAD_SECONDS:-3600}"
data="${PGDATA:?the image sets PGDATA}"
log() { echo "postgres-data: $*"; }

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
        initdb -D "$data" --username=postgres --pwfile="$secrets/postgres-data-superuser-password" \
        --auth=scram-sha-256 --encoding=UTF8 --locale=C \
        --locale-provider=builtin --builtin-locale=C.UTF-8 >/dev/null
    rm -f /tmp/passwd /tmp/group
    log "data folder initialised"
fi

postgres -D "$data" \
    -c listen_addresses='*' \
    -c unix_socket_directories="$socket" \
    -c hba_file=/postgres-data/pg_hba.conf \
    -c password_encryption=scram-sha-256 \
    -c ssl=on \
    -c ssl_cert_file=/tls/tls.crt \
    -c ssl_key_file=/tls/tls.key \
    -c ssl_min_protocol_version=TLSv1.2 \
    -c log_connections=on \
    -c shared_buffers=256MB \
    -c max_connections=50 &
server=$!
stopping=0
# docker stop sends TERM: a fast shutdown, then this script ends with the server's exit code
trap 'stopping=1; kill -INT "$server" 2>/dev/null || true' TERM INT

until pg_isready -q -h "$socket" -d postgres -U postgres; do
    kill -0 "$server" 2>/dev/null || { wait "$server" || exit $?; exit 1; }
    sleep 1
done

# As postgres-auth/run.sh: the superuser's password through a password file in this container's
# in-memory /tmp, removed right after; the roles' passwords from their files into psql variables.
# This session logs no statement, not even a failing one.
{ printf '*:*:*:postgres:'; cat "$secrets/postgres-data-superuser-password"; printf '\n'; } >/tmp/pgpass
PGPASSFILE=/tmp/pgpass psql -h "$socket" -U postgres -d postgres -X -q -v ON_ERROR_STOP=1 <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SELECT format('CREATE ROLE %I LOGIN', r) FROM unnest(ARRAY['data_owner', 'ingester', 'pipeline']) AS r
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) \gexec
\set password `cat /run/secrets/postgres-data-owner-password`
SELECT format('ALTER ROLE data_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', :'password') \gexec
\set password `cat /run/secrets/postgres-data-ingester-password`
SELECT format('ALTER ROLE ingester WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', :'password') \gexec
\set password `cat /run/secrets/postgres-data-pipeline-password`
SELECT format('ALTER ROLE pipeline WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', :'password') \gexec
\unset password
SELECT 'CREATE DATABASE research OWNER data_owner' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'research') \gexec
REVOKE ALL ON DATABASE research FROM PUBLIC;
GRANT CONNECT ON DATABASE research TO ingester, pipeline;
\connect research
SET log_statement = 'none';
-- Nobody creates or reads in public: every table lives in a schema of the migrations
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
rm -f /tmp/pgpass

bash /postgres-data/migrate.sh || { log "the migrations failed: not ready"; kill -INT "$server"; wait "$server" || true; exit 1; }
touch /tmp/ready
log "ready: roles, database research and its migrations in place; reload every ${reload} s"

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
