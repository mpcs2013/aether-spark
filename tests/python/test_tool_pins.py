"""Repo-level checks that pins kept in two places agree."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _devcontainer() -> str:
    return (ROOT / ".devcontainer" / "Dockerfile").read_text(encoding="utf-8")


def test_pre_commit_hook_uses_the_gitleaks_version_the_dev_container_builds() -> None:
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

    built = re.findall(r"^ARG GITLEAKS_VERSION=([0-9.]+)$", _devcontainer(), re.MULTILINE)
    hook_versions = re.findall(r'^expected="([0-9.]+)"$', hook, re.MULTILINE)

    assert len(built) == 1, built
    assert hook_versions == built


def test_ci_runs_the_dev_containers_gitleaks_build() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--target gitleaks-bin" in ci
    assert "ghcr.io/gitleaks/gitleaks" not in ci


def test_dev_container_sdk_satisfies_global_json() -> None:
    sdk = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]
    tags = re.findall(
        r"^FROM mcr\.microsoft\.com/dotnet/sdk:([0-9.]+)-noble@sha256:",
        _devcontainer(),
        re.MULTILINE,
    )

    def parse(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.split("."))

    # latestFeature: same major and minor, any feature band or patch at or above the minimum
    assert sdk["rollForward"] == "latestFeature"
    assert len(tags) == 1, tags
    assert parse(tags[0])[:2] == parse(sdk["version"])[:2]
    assert parse(tags[0]) >= parse(sdk["version"])
