"""Tests for the local folder feed (deploy/packages/local_feed.py); no dotnet, no network.
The restore from the feed alone runs in CI as `just local-feed-proof`."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "local_feed.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("local_feed", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["local_feed"] = module
    spec.loader.exec_module(module)
    return module


feed_tool = _load()


def _hash(data: bytes) -> str:
    return base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _lock(root: Path, project: str, packages: dict[str, tuple[str, str]]) -> Path:
    """A packages.lock.json in root/project naming {id: (version, contentHash)}."""
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    entries = {
        name: {"type": "Direct", "resolved": version, "contentHash": digest}
        for name, (version, digest) in packages.items()
    }
    path = folder / "packages.lock.json"
    path.write_text(json.dumps({"version": 2, "dependencies": {"net10.0": entries}}))
    (folder / f"{Path(project).name}.csproj").write_text("<Project />")
    return path


def _nupkg(packages: Path, package: str, version: str, data: bytes) -> Path:
    folder = packages / package.lower() / version.lower()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{package.lower()}.{version.lower()}.nupkg"
    path.write_bytes(data)
    return path


REALM = b"the realm package"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _lock(
        root,
        "src/AppHost",
        {
            "Ostium.Keycloak.Realm": ("0.5.0", _hash(REALM)),
            "Aspire.Hosting": ("13.6.5", _hash(b"not ours")),
        },
    )
    return root


def test_only_ostium_packages_are_locked_and_build_output_is_skipped(repo: Path) -> None:
    _lock(repo, "src/AppHost/obj", {"Ostium.Stale": ("0.0.1", _hash(b"old"))})
    locked = feed_tool.locked_ostium(repo)
    assert [(i.package, i.version) for i in locked] == [("Ostium.Keycloak.Realm", "0.5.0")]
    assert locked[0].file_name == "ostium.keycloak.realm.0.5.0.nupkg"


def test_two_projects_with_the_same_package_give_one_entry(repo: Path) -> None:
    _lock(repo, "tests/AppHost.Tests", {"Ostium.Keycloak.Realm": ("0.5.0", _hash(REALM))})
    assert len(feed_tool.locked_ostium(repo)) == 1
    projects = feed_tool.projects_using_ostium(repo)
    assert sorted(p.name for p in projects) == ["AppHost.Tests.csproj", "AppHost.csproj"]
    assert all(len(items) == 1 for items in projects.values())


def test_lock_files_that_disagree_on_a_hash_are_unusable(repo: Path) -> None:
    _lock(repo, "tests/AppHost.Tests", {"Ostium.Keycloak.Realm": ("0.5.0", _hash(b"other"))})
    with pytest.raises(feed_tool.InputError, match="disagree"):
        feed_tool.locked_ostium(repo)


def test_a_locked_package_without_a_hash_is_unusable(repo: Path) -> None:
    _lock(repo, "tests/AppHost.Tests", {"Ostium.Other": ("1.0.0", "")})
    with pytest.raises(feed_tool.InputError, match="no version or hash"):
        feed_tool.locked_ostium(repo)


def test_sync_copies_a_package_whose_hash_matches(repo: Path, tmp_path: Path) -> None:
    packages, feed = tmp_path / "packages", tmp_path / "feed"
    _nupkg(packages, "Ostium.Keycloak.Realm", "0.5.0", REALM)
    assert feed_tool.sync(feed, packages, repo) == ["ostium.keycloak.realm.0.5.0.nupkg"]
    assert (feed / "ostium.keycloak.realm.0.5.0.nupkg").read_bytes() == REALM
    assert feed_tool.check(feed, repo) == []
    # A second sync has nothing to copy
    assert feed_tool.sync(feed, packages, repo) == []


def test_sync_refuses_a_source_that_differs_from_the_lock_file(repo: Path, tmp_path: Path) -> None:
    packages, feed = tmp_path / "packages", tmp_path / "feed"
    _nupkg(packages, "Ostium.Keycloak.Realm", "0.5.0", b"tampered")
    with pytest.raises(feed_tool.CheckFailed, match="not copied"):
        feed_tool.sync(feed, packages, repo)
    assert not (feed / "ostium.keycloak.realm.0.5.0.nupkg").exists()


def test_sync_refuses_a_feed_file_that_differs_and_leaves_it(repo: Path, tmp_path: Path) -> None:
    packages, feed = tmp_path / "packages", tmp_path / "feed"
    _nupkg(packages, "Ostium.Keycloak.Realm", "0.5.0", REALM)
    feed.mkdir()
    (feed / "ostium.keycloak.realm.0.5.0.nupkg").write_bytes(b"changed in the feed")
    with pytest.raises(feed_tool.CheckFailed, match="in the feed differs"):
        feed_tool.sync(feed, packages, repo)
    assert (feed / "ostium.keycloak.realm.0.5.0.nupkg").read_bytes() == b"changed in the feed"


def test_sync_without_a_restored_package_says_restore_first(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(feed_tool.InputError, match="restore the solution first"):
        feed_tool.sync(tmp_path / "feed", tmp_path / "packages", repo)


def test_sync_never_removes_an_older_version(repo: Path, tmp_path: Path) -> None:
    packages, feed = tmp_path / "packages", tmp_path / "feed"
    _nupkg(packages, "Ostium.Keycloak.Realm", "0.5.0", REALM)
    feed.mkdir()
    older = feed / "ostium.keycloak.realm.0.4.0.nupkg"
    older.write_bytes(b"the version before")
    feed_tool.sync(feed, packages, repo)
    assert older.read_bytes() == b"the version before"


def test_check_names_missing_and_differing_packages(repo: Path, tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    missing = "ostium.keycloak.realm.0.5.0.nupkg: missing from the feed"
    assert feed_tool.check(feed, repo) == [missing]
    (feed / "ostium.keycloak.realm.0.5.0.nupkg").write_bytes(b"other")
    assert feed_tool.check(feed, repo) == [
        "ostium.keycloak.realm.0.5.0.nupkg: differs from the lock file's hash"
    ]


def test_remove_takes_one_version_out(tmp_path: Path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    (feed / "ostium.keycloak.realm.0.4.0.nupkg").write_bytes(b"withdrawn")
    (feed / "ostium.keycloak.realm.0.5.0.nupkg").write_bytes(REALM)
    assert feed_tool.remove(feed, "Ostium.Keycloak.Realm", "0.4.0") is True
    assert feed_tool.remove(feed, "Ostium.Keycloak.Realm", "0.4.0") is False
    assert sorted(p.name for p in feed.iterdir()) == ["ostium.keycloak.realm.0.5.0.nupkg"]


def test_the_proof_config_has_no_private_source(tmp_path: Path) -> None:
    config = feed_tool.nuget_config(tmp_path / "feed")
    assert "<clear />" in config
    assert "nuget.pkg.github.com" not in config
    assert "packageSourceCredentials" not in config
    assert '<packageSource key="local"><package pattern="Ostium.*" /></packageSource>' in config
    assert '<packageSource key="nuget.org"><package pattern="*" /></packageSource>' in config


def test_the_proof_stops_before_any_restore_when_the_feed_lacks_a_package(
    repo: Path, tmp_path: Path
) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    with pytest.raises(feed_tool.CheckFailed, match="missing from the feed"):
        feed_tool.prove(feed, repo)
