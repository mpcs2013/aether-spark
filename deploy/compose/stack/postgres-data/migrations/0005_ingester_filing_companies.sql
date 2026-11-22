-- The companies whose index lists a filing (docs/architecture/data-plane.md, section 3; owner,
-- 2026-11-22): a joint filing is stored once in ref.filings and listed here by each of its companies.
-- EF Core's migration 20261122082828_FilingCompanies of src/dotnet/AetherSpark.Ingester, generated,
-- never edited:
--   dotnet ef migrations script Initial FilingCompanies --project src/dotnet/AetherSpark.Ingester --no-transactions
-- (migrate.sh runs the file in one transaction). Rights come from 0001's defaults for ref: the
-- ingester reads and writes rows, the pipeline has none.
CREATE TABLE ref.filing_companies (
    accession_no text NOT NULL,
    cik bigint NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    CONSTRAINT "PK_filing_companies" PRIMARY KEY (accession_no, cik),
    CONSTRAINT "FK_filing_companies_filings_accession_no" FOREIGN KEY (accession_no) REFERENCES ref.filings (accession_no) ON DELETE RESTRICT,
    CONSTRAINT "FK_filing_companies_issuers_cik" FOREIGN KEY (cik) REFERENCES ref.issuers (cik) ON DELETE RESTRICT
);

CREATE INDEX "IX_filing_companies_cik" ON ref.filing_companies (cik);

INSERT INTO ref.filing_companies (accession_no, cik, first_seen_at) SELECT accession_no, cik, stored_at FROM ref.filings;

INSERT INTO meta.ef_migrations ("MigrationId", "ProductVersion")
VALUES ('20261122082828_FilingCompanies', '10.0.15');
