"""A stand-in SEC for the stack test (tests/stack/run.sh; data-plane.md, D3 to D5): one made-up
company over plain HTTP on the test's network, Python's standard library only. It answers the paths
the ingester builds and records every request it receives (path, User-Agent, time) in
/log/requests.jsonl, so the test sees exactly what the ingester asked for.

Some answers are hard on purpose: a header answers 429 once and a file 503 once (backoff), a file
redirects (never followed), a filing names a traversing file, a filing's two acceptance times
differ, and the documents carry links, schema locations and paths back to this stub (never
requested).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CIK = 1234567
SELF = "http://sec-stub:8080"
LOG = "/log/requests.jsonl"
FOLDER = f"/Archives/edgar/data/{CIK}/"

# accession, form, filed, header time (ET), index time (UTC), primary document, files, other names
FILINGS: list[tuple[str, str, str, str, str, str, dict[str, str], list[str]]] = [
    (
        "0001234567-25-000001",
        "10-K",
        "2025-02-20",
        "20250220163000",
        "2025-02-20T21:30:00.000Z",
        "stub-20241231.htm",
        {
            "stub-20241231.htm": f'<html><a href="{SELF}/evil/link">x</a>'
            f'<img src="{SELF}/evil/img.png"/><a href="file:///proc/self/environ">y</a>'
            '<a href="/run/secrets/sec-user-agent-contact">z</a></html>',
            "stub-20241231_htm.xml": (
                f'<xbrl><link:schemaRef xlink:href="{SELF}/evil/remote.xsd"/></xbrl>'
            ),
            "stub-20241231.xsd": f'<xs:schema><xs:import schemaLocation="{SELF}/evil/import.xsd"/>'
            '<xs:include schemaLocation="../../../../run/secrets/x.xsd"/></xs:schema>',
            "stub-20241231_lab.xml": "<link:linkbase/>",
        },
        ["R1.htm", "Show.js", "../../escape.htm"],
    ),
    (
        # accepted after 17:30 ET: filed the next day, known_at keeps the true time
        "0001234567-25-000002",
        "10-Q",
        "2025-08-01",
        "20250731180334",
        "2025-07-31T22:03:34.000Z",
        "stub-20250630.htm",
        {
            "stub-20250630.htm": "<html>second quarter</html>",
            "stub-20250630_htm.xml": "<xbrl/>",
            "stub-20250630.xsd": "<xs:schema/>",
        },
        [],
    ),
    (
        "0001234567-25-000003",
        "10-Q/A",
        "2025-09-01",
        "20250829100000",
        "2025-08-29T14:00:00.000Z",
        "stub-q2a.htm",
        {"stub-q2a.htm": "<html/>", "stub-q2a_htm.xml": "<xbrl/>"},
        ["../../run/secrets/x.xsd"],
    ),
    (
        # the header says 16:30 EST (21:30 UTC), the index 21:00 UTC
        "0001234567-25-000004",
        "10-Q",
        "2025-11-03",
        "20251103163000",
        "2025-11-03T21:00:00.000Z",
        "stub-q3.htm",
        {"stub-q3.htm": "<html/>", "stub-q3_htm.xml": "<xbrl/>", "stub-q3.xsd": "<xs:schema/>"},
        [],
    ),
    (
        "0001234567-25-000005",
        "10-K/A",
        "2025-12-01",
        "20251201090000",
        "2025-12-01T14:00:00.000Z",
        "stub-ka.htm",
        {"stub-ka.htm": "<html/>", "stub-ka_htm.xml": "<xbrl/>", "stub-ka.xsd": "<xs:schema/>"},
        [],
    ),
]
ONCE = {  # path: status answered the first time only
    f"{FOLDER}000123456725000002/0001234567-25-000002.hdr.sgml": 429,
    f"{FOLDER}000123456725000002/stub-20250630_htm.xml": 503,
}
REDIRECT = {f"{FOLDER}000123456725000005/stub-ka.xsd": f"{SELF}/evil/redirected"}


def _routes() -> dict[str, bytes]:
    columns: dict[str, list[Any]] = {
        k: []
        for k in (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
            "isXBRL",
            "isInlineXBRL",
        )
    }
    routes: dict[str, bytes] = {}
    for acc, form, filed, header_time, index_time, primary, files, others in FILINGS:
        for key, value in zip(
            columns, (acc, filed, "", index_time, form, primary, 1, 1), strict=True
        ):
            columns[key].append(value)
        folder = FOLDER + acc.replace("-", "") + "/"
        day = filed.replace("-", "")
        routes[folder + acc + ".hdr.sgml"] = (
            f"<SEC-HEADER>{acc}.hdr.sgml : {day}\n<ACCEPTANCE-DATETIME>{header_time}\n"
            f"<ACCESSION-NUMBER>{acc}\n<TYPE>{form}\n<FILING-DATE>{day}\n"
            f"<FILER>\n<COMPANY-DATA>\n<CONFORMED-NAME>Stub Co\n<CIK>{CIK:010d}\n"
        ).encode()
        items = [{"name": n, "type": "text.gif", "size": ""} for n in [*files, *others]]
        routes[folder + "index.json"] = json.dumps({"directory": {"item": items}}).encode()
        for name, content in files.items():
            routes[folder + name] = content.encode()
    routes[f"/submissions/CIK{CIK:010d}.json"] = json.dumps(
        {
            "cik": f"{CIK:010d}",
            "name": "Stub Co",
            "tickers": ["STUB"],
            "exchanges": ["NYSE"],
            "filings": {"recent": columns, "files": []},
        }
    ).encode()
    return routes


ROUTES = _routes()
_lock = threading.Lock()
_seen: dict[str, int] = {}


class Stub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        with _lock:
            count = _seen.get(self.path, 0)
            _seen[self.path] = count + 1
            with open(LOG, "a", encoding="utf-8") as log:
                log.write(
                    json.dumps(
                        {
                            "path": self.path,
                            "agent": self.headers.get("User-Agent"),
                            "at": time.monotonic(),
                        }
                    )
                    + "\n"
                )
        if count == 0 and self.path in ONCE:
            self._answer(ONCE[self.path], b"busy")
        elif self.path in REDIRECT:
            self._answer(302, b"moved", {"Location": REDIRECT[self.path]})
        elif self.path in ROUTES:
            self._answer(200, ROUTES[self.path])
        else:
            self._answer(404, b"not found")

    def _answer(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Stub)  # noqa: S104 - the test's internal network
    print("sec-stub listening", flush=True)
    server.serve_forever()
