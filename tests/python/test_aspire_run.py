"""`aspire run` keeps in step with the stack it stands in for (data-plane.md, PR 3b2b)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPHOST = ROOT / "src" / "dotnet" / "AetherSpark.AppHost"


def _rules(path: Path) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_the_stand_in_sec_runs_in_the_stack_tests_pinned_python_image() -> None:
    layer3 = (ROOT / "tests" / "egress" / "compose.layer3.yaml").read_text(encoding="utf-8")
    program = (APPHOST / "Program.cs").read_text(encoding="utf-8")

    pinned = re.findall(
        r"^ *image: *python:([0-9a-z.-]+)@sha256:([0-9a-f]{64}) *$", layer3, re.MULTILINE
    )
    tags = re.findall(r'AddContainer\("sec-stub", "python", "([0-9a-z.-]+)"\)', program)
    digests = re.findall(r'WithImageSHA256\("([0-9a-f]{64})"\)', program)

    assert len(set(pinned)) == 1, pinned
    assert [(t, d) for t, d in zip(tags, digests, strict=True)] == [pinned[0]]


def test_the_development_pg_hba_differs_from_the_stacks_only_in_the_two_addresses() -> None:
    stack = _rules(ROOT / "deploy" / "compose" / "stack" / "postgres-data" / "pg_hba.conf")
    dev = _rules(APPHOST / "postgres-data" / "pg_hba.conf")

    assert len(dev) == len(stack)
    changed = [(s, d) for s, d in zip(stack, dev, strict=True) if s != d]
    assert changed, "the stack's file names its ingest network; the development file cannot"
    for s, d in changed:
        # Only the address of the ingester's and the pipeline's TLS rules: still hostssl, still
        # scram-sha-256, the same database and role
        assert s[0] == d[0] == "hostssl"
        assert s[1:3] == d[1:3] == ["research", s[2]]
        assert s[2] in {"ingester", "pipeline"}
        assert d[3] == "0.0.0.0/0"
        assert s[4:] == d[4:] == ["scram-sha-256"]
