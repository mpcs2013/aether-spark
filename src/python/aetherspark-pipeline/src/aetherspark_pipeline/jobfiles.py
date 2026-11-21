"""A job's files (data-plane.md, section 4): the contract checked, every path built here from its
checked parts, each file copied from the raw folder into the job's in-memory folder and hashed again
on the way. The raw folder is writable from Windows on the desktop, so nothing in it is trusted: no
link is followed at any level, only regular files are read, and every size is limited."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    raise ImportError("the job files need Linux (O_NOFOLLOW, dir_fd); their tests skip elsewhere")

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from aetherspark_common.contracts.filing_job import FilingJob
from pydantic import ValidationError

from .settings import Settings

# The contract's own patterns, checked again here before any of them becomes part of a path
_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}")
_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_CHUNK = 1024 * 1024


class JobRefused(Exception):
    """The job cannot be processed as it stands; the message is its reason (no file content)."""


@dataclass(frozen=True)
class CopiedFile:
    name: str
    kind: str
    size: int


def read_contract(accession_no: str, contract: object) -> FilingJob:
    """The job's contract, strictly; it must name the job row's own filing."""
    try:
        job = FilingJob.model_validate(contract)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'contract'}: {e['msg']}"
            for e in error.errors()
        )
        raise JobRefused(f"the contract does not hold: {problems}") from None
    if job.accession_no != accession_no:
        raise JobRefused(
            f"the contract names filing {job.accession_no}, the job row {accession_no}"
        )
    names = [f.name for f in job.files]
    if len(set(names)) != len(names):
        raise JobRefused("the contract names a file twice")
    for name in names:
        if not _NAME.fullmatch(name):
            raise JobRefused(f"not a plain file name: {name!r}")
    if not _ACCESSION.fullmatch(job.accession_no):
        raise JobRefused(f"not an accession number: {job.accession_no!r}")
    return job


def _open_dir(name: str, parent: int) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        raise JobRefused(f"the raw folder has no {name}/") from None
    except (NotADirectoryError, OSError) as error:
        # O_NOFOLLOW on a link: ELOOP; anything else that is not a directory: ENOTDIR
        raise JobRefused(
            f"{name} in the raw folder is not a plain folder ({error.strerror})"
        ) from None


def copy_files(settings: Settings, job: FilingJob, destination: Path) -> list[CopiedFile]:
    """Copies the contract's files from `<raw>/<cik>/<accession_no>/<name>` into `destination`
    (an empty folder), hashing each; any difference from the contract refuses the job."""
    raw = os.open(settings.raw_dir, os.O_RDONLY | os.O_DIRECTORY)
    folders: list[int] = [raw]
    try:
        company = _open_dir(str(job.cik), raw)
        folders.append(company)
        filing = _open_dir(job.accession_no, company)
        folders.append(filing)
        total = 0
        copied: list[CopiedFile] = []
        for file in job.files:
            size = _copy_one(settings, filing, file.name, destination / file.name, file.sha256)
            if size != file.size:
                raise JobRefused(f"{file.name}: {size} bytes, the contract says {file.size}")
            total += size
            if total > settings.max_job_bytes:
                raise JobRefused(f"the job's files exceed {settings.max_job_bytes} bytes")
            copied.append(CopiedFile(file.name, file.kind, size))
        return copied
    finally:
        for fd in reversed(folders):
            os.close(fd)


def _copy_one(settings: Settings, folder: int, name: str, target: Path, sha256: str) -> int:
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except FileNotFoundError:
        raise JobRefused(f"{name}: not in the raw folder") from None
    except OSError as error:
        raise JobRefused(f"{name}: cannot be opened ({error.strerror}); a link?") from None
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode):
            raise JobRefused(f"{name}: not a regular file")
        if info.st_size > settings.max_file_bytes:
            raise JobRefused(f"{name}: larger than {settings.max_file_bytes} bytes")
        digest = hashlib.sha256()
        size = 0
        out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            while chunk := os.read(source, _CHUNK):
                size += len(chunk)
                if size > settings.max_file_bytes:
                    raise JobRefused(f"{name}: larger than {settings.max_file_bytes} bytes")
                digest.update(chunk)
                os.write(out, chunk)
        finally:
            os.close(out)
    finally:
        os.close(source)
    if digest.hexdigest() != sha256:
        raise JobRefused(f"{name}: its SHA-256 differs from the contract's")
    return size
