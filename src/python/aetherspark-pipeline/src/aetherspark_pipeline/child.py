"""The parse of one job, run as a child process under limits (sandbox.py), in four steps:

1. a hardened pre-parse of the job's XML files (instance, schemas, linkbases): no DTD and so no
   entities, no network, no huge trees;
2. the reference check: every reference in those files names a file of the job (a plain name) or a
   file on the three taxonomy hosts; anything else (a path, `..`, another host, `file:`) refuses the
   job before Arelle sees it;
3. Arelle, offline, with the read-only taxonomy store as its cache (xbrl.py);
4. the facts, as rows.

Writes one JSON report line on stdout, {"ok": bool, "reason": str, "facts": int}, then one JSON line
per fact; exits 0, 2 (refused) or 3 (memory). Messages name files, URLs and the parser's errors,
never file content.

    python -I -m aetherspark_pipeline.child <job folder> <instance name> <taxonomy store>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

from lxml import etree

from .taxonomies import Refused, raw_references, store_path

EXIT_REFUSED = 2
EXIT_MEMORY = 3
XML_SUFFIXES = (".xml", ".xsd")
_PLAIN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}")


class _Nothing(etree.Resolver):  # type: ignore[misc] # lxml is untyped
    """Every outside reference the parser asks for (a DTD, an entity) resolves to nothing, so it
    reads no file and no address but the one it was given. libxml2 asks for an external DTD even
    without DTD loading (measured on Linux, 2026-11-21); no_network alone only blocks addresses."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

    def resolve(self, url: str, public_id: str | None, context: object) -> object:
        self.asked.append(str(url))
        return self.resolve_string("", context)


def _parser(resolver: _Nothing) -> etree.XMLParser:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        collect_ids=False,
    )
    parser.resolvers.add(resolver)
    return parser


def reference_problem(ref: str, files: set[str]) -> str | None:
    """Why a reference is refused, or None: a job file's plain name (a fragment allowed), or a file
    on the taxonomy hosts as the store would keep it."""
    target, _ = urldefrag(ref)
    if not target:
        return None  # a fragment within the same file
    if urlsplit(target).scheme:
        try:
            store_path(Path("/"), target)
        except Refused as refused:
            return str(refused)
        return None
    if not _PLAIN.fullmatch(target):
        return f"not a file of the job: {target}"
    if target not in files:
        return f"names a file the job does not have: {target}"
    return None


def check(folder: Path) -> tuple[bool, str]:
    """Pre-parses each XML file of the folder and checks its references; the first problem
    refuses the job."""
    files = {p.name for p in folder.iterdir()}
    checked = 0
    for path in sorted(folder.iterdir()):
        if not path.name.lower().endswith(XML_SUFFIXES):
            continue
        resolver = _Nothing()
        try:
            with path.open("rb") as handle:
                tree = etree.parse(handle, _parser(resolver))
        except etree.XMLSyntaxError as error:
            message = " ".join(str(error.msg).split())[:200]
            return False, f"{path.name}: not well-formed XML ({message})"
        except OSError:
            # libxml2 refused to load something from outside the file
            return False, f"{path.name}: refers to an outside resource, refused"
        info = tree.docinfo
        if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
            return False, f"{path.name}: declares a DTD, refused (entities, external references)"
        if resolver.asked:
            return False, f"{path.name}: refers to an outside resource, refused"
        for ref in raw_references(tree.getroot()):
            if problem := reference_problem(ref, files):
                return False, f"{path.name}: a reference refused ({problem})"
        checked += 1
    return True, f"{checked} XML files pre-parsed"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(json.dumps({"ok": False, "reason": "usage: child <folder> <instance> <store>"}))
        return EXIT_REFUSED
    folder, instance, store = Path(argv[0]), argv[1], Path(argv[2])
    try:
        ok, reason = check(folder)
        if not ok:
            print(json.dumps({"ok": False, "reason": reason, "facts": 0}))
            return EXIT_REFUSED
        from .xbrl import LoadFailed, load

        try:
            parsed = load(folder, instance, store)
        except LoadFailed as failed:
            print(json.dumps({"ok": False, "reason": str(failed), "facts": 0}))
            return EXIT_REFUSED
    except MemoryError:
        return EXIT_MEMORY
    left_out = {
        "nil": parsed.nil,
        "more than six decimal places": parsed.too_precise,
        "too large": parsed.too_large,
        "no period": parsed.no_period,
    }
    noted = ", ".join(f"{n} {why}" for why, n in left_out.items() if n)
    summary = f"{reason}; {len(parsed.rows)} numeric facts" + (
        f" ({noted} left out)" if noted else ""
    )
    if parsed.fiscal_year is None or parsed.fiscal_period is None:
        summary += "; no fiscal year or period in its dei facts"
    report = {
        "ok": True,
        "reason": summary,
        "facts": len(parsed.rows),
        "fiscal_year": parsed.fiscal_year,
        "fiscal_period": parsed.fiscal_period,
    }
    out = sys.stdout
    out.write(json.dumps(report) + "\n")
    for row in parsed.rows:
        out.write(json.dumps(row, separators=(",", ":")) + "\n")
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
