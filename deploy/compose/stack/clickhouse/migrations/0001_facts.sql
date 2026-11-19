-- The fact table (docs/architecture/data-plane.md, section 2; ADR-0007 changes 2 and 3): every
-- reported number, append-only. A restatement is a new row with a later known_at, never an update;
-- "value as known on D" is the row with the latest known_at <= D per full context
-- (cik, concept, unit, period_start, period_end, dimensions).
CREATE DATABASE IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS research.facts
(
    cik            UInt32,
    accession_no   String,                          -- 0000000000-00-000000
    form           LowCardinality(String),          -- 10-K, 10-Q, 10-K/A, 10-Q/A
    concept        LowCardinality(String),          -- namespace:name
    unit           LowCardinality(String),
    value          Decimal(38, 6),                  -- exact; more places are quarantined, never rounded
    decimals       Nullable(Int16),                 -- as reported; NULL for INF
    period_start   Date,                            -- equal to period_end for an instant
    period_end     Date,
    dimensions     Array(Tuple(axis String, member String)),  -- sorted; empty for the default context
    known_at       DateTime64(3, 'UTC'),            -- the SEC's acceptance datetime
    filed_date     Date,
    fiscal_year    UInt16,
    fiscal_period  LowCardinality(String),          -- FY, Q1, Q2, Q3, Q4
    fact_hash      FixedString(64),                 -- SHA-256 (hex), accession number included
    ingested_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (cik, concept, period_end, known_at);
