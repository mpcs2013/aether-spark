# ADR-0020: Research universe — rule-based, survivorship-free, two tiers
- **Status:** Accepted · **Date:** 2026-10-03 (answers roadmap Q3)

## In plain words
The universe is defined by a rule applied at each point in time, not by today's company list. Companies that later went bankrupt, were acquired or shrank stay in, so backtests are not flattered by survivors only.

## Where it applies
| Environment | Universe |
|---|---|
| **PROD — DGX Spark** | The full universe defined below (numbers tier + text tier, FY2011 → present) |
| **DEV — desktop** | Only the dev sample (~100 hard companies × 10 years), a hand-picked subset of the same universe; same rules, schema and code |

## Decision
### Membership rule (point in time)
- A company is in the universe for fiscal year *Y* if its 10-K for *Y* reports **public float ≥ USD 700 M** (`dei:EntityPublicFloat`, i.e. large accelerated filer), using the value as known at that 10-K's acceptance time (`known_at`, ADR-0007). No licensed index-membership data needed.
- Membership is recomputed every year; leaving the universe never deletes history.

### Two tiers
| Tier | Members | Stored | Estimate (measured in P4a) |
|---|---|---|---|
| Numbers | All members (rule above) | All XBRL facts | ~2,000–2,500 companies per year; tens of GB in ClickHouse |
| Text | Top ~1,000 members by public float **per year**, incl. later dropouts | Facts + filing text chunks + embeddings | ~6–8 M chunks (fits ADR-0007 `halfvec(512)` budget) |

### Period
**Fiscal 2011 → present** (~15 years): from FY2011 all filers report XBRL. FY2009–2010 may be added later for the largest filers only.

### Scope v1
- Include: US operating companies filing 10-K/10-Q, incl. delisted, bankrupt and acquired; REITs; banks and insurers (numbers stored; financial-sector analytics templates in a later phase, statements differ).
- Exclude: foreign private issuers (20-F/40-F, IFRS), SPACs and shell companies (SIC 6770), funds/ETFs/BDCs.

### Identity
Companies are keyed by **CIK** everywhere; tickers are time-ranged attributes (ticker changes and reuse handled by validity dates), never keys.

### Dev universe (desktop)
~100 companies × 10 years, chosen to be **hard**: restatements, spin-offs and mergers, bankruptcies/delistings, non-calendar fiscal years (e.g. Apple Sep, Microsoft Jun), banks and REITs, ticker changes. Also the source of the golden test set (WP4.4).

## Consequences
Good: free, survivorship-free, point-in-time universe; storage and vector budgets known. Bad: float is reported once a year (mid-year value), so intra-year size changes are not reflected; float is not market cap (excludes insider holdings) — acceptable for universe membership, not for valuation.
