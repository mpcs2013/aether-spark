"""The ClickHouse client (PR 4b2): what it sends, how it reads answers and errors, and that its TLS
checks the certificate and the name. The real server is the stack test's."""

from __future__ import annotations

import io
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from aetherspark_pipeline.clickhouse import COLUMNS, ClickHouse, StoreRefused, StoreUnavailable


class Answer:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class Opener:
    def __init__(self, result: bytes | Exception = b"") -> None:
        self.result = result
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> Answer:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return Answer(self.result)


@pytest.fixture
def client(tmp_path: Path) -> ClickHouse:
    secret = tmp_path / "pw"
    secret.write_text("s3cret\n", encoding="utf-8")
    return ClickHouse(
        "clickhouse.svc.aether.localhost", 8443, "pipeline", secret, tmp_path / "root"
    )


def use(monkeypatch: pytest.MonkeyPatch, client: ClickHouse, opener: Opener) -> None:
    monkeypatch.setattr(ClickHouse, "_opener", lambda self: opener)


def query_of(request: urllib.request.Request) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)


def test_the_hashes_query_is_parameterized_and_signed_in(
    monkeypatch: pytest.MonkeyPatch, client: ClickHouse
) -> None:
    opener = Opener(b"a" * 64 + b"\n" + b"b" * 64 + b"\n")
    use(monkeypatch, client, opener)

    assert client.hashes(320193, "0000320193-24-000123") == {"a" * 64, "b" * 64}

    (request,) = opener.requests
    assert request.full_url.startswith("https://clickhouse.svc.aether.localhost:8443/?")
    q = query_of(request)
    assert "{cik:UInt32}" in q["query"][0] and "{accession_no:String}" in q["query"][0]
    assert (q["param_cik"], q["param_accession_no"]) == (["320193"], ["0000320193-24-000123"])
    assert request.get_header("X-clickhouse-user") == "pipeline"
    assert request.get_header("X-clickhouse-key") == "s3cret"  # in a header, never in the URL
    assert "s3cret" not in request.full_url


def test_an_insert_is_one_request_of_json_lines_in_one_block(
    monkeypatch: pytest.MonkeyPatch, client: ClickHouse
) -> None:
    opener = Opener()
    use(monkeypatch, client, opener)
    row = {c: f"<{c}>" for c in COLUMNS} | {"extra": "dropped"}

    client.insert([row, row])

    (request,) = opener.requests
    q = query_of(request)
    assert q["query"][0].startswith("INSERT INTO research.facts (cik, accession_no, form,")
    assert q["query"][0].endswith("FORMAT JSONEachRow")
    assert q["input_format_parallel_parsing"] == ["0"]
    assert q["wait_end_of_query"] == ["1"]
    assert isinstance(request.data, bytes)
    lines = request.data.decode().splitlines()
    assert [list(json.loads(line)) for line in lines] == [list(COLUMNS)] * 2


def test_nothing_to_insert_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, client: ClickHouse
) -> None:
    opener = Opener()
    use(monkeypatch, client, opener)
    client.insert([])
    assert opener.requests == []


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x/", code, "err", Message(), io.BytesIO(body))


@pytest.mark.parametrize(
    ("error", "kind", "text"),
    [
        (
            http_error(500, b"Code: 241. DB::Exception: Memory limit\nmore"),
            StoreUnavailable,
            "Code: 241",
        ),
        (http_error(503, b""), StoreUnavailable, "503"),
        (
            urllib.error.URLError("Connection refused"),
            StoreUnavailable,
            "unreachable: Connection refused",
        ),
        (
            http_error(403, b"Code: 497. DB::Exception: pipeline: Not enough privileges"),
            StoreRefused,
            "Code: 497",
        ),
        (http_error(400, b"Code: 27. Cannot parse input"), StoreRefused, r"refused \(400\)"),
    ],
)
def test_errors_tell_a_store_that_is_down_from_a_refusal(
    monkeypatch: pytest.MonkeyPatch, client: ClickHouse, error: Exception, kind: type, text: str
) -> None:
    use(monkeypatch, client, Opener(error))
    with pytest.raises(kind, match=text) as raised:
        client.hashes(1, "0000000001-24-000001")
    assert "s3cret" not in str(raised.value)
    assert "\n" not in str(raised.value)


def test_tls_checks_the_certificate_and_the_name_and_uses_no_proxy(
    monkeypatch: pytest.MonkeyPatch, client: ClickHouse
) -> None:
    contexts: list[ssl.SSLContext] = []
    real = ssl.create_default_context

    def spy(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        kwargs.pop("cafile", None)  # the test has no root file; the defaults are what matters
        context = real(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(ssl, "create_default_context", spy)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")

    opener = client._opener()

    (context,) = contexts
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    # The explicit, empty ProxyHandler keeps out the default one, which would read HTTPS_PROXY;
    # being empty, it registers for no scheme, so the opener holds no proxy handler at all
    handlers = getattr(opener, "handlers", [])
    assert handlers
    assert not [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
