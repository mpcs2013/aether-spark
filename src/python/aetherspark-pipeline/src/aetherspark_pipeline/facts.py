"""A filing's facts into research.facts (data-plane.md, section 4 "Loading"; PR 4b2, the owner's
choices of 2026-11-21).

- **Fiscal year and period** come from the filing's dei facts, as reported, and are required: a
  filing without them, or with a period other than FY or Q1-Q4, is refused (all 3,607 stored
  filings had them, measured 2026-11-21).
- **The same fact twice in one filing** (the same concept, unit, period and dimensions): equal
  values are loaded once, with the most precise `decimals`; different values are left out, all of
  them, and counted (PR 5 records them as anomalies).
- **`fact_hash`**: the SHA-256 of the accession number, the full context, the value (six places)
  and `decimals`. Before the insert the loader reads the hashes already stored for the filing and
  inserts only the others, in one insert, which ClickHouse writes whole or not at all. So a job run
  again, or one whose worker died after the insert, adds nothing (D2). This holds because one worker
  handles a filing at a time (the job claim, jobs.py); a second worker on the same filing could load
  it twice.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from aetherspark_common.contracts.filing_job import FilingJob

PERIODS = frozenset({"FY", "Q1", "Q2", "Q3", "Q4"})
SIX_PLACES = Decimal("0.000001")


class LoadRefused(Exception):
    """The filing's facts cannot be loaded as they are; the message says why."""


class FactStore(Protocol):
    def hashes(self, cik: int, accession_no: str) -> set[str]: ...

    def insert(self, rows: Iterable[Mapping[str, Any]]) -> None: ...


@dataclass(frozen=True)
class Prepared:
    rows: list[dict[str, Any]]
    duplicates: int  # equal facts loaded once
    conflicting: int  # facts left out: the same context with different values


def six_places(value: str) -> str:
    """The value with exactly six decimal places, never rounded: more places are refused."""
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise LoadRefused(f"not a number: {value[:40]!r}") from None
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or (exponent < 0 and -exponent > 6):
        raise LoadRefused(f"more than six decimal places: {value[:40]!r}")
    return format(number.quantize(SIX_PLACES), "f")


def _precision(decimals: int | None) -> float:
    return float("inf") if decimals is None else float(decimals)


def fact_hash(accession_no: str, row: Mapping[str, Any]) -> str:
    identity = [
        accession_no,
        row["concept"],
        row["unit"],
        row["period_start"],
        row["period_end"],
        [list(pair) for pair in row["dimensions"]],
        row["value"],
        row["decimals"],
    ]
    canonical = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def prepare(job: FilingJob, report: Mapping[str, Any], lines: Iterable[str]) -> Prepared:
    """The rows for research.facts: the child's facts with the filing's own columns."""
    fiscal_year, fiscal_period = report.get("fiscal_year"), report.get("fiscal_period")
    if not isinstance(fiscal_year, int) or not 1900 <= fiscal_year <= 2100:
        raise LoadRefused(f"no fiscal year in its dei facts ({fiscal_year!r})")
    if fiscal_period not in PERIODS:
        raise LoadRefused(f"fiscal period {fiscal_period!r} is not FY or Q1-Q4")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for line in lines:
        fact = json.loads(line)
        fact["value"] = six_places(fact["value"])
        dims = tuple(tuple(pair) for pair in fact["dimensions"])
        key = (fact["concept"], fact["unit"], fact["period_start"], fact["period_end"], dims)
        groups.setdefault(key, []).append(fact)
    known_at = job.known_at.replace("T", " ").removesuffix("Z") + ".000"
    rows: list[dict[str, Any]] = []
    duplicates = conflicting = 0
    for same in groups.values():
        if len({f["value"] for f in same}) > 1:
            conflicting += len(same)
            continue
        duplicates += len(same) - 1
        fact = max(same, key=lambda f: _precision(f["decimals"]))
        rows.append(
            {
                "cik": job.cik,
                "accession_no": job.accession_no,
                "form": job.form,
                "concept": fact["concept"],
                "unit": fact["unit"],
                "value": fact["value"],
                "decimals": fact["decimals"],
                "period_start": fact["period_start"],
                "period_end": fact["period_end"],
                "dimensions": [{"axis": a, "member": m} for a, m in fact["dimensions"]],
                "known_at": known_at,
                "filed_date": job.filed_date,
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "fact_hash": fact_hash(job.accession_no, fact),
            }
        )
    return Prepared(rows, duplicates, conflicting)


def load(store: FactStore, job: FilingJob, prepared: Prepared) -> tuple[int, int]:
    """Inserts the rows not stored yet; returns (inserted, already stored)."""
    stored = store.hashes(job.cik, job.accession_no)
    new = [row for row in prepared.rows if row["fact_hash"] not in stored]
    if new:
        store.insert(new)
    return len(new), len(prepared.rows) - len(new)
