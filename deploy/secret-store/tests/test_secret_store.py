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


def test_a_derived_file_may_only_name_generated_or_entered_secrets(tmp_path: Path) -> None:
    with pytest.raises(store.InputError, match="not generated or entered"):
        _manifest(tmp_path, {**DERIVED, "derived": "x={missing}"})
    with pytest.raises(store.InputError, match="not generated or entered"):
        _manifest(
            tmp_path, GEN, DERIVED, {**DERIVED, "name": "y.conf", "derived": "{keycloak-db.conf}"}
        )


# Entered secrets (ADR-0010 note of 2026-11-11)

LOGIN = {"name": "backup-login-name", "consumers": ["restic"], "entered": {"kind": "name"}}
LOGIN_PW = {
    "name": "backup-login-password",
    "consumers": ["restic"],
    "entered": {"kind": "password"},
}
REPO = {
    "name": "backup-repository",
    "consumers": ["restic"],
    "derived": "rest:http://{backup-login-name|url}:{backup-login-password|url}@host:1/x/",
}


def test_an_entered_secret_and_a_file_built_from_it_load(tmp_path: Path) -> None:
    name, password, repo = _manifest(tmp_path, LOGIN, LOGIN_PW, REPO)
    assert (name.entered, password.entered, repo.entered) == ("name", "password", "")
    assert store.references(repo.derived) == {"backup-login-name", "backup-login-password"}


@pytest.mark.parametrize(
    "entered",
    [{"kind": "secret"}, {"kind": "name", "value": "x"}, "name", {}],
)
def test_an_unknown_entered_form_is_refused(tmp_path: Path, entered: Any) -> None:
    with pytest.raises(store.InputError, match="entered must be"):
        _manifest(tmp_path, {**LOGIN, "entered": entered})


def test_an_entry_is_exactly_one_kind(tmp_path: Path) -> None:
    with pytest.raises(store.InputError, match="exactly one"):
        _manifest(tmp_path, {**LOGIN, "generator": GEN["generator"]})


def test_url_references_are_percent_encoded() -> None:
    text = store.render("u={a|url} p={b|url} raw={b}", {"a": "me@x", "b": "p/@:#?%"})
    assert text == "u=me%40x p=p%2F%40%3A%23%3F%25 raw=p/@:#?%"


@pytest.mark.parametrize(
    ("kind", "value", "ok"),
    [
        ("name", "aetherspark", True),
        ("name", "a@b", False),
        ("name", "", False),
        ("password", "Correct-Horse-9!x", True),
        ("password", "short", False),
        ("password", "has a space in it", False),
        ("password", "tab\there-12345", False),
        ("address", "backup.example:8000", True),
        ("address", "10.0.0.5:8000", True),
        ("address", "10.0.0.5", False),
        ("address", "user@host:8000", False),
        ("path", "aetherspark/", True),
        ("path", "a/b", True),
        ("path", "/root", False),
        ("path", "a/../b", False),
        ("path", "a?b", False),
    ],
)
def test_entered_values_have_their_kinds_form(kind: str, value: str, ok: bool) -> None:
    if ok:
        store.check_entered(kind, value)
    else:
        with pytest.raises(store.InputError) as raised:
            store.check_entered(kind, value)
        if value:
            assert value not in str(raised.value)  # the message never repeats the value


def test_entered_secrets_are_not_rotated(tmp_path: Path) -> None:
    with pytest.raises(store.InputError, match="just secrets enter"):
        store.rotate(_manifest(tmp_path, LOGIN), "backup-login-name")


def test_enter_refuses_a_pipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(store.ENTER_FD, raising=False)
    # A regular file, as from `enter NAME < file`; os.devnull would read as a terminal on Windows
    piped = tmp_path / "value"
    piped.write_text("aetherspark\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", piped.open(encoding="utf-8"))
    with pytest.raises(store.InputError, match="terminal only"):
        store.enter(_manifest(tmp_path, LOGIN), "backup-login-name")
    with pytest.raises(store.InputError, match="not an entered secret"):
        store.enter(_manifest(tmp_path, GEN), "db-password")


@pytest.mark.skipif(sys.platform == "win32", reason="the in-memory folder exists only on Linux")
def test_unseal_leaves_out_what_is_not_entered_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just up` from a clean clone works before the backup login is entered (C1)."""
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "backup-repository").write_text("stale")  # from an earlier login
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(secrets))
    monkeypatch.setenv("AETHERSPARK_SECRETS_STORE", str(tmp_path / "store"))
    manifest = _manifest(tmp_path, LOGIN, LOGIN_PW, REPO)
    assert store.not_entered(manifest) == ["backup-login-name", "backup-login-password"]
    mountinfo = f"22 1 0:25 / {tmp_path.as_posix()} rw - tmpfs tmpfs rw\n"
    assert store.unseal(manifest, mountinfo=mountinfo) == []
    assert not (secrets / "backup-repository").exists()


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


@pytest.mark.skipif(sys.platform == "win32", reason="the in-memory folder exists only on Linux")
def test_a_folder_where_a_secret_file_belongs_is_named_for_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker leaves an empty folder per secret file when it restarts a container too early."""
    secrets = tmp_path / "aetherspark" / "secrets"
    (secrets / "step-ca-password").mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(secrets))
    with pytest.raises(store.InputError, match="Docker created"):
        store.unseal([], mountinfo="22 1 0:25 / / rw - tmpfs tmpfs rw\n")


@pytest.mark.skipif(sys.platform == "win32", reason="the in-memory folder exists only on Linux")
@pytest.mark.parametrize(
    ("distro", "command"),
    [("Ubuntu-24.04", "wsl -d Ubuntu-24.04 -u root -- rm -r '{}'"), ("", "sudo rm -r '{}'")],
)
def test_clear_names_docker_made_folders_with_the_command_for_this_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, distro: str, command: str
) -> None:
    """`just down` met them on 2026-11-10 and stopped with a bare PermissionError."""
    secrets = tmp_path / "aetherspark" / "secrets"
    (secrets / "keycloak.conf").mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(secrets))
    monkeypatch.setenv("WSL_DISTRO_NAME", distro)
    with pytest.raises(store.InputError) as raised:
        store.clear()
    assert "Docker created" in str(raised.value)
    assert command.format(secrets) in str(raised.value)
    assert (secrets / "keycloak.conf").is_dir()  # nothing is removed


@pytest.mark.skipif(sys.platform == "win32", reason="the in-memory folder exists only on Linux")
def test_a_clean_secrets_folder_is_left_alone(tmp_path: Path) -> None:
    folder = tmp_path / "secrets"
    folder.mkdir()
    (folder / "a-secret").write_text("x")
    assert store._docker_made(folder) is None
    assert store._docker_made(tmp_path / "missing") is None


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


def test_a_secret_used_only_inside_a_mounted_derived_file_counts_as_used(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, GEN, DERIVED)
    assert store.check(manifest, [_project(**{"keycloak-db.conf": "keycloak-db.conf"})]) == []
    # ... but not when the derived file itself is mounted nowhere
    assert store.check(manifest, [_project()]) == [
        "manifest: db-password is used by no Compose file",
        "manifest: keycloak-db.conf is used by no Compose file",
    ]


def test_a_secret_read_only_by_a_host_tool_needs_no_compose_file(tmp_path: Path) -> None:
    host_only = {**GEN, "name": "tool-secret", "consumers": ["host:keycloak-admin"]}
    manifest = _manifest(tmp_path, host_only)
    assert store.check(manifest, [_project()]) == []
    # ... and a service mounting it is not one of its consumers
    assert store.check(manifest, [_project(**{"tool-secret": "tool-secret"})]) == [
        "aetherspark: service keycloak reads tool-secret, not one of its consumers"
    ]


def test_the_repositorys_manifest_loads() -> None:
    """The real manifest follows the rules; tests/egress/lint.sh compares it with the rendered
    Compose files."""
    manifest = store.load_manifest()
    assert manifest, "the manifest lists the stack's secrets"
    assert all(s.random_bytes >= store.MIN_BYTES or s.derived or s.entered for s in manifest)


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


def _enter_through_fd(
    monkeypatch: pytest.MonkeyPatch, manifest: list[Any], name: str, value: str
) -> None:
    """As CI does: the value through a pipe's file descriptor, never an argument."""
    read, write = os.pipe()
    os.write(write, (value + "\n").encode())
    os.close(write)
    monkeypatch.setenv(store.ENTER_FD, str(read))
    try:
        store.enter(manifest, name)
    finally:
        os.close(read)


@pytest.mark.skipif(shutil.which("age") is None, reason="age is not installed (CI installs it)")
def test_enter_seals_and_unseal_builds_the_repository_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmpfs = _usable_tmpfs()
    if tmpfs is None:
        pytest.skip("no tmpfs on this machine")
    memory = tmpfs / f"aetherspark-test-enter-{os.getpid()}"
    monkeypatch.setenv("AETHERSPARK_SECRETS_DIR", str(memory / "secrets"))
    monkeypatch.setenv("AETHERSPARK_SECRETS_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("AETHERSPARK_SECRETS_KEY", str(tmp_path / "keys" / "dev.key"))
    manifest = _manifest(tmp_path, LOGIN, LOGIN_PW, REPO)
    try:
        assert store.generate(manifest) == []  # entered secrets are never generated
        _enter_through_fd(monkeypatch, manifest, "backup-login-name", "aetherspark")
        _enter_through_fd(monkeypatch, manifest, "backup-login-password", "p@ss/w:rd#12345")
        sealed = (tmp_path / "store" / "backup-login-password.age").read_bytes()
        assert sealed.startswith(b"age-encryption.org/v1") and b"p@ss" not in sealed
        assert store.not_entered(manifest) == []
        assert store.unseal(manifest) == list(s.name for s in manifest)
        repo = (memory / "secrets" / "backup-repository").read_text(encoding="utf-8")
        assert repo == "rest:http://aetherspark:p%40ss%2Fw%3Ard%2312345@host:1/x/"
        with pytest.raises(store.InputError, match="not a valid password"):
            _enter_through_fd(monkeypatch, manifest, "backup-login-password", "short")
        # A refused value leaves the sealed one as it was
        assert (tmp_path / "store" / "backup-login-password.age").read_bytes() == sealed
    finally:
        store.clear()
        shutil.rmtree(memory, ignore_errors=True)
