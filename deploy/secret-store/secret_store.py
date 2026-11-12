"""Secrets (ADR-0010; docs/architecture/compose-base.md, "Secrets"). Standard library; needs `age`.

    python3 -I deploy/secret-store/secret_store.py COMMAND

    generate          create the missing secrets, sealed with age
    rotate NAME       replace one generated secret's value
    enter NAME        type in an entered secret's value at a terminal; sealed with age
    unseal            decrypt them into the in-memory folder
    clear             empty and remove the in-memory folder
    check RENDERED... the manifest and the Compose secrets agree

The manifest (deploy/secrets.manifest.json) holds names, consumers, rotation and generator rules,
never values. A secret is generated (random), derived (a file built from other secrets) or entered:
a value another party issues, such as the backup login (ADR-0010 note of 2026-11-11), typed in once
by the owner. Sealed values live in the store (default ~/.aetherspark/secrets/dev), decrypted ones
only in the in-memory folder (default $XDG_RUNTIME_DIR/aetherspark/secrets, which must be a tmpfs).
No value is ever printed, logged, passed in an argument or placed in an environment variable.

Exit codes: 0 done, 1 findings or a refused folder, 2 unusable input or a missing tool.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets as token
import shutil
import stat
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "secrets.manifest.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
# {name} in a derived template; {name|url} percent-encodes the value (a login inside a URL)
REFERENCE = re.compile(r"\{([a-z0-9][a-z0-9.-]{0,62})(\|url)?\}")
MIN_BYTES = 16  # 128 bits
HOST_PREFIX = "host:"  # a consumer that is a tool on the host (WSL2, the Spark), not a service
ENTRY_KEYS = {"name", "consumers", "rotation_days", "generator", "derived", "entered"}
# What an entered value may look like, by kind: nothing that could change a URL's meaning or carry
# a control character. A password may be any printable ASCII, since it is percent-encoded.
ENTERED = {
    "name": re.compile(r"^[A-Za-z0-9._-]{1,64}$"),
    "password": re.compile(r"^[\x21-\x7e]{12,256}$"),
    "address": re.compile(r"^[A-Za-z0-9.-]{1,253}:[0-9]{1,5}$"),
    "path": re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*/?$"),
}
# Tests and CI give an entered value through this file descriptor instead of a terminal
ENTER_FD = "AETHERSPARK_SECRETS_ENTER_FD"


class InputError(Exception):
    """An unusable manifest, store, folder or tool; nothing is written."""


@dataclass(frozen=True)
class Secret:
    name: str
    consumers: tuple[str, ...]
    rotation_days: int | None
    random_bytes: int  # 0 for a derived file or an entered value
    derived: str  # a template naming other secrets as {name}; "" otherwise
    entered: str = ""  # the kind of an entered value (a key of ENTERED); "" otherwise


def references(template: str) -> set[str]:
    """The secrets a derived template names."""
    return {m[0] for m in REFERENCE.findall(template)}


def load_manifest(path: Path = MANIFEST) -> list[Secret]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path.name}: {error}") from error
    if (
        not isinstance(data, dict)
        or set(data) != {"secrets"}
        or not isinstance(data["secrets"], list)
    ):
        raise InputError(f'{path.name}: expected {{"secrets": [...]}}')
    result: list[Secret] = []
    for index, entry in enumerate(data["secrets"]):
        where = f"{path.name} entry {index}"
        if not isinstance(entry, dict) or not {"name", "consumers"} <= set(entry) <= ENTRY_KEYS:
            raise InputError(
                f"{where}: keys must be name, consumers and one of generator, derived, entered"
            )
        name, consumers = entry["name"], entry["consumers"]
        if not isinstance(name, str) or not NAME.match(name):
            raise InputError(f"{where}: name {name!r} is not a lower-case file name")
        if any(s.name == name for s in result):
            raise InputError(f"{where}: {name} is listed twice")
        if (
            not isinstance(consumers, list)
            or not consumers
            or not all(isinstance(c, str) for c in consumers)
        ):
            raise InputError(f"{where}: consumers must name at least one service")
        rotation = entry.get("rotation_days")
        if rotation is not None and (
            not isinstance(rotation, int) or isinstance(rotation, bool) or rotation < 1
        ):
            raise InputError(f"{where}: rotation_days must be a positive number of days or absent")
        generator, derived, entered = (
            entry.get("generator"),
            entry.get("derived"),
            entry.get("entered"),
        )
        if [generator, derived, entered].count(None) != 2:
            raise InputError(f"{where}: exactly one of generator, derived and entered")
        size = 0
        kind = ""
        if entered is not None:
            if (
                not isinstance(entered, dict)
                or set(entered) != {"kind"}
                or entered["kind"] not in ENTERED
            ):
                raise InputError(
                    f'{where}: entered must be {{"kind": one of {", ".join(ENTERED)}}}'
                )
            kind = entered["kind"]
        elif generator is not None:
            if (
                not isinstance(generator, dict)
                or generator.get("type") != "random"
                or set(generator) != {"type", "bytes"}
            ):
                raise InputError(f'{where}: generator must be {{"type": "random", "bytes": n}}')
            size = generator["bytes"]
            if not isinstance(size, int) or isinstance(size, bool) or size < MIN_BYTES:
                raise InputError(f"{where}: at least {MIN_BYTES} random bytes (128 bits)")
        elif not isinstance(derived, str) or not REFERENCE.search(derived):
            raise InputError(f"{where}: derived must be a template naming at least one {{secret}}")
        result.append(Secret(name, tuple(consumers), rotation, size, derived or "", kind))
    sources = {s.name for s in result if not s.derived}  # generated or entered
    for s in result:
        missing = sorted(references(s.derived) - sources)
        if missing:
            raise InputError(
                f"{path.name}: {s.name} refers to {missing}, which are not generated or entered"
            )
    return result


def render(template: str, values: dict[str, str]) -> str:
    """A derived file's text; every {name} must be a known value; {name|url} is percent-encoded."""

    def value(match: re.Match[str]) -> str:
        text = values[match[1]]
        return urllib.parse.quote(text, safe="") if match[2] else text

    return REFERENCE.sub(value, template)


def check_entered(kind: str, value: str) -> None:
    """Refuses a value that does not have its kind's form; the message never holds the value."""
    if not ENTERED[kind].match(value) or (kind == "path" and ".." in value.split("/")):
        raise InputError(f"not a valid {kind}: see docs/runbooks/secrets.md for the allowed form")


def is_tmpfs(path: Path, mountinfo: str | None = None) -> bool:
    """True when the longest mount point above `path` is a tmpfs (from /proc/self/mountinfo)."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            return False
    target = path.as_posix()
    best, fstype = "", ""
    for line in mountinfo.splitlines():
        fields = line.split(" ")
        if " - " not in line or len(fields) < 5:
            continue
        point = fields[4].replace("\\040", " ")
        after = line.split(" - ", 1)[1].split(" ")
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best):
            best, fstype = point, after[0]
    return fstype == "tmpfs"


def secret_dir() -> Path:
    explicit = os.environ.get("AETHERSPARK_SECRETS_DIR")
    if explicit:
        return Path(explicit)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise InputError("XDG_RUNTIME_DIR is not set; on the Spark set AETHERSPARK_SECRETS_DIR")
    return Path(runtime) / "aetherspark" / "secrets"


def store_dir() -> Path:
    return Path(
        os.environ.get(
            "AETHERSPARK_SECRETS_STORE", Path.home() / ".aetherspark" / "secrets" / "dev"
        )
    )


def key_file() -> Path:
    return Path(
        os.environ.get("AETHERSPARK_SECRETS_KEY", Path.home() / ".aetherspark" / "keys" / "dev.key")
    )


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise InputError(f"{name} not found: install it (docs/runbooks/secrets.md)")
    return found


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _recipient(key: Path) -> str:
    if not key.is_file():
        _private_dir(key.parent)
        subprocess.run([_tool("age-keygen"), "-o", str(key)], check=True, capture_output=True)  # noqa: S603
        key.chmod(0o600)
    out = subprocess.run(  # noqa: S603 - fixed tool, no shell
        [_tool("age-keygen"), "-y", str(key)], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def seal(value: bytes, sealed: Path, recipient: str | None = None) -> None:
    """Encrypts `value` to the dev key into `sealed` (0600); the value goes over stdin only."""
    _private_dir(sealed.parent)
    subprocess.run(  # noqa: S603 - fixed tool, no shell
        [_tool("age"), "-r", recipient or _recipient(key_file()), "-o", str(sealed)],
        input=value,
        check=True,
        capture_output=True,
    )
    sealed.chmod(0o600)


def open_sealed(sealed: Path) -> bytes:
    """A sealed file's value, in memory only."""
    out = subprocess.run(  # noqa: S603 - fixed tool, no shell
        [_tool("age"), "-d", "-i", str(key_file()), str(sealed)], check=True, capture_output=True
    )
    return out.stdout


def generate(manifest: list[Secret]) -> list[str]:
    """Seals every generated secret that the store lacks; returns the names created."""
    store = store_dir()
    _private_dir(store)
    recipient = _recipient(key_file())
    created = []
    for s in manifest:
        sealed = store / f"{s.name}.age"
        if s.derived or s.entered or sealed.exists():
            continue
        seal(token.token_urlsafe(s.random_bytes).encode("ascii"), sealed, recipient)
        created.append(s.name)
    return created


def rotate(manifest: list[Secret], name: str) -> None:
    """Replaces one generated secret's sealed value; the consumers get it at the next unseal."""
    entry = next((s for s in manifest if s.name == name), None)
    if entry is None:
        raise InputError(f"{name} is not in the manifest")
    if entry.derived:
        raise InputError(f"{name} is derived; rotate the secrets it names instead")
    if entry.entered:
        raise InputError(f"{name} is entered; type its new value with `just secrets enter {name}`")
    sealed = store_dir() / f"{name}.age"
    if sealed.exists():
        sealed.unlink()
    generate([entry])


def _decrypt(sealed: Path) -> str:
    return open_sealed(sealed).decode("utf-8")


def _read_entered(name: str) -> str:
    """One value: from the tests' file descriptor, else typed twice at a terminal, never echoed.
    A pipe or a redirect is refused: it leaves the value in a file or the shell's history."""
    fd = os.environ.get(ENTER_FD)
    if fd:
        with os.fdopen(int(fd), "r", encoding="utf-8", closefd=False) as handle:
            return handle.readline().rstrip("\n")
    if not sys.stdin.isatty():
        raise InputError("enter reads from a terminal only, never from a pipe or a file")
    value = getpass.getpass(f"{name}: ")
    if getpass.getpass(f"{name}, again: ") != value:
        raise InputError("the two entries differ; nothing was sealed")
    return value


def enter(manifest: list[Secret], name: str) -> None:
    """Seals the value of an entered secret, replacing any earlier one."""
    entry = next((s for s in manifest if s.name == name), None)
    if entry is None:
        raise InputError(f"{name} is not in the manifest")
    if not entry.entered:
        raise InputError(f"{name} is not an entered secret")
    value = _read_entered(name)
    check_entered(entry.entered, value)
    sealed = store_dir() / f"{name}.age"
    if sealed.exists():
        sealed.unlink()
    seal(value.encode("utf-8"), sealed)


def not_entered(manifest: list[Secret]) -> list[str]:
    """The entered secrets whose value is not in the store yet."""
    store = store_dir()
    return [s.name for s in manifest if s.entered and not (store / f"{s.name}.age").is_file()]


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _docker_made(target: Path) -> Path | None:
    """The folder to remove when Docker has filled the secrets path: it creates a missing bind
    source as an empty root-owned folder, for a secret file too, when it restarts a container
    before the secrets are unsealed (after a restart of WSL or Docker without `just down`).
    None when the folder is ours and holds no folder (secret files are never folders)."""
    if sys.platform == "win32" or not target.is_dir():
        return None  # the in-memory folder exists only on Linux (WSL2, the Spark)
    mine = os.getuid()
    if target.stat().st_uid == mine and not any(p.is_dir() for p in target.iterdir()):
        return None
    # Climb to the topmost folder that is not ours, but never above the runtime folder
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"))
    stray = target
    while stray.parent.stat().st_uid != mine and runtime in stray.parent.parents:
        stray = stray.parent
    return stray


def _refuse_docker_made(target: Path, then: str) -> None:
    """Stops with the folder to remove and the exact command for this machine: on the desktop it
    is run from PowerShell into this WSL distribution, on the Spark with sudo. Seen after a restart
    of Docker while the secrets were not unsealed, also for services that do not restart by
    themselves (2026-11-10; cause not known)."""
    stray = _docker_made(target)
    if stray is None:
        return
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    command = (
        f"from PowerShell, wsl -d {distro} -u root -- rm -r '{stray}'"
        if distro
        else f"sudo rm -r '{stray}'"
    )
    raise InputError(
        f"{stray} holds folders Docker created where the secret files belong; they hold no "
        f"secret. Remove them as root, then {then}: {command}"
    )


def unseal(
    manifest: list[Secret], mountinfo: str | None = None, only: set[str] | None = None
) -> list[str]:
    """Decrypts every secret into the in-memory folder, which must be a tmpfs; returns the names.
    With `only`: just those secrets and what a derived one is built from, nothing else touched (a
    machine whose store holds only the backup login, before its store is restored)."""
    if only is not None:
        unknown = only - {s.name for s in manifest}
        if unknown:
            raise InputError(f"not in the manifest: {', '.join(sorted(unknown))}")
        wanted = set(only)
        for s in manifest:
            if s.name in only:
                wanted |= references(s.derived)
        manifest = [s for s in manifest if s.name in wanted]
    target = secret_dir()
    _refuse_docker_made(target, "run `just up` again")
    _private_dir(target)
    if not is_tmpfs(target, mountinfo):
        raise InputError(f"{target} is not on a tmpfs: secrets are never decrypted to disk")
    store = store_dir()
    missing = set(not_entered(manifest))
    values: dict[str, str] = {}
    for s in manifest:
        if s.derived or s.name in missing:
            continue
        sealed = store / f"{s.name}.age"
        if not sealed.is_file():
            raise InputError(f"{s.name} is not in the store: run generate first")
        values[s.name] = _decrypt(sealed)
    written = []
    for s in manifest:
        path = target / s.name
        if path.exists():
            path.chmod(stat.S_IWUSR | stat.S_IRUSR)
            path.unlink()
        # A missing entered value, and every file built from one, are left out (and an older copy
        # removed): the stack starts without them, and the job that needs them says so
        if s.name in missing or references(s.derived) & missing:
            continue
        _write_private(path, render(s.derived, values) if s.derived else values[s.name])
        written.append(s.name)
    return written


def clear() -> bool:
    """Removes the in-memory folder and everything in it; True if there was one. Folders Docker
    made there are not ours to remove: named, with the command (`just down` ends with this)."""
    target = secret_dir()
    if not target.exists():
        return False
    _refuse_docker_made(target, "nothing else is needed")
    for item in target.iterdir():
        item.chmod(stat.S_IWUSR | stat.S_IRUSR)
        item.unlink()
    target.rmdir()
    return True


def check(manifest: list[Secret], rendered: list[dict[str, Any]]) -> list[str]:
    """Every Compose secret is in the manifest, by name and file; every manifest entry is used."""
    findings: list[str] = []
    known = {s.name: s for s in manifest}
    used: set[str] = set()
    for project in rendered:
        label = project.get("name", "?")
        for key, item in (project.get("secrets") or {}).items():
            item = item or {}
            if key not in known:
                findings.append(f"{label}: secret {key} is not in the manifest")
                continue
            used.add(key)
            source = str(item.get("file") or "")
            if not source or Path(source).name != key:
                findings.append(f"{label}: secret {key} must come from a file named {key}")
        for name, service in (project.get("services") or {}).items():
            for ref in (service or {}).get("secrets") or []:
                secret = ref.get("source") if isinstance(ref, dict) else ref
                if secret in known and name not in known[secret].consumers:
                    findings.append(
                        f"{label}: service {name} reads {secret}, not one of its consumers"
                    )
    # A generated secret may reach its service only inside a derived file
    for s in manifest:
        if s.derived and s.name in used:
            used.update(references(s.derived))
    # A secret read only by a host tool (consumer "host:<tool>"), never by a container
    used.update(s.name for s in manifest if all(c.startswith(HOST_PREFIX) for c in s.consumers))
    findings.extend(
        f"manifest: {name} is used by no Compose file" for name in sorted(set(known) - used)
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "clear"):
        sub.add_parser(command)
    uns = sub.add_parser("unseal")
    uns.add_argument("--only", action="append", help="only this secret (repeatable)")
    rot = sub.add_parser("rotate")
    rot.add_argument("name")
    ent = sub.add_parser("enter")
    ent.add_argument("name")
    chk = sub.add_parser("check")
    chk.add_argument(
        "rendered", nargs="+", type=Path, help="docker compose config --format json output"
    )
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "generate":
            created = generate(manifest)
            print(
                f"secrets: {len(created)} created" + (f" ({', '.join(created)})" if created else "")
            )
        elif args.command == "rotate":
            rotate(manifest, args.name)
            print(f"secrets: {args.name} rotated; run unseal and restart its consumers")
        elif args.command == "enter":
            enter(manifest, args.name)
            print(f"secrets: {args.name} entered and sealed; run unseal to use it")
        elif args.command == "unseal":
            only = set(args.only) if args.only else None
            written = unseal(manifest, only=only)
            left = sorted(only - set(written)) if only is not None else []
            if left:
                raise InputError(f"not unsealed, an entered value is missing: {', '.join(left)}")
            print(f"secrets: {len(written)} unsealed into {secret_dir()}")
            waiting = not_entered(manifest)
            if waiting:
                print(
                    f"secrets: not entered yet, left out with what is built from them: "
                    f"{', '.join(waiting)} (just secrets enter NAME)"
                )
        elif args.command == "clear":
            print("secrets: cleared" if clear() else "secrets: nothing to clear")
        else:
            projects = [json.loads(p.read_text(encoding="utf-8")) for p in args.rendered]
            findings = check(manifest, projects)
            print(
                "\n".join(findings)
                or f"secrets: manifest and {len(projects)} Compose project(s) agree"
            )
            return 1 if findings else 0
    except InputError as error:
        print(f"secrets: {error}", file=sys.stderr)
        return 2 if "not on a tmpfs" not in str(error) else 1
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"secrets: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
