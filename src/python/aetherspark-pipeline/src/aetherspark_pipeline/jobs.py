"""The job queue in `ingest_ops.job_runs` (data-plane.md, section 4). The pipeline's login may read
the jobs and update only their status columns (migration 0004), so every change here is one of
those: status, reason, attempts, started_at, finished_at."""

from __future__ import annotations

import ctypes.util
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from .settings import Settings

if TYPE_CHECKING:
    # Loaded only to connect (connect()): psycopg without its binary part needs the system's libpq
    # (Alpine's in the image), which the tests and type checks do not
    import psycopg

# Reasons are for people; keep them to one line of reasonable length
MAX_REASON = 1000

# Alpine's libpq (the image's `libpq` package)
ALPINE_LIBPQ = Path("/usr/lib/libpq.so.5")


def load_psycopg() -> ModuleType:
    """psycopg, with its libpq found on Alpine too. psycopg 3.3 looks the library up only with
    ctypes.util.find_library("pq"), which on musl needs ldconfig, gcc or ld, none of which the
    image has; so for that one lookup, while psycopg loads, the answer is Alpine's file."""
    if "psycopg" in sys.modules or sys.platform != "linux" or not ALPINE_LIBPQ.exists():
        import psycopg

        return psycopg
    original = ctypes.util.find_library

    def find_library(name: str) -> str | None:
        found = original(name)
        return str(ALPINE_LIBPQ) if found is None and name == "pq" else found

    ctypes.util.find_library = find_library
    try:
        import psycopg
    finally:
        ctypes.util.find_library = original
    return psycopg


@dataclass(frozen=True)
class Job:
    """One claimed job: its row id, accession number, contract (as stored) and attempt count."""

    id: int
    accession_no: str
    contract: Any
    attempts: int


def read_secret(path: Path) -> str:
    """A secret file's value without its line end; errors name the file, never the value."""
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as error:
        raise RuntimeError(f"secret file {path} cannot be read") from error
    if not value:
        raise RuntimeError(f"secret file {path} is empty")
    return value


def connect(settings: Settings) -> psycopg.Connection[Any]:
    """The data Postgres over TLS, the certificate checked by name and against the stack's root
    only; the password from its file, never from the environment or a connection string."""
    driver = load_psycopg()
    connection: psycopg.Connection[Any] = driver.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        password=read_secret(settings.password_file),
        sslmode="verify-full",
        sslrootcert=str(settings.root_certificate),
        application_name="pipeline",
        connect_timeout=10,
    )
    return connection


def _reason(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= MAX_REASON else line[: MAX_REASON - 1] + "…"


def recover_stuck(conn: psycopg.Connection[Any], stuck_after: timedelta) -> tuple[int, int]:
    """A job left in `running` past `stuck_after` (a worker that died) goes back to the queue once;
    the second time it fails. Returns (requeued, failed)."""
    with conn.transaction():
        rows = conn.execute(
            """
            UPDATE ingest_ops.job_runs
               SET status      = CASE WHEN attempts >= 2 THEN 'failed' ELSE 'queued' END,
                   reason      = CASE WHEN attempts >= 2
                                      THEN 'stuck in running twice (a worker died with it)'
                                      ELSE 'stuck in running (a worker died with it); queued again'
                                 END,
                   started_at  = CASE WHEN attempts >= 2 THEN started_at END,
                   finished_at = CASE WHEN attempts >= 2 THEN now() END
             WHERE status = 'running' AND started_at < now() - %s
            RETURNING status
            """,
            (stuck_after,),
        ).fetchall()
    failed = sum(1 for (status,) in rows if status == "failed")
    return len(rows) - failed, failed


def claim(conn: psycopg.Connection[Any]) -> Job | None:
    """The oldest queued job, marked `running` with one more attempt; None when the queue is empty.
    SKIP LOCKED lets a second worker take the next one instead of waiting."""
    with conn.transaction():
        row = conn.execute(
            """
            WITH next AS (
                SELECT id FROM ingest_ops.job_runs
                 WHERE status = 'queued'
                 ORDER BY id
                 LIMIT 1
                   FOR UPDATE SKIP LOCKED
            )
            UPDATE ingest_ops.job_runs AS j
               SET status = 'running', attempts = j.attempts + 1,
                   started_at = now(), finished_at = NULL, reason = NULL
              FROM next
             WHERE j.id = next.id
            RETURNING j.id, j.accession_no, j.contract, j.attempts
            """
        ).fetchone()
    if row is None:
        return None
    return Job(id=row[0], accession_no=row[1], contract=row[2], attempts=row[3])


def finish(conn: psycopg.Connection[Any], job: Job, *, ok: bool, reason: str) -> None:
    """The job's end: `done` or `failed`, with a reason either way."""
    with conn.transaction():
        conn.execute(
            """
            UPDATE ingest_ops.job_runs
               SET status = %s, reason = %s, finished_at = now()
             WHERE id = %s AND status = 'running'
            """,
            ("done" if ok else "failed", _reason(reason), job.id),
        )


def release(conn: psycopg.Connection[Any], job: Job, reason: str) -> None:
    """A job the worker gave up because it was stopped: back in the queue, the attempt not
    counted."""
    with conn.transaction():
        conn.execute(
            """
            UPDATE ingest_ops.job_runs
               SET status = 'queued', reason = %s, started_at = NULL,
                   attempts = GREATEST(attempts - 1, 0)
             WHERE id = %s AND status = 'running'
            """,
            (_reason(reason), job.id),
        )
