"""An instance's facts, read by Arelle offline (data-plane.md, sections 2 and 4; PR 4b1). Runs in
the child process only (child.py), after the pre-parse and the reference check.

Arelle resolves the filing's own files in the job folder and every taxonomy file in the read-only
store (taxonomies.py) and never goes online; a taxonomy file missing there fails the job with its
URL. Afterwards every document Arelle loaded must be a job file or a taxonomy URL, or the job fails.

Each numeric, non-nil fact becomes a row: the concept as reported (`prefix:name`, decision D of
2026-11-21), the unit, the exact value, `decimals`, the period read from the filing's own XML
(Arelle reports a date-only end as the next midnight), and the sorted dimensions. A value with
more than six decimal places, or too large for Decimal(38, 6), is counted and not turned into a row
(quarantine comes with PR 5). The fiscal year and period come from the filing's dei facts.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .taxonomies import HOSTS

INSTANCE = "{http://www.xbrl.org/2003/instance}"
DEI = "http://xbrl.sec.gov/dei/"
MAX_PLACES = 6
LIMIT = Decimal(10) ** 32  # Decimal(38, 6): 32 digits before the point


class LoadFailed(Exception):
    """Arelle could not load the filing as it stands; the message says why (no file content)."""


@dataclass
class Parsed:
    rows: list[dict[str, Any]] = field(default_factory=list)
    nil: int = 0
    non_numeric: int = 0
    too_precise: int = 0
    too_large: int = 0
    no_period: int = 0
    fiscal_year: int | None = None
    fiscal_period: str | None = None


def _text_date(context: Any, tag: str) -> dt.date | None:
    element = next(context.iter(f"{INSTANCE}{tag}"), None)
    if element is None or not (element.text or "").strip():
        return None
    return dt.date.fromisoformat(element.text.strip()[:10])


def _unit(fact: Any) -> str:
    numerators, denominators = fact.unit.measures
    unit = "*".join(sorted(str(q) for q in numerators))
    if denominators:
        unit += "/" + "*".join(sorted(str(q) for q in denominators))
    return unit


def _dimensions(context: Any) -> list[tuple[str, str]]:
    pairs = []
    for axis, value in context.qnameDims.items():
        if value.isExplicit:
            member = str(value.memberQname)
        else:
            member = " ".join("".join(value.typedMember.itertext()).split())
        pairs.append((str(axis), member))
    return sorted(pairs)


def _allowed(url: str, folder: Path) -> bool:
    parts = urlsplit(url)
    if parts.scheme in ("http", "https"):
        return parts.hostname in HOSTS
    try:
        return Path(url).resolve().parent == folder.resolve()
    except (OSError, ValueError):
        return False


def load(folder: Path, instance: str, store: Path) -> Parsed:
    """The instance's facts; Arelle offline, its cache the taxonomy store, nothing persistent."""
    from arelle import Cntlr

    controller = Cntlr.Cntlr(logFileName="logToBuffer", disable_persistent_config=True)
    controller.webCache.workOffline = True
    controller.webCache.cacheDir = str(store)
    try:
        model = controller.modelManager.load(str(folder / instance))
        if model is None:
            raise LoadFailed(f"{instance}: Arelle did not load it")
        outside = sorted(u for u in model.urlDocs if not _allowed(u, folder))
        if outside:
            raise LoadFailed(f"loaded a document outside the job and the taxonomies: {outside[0]}")
        problems = [_message(controller)] if model.errors else []
        if problems:
            raise LoadFailed(f"{instance}: {problems[0]}")
        return _facts(model)
    finally:
        controller.close()


def _message(controller: Any) -> str:
    handler = controller.logHandler
    records = getattr(handler, "logRecordBuffer", None) or []
    for record in records:
        if record.levelname in ("ERROR", "CRITICAL"):
            return " ".join(str(record.getMessage()).split())[:300]
    return "Arelle reported errors"


def _facts(model: Any) -> Parsed:
    parsed = Parsed()
    for fact in model.facts:
        if getattr(fact, "isTuple", False):
            continue
        name = fact.qname
        if name.namespaceURI and name.namespaceURI.startswith(DEI):
            if name.localName == "DocumentFiscalYearFocus" and fact.value:
                # A gYear, "2024"; anything else is left unset, and the loader refuses the filing
                year = str(fact.value).strip()
                if len(year) == 4 and year.isascii() and year.isdigit():
                    parsed.fiscal_year = int(year)
            elif name.localName == "DocumentFiscalPeriodFocus" and fact.value:
                parsed.fiscal_period = str(fact.value).strip()
        if not fact.isNumeric:
            parsed.non_numeric += 1
            continue
        if fact.isNil:
            parsed.nil += 1
            continue
        try:
            value = Decimal(str(fact.value).strip())
        except InvalidOperation:
            parsed.too_precise += 1
            continue
        exponent = value.as_tuple().exponent
        places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        if places > MAX_PLACES:
            parsed.too_precise += 1
            continue
        if abs(value) >= LIMIT:
            parsed.too_large += 1
            continue
        context = fact.context
        if context.isInstantPeriod:
            start = end = _text_date(context, "instant")
        else:
            start, end = _text_date(context, "startDate"), _text_date(context, "endDate")
        if start is None or end is None:
            parsed.no_period += 1  # forever, or no period at all
            continue
        decimals = None if str(fact.decimals).strip() in ("INF", "", "None") else int(fact.decimals)
        parsed.rows.append(
            {
                "concept": f"{name.prefix}:{name.localName}",
                "unit": _unit(fact),
                "value": str(value),
                "decimals": decimals,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "dimensions": _dimensions(context),
            }
        )
    return parsed
