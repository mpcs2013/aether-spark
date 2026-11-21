"""The pipeline image installs its packages from a hashed requirements file exported from uv.lock
(src/python/aetherspark-pipeline/Dockerfile): the file must be the lock's current export, so the
image never carries a version the lock does not."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "src" / "python" / "aetherspark-pipeline" / "requirements.txt"
EXPORT = [
    "export",
    "--package",
    "aetherspark-pipeline",
    "--no-dev",
    "--frozen",
    "--no-emit-workspace",
    "--no-header",
]


def test_the_pipeline_requirements_are_the_locks_export() -> None:
    uv = shutil.which("uv")
    assert uv, "uv is needed to compare the export (CI and the dev container have it)"
    exported = subprocess.run(  # noqa: S603 - uv with fixed arguments
        [uv, *EXPORT], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout

    committed = REQUIREMENTS.read_text(encoding="utf-8")

    assert exported.replace("\r\n", "\n") == committed.replace("\r\n", "\n"), (
        "src/python/aetherspark-pipeline/requirements.txt is stale: run "
        "uv export "
        + " ".join(EXPORT[1:])
        + " --output-file "
        + str(REQUIREMENTS.relative_to(ROOT))
    )


def test_every_requirement_is_pinned_with_a_hash() -> None:
    blocks = REQUIREMENTS.read_text(encoding="utf-8").split("\n")
    pins = [line for line in blocks if line and not line.startswith((" ", "#"))]
    assert pins
    for pin in pins:
        assert "==" in pin, pin
    assert "--hash=sha256:" in REQUIREMENTS.read_text(encoding="utf-8")
