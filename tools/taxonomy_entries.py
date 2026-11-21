"""The taxonomy job's entry list (deploy/taxonomies/entry-points.txt; data-plane.md, section 4):
every schema and linkbase on the three taxonomy hosts that the stored filings refer to directly.

Reads the raw folder only (its company schemas and linkbases), downloads nothing, and never parses a
filing as XML: a plain search of the bytes for absolute schemaLocation and href values is enough
here, since the job itself follows each entry's own references. Run it on the desktop after a walk
season, review the list's difference in a pull request, then run the taxonomy session again
(docs/runbooks/taxonomies.md).

    python -I tools/taxonomy_entries.py RAW_FOLDER [--output FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

HOSTS = frozenset({"xbrl.fasb.org", "xbrl.sec.gov", "www.xbrl.org"})  # as the job's (taxonomies.py)
OUTPUT = Path(__file__).resolve().parents[1] / "deploy" / "taxonomies" / "entry-points.txt"
_REF = re.compile(rb'(?:schemaLocation|href)\s*=\s*"(https?://[^"#\s]+)', re.IGNORECASE)
_LINKBASE = re.compile(r"_(cal|def|lab|pre)\.xml$")


def survey(raw: Path) -> tuple[set[str], int, int]:
    """The absolute URLs the company schemas and linkbases under `raw/<cik>/<accession>/` name, the
    number of filings with such a file, and the number of files read."""
    urls: set[str] = set()
    filings: set[str] = set()
    files = 0
    for path in raw.glob("*/*/*"):
        name = path.name.lower()
        if not (name.endswith(".xsd") or _LINKBASE.search(name)) or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        files += 1
        filings.add(path.parent.name)
        urls.update(m.decode("ascii", "replace") for m in _REF.findall(data))
    return urls, len(filings), files


def entries(urls: Iterable[str]) -> list[str]:
    """The schemas and linkbases on the three hosts, sorted; anything else is left out."""
    kept = set()
    for url in urls:
        parts = urlsplit(url)
        if parts.hostname in HOSTS and parts.path.lower().endswith((".xsd", ".xml")):
            kept.add(url)
    return sorted(kept)


def render(urls: list[str], filings: int, files: int) -> str:
    lines = [
        "The taxonomy job's entry files (data-plane.md, section 4; PR 4b1): every schema and "
        "linkbase on",
        "the three taxonomy hosts that the stored filings refer to directly, measured on "
        f"{filings} filings",
        f"({files} schema and linkbase files) of the dev universe by tools/taxonomy_entries.py. "
        "The job",
        "fetches these and everything they refer to, transitively; never anything named only by a "
        "filing.",
        "One URL per line; `#` starts a comment.",
    ]
    return "".join(f"# {line}\n" for line in lines) + "".join(f"{url}\n" for url in urls)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="measure the taxonomy entry list")
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    urls, filings, files = survey(args.raw)
    if not files:
        print(f"no company schema or linkbase under {args.raw}; nothing written")
        return 2
    kept = entries(urls)
    args.output.write_text(render(kept, filings, files), encoding="utf-8", newline="\n")
    others = sorted({urlsplit(u).hostname or u for u in urls} - HOSTS)
    print(f"{len(kept)} entry files from {filings} filings ({files} files read)")
    if others:
        print(f"  referred to but not taxonomy hosts: {', '.join(others)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
