-- Rights on the ingester's tables beyond 0001's defaults (docs/architecture/data-plane.md, decision 7).
--   pipeline: on ingest_ops.job_runs, read the jobs and update only their status columns; nothing
--             on ref and on Wolverine's tables (0001 granted it USAGE on ingest_ops, which reads nothing)
--   ingester: read meta.ef_migrations, so it can refuse to start while a migration is missing; nothing
--             else in meta (meta.migrations stays the migration owner's)
REVOKE ALL ON ingest_ops.job_runs FROM pipeline;
GRANT SELECT ON ingest_ops.job_runs TO pipeline;
GRANT UPDATE (status, reason, attempts, started_at, finished_at) ON ingest_ops.job_runs TO pipeline;

GRANT USAGE ON SCHEMA meta TO ingester;
GRANT SELECT ON meta.ef_migrations TO ingester;
