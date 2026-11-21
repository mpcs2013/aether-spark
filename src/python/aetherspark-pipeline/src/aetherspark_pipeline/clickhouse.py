"""The pipeline's ClickHouse client (data-plane.md, section 4; PR 4b2, the owner's choice of
2026-11-21): the standard library over HTTPS to port 8443, the certificate checked against the
stack's root and the server's name (ClickHouse's own client does not check the name). The pipeline's
login may read and insert `research.facts`, nothing else (users.xml).

Two calls: the fact hashes already stored for a filing, and one insert of a filing's new rows in
JSONEachRow. The insert is one block, so ClickHouse writes it whole or not at all: no parallel
parsing (it could split the rows into several parts) and `wait_end_of_query`, so the HTTP status is
the insert's real outcome. Values must already have at most six decimal places: ClickHouse rounds
a longer one into Decimal(38, 6) without a word (measured 2026-11-21), so facts.py refuses it first.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COLUMNS = (
    "cik",
    "accession_no",
    "form",
    "concept",
    "unit",
    "value",
    "decimals",
    "period_start",
    "period_end",
    "dimensions",
    "known_at",
    "filed_date",
    "fiscal_year",
    "fiscal_period",
    "fact_hash",
)
_INSERT = f"INSERT INTO research.facts ({', '.join(COLUMNS)}) FORMAT JSONEachRow"
_HASHES = (
    "SELECT fact_hash FROM research.facts"
    " WHERE cik = {cik:UInt32} AND accession_no = {accession_no:String} FORMAT TabSeparated"
)
_SETTINGS = {"wait_end_of_query": "1", "input_format_parallel_parsing": "0"}


class StoreUnavailable(Exception):
    """ClickHouse did not answer, or answered with a server error: the job goes back to the
    queue."""


class StoreRefused(Exception):
    """ClickHouse refused the request (a client error): the job fails with ClickHouse's message."""


@dataclass(frozen=True)
class ClickHouse:
    host: str
    port: int
    user: str
    password_file: Path
    root_certificate: Path
    timeout: float = 300.0

    def _opener(self) -> urllib.request.OpenerDirector:
        context = ssl.create_default_context(cafile=str(self.root_certificate))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # No proxy, whatever the environment says: ClickHouse is on the internal network
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )

    def _post(
        self, query: str, params: Mapping[str, str] | None = None, body: bytes = b""
    ) -> bytes:
        password = self.password_file.read_text(encoding="utf-8").strip()
        fields = {"query": query, **_SETTINGS}
        fields.update({f"param_{k}": v for k, v in (params or {}).items()})
        url = f"https://{self.host}:{self.port}/?{urllib.parse.urlencode(fields)}"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "X-ClickHouse-User": self.user,
                "X-ClickHouse-Key": password,
                "Content-Type": "application/octet-stream",
            },
        )
        try:
            with self._opener().open(request, timeout=self.timeout) as response:
                data: bytes = response.read()
                return data
        except urllib.error.HTTPError as error:
            message = _message(error)
            if error.code >= 500:
                raise StoreUnavailable(f"ClickHouse answered {error.code}: {message}") from None
            raise StoreRefused(f"ClickHouse refused ({error.code}): {message}") from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise StoreUnavailable(f"ClickHouse unreachable: {reason}") from None

    def hashes(self, cik: int, accession_no: str) -> set[str]:
        """The fact hashes already stored for this filing."""
        text = self._post(_HASHES, {"cik": str(cik), "accession_no": accession_no})
        return {line for line in text.decode("ascii").splitlines() if line}

    def insert(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """One insert of the rows, written whole or not at all."""
        body = "".join(
            json.dumps({c: row[c] for c in COLUMNS}, separators=(",", ":")) + "\n" for row in rows
        ).encode("utf-8")
        if body:
            self._post(_INSERT, body=body)


def _message(error: urllib.error.HTTPError) -> str:
    """ClickHouse's own message, first line only and short (it never repeats the password)."""
    try:
        text = error.read(2000).decode("utf-8", "replace")
    except OSError:
        text = ""
    first = text.strip().splitlines()[0] if text.strip() else str(error.reason)
    return " ".join(first.split())[:300]
