"""Arelle offline (PR 4b1): a filing's facts as rows with its taxonomy from the store; a missing
taxonomy file fails with its URL; the reference check refuses what is neither the job's nor the
taxonomy hosts'. A made-up taxonomy stands in for US GAAP, kept where the store keeps a FASB
file."""

from __future__ import annotations

from pathlib import Path

import pytest
from aetherspark_pipeline.child import check, reference_problem
from aetherspark_pipeline.taxonomies import store_path
from aetherspark_pipeline.xbrl import LoadFailed, load

TAXONOMY = "https://xbrl.fasb.org/test/2024/test-2024.xsd"
TAX_SCHEMA = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance"
 targetNamespace="http://fasb.org/test/2024" elementFormDefault="qualified">
 <xs:import namespace="http://www.xbrl.org/2003/instance"
   schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
 <xs:element name="Revenues" id="test_Revenues" type="xbrli:monetaryItemType"
   substitutionGroup="xbrli:item" xbrli:periodType="duration" nillable="true"/>
 <xs:element name="Assets" id="test_Assets" type="xbrli:monetaryItemType"
   substitutionGroup="xbrli:item" xbrli:periodType="instant" nillable="true"/>
 <xs:element name="Note" id="test_Note" type="xbrli:stringItemType"
   substitutionGroup="xbrli:item" xbrli:periodType="duration" nillable="true"/>
</xs:schema>
"""
COMPANY = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="http://example.com/co"
 xmlns:xbrldt="http://xbrl.org/2005/xbrldt" xmlns:xbrli="http://www.xbrl.org/2003/instance"
 elementFormDefault="qualified">
 <xs:import namespace="http://fasb.org/test/2024" schemaLocation="TAXONOMY"/>
 <xs:import namespace="http://www.xbrl.org/2003/instance"
   schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
 <xs:import namespace="http://xbrl.org/2005/xbrldt" schemaLocation="http://www.xbrl.org/2005/xbrldt-2005.xsd"/>
 <xs:element name="SegmentAxis" id="co_SegmentAxis" type="xbrli:stringItemType" abstract="true"
   substitutionGroup="xbrldt:dimensionItem" xbrli:periodType="duration" nillable="true"/>
 <xs:element name="EuropeMember" id="co_EuropeMember" type="xbrli:stringItemType" abstract="true"
   substitutionGroup="xbrli:item" xbrli:periodType="duration" nillable="true"/>
</xs:schema>
""".replace("TAXONOMY", TAXONOMY)
CONTEXT = (
    '<xbrli:context id="{id}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
    "0001234567</xbrli:identifier>{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period>"
    "</xbrli:context>"
)
INSTANCE = """<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase"
 xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:test="http://fasb.org/test/2024" xmlns:co="http://example.com/co"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
 <link:schemaRef xlink:type="simple" xlink:href="co-20241231.xsd"/>
 CONTEXTS
 <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <test:Revenues contextRef="fy" unitRef="usd" decimals="-6">391035000000</test:Revenues>
 <test:Revenues contextRef="eu" unitRef="usd" decimals="-6">101328000000</test:Revenues>
 <test:Assets contextRef="end" unitRef="usd" decimals="INF">364980000000.5</test:Assets>
 <test:Assets contextRef="end2" unitRef="usd" decimals="INF">1.1234567</test:Assets>
 <test:Revenues contextRef="fy2" unitRef="usd" xsi:nil="true"
   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>
 <test:Note contextRef="fy">text</test:Note>
</xbrli:xbrl>
""".replace(
    "CONTEXTS",
    "".join(
        CONTEXT.format(id=i, segment=s, period=p)
        for i, s, p in [
            (
                "fy",
                "",
                "<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>",
            ),
            (
                "fy2",
                "",
                "<xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate>",
            ),
            (
                "eu",
                '<xbrli:segment><xbrldi:explicitMember dimension="co:SegmentAxis">co:EuropeMember'
                "</xbrldi:explicitMember></xbrli:segment>",
                "<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>",
            ),
            ("end", "", "<xbrli:instant>2024-12-31</xbrli:instant>"),
            ("end2", "", "<xbrli:instant>2023-12-31</xbrli:instant>"),
        ]
    ),
)


@pytest.fixture
def job(tmp_path: Path) -> tuple[Path, Path]:
    folder, store = tmp_path / "job", tmp_path / "store"
    folder.mkdir()
    (folder / "co-20241231.xsd").write_text(COMPANY, encoding="utf-8")
    (folder / "co-20241231_htm.xml").write_text(INSTANCE, encoding="utf-8")
    taxonomy = store_path(store, TAXONOMY)
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text(TAX_SCHEMA, encoding="utf-8")
    return folder, store


def test_the_numeric_facts_become_rows(job: tuple[Path, Path]) -> None:
    folder, store = job
    assert check(folder) == (True, "2 XML files pre-parsed")

    parsed = load(folder, "co-20241231_htm.xml", store)

    rows = {
        (r["concept"], r["period_end"], tuple(map(tuple, r["dimensions"]))): r for r in parsed.rows
    }
    assert len(parsed.rows) == 3
    total = rows[("test:Revenues", "2024-12-31", ())]
    assert total == {
        "concept": "test:Revenues",
        "unit": "iso4217:USD",
        "value": "391035000000",
        "decimals": -6,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",  # the filing's own date, not Arelle's next midnight
        "dimensions": [],
    }
    europe = rows[("test:Revenues", "2024-12-31", (("co:SegmentAxis", "co:EuropeMember"),))]
    assert europe["value"] == "101328000000"
    assets = rows[("test:Assets", "2024-12-31", ())]
    assert (assets["period_start"], assets["decimals"], assets["value"]) == (
        "2024-12-31",
        None,
        "364980000000.5",
    )
    assert parsed.too_precise == 1  # 1.1234567: seven places, never rounded
    assert parsed.nil == 1
    assert parsed.non_numeric == 1


def test_a_missing_taxonomy_file_fails_with_its_url(job: tuple[Path, Path]) -> None:
    folder, store = job
    store_path(store, TAXONOMY).unlink()

    with pytest.raises(LoadFailed, match="Could not load file") as failed:
        load(folder, "co-20241231_htm.xml", store)
    assert TAXONOMY in str(failed.value)


@pytest.mark.parametrize(
    ("ref", "problem"),
    [
        ("co-20241231.xsd", None),
        ("co-20241231.xsd#co_SegmentAxis", None),
        ("#local", None),
        (TAXONOMY, None),
        ("http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd#xbrli_item", None),
        ("../../run/secrets/x.xsd", "not a file of the job"),
        ("/run/secrets/x", "not a file of the job"),
        ("sub/co.xsd", "not a file of the job"),
        ("other.xsd", "names a file the job does not have"),
        ("https://evil.example/x.xsd", "not a taxonomy host"),
        ("file:///proc/self/environ", "not a taxonomy host"),
        ("https://xbrl.fasb.org/../x.xsd", "not a plain taxonomy path"),
    ],
)
def test_the_reference_check(ref: str, problem: str | None) -> None:
    found = reference_problem(ref, {"co-20241231.xsd", "co-20241231_htm.xml"})
    assert (found is None) if problem is None else (found is not None and problem in found)


def test_a_traversing_import_refuses_the_job_before_arelle(job: tuple[Path, Path]) -> None:
    folder, _ = job
    (folder / "co-20241231.xsd").write_text(
        COMPANY.replace(TAXONOMY, "../../run/secrets/postgres-data-pipeline-password"),
        encoding="utf-8",
    )

    ok, reason = check(folder)

    assert not ok
    assert reason.startswith("co-20241231.xsd: a reference refused (not a file of the job")
