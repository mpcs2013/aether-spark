"""Keycloak's realms, administered as code (compose-base.md, "Recovery and lock-out"; PRs 5b, 6a).
Standard library; runs where `just up` runs, with the stack up.

    python3 -I deploy/keycloak/admin.py setup USERNAME [--password-file FILE]
    python3 -I deploy/keycloak/admin.py apply [--if-ready]
    python3 -I deploy/keycloak/admin.py user add USERNAME [--key] [--password-file FILE]

`setup`, once per stack, hardens the `master` realm (the administrators') and creates the owner's
permanent administrator and the config service account, in one run:
  0. the realm's own address (Frontend URL) becomes the admin name: Keycloak's `hostname-admin`
     moves the console only, so without it the administrators' login pages would post to the
     sign-in name, where Traefik refuses /realms/master/ (measured 2026-11-08);
  1. a login flow that requires the password and then a security key (WebAuthn) for every
     administrator; a new administrator registers the key at the first login;
  2. brute-force detection, login and admin events, TLS everywhere, a password policy;
  3. the administrator USERNAME, with the realm's `admin` role, a temporary password (asked for, or
     read from FILE) that must be changed at the first login, and the key to register;
  4. the config service account `aetherspark-config` (client credentials, its secret from the
     secrets store): it may administer the `aetherspark` realm and nothing else;
  5. last, no client of the realm may sign in with a password alone (direct grants), so this tool
     cannot sign in as an administrator again: from then on the console, with the key, is the way
     in;
  6. then `apply`, below, as the config service account.
It signs in as Keycloak's temporary first-start administrator, whose password it reads from the
in-memory secrets folder. After the run the owner signs in with the new account and deletes
`bootstrap-admin` in the console (docs/runbooks/keycloak-admin.md).

`apply` brings the `aetherspark` realm to what this repository says: its settings
(deploy/keycloak/aetherspark-realm.json), its required actions, and the login flow `browser-2fa`:
the password, then an app code or a security key, whichever the user has set up. Every new user
must set one up at the first sign-in, and no user may delete their own. Keycloak imports the realm
file only into an empty database, so `apply` is how a change reaches a running realm. It signs in
as the config service account. `just up` runs it (`--if-ready`: skipped, with a note, before
`setup` has created the account). A flow that differs from the code is reported, never rebuilt in
place.

`user add` creates a user of the `aetherspark` realm with a temporary password (asked for, or read
from FILE) that must be changed at the first sign-in, then an app code to set up (or, with --key, a
security key to register).

Every call goes to Traefik's admin route (auth-admin.aether.<place>), checking its certificate
against AetherSpark's root. No value is printed or passed in an argument, and no user's name is
printed (output gets pasted into chats).

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
import time
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

MASTER = "master"
REALM = "aetherspark"
REALM_FILE = Path(__file__).resolve().parent / "aetherspark-realm.json"
BOOTSTRAP_USER = "bootstrap-admin"  # KC_BOOTSTRAP_ADMIN_USERNAME in the stack's Compose file
BOOTSTRAP_SECRET = "keycloak-bootstrap-admin-password"  # noqa: S105 - a secret's name
CONFIG_CLIENT = "aetherspark-config"
CONFIG_SECRET = "keycloak-config-client-secret"  # noqa: S105 - a secret's name
# The config account's rights: these roles of master's management client for the aetherspark realm
CONFIG_ROLES = (
    "view-realm",
    "manage-realm",
    "view-clients",
    "manage-clients",
    "query-clients",
    "view-users",
    "manage-users",
    "query-users",
)
MIN_PASSWORD = 14  # both realms' password policy

# A flow is (alias, description, steps); a step is (parent, provider or sub-flow alias, requirement)
Flow = tuple[str, str, tuple[tuple[str, str, str], ...]]
ADMIN_FLOW: Flow = (
    "browser-security-key",
    "Password, then a security key",
    (
        ("browser-security-key", "auth-cookie", "ALTERNATIVE"),
        ("browser-security-key", "browser-security-key forms", "ALTERNATIVE"),
        ("browser-security-key forms", "auth-username-password-form", "REQUIRED"),
        ("browser-security-key forms", "webauthn-authenticator", "REQUIRED"),
    ),
)
# The users' second factor is conditional on the user having one: a REQUIRED step of alternatives
# that are not set up yet fails the whole sign-in (measured 2026-11-08), so a new user could never
# set one up. Instead every new user gets the default action to set up an app code (or, created
# with --key, to register a key), and may not delete their own second factor (USER_ACTIONS).
USER_FLOW: Flow = (
    "browser-2fa",
    "Password, then an app code or a security key",
    (
        ("browser-2fa", "auth-cookie", "ALTERNATIVE"),
        ("browser-2fa", "browser-2fa forms", "ALTERNATIVE"),
        ("browser-2fa forms", "auth-username-password-form", "REQUIRED"),
        ("browser-2fa forms", "browser-2fa second factor", "CONDITIONAL"),
        ("browser-2fa second factor", "conditional-user-configured", "REQUIRED"),
        ("browser-2fa second factor", "auth-otp-form", "ALTERNATIVE"),
        ("browser-2fa second factor", "webauthn-authenticator", "ALTERNATIVE"),
    ),
)
# The aetherspark realm's required actions: alias -> (enabled, a default action for every new user).
# VERIFY_PROFILE is off: Keycloak 26 would ask every user for an email and names, and AetherSpark
# sends no email and shows the username (measured 2026-11-08)
USER_ACTIONS = {
    "CONFIGURE_TOTP": (True, True),
    "webauthn-register": (True, False),
    "UPDATE_PASSWORD": (True, False),
    "delete_credential": (False, False),
    "VERIFY_PROFILE": (False, False),
}

ADMIN_SETTINGS: dict[str, Any] = {
    "browserFlow": ADMIN_FLOW[0],
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


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def admin_route(wait: float = 90) -> Admin:
    """The admin route, once Traefik serves the admin name: after a start Traefik orders each
    name's certificate in turn, and until then the handshake fails with "unrecognized name"."""
    where = ca.place()
    root = ca.run_in_ca("cat /ca/certs/root_ca.crt").decode("ascii")
    context = ssl.create_default_context(cadata=root)  # AetherSpark's root only
    address = os.environ.get("AETHERSPARK_PUBLISH_ADDRESS", "127.0.0.1")
    admin = Admin(f"auth-admin.aether.{where}", address, context)
    deadline = time.monotonic() + wait
    while True:
        try:
            admin.call("GET", f"/realms/{MASTER}/.well-known/openid-configuration")
            return admin
        except ssl.SSLError as error:
            if "UNRECOGNIZED_NAME" not in str(error) or time.monotonic() > deadline:
                raise
            time.sleep(2)


def read_secret(name: str) -> str:
    path = secret_store.secret_dir() / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as missing:
        raise InputError(f"{path}: {missing.strerror}; is the stack up (just up)?") from missing


def token_request(admin: Admin, form: dict[str, str]) -> tuple[int, Any]:
    return admin.call("POST", f"/realms/{MASTER}/protocol/openid-connect/token", form, form=True)


def password_grant(admin: Admin, username: str, password: str) -> tuple[int, Any]:
    return token_request(
        admin,
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        },
    )


def sign_in_bootstrap(admin: Admin) -> None:
    status, body = password_grant(admin, BOOTSTRAP_USER, read_secret(BOOTSTRAP_SECRET))
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


def sign_in_config(admin: Admin) -> bool:
    """The config service account's token; False if the account does not exist (yet)."""
    status, body = token_request(
        admin,
        {
            "grant_type": "client_credentials",
            "client_id": CONFIG_CLIENT,
            "client_secret": read_secret(CONFIG_SECRET),
        },
    )
    if status == 200:
        admin.token = body["access_token"]
        return True
    reason = body.get("error") if isinstance(body, dict) else ""
    if reason in ("invalid_client", "unauthorized_client"):
        return False
    raise Refused(f"the config service account cannot sign in (HTTP {status}, {reason})")


# Flows


def _subflows(flow: Flow) -> set[str]:
    return {parent for parent, _, _ in flow[2]} - {flow[0]}


def ensure_flow(admin: Admin, realm: str, flow: Flow) -> bool:
    """Creates the flow if missing (True); an existing one is left alone (False)."""
    alias, description, steps = flow
    base = f"/admin/realms/{realm}/authentication/flows"
    if any(f["alias"] == alias for f in admin.need("GET", base)):
        return False
    admin.need(
        "POST",
        base,
        {
            "alias": alias,
            "providerId": "basic-flow",
            "topLevel": True,
            "builtIn": False,
            "description": description,
        },
        ok=(201,),
    )
    subflows = _subflows(flow)
    for parent, step, _ in steps:
        executions = f"{base}/{_q(parent)}/executions"
        if step in subflows:
            sub = {"alias": step, "type": "basic-flow", "provider": "registration-page-form"}
            admin.need("POST", f"{executions}/flow", {**sub, "description": step}, ok=(201,))
        else:
            admin.need("POST", f"{executions}/execution", {"provider": step}, ok=(201,))
    top = f"{base}/{_q(alias)}/executions"
    wanted = {step: requirement for _, step, requirement in steps}
    for execution in admin.need("GET", top):
        requirement = wanted.get(_key(execution))
        if requirement and execution.get("requirement") != requirement:
            admin.need("PUT", top, {**execution, "requirement": requirement}, ok=(202, 204))
    return True


def _key(execution: dict[str, Any]) -> str:
    return str(execution.get("providerId") or execution.get("displayName"))


def check_flow(admin: Admin, realm: str, flow: Flow) -> list[str]:
    alias, _, steps = flow
    path = f"/admin/realms/{realm}/authentication/flows/{_q(alias)}/executions"
    got = sorted((_key(e), e.get("requirement"), e.get("level")) for e in admin.need("GET", path))
    level = {alias: -1}
    for parent, step, _ in steps:
        level[step] = level[parent] + 1
    want = sorted((step, requirement, level[step]) for _, step, requirement in steps)
    return [] if got == want else [f"{realm} flow {alias}: {got} is not {want}"]


# Settings, users, clients


def check_settings(admin: Admin, realm: str, settings: dict[str, Any]) -> list[str]:
    current = admin.need("GET", f"/admin/realms/{realm}")
    return [
        f"{realm} {k}: {current.get(k)!r} is not {v!r}"
        for k, v in settings.items()
        if current.get(k) != v
    ]


def find_user(admin: Admin, realm: str, username: str) -> list[dict[str, Any]]:
    path = f"/admin/realms/{realm}/users?exact=true&username={_q(username)}"
    found: list[dict[str, Any]] = admin.need("GET", path)
    return found


def create_user(
    admin: Admin, realm: str, username: str, password: str, actions: list[str]
) -> dict[str, Any]:
    """The user with exactly these required actions (Keycloak may add its default ones)."""
    if find_user(admin, realm, username):
        raise Refused("a user with that name exists already")
    admin.need(
        "POST",
        f"/admin/realms/{realm}/users",
        {
            "username": username,
            "enabled": True,
            "requiredActions": actions,
            "credentials": [{"type": "password", "value": password, "temporary": True}],
        },
        ok=(201,),
    )
    user = find_user(admin, realm, username)[0]
    if set(user.get("requiredActions", [])) != set(actions):
        path = f"/admin/realms/{realm}/users/{user['id']}"
        admin.need("PUT", path, {"requiredActions": actions}, ok=(204,))
        user = find_user(admin, realm, username)[0]
    return user


def client_by_id(admin: Admin, realm: str, client_id: str) -> dict[str, Any] | None:
    found = admin.need("GET", f"/admin/realms/{realm}/clients?clientId={_q(client_id)}")
    return found[0] if found else None


# The administrators' realm


def frontend_url(admin: Admin) -> str:
    return f"https://{admin.host}"


def move_to_admin_name(admin: Admin) -> None:
    """The realm's address becomes the admin name; tokens issued before carry the old issuer, so
    the caller signs in again afterwards."""
    admin.need(
        "PUT",
        f"/admin/realms/{MASTER}",
        {"attributes": {"frontendUrl": frontend_url(admin)}},
        ok=(204,),
    )


def check_master(admin: Admin) -> list[str]:
    findings = check_settings(admin, MASTER, ADMIN_SETTINGS)
    realm = admin.need("GET", f"/admin/realms/{MASTER}")
    url = (realm.get("attributes") or {}).get("frontendUrl")
    if url != frontend_url(admin):
        findings.append(f"master frontendUrl: {url!r} is not {frontend_url(admin)!r}")
    return findings


def check_admin(admin: Admin, username: str) -> list[str]:
    found = find_user(admin, MASTER, username)
    if len(found) != 1:
        return ["the new administrator: not found"]
    user = found[0]
    path = f"/admin/realms/{MASTER}/users/{user['id']}/role-mappings/realm"
    roles = {r["name"] for r in admin.need("GET", path)}
    findings = [] if "admin" in roles else ["the new administrator: no admin role"]
    if not {"UPDATE_PASSWORD", "webauthn-register"} <= set(user.get("requiredActions", [])):
        findings.append("the new administrator: the first login must set a password and a key")
    return findings


def create_config_account(admin: Admin) -> None:
    """A confidential client with only a service account, whose roles administer the aetherspark
    realm (master's client `aetherspark-realm`) and nothing in master."""
    if client_by_id(admin, MASTER, CONFIG_CLIENT):
        raise Refused(f"the client {CONFIG_CLIENT} exists already")
    admin.need(
        "POST",
        f"/admin/realms/{MASTER}/clients",
        {
            "clientId": CONFIG_CLIENT,
            "name": "AetherSpark configuration (just keycloak apply)",
            "enabled": True,
            "publicClient": False,
            "clientAuthenticatorType": "client-secret",
            "secret": read_secret(CONFIG_SECRET),
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "frontchannelLogout": False,
        },
        ok=(201,),
    )
    client = client_by_id(admin, MASTER, CONFIG_CLIENT)
    management = client_by_id(admin, MASTER, f"{REALM}-realm")
    if client is None or management is None:
        raise Refused(f"{CONFIG_CLIENT} or master's {REALM}-realm client is missing")
    clients = f"/admin/realms/{MASTER}/clients"
    account = admin.need("GET", f"{clients}/{client['id']}/service-account-user")
    roles = [
        admin.need("GET", f"{clients}/{management['id']}/roles/{name}") for name in CONFIG_ROLES
    ]
    mapping = (
        f"/admin/realms/{MASTER}/users/{account['id']}/role-mappings/clients/{management['id']}"
    )
    admin.need("POST", mapping, roles, ok=(204,))


def no_direct_grants(admin: Admin) -> None:
    clients = f"/admin/realms/{MASTER}/clients"
    for client in admin.need("GET", clients):
        if client.get("directAccessGrantsEnabled"):
            admin.need(
                "PUT",
                f"{clients}/{client['id']}",
                {**client, "directAccessGrantsEnabled": False},
                ok=(204,),
            )


# The aetherspark realm


def realm_settings() -> dict[str, Any]:
    """The realm file's settings, and the login flow `apply` creates (not in the file: Keycloak's
    import would refuse a flow that does not exist yet)."""
    data = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    if data.get("realm") != REALM:
        raise InputError(f"{REALM_FILE.name}: realm must be {REALM}")
    settings = {k: v for k, v in data.items() if k != "realm"}
    settings["browserFlow"] = USER_FLOW[0]
    return settings


def apply_actions(admin: Admin) -> list[str]:
    base = f"/admin/realms/{REALM}/authentication/required-actions"
    findings = []
    for alias, (enabled, default) in USER_ACTIONS.items():
        action = admin.need("GET", f"{base}/{_q(alias)}")
        if action.get("enabled") != enabled or action.get("defaultAction") != default:
            wanted = {**action, "enabled": enabled, "defaultAction": default}
            admin.need("PUT", f"{base}/{_q(alias)}", wanted, ok=(204,))
            action = admin.need("GET", f"{base}/{_q(alias)}")
        if action.get("enabled") != enabled or action.get("defaultAction") != default:
            findings.append(f"{REALM} required action {alias}: not as set")
    return findings


def apply_realm(admin: Admin) -> list[str]:
    settings = realm_settings()
    ensure_flow(admin, REALM, USER_FLOW)
    findings = check_flow(admin, REALM, USER_FLOW)
    if findings:
        return findings  # never bind a flow that differs from the code
    findings = apply_actions(admin)
    admin.need("PUT", f"/admin/realms/{REALM}", settings, ok=(204,))
    return findings + check_settings(admin, REALM, settings)


# Commands


def read_password(path: Path | None, purpose: str) -> str:
    if path is not None:
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = getpass.getpass(f"{purpose} ({MIN_PASSWORD}+ characters): ")
        if getpass.getpass("The same again: ") != value:
            raise InputError("the two entries differ")
    if len(value) < MIN_PASSWORD:
        raise InputError(f"at least {MIN_PASSWORD} characters (the realm's password policy)")
    return value


def valid_username(username: str) -> None:
    if not username or username == BOOTSTRAP_USER or not username.isprintable() or " " in username:
        raise InputError(f"a username other than {BOOTSTRAP_USER}, without spaces")


def setup(username: str, password_file: Path | None) -> list[str]:
    valid_username(username)
    password = read_password(password_file, "Temporary password for the first login")
    admin = admin_route()
    sign_in_bootstrap(admin)
    move_to_admin_name(admin)
    sign_in_bootstrap(admin)
    if not ensure_flow(admin, MASTER, ADMIN_FLOW):
        raise Refused(
            f"the flow {ADMIN_FLOW[0]} exists already: an earlier setup was interrupted; "
            "re-create the stack's database (keycloak-admin.md)"
        )
    admin.need("PUT", f"/admin/realms/{MASTER}", ADMIN_SETTINGS, ok=(204,))
    user = create_user(admin, MASTER, username, password, ["UPDATE_PASSWORD", "webauthn-register"])
    role = admin.need("GET", f"/admin/realms/{MASTER}/roles/admin")
    mapping = f"/admin/realms/{MASTER}/users/{user['id']}/role-mappings/realm"
    admin.need("POST", mapping, [role], ok=(204,))
    create_config_account(admin)
    findings = check_flow(admin, MASTER, ADMIN_FLOW) + check_master(admin)
    findings += check_admin(admin, username)
    if findings:
        return findings  # direct grants stay on, so the run can be repeated after a fix
    no_direct_grants(admin)
    status, body = password_grant(admin, username, password)
    error = body.get("error") if isinstance(body, dict) else ""
    if error != "unauthorized_client":
        return [
            f"a password sign-in still answers HTTP {status} ({error}), not unauthorized_client"
        ]
    if not sign_in_config(admin):
        return ["the config service account cannot sign in"]
    return apply_realm(admin)


def apply(if_ready: bool) -> list[str] | None:
    """The findings; None when skipped (--if-ready, before setup)."""
    admin = admin_route()
    if not sign_in_config(admin):
        if if_ready:
            return None
        raise Refused(f"the config service account {CONFIG_CLIENT} cannot sign in; run setup first")
    return apply_realm(admin)


def add_user(username: str, key: bool, password_file: Path | None) -> list[str]:
    valid_username(username)
    password = read_password(password_file, "Temporary password for the user's first sign-in")
    admin = admin_route()
    if not sign_in_config(admin):
        raise Refused(f"the config service account {CONFIG_CLIENT} cannot sign in; run setup first")
    actions = ["UPDATE_PASSWORD", "webauthn-register" if key else "CONFIGURE_TOTP"]
    user = create_user(admin, REALM, username, password, actions)
    return [] if set(user.get("requiredActions", [])) == set(actions) else ["the new user: actions"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("setup", help="harden master; create the owner's administrator")
    run.add_argument("username")
    run.add_argument("--password-file", type=Path, help="the temporary password (tests)")
    run = sub.add_parser("apply", help="bring the aetherspark realm to the repository's state")
    run.add_argument("--if-ready", action="store_true", help="skip if setup has not run yet")
    users = sub.add_parser("user", help="the aetherspark realm's users")
    user_sub = users.add_subparsers(dest="action", required=True)
    run = user_sub.add_parser("add", help="a user with a temporary password and a second factor")
    run.add_argument("username")
    run.add_argument("--key", action="store_true", help="a security key instead of an app code")
    run.add_argument("--password-file", type=Path, help="the temporary password (tests)")
    args = parser.parse_args(argv)
    findings: list[str] | None
    try:
        if args.command == "setup":
            findings = setup(args.username, args.password_file)
        elif args.command == "apply":
            findings = apply(args.if_ready)
        else:
            findings = add_user(args.username, args.key, args.password_file)
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
    if findings is None:
        print("keycloak: apply skipped: no config service account yet (just keycloak setup)")
        return 0
    if findings:
        print("\n".join(f"keycloak: check failed: {f}" for f in findings), file=sys.stderr)
        return 1
    # No user's name is printed: output gets pasted into chats and tickets
    if args.command == "setup":
        print(
            "keycloak: master realm hardened (password + security key, brute-force detection, "
            "events, no password-only sign-in); your administrator and the config service "
            "account are created; the aetherspark realm is applied."
        )
        print(
            "keycloak: next, in the browser: sign in as that user, set the password, register "
            f"the key, then delete {BOOTSTRAP_USER} (docs/runbooks/keycloak-admin.md)"
        )
    elif args.command == "apply":
        print("keycloak: the aetherspark realm matches the repository")
    else:
        factor = "a security key" if args.key else "an app code"
        print(
            f"keycloak: the user is created; at the first sign-in: a new password, then {factor} "
            f"(https://auth.aether.{ca.place()}/realms/{REALM}/account/)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
