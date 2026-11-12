"""The local folder feed of pinned Ostium.* packages (ADR-0009 amendment 1; WP3.5;
docs/runbooks/ostium-packages.md, "The local folder feed"). Standard library only.

    python -I deploy/packages/local_feed.py sync   --feed DIR [--packages DIR]
    python -I deploy/packages/local_feed.py check  --feed DIR
    python -I deploy/packages/local_feed.py prove  --feed DIR
    python -I deploy/packages/local_feed.py remove --feed DIR ID VERSION
    python -I deploy/packages/local_feed.py prove-ci

sync    copies every Ostium.* package that a committed lock file names from NuGet's package folder
        into the feed (flat: <id>.<version>.nupkg, lower case), after checking its SHA-512 against
        the lock file. A package already in the feed must have the same hash. Nothing is removed.
check   every locked Ostium.* package is in the feed, with the lock file's hash.
prove   a locked restore of each project that uses an Ostium.* package, into an empty package
        folder, with a NuGet configuration in which Ostium.* comes only from the feed and every
        other package from nuget.org: the private feed and its credentials play no part. Then the
        project is restored again as usual, so its build files point at NuGet's own folder.
remove  takes a withdrawn version out of the feed (Ostium announces it; ADR-0009 amendment 1).
prove-ci  CI: a feed made by sync in a temporary folder, then prove with it.

Exit codes: 0 done, 1 a check failed, 2 unusable input.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "ostium."  # compared in lower case, as NuGet compares ids
SKIP = {".git", ".venv", "bin", "obj", "node_modules", "TestResults"}
NUGET_ORG = "https://api.nuget.org/v3/index.json"


class InputError(Exception):
    """An unusable lock file, feed or package folder; nothing was changed."""


class CheckFailed(Exception):
    """A package is missing or differs from its lock file."""


@dataclass(frozen=True)
class Locked:
    package: str  # the id as the lock file writes it
    version: str
    content_hash: str  # base64 SHA-512 of the .nupkg, as in the lock file
    lock_file: Path

    @property
    def file_name(self) -> str:
        return f"{self.package.lower()}.{self.version.lower()}.nupkg"


def lock_files(root: Path = ROOT) -> list[Path]:
    found = []
    for path in root.rglob("packages.lock.json"):
        if not SKIP.intersection(path.relative_to(root).parts):
            found.append(path)
    return sorted(found)


def _entries(root: Path) -> list[Locked]:
    """Every Ostium.* entry of every lock file (a package two projects use appears twice)."""
    found = []
    for path in lock_files(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise InputError(f"{path.relative_to(root)}: {error}") from error
        for framework in (data.get("dependencies") or {}).values():
            for package, entry in (framework or {}).items():
                if not package.lower().startswith(PREFIX):
                    continue
                version, digest = entry.get("resolved"), entry.get("contentHash")
                if not version or not digest:
                    raise InputError(f"{path.relative_to(root)}: {package} has no version or hash")
                found.append(Locked(package, version, digest, path))
    return found


def locked_ostium(root: Path = ROOT) -> list[Locked]:
    """Every Ostium.* package any lock file names; one entry per id and version."""
    result: dict[tuple[str, str], Locked] = {}
    for item in _entries(root):
        key = (item.package.lower(), item.version.lower())
        known = result.get(key)
        if known is not None and known.content_hash != item.content_hash:
            raise InputError(f"{item.package} {item.version}: two lock files disagree on its hash")
        result[key] = item
    return [result[k] for k in sorted(result)]


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode("ascii")


def nuget_packages() -> Path:
    explicit = os.environ.get("NUGET_PACKAGES")
    return Path(explicit) if explicit else Path.home() / ".nuget" / "packages"


def sync(feed: Path, packages: Path, root: Path = ROOT) -> list[str]:
    """Copies what the feed lacks; returns the file names copied."""
    locked = locked_ostium(root)
    feed.mkdir(parents=True, exist_ok=True)
    copied = []
    for item in locked:
        target = feed / item.file_name
        if target.exists():
            if sha512(target) != item.content_hash:
                raise CheckFailed(f"{item.file_name} in the feed differs from the lock file's hash")
            continue
        source = packages / item.package.lower() / item.version.lower() / item.file_name
        if not source.is_file():
            raise InputError(f"{item.file_name} is not in {packages}: restore the solution first")
        if sha512(source) != item.content_hash:
            raise CheckFailed(f"{source} differs from the lock file's hash: not copied")
        shutil.copyfile(source, target)
        if sha512(target) != item.content_hash:
            target.unlink()
            raise CheckFailed(f"{item.file_name}: the copy differs from its source; removed")
        copied.append(item.file_name)
    return copied


def check(feed: Path, root: Path = ROOT) -> list[str]:
    """The findings: locked packages missing from the feed or differing from their hash."""
    findings = []
    for item in locked_ostium(root):
        target = feed / item.file_name
        if not target.is_file():
            findings.append(f"{item.file_name}: missing from the feed")
        elif sha512(target) != item.content_hash:
            findings.append(f"{item.file_name}: differs from the lock file's hash")
    return findings


def remove(feed: Path, package: str, version: str) -> bool:
    target = feed / f"{package.lower()}.{version.lower()}.nupkg"
    if not target.is_file():
        return False
    target.unlink()
    return True


def nuget_config(feed: Path) -> str:
    """Ostium.* from the feed only; everything else from nuget.org only; no other source."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<configuration>\n  <packageSources>\n'
        f'    <clear />\n    <add key="nuget.org" value="{NUGET_ORG}" />\n'
        f'    <add key="local" value="{feed.resolve()}" />\n  </packageSources>\n'
        "  <packageSourceMapping>\n"
        '    <packageSource key="nuget.org"><package pattern="*" /></packageSource>\n'
        '    <packageSource key="local"><package pattern="Ostium.*" /></packageSource>\n'
        "  </packageSourceMapping>\n</configuration>\n"
    )


def projects_using_ostium(root: Path = ROOT) -> dict[Path, list[Locked]]:
    """Each project file whose lock file names an Ostium.* package, with those packages."""
    locked_ostium(root)  # the lock files agree on every hash
    by_lock: dict[Path, list[Locked]] = {}
    for item in _entries(root):
        by_lock.setdefault(item.lock_file, []).append(item)
    projects = {}
    for path in sorted(by_lock):
        found = sorted(path.parent.glob("*.csproj"))
        if len(found) != 1:
            raise InputError(f"{path.parent.relative_to(root)}: expected one project file")
        projects[found[0]] = by_lock[path]
    return projects


def _dotnet() -> str:
    found = shutil.which("dotnet")
    if not found:
        raise InputError("dotnet not found")
    return found


def prove(feed: Path, root: Path = ROOT) -> None:
    findings = check(feed, root)
    if findings:
        raise CheckFailed("; ".join(findings))
    # No credential for any private source reaches this restore
    env = {k: v for k, v in os.environ.items() if not k.startswith("NuGetPackageSourceCredentials")}
    # No build server may stay behind holding files of the temporary folder (Windows locks them)
    scratch_dir = tempfile.TemporaryDirectory(prefix="as-local-feed-", ignore_cleanup_errors=True)
    with scratch_dir as scratch:
        config = Path(scratch) / "nuget.config"
        config.write_text(nuget_config(feed), encoding="utf-8")
        empty = Path(scratch) / "packages"
        for project, locked in projects_using_ostium(root).items():
            base = [_dotnet(), "restore", str(project), "--locked-mode", "--disable-build-servers"]
            alone = [*base, "--packages", str(empty), "--configfile", str(config)]
            result = subprocess.run(alone, env=env, capture_output=True, text=True, check=False)  # noqa: S603
            if result.returncode != 0:
                tail = "\n".join((result.stdout + result.stderr).splitlines()[-8:])
                raise CheckFailed(f"{project.name}: the restore from the feed alone failed\n{tail}")
            # Back to NuGet's own folder, so the build files point where `just ci` expects them
            again = subprocess.run(base, capture_output=True, text=True, check=False)  # noqa: S603
            if again.returncode != 0:
                raise CheckFailed(f"{project.name}: the usual restore afterwards failed")
            for item in locked:
                restored = empty / item.package.lower() / item.version.lower() / item.file_name
                if not restored.is_file() or sha512(restored) != item.content_hash:
                    raise CheckFailed(f"{item.file_name}: not restored from the feed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "check", "prove", "remove"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--feed", type=Path, required=True)
        if name == "sync":
            cmd.add_argument("--packages", type=Path, default=None)
        if name == "remove":
            cmd.add_argument("package")
            cmd.add_argument("version")
    sub.add_parser("prove-ci")
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            copied = sync(args.feed, args.packages or nuget_packages())
            names = f" ({', '.join(copied)})" if copied else ""
            print(f"local-feed: {len(copied)} copied{names}")
        elif args.command == "check":
            findings = check(args.feed)
            for finding in findings:
                print(f"local-feed: {finding}")
            if findings:
                return 1
            print("local-feed: every locked Ostium.* package is in the feed")
        elif args.command == "prove":
            prove(args.feed)
            print("local-feed: a locked restore from the feed alone works (no private feed)")
        elif args.command == "remove":
            removed = remove(args.feed, args.package, args.version)
            outcome = "removed" if removed else "not in the feed"
            print(f"local-feed: {outcome}: {args.package} {args.version}")
        else:
            with tempfile.TemporaryDirectory(prefix="as-feed-ci-") as feed:
                copied = sync(Path(feed), nuget_packages())
                prove(Path(feed))
                print(
                    f"local-feed: {len(copied)} package(s) synced into a fresh feed;"
                    " a locked restore from it alone works"
                )
    except InputError as error:
        print(f"local-feed: {error}", file=sys.stderr)
        return 2
    except CheckFailed as error:
        print(f"local-feed: check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
