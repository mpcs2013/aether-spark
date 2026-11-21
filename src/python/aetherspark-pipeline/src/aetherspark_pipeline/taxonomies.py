"""The taxonomy store (data-plane.md, section 4; egress.md, "Model store and taxonomies"; PR 4b1,
the owner's choice of 2026-11-21): the XBRL taxonomies the filings refer to, fetched once in an ops
session and read offline by Arelle in the worker.

The job starts from a committed list of entry files (deploy/taxonomies/entry-points.txt, measured
from the stored filings), never from a filing. Each file is fetched over HTTPS through the ops proxy
from three hosts only, stored where Arelle's offline cache looks for its URL
(`<store>/<scheme>/<host>/<path>`; an `http://` address is fetched over HTTPS and stored under
`http/`), and read for the files it refers to in turn (imports, includes, linkbases, locators),
until nothing new is referred to. A manifest records every file's SHA-256 (`MANIFEST.sha256`).

    python -I -m aetherspark_pipeline.taxonomies --entries FILE --store DIR --contact-file FILE
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import etree

HOSTS = frozenset({"xbrl.fasb.org", "xbrl.sec.gov", "www.xbrl.org"})
XLINK = "{http://www.w3.org/1999/xlink}href"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
MIB = 1024 * 1024
_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")


class Refused(Exception):
    """A URL or file the store does not take; the message names it."""


@dataclass(frozen=True)
class Limits:
    max_files: int = 20_000
    max_file_bytes: int = 64 * MIB
    max_total_bytes: int = 8 * 1024 * MIB


@dataclass
class Report:
    fetched: int = 0
    kept: int = 0
    bytes: int = 0
    skipped: dict[str, set[str]] = field(default_factory=dict)  # outside host -> referring files
    failed: dict[str, str] = field(default_factory=dict)  # url -> reason
    stopped: str = ""  # why the crawl stopped before the end (a host refused this client)


def store_path(store: Path, url: str) -> Path:
    """Where Arelle's offline cache looks for `url` (WebCache.urlToCacheFilepath): the scheme, the
    host and the path. Only the three hosts, and only plain path segments: no `..`, no query."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in HOSTS:
        raise Refused(f"not a taxonomy host: {url}")
    if parts.query or parts.port is not None or parts.username or parts.password:
        raise Refused(f"not a plain taxonomy URL: {url}")
    segments = [s for s in parts.path.split("/") if s]
    if not segments or any(s in (".", "..") or not _SEGMENT.fullmatch(s) for s in segments):
        raise Refused(f"not a plain taxonomy path: {url}")
    return store.joinpath(parts.scheme, parts.hostname, *segments)


def _parser() -> etree.XMLParser:
    # The same hardening as the worker's pre-parse: no DTD, no entities, no network
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, collect_ids=False
    )


def raw_references(root: etree._Element) -> list[str]:
    """Every reference a schema, linkbase or instance makes, as written: xs:import and xs:include,
    xlink:href (schemaRef, linkbaseRef, loc, roleRef, arcroleRef) and xsi:schemaLocation."""
    refs: list[str] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.endswith(("}import", "}include")) and element.get("schemaLocation"):
            refs.append(element.get("schemaLocation", "").strip())
        if element.get(XLINK):
            refs.append(element.get(XLINK, "").strip())
        if element.get(XSI):
            refs.extend(element.get(XSI, "").split()[1::2])
    return [r for r in refs if r]


def references(data: bytes, url: str) -> set[str]:
    """The absolute URLs a schema or linkbase at `url` refers to, without fragments."""
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError as error:
        raise Refused(f"not well-formed XML: {url} ({error.msg})") from None
    if root.getroottree().docinfo.doctype:
        raise Refused(f"declares a DTD: {url}")
    found: set[str] = set()
    for ref in raw_references(root):
        absolute, _ = urldefrag(urljoin(url, ref))
        if absolute:
            found.add(absolute)
    return found


def crawl(
    entries: Iterable[str],
    store: Path,
    fetch: Callable[[str], bytes],
    limits: Limits = Limits(),  # noqa: B008 - frozen
    progress: Callable[[Report], None] | None = None,
) -> Report:
    """Every file the entries refer to, transitively; already stored files are read, not fetched.
    A refusal of this client (HTTP 403 or 429) stops the crawl: every further request would be
    refused too, and the SEC counts them. What is stored stays; the next run keeps it."""
    report = Report()
    queue: deque[tuple[str, str]] = deque((url.strip(), "the entry list") for url in entries)
    seen: set[str] = set()
    while queue:
        url, referrer = queue.popleft()
        if not url or url in seen:
            continue
        seen.add(url)
        host = urlsplit(url).hostname or ""
        if host not in HOSTS:
            report.skipped.setdefault(host or url, set()).add(referrer)
            continue
        try:
            path = store_path(store, url)
            if path.is_file():
                data = path.read_bytes()
                report.kept += 1
            else:
                if report.fetched + report.kept >= limits.max_files:
                    raise Refused(f"more than {limits.max_files} files")
                data = fetch(url)
                if len(data) > limits.max_file_bytes:
                    raise Refused(f"larger than {limits.max_file_bytes} bytes: {url}")
                report.bytes += len(data)
                if report.bytes > limits.max_total_bytes:
                    raise Refused(f"the store would exceed {limits.max_total_bytes} bytes")
                _write(path, data)
                report.fetched += 1
                if progress and report.fetched % 250 == 0:
                    progress(report)
            if path.suffix.lower() in (".xsd", ".xml"):
                # Sorted: the same order every run (a set's order changes with each process)
                for ref in sorted(references(data, url)):
                    if ref not in seen:
                        queue.append((ref, url))
        except Refused as refused:
            report.failed[url] = str(refused)
        except urllib.error.HTTPError as error:
            report.failed[url] = f"HTTP {error.code}"
            if error.code in (403, 429):
                report.stopped = f"{host} answered {error.code}: it refuses this client"
                break
        except (OSError, urllib.error.URLError) as error:
            report.failed[url] = f"{type(error).__name__}: {error}"
    return report


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    partial.write_bytes(data)
    os.replace(partial, path)


def manifest(store: Path) -> str:
    """`sha256  scheme/host/path` for every stored file, sorted; the store's own record."""
    lines = []
    for path in sorted(p for p in store.rglob("*") if p.is_file()):
        relative = path.relative_to(store).as_posix()
        if relative == "MANIFEST.sha256" or path.name.startswith("."):
            continue
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
    return "\n".join(lines) + "\n"


class _SameHostsOnly(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to HTTPS on the three hosts."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname not in HOSTS:
            raise Refused(f"redirected outside the taxonomy hosts: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_fetcher(
    user_agent: str, pace_seconds: float = 0.25, max_bytes: int = 64 * MIB
) -> Callable[[str], bytes]:
    """Fetches over HTTPS (an `http://` address too), through the proxy in HTTPS_PROXY, at most one
    request per `pace_seconds`, three tries on server errors."""
    opener = urllib.request.build_opener(_SameHostsOnly())
    last = [0.0]

    def fetch(url: str) -> bytes:
        target = "https://" + url.split("://", 1)[1]
        for attempt in range(3):
            wait = pace_seconds - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
            request = urllib.request.Request(target, headers={"User-Agent": user_agent})
            try:
                with opener.open(request, timeout=60) as response:
                    data: bytes = response.read(max_bytes + 1)
                    return data
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise Refused(f"no answer: {url}")

    return fetch


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="fill the taxonomy store")
    parser.add_argument("--entries", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--contact-file", type=Path, required=True)
    args = parser.parse_args(argv)
    contact = args.contact_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}", contact):
        print("the contact file does not hold an email address; nothing fetched")
        return 2
    entries = [
        line.strip()
        for line in args.entries.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"taxonomies: {len(entries)} entry files, into {args.store}", flush=True)
    report = crawl(
        entries,
        args.store,
        https_fetcher(f"AetherSpark {contact}"),
        progress=lambda r: print(f"  {r.fetched} fetched, {r.bytes} bytes so far", flush=True),
    )
    (args.store / "MANIFEST.sha256").write_text(manifest(args.store), encoding="utf-8")
    print(
        f"taxonomies: {report.fetched} fetched, {report.kept} already stored, {report.bytes} bytes"
    )
    for host, referrers in sorted(report.skipped.items()):
        print(f"  outside the hosts, not fetched: {host} (from {len(referrers)} files)")
    for url, reason in sorted(report.failed.items()):
        print(f"  FAILED {url}: {reason}")
    if report.stopped:
        print(f"  STOPPED: {report.stopped}")
        print("  check the contact: just secrets enter sec-user-agent-contact")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
