-- The research database's schemas and the runtime roles' rights (docs/architecture/data-plane.md,
-- decision 7). Applied once by migrate.sh as data_owner, the migration owner. The runtime roles get
-- rows, never DDL: no CREATE on any schema, so they cannot create, alter or drop a table.
--   ref, ingest_ops: the ingester's (reference data, filings; its Wolverine tables and job_runs)
--   pipeline:        the pipeline's (membership, anomalies, chunks)
-- The pipeline's rights on ingest_ops.job_runs (select, update of its status columns) come with that
-- table's migration; USAGE alone reads nothing.
CREATE SCHEMA ref AUTHORIZATION data_owner;
CREATE SCHEMA ingest_ops AUTHORIZATION data_owner;
CREATE SCHEMA pipeline AUTHORIZATION data_owner;

GRANT USAGE ON SCHEMA ref, ingest_ops TO ingester;
GRANT USAGE ON SCHEMA pipeline, ingest_ops TO pipeline;

ALTER DEFAULT PRIVILEGES FOR ROLE data_owner IN SCHEMA ref, ingest_ops
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ingester;
ALTER DEFAULT PRIVILEGES FOR ROLE data_owner IN SCHEMA ref, ingest_ops
    GRANT USAGE, SELECT ON SEQUENCES TO ingester;
ALTER DEFAULT PRIVILEGES FOR ROLE data_owner IN SCHEMA pipeline
    GRANT SELECT, INSERT ON TABLES TO pipeline;
ALTER DEFAULT PRIVILEGES FOR ROLE data_owner IN SCHEMA pipeline
    GRANT USAGE, SELECT ON SEQUENCES TO pipeline;
