"""Keycloak's administrators' realm (compose-base.md, "Recovery and lock-out"; PR 5b). Standard
library; runs where `just up` runs, with the stack up.

    python3 -I deploy/keycloak/admin.py setup USERNAME [--password-file FILE]

`setup` hardens the `master` realm and creates the owner's permanent administrator, in one run:
  0. the realm's own address (Frontend URL) becomes the admin name: Keycloak's `hostname-admin`
     moves the console only, so without it the administrators' login pages would post to the
     sign-in name, where Traefik refuses /realms/master/ (measured 2026-11-08);
  1. a login flow that requires the password and then a security key (WebAuthn) for every
     administrator; a new administrator registers the key at the first login;
  2. brute-force detection, login and admin events, TLS everywhere, a password policy;
  3. the administrator USERNAME, with the realm's `admin` role, a temporary password (asked for, or
     read from FILE) that must be changed at the first login, and the key to register;
  4. last, no client of the realm may sign in with a password alone (direct grants), so this tool
     itself cannot sign in again: from then on the console, with the security key, is the way in.

It signs in as Keycloak's temporary first-start administrator, whose password it reads from the
in-memory secrets folder, and talks only to Traefik's admin route (auth-admin.aether.<place>),
checking its certificate against AetherSpark's root. No value is printed or passed in an argument.
After the run the owner signs in with the new account and deletes `bootstrap-admin` in the console
(docs/runbooks/keycloak-admin.md).

Exit codes: 0 done, 1 refused (already set up, the user exists, a check failed), 2 unusable input.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import ssl
import sys
import urllib.parse
from dataclasses import dataclass
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy" / "ca"))
sys.path.insert(0, str(ROOT / "deploy" / "secret-store"))
import ca  # noqa: E402
import secret_store  # noqa: E402

REALM = "master"
BOOTSTRAP_USER = "bootstrap-admin"  # KC_BOOTSTRAP_ADMIN_USERNAME in the stack's Compose file
BOOTSTRAP_SECRET = "keycloak-bootstrap-admin-password"  # noqa: S105 - a secret's name
FLOW = "browser-security-key"
FORMS = f"{FLOW} forms"
MIN_PASSWORD = 14  # the realm's password policy below
REALM_SETTINGS: dict[str, Any] = {
    "browserFlow": FLOW,
    "sslRequired": "all",
    "bruteForceProtected": True,
    "permanentLockout": False,
    "failureFactor": 5,
    "waitIncrementSeconds": 60,
    "minimumQuickLoginWaitSeconds": 60,
    "quickLoginCheckMilliSeconds": 1000,
    "maxFailureWaitSeconds": 900,
    "maxDeltaTimeSeconds": 43200,
    "passwordPolicy": f"length({MIN_PASSWORD}) and notUsername and passwordHistory(3)",
    "eventsEnabled": True,
    "eventsExpiration": 2592000,
    "adminEventsEnabled": True,
    "adminEventsDetailsEnabled": False,
    "webAuthnPolicyRpEntityName": "AetherSpark administration",
}
# The flow: the session cookie, or (the form, then the security key)
STEPS = (
    (FLOW, "auth-cookie", "ALTERNATIVE"),
    (FLOW, FORMS, "ALTERNATIVE"),
    (FORMS, "auth-username-password-form", "REQUIRED"),
    (FORMS, "webauthn-authenticator", "REQUIRED"),
)


class InputError(Exception):
    """Unusable input or a missing piece; nothing more is changed."""


class Refused(Exception):
    """The realm is not in a state this tool may change, or a check after a change failed."""


@dataclass
class Admin:
    """The admin route, reached on the address Traefik publishes, under its public name."""

    host: str
    address: str
    context: ssl.SSLContext
    token: str = ""

    def call(self, method: str, path: str, body: Any = None, form: bool = False) -> tuple[int, Any]:
        connection = _Connection(self.host, self.address, self.context)
        headers = {"Host": self.host, "Accept": "application/json"}
        data = None
        if form:
            data = urllib.parse.urlencode(body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        connection.close()
        try:
            parsed: Any = json.loads(text) if text.strip() else None
        except ValueError:
            parsed = text
        return response.status, parsed

    def need(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,)) -> Any:
        status, parsed = self.call(method, path, body)
        if status not in ok:
            raise Refused(f"{method} {path}: HTTP {status} {_short(parsed)}")
        return parsed


class _Connection(HTTPSConnection):
    """Connects to `address` but speaks TLS for `host` (SNI and name check), like curl --resolve."""

    def __init__(self, host: str, address: str, context: ssl.SSLContext) -> None:
        super().__init__(host, 443, context=context, timeout=15)
        self._address = address
        self._tls = context

    def connect(self) -> None:
        raw = socket.create_connection((self._address, 443), timeout=15)
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)


def _short(value: Any) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text[:200]


def admin_route() -> Admin:
    where = ca.place()
    root = ca.run_in_ca("cat /ca/certs/root_ca.crt").decode("ascii")
    context = ssl.create_default_context(cadata=root)  # AetherSpark's root only
    address = os.environ.get("AETHERSPARK_PUBLISH_ADDRESS", "127.0.0.1")
    return Admin(f"auth-admin.aether.{where}", address, context)


def password_grant(admin: Admin, username: str, password: str) -> tuple[int, Any]:
    return admin.call(
        "POST",
        f"/realms/{REALM}/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        },
        form=True,
    )


def sign_in(admin: Admin) -> None:
    path = secret_store.secret_dir() / BOOTSTRAP_SECRET
    try:
        password = path.read_text(encoding="utf-8").strip()
    except OSError as missing:
        raise InputError(f"{path}: {missing.strerror}; is the stack up (just up)?") from missing
    status, body = password_grant(admin, BOOTSTRAP_USER, password)
    if status == 200:
        admin.token = body["access_token"]
        return
    reason = body.get("error") if isinstance(body, dict) else ""
    if reason == "unauthorized_client":
        raise Refused(
            "password sign-in is already off: setup has run; use the console (keycloak-admin.md)"
        )
    raise Refused(
        f"the first-start administrator cannot sign in (HTTP {status}, {reason}); it may "
        "already be deleted: setup has run"
    )


def ensure_flow(admin: Admin) -> None:
    base = f"/admin/realms/{REALM}/authentication/flows"
    flows = admin.need("GET", base)
    if any(f["alias"] == FLOW for f in flows):
        raise Refused(
            f"the flow {FLOW} exists already: an earlier setup was interrupted; "
            "delete it in the console, or re-create the stack's database"
        )
    admin.need(
        "POST",
        base,
        {
            "alias": FLOW,
            "providerId": "basic-flow",
            "topLevel": True,
            "builtIn": False,
            "description": "Password, then a security key",
        },
        ok=(201,),
    )
    flow_path = f"{base}/{urllib.parse.quote(FLOW)}/executions"
    forms_path = f"{base}/{urllib.parse.quote(FORMS)}/executions"
    admin.need("POST", f"{flow_path}/execution", {"provider": "auth-cookie"}, ok=(201,))
    admin.need(
        "POST",
        f"{flow_path}/flow",
        {
            "alias": FORMS,
            "type": "basic-flow",
            "provider": "registration-page-form",
            "description": "The form, then the key",
        },
        ok=(201,),
    )
    for provider in ("auth-username-password-form", "webauthn-authenticator"):
        admin.need("POST", f"{forms_path}/execution", {"provider": provider}, ok=(201,))
    for execution in admin.need("GET", flow_path):
        wanted = _requirement(execution)
        if wanted and execution.get("requirement") != wanted:
            admin.need("PUT", flow_path, {**execution, "requirement": wanted}, ok=(202, 204))


def _requirement(execution: dict[str, Any]) -> str:
    key = execution.get("providerId") or execution.get("displayName")
    return next((r for _, step, r in STEPS if step == key), "")


def check_flow(admin: Admin) -> list[str]:
    path = f"/admin/realms/{REALM}/authentication/flows/{urllib.parse.quote(FLOW)}/executions"
    got = {(_key(e), e.get("requirement"), e.get("level")) for e in admin.need("GET", path)}
    want = {(step, r, 0 if parent == FLOW else 1) for parent, step, r in STEPS}
    return [] if got == want else [f"flow {FLOW}: {sorted(got)} is not {sorted(want)}"]


def _key(execution: dict[str, Any]) -> str:
    return str(execution.get("providerId") or execution.get("displayName"))


def frontend_url(admin: Admin) -> str:
    return f"https://{admin.host}"


def move_to_admin_name(admin: Admin) -> None:
    """The realm's address becomes the admin name; tokens issued before carry the old issuer, so
    the caller signs in again afterwards."""
    admin.need(
        "PUT",
        f"/admin/realms/{REALM}",
        {"attributes": {"frontendUrl": frontend_url(admin)}},
        ok=(204,),
    )


def harden_realm(admin: Admin) -> None:
    admin.need("PUT", f"/admin/realms/{REALM}", REALM_SETTINGS, ok=(204,))


def check_realm(admin: Admin) -> list[str]:
    realm = admin.need("GET", f"/admin/realms/{REALM}")
    findings = [
        f"realm {k}: {realm.get(k)!r} is not {v!r}"
        for k, v in REALM_SETTINGS.items()
        if realm.get(k) != v
    ]
    url = (realm.get("attributes") or {}).get("frontendUrl")
    if url != frontend_url(admin):
        findings.append(f"realm frontendUrl: {url!r} is not {frontend_url(admin)!r}")
    return findings


def create_admin(admin: Admin, username: str, password: str) -> None:
    users = f"/admin/realms/{REALM}/users"
    if admin.need("GET", f"{users}?exact=true&username={urllib.parse.quote(username)}"):
        raise Refused("a user with that name exists already")
    admin.need(
        "POST",
        users,
        {
            "username": username,
            "enabled": True,
            "requiredActions": ["UPDATE_PASSWORD", "webauthn-register"],
            "credentials": [{"type": "password", "value": password, "temporary": True}],
        },
        ok=(201,),
    )
    user = admin.need("GET", f"{users}?exact=true&username={urllib.parse.quote(username)}")[0]
    role = admin.need("GET", f"/admin/realms/{REALM}/roles/admin")
    admin.need("POST", f"{users}/{user['id']}/role-mappings/realm", [role], ok=(204,))


def check_admin(admin: Admin, username: str) -> list[str]:
    users = f"/admin/realms/{REALM}/users"
    found = admin.need("GET", f"{users}?exact=true&username={urllib.parse.quote(username)}")
    if len(found) != 1:
        return ["the new administrator: not found"]
    user = found[0]
    roles = {r["name"] for r in admin.need("GET", f"{users}/{user['id']}/role-mappings/realm")}
    findings = [] if "admin" in roles else ["the new administrator: no admin role"]
    if not {"UPDATE_PASSWORD", "webauthn-register"} <= set(user.get("requiredActions", [])):
        findings.append(
            "the new administrator: the first login must set a password and register a key"
        )
    return findings


def no_direct_grants(admin: Admin) -> None:
    clients = f"/admin/realms/{REALM}/clients"
    for client in admin.need("GET", clients):
        if client.get("directAccessGrantsEnabled"):
            admin.need(
                "PUT",
                f"{clients}/{client['id']}",
                {**client, "directAccessGrantsEnabled": False},
                ok=(204,),
            )


def read_password(path: Path | None) -> str:
    if path is not None:
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = getpass.getpass(
            f"Temporary password for the first login ({MIN_PASSWORD}+ characters): "
        )
        if getpass.getpass("The same again: ") != value:
            raise InputError("the two entries differ")
    if len(value) < MIN_PASSWORD:
        raise InputError(f"at least {MIN_PASSWORD} characters (the realm's password policy)")
    return value


def setup(username: str, password_file: Path | None) -> list[str]:
    if not username or username == BOOTSTRAP_USER or not username.isprintable() or " " in username:
        raise InputError(f"a username other than {BOOTSTRAP_USER}, without spaces")
    password = read_password(password_file)
    admin = admin_route()
    sign_in(admin)
    move_to_admin_name(admin)
    sign_in(admin)
    ensure_flow(admin)
    harden_realm(admin)
    create_admin(admin, username, password)
    findings = check_flow(admin) + check_realm(admin) + check_admin(admin, username)
    if findings:
        return findings  # direct grants stay on, so the run can be repeated after a fix
    no_direct_grants(admin)
    admin.token = ""
    status, body = password_grant(admin, username, password)
    error = body.get("error") if isinstance(body, dict) else ""
    if error != "unauthorized_client":
        return [
            f"a password sign-in still answers HTTP {status} ({error}), not unauthorized_client"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser(
        "setup", help="harden the master realm and create the owner's administrator"
    )
    run.add_argument("username")
    run.add_argument(
        "--password-file", type=Path, help="the temporary password (tests); else asked"
    )
    args = parser.parse_args(argv)
    try:
        findings = setup(args.username, args.password_file)
    except (InputError, ca.InputError, secret_store.InputError) as error:
        print(f"keycloak: {error}", file=sys.stderr)
        return 2
    except Refused as error:
        print(f"keycloak: refused: {error}", file=sys.stderr)
        return 1
    except ssl.SSLError as error:
        hint = (
            "; Traefik may still be ordering the admin name's certificate after a start: "
            "wait a minute and run it again"
            if "UNRECOGNIZED_NAME" in str(error)
            else ""
        )
        print(f"keycloak: {type(error).__name__}: {error}{hint}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"keycloak: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    if findings:
        print("\n".join(f"keycloak: check failed: {f}" for f in findings), file=sys.stderr)
        return 1
    # The administrator's name is not repeated: output gets pasted into chats and tickets
    print(
        "keycloak: master realm hardened (password + security key, brute-force detection, events, "
        "no password-only sign-in); your administrator is created."
    )
    print(
        "keycloak: next, in the browser: sign in as that user, set the password, register the key, "
        f"then delete {BOOTSTRAP_USER} (docs/runbooks/keycloak-admin.md)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
