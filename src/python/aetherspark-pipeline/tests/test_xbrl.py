"""Arelle offline (PR 4b1): a filing's facts as rows with its taxonomy from the store; a missing
taxonomy file fails with its URL; the reference check refuses what is neither the job's nor the
taxonomy hosts'. A made-up taxonomy stands in for US GAAP, kept where the store keeps a FASB
file."""

from __future__ import annotations

from pathlib import Path

import pytest
from aetherspark_pipeline import xbrl
from aetherspark_pipeline.child import check, reference_problem
from aetherspark_pipeline.taxonomies import store_path
from aetherspark_pipeline.xbrl import VALUE_ERROR, LoadFailed, load

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
 <xs:element name="ServicePeriod" id="test_ServicePeriod" type="xbrli:durationItemType"
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


# A made-up dei taxonomy where the store keeps the SEC's, and a filing variant that states its
# fiscal year and period, as every real filing does (the loader requires them, PR 4b2)
DEI = "https://xbrl.sec.gov/dei/2024/dei-2024.xsd"
DEI_SCHEMA = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance"
 targetNamespace="http://xbrl.sec.gov/dei/2024" elementFormDefault="qualified">
 <xs:import namespace="http://www.xbrl.org/2003/instance"
   schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
 <xs:element name="DocumentFiscalYearFocus" id="dei_DocumentFiscalYearFocus"
   type="xbrli:gYearItemType" substitutionGroup="xbrli:item" xbrli:periodType="duration"/>
 <xs:element name="DocumentFiscalPeriodFocus" id="dei_DocumentFiscalPeriodFocus"
   type="xbrli:stringItemType" substitutionGroup="xbrli:item" xbrli:periodType="duration"/>
</xs:schema>
"""
DEI_IMPORT = f'<xs:import namespace="http://xbrl.sec.gov/dei/2024" schemaLocation="{DEI}"/>'
COMPANY_DEI = COMPANY.replace("<xs:import namespace", f"{DEI_IMPORT}\n <xs:import namespace", 1)


def instance_dei(year: str = "2024", period: str = "FY") -> str:
    """INSTANCE with its dei fiscal year and period."""
    return INSTANCE.replace(
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi">',
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:dei="http://xbrl.sec.gov/dei/2024">',
    ).replace(
        "</xbrli:xbrl>",
        f'<dei:DocumentFiscalYearFocus contextRef="fy">{year}</dei:DocumentFiscalYearFocus>'
        f'<dei:DocumentFiscalPeriodFocus contextRef="fy">{period}</dei:DocumentFiscalPeriodFocus>'
        "</xbrli:xbrl>",
    )


def put(store: Path, url: str, text: str) -> None:
    path = store_path(store, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def test_a_fiscal_year_that_is_not_a_year_fails_the_load(job: tuple[Path, Path]) -> None:
    folder, store = job
    put(store, DEI, DEI_SCHEMA)
    (folder / "co-20241231.xsd").write_text(COMPANY_DEI, encoding="utf-8")
    (folder / "co-20241231_htm.xml").write_text(instance_dei("FY24", "Q2"), encoding="utf-8")

    # Arelle checks the gYear type itself; an Arelle error fails the job (strict, the owner's
    # choice of 2026-11-21), a value error on a dei fact too (2026-11-22)
    with pytest.raises(LoadFailed, match="DocumentFiscalYearFocus"):
        load(folder, "co-20241231_htm.xml", store)


def write_instance(folder: Path, text: str) -> None:
    (folder / "co-20241231_htm.xml").write_text(text, encoding="utf-8")


def test_an_invalid_value_of_a_non_numeric_fact_is_counted_not_fatal(
    job: tuple[Path, Path],
) -> None:
    # Wells Fargo's 10-K for 2020 wrote "one year" where a duration belongs (2026-11-22)
    folder, store = job
    write_instance(
        folder,
        INSTANCE.replace(
            "</xbrli:xbrl>",
            '<test:ServicePeriod contextRef="fy">one year</test:ServicePeriod></xbrli:xbrl>',
        ),
    )

    parsed = load(folder, "co-20241231_htm.xml", store)

    assert len(parsed.rows) == 3
    assert parsed.invalid_non_numeric == 1
    assert parsed.non_numeric == 2


class _Record:
    def __init__(self, code: str, level: str = "ERROR") -> None:
        self.messageCode, self.levelname = code, level

    def getMessage(self) -> str:  # logging's name
        return f"{self.messageCode} message"


class _Fake:
    def __init__(self, records: list[_Record], codes: list[str]) -> None:
        self.logHandler = self
        self.logRecordBuffer = records
        self.errors = codes


@pytest.mark.parametrize(
    ("records", "codes", "expected"),
    [
        ([_Record(VALUE_ERROR)], [VALUE_ERROR], (None, 1)),
        # an inconsistency is captured in model.errors without an ERROR log line
        (
            [_Record(VALUE_ERROR), _Record("calc:summationItemsInconsistency", "INCONSISTENCY")],
            [VALUE_ERROR, "calc:summationItemsInconsistency"],
            ("Arelle reported calc:summationItemsInconsistency", 0),
        ),
        # a code recorded without any log line at ERROR
        (
            [_Record(VALUE_ERROR)],
            [VALUE_ERROR, "arelle.schemaFileError"],
            ("Arelle reported arelle.schemaFileError", 0),
        ),
        # more value errors captured than tolerated lines
        ([_Record(VALUE_ERROR)], [VALUE_ERROR, VALUE_ERROR], ("Arelle reported errors", 0)),
        ([], [VALUE_ERROR], ("Arelle reported errors", 0)),
    ],
    ids=["tolerated", "inconsistency", "code without a line", "uncounted", "no line"],
)
def test_only_tolerated_value_errors_let_a_filing_through(
    monkeypatch: pytest.MonkeyPatch,
    records: list[_Record],
    codes: list[str],
    expected: tuple[str | None, int],
) -> None:
    # The security review of 2026-11-22: model.errors also holds codes below ERROR
    monkeypatch.setattr(xbrl, "_tolerated", lambda _model, r: r.messageCode == VALUE_ERROR)
    fake = _Fake(records, codes)

    assert xbrl._errors(fake, fake) == expected


def test_a_reference_without_an_object_id_is_not_tolerated() -> None:
    record = _Record(VALUE_ERROR)
    record.refs = [{"href": "a_htm.xml#f1"}]  # type: ignore[attr-defined]

    assert not xbrl._tolerated(object(), record)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (">391035000000<", ">391 billion<", "test:Revenues"),
        (
            "<xbrli:instant>2023-12-31</xbrli:instant>",
            "<xbrli:instant>2023-13-45</xbrli:instant>",
            "instant",
        ),
    ],
    ids=["numeric fact", "context date"],
)
def test_an_invalid_value_elsewhere_still_fails_the_load(
    job: tuple[Path, Path], old: str, new: str, message: str
) -> None:
    folder, store = job
    assert old in INSTANCE
    write_instance(folder, INSTANCE.replace(old, new))

    with pytest.raises(LoadFailed, match=message):
        load(folder, "co-20241231_htm.xml", store)


@pytest.mark.parametrize(
    ("year", "period", "expected"),
    [("2024", "FY", (2024, "FY")), ("2025", "Q2", (2025, "Q2"))],
)
def test_the_fiscal_year_and_period_come_from_the_dei_facts(
    job: tuple[Path, Path], year: str, period: str, expected: tuple[int, str]
) -> None:
    folder, store = job
    put(store, DEI, DEI_SCHEMA)
    (folder / "co-20241231.xsd").write_text(COMPANY_DEI, encoding="utf-8")
    (folder / "co-20241231_htm.xml").write_text(instance_dei(year, period), encoding="utf-8")

    parsed = load(folder, "co-20241231_htm.xml", store)

    assert (parsed.fiscal_year, parsed.fiscal_period) == expected
    assert len(parsed.rows) == 3  # the dei facts are not numeric facts


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
        ("co-20241231.htm", "not an XML file of the job"),
        ("co-20241231.htm#anchor", "not an XML file of the job"),
        ("CO-20241231_HTM.XML", "names a file the job does not have"),
        ("https://evil.example/x.xsd", "not a taxonomy host"),
        ("file:///proc/self/environ", "not a taxonomy host"),
        ("https://xbrl.fasb.org/../x.xsd", "not a plain taxonomy path"),
    ],
)
def test_the_reference_check(ref: str, problem: str | None) -> None:
    found = reference_problem(ref, {"co-20241231.xsd", "co-20241231_htm.xml", "co-20241231.htm"})
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


# #136: a linkbaseRef to the filing's own .htm main document. Its content here is a well-formed
# linkbase, so Arelle would load it as one; the pre-parse never checks a .htm file
HTM_LINKBASE = """<?xml version="1.0"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
 xmlns:xlink="http://www.w3.org/1999/xlink"/>
"""
LINKBASE_REF = (
    '<link:linkbaseRef xlink:type="simple" xlink:href="co-20241231.htm"'
    ' xlink:arcrole="http://www.w3.org/1999/xlink/properties/linkbase"/>'
)


def _link_to_htm(folder: Path) -> None:
    (folder / "co-20241231.htm").write_text(HTM_LINKBASE, encoding="utf-8")
    schema_ref = 'xlink:href="co-20241231.xsd"/>'
    (folder / "co-20241231_htm.xml").write_text(
        INSTANCE.replace(schema_ref, schema_ref + LINKBASE_REF), encoding="utf-8"
    )


def test_a_link_to_the_jobs_htm_document_refuses_the_job_before_arelle(
    job: tuple[Path, Path],
) -> None:
    folder, _ = job
    _link_to_htm(folder)

    ok, reason = check(folder)

    assert not ok
    assert reason == (
        "co-20241231_htm.xml: a reference refused (not an XML file of the job: co-20241231.htm)"
    )


def test_arelle_loading_a_job_file_the_pre_parse_did_not_check_fails_the_load(
    job: tuple[Path, Path],
) -> None:
    # The second guard: should a non-XML job file pass the reference check, the load still fails
    folder, store = job
    _link_to_htm(folder)

    with pytest.raises(LoadFailed, match="neither an XML file of the job nor a taxonomy file"):
        load(folder, "co-20241231_htm.xml", store)
