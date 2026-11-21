"""The pipeline image installs its packages from a hashed requirements file: uv.lock's export minus
what an offline Arelle load never imports (tools/pipeline_requirements.py). The file must be that
generator's current output, so the image never carries a version the lock does not, nor a package
left out on purpose."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "pipeline_requirements.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pipeline_requirements", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["pipeline_requirements"] = module
    spec.loader.exec_module(module)
    return module


tool = _tool()


def test_the_pipeline_requirements_are_the_generators_output() -> None:
    assert tool.main(["--check"]) == 0, (
        "src/python/aetherspark-pipeline/requirements.txt is stale: "
        "run uv run --all-packages python -I tools/pipeline_requirements.py"
    )


def test_every_requirement_is_pinned_with_a_hash_and_none_left_out_is_kept() -> None:
    text = tool.REQUIREMENTS.read_text(encoding="utf-8")
    pins = [line for line in text.splitlines() if line and not line.startswith((" ", "#"))]
    assert pins
    for pin in pins:
        assert "==" in pin, pin
        assert pin.split("==")[0] not in tool.NOT_IN_IMAGE, pin
    assert "--hash=sha256:" in text
    for wanted in ("arelle-release==", "lxml==", "pillow==", "openpyxl==", "psycopg=="):
        assert any(p.startswith(wanted) for p in pins), wanted


def test_a_left_out_package_is_dropped_with_its_hashes() -> None:
    export = (
        "keep==1.0 \\\n    --hash=sha256:aa\n    # via x\n"
        "numpy==2.5.4 \\\n    --hash=sha256:bb\n    --hash=sha256:cc\n"
        "regex==1 \\\n    --hash=sha256:dd\n"
    )
    filtered = tool.image_requirements(
        export
        + "".join(f"{n}==0 \\\n    --hash=sha256:ee\n" for n in tool.NOT_IN_IMAGE if n != "numpy")
    )
    assert "keep==1.0" in filtered
    assert "regex==1" in filtered
    assert "numpy" not in filtered
    assert "sha256:bb" not in filtered
