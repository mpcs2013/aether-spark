"""Offline tests for the image scan policy (ADR-0005 amendment 3, #31)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

POLICY = Path(__file__).resolve().parents[1] / "scan_policy.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_policy", POLICY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_policy"] = module
    spec.loader.exec_module(module)
    return module


policy = _load()
TODAY = dt.date(2026, 11, 1)
DIGEST = "sha256:" + "a" * 64
APP = policy.Target("build", "app", "app", "app/Dockerfile")


def _match(
    vuln: str = "CVE-2026-1000",
    severity: str = "High",
    package: str = "libfoo",
    state: str = "fixed",
    related: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "vulnerability": {
            "id": vuln,
            "severity": severity,
            "fix": {"state": state, "versions": ["2.0"] if state == "fixed" else []},
        },
        "relatedVulnerabilities": [{"id": r} for r in related],
        "artifact": {"name": package, "version": "1.0"},
    }


def _doc(
    *matches: dict[str, Any],
    arch: str = "amd64",
    distro: str = "ubuntu",
    repo_digests: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "matches": list(matches),
        "source": {
            "type": "image",
            "target": {"architecture": arch, "repoDigests": repo_digests or []},
        },
        "distro": {"name": distro, "version": "24.04"},
        "descriptor": {"version": "0.121.0", "db": {"built": "2026-11-01T00:00:00Z"}},
    }


def _write(tmp_path: Path, name: str, doc: dict[str, Any]) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _exception(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "CVE-2026-1000",
        "target": "app",
        "package": "libfoo",
        "reason": "Not reachable: the image never parses untrusted input with it.",
        "issue": 31,
        "approved": "2026-10-20",
        "expires": "2026-12-01",
    }
    entry.update(overrides)
    return entry


def _exceptions(tmp_path: Path, *entries: dict[str, Any]) -> list[Any]:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": list(entries)}), encoding="utf-8")
    result: list[Any] = policy.load_exceptions(path, TODAY)
    return result


def _evaluate(
    tmp_path: Path, matches: list[dict[str, Any]], *entries: dict[str, Any], arch: str = "amd64"
) -> tuple[int, list[str]]:
    results = {"app": (APP, _write(tmp_path, "app", _doc(*matches, arch=arch)))}
    code, lines = policy.evaluate(results, _exceptions(tmp_path, *entries), arch, TODAY)
    return int(code), list(lines)


# The fail rule: High and Critical fail whether or not a fix exists


@pytest.mark.parametrize("state", ["fixed", "not-fixed", "wont-fix", "unknown", ""])
@pytest.mark.parametrize("severity", ["High", "Critical"])
def test_high_and_critical_fail_whatever_the_fix_state(
    tmp_path: Path, severity: str, state: str
) -> None:
    code, lines = _evaluate(tmp_path, [_match(severity=severity, state=state)])
    assert code == 1
    assert any(line.startswith("FAIL app: CVE-2026-1000") for line in lines)


@pytest.mark.parametrize("severity", ["Medium", "Low", "Negligible", "Unknown"])
def test_lower_severities_are_counted_not_failed(tmp_path: Path, severity: str) -> None:
    code, lines = _evaluate(tmp_path, [_match(severity=severity)])
    assert code == 0
    assert f"{severity} 1" in lines[0]


@pytest.mark.parametrize("severity", ["high", "CRITICAL", "Severe", ""])
def test_an_unknown_severity_is_refused(tmp_path: Path, severity: str) -> None:
    with pytest.raises(policy.InputError, match="unknown severity"):
        _evaluate(tmp_path, [_match(severity=severity)])


def test_no_findings_pass(tmp_path: Path) -> None:
    code, lines = _evaluate(tmp_path, [])
    assert code == 0
    assert lines[-1] == "image scan (amd64): pass: 0 failing, 0 excepted"


def test_duplicate_matches_count_once(tmp_path: Path) -> None:
    code, lines = _evaluate(tmp_path, [_match(), _match()])
    assert code == 1
    assert sum(line.startswith("FAIL") for line in lines) == 1


def test_matches_grype_ignored_are_reported(tmp_path: Path) -> None:
    doc = _doc()
    doc["ignoredMatches"] = [_match()]
    results = {"app": (APP, _write(tmp_path, "app", doc))}
    _, lines = policy.evaluate(results, [], "amd64", TODAY)
    assert "1 ignored by Grype" in lines[0]


# Exceptions


def test_an_exception_covers_its_finding(tmp_path: Path) -> None:
    code, lines = _evaluate(tmp_path, [_match()], _exception())
    assert code == 0
    assert any(line.startswith("EXCEPTED app: CVE-2026-1000") for line in lines)


def test_an_exception_matches_a_related_id(tmp_path: Path) -> None:
    finding = _match(vuln="GHSA-2222-3333-4444", related=("CVE-2026-1000",))
    code, _ = _evaluate(tmp_path, [finding], _exception())
    assert code == 0


@pytest.mark.parametrize(
    "overrides",
    [{"target": "other"}, {"package": "libbar"}, {"id": "CVE-2026-9999"}, {"arches": ["arm64"]}],
)
def test_an_exception_covers_only_its_own_finding(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    code, _ = _evaluate(tmp_path, [_match()], _exception(**overrides))
    assert code == 1


def test_an_expired_exception_fails_again(tmp_path: Path) -> None:
    entry = _exception(approved="2026-09-01", expires="2026-10-31")
    code, lines = _evaluate(tmp_path, [_match()], entry)
    assert code == 1
    assert any(line.endswith("exception expired") for line in lines)
    assert any(line.startswith("EXPIRED exception CVE-2026-1000") for line in lines)


def test_the_expiry_day_is_still_covered(tmp_path: Path) -> None:
    code, _ = _evaluate(tmp_path, [_match()], _exception(expires="2026-11-01"))
    assert code == 0


def test_an_unused_exception_names_the_architecture(tmp_path: Path) -> None:
    code, lines = _evaluate(tmp_path, [], _exception(), arch="arm64")
    assert code == 0
    assert any(
        line.startswith("STALE exception CVE-2026-1000") and "not found on arm64" in line
        for line in lines
    )


def test_an_exception_due_within_two_weeks_is_reported(tmp_path: Path) -> None:
    _, lines = _evaluate(tmp_path, [_match()], _exception(expires="2026-11-10"))
    assert any(line.startswith("DUE exception") for line in lines)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "CVE-26-1"}, "not a CVE"),
        ({"id": "GHSA-0000-0000-0000"}, "not a CVE"),
        ({"target": "App Image"}, "not a target name"),
        ({"package": " "}, "package is empty"),
        ({"reason": "accepted"}, "must say why"),
        ({"issue": 0}, "issue number"),
        ({"issue": True}, "issue number"),
        ({"issue": "31"}, "issue number"),
        ({"approved": "2026-11-02"}, "in the future"),
        ({"approved": "1 Nov"}, "not a date"),
        ({"expires": "2026-10-20"}, "1-90 days"),
        ({"approved": "2026-10-01", "expires": "2026-12-31"}, "1-90 days"),
        ({"arches": []}, "arches"),
        ({"arches": ["riscv64"]}, "arches"),
        ({"arches": ["amd64", "amd64"]}, "arches"),
        ({"note": "extra"}, "keys must be"),
    ],
)
def test_unusable_exceptions_are_refused(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(policy.InputError, match=message):
        _exceptions(tmp_path, _exception(**overrides))


def test_a_missing_key_is_refused(tmp_path: Path) -> None:
    entry = _exception()
    del entry["expires"]
    with pytest.raises(policy.InputError, match="keys must be"):
        _exceptions(tmp_path, entry)


def test_ninety_days_is_the_longest_exception(tmp_path: Path) -> None:
    assert _exceptions(tmp_path, _exception(approved="2026-10-01", expires="2026-12-30"))


def test_a_duplicate_exception_is_refused(tmp_path: Path) -> None:
    with pytest.raises(policy.InputError, match="duplicate"):
        _exceptions(tmp_path, _exception(), _exception())


@pytest.mark.parametrize("text", ["[]", '{"exceptions": {}}', '{"exceptions": [], "x": 1}', "{"])
def test_an_unusable_exceptions_file_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "exceptions.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(policy.InputError):
        policy.load_exceptions(path, TODAY)


def test_the_repositorys_exceptions_file_is_valid() -> None:
    entries = policy.load_exceptions(policy.HERE / "exceptions.json", TODAY)
    targets = {t.name for t in policy.load_targets()}
    assert {e.target for e in entries} <= targets


# Results: a result must describe the image that was meant


@pytest.mark.parametrize("text", ["{", "{}", '{"matches": {}, "descriptor": {}}'])
def test_an_unusable_result_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "app.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(policy.InputError):
        policy.load_findings(APP, path, "amd64")


def test_a_malformed_match_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "app", _doc({"vulnerability": {"id": "CVE-2026-1000"}}))
    with pytest.raises(policy.InputError, match="malformed"):
        policy.load_findings(APP, path, "amd64")


def test_a_result_for_the_other_architecture_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "app", _doc(arch="arm64"))
    with pytest.raises(policy.InputError, match="architecture 'arm64', not amd64"):
        policy.load_findings(APP, path, "amd64")


def test_a_result_without_a_distribution_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "app", _doc(distro=""))
    with pytest.raises(policy.InputError, match="no distribution"):
        policy.load_findings(APP, path, "amd64")


def test_a_target_without_a_distribution_may_opt_out(tmp_path: Path) -> None:
    scratch = policy.Target("build", "app", "app", "app/Dockerfile", distro=False)
    path = _write(tmp_path, "app", _doc(distro=""))
    assert policy.load_findings(scratch, path, "amd64")[0] == []


def test_a_pulled_image_must_be_the_pinned_digest(tmp_path: Path) -> None:
    pulled = policy.Target("pull", "busybox", f"busybox:1.37.0@{DIGEST}")
    good = _write(tmp_path, "good", _doc(repo_digests=[f"busybox@{DIGEST}"]))
    other = _write(tmp_path, "other", _doc(repo_digests=["busybox@sha256:" + "b" * 64]))
    assert policy.load_findings(pulled, good, "amd64")[0] == []
    with pytest.raises(policy.InputError, match="pinned digest"):
        policy.load_findings(pulled, other, "amd64")


def test_the_database_date_comes_from_the_providers() -> None:
    providers = {
        "a": {"captured": "2026-10-30T01:00:00Z"},
        "b": {"captured": "2026-10-31T02:00:00Z"},
    }
    assert policy._db_date({"db": {"providers": providers}}) == "2026-10-31T02:00:00Z"


# Targets: every Dockerfile and every Compose image, or the scan does not start


def _repo(tmp_path: Path, compose: str, extra: tuple[str, ...] = ()) -> tuple[Path, list[str]]:
    files = ["app/Dockerfile", "tools/lab/Dockerfile", "tests/compose.test.yaml", *extra]
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(compose if name.endswith(".yaml") else "", encoding="utf-8")
    return tmp_path, files


BUILD = [{"name": "app", "context": "app"}, {"name": "lab", "context": "tools/lab"}]


def _targets(repo: tuple[Path, list[str]], **config: Any) -> list[Any]:
    root, files = repo
    path = root / "targets.json"
    path.write_text(json.dumps({"build": BUILD, **config}), encoding="utf-8")
    result: list[Any] = policy.load_targets(path, root, files)
    return result


def test_targets_are_the_builds_and_the_pinned_compose_images(tmp_path: Path) -> None:
    compose = (
        f"services:\n  a:\n    image: curlimages/curl:8.23.0@{DIGEST}\n"
        f'  b: {{ image: "busybox:1.37.0@{DIGEST}", read_only: true }}\n'
        f"  c:\n    build: .\n    image: local/app:dev\n"
        f"  d:\n    'image': curlimages/curl:8.23.0@{DIGEST}\n"
    )
    targets = _targets(_repo(tmp_path, compose), local_images=["local/app:dev"])
    assert [(t.kind, t.name) for t in targets] == [
        ("build", "app"),
        ("build", "lab"),
        ("pull", "busybox"),
        ("pull", "curlimages/curl"),
    ]
    assert targets[3].source == f"curlimages/curl:8.23.0@{DIGEST}"


@pytest.mark.parametrize(
    "line",
    [
        "    image: curlimages/curl:8.24.0",
        "  x: { image: busybox }",
        "    image: ${IMAGE}",
        f"    image: ${{REGISTRY}}/curl@{DIGEST}",
        "    image: local/app:dev",
    ],
)
def test_a_compose_image_without_a_digest_is_refused(tmp_path: Path, line: str) -> None:
    with pytest.raises(policy.InputError, match="not pinned by digest"):
        _targets(_repo(tmp_path, f"services:\n  a:\n{line}\n"))


@pytest.mark.parametrize(
    "extra",
    ["tools/Dockerfile.dev", "x/Containerfile", "y/build.Dockerfile", "deploy/bin/Dockerfile"],
)
def test_a_dockerfile_missing_from_the_targets_is_refused(tmp_path: Path, extra: str) -> None:
    with pytest.raises(policy.InputError, match="differ from the repository's Dockerfiles"):
        _targets(_repo(tmp_path, "", (extra,)))


def test_an_excluded_dockerfile_needs_a_reason(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "", ("deploy/images/grype.Dockerfile",))
    reason = "the scanner's own pin, read by scan.sh"
    assert _targets(repo, exclude={"deploy/images/grype.Dockerfile": reason})
    with pytest.raises(policy.InputError, match="reason"):
        _targets(repo, exclude={"deploy/images/grype.Dockerfile": "pin"})


def test_compose_files_anywhere_are_read(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "", ("docker-compose.yml",))
    (tmp_path / "docker-compose.yml").write_text("services:\n  a:\n    image: nginx\n")
    with pytest.raises(policy.InputError, match="not pinned by digest"):
        _targets(repo)


def test_an_image_pinned_twice_is_refused(tmp_path: Path) -> None:
    other = "sha256:" + "b" * 64
    compose = f"  image: busybox:1.37.0@{DIGEST}\n  image: busybox:1.36.1@{other}\n"
    with pytest.raises(policy.InputError, match="pinned to two references"):
        _targets(_repo(tmp_path, compose))


@pytest.mark.parametrize(
    ("ref", "name"),
    [
        (f"busybox:1.37.0@{DIGEST}", "busybox"),
        (f"ghcr.io/anchore/grype:v0.121.0@{DIGEST}", "ghcr.io/anchore/grype"),
        (f"registry.local:5000/team/app@{DIGEST}", "registry.local:5000/team/app"),
    ],
)
def test_image_names_drop_tag_and_digest(ref: str, name: str) -> None:
    assert policy.image_name(ref) == name


def test_the_repositorys_targets_cover_every_dockerfile() -> None:
    names = {t.name for t in policy.load_targets()}
    assert {"dev-container", "egress-proxy", "egress-lab", "host-role-test"} <= names


def test_cis_image_filter_covers_every_build_context() -> None:
    ci = (policy.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    line = next(x for x in ci.splitlines() if x.strip().startswith("images=$all;"))
    pattern = re.search(r"any '([^']+)'", line)
    assert pattern is not None
    for target in policy.load_targets():
        if target.kind == "build":
            assert re.search(pattern[1], f"{target.source}/entrypoint.sh"), target.source
    assert re.search(pattern[1], "deploy/images/exceptions.json")


def test_scan_sh_reads_a_digest_pinned_grype() -> None:
    text = (policy.HERE / "grype.Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM ghcr\.io/anchore/grype:v[0-9.]+@sha256:[0-9a-f]{64}$", text, re.M)


# Command line


def test_evaluate_refuses_results_that_do_not_match_the_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, "app", _doc())
    exceptions = str(policy.HERE / "exceptions.json")
    code = policy.main(
        ["evaluate", "--arch", "amd64", "--exceptions", exceptions, "--result", f"app={path}"]
    )
    assert code == 2
    assert "do not match the targets" in capsys.readouterr().err


def test_evaluate_passes_on_clean_results_for_every_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text('{"exceptions": []}', encoding="utf-8")
    args = ["evaluate", "--arch", "arm64", "--exceptions", str(exceptions), "--today", "2026-11-01"]
    for i, target in enumerate(policy.load_targets()):
        digests = [f"x@{target.source.split('@')[-1]}"] if target.kind == "pull" else []
        doc = _doc(arch="arm64", repo_digests=digests)
        args += ["--result", f"{target.name}={_write(tmp_path, f't{i}', doc)}"]
    assert policy.main(args) == 0
    assert capsys.readouterr().out.endswith("image scan (arm64): pass: 0 failing, 0 excepted\n")
