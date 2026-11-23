"""D6, the parse's own side: the hardened pre-parse refuses DTDs and with them every entity trick,
never reaches the network, and refuses what is not well-formed, nested deeper than 256 levels or
larger than its size limit; a long text node passes."""

from __future__ import annotations

from pathlib import Path

import pytest
from aetherspark_pipeline import child
from aetherspark_pipeline.child import MAX_DEPTH, check

BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<xbrl>&lol3;</xbrl>
"""
EXTERNAL_FILE = """<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///run/secrets/postgres-data-pipeline-password">]>
<xbrl>&secret;</xbrl>
"""
EXTERNAL_HTTP = """<?xml version="1.0"?>
<!DOCTYPE x SYSTEM "http://sec-stub:8080/evil.dtd">
<xbrl/>
"""
LOCAL_DTD = """<?xml version="1.0"?>
<!DOCTYPE x SYSTEM "file:///etc/hostname">
<xbrl/>
"""
XINCLUDE = """<?xml version="1.0"?>
<xbrl xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="/proc/self/environ"/></xbrl>
"""
SCHEMA_REF = """<?xml version="1.0"?>
<xbrl xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
<link:schemaRef xlink:href="http://sec-stub:8080/evil.xsd"/></xbrl>
"""


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def test_well_formed_xml_passes_and_html_is_left_for_later(tmp_path: Path) -> None:
    write(tmp_path, "a_htm.xml", "<xbrl><x>1</x></xbrl>")
    write(tmp_path, "a.xsd", "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'/>")
    write(tmp_path, "a.htm", "<html><p>not XML <br></p></html>")

    assert check(tmp_path) == (True, "2 XML files pre-parsed")


@pytest.mark.parametrize(
    "text",
    [BOMB, EXTERNAL_FILE, EXTERNAL_HTTP, LOCAL_DTD],
    ids=["entity bomb", "file entity", "remote DTD", "local DTD"],
)
def test_a_dtd_refuses_the_file(tmp_path: Path, text: str) -> None:
    write(tmp_path, "a_htm.xml", text)

    ok, reason = check(tmp_path)

    assert not ok
    # Either way the file is refused, and no outside file or address was read: the resolver
    # answers every outside reference with nothing (libxml2 asks for external DTDs on Linux)
    assert reason.startswith(
        ("a_htm.xml: declares a DTD", "a_htm.xml: refers to an outside resource")
    )


def test_an_xinclude_is_not_followed(tmp_path: Path) -> None:
    # The pre-parse reads the one file only: XInclude is not processed, Arelle does not process it
    write(tmp_path, "a_htm.xml", XINCLUDE)

    assert check(tmp_path) == (True, "1 XML files pre-parsed")


def test_a_reference_to_another_host_refuses_the_file(tmp_path: Path) -> None:
    # PR 4b1's reference check: only the job's files and the three taxonomy hosts
    write(tmp_path, "a_htm.xml", SCHEMA_REF)

    ok, reason = check(tmp_path)

    assert not ok
    assert reason == (
        "a_htm.xml: a reference refused (not a taxonomy host: http://sec-stub:8080/evil.xsd)"
    )


def test_not_well_formed_xml_is_refused_with_its_name(tmp_path: Path) -> None:
    write(tmp_path, "a_lab.xml", "<link:linkbase><unclosed></link:linkbase>")

    ok, reason = check(tmp_path)

    assert not ok
    assert reason.startswith("a_lab.xml: not well-formed XML")


def test_nesting_past_the_parsers_limit_is_refused(tmp_path: Path) -> None:
    depth = 10_000
    write(tmp_path, "deep.xml", "<a>" * depth + "</a>" * depth)

    ok, reason = check(tmp_path)

    assert not ok
    assert "deep.xml" in reason


@pytest.mark.parametrize(("depth", "ok"), [(MAX_DEPTH, True), (MAX_DEPTH + 1, False)])
def test_the_depth_limit_stays_libxml2s_default(tmp_path: Path, depth: int, ok: bool) -> None:
    # huge_tree would allow 2048 levels; the pre-parse keeps 256
    write(tmp_path, "deep.xml", "<a>" * depth + "</a>" * depth)

    assert check(tmp_path) == (
        (True, "1 XML files pre-parsed")
        if ok
        else (False, f"deep.xml: nested deeper than {MAX_DEPTH} levels, refused")
    )


def test_a_text_node_longer_than_libxml2s_default_passes(tmp_path: Path) -> None:
    # Prologis's 10-Ks for 2015 and 2016: a text block past libxml2's 10 MB limit (2026-11-22)
    write(tmp_path, "a_htm.xml", "<xbrl><block>" + "x" * (11 * 1024 * 1024) + "</block></xbrl>")

    assert check(tmp_path) == (True, "1 XML files pre-parsed")


def test_a_file_past_the_size_limit_is_not_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(child, "MAX_XML_BYTES", 100)
    write(tmp_path, "a.xsd", "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'/>")
    write(tmp_path, "a_htm.xml", "<xbrl>" + "x" * 100 + "</xbrl>")

    assert check(tmp_path) == (False, "a_htm.xml: larger than 100 bytes, not parsed")
