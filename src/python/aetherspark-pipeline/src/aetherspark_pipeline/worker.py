"""The worker's loop (data-plane.md, section 4): one job at a time, from claim to `done` or
`failed`; stuck jobs recovered; a stop (SIGTERM) gives the job in hand back to the queue."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import jobs
from .jobfiles import JobRefused, copy_files, read_contract
from .sandbox import Limits, Outcome, parse_job
from .settings import Settings

if TYPE_CHECKING:
    import psycopg

log = logging.getLogger("pipeline")


class Stopping(Exception):
    """Raised from the SIGTERM handler: finish nothing, give the job back."""


def touch(path: Path) -> None:
    path.touch(exist_ok=True)


def process(settings: Settings, job: jobs.Job) -> Outcome:
    """One job: its contract, its files copied and hashed into an in-memory folder, then the parse
    in a child process. The folder is removed whatever happens."""
    folder = settings.job_dir / str(job.id)
    try:
        contract = read_contract(job.accession_no, job.contract)
        folder.mkdir(mode=0o700)
        copied = copy_files(settings, contract, folder)
        limits = Limits(
            settings.child_memory_bytes, settings.child_cpu_seconds, settings.child_wall_seconds
        )
        parsed = parse_job(folder, limits)
        if not parsed.ok:
            return parsed
        return Outcome(
            True,
            f"checked: {len(copied)} files, hashes as in the contract; {parsed.reason} "
            "(PR 4a: no facts yet)",
        )
    except JobRefused as refused:
        return Outcome(False, str(refused))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def run(settings: Settings, conn: psycopg.Connection[Any]) -> None:
    """Works until stopped: recover stuck jobs, claim one, process it, record the outcome."""
    settings.job_dir.mkdir(mode=0o700, exist_ok=True)
    while True:
        requeued, failed = jobs.recover_stuck(conn, settings.stuck_after)
        if requeued or failed:
            log.warning("stuck jobs: %d queued again, %d failed", requeued, failed)
        job = jobs.claim(conn)
        touch(settings.heartbeat_file)
        if job is None:
            time.sleep(settings.poll_seconds)
            continue
        try:
            outcome = process(settings, job)
        except Stopping:
            jobs.release(conn, job, "the worker was stopped during the job")
            log.info("job %d (%s) given back: the worker is stopping", job.id, job.accession_no)
            raise
        jobs.finish(conn, job, ok=outcome.ok, reason=outcome.reason)
        touch(settings.heartbeat_file)
        level = logging.INFO if outcome.ok else logging.WARNING
        log.log(
            level,
            "job %d (%s) %s: %s",
            job.id,
            job.accession_no,
            "done" if outcome.ok else "failed",
            outcome.reason,
        )
