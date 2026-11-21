"""The stack test's jobs for the pipeline worker (tests/stack/run.sh; data-plane.md, D5, D6):
one made-up company whose filings are each a case. Standard library only.

    pipeline_jobs.py prepare RAW TAXONOMIES
                                     writes the cases' files under RAW and a made-up taxonomy into
                                     the store TAXONOMIES (where the store keeps a FASB file), and
                                     prints the SQL for their company, filings and jobs (run by the
                                     migration owner)
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
# A made-up taxonomy stands in for US GAAP (as in the worker's tests, test_xbrl.py); Arelle brings
# the XBRL base schemas it imports itself
TAXONOMY = "https://xbrl.fasb.org/test/2024/test-2024.xsd"
TAX_SCHEMA = b"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance"
 targetNamespace="http://fasb.org/test/2024" elementFormDefault="qualified">
 <xs:import namespace="http://www.xbrl.org/2003/instance"
   schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
 <xs:element name="Revenues" id="test_Revenues" type="xbrli:monetaryItemType"
   substitutionGroup="xbrli:item" xbrli:periodType="duration" nillable="true"/>
</xs:schema>
"""


def company(imports: str) -> bytes:
    """The filing's own schema, importing the taxonomy from `imports`."""
    return (
        '<?xml version="1.0"?><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
        ' targetNamespace="http://example.com/q" elementFormDefault="qualified">'
        f'<xs:import namespace="http://fasb.org/test/2024" schemaLocation="{imports}"/></xs:schema>'
    ).encode()


INSTANCE = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase"
 xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:test="http://fasb.org/test/2024">
 <link:schemaRef xlink:type="simple" xlink:href="q.xsd"/>
 <xbrli:context id="q4"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0007654321</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-10-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="q3"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0007654321</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <test:Revenues contextRef="q4" unitRef="usd" decimals="-3">1250000</test:Revenues>
 <test:Revenues contextRef="q3" unitRef="usd" decimals="-3">1175000</test:Revenues>
</xbrli:xbrl>
"""
GOOD: dict[str, bytes | str] = {
    "q.htm": b"<html><p>q</p></html>",
    "q_htm.xml": INSTANCE,
    "q.xsd": company(TAXONOMY),
}
BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    b"<xbrl>&c;</xbrl>"
)
REMOTE_DTD = b'<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://sec-stub:8080/evil.dtd"><xbrl/>'
OUTSIDE = ("declares a DTD", "refers to an outside resource")
CHECKED = ("3 files, hashes as in the contract; 2 XML files pre-parsed; 2 numeric facts",)


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
    # The taxonomy (PR 4b1): a file the store lacks fails with its URL, never fetched; an import
    # that climbs out of the job folder, or one from a host other than the three, refused before
    # Arelle reads anything
    10: Case(
        "failed",
        1,
        ("Could not load file",),
        files={**GOOD, "q.xsd": company("https://xbrl.fasb.org/test/2024/missing-2024.xsd")},
    ),
    11: Case(
        "failed",
        1,
        ("q.xsd: a reference refused (not a file of the job",),
        files={**GOOD, "q.xsd": company("../../../taxonomies/https/xbrl.fasb.org/test-2024.xsd")},
    ),
    12: Case(
        "failed",
        1,
        ("q.xsd: a reference refused (not a taxonomy host",),
        files={**GOOD, "q.xsd": company("https://sec-stub:8080/test-2024.xsd")},
    ),
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


def store_taxonomy(store: Path) -> None:
    """The made-up taxonomy where the taxonomy job would keep it: <store>/<scheme>/<host>/<path>."""
    scheme, rest = TAXONOMY.split("://", 1)
    path = store.joinpath(scheme, *rest.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TAX_SCHEMA)


def prepare(raw: Path, taxonomies: Path) -> str:
    store_taxonomy(taxonomies)
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
        case ["prepare", raw, taxonomies]:
            print(prepare(Path(raw), Path(taxonomies)))
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
