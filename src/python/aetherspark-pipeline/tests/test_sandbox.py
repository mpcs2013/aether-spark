"""D6, the worker's side: a child past its memory, CPU or time limit, or one that tries to write a
file, ends with a reason; the worker goes on. And one whole job, files to parse, in a temp
folder."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("the sandbox needs Linux (rlimits)", allow_module_level=True)

from aetherspark_pipeline.jobs import Job
from aetherspark_pipeline.sandbox import Limits, run
from aetherspark_pipeline.settings import Settings
from aetherspark_pipeline.taxonomies import store_path
from aetherspark_pipeline.worker import process
from test_xbrl import COMPANY, INSTANCE, TAX_SCHEMA, TAXONOMY

MIB = 1024 * 1024
LIMITS = Limits(memory_bytes=512 * MIB, cpu_seconds=10, wall_seconds=20)


def python(code: str, limits: Limits = LIMITS) -> tuple[bool, str]:
    outcome = run([sys.executable, "-I", "-c", code], limits)
    return outcome.ok, outcome.reason


def test_a_report_is_passed_on() -> None:
    assert python('print(\'{"ok": true, "reason": "fine"}\')') == (True, "fine")


def test_the_memory_limit_stops_the_child() -> None:
    code = (
        "import sys\n"
        "try:\n    b = bytearray(1024 * 1024 * 1024)\n"
        "except MemoryError:\n    sys.exit(3)\n"
    )
    ok, reason = python(code, Limits(256 * MIB, 10, 20))
    assert not ok
    assert "memory limit" in reason


def test_the_cpu_limit_stops_the_child() -> None:
    ok, reason = python("while True: pass", Limits(512 * MIB, 1, 30))
    assert not ok
    assert "CPU limit" in reason


def test_the_time_limit_stops_the_child() -> None:
    ok, reason = python("import time; time.sleep(60)", Limits(512 * MIB, 10, 1))
    assert not ok
    assert "time limit (1 s)" in reason


def test_the_child_cannot_write_a_file(tmp_path: Path) -> None:
    # Python ignores SIGXFSZ, so the file-size limit of 0 shows as a refused write (EFBIG)
    target = tmp_path / "written"
    code = (
        "import json, sys\n"
        "try:\n"
        f"    with open({str(target)!r}, 'w') as f:\n"
        "        f.write('x' * 10)\n"
        "except OSError as e:\n"
        "    print(json.dumps({'ok': False, 'reason': f'write refused: {e.errno}'})); sys.exit(2)\n"
        "print(json.dumps({'ok': True, 'reason': 'written'}))\n"
    )
    ok, reason = python(code)
    assert not ok
    assert reason == "write refused: 27"  # EFBIG
    assert not target.exists() or target.stat().st_size == 0


def test_a_child_without_a_report_fails_the_job() -> None:
    ok, reason = python("raise SystemExit(7)")
    assert not ok
    assert "without a report (exit 7)" in reason


def job_in(raw: Path, files: dict[str, bytes], job_id: int = 7) -> Job:
    acc = "0001234567-25-000001"
    folder = raw / "1234567" / acc
    folder.mkdir(parents=True)
    for name, body in files.items():
        (folder / name).write_bytes(body)

    def kind(name: str) -> str:
        if name.endswith("_htm.xml"):
            return "instance"
        return "schema" if name.endswith(".xsd") else "primary"

    contract = {
        "version": 1,
        "accession_no": acc,
        "cik": 1234567,
        "form": "10-Q",
        "known_at": "2025-07-31T22:03:34Z",
        "filed_date": "2025-08-01",
        "files": [
            {
                "name": n,
                "kind": kind(n),
                "sha256": hashlib.sha256(b).hexdigest(),
                "size": len(b),
            }
            for n, b in files.items()
        ],
    }
    return Job(id=job_id, accession_no=acc, contract=contract, attempts=1)


def worker_settings(tmp_path: Path) -> Settings:
    (tmp_path / "jobs").mkdir()
    store = tmp_path / "store"
    taxonomy = store_path(store, TAXONOMY)
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text(TAX_SCHEMA, encoding="utf-8")
    return Settings(
        database_host="unused.invalid",
        raw_dir=tmp_path / "raw",
        job_dir=tmp_path / "jobs",
        taxonomy_dir=store,
        child_memory_bytes=1024 * MIB,
        child_cpu_seconds=60,
        child_wall_seconds=120,
    )


def test_a_whole_job_is_parsed_in_the_sandbox_and_its_folders_removed(tmp_path: Path) -> None:
    settings = worker_settings(tmp_path)
    job = job_in(
        tmp_path / "raw",
        {
            "q.htm": b"<html/>",
            "co-20241231.xsd": COMPANY.encode(),
            "co-20241231_htm.xml": INSTANCE.encode(),
        },
    )

    outcome = process(settings, job)

    assert outcome.ok, outcome.reason
    assert "3 files" in outcome.reason
    assert "3 numeric facts" in outcome.reason
    assert "1 more than six decimal places" in outcome.reason
    assert len(outcome.rows) == 3
    assert list((tmp_path / "jobs").iterdir()) == []


def test_a_filing_without_an_instance_has_no_facts(tmp_path: Path) -> None:
    settings = worker_settings(tmp_path)
    job = job_in(tmp_path / "raw", {"q.htm": b"<html/>"})

    outcome = process(settings, job)

    assert outcome.ok
    assert outcome.reason.endswith("no XBRL instance, so no facts")


def test_a_missing_taxonomy_fails_the_job_with_its_url(tmp_path: Path) -> None:
    settings = worker_settings(tmp_path)
    store_path(settings.taxonomy_dir, TAXONOMY).unlink()
    job = job_in(
        tmp_path / "raw",
        {"co-20241231.xsd": COMPANY.encode(), "co-20241231_htm.xml": INSTANCE.encode()},
    )

    outcome = process(settings, job)

    assert not outcome.ok
    assert TAXONOMY in outcome.reason


def test_a_hostile_file_fails_its_job_and_the_folder_is_removed(tmp_path: Path) -> None:
    settings = worker_settings(tmp_path)
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "a">]><xbrl>&a;</xbrl>'
    job = job_in(tmp_path / "raw", {"q_htm.xml": bomb})

    outcome = process(settings, job)

    assert not outcome.ok
    assert "declares a DTD" in outcome.reason
    assert list((tmp_path / "jobs").iterdir()) == []
