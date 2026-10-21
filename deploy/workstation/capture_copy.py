"""One-way copy of usage captures into Greenlight's intake (ADR-0013 amendment 6, exchange G-004).

Standard library only. Run by the owner's scheduled task from a hash-checked copy in an
admin-only folder, with ``python -I -S``. Never run by a Claude session against a project folder.

Exit codes: 0 done, 2 done with refused or ignored files, 1 stopped on an error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 50
CAPTURE_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", re.ASCII)
CAPTURE_NAME = re.compile(rf"capture-{CAPTURE_ID.pattern}\.json", re.ASCII)
LOOKS_LIKE_CAPTURE = re.compile(r"capture-.*\.json", re.ASCII | re.IGNORECASE | re.DOTALL)
PROJECT_ID = re.compile(r"GL-[0-9]{4}", re.ASCII)
TEMPORARY_NAME = re.compile(r"\.tmp-[0-9a-f]{16}", re.ASCII)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# Windows attributes: reparse point, offline file, cloud placeholder (opening recalls it)
_WINDOWS_REFUSED_ATTRIBUTES = 0x400 | 0x1000 | 0x40000 | 0x400000


class StopRunError(Exception):
    """An error that ends the run: nothing after it is copied."""


class RefusedError(Exception):
    """A file that is not copied; the message is the reason, never content or a path."""


@dataclass(frozen=True)
class Source:
    project_id: str
    folder: Path


class RunLog:
    """One JSON object per event: project ids, capture names and hashes, never paths."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.refused = 0

    def write(self, event: str, **fields: Any) -> None:
        record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "event": event}
        record.update(fields)
        with self._path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, sort_keys=True) + "\n")

    def refuse(self, project_id: str, name: str, reason: str) -> None:
        self.refused += 1
        self.write("refused", project=project_id, name=name, reason=reason)


# --- file system checks ---------------------------------------------------------------------


def is_link_or_reparse(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    return (
        stat.S_ISLNK(st.st_mode)
        or bool(attributes & _WINDOWS_REFUSED_ATTRIBUTES)
        or getattr(st, "st_reparse_tag", 0) != 0
    )


def check_regular(st: os.stat_result) -> None:
    if is_link_or_reparse(st):
        raise RefusedError("link, reparse point or placeholder")
    if not stat.S_ISREG(st.st_mode):
        raise RefusedError("not a regular file")
    if st.st_nlink != 1:
        raise RefusedError("more than one hard link")
    if st.st_size > MAX_BYTES:
        raise RefusedError("larger than 1 MiB")


def read_regular(path: Path, listed: os.stat_result | None = None) -> bytes:
    """Read a regular single-link file of at most 1 MiB; refuse a swap between check and read."""
    if listed is not None and is_link_or_reparse(listed):
        # The directory listing reports reparse and placeholder attributes without opening the file
        raise RefusedError("link, reparse point or placeholder")
    before = os.lstat(path)  # not DirEntry.stat(): on Windows it reports st_ino and st_nlink as 0
    check_regular(before)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        after = os.fstat(handle.fileno())
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RefusedError("changed while being read")
        check_regular(after)
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RefusedError("larger than 1 MiB")
    return data


def folder_identity(path: Path) -> tuple[int, int]:
    st = path.lstat()
    if is_link_or_reparse(st) or not stat.S_ISDIR(st.st_mode):
        raise StopRunError("an intake folder is a link, reparse point or not a folder")
    return st.st_dev, st.st_ino


# --- content check (G-004 point 3: closed schema and cross-checks) ---------------------------

_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2})?(Z|[+-][0-9]{2}:[0-9]{2})", re.ASCII
)
# Stricter than the schema, to keep the free-text fields from carrying other data
_MODEL = re.compile(r"[A-Za-z0-9._:/@+-]{1,100}", re.ASCII)
_AGENT = re.compile(r"[A-Za-z0-9._-]{1,64}", re.ASCII)
_REASON = re.compile(r"[ -~]{1,200}", re.ASCII)
_FORMAT = re.compile(r"1\.[0-9]+\.[0-9]+", re.ASCII)
_TOP_KEYS = {
    "format_version",
    "project_id",
    "capture_id",
    "captured_at",
    "issue",
    "window",
    "usage",
}
_COUNT_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
_TTL_KEYS = ("cache_creation_5m_tokens", "cache_creation_1h_tokens")
_ENTRY_KEYS = {"model", "source", "agent", *_COUNT_KEYS, *_TTL_KEYS}


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RefusedError("duplicate key")
        result[key] = value
    return result


def _no_constants(_: str) -> Any:
    raise RefusedError("NaN or Infinity")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RefusedError(reason)


def _object(value: Any, required: set[str], allowed: set[str], what: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{what} is not an object")
    keys = set(value)
    _require(required <= keys, f"{what} misses a field")
    _require(keys <= allowed, f"{what} has an unknown field")
    result: dict[str, Any] = value
    return result


def _text(value: Any, pattern: re.Pattern[str], what: str) -> str:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None, f"invalid {what}")
    result: str = value
    return result


def _count(value: Any, what: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f"bad {what}")
    result: int = value
    return result


def _time(value: Any, what: str) -> dt.datetime:
    text = _text(value, _TIMESTAMP, what)
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise RefusedError(f"invalid {what}") from None


def _check_usage(value: Any) -> str:
    usage = _object(
        value, {"status", "by_model"}, {"status", "missing_reason", "by_model"}, "usage"
    )
    status = usage["status"]
    _require(status in ("complete", "partial", "missing"), "invalid usage status")
    if status != "complete" or "missing_reason" in usage:
        _text(usage.get("missing_reason"), _REASON, "missing_reason")

    entries = usage["by_model"]
    _require(isinstance(entries, list), "by_model is not a list")
    _require(len(entries) <= MAX_ENTRIES, "too many usage entries")
    _require(status != "missing" or not entries, "missing usage has entries")
    seen: set[tuple[str, str, str | None]] = set()
    for item in entries:
        entry = _object(item, {"model", "source", *_COUNT_KEYS}, _ENTRY_KEYS, "usage entry")
        model = _text(entry["model"], _MODEL, "model")
        source = entry["source"]
        _require(source in ("main", "subagent"), "invalid source")
        agent = None
        if "agent" in entry:
            _require(source == "subagent", "agent on a main entry")
            agent = _text(entry["agent"], _AGENT, "agent")
        for key in _COUNT_KEYS:
            _count(entry[key], key)
        ttl = [key for key in _TTL_KEYS if key in entry]
        if ttl:
            _require(len(ttl) == len(_TTL_KEYS), "only one TTL part")
            parts = sum(_count(entry[key], key) for key in _TTL_KEYS)
            _require(parts == entry["cache_creation_tokens"], "TTL parts do not sum to the total")
        identity = (model, source, agent)
        _require(identity not in seen, "two entries share model, source and agent")
        seen.add(identity)
    return str(status)


def check_content(data: bytes, project_id: str, name: str) -> None:
    """Refuse anything but a capture that fits G-004's closed schema and cross-checks."""
    try:
        capture = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_no_constants,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise RefusedError("not UTF-8 JSON") from None

    capture = _object(capture, _TOP_KEYS, _TOP_KEYS, "capture")
    _text(capture["format_version"], _FORMAT, "format_version")
    _require(capture["project_id"] == project_id, "project_id does not match the folder list")
    capture_id = _text(capture["capture_id"], CAPTURE_ID, "capture_id")
    _require(name == f"capture-{capture_id}.json", "capture_id does not match the file name")
    captured_at = _time(capture["captured_at"], "captured_at")
    basic = captured_at.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    _require(capture_id.startswith(basic), "capture_id does not start with captured_at")

    issue = capture["issue"]
    _require(issue is None or _count(issue, "issue") >= 1, "invalid issue")
    status = _check_usage(capture["usage"])
    window = capture["window"]
    if status == "missing":
        _require(window is None, "window set for missing usage")
    else:
        window = _object(window, {"from", "to"}, {"from", "to"}, "window")
        start, end = _time(window["from"], "window.from"), _time(window["to"], "window.to")
        _require(start <= end <= captured_at, "window out of order")


# --- the copy -------------------------------------------------------------------------------


def read_folder_list(path: Path) -> list[Source]:
    """Lines of ``<project id><TAB><capture folder>``; blank and ``#`` lines are skipped."""
    sources: list[Source] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not parts[1].strip():
            raise StopRunError(f"folder list line {number}: expected '<project id><TAB><folder>'")
        project_id, folder = parts[0].strip(), parts[1].strip()
        if not PROJECT_ID.fullmatch(project_id):
            raise StopRunError(f"folder list line {number}: invalid project id")
        if project_id in seen:
            raise StopRunError(f"folder list line {number}: project id listed twice")
        seen.add(project_id)
        sources.append(Source(project_id, Path(folder)))
    return sources


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def existing_hash(path: Path) -> str | None:
    try:
        return sha256(read_regular(path))
    except FileNotFoundError:
        return None
    except RefusedError as refused:
        raise StopRunError(f"intake entry {path.name}: {refused}") from None


def write_once(target: Path, data: bytes) -> None:
    """Write under a temporary name, then link to the final name; never replaces a name."""
    temporary = target.with_name(f".tmp-{secrets.token_hex(8)}")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, target)
    finally:
        temporary.unlink()
    target.chmod(stat.S_IREAD)


def remove_stale_temporaries(folder: Path) -> None:
    for entry in folder.iterdir():
        if TEMPORARY_NAME.fullmatch(entry.name) and stat.S_ISREG(entry.lstat().st_mode):
            entry.unlink()


def copy_project(source: Source, intake_root: Path, log: RunLog) -> None:
    try:
        folder_stat = source.folder.lstat()
    except FileNotFoundError:
        log.write("folder-missing", project=source.project_id)
        return
    if is_link_or_reparse(folder_stat) or not stat.S_ISDIR(folder_stat.st_mode):
        log.refuse(source.project_id, "", "capture folder is a link, reparse point or not a folder")
        return

    target_folder = intake_root / source.project_id
    target_folder.mkdir(exist_ok=True)
    identity = folder_identity(target_folder)
    remove_stale_temporaries(target_folder)

    with os.scandir(source.folder) as listing:
        entries = sorted(listing, key=lambda e: e.name)
    candidates = [e for e in entries if CAPTURE_NAME.fullmatch(e.name)]
    ignored = sum(1 for e in entries if LOOKS_LIKE_CAPTURE.fullmatch(e.name)) - len(candidates)
    if ignored:
        log.refused += ignored
        log.write("ignored", project=source.project_id, count=ignored)

    for entry in candidates:
        try:
            data = read_regular(Path(entry.path), entry.stat(follow_symlinks=False))
            check_content(data, source.project_id, entry.name)
        except RefusedError as refused:
            log.refuse(source.project_id, entry.name, str(refused))
            continue
        digest = sha256(data)
        if folder_identity(target_folder) != identity:
            raise StopRunError(f"intake folder for {source.project_id} changed during the run")
        target = target_folder / entry.name
        known = existing_hash(target)
        if known == digest:
            log.write("skipped", project=source.project_id, name=entry.name, sha256=digest)
            continue
        if known is not None:
            log.write(
                "conflict",
                project=source.project_id,
                name=entry.name,
                sha256=digest,
                intake_sha256=known,
            )
            raise StopRunError(
                f"{source.project_id}/{entry.name}: known name with a different hash"
            )
        write_once(target, data)
        log.write("copied", project=source.project_id, name=entry.name, sha256=digest)


def run(folder_list: Path, intake_root: Path, log_path: Path) -> int:
    log = RunLog(log_path)
    log.write("start")
    try:
        sources = read_folder_list(folder_list)
        folder_identity(intake_root)
        for source in sources:
            copy_project(source, intake_root, log)
    except StopRunError as error:
        log.write("error", reason=str(error))
    except Exception as error:  # any other failure ends the run, logged by type only (no paths)
        log.write("error", reason=type(error).__name__)
    else:
        status = "refused" if log.refused else "ok"
        log.write("end", status=status, refused=log.refused)
        return EXIT_REFUSED if log.refused else EXIT_OK
    log.write("end", status="error", refused=log.refused)
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-way copy of usage captures (G-004)")
    parser.add_argument("--folders", type=Path, required=True, help="folder list (id, TAB, folder)")
    parser.add_argument("--intake", type=Path, required=True, help="Greenlight's intake root")
    parser.add_argument("--log", type=Path, required=True, help="run log, appended as JSON lines")
    args = parser.parse_args(argv)
    return run(args.folders, args.intake, args.log)


if __name__ == "__main__":
    sys.exit(main())
