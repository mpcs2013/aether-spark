-- The ingester's tables (docs/architecture/data-plane.md, sections 1 and 3): EF Core's migration
-- 20261119212245_Initial of src/dotnet/AetherSpark.Ingester, generated, never edited:
--   dotnet ef migrations script 0 Initial --project src/dotnet/AetherSpark.Ingester --no-transactions
-- (migrate.sh runs the file in one transaction). The ingester refuses to start while an EF
-- migration is missing from meta.ef_migrations.
DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname = 'meta') THEN
        CREATE SCHEMA meta;
    END IF;
END $EF$;
CREATE TABLE IF NOT EXISTS meta.ef_migrations (
    "MigrationId" character varying(150) NOT NULL,
    "ProductVersion" character varying(32) NOT NULL,
    CONSTRAINT "PK_ef_migrations" PRIMARY KEY ("MigrationId")
);

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname = 'ref') THEN
        CREATE SCHEMA ref;
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname = 'ingest_ops') THEN
        CREATE SCHEMA ingest_ops;
    END IF;
END $EF$;

CREATE TABLE ref.issuers (
    cik bigint NOT NULL,
    name text NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT "PK_issuers" PRIMARY KEY (cik),
    CONSTRAINT ck_issuers_cik CHECK (cik BETWEEN 1 AND 9999999999)
);

CREATE TABLE ref.filings (
    accession_no text NOT NULL,
    cik bigint NOT NULL,
    form text NOT NULL,
    known_at timestamp with time zone NOT NULL,
    filed_date date NOT NULL,
    period_of_report date,
    stored_at timestamp with time zone NOT NULL,
    CONSTRAINT "PK_filings" PRIMARY KEY (accession_no),
    CONSTRAINT ck_filings_accession_no CHECK (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
    CONSTRAINT "FK_filings_issuers_cik" FOREIGN KEY (cik) REFERENCES ref.issuers (cik) ON DELETE RESTRICT
);

CREATE TABLE ref.tickers (
    cik bigint NOT NULL,
    symbol text NOT NULL,
    exchange text NOT NULL,
    valid_from date NOT NULL,
    valid_to date,
    CONSTRAINT "PK_tickers" PRIMARY KEY (cik, symbol, exchange, valid_from),
    CONSTRAINT ck_tickers_validity CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT "FK_tickers_issuers_cik" FOREIGN KEY (cik) REFERENCES ref.issuers (cik) ON DELETE RESTRICT
);

CREATE TABLE ref.filing_files (
    accession_no text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL,
    CONSTRAINT "PK_filing_files" PRIMARY KEY (accession_no, name),
    CONSTRAINT ck_filing_files_kind CHECK (kind IN ('primary', 'instance', 'schema', 'linkbase')),
    CONSTRAINT ck_filing_files_name CHECK (name ~ '^[A-Za-z0-9._-]+$'),
    CONSTRAINT ck_filing_files_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_filing_files_size CHECK (size >= 0),
    CONSTRAINT "FK_filing_files_filings_accession_no" FOREIGN KEY (accession_no) REFERENCES ref.filings (accession_no) ON DELETE RESTRICT
);

CREATE TABLE ingest_ops.job_runs (
    id bigint GENERATED ALWAYS AS IDENTITY,
    accession_no text NOT NULL,
    contract jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    reason text,
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT "PK_job_runs" PRIMARY KEY (id),
    CONSTRAINT ck_job_runs_attempts CHECK (attempts >= 0),
    CONSTRAINT ck_job_runs_status CHECK (status IN ('queued', 'running', 'done', 'failed')),
    CONSTRAINT "FK_job_runs_filings_accession_no" FOREIGN KEY (accession_no) REFERENCES ref.filings (accession_no) ON DELETE RESTRICT
);

CREATE INDEX "IX_filings_cik_known_at" ON ref.filings (cik, known_at);

CREATE UNIQUE INDEX "IX_job_runs_accession_no" ON ingest_ops.job_runs (accession_no);

CREATE INDEX "IX_job_runs_status" ON ingest_ops.job_runs (status);

CREATE INDEX "IX_tickers_symbol" ON ref.tickers (symbol);

INSERT INTO meta.ef_migrations ("MigrationId", "ProductVersion")
VALUES ('20261119212245_Initial', '10.0.15');

