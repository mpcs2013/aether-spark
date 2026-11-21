"""The worker's settings: the stack's container paths by default, `PIPELINE_<NAME>` overrides."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from aetherspark_pipeline.settings import Settings


def test_the_defaults_are_the_stacks_paths() -> None:
    s = Settings.from_env({})

    assert s.database_host == "postgres-data.svc.aether.localhost"
    assert s.database_user == "pipeline"
    assert s.password_file == Path("/run/secrets/postgres-data-pipeline-password")
    assert s.root_certificate == Path("/ca-certs/root_ca.crt")
    assert s.raw_dir == Path("/raw")
    assert s.job_dir == Path("/jobs")
    # The ingester's own limit for a file (IngesterSettings.MaxFileBytes)
    assert s.max_file_bytes == 256 * 1024 * 1024


def test_the_place_names_the_database_host() -> None:
    assert Settings.from_env({"AETHERSPARK_PLACE": "home"}).database_host == (
        "postgres-data.svc.aether.home"
    )


def test_each_setting_can_be_overridden() -> None:
    s = Settings.from_env(
        {
            "PIPELINE_DATABASE_HOST": "localhost",
            "PIPELINE_DATABASE_PORT": "6543",
            "PIPELINE_RAW_DIR": "/tmp/raw",  # noqa: S108 - a test value
            "PIPELINE_POLL_SECONDS": "0.5",
            "PIPELINE_STUCK_AFTER": "120",
            "PIPELINE_CHILD_MEMORY_BYTES": "1000000",
        }
    )

    assert s.database_host == "localhost"
    assert s.database_port == 6543
    assert s.raw_dir == Path("/tmp/raw")  # noqa: S108 - a test value
    assert s.poll_seconds == 0.5
    assert s.stuck_after == timedelta(seconds=120)
    assert s.child_memory_bytes == 1_000_000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PIPELINE_DATABASE_PORT", "zero"),
        ("PIPELINE_DATABASE_PORT", "0"),
        ("PIPELINE_CHILD_CPU_SECONDS", "-5"),
        ("PIPELINE_POLL_SECONDS", "0"),
        ("PIPELINE_STUCK_AFTER", "soon"),
    ],
)
def test_a_bad_value_names_its_setting(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: value})
