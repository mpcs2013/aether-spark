"""Fixture tests for the Compose and allowlist lint (L1, L2); each forbidden setting must fail."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

LINT = Path(__file__).resolve().parents[1] / "lint.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compose_lint", LINT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["compose_lint"] = module
    spec.loader.exec_module(module)
    return module


lint = _load()
POLICY: dict[str, Any] = json.loads(lint.POLICY.read_text(encoding="utf-8"))
# Traefik's image is bound when the stack's Compose files arrive (P3a); a stand-in until then
POLICY["role_images"]["traefik"] = "traefik:test"
PROJECT = "aetherspark"


def _net(key: str) -> dict[str, Any]:
    rule = POLICY["networks"][key]
    net: dict[str, Any] = {
        "name": f"{PROJECT}_{key}",
        "driver_opts": {lint.BRIDGE_NAME: rule["bridge"]},
        "enable_ipv6": False,
    }
    if rule["internal"]:
        net["internal"] = True
    return net


def _service(role: str, *nets: str, **extra: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "labels": {POLICY["role_label"]: role},
        "cap_drop": ["ALL"],
        "networks": {n: {} for n in nets},
    }
    env = POLICY["phone_home"].get(role)
    if env:
        service["environment"] = dict(env)
    service.update(extra)
    return service


def _valid() -> dict[str, Any]:
    """A small project that follows every rule: one service per kind of case the rules cover."""
    services = {
        "traefik": _service(
            "traefik",
            "ingress",
            "edge",
            "pki",
            image="traefik:test",
            ports=[{"target": 443, "published": "443"}],
            cap_add=["NET_BIND_SERVICE"],
        ),
        "egress-runtime": _service(
            "runtime-proxy",
            "egress-runtime",
            "out-runtime",
            image=POLICY["role_images"]["runtime-proxy"],
            build={"context": "/src/deploy/compose/egress", "dockerfile": "Dockerfile"},
            security_opt=["no-new-privileges:true"],
            volumes=[
                {
                    "type": "bind",
                    "source": "C:\\src\\runtime.conf",
                    "target": "/etc/squid/squid.conf",
                }
            ],
        ),
        "ingester": _service(
            "ingester",
            "egress-runtime",
            "ingest",
            "auth-ingest",
            "obs-ingest",
            volumes=[{"type": "bind", "source": "/etc/localtime", "target": "/etc/localtime"}],
        ),
        "vllm": _service(
            "llm-serving",
            "llm",
            "auth-llm",
            "obs-llm",
            volumes=[
                {"type": "volume", "source": "model-store", "target": "/models", "read_only": True}
            ],
        ),
        "pull": _service(
            "pull-job",
            "egress-ops",
            volumes=[{"type": "volume", "source": "model-store", "target": "/models"}],
        ),
        "grafana": _service(
            "grafana", "edge", "auth-edge", "obs-edge", cap_add=["CHOWN", "SETUID", "SETGID"]
        ),
    }
    used = sorted({n for s in services.values() for n in s["networks"]})
    return {
        "name": PROJECT,
        "services": services,
        "networks": {n: _net(n) for n in used},
        "volumes": {"model-store": {"name": f"{PROJECT}_model-store"}},
    }


def _findings(project: dict[str, Any], policy: dict[str, Any] | None = None) -> list[str]:
    result: list[str] = lint.lint_compose(project, policy or POLICY)
    return result


def test_valid_project_is_clean() -> None:
    assert _findings(_valid()) == []


Mutation = Callable[[dict[str, Any]], None]


def _svc(name: str, **changes: Any) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        project["services"][name].update(changes)

    return apply


def _netset(key: str, **changes: Any) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        project["networks"][key].update(changes)

    return apply


def _opts(key: str, **opts: str) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        project["networks"][key]["driver_opts"].update(opts)

    return apply


def _drop_key(path: tuple[str, ...]) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        node = project
        for part in path[:-1]:
            node = node[part]
        del node[path[-1]]

    return apply


def _join(service: str, net: str, declare: bool = True) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        project["services"][service]["networks"][net] = {}
        if declare and net in POLICY["networks"]:
            project["networks"].setdefault(net, _net(net))

    return apply


def _top(kind: str, key: str, item: dict[str, Any]) -> Mutation:
    def apply(project: dict[str, Any]) -> None:
        project.setdefault(kind, {})[key] = item

    return apply


def _mount(source: str, kind: str = "bind") -> Mutation:
    return _svc("grafana", volumes=[{"type": kind, "source": source, "target": "/mnt"}])


GAS = "com.docker.network.bridge.gateway_mode_ipv4"
MASQ = "com.docker.network.bridge.enable_ip_masquerade"

CASES: list[tuple[str, Mutation, str]] = [
    ("network_mode host", _svc("ingester", network_mode="host"), "network_mode host"),
    (
        "network_mode service",
        _svc("ingester", network_mode="service:egress-runtime"),
        "network_mode service:",
    ),
    (
        "network_mode container",
        _svc("ingester", network_mode="container:proxy"),
        "network_mode container:",
    ),
    ("network_mode bridge", _svc("ingester", network_mode="bridge"), "network_mode bridge"),
    ("privileged", _svc("ingester", privileged=True), "privileged"),
    ("cap_add NET_ADMIN", _svc("ingester", cap_add=["NET_ADMIN"]), "cap_add NET_ADMIN"),
    ("cap_add NET_RAW", _svc("ingester", cap_add=["NET_RAW"]), "cap_add NET_RAW"),
    ("cap_add CAP_NET_RAW", _svc("ingester", cap_add=["CAP_NET_RAW"]), "cap_add CAP_NET_RAW"),
    ("cap_add ALL", _svc("ingester", cap_add=["ALL"]), "cap_add ALL"),
    ("NET_RAW not dropped", _svc("ingester", cap_drop=["SYS_ADMIN"]), "cap_drop must include"),
    ("no cap_drop", _drop_key(("services", "ingester", "cap_drop")), "cap_drop must include"),
    ("cap_add SYS_ADMIN", _svc("ingester", cap_add=["SYS_ADMIN"]), "cap_add SYS_ADMIN"),
    ("cap_add SYS_PTRACE", _svc("ingester", cap_add=["sys_ptrace"]), "cap_add sys_ptrace"),
    ("cap_add BPF", _svc("ingester", cap_add=["BPF"]), "cap_add BPF"),
    ("pid host", _svc("ingester", pid="host"), "pid host"),
    ("pid of another service", _svc("ingester", pid="service:egress-runtime"), "pid service:"),
    ("ipc host", _svc("ingester", ipc="host"), "ipc host"),
    ("uts host", _svc("ingester", uts="host"), "uts host"),
    ("userns_mode host", _svc("ingester", userns_mode="host"), "userns_mode host"),
    ("cgroup host", _svc("ingester", cgroup="host"), "cgroup host"),
    ("seccomp off", _svc("ingester", security_opt=["seccomp=unconfined"]), "seccomp=unconfined"),
    ("apparmor off", _svc("ingester", security_opt=["apparmor:unconfined"]), "apparmor:unconfined"),
    ("selinux off", _svc("ingester", security_opt=["label=disable"]), "label=disable"),
    ("host devices", _svc("ingester", devices=[{"source": "/dev/net/tun"}]), "host devices"),
    ("device cgroup rules", _svc("ingester", device_cgroup_rules=["c 10:200 rwm"]), "host devices"),
    (
        "build network host",
        _svc("ingester", build={"context": ".", "network": "host"}),
        "build network host",
    ),
    (
        "build privileged",
        _svc("ingester", build={"context": ".", "privileged": True}),
        "build privileged",
    ),
    (
        "build entitlements",
        _svc("ingester", build={"context": ".", "entitlements": ["network.host"]}),
        "build entitlements",
    ),
    ("docker socket", _mount("/var/run/docker.sock"), "reaches"),
    ("docker socket, desktop form", _mount("//var/run/docker.sock"), "reaches"),
    ("docker named pipe", _mount("\\\\.\\pipe\\docker_engine", "npipe"), "reaches"),
    ("parent of the socket", _mount("/var/run"), "reaches /var/run"),
    ("parent of /var/run", _mount("/var"), "reaches"),
    ("host root", _mount("/"), "reaches"),
    ("host root, unnormalised", _mount("/etc/.."), "reaches"),
    ("containerd socket", _mount("/run/containerd/containerd.sock"), "reaches /run/containerd"),
    ("network namespaces", _mount("/run/netns"), "reaches /run/netns"),
    ("host proc", _mount("/proc/1/ns"), "reaches /proc"),
    (
        "host path behind a named volume",
        _top(
            "volumes",
            "sneaky",
            {"driver_opts": {"type": "none", "o": "bind", "device": "/var/run"}},
        ),
        "volumes sneaky: host path /var/run",
    ),
    ("host path as a config", _top("configs", "c", {"file": "/run/docker.sock"}), "configs c"),
    ("host path as a secret", _top("secrets", "s", {"file": "/proc/self/environ"}), "secrets s"),
    (
        "network named after another one",
        _netset("edge", name="bridge"),
        "network edge: name 'bridge'",
    ),
    (
        "proxy role claimed by another image",
        _svc("egress-runtime", image="alpine:3"),
        "role runtime-proxy runs only aetherspark/egress-proxy:local",
    ),
    (
        "proxy role without an image",
        _drop_key(("services", "egress-runtime", "image")),
        "role runtime-proxy runs only",
    ),
    (
        "published port",
        _svc("grafana", ports=[{"target": 3000, "published": "3000"}]),
        "published ports",
    ),
    ("dns", _svc("ingester", dns=["1.1.1.1"]), "dns is not allowed"),
    ("dns_search", _svc("ingester", dns_search=["example.org"]), "dns_search is not allowed"),
    ("dns_opt", _svc("ingester", dns_opt=["ndots:1"]), "dns_opt is not allowed"),
    ("undeclared network", _join("ingester", "mcp", declare=False), "network mcp is not declared"),
    ("network outside the matrix", _join("grafana", "extra"), "not declared"),
    (
        "declared network outside the matrix",
        lambda p: p["networks"].update(extra=_net("edge")),
        "network extra: not in the membership matrix",
    ),
    (
        "missing bridge name",
        _drop_key(("networks", "edge", "driver_opts", lint.BRIDGE_NAME)),
        "bridge name None",
    ),
    ("wrong bridge name", _opts("edge", **{lint.BRIDGE_NAME: "br-edge"}), "bridge name 'br-edge'"),
    ("macvlan", _netset("edge", driver="macvlan"), "driver macvlan"),
    ("ipvlan", _netset("edge", driver="ipvlan"), "driver ipvlan"),
    ("gateway mode", _opts("out-runtime", **{GAS: "routed"}), GAS),
    ("masquerade option", _opts("out-runtime", **{MASQ: "false"}), MASQ),
    ("enable_ipv6 true", _netset("edge", enable_ipv6=True), "enable_ipv6 must be false"),
    (
        "enable_ipv6 unset",
        _drop_key(("networks", "edge", "enable_ipv6")),
        "enable_ipv6 must be false",
    ),
    (
        "internal network not internal",
        _netset("egress-runtime", internal=False),
        "network egress-runtime: internal must be true",
    ),
    (
        "outbound network internal",
        _netset("out-runtime", internal=True),
        "network out-runtime: internal must be false",
    ),
    ("external network", _netset("edge", external=True), "external networks"),
    (
        "role on a network outside its row",
        _join("ingester", "llm"),
        "role ingester may not join llm",
    ),
    (
        "a second container on out-*",
        _join("ingester", "out-runtime"),
        "role ingester may not join out-runtime",
    ),
    (
        "serving on a proxy network",
        _join("vllm", "egress-ops"),
        "role llm-serving may not join egress-ops",
    ),
    ("no role label", _svc("grafana", labels={}), "label aetherspark.role missing"),
    (
        "unknown role",
        _svc("grafana", labels={"aetherspark.role": "dashboard"}),
        "not a known role ('dashboard')",
    ),
    (
        "writable model store",
        _svc("vllm", volumes=[{"type": "volume", "source": "model-store", "target": "/models"}]),
        "writable model-store",
    ),
    (
        "phone-home setting missing",
        _svc("traefik", environment={}),
        "TRAEFIK_GLOBAL_CHECKNEWVERSION=false missing",
    ),
    (
        "phone-home setting wrong",
        _svc("vllm", environment={**POLICY["phone_home"]["llm-serving"], "HF_HUB_OFFLINE": "0"}),
        "HF_HUB_OFFLINE=1 missing",
    ),
    ("no network listed", _svc("grafana", networks={}), "lists no network"),
]


@pytest.mark.parametrize(
    ("mutate", "expected"), [(m, e) for _, m, e in CASES], ids=[c[0] for c in CASES]
)
def test_each_forbidden_setting_fails(mutate: Mutation, expected: str) -> None:
    project = _valid()
    mutate(project)
    findings = _findings(project)
    assert any(expected in f for f in findings), findings


def test_pair_rule_holds_even_if_the_matrix_is_widened() -> None:
    """The ingester never shares a network with LLM serving, even after a matrix edit allows it."""
    policy = copy.deepcopy(POLICY)
    policy["networks"]["llm"]["roles"].append("ingester")
    project = _valid()
    _join("ingester", "llm")(project)
    findings = _findings(project, policy)
    assert findings == ["network llm: ['ingester'] share it with ['llm-serving']"]


def test_role_with_a_way_out_needs_an_image_binding() -> None:
    """A role on a non-internal network that policy.json binds no image to is refused."""
    policy = copy.deepcopy(POLICY)
    del policy["role_images"]["traefik"]
    assert _findings(_valid(), policy) == [
        "service traefik: role traefik joins ingress, which has a way out;"
        " policy.json binds no image to the role"
    ]


def test_ordinary_mounts_pass() -> None:
    forbidden = POLICY["host_paths_forbidden"]
    for source in ("/etc/localtime", "/srv/data", "/variable", "/runner", "./conf", "C:\\src\\x"):
        assert lint.host_path_hit(source, forbidden) is None, source


def test_environment_as_list() -> None:
    project = _valid()
    env = POLICY["phone_home"]["traefik"]
    project["services"]["traefik"]["environment"] = [f"{k}={v}" for k, v in env.items()]
    assert _findings(project) == []


# --- L2 ------------------------------------------------------------------------------------------

TABLE = """\
## Literal allowlists
| Proxy | Host | Reason |
|---|---|---|
| runtime | `www.sec.gov` | filings |
| runtime | price host, *to confirm* | prices |
| ops | `github.com`; release host, *to confirm* | releases |
| ops | `pypi.org`, `files.pythonhosted.org` | packages |

## Builds
"""
FILES = {"runtime": {"www.sec.gov"}, "ops": {"github.com", "pypi.org", "files.pythonhosted.org"}}


def test_l2_table_and_files_agree() -> None:
    assert lint.lint_allowlists(TABLE, FILES) == []


def test_l2_host_added_to_a_file_fails() -> None:
    files = {**FILES, "runtime": {"www.sec.gov", "example.com"}}
    assert lint.lint_allowlists(TABLE, files) == [
        "runtime allowlist: example.com is in the file but not in the design note"
    ]


def test_l2_host_dropped_from_a_file_fails() -> None:
    files = {**FILES, "ops": {"github.com", "pypi.org"}}
    assert lint.lint_allowlists(TABLE, files) == [
        "ops allowlist: files.pythonhosted.org is in the design note but not in the file"
    ]


def test_l2_malformed_row_fails() -> None:
    merged = TABLE.replace("| releases |\n| ops |", "| releases || ops |")
    findings = lint.lint_allowlists(merged, FILES)
    assert any("malformed" in f for f in findings), findings


def test_l2_missing_section_fails() -> None:
    assert lint.lint_allowlists("# nothing here\n", FILES)[0].startswith("design note: section")


def test_l2_comments_and_blank_lines_are_ignored() -> None:
    assert lint.file_allowlist(["# note", "", "  www.sec.gov  "]) == {"www.sec.gov"}


def test_l2_real_files_match_the_design_note() -> None:
    files = {
        p: lint.file_allowlist(f.read_text(encoding="utf-8").splitlines())
        for p, f in lint.ALLOWLISTS.items()
    }
    assert lint.lint_allowlists(lint.DESIGN.read_text(encoding="utf-8"), files) == []


def test_main_exit_codes(tmp_path: Path) -> None:
    """main() reads the real policy.json, which binds no Traefik image yet: Traefik is left out."""
    clean = tmp_path / "clean.json"
    project = _valid()
    del project["services"]["traefik"]
    clean.write_text(json.dumps(project), encoding="utf-8")
    dirty = tmp_path / "dirty.json"
    project["services"]["ingester"]["privileged"] = True
    dirty.write_text(json.dumps(project), encoding="utf-8")
    assert lint.main(["lint.py", "compose", str(clean)]) == 0
    assert lint.main(["lint.py", "compose", str(dirty)]) == 1
    assert lint.main(["lint.py", "compose", str(tmp_path / "missing.json")]) == 2
    assert lint.main(["lint.py"]) == 2
