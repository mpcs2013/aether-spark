"""Fixture tests for the usage-capture copy job (ADR-0013 amendment 6); no real folders."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

JOB = Path(__file__).resolve().parents[1] / "capture_copy.py"
ID_A = "20261021T090000Z-0123abcd"
ID_B = "20261021T100000Z-4567ef01"
NAME_A = f"capture-{ID_A}.json"
NAME_B = f"capture-{ID_B}.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_copy", JOB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_copy"] = module
    spec.loader.exec_module(module)
    return module


cc = _load()


def capture(capture_id: str = ID_A, **changes: Any) -> dict[str, Any]:
    """A capture that fits G-004 format 1.0.0; keyword arguments replace top-level fields."""
    stamp = capture_id[:16]
    captured_at = (
        f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
    )
    value: dict[str, Any] = {
        "format_version": "1.0.0",
        "project_id": "GL-0003",
        "capture_id": capture_id,
        "captured_at": captured_at,
        "issue": 42,
        "window": {"from": "2026-10-21T08:00:00Z", "to": "2026-10-21T08:30:00Z"},
        "usage": {
            "status": "complete",
            "by_model": [
                {
                    "model": "claude-opus-5-5",
                    "source": "main",
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_tokens": 30,
                    "cache_creation_tokens": 40,
                    "cache_creation_5m_tokens": 15,
                    "cache_creation_1h_tokens": 25,
                },
                {
                    "model": "claude-sonnet-5-5",
                    "source": "subagent",
                    "agent": "security-reviewer",
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                },
            ],
        },
    }
    value.update(changes)
    return value


def encode(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")


class Layout:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.source = root / "project" / "usage.local"
        self.source.mkdir(parents=True)
        self.intake = root / "intake"
        self.intake.mkdir()
        self.folders = root / "folders.txt"
        self.folders.write_text(f"# test list\nGL-0003\t{self.source}\n", encoding="utf-8")
        self.log = root / "run.log"

    def put(self, name: str = NAME_A, data: bytes | None = None) -> Path:
        path = self.source / name
        path.write_bytes(encode(capture(name[8:-5])) if data is None else data)
        return path

    def run(self) -> int:
        code: int = cc.run(self.folders, self.intake, self.log)
        return code

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        lines = self.log.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        return [r for r in records if kind is None or r["event"] == kind]

    def intake_names(self) -> list[str]:
        folder = self.intake / "GL-0003"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


def _link_folder(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined,unused-ignore]
    else:
        link.symlink_to(target, target_is_directory=True)


# --- copy, skip, conflict -------------------------------------------------------------------


def test_copies_a_capture_read_only_and_logs_its_hash(layout: Layout) -> None:
    data = layout.put().read_bytes()

    assert layout.run() == cc.EXIT_OK
    copied = layout.intake / "GL-0003" / NAME_A
    assert copied.read_bytes() == data
    assert not copied.stat().st_mode & stat.S_IWUSR
    assert [(e["name"], e["sha256"]) for e in layout.events("copied")] == [
        (NAME_A, cc.sha256(data))
    ]
    assert layout.events("end")[-1]["status"] == "ok"


def test_second_run_skips_the_same_hash(layout: Layout) -> None:
    layout.put()
    layout.run()

    assert layout.run() == cc.EXIT_OK
    assert len(layout.events("skipped")) == 1


def test_known_name_with_a_different_hash_stops_without_overwrite(layout: Layout) -> None:
    first = layout.put().read_bytes()
    layout.run()
    layout.put(data=encode(capture(issue=7)))
    layout.put(NAME_B)

    assert layout.run() == cc.EXIT_ERROR
    assert (layout.intake / "GL-0003" / NAME_A).read_bytes() == first
    assert NAME_B not in layout.intake_names()
    assert len(layout.events("conflict")) == 1


def test_no_temporary_files_left_and_stale_ones_removed(layout: Layout) -> None:
    (layout.intake / "GL-0003").mkdir()
    (layout.intake / "GL-0003" / ".tmp-0123456789abcdef").write_bytes(b"crash")
    layout.put()
    layout.put(NAME_B)

    layout.run()

    assert layout.intake_names() == [NAME_A, NAME_B]


def test_source_is_left_unchanged(layout: Layout) -> None:
    path = layout.put()
    before = (sorted(os.listdir(layout.source)), path.read_bytes(), path.stat().st_mtime_ns)

    layout.run()

    assert (sorted(os.listdir(layout.source)), path.read_bytes(), path.stat().st_mtime_ns) == before


# --- names ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ledger.json", f".{NAME_A}.tmp", f"{NAME_A}.part", "notes.txt"])
def test_other_names_are_not_read(layout: Layout, name: str) -> None:
    layout.put(name, b"x")

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == []


@pytest.mark.parametrize(
    "name",
    ["capture-ledger.json", "capture-20261021T090000Z-0123ABCD.json", "capture-٢٠٢٦.json"],
)
def test_capture_like_names_are_counted_as_ignored(layout: Layout, name: str) -> None:
    layout.put(name, b"x")

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []
    assert [e["count"] for e in layout.events("ignored")] == [1]
    assert name not in layout.log.read_text(encoding="utf-8")


# --- file rules -----------------------------------------------------------------------------


def test_file_over_1_mib_is_refused(layout: Layout) -> None:
    layout.put(data=b" " * (cc.MAX_BYTES + 1))
    layout.put(NAME_B)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == [NAME_B]
    assert [(e["name"], e["reason"]) for e in layout.events("refused")] == [
        (NAME_A, "larger than 1 MiB")
    ]


def test_file_of_exactly_1_mib_passes_the_size_rule(layout: Layout) -> None:
    data = encode(capture())
    layout.put(data=data + b" " * (cc.MAX_BYTES - len(data)))

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == [NAME_A]


def test_subfolder_with_a_capture_name_is_refused(layout: Layout) -> None:
    (layout.source / NAME_A).mkdir()

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []


def test_folder_link_with_a_capture_name_is_refused(layout: Layout, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link_folder(layout.source / NAME_A, elsewhere)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []


def test_hard_linked_file_is_refused(layout: Layout, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_bytes(encode(capture()))
    os.link(secret, layout.source / NAME_A)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.events("refused")[0]["reason"] == "more than one hard link"


def test_symbolic_link_is_refused(layout: Layout, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_bytes(encode(capture()))
    try:
        (layout.source / NAME_A).symlink_to(secret)
    except OSError:
        pytest.skip("creating symbolic links needs a privilege on this machine")

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []


def test_file_swapped_between_check_and_read_is_refused(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.put()
    real_fstat = os.fstat

    def swapped(fd: int) -> Any:
        st = real_fstat(fd)
        fields = list(st)
        fields[stat.ST_INO] = st.st_ino + 1
        return os.stat_result(fields)

    monkeypatch.setattr(cc.os, "fstat", swapped)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.events("refused")[0]["reason"] == "changed while being read"


def test_capture_folder_behind_a_junction_or_link_is_refused(
    layout: Layout, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / NAME_A).write_bytes(encode(capture()))
    linked = tmp_path / "linked"
    _link_folder(linked, elsewhere)
    layout.folders.write_text(f"GL-0003\t{linked}\n", encoding="utf-8")

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []


# --- intake side ----------------------------------------------------------------------------


def test_intake_project_folder_that_is_a_link_stops(layout: Layout, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link_folder(layout.intake / "GL-0003", elsewhere)
    layout.put()

    assert layout.run() == cc.EXIT_ERROR
    assert list(elsewhere.iterdir()) == []


def test_intake_entry_that_is_not_a_regular_file_stops(layout: Layout) -> None:
    (layout.intake / "GL-0003" / NAME_A).mkdir(parents=True)
    layout.put()

    assert layout.run() == cc.EXIT_ERROR


def test_missing_intake_root_stops(layout: Layout) -> None:
    layout.intake.rmdir()

    assert layout.run() == cc.EXIT_ERROR
    assert not layout.intake.exists()


# --- content (G-004 point 3) ----------------------------------------------------------------


def _without(key: str) -> Callable[[dict[str, Any]], None]:
    return lambda c: c.pop(key)


def _entries(c: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = c["usage"]["by_model"]
    return entries


def _entry(index: int, **changes: Any) -> Callable[[dict[str, Any]], None]:
    return lambda c: _entries(c)[index].update(changes)


CONTENT_CASES: dict[str, Callable[[dict[str, Any]], None]] = {
    "extra field": lambda c: c.update(branch="issue/42-secret-fix"),
    "missing field": _without("issue"),
    "other project": lambda c: c.update(project_id="GL-0002"),
    "id differs from name": lambda c: c.update(capture_id=ID_B),
    "id not from captured_at": lambda c: c.update(captured_at="2026-10-21T09:00:01Z"),
    "issue zero": lambda c: c.update(issue=0),
    "issue boolean": lambda c: c.update(issue=True),
    "major version 2": lambda c: c.update(format_version="2.0.0"),
    "window after capture": lambda c: c["window"].update(to="2026-10-21T10:00:00Z"),
    "window missing while complete": lambda c: c.update(window=None),
    "missing with entries": lambda c: c.update(
        window=None, usage=dict(c["usage"], status="missing", missing_reason="sandbox run")
    ),
    "partial without reason": lambda c: c["usage"].update(status="partial"),
    "reason not printable": lambda c: c["usage"].update(status="partial", missing_reason="a\tb"),
    "negative count": _entry(0, input_tokens=-1),
    "boolean count": _entry(0, output_tokens=False),
    "float count": _entry(0, output_tokens=1.5),
    "one TTL part": lambda c: c["usage"]["by_model"][0].pop("cache_creation_1h_tokens"),
    "TTL sum": _entry(0, cache_creation_5m_tokens=1),
    "agent on main": _entry(0, agent="architect"),
    "model with spaces": _entry(0, model="my secret value"),
    "model too long": _entry(0, model="m" * 101),
    "duplicate entry": lambda c: c["usage"]["by_model"].append(copy.deepcopy(_entries(c)[1])),
    "too many entries": lambda c: c["usage"]["by_model"].extend(
        {"model": f"m{i}", "source": "main", "input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_creation_tokens": 0}
        for i in range(cc.MAX_ENTRIES)
    ),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(CONTENT_CASES))
def test_capture_outside_the_format_is_refused(layout: Layout, case: str) -> None:
    value = capture()
    CONTENT_CASES[case](value)
    layout.put(data=encode(value))

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []
    assert len(layout.events("refused")) == 1


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe not utf-8",
        b"not json",
        b"[]",
        encode(capture())[:-1] + b', "issue": 43}',
        encode(capture()).replace(b'"issue": 42', b'"issue": NaN'),
    ],
    ids=["not utf-8", "not json", "not an object", "duplicate key", "NaN"],
)
def test_file_that_is_not_a_plain_capture_is_refused(layout: Layout, data: bytes) -> None:
    layout.put(data=data)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []


def test_valid_variants_are_copied(layout: Layout) -> None:
    missing = capture(
        ID_A, issue=None, window=None, usage={"status": "missing", "missing_reason": "sandbox run",
                                              "by_model": []}
    )  # fmt: skip
    offset = capture(ID_B, captured_at="2026-10-21T12:00:00+02:00")
    layout.put(NAME_A, encode(missing))
    layout.put(NAME_B, encode(offset))

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == [NAME_A, NAME_B]


# --- folder list, errors, log ---------------------------------------------------------------


def test_missing_capture_folder_is_logged_and_the_run_continues(
    layout: Layout, tmp_path: Path
) -> None:
    layout.folders.write_text(
        f"GL-0001\t{tmp_path / 'absent'}\nGL-0003\t{layout.source}\n", encoding="utf-8"
    )
    layout.put()

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == [NAME_A]
    assert len(layout.events("folder-missing")) == 1


@pytest.mark.parametrize(
    "content",
    ["../x\tC:/x\n", "GL-03\tC:/x\n", "GL-0003 C:/x\n", "GL-0003\t\n", "GL-0003\ta\nGL-0003\tb\n",
     "GL-٠٠٠٣\tC:/x\n"],
)  # fmt: skip
def test_bad_folder_list_stops_before_any_copy(layout: Layout, content: str) -> None:
    layout.put()
    layout.folders.write_text(content, encoding="utf-8")

    assert layout.run() == cc.EXIT_ERROR
    assert list(layout.intake.iterdir()) == []


def test_unexpected_error_ends_with_an_end_event(layout: Layout) -> None:
    layout.folders.write_bytes(b"\xff\xfe")

    assert layout.run() == cc.EXIT_ERROR
    assert layout.events("error")[0]["reason"] == "UnicodeDecodeError"
    assert layout.events("end")[0]["status"] == "error"


def test_log_holds_no_paths(layout: Layout) -> None:
    layout.put(data=b" " * (cc.MAX_BYTES + 1))
    layout.folders.write_bytes(b"\xff")
    layout.run()
    layout.folders.write_text(f"GL-0003\t{layout.source}\n", encoding="utf-8")
    layout.intake.rmdir()
    layout.run()
    layout.intake.mkdir()
    layout.run()

    text = layout.log.read_text(encoding="utf-8")
    for folder in (layout.root, layout.source, layout.intake):
        assert str(folder) not in text
        assert json.dumps(str(folder))[1:-1] not in text


def test_runs_in_isolated_mode_without_site(layout: Layout) -> None:
    layout.put()

    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-I", "-S", str(JOB), "--folders", str(layout.folders),
         "--intake", str(layout.intake), "--log", str(layout.log)],
        capture_output=True, check=False, timeout=60,
    )  # fmt: skip

    assert result.returncode == cc.EXIT_OK, result.stderr
    assert layout.intake_names() == [NAME_A]
