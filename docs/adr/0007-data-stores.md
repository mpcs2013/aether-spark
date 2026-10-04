# ADR-0007: Data stores — Postgres 18 + pgvector, ClickHouse; Redis optional
- **Status:** Accepted (with changes 1–3, Redis optional) · **Date:** 2026-10-03

## In plain words
| Store | Think of it as | Holds |
|---|---|---|
| **Postgres 18 + pgvector** | Well-organised filing cabinet | Issuers, filings, lineage, app state, Keycloak DB, LiteLLM usage, filing text chunks + embeddings |
| **ClickHouse** | Huge, very fast spreadsheet | Every reported number (XBRL facts), prices, derived factors |
| **Redis / Valkey** *(optional)* | Sticky-note board | Only if needed later: LiteLLM rate limits and cache |

## Decision
### 1. Embeddings sized to fit the memory budget
- Sizing estimate: text tier of ADR-0020 (~1,000 companies per year, FY2011+) × ~370 chunks per company-year ≈ **6–8 M chunks**.
- 1024-dim float32 ≈ 26 GB with HNSW index → does not fit Postgres's 10 GB budget (c4.md).
- **Store `halfvec(512)`** (half precision, embedding truncated to 512 dims) ≈ 8 GB incl. index.
- Verified on the Spark (P5b): retrieval recall on the golden question set vs. full-size embeddings. Fallbacks, in order: (a) embed only 10-K + footnotes at full size; (b) raise Postgres to 16 GB, taking 6 GB from headroom.

### 2. Point-in-time key = SEC acceptance timestamp
- Every fact carries `known_at` = EDGAR **acceptance datetime**, stored in UTC — not the filing date (filings accepted after 17:30 ET carry the next business day's filing date).
- Every point-in-time query filters `known_at <= as_of`. Also kept: `period_end` (the period the number describes) and `accession_no`.

### 3. Restatements are never overwritten
- ClickHouse fact table: plain **`MergeTree`**, `ORDER BY (cik, concept, period_end, known_at)`, append-only.
- "Value as known on date D" = latest row with `known_at <= D` (e.g. `argMax(value, known_at)`).
- **Not** `ReplacingMergeTree` keyed on (cik, concept, period): it would silently merge a restatement into the original.
- Re-run duplicates are removed in the ingest staging step by `fact_hash` (includes `accession_no`), so re-ingest is idempotent (NFR-DQ-03).
- Reported values are `Decimal(38, s)` (ADR-0002 rule 4).

### 4. Redis optional
Nothing in the design requires it (Wolverine keeps durable state in Postgres). Added only when a concrete need appears (LiteLLM rate limiting/cache), as **Valkey** drop-in, consistent with Decisya ADR-0007.

### Migrations
EF Core 10 migrations (Postgres); versioned SQL scripts (ClickHouse).

## Consequences
- Good: all stores fit the 128 GB budget; backtests are free of filing-date look-ahead; full restatement history.
- Bad: truncated half-precision embeddings may lose some retrieval quality (measured, with fallbacks); append-only tables grow (small for this universe: tens of GB).

## Alternatives considered
Qdrant (better beyond ~50 M vectors; extra service and memory); DuckDB instead of ClickHouse (excellent embedded, weaker for concurrent service access); `ReplacingMergeTree` for facts (rejected, change 3).

## Revisit trigger
Recall test fails both fallbacks, or NFR-PERF-06 missed at target scale → evaluate Qdrant.
