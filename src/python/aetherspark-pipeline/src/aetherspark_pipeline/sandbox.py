"""The child process that parses one job (data-plane.md, section 4; T20): a fresh interpreter with
a memory limit (RLIMIT_AS), a CPU limit, a wall-clock timeout, no file writes, no core dumps and a
few file handles. A hostile file can make the child fail; the worker survives and fails the job."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    raise ImportError("the pipeline's sandbox needs Linux (rlimits); its tests skip elsewhere")

import json
import resource
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Exit codes the child uses for what it could detect itself
EXIT_REFUSED = 2
EXIT_MEMORY = 3
# The report line and the facts, one JSON line each (a large 10-K has some thousands)
MAX_OUTPUT = 256 * 1024 * 1024


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    cpu_seconds: int
    wall_seconds: int


@dataclass(frozen=True)
class Outcome:
    ok: bool
    reason: str
    # The child's report beyond ok and reason, and its fact rows (JSON lines), when it parsed
    report: dict[str, object] = field(default_factory=dict)
    rows: tuple[str, ...] = ()


def _limit(limits: Limits) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    # SIGXCPU at the soft limit, SIGKILL a little later
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def run(argv: list[str], limits: Limits, home: Path | None = None) -> Outcome:
    """Runs `argv` under the limits; the child reports `{"ok": bool, "reason": str, ...}` as its
    first line on stdout, then any fact rows, one JSON line each. `home` is its HOME (Arelle wants
    one; nothing can be written there, the file-size limit is 0)."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    if home is not None:
        env["HOME"] = str(home)
    try:
        done = subprocess.run(  # noqa: S603 - argv is the worker's own interpreter and module
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=limits.wall_seconds,
            preexec_fn=lambda: _limit(limits),
            env=env,
            close_fds=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Outcome(False, f"the parse ran past its time limit ({limits.wall_seconds} s)")
    code = done.returncode
    if code == EXIT_MEMORY:
        return Outcome(False, f"the parse ran past its memory limit ({limits.memory_bytes} bytes)")
    if code < 0:
        sig = -code
        if sig in (signal.SIGXCPU, signal.SIGKILL):
            return Outcome(False, f"the parse ran past its CPU limit ({limits.cpu_seconds} s)")
        return Outcome(False, f"the parse was stopped by signal {signal.Signals(sig).name}")
    if len(done.stdout) > MAX_OUTPUT:
        return Outcome(False, f"the parse wrote more than {MAX_OUTPUT} bytes of output")
    first, _, rest = done.stdout.partition(b"\n")
    try:
        report = json.loads(first.decode("utf-8"))
        ok, reason = bool(report["ok"]), str(report["reason"])
    except (ValueError, KeyError, TypeError):
        tail = done.stderr[-300:].decode("utf-8", "replace").strip().splitlines()
        return Outcome(
            False,
            f"the parse ended without a report (exit {code}): {tail[-1] if tail else 'no message'}",
        )
    if code not in (0, EXIT_REFUSED) or ok != (code == 0):
        return Outcome(False, f"the parse ended with exit {code}: {reason}")
    rows = tuple(line for line in rest.decode("utf-8").splitlines() if line) if ok else ()
    extra = {k: v for k, v in report.items() if k not in ("ok", "reason")}
    if ok and extra.get("facts", len(rows)) != len(rows):
        return Outcome(
            False, f"the parse reported {extra.get('facts')} facts and wrote {len(rows)}"
        )
    return Outcome(ok, reason, extra, rows)


def parse_job(job_folder: Path, instance: str, store: Path, limits: Limits) -> Outcome:
    """The job's parse in a child process: the pre-parse, the reference check, then Arelle offline
    on `instance` with the taxonomy `store` (child.py)."""
    home = job_folder.with_name(job_folder.name + ".home")
    home.mkdir(mode=0o700, exist_ok=True)
    argv = [sys.executable, "-I", "-m", "aetherspark_pipeline.child"]
    return run([*argv, str(job_folder), instance, str(store)], limits, home)
