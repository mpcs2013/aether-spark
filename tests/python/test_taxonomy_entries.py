"""tools/taxonomy_entries.py: the entry list from the stored filings, the three hosts only, and the
committed list in the tool's own format."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/taxonomy_entries.py"
spec = importlib.util.spec_from_file_location("taxonomy_entries", TOOL)
assert spec and spec.loader
tool = importlib.util.module_from_spec(spec)
sys.modules["taxonomy_entries"] = tool
spec.loader.exec_module(tool)

GAAP = "https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-2024.xsd"
DEI = "https://xbrl.sec.gov/dei/2024/dei-2024.xsd"


def test_the_survey_reads_schemas_and_linkbases_and_keeps_the_three_hosts(tmp_path: Path) -> None:
    filing = tmp_path / "320193" / "0000320193-24-000123"
    filing.mkdir(parents=True)
    (filing / "aapl-20240928.xsd").write_bytes(
        f'<xs:import schemaLocation="{GAAP}"/><xs:import schemaLocation="{DEI}"/>'.encode()
    )
    (filing / "aapl-20240928_lab.xml").write_bytes(
        f'<link:loc xlink:href="{GAAP}#us-gaap_Assets"/>'
        '<link:loc xlink:href="https://evil.example/x.xsd"/>'.encode()
    )
    (filing / "aapl-20240928_htm.xml").write_bytes(b'<x href="https://xbrl.fasb.org/never.xsd"/>')

    urls, filings, files = tool.survey(tmp_path)

    assert (filings, files) == (1, 2)  # the instance is not read
    assert tool.entries(urls) == [GAAP, DEI]


def test_an_empty_raw_folder_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "entry-points.txt"
    assert tool.main([str(tmp_path), "--output", str(out)]) == 2
    assert not out.exists()


def test_the_committed_list_is_in_the_tools_format() -> None:
    text = tool.OUTPUT.read_text(encoding="utf-8")
    urls = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert urls == tool.entries(urls)  # sorted, the three hosts only, no duplicates
    header = text[: text.index(urls[0])]
    assert header.startswith("# The taxonomy job's entry files")
    assert "tools/taxonomy_entries.py" in header
