"""Compose and allowlist lint for the egress design (docs/architecture/egress.md, L1 and L2).

    lint.py compose <rendered.json>   L1: a Compose project rendered with
                                      `docker compose -f ... --profile '*' config --format json`
    lint.py allowlists                L2: each proxy's allowlist file equals the design note's table

The rules for L1 come from policy.json (the network membership matrix and the Compose rules).
Standard library only. Exit 0 when clean, 1 with findings, 2 on a usage or input error.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path(__file__).with_name("policy.json")
DESIGN = ROOT / "docs" / "architecture" / "egress.md"
ALLOWLISTS = {
    "runtime": ROOT / "deploy" / "compose" / "egress" / "runtime.allowlist",
    "ops": ROOT / "deploy" / "compose" / "egress" / "ops.allowlist",
}

BRIDGE_NAME = "com.docker.network.bridge.name"
# Bridge options that would bypass layer 3: routed or unprotected gateway modes, masquerade changes
FORBIDDEN_DRIVER_OPTS = (
    "com.docker.network.bridge.gateway_mode_ipv4",
    "com.docker.network.bridge.enable_ip_masquerade",
)
# Capabilities that open raw or administrative network access (ALL includes both)
FORBIDDEN_CAPS = {"NET_ADMIN", "NET_RAW", "ALL"}
DNS_KEYS = ("dns", "dns_search", "dns_opt")


def _cap(name: str) -> str:
    name = name.upper()
    return name.removeprefix("CAP_")


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = (item.split("=", 1) for item in env)
        return {p[0]: p[1] if len(p) > 1 else "" for p in pairs}
    return {str(k): str(v) for k, v in env.items()}


def _service_networks(service: dict[str, Any]) -> list[str]:
    nets = service.get("networks") or {}
    return list(nets) if isinstance(nets, (dict, list)) else []


def lint_compose(rendered: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """L1: every finding in a rendered Compose project, one line each."""
    findings: list[str] = []
    matrix: dict[str, Any] = policy["networks"]
    declared: dict[str, Any] = rendered.get("networks") or {}

    for key, net in declared.items():
        net = net or {}
        where = f"network {key}"
        rule = matrix.get(key)
        if rule is None:
            findings.append(f"{where}: not in the membership matrix")
            continue
        if net.get("external"):
            findings.append(f"{where}: external networks are not allowed")
        driver = net.get("driver") or "bridge"
        if driver != "bridge":
            findings.append(f"{where}: driver {driver}; only bridge is allowed")
        opts: dict[str, Any] = net.get("driver_opts") or {}
        bridge = opts.get(BRIDGE_NAME)
        if bridge != rule["bridge"]:
            findings.append(f"{where}: bridge name {bridge!r}; the matrix says {rule['bridge']!r}")
        findings.extend(
            f"{where}: driver option {opt} is not allowed"
            for opt in FORBIDDEN_DRIVER_OPTS
            if opt in opts
        )
        if net.get("enable_ipv6") is not False:
            findings.append(f"{where}: enable_ipv6 must be false")
        if bool(net.get("internal", False)) != rule["internal"]:
            findings.append(f"{where}: internal must be {str(rule['internal']).lower()}")

    roles: set[str] = set(policy["roles"])
    label: str = policy["role_label"]
    model_store: dict[str, Any] = policy["model_store"]
    members: dict[str, set[str]] = {}

    for name, service in (rendered.get("services") or {}).items():
        where = f"service {name}"
        role = (service.get("labels") or {}).get(label)
        if role not in roles:
            findings.append(f"{where}: label {label} missing or not a known role ({role!r})")
            role = None

        mode = service.get("network_mode")
        if mode not in (None, "none"):
            findings.append(f"{where}: network_mode {mode} is not allowed")
        if service.get("privileged"):
            findings.append(f"{where}: privileged is not allowed")
        findings.extend(
            f"{where}: cap_add {cap} is not allowed"
            for cap in service.get("cap_add") or []
            if _cap(cap) in FORBIDDEN_CAPS
        )
        if not {_cap(c) for c in service.get("cap_drop") or []} & {"ALL", "NET_RAW"}:
            findings.append(
                f"{where}: cap_drop must include ALL or NET_RAW (Docker grants NET_RAW)"
            )
        findings.extend(f"{where}: {key} is not allowed" for key in DNS_KEYS if service.get(key))
        if service.get("ports") and role not in policy["published_ports_roles"]:
            findings.append(f"{where}: published ports are not allowed for this role")

        for volume in service.get("volumes") or []:
            source, target = str(volume.get("source", "")), str(volume.get("target", ""))
            if "docker.sock" in source or "docker.sock" in target:
                findings.append(f"{where}: mounts the Docker socket")
            if (
                volume.get("type") == "volume"
                and source == model_store["volume"]
                and not volume.get("read_only")
                and role not in model_store["writers"]
            ):
                findings.append(f"{where}: writable model-store mount outside a pull job")

        if mode is None:
            nets = _service_networks(service)
            if not nets:
                findings.append(f"{where}: lists no network (it would join the default network)")
            for net in nets:
                if net not in declared:
                    findings.append(f"{where}: network {net} is not declared")
                elif net in matrix and role is not None and role not in matrix[net]["roles"]:
                    findings.append(f"{where}: role {role} may not join {net}")
                if role is not None:
                    members.setdefault(net, set()).add(role)

        env = _environment(service)
        for key, value in (policy["phone_home"].get(role) or {}).items():
            if env.get(key) != value:
                findings.append(f"{where}: phone-home setting {key}={value} missing")

    for pair in policy["never_share_a_network"]:
        for net, present in sorted(members.items()):
            left, right = present & set(pair["roles"]), present & set(pair["with"])
            if left and right:
                findings.append(f"network {net}: {sorted(left)} share it with {sorted(right)}")
    return findings


def design_allowlists(text: str) -> tuple[dict[str, set[str]], list[str]]:
    """The hosts per proxy in the design note's 'Literal allowlists' table, and malformed rows."""
    lists: dict[str, set[str]] = {"runtime": set(), "ops": set()}
    problems: list[str] = []
    if "## Literal allowlists" not in text:
        return lists, ["design note: section 'Literal allowlists' not found"]
    section = text.split("## Literal allowlists", 1)[1].split("\n## ", 1)[0]
    for number, line in enumerate(section.splitlines(), 1):
        if not line.startswith("|") or set(line) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "Proxy":
            continue
        if len(cells) != 3 or cells[0] not in lists:
            problems.append(
                f"design note, allowlist table row {number}: malformed ({line.strip()[:80]})"
            )
            continue
        lists[cells[0]].update(re.findall(r"`([^`\s]+)`", cells[1]))
    return lists, problems


def file_allowlist(lines: Iterable[str]) -> set[str]:
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def lint_allowlists(design_text: str, files: dict[str, set[str]]) -> list[str]:
    """L2: each proxy's file equals the design note; no host added or dropped on one side only."""
    design, findings = design_allowlists(design_text)
    for proxy, hosts in design.items():
        have = files.get(proxy, set())
        findings.extend(
            f"{proxy} allowlist: {h} is in the design note but not in the file"
            for h in sorted(hosts - have)
        )
        findings.extend(
            f"{proxy} allowlist: {h} is in the file but not in the design note"
            for h in sorted(have - hosts)
        )
    return findings


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "compose":
        try:
            text = sys.stdin.read() if argv[2] == "-" else Path(argv[2]).read_text(encoding="utf-8")
            rendered = json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            print(f"L1: cannot read the rendered Compose project: {error}", file=sys.stderr)
            return 2
        findings = lint_compose(rendered, json.loads(POLICY.read_text(encoding="utf-8")))
        services, networks = rendered.get("services") or {}, rendered.get("networks") or {}
        prefix, summary = "L1", f"{len(services)} services, {len(networks)} networks"
    elif len(argv) == 2 and argv[1] == "allowlists":
        files = {
            p: file_allowlist(f.read_text(encoding="utf-8").splitlines())
            for p, f in ALLOWLISTS.items()
        }
        findings = lint_allowlists(DESIGN.read_text(encoding="utf-8"), files)
        prefix, summary = "L2", ", ".join(f"{p} {len(h)} hosts" for p, h in files.items())
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for finding in findings:
        print(f"{prefix} {finding}")
    print(f"{prefix}: {'clean' if not findings else f'{len(findings)} findings'} ({summary})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
