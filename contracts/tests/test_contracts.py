"""Tests for the contracts generator (ADR-0002 rule 2) and the generated Pydantic models. The C#
side reads the same cases in tests/dotnet/AetherSpark.Ingester.Tests/ContractTests.cs."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from aetherspark_common.contracts.filing_job import FilingJob
from pydantic import ValidationError

GENERATOR = Path(__file__).resolve().parents[1] / "generate.py"
CASES = Path(__file__).resolve().parents[1] / "examples" / "filing-job.cases.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate"] = module
    spec.loader.exec_module(module)
    return module


gen = _load()
cases: dict[str, Any] = json.loads(CASES.read_text(encoding="utf-8"))
SCHEMA: dict[str, Any] = json.loads((gen.REPO / gen.TARGETS[0].schema).read_text(encoding="utf-8"))


def test_the_generated_files_are_current() -> None:
    # The same comparison as `just contracts --check` in CI
    assert gen.stale(gen.outputs()) == []


def test_check_names_a_stale_file(tmp_path: Path) -> None:
    for target in gen.TARGETS:
        (tmp_path / target.schema).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / target.schema).write_text(json.dumps(SCHEMA), encoding="utf-8")
    files = gen.outputs(tmp_path)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert gen.stale(files) == []
    edited = tmp_path / gen.TARGETS[0].csharp
    edited.write_text(edited.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")
    assert gen.stale(files) == [edited]


@pytest.mark.parametrize("name", sorted(cases["valid"]))
def test_pydantic_accepts_the_valid_cases(name: str) -> None:
    job = FilingJob.model_validate_json(json.dumps(cases["valid"][name]))
    assert job.files


@pytest.mark.parametrize("name", sorted(cases["invalid"]))
def test_pydantic_refuses_the_invalid_cases(name: str) -> None:
    with pytest.raises(ValidationError):
        FilingJob.model_validate_json(json.dumps(cases["invalid"][name]))


def test_csharp_patterns_end_at_the_end_of_the_text() -> None:
    # .NET's `$` also matches before a final line end
    text = gen.render_csharp(gen.parse(SCHEMA, "s"), "s", "N")
    assert '\\z", RegexOptions.CultureInvariant)' in text
    assert '$", RegexOptions' not in text


def _schema_with(change: Any) -> dict[str, Any]:
    schema = copy.deepcopy(SCHEMA)
    change(schema)
    return schema


def _prop(schema: dict[str, Any], name: str) -> dict[str, Any]:
    prop: dict[str, Any] = schema["properties"][name]
    return prop


def _rename(schema: dict[str, Any], old: str, new: str) -> None:
    schema["properties"] = {(new if k == old else k): v for k, v in schema["properties"].items()}
    schema["required"] = [new if k == old else k for k in schema["required"]]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: _prop(s, "accession_no").update(pattern="[0-9]+"), "anchored"),
        (lambda s: _prop(s, "accession_no").update(pattern="^\\d{10}$"), "outside the subset"),
        (lambda s: _prop(s, "known_at").update(format="date-time"), "keywords outside"),
        (lambda s: s["required"].remove("form"), "every property is required"),
        (lambda s: s.update(additionalProperties=True), "additionalProperties: false"),
        (lambda s: _prop(s, "cik").pop("maximum"), "maximum must be an integer"),
        (lambda s: _prop(s, "cik").update(type="number"), "outside the subset"),
        (lambda s: _prop(s, "files").update(items={"type": "string"}), "single $ref"),
        (lambda s: _prop(s, "form").pop("description"), "description missing"),
        (lambda s: _prop(s, "form").update(enum=['x"y']), "plain strings"),
        (lambda s: _rename(s, "form", "schema"), "free in Python"),
        (lambda s: _rename(s, "form", "model_form"), "belong to Pydantic"),
        (lambda s: s.update(title="filing_job"), "PascalCase"),
        (lambda s: s.update({"$schema": "http://json-schema.org/draft-07/schema#"}), "2020-12"),
    ],
)
def test_the_generator_refuses_anything_outside_its_subset(change: Any, message: str) -> None:
    with pytest.raises(gen.SchemaError, match=message.replace("$", "\\$")):
        gen.parse(_schema_with(change), "s")


def test_python_output_keeps_patterns_and_bounds() -> None:
    text = gen.render_python(gen.parse(SCHEMA, "s"), "s")
    assert 'extra="forbid", frozen=True, strict=True' in text
    assert "Field(ge=1, le=9999999999)" in text
    assert "Field(min_length=1, max_length=64)" in text
