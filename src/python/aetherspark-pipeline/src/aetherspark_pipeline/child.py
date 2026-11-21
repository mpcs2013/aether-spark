"""The parse of one job, run as a child process under limits (sandbox.py). PR 4a: a hardened
pre-parse of the job's XML files (instance, schemas, linkbases) before any XBRL processor sees them:
no DTD and so no entities, no network, no huge trees. Arelle comes with PR 4b, after this.

Reports one JSON line on stdout: {"ok": true|false, "reason": "..."}; exits 0, 2 (refused) or 3
(memory). Messages name files and the parser's error, never file content."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lxml import etree

EXIT_REFUSED = 2
EXIT_MEMORY = 3
XML_SUFFIXES = (".xml", ".xsd")


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


def check(folder: Path) -> tuple[bool, str]:
    """Pre-parses each XML file of the folder; the first problem refuses the job."""
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
        checked += 1
    return True, f"{checked} XML files pre-parsed"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(json.dumps({"ok": False, "reason": "usage: child <job folder>"}))
        return EXIT_REFUSED
    try:
        ok, reason = check(Path(argv[0]))
    except MemoryError:
        return EXIT_MEMORY
    print(json.dumps({"ok": ok, "reason": reason}))
    return 0 if ok else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
