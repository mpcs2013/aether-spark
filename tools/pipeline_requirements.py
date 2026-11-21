"""The pipeline image's requirements (src/python/aetherspark-pipeline/requirements.txt): uv.lock's
export for the pipeline, with hashes, minus the packages an offline Arelle load never imports (PR
4b1, the owner's choice of 2026-11-21: measured by loading a filing with only the remaining ones;
pillow and openpyxl stay, Arelle imports them unconditionally). The image installs with --no-deps,
so the left-out packages stay out. tests/python/test_pipeline_requirements.py keeps the file
current.

    uv run --all-packages python -I tools/pipeline_requirements.py          write the file
    uv run --all-packages python -I tools/pipeline_requirements.py --check  exit 1 when stale
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
# Arelle's declared dependencies that an offline load of an instance never imports
NOT_IN_IMAGE = {
    "attrs": "jsonschema's",
    "bottle": "Arelle's web server",
    "certifi": "Arelle's online access",
    "et-xmlfile": "openpyxl's writer; Arelle only imports openpyxl's cell module",
    "filelock": "Arelle's cache locking for online use",
    "jaconv": "Japanese text conversion in a validation plugin",
    "jsonschema": "validation plugins",
    "jsonschema-specifications": "jsonschema's",
    "numpy": "Arelle's formula and table processing",
    "python-dateutil": "Arelle's online and GUI parts",
    "referencing": "jsonschema's",
    "rpds-py": "jsonschema's",
    "six": "python-dateutil's",
    "truststore": "Arelle's online access",
}
_PIN = re.compile(r"^([a-z0-9][a-z0-9._-]*)==")
HEADER = (
    "# The pipeline image's packages: uv.lock's export minus what an offline Arelle load never\n"
    "# imports (tools/pipeline_requirements.py). Generated; do not edit.\n"
)


def image_requirements(export: str) -> str:
    """The export without the blocks of the packages in NOT_IN_IMAGE."""
    blocks: list[list[str]] = []
    for line in export.replace("\r\n", "\n").splitlines():
        if _PIN.match(line) or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    kept = [b for b in blocks if not ((m := _PIN.match(b[0])) and m.group(1) in NOT_IN_IMAGE)]
    names = {m.group(1) for b in blocks if (m := _PIN.match(b[0]))}
    unknown = sorted(set(NOT_IN_IMAGE) - names)
    if unknown:
        raise SystemExit(f"NOT_IN_IMAGE names packages the export no longer has: {unknown}")
    return HEADER + "\n".join(line for block in kept for line in block) + "\n"


def exported() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is needed (CI and the dev container have it)")
    return subprocess.run(  # noqa: S603 - uv with fixed arguments
        [uv, *EXPORT], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def main(argv: list[str]) -> int:
    wanted = image_requirements(exported())
    if argv == ["--check"]:
        current = REQUIREMENTS.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current != wanted:
            print(f"{REQUIREMENTS.relative_to(ROOT)} is stale: run tools/pipeline_requirements.py")
            return 1
        return 0
    REQUIREMENTS.write_bytes(wanted.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
