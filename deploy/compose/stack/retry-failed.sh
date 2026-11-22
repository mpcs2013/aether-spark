#!/usr/bin/env bash
# `just retry-failed` (data-plane.md, section 4): every failed pipeline job goes back to the queue,
# for after a pipeline change that may let it through. The worker takes the jobs again in their
# order; a job that still fails ends failed again with its reason, and a filing processed again adds
# no fact (D2). Runs where the stack runs, after `just up`. It signs in as the data Postgres's
# migration owner on the server's own socket inside its container, as migrate.sh does: the pipeline
# may only end jobs, and the ingester only creates them.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../.." && pwd -P)"
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
container="$(docker compose -f "$repo/deploy/compose/stack/compose.yaml" ps -q postgres-data)"
[ -n "$container" ] || {
    echo "retry-failed: the data Postgres is not running: just up first" >&2
    exit 2
}
# The old reason stays in the new one until the worker writes its own
docker exec "$container" bash -c '
    umask 077
    trap "rm -f /tmp/owner.pgpass" EXIT
    { printf "*:*:*:data_owner:"; cat /run/secrets/postgres-data-owner-password; printf "\n"; } >/tmp/owner.pgpass
    PGPASSFILE=/tmp/owner.pgpass psql -h /var/run/postgresql -U data_owner -d research -X -q -t -A -v ON_ERROR_STOP=1 -c "
        WITH queued AS (
            UPDATE ingest_ops.job_runs
               SET status = '"'"'queued'"'"', attempts = 0, started_at = NULL, finished_at = NULL,
                   reason = left('"'"'queued again by the owner after: '"'"' || coalesce(reason, '"'"''"'"'), 1000)
             WHERE status = '"'"'failed'"'"'
            RETURNING id, accession_no)
        SELECT count(*) || '"'"' failed jobs queued again'"'"' || coalesce('"'"': '"'"' || string_agg(id || '"'"' ('"'"' || accession_no || '"'"')'"'"', '"'"', '"'"' ORDER BY id), '"'"''"'"')
          FROM queued"' | sed 's/^/retry-failed: /'
