"""Repo-level checks that pins kept in two places agree."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pre_commit_hook_uses_the_gitleaks_version_ci_pins() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

    ci_versions = set(re.findall(r"ghcr\.io/gitleaks/gitleaks:v([0-9.]+)@sha256:", ci))
    hook_versions = re.findall(r'^expected="([0-9.]+)"$', hook, re.MULTILINE)

    assert len(ci_versions) == 1, ci_versions
    assert hook_versions == list(ci_versions)
