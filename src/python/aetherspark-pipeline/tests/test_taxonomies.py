"""The taxonomy store (PR 4b1): where each URL is kept, what a file refers to, the transitive fetch
from the entry list only, the three hosts only, and the manifest."""

from __future__ import annotations

import hashlib
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from aetherspark_pipeline.taxonomies import (
    Limits,
    Refused,
    crawl,
    manifest,
    references,
    store_path,
)

FASB = "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-2024.xsd"


def test_a_url_is_kept_where_arelles_offline_cache_looks(tmp_path: Path) -> None:
    assert store_path(tmp_path, FASB) == tmp_path.joinpath(
        "https", "xbrl.fasb.org", "us-gaap", "2024", "elts", "us-gaap-2024.xsd"
    )
    assert store_path(tmp_path, "http://www.xbrl.org/2003/xlink-2003-12-31.xsd") == (
        tmp_path / "http" / "www.xbrl.org" / "2003" / "xlink-2003-12-31.xsd"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x.xsd",
        "ftp://xbrl.fasb.org/x.xsd",
        "file:///run/secrets/x",
        "https://xbrl.fasb.org/../../etc/passwd",
        "https://xbrl.fasb.org/a/%2e%2e/b.xsd",
        "https://xbrl.fasb.org/x.xsd?y=1",
        "https://xbrl.fasb.org:8443/x.xsd",
        "https://user@xbrl.fasb.org/x.xsd",
        "https://xbrl.fasb.org/",
    ],
)
def test_anything_but_a_plain_file_on_the_three_hosts_is_refused(tmp_path: Path, url: str) -> None:
    with pytest.raises(Refused):
        store_path(tmp_path, url)


SCHEMA = b"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
  xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
 <xs:annotation><xs:appinfo>
  <link:linkbaseRef xlink:type="simple" xlink:href="../lab/us-gaap-lab-2024.xml"/>
 </xs:appinfo></xs:annotation>
 <xs:import namespace="http://www.xbrl.org/2003/instance"
   schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
 <xs:include schemaLocation="us-gaap-types-2024.xsd"/>
</xs:schema>"""


def test_a_schemas_references_are_resolved_against_its_url() -> None:
    assert references(SCHEMA, FASB) == {
        "https://xbrl.fasb.org/us-gaap/2024/lab/us-gaap-lab-2024.xml",
        "http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd",
        "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-types-2024.xsd",
    }


def test_a_linkbases_locators_lose_their_fragment() -> None:
    linkbase = b"""<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"><link:labelLink>
      <link:loc xlink:type="locator" xlink:href="../elts/us-gaap-2024.xsd#us-gaap_Revenues"/>
      </link:labelLink></link:linkbase>"""
    assert references(linkbase, "https://xbrl.fasb.org/us-gaap/2024/lab/l.xml") == {FASB}


def test_a_dtd_is_refused() -> None:
    with pytest.raises(Refused, match="DTD"):
        references(b'<!DOCTYPE x [<!ENTITY a "a">]><x>&a;</x>', FASB)


def test_the_crawl_fetches_what_the_entries_refer_to_and_nothing_else(tmp_path: Path) -> None:
    served = {
        FASB: SCHEMA,
        "https://xbrl.fasb.org/us-gaap/2024/lab/us-gaap-lab-2024.xml": b"<l/>",
        "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-types-2024.xsd": (
            b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            b'<xs:import schemaLocation="https://evil.example/x.xsd"/></xs:schema>'
        ),
        "http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd": b"<s/>",
    }
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        return served[url]

    report = crawl([FASB, ""], tmp_path, fetch)

    assert sorted(asked) == sorted(served)
    assert report.fetched == 4
    assert report.failed == {}
    assert set(report.skipped) == {"evil.example"}
    assert (tmp_path / "http" / "www.xbrl.org" / "2003" / "xbrl-instance-2003-12-31.xsd").exists()

    again = crawl([FASB], tmp_path, fetch)
    assert again.fetched == 0
    assert again.kept == 4
    assert len(asked) == 4  # nothing fetched twice


def test_a_failed_fetch_is_reported_and_the_rest_goes_on(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        if url.endswith("types-2024.xsd"):
            raise OSError("timed out")
        return b"<x/>" if not url.endswith("us-gaap-2024.xsd") else SCHEMA

    report = crawl([FASB], tmp_path, fetch)

    assert list(report.failed) == ["https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-types-2024.xsd"]
    assert report.fetched == 3


@pytest.mark.parametrize("code", [403, 429])
def test_a_refusal_of_this_client_stops_the_crawl(tmp_path: Path, code: int) -> None:
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        if url != FASB:
            raise urllib.error.HTTPError(url, code, "refused", Message(), None)
        return SCHEMA

    report = crawl([FASB], tmp_path, fetch)

    assert len(asked) == 2  # the entry, then the first refusal; nothing after it
    # SCHEMA's first reference in sorted order: http:// sorts before https://
    assert asked[1] == "http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"
    assert report.stopped == f"www.xbrl.org answered {code}: it refuses this client"
    assert list(report.failed.values()) == [f"HTTP {code}"]


def test_a_missing_file_is_a_failure_and_the_crawl_goes_on(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        if url.endswith("types-2024.xsd"):
            raise urllib.error.HTTPError(url, 404, "not found", Message(), None)
        return b"<x/>" if url != FASB else SCHEMA

    report = crawl([FASB], tmp_path, fetch)

    types = "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-types-2024.xsd"
    assert report.failed == {types: "HTTP 404"}
    assert report.stopped == ""
    assert report.fetched == 3


def test_progress_is_reported_every_250_files(tmp_path: Path) -> None:
    entries = [f"https://xbrl.fasb.org/test/{n}.txt" for n in range(600)]
    seen: list[int] = []

    report = crawl(entries, tmp_path, lambda url: b"x", progress=lambda r: seen.append(r.fetched))

    assert report.fetched == 600
    assert seen == [250, 500]


def test_the_limits_hold(tmp_path: Path) -> None:
    report = crawl([FASB], tmp_path, lambda url: SCHEMA, Limits(max_file_bytes=10))
    assert "larger than 10 bytes" in report.failed[FASB]


def test_the_manifest_lists_every_stored_file_with_its_hash(tmp_path: Path) -> None:
    path = store_path(tmp_path, FASB)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    (path.parent / ".partial.part").write_bytes(b"y")

    text = manifest(tmp_path)

    digest = hashlib.sha256(b"x").hexdigest()
    assert text == f"{digest}  https/xbrl.fasb.org/us-gaap/2024/elts/us-gaap-2024.xsd\n"
