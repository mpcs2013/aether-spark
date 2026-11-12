"""Tests for the CA tool (compose-base.md, "Certificates"). What the CA's files say is checked
here without Docker; issuance, renewal and the clients' checks run in tests/stack/run.sh."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ca.py"
ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose" / "stack" / "compose.yaml"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ca_tool", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ca_tool"] = module
    spec.loader.exec_module(module)
    return module


ca = _load()
KEYS = {"postgres-auth": {"key": {"kty": "EC", "kid": "k1"}, "encryptedKey": "eyJ.x.y.z.w"}}


def _template_json(template: str) -> dict[str, Any]:
    """A CA template with its Go expressions replaced, as JSON."""
    result: dict[str, Any] = json.loads(template.replace("{{ toJson .Subject }}", "{}"))
    return result


@pytest.mark.parametrize("where", ["localhost", "home.arpa"])
def test_root_and_intermediate_are_constrained_to_the_place(where: str) -> None:
    root = _template_json(ca.ca_template(where, 1, self_signed=True))
    inter = _template_json(ca.ca_template(where, 0, self_signed=False))
    for cert in (root, inter):
        assert cert["nameConstraints"] == {
            "critical": True,
            "permittedDNSDomains": [f"aether.{where}"],
        }
        assert cert["keyUsage"] == ["certSign", "crlSign"]
    assert root["basicConstraints"] == {"isCA": True, "maxPathLen": 1}
    assert inter["basicConstraints"] == {"isCA": True, "maxPathLen": 0}
    assert "issuer" in root and "issuer" not in inter


def test_a_jwk_provisioner_issues_only_its_services_name() -> None:
    config = ca.ca_config("localhost", KEYS, "24h")
    jwk = [p for p in config["authority"]["provisioners"] if p["type"] == "JWK"]
    assert [p["name"] for p in jwk] == ["postgres-auth"]
    template = json.loads(jwk[0]["options"]["x509"]["template"])
    name = "postgres-auth.svc.aether.localhost"
    assert template["subject"] == {"commonName": name}
    assert template["sans"] == [{"type": "dns", "value": name}]
    assert "{{" not in jwk[0]["options"]["x509"]["template"]  # nothing taken from the request


def test_policy_allows_only_the_places_names() -> None:
    config = ca.ca_config("home.arpa", KEYS, "24h")
    assert config["authority"]["policy"] == {
        "x509": {"allow": {"dns": ["*.aether.home.arpa", "*.svc.aether.home.arpa"]}}
    }
    acme = config["authority"]["provisioners"][0]
    assert acme["type"] == "ACME" and acme["name"] == "traefik"
    assert acme["challenges"] == ["tls-alpn-01"]
    assert acme["policy"] == {"x509": {"allow": {"dns": ["*.aether.home.arpa"]}}}
    assert config["dnsNames"] == ["step-ca.svc.aether.home.arpa"]


def test_lifetimes_and_renewal_rules() -> None:
    for p in ca.ca_config("localhost", KEYS, "10m")["authority"]["provisioners"]:
        claims = p["claims"]
        assert claims["defaultTLSCertDuration"] == claims["maxTLSCertDuration"] == "10m"
        assert claims["allowRenewalAfterExpiry"] is False


def test_no_password_and_no_root_key_in_the_configuration() -> None:
    text = json.dumps(ca.ca_config("localhost", KEYS, "24h"))
    assert "password" not in text.lower()
    assert "root_ca.key" not in text and "root.key" not in text
    config = json.loads(text)
    assert config["key"] == "/ca/secrets/intermediate_ca_key"
    assert config["root"] == "/ca/certs/root_ca.crt"


def test_kept_keys_keeps_wanted_jwk_keys_only() -> None:
    current = ca.ca_config(
        "localhost", {**KEYS, "old": {"key": {"kid": "o"}, "encryptedKey": "e"}}, "24h"
    )
    assert ca.kept_keys(current, ["postgres-auth"]) == KEYS
    assert ca.kept_keys(current, ["keycloak"]) == {}
    assert ca.kept_keys(None, ["postgres-auth"]) == {}


def test_provisioners_follow_the_manifest() -> None:
    sys.path.insert(0, str(ROOT / "deploy" / "secret-store"))
    import secret_store

    manifest = secret_store.load_manifest()
    services = ca.jwk_services(manifest)
    assert services == ["postgres-auth", "keycloak", "forward-auth", "whoami"]
    compose = COMPOSE.read_text(encoding="utf-8")
    # The bootstrap step serves exactly these, each with its own volume and secret
    assert re.search(r"^\s+SERVICES: (.+)$", compose, re.M)[1].split() == services  # type: ignore[index]
    provisioner_secrets = ", ".join(f"step-ca-provisioner-{s}" for s in services)
    assert f"secrets: [{provisioner_secrets}]" in compose
    for service in services:
        assert f"  {service}-tls: {{}}" in compose
        assert f"- {service}-tls:/tls/{service}" in compose


def test_the_step_image_is_built_here_from_pinned_inputs() -> None:
    dockerfile = (COMPOSE.parent / "step-ca" / "Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"^FROM (\S+)", dockerfile, re.M)
    assert len(froms) == 2
    assert all(re.fullmatch(r"[a-z0-9./-]+:[\w.-]+@sha256:[0-9a-f]{64}", f) for f in froms)
    for name in ("STEP_CA", "STEP_CLI"):
        assert re.search(rf"^ARG {name}_VERSION=[0-9.]+$", dockerfile, re.M)
        assert re.search(rf"^ARG {name}_SHA256=[0-9a-f]{{64}}$", dockerfile, re.M)
    assert "smallstep/step-ca" not in dockerfile  # the upstream image is not used at all
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "build: ./step-ca" in compose
    assert f"image: {ca.IMAGE}" in compose  # the tool runs the very image Compose builds


def test_place_and_lifetimes_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERSPARK_PLACE", "example.com")
    with pytest.raises(ca.InputError):
        ca.place()
    monkeypatch.setenv("AETHERSPARK_PLACE", "home.arpa")
    assert ca.place() == "home.arpa"
    monkeypatch.setenv("AETHERSPARK_CA_LEAF_LIFETIME", "1d")
    with pytest.raises(ca.InputError):
        ca.lifetime("AETHERSPARK_CA_LEAF_LIFETIME", "24h")
    monkeypatch.setenv("AETHERSPARK_CA_LEAF_LIFETIME", "10m")
    assert ca.lifetime("AETHERSPARK_CA_LEAF_LIFETIME", "24h") == "10m"


def test_the_stack_user_is_never_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERSPARK_UID", "0")
    monkeypatch.setenv("AETHERSPARK_GID", "0")
    with pytest.raises(ca.InputError):
        ca.stack_user()
    monkeypatch.setenv("AETHERSPARK_UID", "1000")
    monkeypatch.setenv("AETHERSPARK_GID", "1000")
    assert ca.stack_user() == (1000, 1000)


def test_days_left() -> None:
    now = datetime(2026, 11, 6, tzinfo=UTC)
    assert ca.days_left("2027-11-06T00:00:00Z", now) == 365
    assert ca.days_left("2026-12-01T12:00:00Z", now) == 25


def test_volume_names_follow_the_compose_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    assert ca.volume_name("step-ca") == "aetherspark_step-ca"
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "as-test")
    assert ca.volume_name("step-ca") == "as-test_step-ca"


def test_volumes_only_makes_the_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ca.py volumes` once fell through to renew-intermediate; a restore calls it before the CA's
    files are back (backup.sh restore, found by the drill on 2026-11-12)."""
    made: list[list[str]] = []
    monkeypatch.setattr(ca, "own_volumes", lambda volumes: made.append(list(volumes)))

    def must_not_run(*_: object) -> None:
        raise AssertionError("volumes must not touch the CA")

    for name in ("renew_intermediate", "init", "configure", "_restart"):
        monkeypatch.setattr(ca, name, must_not_run)
    assert ca.main(["volumes"]) == 0
    assert made and "step-ca" in made[0]
