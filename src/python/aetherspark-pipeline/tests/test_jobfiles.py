"""D5, the pipeline's side: paths built only from the contract's checked parts, no link followed at
any level, only regular files read, sizes limited, every file hashed again against the contract."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

if sys.platform == "win32":
    pytest.skip("the job files need Linux (O_NOFOLLOW, dir_fd)", allow_module_level=True)

from aetherspark_pipeline.jobfiles import JobRefused, copy_files, read_contract
from aetherspark_pipeline.settings import Settings

CIK = 1234567
ACC = "0001234567-25-000001"
FILES = {"a.htm": b"<html/>", "a_htm.xml": b"<xbrl/>", "a.xsd": b"<schema/>"}
KINDS = {"a.htm": "primary", "a_htm.xml": "instance", "a.xsd": "schema"}


def contract(files: dict[str, bytes] = FILES, **change: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": 1,
        "accession_no": ACC,
        "cik": CIK,
        "form": "10-K",
        "known_at": "2025-02-20T21:30:00Z",
        "filed_date": "2025-02-20",
        "files": [
            {
                "name": n,
                "kind": KINDS.get(n, "linkbase"),
                "sha256": hashlib.sha256(b).hexdigest(),
                "size": len(b),
            }
            for n, b in files.items()
        ],
    }
    doc.update(change)
    return doc


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    folder = tmp_path / "raw" / str(CIK) / ACC
    folder.mkdir(parents=True)
    for name, body in FILES.items():
        (folder / name).write_bytes(body)
    return tmp_path / "raw"


def settings(raw: Path, **change: Any) -> Settings:
    return Settings(database_host="unused.invalid", raw_dir=raw, **change)


def copy(raw: Path, tmp_path: Path, doc: dict[str, Any], **change: Any) -> list[str]:
    target = tmp_path / "job"
    target.mkdir(exist_ok=True)
    copied = copy_files(settings(raw, **change), read_contract(ACC, doc), target)
    return [c.name for c in copied]


def test_the_files_are_copied_and_hashed(raw: Path, tmp_path: Path) -> None:
    assert copy(raw, tmp_path, contract()) == list(FILES)
    for name, body in FILES.items():
        assert (tmp_path / "job" / name).read_bytes() == body


def test_a_changed_file_refuses_the_job(raw: Path, tmp_path: Path) -> None:
    (raw / str(CIK) / ACC / "a_htm.xml").write_bytes(b"<xbrL/>")  # same size, other bytes

    with pytest.raises(JobRefused, match=r"a_htm\.xml: its SHA-256 differs"):
        copy(raw, tmp_path, contract())


def test_a_missing_file_refuses_the_job(raw: Path, tmp_path: Path) -> None:
    (raw / str(CIK) / ACC / "a.xsd").unlink()

    with pytest.raises(JobRefused, match=r"a\.xsd: not in the raw folder"):
        copy(raw, tmp_path, contract())


def test_a_linked_file_is_never_followed(raw: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"<xbrl/>")  # even with the right bytes
    target = raw / str(CIK) / ACC / "a_htm.xml"
    target.unlink()
    target.symlink_to(secret)

    with pytest.raises(JobRefused, match=r"a_htm\.xml: cannot be opened"):
        copy(raw, tmp_path, contract())


@pytest.mark.parametrize("level", ["company", "filing"])
def test_a_linked_folder_is_never_followed(raw: Path, tmp_path: Path, level: str) -> None:
    elsewhere = tmp_path / "elsewhere"
    (raw / str(CIK)).rename(elsewhere)
    if level == "company":
        (raw / str(CIK)).symlink_to(elsewhere)
    else:
        (raw / str(CIK)).mkdir()
        (raw / str(CIK) / ACC).symlink_to(elsewhere / ACC)

    with pytest.raises(JobRefused, match="not a plain folder"):
        copy(raw, tmp_path, contract())


def test_a_fifo_is_not_read(raw: Path, tmp_path: Path) -> None:
    path = raw / str(CIK) / ACC / "a.xsd"
    path.unlink()
    os.mkfifo(path)

    with pytest.raises(JobRefused, match=r"a\.xsd: not a regular file"):
        copy(raw, tmp_path, contract())


def test_a_file_over_the_limit_is_refused(raw: Path, tmp_path: Path) -> None:
    with pytest.raises(JobRefused, match="larger than 8 bytes"):
        copy(raw, tmp_path, contract(), max_file_bytes=8)


def test_files_over_the_jobs_limit_are_refused(raw: Path, tmp_path: Path) -> None:
    with pytest.raises(JobRefused, match="exceed 10 bytes"):
        copy(raw, tmp_path, contract(), max_job_bytes=10)


@pytest.mark.parametrize(
    "name",
    ["../escape.xml", "../../run/secrets/x", ".hidden.xml", "a/b.xml", "a\\b.xml", "a.xml\n"],
)
def test_a_name_that_is_not_a_plain_file_name_refuses_the_contract(name: str) -> None:
    with pytest.raises(JobRefused, match="the contract does not hold"):
        read_contract(ACC, contract({name: b"x"}))


def test_a_contract_for_another_filing_is_refused() -> None:
    with pytest.raises(JobRefused, match="names filing 0001234567-25-000002"):
        read_contract(ACC, contract(accession_no="0001234567-25-000002"))


def test_a_contract_naming_a_file_twice_is_refused() -> None:
    doc = contract()
    doc["files"].append(doc["files"][0])

    with pytest.raises(JobRefused, match="names a file twice"):
        read_contract(ACC, doc)


def test_an_unknown_field_or_version_refuses_the_contract() -> None:
    with pytest.raises(JobRefused, match="the contract does not hold"):
        read_contract(ACC, contract(path="/run/secrets"))
    with pytest.raises(JobRefused, match="the contract does not hold"):
        read_contract(ACC, contract(version=2))
