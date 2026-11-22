"""Checks for the stack test's SEC section (tests/stack/run.sh): what the ingester asked the stub
SEC (stub_sec.py) for, and what it stored. Standard library only.

    sec_stub_check.py requests LOG FROM AGENT    the first run's requests (log lines FROM on)
    sec_stub_check.py again LOG FROM             a second run asks for no stored filing
    sec_stub_check.py stored RAW CONTRACTS FILES  contracts, raw files and ref.filing_files agree
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

CIK = 1234567
PARTNER = 2345678  # lists only A, the joint 10-K: its index is its one request
FOLDER = f"/Archives/edgar/data/{CIK}/"
A, B, C, D, E = (f"0001234567-25-00000{n}" for n in range(1, 6))
STORED = {
    A: ["stub-20241231.htm", "stub-20241231_htm.xml", "stub-20241231.xsd", "stub-20241231_lab.xml"],
    B: ["stub-20250630.htm", "stub-20250630_htm.xml", "stub-20250630.xsd"],
}
# Arrival times at the stub: a request may arrive a little after it left, so the window allows 30 ms
SECOND = 1.0 - 0.03


def _path(accession: str, name: str) -> str:
    return FOLDER + accession.replace("-", "") + "/" + name


def _expected() -> Counter[str]:
    want: Counter[str] = Counter(
        {f"/submissions/CIK{CIK:010d}.json": 1, f"/submissions/CIK{PARTNER:010d}.json": 1}
    )
    for acc in (A, B, C, D, E):
        want[_path(acc, acc + ".hdr.sgml")] += 1
    for acc in (A, B, C, E):
        want[_path(acc, "index.json")] += 1
    for acc, names in STORED.items():
        for name in names:
            want[_path(acc, name)] += 1
    for name in ("stub-ka.htm", "stub-ka_htm.xml", "stub-ka.xsd"):
        want[_path(E, name)] += 1
    # The 429 and the 503 were retried, once each
    want[_path(B, B + ".hdr.sgml")] += 1
    want[_path(B, "stub-20250630_htm.xml")] += 1
    return want


def _read(log: str, start: int) -> list[dict[str, Any]]:
    lines = Path(log).read_text(encoding="utf-8").splitlines()[start:]
    return [json.loads(line) for line in lines]


def requests(log: str, start: int, agent: str) -> list[str]:
    seen = _read(log, start)
    problems = []
    got = Counter(r["path"] for r in seen)
    if got != _expected():
        extra = sorted((got - _expected()).elements())
        missing = sorted((_expected() - got).elements())
        problems.append(f"requests differ: extra {extra}, missing {missing}")
    if any(re.search(r"evil|escape|secrets|proc", r["path"]) for r in seen):
        problems.append("a request followed a link, a schema location, a redirect or a path")
    agents = {r["agent"] for r in seen}
    if agents != {agent}:
        problems.append(f"User-Agents: {len(agents)} different, expected only the declared one")
    times = sorted(float(r["at"]) for r in seen)
    for i in range(len(times) - 5):
        if times[i + 5] - times[i] < SECOND:
            problems.append(f"6 requests within {times[i + 5] - times[i]:.3f} s (from request {i})")
            break
    return problems


def again(log: str, start: int) -> list[str]:
    seen = [r["path"] for r in _read(log, start)]
    for cik in (CIK, PARTNER):
        if f"/submissions/CIK{cik:010d}.json" not in seen:
            return [f"the second run did not read {cik}'s index"]
    stored = [p for p in seen if any(acc.replace("-", "") in p for acc in STORED)]
    return [f"the second run asked for stored filings: {stored}"] if stored else []


def stored(raw: str, contracts: str, files: str) -> list[str]:
    problems = []
    rows = {tuple(line.split(" ")) for line in Path(files).read_text().splitlines() if line}
    from_contracts = set()
    jobs = [json.loads(line) for line in Path(contracts).read_text().splitlines() if line]
    if sorted(j["accession_no"] for j in jobs) != sorted(STORED):
        problems.append(f"jobs: {sorted(j['accession_no'] for j in jobs)}")
    for job in jobs:
        acc = job["accession_no"]
        if job["version"] != 1 or job["cik"] != CIK:
            problems.append(f"{acc}: version or CIK")
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", job["known_at"]
        ):
            problems.append(f"{acc}: known_at {job['known_at']}")
        if [f["name"] for f in job["files"]] != STORED.get(acc):
            problems.append(f"{acc}: files {[f['name'] for f in job['files']]}")
        for f in job["files"]:
            data = (Path(raw) / str(CIK) / acc / f["name"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != f["sha256"] or len(data) != f["size"]:
                problems.append(f"{acc}/{f['name']}: the stored file's hash or size differs")
            from_contracts.add((acc, f["name"], f["kind"], f["sha256"], str(f["size"])))
    late = next((j for j in jobs if j["accession_no"] == B), None)
    if late and (late["known_at"], late["filed_date"]) != ("2025-07-31T22:03:34Z", "2025-08-01"):
        problems.append(f"after-hours filing: {late['known_at']} filed {late['filed_date']}")
    if rows != from_contracts:
        problems.append("ref.filing_files differs from the contracts")
    # The raw folder holds the recorded files and nothing else: a filing refused while its files
    # were being stored (E, at its schema's redirect) leaves none behind, nor a partial file
    company = Path(raw) / str(CIK)
    on_disk = {
        (p.parent.name, p.name) for p in company.glob("*/*") if p.is_file() or p.is_symlink()
    }
    folders = sorted(p.name for p in company.iterdir())
    recorded = {(acc, name) for acc, names in STORED.items() for name in names}
    if folders != sorted(STORED):
        problems.append(f"filing folders in the raw folder: {folders}")
    if on_disk != recorded:
        problems.append(f"files not recorded: {sorted(on_disk - recorded)}")
    return problems


def main(argv: list[str]) -> int:
    match argv:
        case ["requests", log, start, agent]:
            problems = requests(log, int(start), agent)
        case ["again", log, start]:
            problems = again(log, int(start))
        case ["stored", raw, contracts, files]:
            problems = stored(raw, contracts, files)
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
