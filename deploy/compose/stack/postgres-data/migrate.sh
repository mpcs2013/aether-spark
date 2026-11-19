#!/usr/bin/env bash
# postgres-data's migrations (docs/architecture/data-plane.md, section 2 and decision 7), run by
# run.sh at every start, before the server reports healthy. As the migration owner (data_owner) on
# this container's own socket: the only role that may create, alter or drop. Each file of
# migrations/ (NNNN_name.sql, in name order) is applied once, in one transaction, and recorded in
# meta.migrations with its SHA-256; a file that changed after it was applied stops the start, so a
# migration is never edited, only followed by a new one. The EF Core migrations of the services
# built here arrive as such files too (`dotnet ef migrations script`). Its password goes through a
# password file in the container's in-memory /tmp, never through an argument or the environment.
set -euo pipefail
umask 077
dir=/postgres-data/migrations
pass=/tmp/migrate.pgpass
{ printf '*:*:*:data_owner:'; cat /run/secrets/postgres-data-owner-password; printf '\n'; } >"$pass"
trap 'rm -f "$pass"' EXIT
sql() { PGPASSFILE="$pass" psql -h /var/run/postgresql -U data_owner -d research -X -q -t -A -v ON_ERROR_STOP=1 "$@"; }

sql -c "CREATE SCHEMA IF NOT EXISTS meta" \
    -c "CREATE TABLE IF NOT EXISTS meta.migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
applied=0
for file in "$dir"/*.sql; do
    [ -e "$file" ] || continue
    name="$(basename "$file")"
    [[ "$name" =~ ^[0-9]{4}_[a-z0-9_]+\.sql$ ]] || { echo "postgres-data: $name is not NNNN_name.sql" >&2; exit 1; }
    sum="$(sha256sum "$file" | cut -d' ' -f1)"
    known="$(sql -c "SELECT sha256 FROM meta.migrations WHERE name = '$name'")"
    if [ -n "$known" ]; then
        [ "$known" = "$sum" ] || { echo "postgres-data: $name changed after it was applied: add a new migration instead" >&2; exit 1; }
        continue
    fi
    sql -1 -f "$file" -c "INSERT INTO meta.migrations (name, sha256) VALUES ('$name', '$sum')"
    applied=$((applied + 1))
    echo "postgres-data: migration $name applied"
done
echo "postgres-data: migrations up to date ($applied applied now)"
