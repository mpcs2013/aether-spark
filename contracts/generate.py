"""The contracts generator (ADR-0002 rule 2): C# records and Pydantic models from the JSON Schema
files in contracts/. It reads a small, fixed subset of JSON Schema and refuses anything else by
name, so a schema never means more than both languages check.

    python -I contracts/generate.py           write the generated files
    python -I contracts/generate.py --check   exit 1 when a generated file is stale (CI)

The subset: an object with `additionalProperties: false` and every property required; properties
that are a `const` integer, a string with an anchored `pattern` (ASCII classes only, no `\\d`, `\\w`
or `\\s`, whose meaning differs between .NET, Python and PostgreSQL) or an `enum` of strings, an
integer with `minimum` and `maximum`, or an array of one `$defs` object with `minItems` and
`maxItems`. Each property has a `description`.
"""

from __future__ import annotations

import argparse
import json
import keyword
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRAFT = "https://json-schema.org/draft/2020-12/schema"
WIDTH = 100


@dataclass(frozen=True)
class Target:
    """One schema and where its generated code goes (repository-relative)."""

    schema: str
    python: str
    csharp: str
    namespace: str


TARGETS = (
    Target(
        schema="contracts/filing-job.schema.json",
        python="src/python/aetherspark-common/src/aetherspark_common/contracts/filing_job.py",
        csharp="src/dotnet/AetherSpark.Ingester/Contracts/FilingJob.g.cs",
        namespace="AetherSpark.Contracts",
    ),
)
PYTHON_PACKAGE = "src/python/aetherspark-common/src/aetherspark_common/contracts/__init__.py"


class SchemaError(Exception):
    """The schema leaves the subset this generator reads."""


@dataclass(frozen=True)
class Prop:
    json_name: str
    description: str
    kind: str  # const | pattern | enum | integer | array
    pattern: str = ""
    values: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 0
    item: str = ""


@dataclass(frozen=True)
class Model:
    name: str
    description: str
    props: tuple[Prop, ...]


_ROOT_KEYS = {"$schema", "$id", "title", "$defs"}
_OBJECT_KEYS = {"description", "type", "additionalProperties", "required", "properties"}
_CLASS_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_JSON_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Names Pydantic's BaseModel uses itself; a field with one of them would shadow it
_RESERVED = {"copy", "dict", "json", "schema", "validate", "construct", "fields", "parse_obj"}
_UNPORTABLE = re.compile(r"\\[dDwWsSbB]")


def _keys(node: dict[str, object], allowed: set[str], where: str) -> None:
    extra = set(node) - allowed
    if extra:
        raise SchemaError(f"{where}: keywords outside the subset: {', '.join(sorted(extra))}")


def _int(node: dict[str, object], key: str, where: str) -> int:
    value = node.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{where}: {key} must be an integer")
    return value


def _prop(name: str, node: object, defs: dict[str, object], where: str) -> Prop:
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: not an object")
    if not _JSON_NAME.fullmatch(name) or keyword.iskeyword(name) or name in _RESERVED:
        raise SchemaError(f"{where}: the name must be snake_case and free in Python and Pydantic")
    if name.startswith("model_"):
        raise SchemaError(f"{where}: names starting with model_ belong to Pydantic")
    description = node.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SchemaError(f"{where}: description missing")
    if "const" in node:
        _keys(node, {"description", "const"}, where)
        return Prop(name, description, "const", minimum=_int(node, "const", where))
    kind = node.get("type")
    if kind == "string":
        _keys(node, {"description", "type", "pattern", "enum"}, where)
        if ("pattern" in node) == ("enum" in node):
            raise SchemaError(f"{where}: a string needs exactly one of pattern and enum")
        if "enum" in node:
            values = node["enum"]
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(v, str) and v.isprintable() and '"' not in v for v in values)
            ):
                raise SchemaError(f"{where}: enum must be a list of plain strings")
            return Prop(name, description, "enum", values=tuple(values))
        pattern = node["pattern"]
        if not isinstance(pattern, str) or not pattern.startswith("^") or not pattern.endswith("$"):
            raise SchemaError(f"{where}: the pattern must be anchored with ^ and $")
        if pattern.endswith("\\$") or _UNPORTABLE.search(pattern) or '"' in pattern:
            raise SchemaError(f"{where}: the pattern uses an escape or quote outside the subset")
        re.compile(pattern)
        return Prop(name, description, "pattern", pattern=pattern)
    if kind == "integer":
        _keys(node, {"description", "type", "minimum", "maximum"}, where)
        low, high = _int(node, "minimum", where), _int(node, "maximum", where)
        if low > high:
            raise SchemaError(f"{where}: minimum above maximum")
        return Prop(name, description, "integer", minimum=low, maximum=high)
    if kind == "array":
        _keys(node, {"description", "type", "items", "minItems", "maxItems"}, where)
        items = node.get("items")
        ref = items.get("$ref") if isinstance(items, dict) and len(items) == 1 else None
        target = ref.removeprefix("#/$defs/") if isinstance(ref, str) else ""
        if not isinstance(ref, str) or not ref.startswith("#/$defs/") or target not in defs:
            raise SchemaError(f"{where}: items must be a single $ref to a $defs object")
        low, high = _int(node, "minItems", where), _int(node, "maxItems", where)
        if not 0 <= low <= high:
            raise SchemaError(f"{where}: minItems and maxItems out of order")
        return Prop(name, description, "array", minimum=low, maximum=high, item=target)
    raise SchemaError(f"{where}: type {kind!r} is outside the subset")


def _model(name: str, node: object, defs: dict[str, object], where: str, root: bool) -> Model:
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: not an object")
    _keys(node, _OBJECT_KEYS | (_ROOT_KEYS if root else set()), where)
    if not _CLASS_NAME.fullmatch(name):
        raise SchemaError(f"{where}: the name must be PascalCase")
    if node.get("type") != "object" or node.get("additionalProperties") is not False:
        raise SchemaError(f"{where}: an object with additionalProperties: false")
    properties = node.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise SchemaError(f"{where}: properties missing")
    if node.get("required") != list(properties):
        raise SchemaError(f"{where}: every property is required, listed in the same order")
    description = node.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SchemaError(f"{where}: description missing")
    props = tuple(_prop(k, v, defs, f"{where}.{k}") for k, v in properties.items())
    return Model(name, description, props)


def parse(schema: object, source: str) -> list[Model]:
    """The schema's models, its $defs first and the root last."""
    if not isinstance(schema, dict) or schema.get("$schema") != DRAFT:
        raise SchemaError(f"{source}: not a JSON Schema of draft 2020-12")
    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        raise SchemaError(f"{source}: $defs must be an object")
    title = schema.get("title")
    if not isinstance(title, str):
        raise SchemaError(f"{source}: title missing")
    models = [_model(k, v, defs, f"{source}#/$defs/{k}", False) for k, v in defs.items()]
    models.append(_model(title, schema, defs, source, True))
    names = [m.name for m in models]
    if len(set(names)) != len(names):
        raise SchemaError(f"{source}: a $defs name repeats the title")
    return models


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _wrap(text: str, indent: str, lead: str) -> list[str]:
    width = WIDTH - len(indent) - len(lead)
    return [f"{indent}{lead}{line}" for line in textwrap.wrap(" ".join(text.split()), width)]


# --- Python (Pydantic) -------------------------------------------------------------------------


def _py_const(model: Model, prop: Prop) -> str:
    return f"_{model.name.upper()}_{prop.json_name.upper()}"


def render_python(models: list[Model], schema: str) -> str:
    lines = [
        f"# Generated by `just contracts` from {schema}; do not edit.",
        f'"""The {models[-1].name} contract (ADR-0002 rule 2), generated from {schema}."""',
        "",
        "from typing import Annotated, Literal",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
    ]
    for model in models:
        for prop in model.props:
            if prop.kind == "pattern":
                lines.append(f'{_py_const(model, prop)} = r"{prop.pattern}"')
    for model in models:
        lines += ["", "", f"class {model.name}(BaseModel):"]
        lines += ['    """', *_wrap(model.description, "    ", ""), '    """', ""]
        lines.append('    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)')
        for prop in model.props:
            lines.append("")
            lines += _wrap(prop.description, "    ", "# ")
            if prop.kind == "const":
                kind = f"Literal[{prop.minimum}]"
            elif prop.kind == "pattern":
                kind = f"Annotated[str, Field(pattern={_py_const(model, prop)})]"
            elif prop.kind == "enum":
                kind = "Literal[" + ", ".join(json.dumps(v) for v in prop.values) + "]"
            elif prop.kind == "integer":
                kind = f"Annotated[int, Field(ge={prop.minimum}, le={prop.maximum})]"
            else:
                kind = (
                    f"Annotated[list[{prop.item}], "
                    f"Field(min_length={prop.minimum}, max_length={prop.maximum})]"
                )
            lines.append(f"    {prop.json_name}: {kind}")
    return "\n".join(lines) + "\n"


def render_python_package() -> str:
    return (
        "# Generated by `just contracts`; do not edit.\n"
        '"""The job contracts, generated from contracts/ (ADR-0002 rule 2)."""\n'
    )


# --- C# ----------------------------------------------------------------------------------------


def _xml(text: str) -> str:
    return " ".join(text.split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cs_type(prop: Prop) -> str:
    if prop.kind in ("pattern", "enum"):
        return "string"
    if prop.kind == "array":
        return f"IReadOnlyList<{prop.item}>"
    if prop.kind == "integer" and (prop.maximum > 2**31 - 1 or prop.minimum < -(2**31)):
        return "long"
    return "int"


def _cs_checks(prop: Prop) -> list[str]:
    member, name = _pascal(prop.json_name), prop.json_name
    if prop.kind == "const":
        return [
            f"        if ({member} != {prop.minimum})",
            f'            problems.Add($"{{at}}{name}: must be {prop.minimum}");',
        ]
    if prop.kind == "pattern":
        return [
            f"        if ({member} is null || !{member}Pattern().IsMatch({member}))",
            f'            problems.Add($"{{at}}{name}: does not match its pattern");',
        ]
    if prop.kind == "enum":
        return [
            f"        if ({member} is null || Array.IndexOf({member}Values, {member}) < 0)",
            f'            problems.Add($"{{at}}{name}: not one of its values");',
        ]
    if prop.kind == "integer":
        return [
            f"        if ({member} < {prop.minimum} || {member} > {prop.maximum})",
            f'            problems.Add($"{{at}}{name}: out of range");',
        ]
    return [
        f"        if ({member} is null || {member}.Count < {prop.minimum}"
        f" || {member}.Count > {prop.maximum})",
        f'            problems.Add($"{{at}}{name}: number of items out of range");',
        "        else",
        f"            for (var i = 0; i < {member}.Count; i++)",
        f"                if ({member}[i] is null)",
        f'                    problems.Add($"{{at}}{name}[{{i}}]: missing");',
        "                else",
        f'                    {member}[i].AddProblems(problems, $"{{at}}{name}[{{i}}].");',
    ]


def render_csharp(models: list[Model], schema: str, namespace: str) -> str:
    lines = [
        "// <auto-generated>",
        f"//   Generated by `just contracts` from {schema} (ADR-0002 rule 2); do not edit.",
        "// </auto-generated>",
        "#nullable enable",
        "",
        "using System;",
        "using System.Collections.Generic;",
        "using System.Text.Json.Serialization;",
        "using System.Text.RegularExpressions;",
        "",
        f"namespace {namespace};",
    ]
    for model in models:
        lines += ["", f"/// <summary>{_xml(model.description)}</summary>"]
        for prop in model.props:
            lines.append(
                f'/// <param name="{_pascal(prop.json_name)}">{_xml(prop.description)}</param>'
            )
        lines += [
            "[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]",
            f"public sealed partial record {model.name}(",
        ]
        params = [
            f'    [property: JsonPropertyName("{p.json_name}"), JsonRequired] '
            f"{_cs_type(p)} {_pascal(p.json_name)}"
            for p in model.props
        ]
        lines.append(",\n".join(params) + ")")
        lines.append("{")
        for prop in model.props:
            if prop.kind == "enum":
                values = ", ".join(json.dumps(v) for v in prop.values)
                lines.append(
                    f"    private static readonly string[] {_pascal(prop.json_name)}Values = "
                    f"[{values}];"
                )
                lines.append("")
        lines += [
            "    /// <summary>Every way this value breaks its schema; empty when it holds."
            "</summary>",
            "    public IReadOnlyList<string> Validate()",
            "    {",
            "        var problems = new List<string>();",
            '        AddProblems(problems, "");',
            "        return problems;",
            "    }",
            "",
            "    internal void AddProblems(List<string> problems, string at)",
            "    {",
        ]
        for prop in model.props:
            lines += _cs_checks(prop)
        lines.append("    }")
        for prop in model.props:
            if prop.kind == "pattern":
                # .NET's `$` also matches before a final line end; `\z` is the end of the text
                dotnet = prop.pattern[:-1] + "\\z"
                lines += [
                    "",
                    f'    [GeneratedRegex(@"{dotnet}", RegexOptions.CultureInvariant)]',
                    f"    private static partial Regex {_pascal(prop.json_name)}Pattern();",
                ]
        lines.append("}")
    return "\n".join(lines) + "\n"


# --- Files -------------------------------------------------------------------------------------


def outputs(repo: Path = REPO) -> dict[Path, str]:
    """Every generated file and its text."""
    files: dict[Path, str] = {repo / PYTHON_PACKAGE: render_python_package()}
    for target in TARGETS:
        source = repo / target.schema
        models = parse(json.loads(source.read_text(encoding="utf-8")), target.schema)
        files[repo / target.python] = render_python(models, target.schema)
        files[repo / target.csharp] = render_csharp(models, target.schema, target.namespace)
    return files


def stale(files: dict[Path, str]) -> list[Path]:
    return [
        path
        for path, text in files.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 when a file is stale")
    args = parser.parse_args(argv)
    try:
        files = outputs()
    except SchemaError as error:
        print(f"contracts: {error}", file=sys.stderr)
        return 2
    behind = stale(files)
    if args.check:
        for path in behind:
            print(f"contracts: stale: {path.relative_to(REPO).as_posix()}", file=sys.stderr)
        if behind:
            print("contracts: run `just contracts` and commit the result", file=sys.stderr)
        return 1 if behind else 0
    for path in behind:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(files[path])
        print(f"contracts: wrote {path.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
