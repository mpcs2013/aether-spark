"""Image scan policy (ADR-0005 amendment 3, #31). Standard library only.

`targets` prints what to scan: the images the repository builds (targets.json) and every image its
Compose files run, which must be pinned by digest. `evaluate` reads Grype's JSON for each target
and fails on any High or Critical finding, fixed or not, that no valid, unexpired exception covers.

    python -I scan_policy.py targets
    python -I scan_policy.py evaluate --arch amd64 --exceptions exceptions.json \\
        --result dev-container=0.json [--result ...] [--today YYYY-MM-DD]

Exit codes: 0 pass, 1 failing findings, 2 unusable input (targets, exceptions or results).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGETS = HERE / "targets.json"

FAIL_SEVERITIES = ("Critical", "High")
# Grype's severities; anything else (a renamed or lower-cased value) is refused, never passed
KNOWN_SEVERITIES = {"Critical", "High", "Medium", "Low", "Negligible", "Unknown"}
ARCHES = ("amd64", "arm64")
MAX_DAYS = 90
ID_FORM = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|GO-\d{4}-\d{4,})$")
NAME_FORM = re.compile(r"^[a-z0-9][a-z0-9._/:-]*$")
DIGEST_REF = re.compile(r"^[^\s@]+@(?P<digest>sha256:[0-9a-f]{64})$")
# Every Dockerfile or Containerfile, and every Compose file, anywhere in the tracked tree
IMAGE_FILE = re.compile(
    r"(^|/)(Dockerfile|Containerfile)([.-][^/]*)?$|\.(Dockerfile|Containerfile)$"
)
COMPOSE_FILE = re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$")
# Every `image` key, block or flow style, quoted or not; values are checked one by one
IMAGE_KEY = re.compile(r"""(?:^|[\s{,])["']?image["']?\s*:\s*["']?(?P<ref>[^\s"',}#]+)""", re.M)
EXCEPTION_KEYS = {"id", "target", "package", "reason", "issue", "approved", "expires", "arches"}
REQUIRED_KEYS = EXCEPTION_KEYS - {"arches"}


class InputError(Exception):
    """Targets, exceptions or results that cannot be used; the scan fails closed."""


@dataclass(frozen=True)
class Target:
    kind: str  # "build" or "pull"
    name: str
    source: str  # build context, or the image reference
    dockerfile: str = ""  # build only: the Dockerfile, relative to the repository root
    distro: bool = True  # Grype must detect an OS distribution (false for scratch images)


@dataclass(frozen=True)
class Finding:
    target: str
    vuln: str
    aliases: frozenset[str]
    severity: str
    package: str
    version: str
    fix_state: str
    fix_versions: tuple[str, ...]


@dataclass
class Exception_:
    id: str
    target: str
    package: str
    reason: str
    issue: int
    approved: dt.date
    expires: dt.date
    arches: tuple[str, ...]
    matched: list[Finding] = field(default_factory=list)

    def covers(self, finding: Finding, arch: str) -> bool:
        return (
            arch in self.arches
            and self.target == finding.target
            and self.package == finding.package
            and self.id in finding.aliases
        )


def image_name(ref: str) -> str:
    """The reference without tag and digest, so an exception survives a digest bump."""
    path = ref.split("@", 1)[0]
    last = path.rsplit("/", 1)
    if ":" in last[-1]:
        path = path.rsplit(":", 1)[0]
    return path


def repo_files(root: Path) -> list[str]:
    """The tracked files; a scan that cannot list them fails closed."""
    try:
        listing = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607 - git from PATH, as in CI
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise InputError(f"cannot list the repository's files: {error}") from error
    return [name for name in listing.decode("utf-8").split("\0") if name]


def load_targets(
    path: Path = TARGETS, root: Path = ROOT, files: list[str] | None = None
) -> list[Target]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path.name}: {error}") from error
    if not isinstance(data, dict) or not set(data) <= {"build", "local_images", "exclude"}:
        raise InputError(f"{path.name}: expected the keys build, local_images and exclude")
    tracked = repo_files(root) if files is None else files
    targets: list[Target] = []
    for entry in data.get("build", []):
        name, context = entry.get("name", ""), entry.get("context", "")
        dockerfile = entry.get("dockerfile", f"{context}/Dockerfile")
        if not NAME_FORM.match(name) or not context or dockerfile not in tracked:
            raise InputError(f"{path.name}: unusable build entry {entry}")
        if not set(entry) <= {"name", "context", "dockerfile", "distro"}:
            raise InputError(f"{path.name}: unknown keys in build entry {entry}")
        targets.append(Target("build", name, context, dockerfile, entry.get("distro", True)))
    # Every Dockerfile in the repository is a target or excluded with a reason, so a new image
    # cannot skip the scan
    exclude = data.get("exclude", {})
    if not isinstance(exclude, dict) or not all(
        isinstance(v, str) and len(v) >= 20 for v in exclude.values()
    ):
        raise InputError(f"{path.name}: exclude maps a file to its reason (20+ characters)")
    built = {t.dockerfile for t in targets}
    found = {f for f in tracked if IMAGE_FILE.search(f)}
    if found - set(exclude) != built or not set(exclude) <= found:
        raise InputError(
            f"{path.name}: built {sorted(built)} and excluded {sorted(exclude)} differ from the "
            f"repository's Dockerfiles {sorted(found)}"
        )
    local = set(data.get("local_images", []))
    refs: dict[str, str] = {}
    for compose in sorted(f for f in tracked if COMPOSE_FILE.search(f)):
        text = (root / compose).read_text(encoding="utf-8")
        for match in IMAGE_KEY.finditer(text):
            ref = match["ref"]
            if ref in local:
                continue
            if "$" in ref or not DIGEST_REF.match(ref):
                raise InputError(f"{compose}: image {ref} is not pinned by digest")
            if refs.setdefault(image_name(ref), ref) != ref:
                raise InputError(f"{compose}: {image_name(ref)} is pinned to two references")
    for name, ref in sorted(refs.items()):
        if any(t.name == name for t in targets):
            raise InputError(f"{path.name}: {name} is both built and pulled")
        targets.append(Target("pull", name, ref))
    if len({t.name for t in targets}) != len(targets):
        raise InputError(f"{path.name}: duplicate target names")
    return targets


def _date(value: Any, key: str, where: str) -> dt.date:
    if not isinstance(value, str):
        raise InputError(f"{where}: {key} must be a YYYY-MM-DD string")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise InputError(f"{where}: {key} {value!r} is not a date") from error


def load_exceptions(path: Path, today: dt.date) -> list[Exception_]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path.name}: {error}") from error
    entries = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(entries, list) or set(data) != {"exceptions"}:
        raise InputError(f'{path.name}: expected {{"exceptions": [...]}}')
    result: list[Exception_] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"{path.name} entry {index}"
        if not isinstance(entry, dict):
            raise InputError(f"{where}: not an object")
        keys = set(entry)
        if not keys >= REQUIRED_KEYS or not keys <= EXCEPTION_KEYS:
            raise InputError(f"{where}: keys must be {sorted(REQUIRED_KEYS)} (+ arches)")
        vuln, target, package = entry["id"], entry["target"], entry["package"]
        if not isinstance(vuln, str) or not ID_FORM.match(vuln):
            raise InputError(f"{where}: id {vuln!r} is not a CVE, GHSA or GO id")
        if not isinstance(target, str) or not NAME_FORM.match(target):
            raise InputError(f"{where}: target {target!r} is not a target name")
        if not isinstance(package, str) or not package.strip():
            raise InputError(f"{where}: package is empty")
        reason = entry["reason"]
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            raise InputError(f"{where}: reason must say why (at least 20 characters)")
        issue = entry["issue"]
        if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
            raise InputError(f"{where}: issue must be an issue number")
        approved = _date(entry["approved"], "approved", where)
        expires = _date(entry["expires"], "expires", where)
        if approved > today:
            raise InputError(f"{where}: approved {approved} lies in the future")
        if not approved < expires <= approved + dt.timedelta(days=MAX_DAYS):
            raise InputError(f"{where}: expires must be 1-{MAX_DAYS} days after approved")
        arches = entry.get("arches", list(ARCHES))
        if (
            not isinstance(arches, list)
            or not arches
            or len(set(arches)) != len(arches)
            or not set(arches) <= set(ARCHES)
        ):
            raise InputError(f"{where}: arches must be a non-empty subset of {list(ARCHES)}")
        key = (vuln, target, package)
        if key in seen:
            raise InputError(f"{where}: duplicate of an earlier entry")
        seen.add(key)
        result.append(
            Exception_(vuln, target, package, reason, issue, approved, expires, tuple(arches))
        )
    return result


def _check_source(target: Target, doc: dict[str, Any], arch: str) -> None:
    """A result must describe the image that was meant, so an empty catalogue never passes."""
    source = (doc.get("source") or {}).get("target") or {}
    if not isinstance(source, dict) or source.get("architecture") != arch:
        found = source.get("architecture") if isinstance(source, dict) else None
        raise InputError(f"{target.name}: the result is for architecture {found!r}, not {arch}")
    if target.distro and not (doc.get("distro") or {}).get("name"):
        raise InputError(f"{target.name}: Grype detected no distribution; the scan saw nothing")
    if target.kind == "pull":
        match = DIGEST_REF.match(target.source)
        digest = match["digest"] if match else ""
        repo_digests = source.get("repoDigests") or []
        if not digest or not any(str(d).endswith(f"@{digest}") for d in repo_digests):
            raise InputError(f"{target.name}: the result is not for the pinned digest")


def load_findings(target: Target, path: Path, arch: str) -> tuple[list[Finding], dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        matches = doc["matches"]
        descriptor = doc["descriptor"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InputError(f"{target.name}: unusable Grype result {path.name}: {error}") from error
    if not isinstance(matches, list):
        raise InputError(f"{target.name}: matches is not a list")
    _check_source(target, doc, arch)
    found: dict[tuple[str, str, str, str], Finding] = {}
    counts: Counter[str] = Counter()
    for match in matches:
        try:
            vuln = match["vulnerability"]
            artifact = match["artifact"]
            severity = vuln["severity"]
            fix = vuln.get("fix") or {}
            aliases = {vuln["id"]} | {r["id"] for r in match.get("relatedVulnerabilities", [])}
            finding = Finding(
                target.name,
                vuln["id"],
                frozenset(aliases),
                severity,
                artifact["name"],
                artifact["version"],
                fix.get("state") or "unknown",
                tuple(fix.get("versions") or ()),
            )
        except (KeyError, TypeError) as error:
            raise InputError(f"{target.name}: malformed match: {error}") from error
        if severity not in KNOWN_SEVERITIES:
            raise InputError(f"{target.name}: unknown severity {severity!r}")
        counts[severity] += 1
        if severity in FAIL_SEVERITIES:
            found.setdefault((finding.vuln, finding.package, finding.version, severity), finding)
    meta = {
        "grype": descriptor.get("version", "?"),
        "db": _db_date(descriptor),
        "counts": counts,
        "ignored": len(doc.get("ignoredMatches") or []),
    }
    return list(found.values()), meta


def _db_date(descriptor: dict[str, Any]) -> str:
    db = descriptor.get("db") or {}
    if isinstance(db.get("built"), str):
        return str(db["built"])
    captured = [
        p.get("captured", "") for p in (db.get("providers") or {}).values() if isinstance(p, dict)
    ]
    return max(captured, default="unknown")


def evaluate(
    results: dict[str, tuple[Target, Path]],
    exceptions: list[Exception_],
    arch: str,
    today: dt.date,
) -> tuple[int, list[str]]:
    lines: list[str] = []
    failing: list[tuple[Finding, str]] = []
    excepted: list[tuple[Finding, Exception_]] = []
    for name, (target, path) in sorted(results.items()):
        findings, meta = load_findings(target, path, arch)
        counts = ", ".join(f"{k} {v}" for k, v in sorted(meta["counts"].items())) or "none"
        ignored = f", {meta['ignored']} ignored by Grype" if meta["ignored"] else ""
        lines.append(
            f"{name} ({arch}): Grype {meta['grype']}, database {meta['db']}: {counts}{ignored}"
        )
        for finding in sorted(findings, key=lambda f: (f.severity, f.vuln, f.package)):
            covering = [e for e in exceptions if e.covers(finding, arch)]
            for e in covering:
                e.matched.append(finding)
            active = [e for e in covering if today <= e.expires]
            if active:
                excepted.append((finding, active[0]))
            else:
                failing.append((finding, "exception expired" if covering else "no exception"))
    for finding, why in failing:
        fixed = ", ".join(finding.fix_versions) or "none"
        lines.append(
            f"FAIL {finding.target}: {finding.vuln} {finding.severity} in {finding.package} "
            f"{finding.version} (fix: {finding.fix_state}, {fixed}): {why}"
        )
    for finding, e in excepted:
        lines.append(
            f"EXCEPTED {finding.target}: {finding.vuln} {finding.severity} in {finding.package} "
            f"until {e.expires} (#{e.issue})"
        )
    for e in exceptions:
        if arch not in e.arches or e.target not in results:
            continue
        label = f"exception {e.id} for {e.target}/{e.package}"
        if not e.matched:
            note = (
                "expired, remove it"
                if today > e.expires
                else f"not found on {arch}: narrow its arches, or remove it if no arch needs it"
            )
            lines.append(f"STALE {label}: {note}")
        elif today > e.expires:
            lines.append(f"EXPIRED {label} on {e.expires}")
        elif (e.expires - today).days <= 14:
            lines.append(f"DUE {label} expires {e.expires}")
    verdict = "fail" if failing else "pass"
    lines.append(
        f"image scan ({arch}): {verdict}: {len(failing)} failing, {len(excepted)} excepted"
    )
    return (1 if failing else 0), lines


def _result(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not NAME_FORM.match(name) or not path:
        raise argparse.ArgumentTypeError(f"expected NAME=PATH, got {value!r}")
    return name, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("targets", help="print the scan targets, one per line")
    ev = sub.add_parser("evaluate", help="apply the policy to Grype results")
    ev.add_argument("--arch", choices=ARCHES, required=True)
    ev.add_argument("--exceptions", type=Path, required=True)
    ev.add_argument("--result", type=_result, action="append", required=True)
    ev.add_argument("--today", type=dt.date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        targets = {t.name: t for t in load_targets()}
        if args.command == "targets":
            for t in targets.values():
                print(t.kind, t.name, t.source, t.dockerfile or "-")
            return 0
        today = args.today or dt.datetime.now(dt.UTC).date()
        given = dict(args.result)
        if len(given) != len(args.result):
            raise InputError("a target is named twice")
        if set(given) != set(targets):
            missing = sorted(set(targets) - set(given))
            extra = sorted(set(given) - set(targets))
            raise InputError(f"results do not match the targets: missing {missing}, extra {extra}")
        exceptions = load_exceptions(args.exceptions, today)
        unknown = sorted({e.target for e in exceptions} - set(targets))
        if unknown:
            raise InputError(f"exceptions name targets that are not scanned: {unknown}")
        results = {name: (targets[name], path) for name, path in given.items()}
        code, lines = evaluate(results, exceptions, args.arch, today)
    except InputError as error:
        print(f"image scan: unusable input: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
