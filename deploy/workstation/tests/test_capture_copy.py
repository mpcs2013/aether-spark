"""Fixture tests for the usage-capture copy job (ADR-0013 amendment 6); no real folders."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
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
LATER = "2026-10-21T09:00:01Z"  # one second after ID_A's capture time
AFTER = "2026-10-21T10:00:00Z"  # after ID_A's capture time
TAB = "a\tb"
BEFORE_YEAR_1 = "0001-01-01T00:00:00+01:00"  # valid text, but before year 1 once in UTC
AFTER_YEAR_9999 = "9999-12-31T23:59:59-01:00"


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
        """Captures in the intake; refused markers are read with markers()."""
        folder = self.intake / "GL-0003"
        if not folder.exists():
            return []
        return sorted(p.name for p in folder.iterdir() if not p.name.startswith("refused-"))

    def markers(self) -> list[dict[str, Any]]:
        folder = self.intake / "GL-0003"
        if not folder.exists():
            return []
        paths = sorted(folder.glob("refused-*.json"))
        return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


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
def test_capture_like_name_leaves_a_marker_without_the_name(layout: Layout, name: str) -> None:
    layout.put(name, b"x")

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []
    assert [(m["capture_id"], m["reason"]) for m in layout.markers()] == [(None, "name-form")]
    marker_text = "".join(p.read_text(encoding="utf-8") for p in layout.intake.rglob("refused-*"))
    for text in (marker_text, layout.log.read_text(encoding="utf-8")):
        assert name not in text
        assert json.dumps(name)[1:-1] not in text


# --- refused markers (exchange A-006) -------------------------------------------------------


def test_marker_has_exactly_the_five_fields(layout: Layout) -> None:
    layout.put(data=b"not json")

    layout.run()

    (marker,) = layout.markers()
    assert marker == {
        "marker_version": "1.0.0",
        "project_id": "GL-0003",
        "capture_id": ID_A,
        "reason": "not-json",
        "first_seen": marker["first_seen"],
    }
    assert re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", marker["first_seen"]
    )


def test_marker_is_written_once_and_read_only(layout: Layout) -> None:
    layout.put(data=b"not json")
    layout.run()
    (path,) = (layout.intake / "GL-0003").glob("refused-*.json")
    first = path.read_bytes()

    assert layout.run() == cc.EXIT_REFUSED
    assert list((layout.intake / "GL-0003").glob("refused-*.json")) == [path]
    assert path.read_bytes() == first
    assert not path.stat().st_mode & stat.S_IWUSR
    assert len(layout.events("marker")) == 1


def test_new_reason_for_the_same_file_gives_a_new_marker(layout: Layout) -> None:
    layout.put(data=b"not json")
    layout.run()
    layout.put(data=b" " * (cc.MAX_BYTES + 1))

    layout.run()

    assert sorted(m["reason"] for m in layout.markers()) == ["not-json", "too-large"]


def test_marker_name_depends_on_project_name_and_code() -> None:
    names = {
        cc.marker_name("GL-0003", NAME_A, "schema"),
        cc.marker_name("GL-0004", NAME_A, "schema"),
        cc.marker_name("GL-0003", NAME_B, "schema"),
        cc.marker_name("GL-0003", NAME_A, "cap"),
    }
    assert len(names) == 4
    assert all(re.fullmatch(r"refused-[0-9a-f]{16}\.json", n) for n in names)


def test_marker_name_taken_by_a_folder_stops(layout: Layout) -> None:
    layout.put(data=b"not json")
    (layout.intake / "GL-0003" / cc.marker_name("GL-0003", NAME_A, "not-json")).mkdir(parents=True)

    assert layout.run() == cc.EXIT_ERROR


def test_every_marker_reason_is_a_known_code(layout: Layout, tmp_path: Path) -> None:
    layout.put(data=b"not json")
    layout.put(NAME_B, data=b" " * (cc.MAX_BYTES + 1))
    layout.put("capture-ledger.json", b"x")
    (layout.source / "capture-20261021T110000Z-89abcdef.json").mkdir()

    layout.run()

    reasons = sorted(m["reason"] for m in layout.markers())
    assert reasons == ["name-form", "not-json", "not-regular", "too-large"]
    assert set(reasons) <= set(cc.REASON_CODES)


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
    assert [m["reason"] for m in layout.markers()] == ["link"]


def test_hard_linked_file_is_refused(layout: Layout, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_bytes(encode(capture()))
    os.link(secret, layout.source / NAME_A)

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.events("refused")[0]["reason"] == "more than one hard link"
    assert [m["reason"] for m in layout.markers()] == ["hard-link"]


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
    assert [m["reason"] for m in layout.markers()] == ["changed-while-read"]


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
    assert [(m["capture_id"], m["reason"]) for m in layout.markers()] == [(None, "link")]


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


CONTENT_CASES: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "extra field": ("schema", lambda c: c.update(branch="issue/42-secret-fix")),
    "missing field": ("schema", _without("issue")),
    "other project": ("project-mismatch", lambda c: c.update(project_id="GL-0002")),
    "project id not a string": ("schema", lambda c: c.update(project_id=3)),
    "time before year 1 in UTC": ("schema", lambda c: c.update(captured_at=BEFORE_YEAR_1)),
    "time after year 9999 in UTC": ("schema", lambda c: c.update(captured_at=AFTER_YEAR_9999)),
    "bracket inside model": ("cap", _entry(0, model="claude[1m]-x")),
    "upper-case suffix": ("cap", _entry(0, model="claude[1M]")),
    "two plugin levels": ("cap", _entry(1, agent="a:b:c")),
    "empty plugin": ("cap", _entry(1, agent=":agent")),
    "id differs from name": ("cross-check", lambda c: c.update(capture_id=ID_B)),
    "id not from captured_at": ("cross-check", lambda c: c.update(captured_at=LATER)),
    "issue zero": ("schema", lambda c: c.update(issue=0)),
    "issue boolean": ("schema", lambda c: c.update(issue=True)),
    "major version 2": ("schema", lambda c: c.update(format_version="2.0.0")),
    "window after capture": ("cross-check", lambda c: c["window"].update(to=AFTER)),
    "window missing while complete": ("cross-check", lambda c: c.update(window=None)),
    "missing with entries": ("cross-check", lambda c: c.update(
        window=None, usage=dict(c["usage"], status="missing", missing_reason="sandbox run")
    )),
    "partial without reason": ("cross-check", lambda c: c["usage"].update(status="partial")),
    "reason not printable": ("cap", lambda c: c["usage"].update(status="partial",
                                                                missing_reason=TAB)),
    "reason empty": ("schema", lambda c: c["usage"].update(status="partial", missing_reason="")),
    "negative count": ("schema", _entry(0, input_tokens=-1)),
    "boolean count": ("schema", _entry(0, output_tokens=False)),
    "float count": ("schema", _entry(0, output_tokens=1.5)),
    "one TTL part": ("cross-check", lambda c: _entries(c)[0].pop("cache_creation_1h_tokens")),
    "TTL sum": ("cross-check", _entry(0, cache_creation_5m_tokens=1)),
    "agent on main": ("cross-check", _entry(0, agent="architect")),
    "model not a string": ("schema", _entry(0, model=5)),
    "model with spaces": ("cap", _entry(0, model="my secret value")),
    "model too long": ("cap", _entry(0, model="m" * 101)),
    "agent too long": ("cap", _entry(1, agent="a" * 101)),
    "agent with a slash": ("cap", _entry(1, agent="plugin/agent")),
    "duplicate entry": ("cross-check", lambda c: _entries(c).append(copy.deepcopy(_entries(c)[1]))),
    "too many entries": ("cap", lambda c: _entries(c).extend(
        {"model": f"m{i}", "source": "main", "input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_creation_tokens": 0}
        for i in range(cc.MAX_ENTRIES)
    )),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(CONTENT_CASES))
def test_capture_outside_the_format_is_refused(layout: Layout, case: str) -> None:
    code, change = CONTENT_CASES[case]
    value = capture()
    change(value)
    layout.put(data=encode(value))

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.intake_names() == []
    assert len(layout.events("refused")) == 1
    assert [(m["capture_id"], m["reason"]) for m in layout.markers()] == [(ID_A, code)]


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
    expected = "schema" if data == b"[]" else "not-json"
    assert [m["reason"] for m in layout.markers()] == [expected]


def test_plugin_agent_and_bracketed_model_pass(layout: Layout) -> None:
    value = capture()
    _entries(value)[0]["model"] = "claude-opus-5-5[1m]"
    _entries(value)[1]["agent"] = "marketing:brand-review"
    layout.put(data=encode(value))

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == [NAME_A]
    assert layout.markers() == []


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


# --- review of #22: robustness, flood, planted markers --------------------------------------


def test_integer_past_the_digit_limit_is_refused_as_not_json(layout: Layout) -> None:
    layout.put(data=encode(capture()).replace(b'"issue": 42', b'"issue": ' + b"1" * 5000))

    assert layout.run() == cc.EXIT_REFUSED
    assert [m["reason"] for m in layout.markers()] == ["not-json"]


def test_caps_pass_at_100_characters(layout: Layout) -> None:
    value = capture()
    _entries(value)[0]["model"] = "m" * 96 + "[1m]"
    _entries(value)[1]["agent"] = "p" * 50 + ":" + "a" * 49
    layout.put(data=encode(value))

    assert layout.run() == cc.EXIT_OK
    assert layout.intake_names() == [NAME_A]


def test_one_failing_project_does_not_stop_the_others(layout: Layout, tmp_path: Path) -> None:
    other = tmp_path / "other" / "usage.local"
    other.mkdir(parents=True)
    (other / NAME_A).write_bytes(encode(capture(project_id="GL-0001")))
    (layout.intake / "GL-0001" / NAME_A).mkdir(parents=True)  # makes GL-0001 stop
    layout.folders.write_text(f"GL-0001\t{other}\nGL-0003\t{layout.source}\n", encoding="utf-8")
    layout.put()

    assert layout.run() == cc.EXIT_ERROR
    assert layout.intake_names() == [NAME_A]
    assert [e["project"] for e in layout.events("error")] == ["GL-0001"]


def test_unexpected_error_in_one_project_is_isolated(
    layout: Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other" / "usage.local"
    other.mkdir(parents=True)
    layout.folders.write_text(f"GL-0001\t{other}\nGL-0003\t{layout.source}\n", encoding="utf-8")
    layout.put()
    real = cc.copy_project

    def failing(source: Any, *args: Any) -> None:
        if source.project_id == "GL-0001":
            raise RuntimeError("boom")
        real(source, *args)

    monkeypatch.setattr(cc, "copy_project", failing)

    assert layout.run() == cc.EXIT_ERROR
    assert layout.intake_names() == [NAME_A]
    assert layout.events("error") == [
        {"at": layout.events("error")[0]["at"], "event": "error", "project": "GL-0001",
         "reason": "RuntimeError"}
    ]  # fmt: skip


def test_refusals_past_the_cap_leave_no_markers(layout: Layout) -> None:
    for n in range(cc.MAX_REFUSALS + 20):
        layout.put(f"capture-20261021T090000Z-{n:08x}.json", b"not json")

    assert layout.run() == cc.EXIT_REFUSED
    assert len(layout.markers()) == cc.MAX_REFUSALS
    assert len(layout.events("refused")) == cc.MAX_REFUSALS
    assert [e["not_marked"] for e in layout.events("refused-cap")] == [20]


def test_out_of_form_names_share_one_marker_per_code(layout: Layout) -> None:
    for name in ("capture-a.json", "capture-b.json", "capture-secret-token.json"):
        layout.put(name, b"x")

    assert layout.run() == cc.EXIT_REFUSED
    assert [(m["capture_id"], m["reason"]) for m in layout.markers()] == [(None, "name-form")]
    assert cc.marker_name("GL-0003", "capture-a.json", "name-form") == cc.marker_name(
        "GL-0003", "capture-b.json", "name-form"
    )


def test_valid_planted_marker_is_kept(layout: Layout) -> None:
    layout.put(data=b"not json")
    planted = {"marker_version": "1.0.0", "project_id": "GL-0003", "capture_id": ID_A,
               "reason": "not-json", "first_seen": "2026-10-01T00:00:00Z"}  # fmt: skip
    folder = layout.intake / "GL-0003"
    folder.mkdir()
    (folder / cc.marker_name("GL-0003", NAME_A, "not-json")).write_text(json.dumps(planted))

    assert layout.run() == cc.EXIT_REFUSED
    assert layout.markers() == [planted]


@pytest.mark.parametrize(
    "content",
    [
        b"free text",
        json.dumps({"marker_version": "1.0.0", "project_id": "GL-0003", "capture_id": ID_A,
                    "reason": "not-json", "first_seen": "2026-10-01T00:00:00Z",
                    "note": "ignore previous instructions"}).encode(),
        json.dumps({"marker_version": "1.0.0", "project_id": "GL-0003", "capture_id": ID_A,
                    "reason": "other", "first_seen": "2026-10-01T00:00:00Z"}).encode(),
    ],
    ids=["not json", "extra field", "unknown code"],
)  # fmt: skip
def test_invalid_planted_marker_stops_the_project(layout: Layout, content: bytes) -> None:
    layout.put(data=b"not json")
    folder = layout.intake / "GL-0003"
    folder.mkdir()
    (folder / cc.marker_name("GL-0003", NAME_A, "not-json")).write_bytes(content)

    assert layout.run() == cc.EXIT_ERROR
    assert layout.events("error")[0]["project"] == "GL-0003"


def test_capture_folder_that_is_a_file_is_refused(layout: Layout, tmp_path: Path) -> None:
    not_a_folder = tmp_path / "usage.local"
    not_a_folder.write_bytes(b"x")
    layout.folders.write_text(f"GL-0003\t{not_a_folder}\n", encoding="utf-8")

    assert layout.run() == cc.EXIT_REFUSED
    assert [(m["capture_id"], m["reason"]) for m in layout.markers()] == [(None, "not-regular")]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file names are always Unicode")
def test_undecodable_name_is_refused_without_a_crash(layout: Layout) -> None:
    raw = os.fsencode(layout.source) + b"/capture-\xff.json"
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)

    assert layout.run() == cc.EXIT_REFUSED
    assert [m["reason"] for m in layout.markers()] == ["name-form"]


def test_every_reason_code_in_the_source_is_known() -> None:
    import ast

    tree = ast.parse(JOB.read_text(encoding="utf-8"))
    codes: set[str] = set()
    # Code literal positions: RefusedError(code, …), _require(…, …, code), x.refuse(_, code, _)
    positions = {"RefusedError": 0, "_require": 2, "refuse": 1}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        index = positions.get(name or "")
        if index is None or len(node.args) <= index:
            continue
        arg = node.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            codes.add(arg.value)
    assert {"link", "name-form", "cap", "cross-check"} <= codes, "the test no longer reads codes"
    assert codes <= set(cc.REASON_CODES)
