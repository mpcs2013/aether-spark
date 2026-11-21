"""CI's stack test under the host rules (tests/stack/layer3.sh) builds every image before the rules
cut the network off; an image missing from its build list fails only there, late in CI (PR 2a's
postgres-data, PR 4a's pipeline). This keeps the list complete on every `just ci`: each image the
stack test's Compose files build, profiles included, is built by at least one listed service."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The files layer3.sh and run.sh combine: the stack, its include, the test overlay
FILES = [
    ROOT / "deploy" / "compose" / "stack" / "compose.yaml",
    ROOT / "deploy" / "compose" / "egress" / "compose.yaml",
    ROOT / "tests" / "stack" / "compose.test.yaml",
]
LAYER3 = ROOT / "tests" / "stack" / "layer3.sh"

_TOP = re.compile(r"^([A-Za-z_][\w-]*):(?:\s*&([\w-]+))?\s*$")
_SERVICE = re.compile(r"^  ([a-z0-9][a-z0-9-]*):(?:\s*&([\w-]+))?\s*$")
_MERGE = re.compile(r"^(\s+)<<:\s*(.+)$")
_BUILD = re.compile(r"^(\s+)build:")
_IMAGE = re.compile(r"^(\s+)image:\s*(\S+)")


@dataclass
class Block:
    """A service or an anchored block: whether it builds, its image, the anchors it merges."""

    builds: bool = False
    image: str | None = None
    merges: list[str] = field(default_factory=list)


def _blocks(path: Path) -> tuple[dict[str, Block], dict[str, Block]]:
    services: dict[str, Block] = {}
    anchors: dict[str, Block] = {}
    section = ""
    current: Block | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if top := _TOP.match(line):
            section = top.group(1)
            current = Block() if top.group(2) else None
            if top.group(2):
                anchors[top.group(2)] = current  # type: ignore[assignment]
            continue
        if section == "services" and (svc := _SERVICE.match(line)):
            current = Block()
            services[svc.group(1)] = current
            if svc.group(2):
                anchors[svc.group(2)] = current
            continue
        if current is None:
            continue
        if merge := _MERGE.match(line):
            current.merges += re.findall(r"\*([\w-]+)", merge.group(2))
        elif _BUILD.match(line):
            current.builds = True
        elif image := _IMAGE.match(line):
            current.image = current.image or image.group(2)
    return services, anchors


def _resolve(block: Block, anchors: dict[str, Block]) -> tuple[bool, str | None]:
    builds, image = block.builds, block.image
    for name in block.merges:
        if name in anchors:
            b, i = _resolve(anchors[name], anchors)
            builds = builds or b
            image = image or i
    return builds, image


def built_images() -> dict[str, set[str]]:
    """Each image the files build, with the services that build it."""
    services: dict[str, Block] = {}
    anchors: dict[str, Block] = {}
    for path in FILES:
        s, a = _blocks(path)
        anchors.update(a)
        for name, block in s.items():
            # A later file adds to a service, as Compose merges them (the overlay's ingester)
            base = services.setdefault(name, Block())
            base.builds = base.builds or block.builds
            base.image = base.image or block.image
            base.merges += block.merges
    images: dict[str, set[str]] = {}
    for name, block in services.items():
        builds, image = _resolve(block, anchors)
        if builds:
            images.setdefault(image or name, set()).add(name)
    return images


def prebuilt() -> set[str]:
    text = LAYER3.read_text(encoding="utf-8")
    found = re.search(r"\bbuild ([a-z0-9 -]+?) \\\n", text)
    assert found, "layer3.sh: no `build <services> \\` line"
    return set(found.group(1).split())


def test_the_parser_sees_the_known_images() -> None:
    images = built_images()
    # A few known ones, built directly, through an anchor, by an include and in the overlay
    assert "aetherspark/postgres-data:local" in images
    assert "aetherspark/step-ca:local" in images
    assert "aetherspark/egress-proxy:local" in images
    assert "aetherspark/rest-server:local" in images
    assert "aetherspark/pipeline:local" in images


def test_layer3_prebuilds_every_image_the_stack_test_builds() -> None:
    listed = prebuilt()
    missing = {
        image: sorted(names) for image, names in built_images().items() if not names & listed
    }
    assert not missing, f"tests/stack/layer3.sh does not prebuild: {missing}"


def test_layer3_lists_only_services_that_build() -> None:
    builders = set().union(*built_images().values())
    assert prebuilt() <= builders, sorted(prebuilt() - builders)
