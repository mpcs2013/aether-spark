"""The loader (PR 4b2): fiscal year and period required, the same fact twice in one filing, six
decimal places never rounded, the hash, and D2 on the worker's side (a second load adds nothing)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

import pytest
from aetherspark_common.contracts.filing_job import FilingJob
from aetherspark_pipeline.facts import LoadRefused, fact_hash, load, prepare, six_places

JOB = FilingJob.model_validate(
    {
        "version": 1,
        "accession_no": "0000320193-24-000123",
        "cik": 320193,
        "form": "10-K",
        "known_at": "2024-11-01T20:30:15Z",
        "filed_date": "2024-11-01",
        "files": [{"name": "a.htm", "kind": "primary", "sha256": "0" * 64, "size": 1}],
    }
)
REPORT = {"ok": True, "facts": 0, "fiscal_year": 2024, "fiscal_period": "FY"}


def fact(value: str = "100", decimals: int | None = -6, **changes: Any) -> str:
    row = {
        "concept": "us-gaap:Revenues",
        "unit": "iso4217:USD",
        "value": value,
        "decimals": decimals,
        "period_start": "2023-10-01",
        "period_end": "2024-09-28",
        "dimensions": [],
        **changes,
    }
    return json.dumps(row)


class Store:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.inserts = 0

    def hashes(self, cik: int, accession_no: str) -> set[str]:
        return {
            r["fact_hash"]
            for r in self.rows
            if (r["cik"], r["accession_no"]) == (cik, accession_no)
        }

    def insert(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self.inserts += 1
        self.rows.extend(dict(r) for r in rows)


def test_a_row_carries_the_filings_columns() -> None:
    prepared = prepare(JOB, REPORT, [fact(dimensions=[["co:SegmentAxis", "co:EuropeMember"]])])

    (row,) = prepared.rows
    assert row == {
        "cik": 320193,
        "accession_no": "0000320193-24-000123",
        "form": "10-K",
        "concept": "us-gaap:Revenues",
        "unit": "iso4217:USD",
        "value": "100.000000",
        "decimals": -6,
        "period_start": "2023-10-01",
        "period_end": "2024-09-28",
        "dimensions": [{"axis": "co:SegmentAxis", "member": "co:EuropeMember"}],
        "known_at": "2024-11-01 20:30:15.000",
        "filed_date": "2024-11-01",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "fact_hash": row["fact_hash"],
    }
    assert len(row["fact_hash"]) == 64


@pytest.mark.parametrize(
    ("report", "message"),
    [
        ({"fiscal_year": None, "fiscal_period": "FY"}, "no fiscal year in its dei facts (None)"),
        ({"fiscal_year": 24, "fiscal_period": "FY"}, "no fiscal year in its dei facts (24)"),
        ({"fiscal_year": 2024, "fiscal_period": None}, "fiscal period None is not FY or Q1-Q4"),
        ({"fiscal_year": 2024, "fiscal_period": "H1"}, "fiscal period 'H1' is not FY or Q1-Q4"),
    ],
)
def test_the_fiscal_year_and_period_are_required(report: dict[str, Any], message: str) -> None:
    with pytest.raises(LoadRefused, match=re.escape(message)):
        prepare(JOB, report, [fact()])


@pytest.mark.parametrize("period", ["FY", "Q1", "Q2", "Q3", "Q4"])
def test_every_measured_period_is_taken(period: str) -> None:
    assert prepare(JOB, {**REPORT, "fiscal_period": period}, [fact()]).rows


def test_equal_duplicates_load_once_with_the_most_precise_decimals() -> None:
    prepared = prepare(
        JOB,
        REPORT,
        [fact("100", -6), fact("100.0", None), fact("100", -3), fact("7", unit="shares")],
    )

    assert prepared.duplicates == 2
    assert prepared.conflicting == 0
    by_unit = {r["unit"]: r for r in prepared.rows}
    assert by_unit["iso4217:USD"]["decimals"] is None  # INF, the most precise
    assert len(prepared.rows) == 2


def test_different_values_for_one_context_are_all_left_out() -> None:
    prepared = prepare(JOB, REPORT, [fact("100"), fact("101"), fact("5", period_end="2023-09-30")])

    assert prepared.conflicting == 2
    assert [r["value"] for r in prepared.rows] == ["5.000000"]


def test_six_places_are_kept_and_more_are_refused_never_rounded() -> None:
    # ClickHouse would round 1.1234567 into Decimal(38, 6) without a word (measured 2026-11-21)
    assert six_places("1.123456") == "1.123456"
    assert six_places("-0.5") == "-0.500000"
    assert six_places("1E+3") == "1000.000000"
    with pytest.raises(LoadRefused, match="more than six decimal places"):
        six_places("1.1234567")
    with pytest.raises(LoadRefused, match="not a number"):
        six_places("NaN-ish")


def test_the_hash_names_the_filing_the_context_and_the_value() -> None:
    base = json.loads(fact())
    base["value"] = "100.000000"
    same = fact_hash(JOB.accession_no, base)
    assert fact_hash(JOB.accession_no, dict(base)) == same
    assert fact_hash("0000320193-25-000001", base) != same
    for change in (
        {"value": "100.000001"},
        {"decimals": -3},
        {"unit": "iso4217:EUR"},
        {"period_start": "2023-10-02"},
        {"dimensions": [["a", "b"]]},
    ):
        assert fact_hash(JOB.accession_no, {**base, **change}) != same, change


def test_a_second_load_of_the_same_filing_adds_nothing() -> None:
    store = Store()
    prepared = prepare(JOB, REPORT, [fact("100"), fact("7", unit="shares")])

    assert load(store, JOB, prepared) == (2, 0)
    assert load(store, JOB, prepared) == (0, 2)
    assert store.inserts == 1
    assert len(store.rows) == 2


def test_a_partly_stored_filing_gets_only_its_missing_rows() -> None:
    store = Store()
    prepared = prepare(JOB, REPORT, [fact("100"), fact("7", unit="shares")])
    store.rows.append(prepared.rows[0])

    assert load(store, JOB, prepared) == (1, 1)
    assert [r["unit"] for r in store.rows] == ["iso4217:USD", "shares"]
