-- "As known on D" (docs/architecture/data-plane.md, section 2; ADR-0007 note of 2026-11-19; the
-- owner's choice of 2026-11-21): for each full context (cik, concept, unit, period_start,
-- period_end, dimensions), the row with the latest known_at on or before the given moment. Every
-- reader of point-in-time data goes through this view, and test D1 tests the view itself, so the
-- rule that keeps a backtest from seeing the future lives in one place. A restatement is a later
-- row; both stay in research.facts. Ties at the same known_at (two filings accepted the same
-- millisecond) fall to the later accession number. Use: SELECT ... FROM
-- research.facts_as_of(as_of = '2025-06-30 00:00:00.000')
CREATE VIEW IF NOT EXISTS research.facts_as_of AS
SELECT *
FROM research.facts
WHERE known_at <= {as_of:DateTime64(3, 'UTC')}
ORDER BY cik, concept, unit, period_start, period_end, dimensions, known_at DESC, accession_no DESC
LIMIT 1 BY cik, concept, unit, period_start, period_end, dimensions;
