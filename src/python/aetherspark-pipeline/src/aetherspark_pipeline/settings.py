"""Where the worker finds its database, secrets, folders and limits. The defaults are the stack's
container paths (deploy/compose/stack/compose.yaml); each can be set as `PIPELINE_<NAME>`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import timedelta
from pathlib import Path

MIB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    """The worker's settings; nothing here is secret (the password is read from its file)."""

    database_host: str
    database_port: int = 5432
    database_name: str = "research"
    database_user: str = "pipeline"
    password_file: Path = Path("/run/secrets/postgres-data-pipeline-password")
    # The stack CA's root, the only trust anchor for the database's certificate
    root_certificate: Path = Path("/ca-certs/root_ca.crt")
    # The raw SEC downloads, read-only (data-plane.md, decision 9)
    raw_dir: Path = Path("/raw")
    # The job folders, in memory (a tmpfs of the container)
    job_dir: Path = Path("/jobs")
    # The XBRL taxonomies, read-only, Arelle's offline cache (taxonomies.py)
    taxonomy_dir: Path = Path("/taxonomies")
    # Touched after each successful database round trip; the health check reads its age
    heartbeat_file: Path = Path("/tmp/heartbeat")  # noqa: S108 - the container's own tmpfs
    poll_seconds: float = 5.0
    # A job left in `running` this long goes back to the queue once, then fails
    stuck_after: timedelta = timedelta(hours=1)
    # The same limit as the ingester's (IngesterSettings.MaxFileBytes)
    max_file_bytes: int = 256 * MIB
    max_job_bytes: int = 1024 * MIB
    # The child process that parses one job
    child_memory_bytes: int = 2048 * MIB
    child_cpu_seconds: int = 600
    child_wall_seconds: int = 900

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """The stack's defaults, with each `PIPELINE_<NAME>` taking precedence."""
        place = env.get("AETHERSPARK_PLACE") or "localhost"
        settings = cls(database_host=f"postgres-data.svc.aether.{place}")
        changes: dict[str, object] = {}
        for field in fields(cls):
            raw = env.get(f"PIPELINE_{field.name.upper()}")
            if raw is None or raw == "":
                continue
            changes[field.name] = _parse(field.name, raw, getattr(settings, field.name))
        return replace(settings, **changes)  # type: ignore[arg-type]


def _parse(name: str, raw: str, default: object) -> object:
    try:
        if isinstance(default, bool):
            raise ValueError("no boolean settings")
        if isinstance(default, int):
            value: object = int(raw)
            if value <= 0:  # type: ignore[operator]
                raise ValueError("must be positive")
            return value
        if isinstance(default, float):
            number = float(raw)
            if number <= 0:
                raise ValueError("must be positive")
            return number
        if isinstance(default, timedelta):
            seconds = int(raw)
            if seconds <= 0:
                raise ValueError("must be positive")
            return timedelta(seconds=seconds)
        if isinstance(default, Path):
            return Path(raw)
        return raw
    except ValueError as error:
        raise ValueError(f"PIPELINE_{name.upper()}: {error}") from None
