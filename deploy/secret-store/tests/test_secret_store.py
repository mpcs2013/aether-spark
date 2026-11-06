"""Tests for the secrets mechanism (ADR-0010, compose-base.md "Secrets"). The round trip needs `age`
and a tmpfs (Linux); CI installs `age`, so there it may not skip."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "secret_store.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("secret_store", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["secret_store"] = module
    spec.loader.exec_module(module)
    return module


store = _load()


def _manifest(tmp_path: Path, *entries: dict[str, Any]) -> list[Any]:
    path = tmp_path / "secrets.manifest.json"
    path.write_text(json.dumps({"secrets": list(entries)}), encoding="utf-8")
    result: list[Any] = store.load_manifest(path)
    return result


GEN = {
    "name": "db-password",
    "consumers": ["keycloak"],
    "rotation_days": 365,
    "generator": {"type": "random", "bytes": 32},
}
DERIVED = {
    "name": "keycloak-db.conf",
    "consumers": ["keycloak"],
    "derived": "db-password={db-password}\n",
}


# The manifest


def test_the_repositorys_manifest_is_valid() -> None:
    assert isinstance(store.load_manifest(), list)


def test_a_generated_and_a_derived_secret_load(tmp_path: Path) -> None:
    gen, derived = _manifest(tmp_path, GEN, DERIVED)
    assert (gen.random_bytes, gen.derived) == (32, "")
    assert derived.derived == "db-password={db-password}\n"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({**GEN, "name": "DB_Password"}, "lower-case file name"),
        ({**GEN, "name": "../escape"}, "lower-case file name"),
        ({**GEN, "consumers": []}, "at least one service"),
        ({**GEN, "rotation_days": 0}, "positive number"),
        ({**GEN, "generator": {"type": "random", "bytes": 8}}, "128 bits"),
        ({**GEN, "generator": {"type": "fixed", "value": "x"}}, "generator must be"),
        ({**GEN, "derived": "x={db-password}"}, "exactly one"),
        ({"name": "x", "consumers": ["a"]}, "exactly one"),
        ({**GEN, "value": "secret"}, "keys must be"),
        ({"name": "c.conf", "consumers": ["a"], "derived": "no reference"}, "naming at least one"),
    ],
)
def test_unusable_entries_are_refused(tmp_path: Path, entry: dict[str, Any], message: str) -> None:
    with pytest.raises(store.InputError, match=message):
        _manifest(tmp_path, entry)


def test_a_derived_file_may_only_name_generated_secrets(tmp_path: Path) -> None:
    with pytest.raises(store.InputError, match="not generated secrets"):
        _manifest(tmp_path, {**DERIVED, "derived": "x={missing}"})


def test_a_name_listed_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(store.InputError, match="twice"):
        _manifest(tmp_path, GEN, GEN)


def test_rotate_refuses_unknown_and_derived_names(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, GEN, DERIVED)
    with pytest.raises(store.InputError, match="not in the manifest"):
        store.rotate(manifest, "missing")
    with pytest.raises(store.InputError, match="derived"):
        store.rotate(manifest, "keycloak-db.conf")


def test_render_fills_every_reference() -> None:
    assert store.render("a={x}\nb={y}\n", {"x": "1", "y": "2"}) == "a=1\nb=2\n"


# The in-memory folder


MOUNTINFO = (
    "22 1 8:2 / / rw,relatime - ext4 /dev/sdc rw\n"
    "30 22 0:25 / /run rw,nosuid - tmpfs tmpfs rw\n"
    "31 30 0:26 / /run/user/1000 rw,nosuid - tmpfs tmpfs rw,uid=1000\n"
    "40 22 0:30 / /var/tmp rw - ext4 /dev/sdc rw\n"
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/run/user/1000/aetherspark/secrets", True),
        ("/run/aetherspark/secrets", True),
        ("/var/tmp/aetherspark/secrets", False),  # noqa: S108 - a path string, nothing is written
        ("/home/dev/secrets", False),
        ("/runner/secrets", False),
    ],
)
def test_tmpfs_is_read_from_the_longest_mount_point(path: str, expected: bool) -> None:
    assert store.is_tmpfs(Path(path), MOUNTINFO) is expected


def test_unseal_refuses_a_folder_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(tmp_path / "secrets"))
    with pytest.raises(store.InputError, match="not on a tmpfs"):
        store.unseal([], mountinfo="22 1 8:2 / / rw - ext4 /dev/sdc rw\n")


# The check against rendered Compose files


def _project(**secrets: str) -> dict[str, Any]:
    return {
        "name": "aetherspark",
        "secrets": {k: {"file": f"/run/aetherspark/secrets/{v}"} for k, v in secrets.items()},
        "services": {"keycloak": {"secrets": [{"source": k} for k in secrets]}},
    }


def test_check_passes_when_manifest_and_compose_agree(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, GEN, DERIVED)
    project = _project(**{"db-password": "db-password", "keycloak-db.conf": "keycloak-db.conf"})
    assert store.check(manifest, [project]) == []


def test_check_finds_each_disagreement(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, GEN, DERIVED)
    project = _project(**{"db-password": "other-name", "extra": "extra"})
    project["services"]["grafana"] = {"secrets": ["db-password"]}
    assert store.check(manifest, [project]) == [
        "aetherspark: secret db-password must come from a file named db-password",
        "aetherspark: secret extra is not in the manifest",
        "aetherspark: service grafana reads db-password, not one of its consumers",
        "manifest: keycloak-db.conf is used by no Compose file",
    ]


def test_the_repositorys_manifest_loads() -> None:
    """The real manifest follows the rules; tests/egress/lint.sh compares it with the rendered
    Compose files."""
    manifest = store.load_manifest()
    assert manifest, "the manifest lists the stack's secrets"
    assert all(s.random_bytes >= store.MIN_BYTES or s.derived for s in manifest)


# The round trip with age, on a real tmpfs


def _usable_tmpfs() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and store.is_tmpfs(Path(runtime)):
        return Path(runtime)
    shm = Path("/dev/shm")  # noqa: S108 - a memory-backed folder, the point of this test
    if shm.is_dir() and store.is_tmpfs(shm):
        return shm
    return None


def test_ci_has_age_so_the_round_trip_never_skips_there() -> None:
    if os.environ.get("CI") == "true" and sys.platform == "linux":
        assert shutil.which("age") and shutil.which("age-keygen"), "CI must install age"


@pytest.mark.skipif(shutil.which("age") is None, reason="age is not installed (CI installs it)")
def test_generate_unseal_and_clear_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmpfs = _usable_tmpfs()
    if tmpfs is None:
        pytest.skip("no tmpfs on this machine")
    memory = tmpfs / f"aetherspark-test-{os.getpid()}"
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(memory / "secrets"))
    monkeypatch.setenv("AETHERSPARK_SECRETS_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("AETHERSPARK_SECRETS_KEY", str(tmp_path / "keys" / "dev.key"))
    manifest = _manifest(tmp_path, GEN, DERIVED)
    try:
        assert store.generate(manifest) == ["db-password"]
        assert store.generate(manifest) == []  # nothing replaced on a second run
        sealed = (tmp_path / "store" / "db-password.age").read_bytes()
        assert sealed.startswith(b"age-encryption.org/v1")
        assert oct((tmp_path / "keys" / "dev.key").stat().st_mode & 0o777) == "0o600"

        assert store.unseal(manifest) == ["db-password", "keycloak-db.conf"]
        folder = memory / "secrets"
        value = (folder / "db-password").read_text(encoding="utf-8")
        assert len(value) >= 40 and value.encode("ascii") not in sealed
        assert (folder / "keycloak-db.conf").read_text(encoding="utf-8") == f"db-password={value}\n"
        assert oct(folder.stat().st_mode & 0o777) == "0o700"
        assert {oct(p.stat().st_mode & 0o777) for p in folder.iterdir()} == {"0o400"}

        store.rotate(manifest, "db-password")
        assert (tmp_path / "store" / "db-password.age").read_bytes() != sealed
        assert store.unseal(manifest) == [
            "db-password",
            "keycloak-db.conf",
        ]  # a second unseal replaces
        assert (folder / "db-password").read_text(encoding="utf-8") != value
        assert store.clear() is True
        assert not folder.exists()
        assert store.clear() is False
    finally:
        shutil.rmtree(memory, ignore_errors=True)
