#!/usr/bin/env bash
# ClickHouse's migrations (docs/architecture/data-plane.md, section 2 and decision 7; ADR-0007:
# versioned SQL files), run by run.sh at every start, before the server reports healthy. As the
# migration owner (data_owner) from this container's loopback: the only user that may create, alter
# or drop. Each file of migrations/ (NNNN_name.sql, in name order) is applied once and recorded in
# meta.migrations with its SHA-256; a file that changed after it was applied stops the start, so a
# migration is never edited, only followed by a new one. ClickHouse has no transactions for these
# statements: every statement in a file must be safe to run again (IF NOT EXISTS), so that a file
# that failed half-way is applied whole at the next start.
set -euo pipefail
dir=/clickhouse/migrations
owner() { clickhouse-client --config-file /tmp/clickhouse/owner-client.xml "$@"; }

owner -q "CREATE DATABASE IF NOT EXISTS meta"
owner -q "CREATE TABLE IF NOT EXISTS meta.migrations (name String, sha256 String, applied_at DateTime64(3, 'UTC') DEFAULT now64(3)) ENGINE = MergeTree ORDER BY name"
applied=0
for file in "$dir"/*.sql; do
    [ -e "$file" ] || continue
    name="$(basename "$file")"
    [[ "$name" =~ ^[0-9]{4}_[a-z0-9_]+\.sql$ ]] || { echo "clickhouse: $name is not NNNN_name.sql" >&2; exit 1; }
    sum="$(sha256sum "$file" | cut -d' ' -f1)"
    known="$(owner --param_name="$name" -q "SELECT sha256 FROM meta.migrations WHERE name = {name:String}")"
    if [ -n "$known" ]; then
        [ "$known" = "$sum" ] || { echo "clickhouse: $name changed after it was applied: add a new migration instead" >&2; exit 1; }
        continue
    fi
    owner --queries-file "$file"
    owner --param_name="$name" --param_sum="$sum" -q "INSERT INTO meta.migrations (name, sha256) VALUES ({name:String}, {sum:String})"
    applied=$((applied + 1))
    echo "clickhouse: migration $name applied"
done
echo "clickhouse: migrations up to date ($applied applied now)"
