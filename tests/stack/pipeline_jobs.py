"""The stack test's jobs for the pipeline worker (tests/stack/run.sh; data-plane.md, D5, D6):
one made-up company whose filings are each a case. Standard library only.

    pipeline_jobs.py prepare RAW     writes the cases' files under RAW and prints the SQL for their
                                     company, filings and jobs (run by the migration owner)
    pipeline_jobs.py check RESULTS   compares the jobs' end states (accession|status|attempts|reason
                                     per line) with each case's expected end
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

CIK = 7654321
SECRET = "/run/secrets/postgres-data-pipeline-password"  # noqa: S105 - its path, not a value
KINDS = {"q.htm": "primary", "q_htm.xml": "instance", "q.xsd": "schema"}
GOOD: dict[str, bytes | str] = {
    "q.htm": b"<html><p>q</p></html>",
    "q_htm.xml": b"<xbrl><x>1</x></xbrl>",
    "q.xsd": b"<s/>",
}
BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    b"<xbrl>&c;</xbrl>"
)
REMOTE_DTD = b'<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://sec-stub:8080/evil.dtd"><xbrl/>'
OUTSIDE = ("declares a DTD", "refers to an outside resource")
CHECKED = ("3 files, hashes as in the contract",)


@dataclass(frozen=True)
class Case:
    """A filing's files on disk (a str starting with `link:` is a symbolic link), a change to its
    contract, the job row as written, and the end it must reach (status, attempts, reason parts of
    which one must appear)."""

    status: str
    attempts: int
    reasons: tuple[str, ...]
    files: dict[str, bytes | str] = field(default_factory=lambda: dict(GOOD))
    change: str = ""
    row_status: str = "queued"
    row_attempts: int = 0


CASES = {
    1: Case("done", 1, CHECKED),
    2: Case("failed", 1, ("q_htm.xml: its SHA-256 differs",), change="wrong-hash"),
    3: Case("failed", 1, ("the contract does not hold",), change="traversing-name"),
    4: Case(
        "failed", 1, ("q_htm.xml: cannot be opened",), files={**GOOD, "q_htm.xml": "link:" + SECRET}
    ),
    5: Case("failed", 1, OUTSIDE, files={**GOOD, "q_htm.xml": BOMB}),
    6: Case("failed", 1, OUTSIDE, files={**GOOD, "q_htm.xml": REMOTE_DTD}),
    7: Case("failed", 1, ("q.xsd: not in the raw folder",), change="missing-file"),
    # A worker died with it once: back in the queue, then done on the second attempt
    8: Case("done", 2, CHECKED, row_status="running", row_attempts=1),
    # ... twice: failed, never tried a third time
    9: Case("failed", 2, ("stuck in running twice",), row_status="running", row_attempts=2),
}


def accession(case: int) -> str:
    return f"{CIK:010d}-26-{case:06d}"


def _files(folder: Path, case: Case) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for name, body in case.files.items():
        path = folder / name
        if isinstance(body, str):
            path.symlink_to(body.removeprefix("link:"))
            digest, size = "0" * 64, 10
        else:
            if not (case.change == "missing-file" and name == "q.xsd"):
                path.write_bytes(body)
            digest, size = hashlib.sha256(body).hexdigest(), len(body)
        entries.append({"name": name, "kind": KINDS[name], "sha256": digest, "size": size})
    if case.change == "wrong-hash":
        entries[1]["sha256"] = "f" * 64
    if case.change == "traversing-name":
        entries[2]["name"] = "../../run/secrets/x"
    return entries


def prepare(raw: Path) -> str:
    # The SQL is built only from this file's constants and the contracts it writes itself (S608)
    sql = [f"insert into ref.issuers values ({CIK}, 'Pipeline Test Co', now(), now());"]  # noqa: S608
    for number, case in CASES.items():
        acc = accession(number)
        folder = raw / str(CIK) / acc
        folder.mkdir(parents=True, exist_ok=True)
        contract = {
            "version": 1,
            "accession_no": acc,
            "cik": CIK,
            "form": "10-Q",
            "known_at": "2026-01-30T21:00:00Z",
            "filed_date": "2026-01-30",
            "files": _files(folder, case),
        }
        started = "now() - interval '2 hours'" if case.row_status == "running" else "null"
        sql.append(
            f"insert into ref.filings values ('{acc}', {CIK}, '10-Q', "  # noqa: S608
            "'2026-01-30T21:00:00Z', '2026-01-30', null, now());"
        )
        sql.append(
            "insert into ingest_ops.job_runs"  # noqa: S608
            " (accession_no, contract, status, attempts, created_at, started_at)"
            f" values ('{acc}', $j${json.dumps(contract)}$j$::jsonb, '{case.row_status}',"
            f" {case.row_attempts}, now(), {started});"
        )
    return "\n".join(sql)


def check(results: Path) -> list[str]:
    got: dict[str, tuple[str, int, str]] = {}
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            acc, status, attempts, reason = line.split("|", 3)
            got[acc] = (status, int(attempts), reason)
    problems = []
    for number, case in CASES.items():
        acc = accession(number)
        if acc not in got:
            problems.append(f"case {number}: no job {acc}")
            continue
        ended, tries, why = got[acc]
        if (ended, tries) != (case.status, case.attempts) or not any(
            r in why for r in case.reasons
        ):
            problems.append(
                f"case {number}: {ended} after {tries} attempts ({why}); expected "
                f"{case.status} after {case.attempts} with one of {case.reasons}"
            )
    return problems


def main(argv: list[str]) -> int:
    match argv:
        case ["prepare", raw]:
            print(prepare(Path(raw)))
            return 0
        case ["check", results]:
            problems = check(Path(results))
            for problem in problems:
                print(problem)
            return 1 if problems else 0
        case _:
            print(__doc__, file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
