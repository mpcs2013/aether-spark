"""Tests for the append-only proof's address handling (deploy/backup/append_only.py); no network."""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "append_only.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("append_only", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["append_only"] = module
    spec.loader.exec_module(module)
    return module


proof = _load()
SNAP = "ab" * 32


def test_the_login_leaves_the_address_and_goes_into_the_header() -> None:
    url, authorization = proof.target(
        "rest:http://me:p%40ss%2Fw@backup.example:8000/aetherspark/", SNAP
    )
    assert url == f"http://backup.example:8000/aetherspark/snapshots/{SNAP}"
    assert "p@ss" not in url and "me" not in url
    assert base64.b64decode(authorization.removeprefix("Basic ")) == b"me:p@ss/w"


def test_a_path_without_its_final_slash_is_completed() -> None:
    url, _ = proof.target("rest:http://me:pw@10.0.0.5:8000/repo", SNAP)
    assert url == f"http://10.0.0.5:8000/repo/snapshots/{SNAP}"


@pytest.mark.parametrize(
    ("repository", "snapshot"),
    [
        ("s3:http://me:pw@host/repo", SNAP),  # not a rest-server
        ("rest:ftp://me:pw@host/repo", SNAP),  # not http(s)
        ("rest:http://host:8000/repo", SNAP),  # no login
        ("rest:http://me:pw@host/repo", "abc"),  # not a snapshot id
        ("rest:http://me:pw@host/repo", "../config" + "0" * 55),
    ],
)
def test_unusable_input_is_refused_before_anything_is_sent(repository: str, snapshot: str) -> None:
    with pytest.raises(proof.InputError):
        proof.target(repository, snapshot)
