"""A browser's sign-in to the aetherspark realm, end to end (compose-base.md, tests C10 and C8's
chain). Standard library; used by tests/stack/run.sh.

    python3 -I oidc_flow.py STATE_DIR USERNAME CA_FILE

STATE_DIR holds the user's files: `password` (read; replaced by `password` again when Keycloak asks
for a new one), `totp` (the app-code secret, written when Keycloak asks to set one up, read at later
sign-ins). The steps, each printed as a line:
  1. the authorization request with PKCE (S256) through Traefik's sign-in route; the login form must
     post to the sign-in name;
  2. the password; then, if Keycloak asks: a new password, an app code to set up (its secret taken
     from the form, as an authenticator app scans it) or the app code. A sign-in without a second
     factor fails: each run is a fresh browser, so none may be remembered;
  3. the redirect back with an authorization code; the code for tokens (public client, PKCE);
  4. the tokens' issuer and user; a refresh; userinfo; logout, after which the refresh token is
     dead.
Exits 0 when every step holds, else 1 with the failing step. Prints no secret and no user's name.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.cookiejar
import json
import re
import secrets
import socket
import ssl
import struct
import sys
import time
import urllib.parse
import urllib.request
from http.client import HTTPResponse, HTTPSConnection
from pathlib import Path
from typing import Any

PLACE = "localhost"
HOST = f"auth.aether.{PLACE}"
ADDRESS = "127.0.0.1"
REALM = "aetherspark"
CLIENT = "account-console"  # Keycloak's own public client with PKCE; the realm's first test client
REDIRECT = f"https://{HOST}/realms/{REALM}/account/"
OIDC = f"https://{HOST}/realms/{REALM}/protocol/openid-connect"


class Failed(Exception):
    pass


class _Connection(HTTPSConnection):
    def connect(self) -> None:
        raw = socket.create_connection((ADDRESS, self.port or 443), timeout=15)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


class _Https(urllib.request.HTTPSHandler):
    def __init__(self, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._tls = context

    def https_open(self, req: urllib.request.Request) -> HTTPResponse:
        return self.do_open(_Connection, req, context=self._tls)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Browser:
    def __init__(self, cafile: str) -> None:
        context = ssl.create_default_context(cafile=cafile)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            _Https(context), urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def send(self, url: str, form: dict[str, str] | None = None) -> tuple[int, str, str]:
        """One request, no redirect followed: status, Location, body."""
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(url, data=data)  # noqa: S310 - fixed https base
        try:
            with self.opener.open(request, timeout=15) as response:
                return (
                    response.status,
                    response.headers.get("Location", ""),
                    response.read().decode(),
                )
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location", ""), error.read().decode()

    def follow(self, url: str, form: dict[str, str] | None = None) -> tuple[str, str]:
        """Follows redirects within Keycloak; stops at the client's redirect: (page, final URL)."""
        status, location, body = self.send(url, form)
        for _ in range(10):
            if status not in (301, 302, 303, 307):
                return body, url
            url = urllib.parse.urljoin(url, location)
            if url.startswith(REDIRECT):
                return "", url
            status, location, body = self.send(url)
        raise Failed("too many redirects")


def form_action(page: str) -> str:
    found = re.search(r'<form[^>]*action="([^"]+)"', page)
    if not found:
        raise Failed(f"no form on the page ({title(page)})")
    return html.unescape(found[1])


def hidden(page: str, name: str) -> str:
    found = re.search(rf'name="{name}"[^>]*value="([^"]*)"|value="([^"]*)"[^>]*name="{name}"', page)
    return html.unescape((found[1] or found[2]) if found else "")


def title(page: str) -> str:
    """The page's title, and Keycloak's message on an error page."""
    found = re.search(r'id="kc-page-title">\s*([^<]+?)\s*<', page)
    message = re.search(r'class="[^"]*instruction[^"]*">\s*([^<]+?)\s*<', page)
    text = found[1] if found else "?"
    return f"{text} ({html.unescape(message[1])})" if message else text


def totp(secret: str, at: float | None = None) -> str:
    """RFC 6238 (SHA-1, 6 digits, 30 s), keyed with the raw secret from Keycloak's setup form (its
    `totpSecret` field; an app gets the same bytes base32-encoded from the QR code)."""
    key = secret.encode("utf-8")
    counter = int((at or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def fresh_code(secret: str, used: Path) -> str:
    """A code from a 30-second window not used before (Keycloak refuses a code twice)."""
    window = str(int(time.time() // 30))
    if used.exists() and used.read_text() == window:
        time.sleep(31 - time.time() % 30)
        window = str(int(time.time() // 30))
    used.write_text(window)
    return totp(secret)


def token(browser: Browser, form: dict[str, str]) -> dict[str, Any]:
    status, _, body = browser.send(f"{OIDC}/token", form)
    parsed: dict[str, Any] = json.loads(body) if body.startswith("{") else {}
    if status != 200:
        raise Failed(f"token endpoint HTTP {status} {parsed.get('error')}")
    return parsed


def claims(jwt: str) -> dict[str, Any]:
    payload = jwt.split(".")[1]
    parsed: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    return parsed


def step(text: str) -> None:
    print(f"  ok  {text}")


def sign_in(state: Path, username: str, cafile: str) -> None:
    browser = Browser(cafile)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    query = urllib.parse.urlencode(
        {
            "client_id": CLIENT,
            "response_type": "code",
            "scope": "openid",
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": secrets.token_hex(8),
        }
    )
    page, _ = browser.follow(f"{OIDC}/auth?{query}")
    action = form_action(page)
    if not action.startswith(f"https://{HOST}/realms/{REALM}/"):
        raise Failed(f"the login form posts to {action.split('?')[0]}, not the sign-in name")
    step("authorization request with PKCE; the login form is on the sign-in name")
    password = (state / "password").read_text().strip()
    page, url = browser.follow(action, {"username": username, "password": password})
    second = False
    for _ in range(4):
        if url.startswith(REDIRECT):
            break
        if 'name="password-new"' in page:
            new = secrets.token_urlsafe(24)
            page, url = browser.follow(
                form_action(page), {"password-new": new, "password-confirm": new}
            )
            (state / "password").write_text(new)
            step("a new password, as Keycloak asked")
        elif 'name="totpSecret"' in page:
            secret = hidden(page, "totpSecret")
            (state / "totp").write_text(secret)
            code = fresh_code(secret, state / "totp-window")
            page, url = browser.follow(
                form_action(page), {"totp": code, "totpSecret": secret, "userLabel": "test"}
            )
            second = True
            step("an app code set up, as Keycloak asked")
        elif 'name="otp"' in page:
            if not (state / "totp").exists():
                raise Failed("Keycloak asks for an app code, but none was set up")
            code = fresh_code((state / "totp").read_text().strip(), state / "totp-window")
            page, url = browser.follow(form_action(page), {"otp": code})
            second = True
            step("the app code, as Keycloak asked")
        else:
            (state / "unexpected.html").write_text(page)  # for a look; holds no secret
            raise Failed(f"unexpected page: {title(page)}")
    if not url.startswith(REDIRECT):
        raise Failed(f"no redirect back with a code (last page: {title(page)})")
    if not second:
        raise Failed("signed in without a second factor")
    code = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("code", [""])[0]
    tokens = token(
        browser,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
    )
    access = claims(tokens["access_token"])
    if access.get("iss") != f"https://{HOST}/realms/{REALM}":
        raise Failed(f"the token's issuer is {access.get('iss')}")
    if access.get("preferred_username") != username:
        raise Failed("the token names another user")
    step("the code for tokens (PKCE); issuer and user as expected")
    refreshed = token(
        browser,
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT,
            "refresh_token": tokens["refresh_token"],
        },
    )
    step("a refresh")
    request = urllib.request.Request(  # noqa: S310 - fixed https base
        f"{OIDC}/userinfo", headers={"Authorization": f"Bearer {refreshed['access_token']}"}
    )
    with browser.opener.open(request, timeout=15) as response:
        info = json.loads(response.read().decode())
    if info.get("preferred_username") != username:
        raise Failed("userinfo names another user")
    step("userinfo")
    status, _, _ = browser.send(
        f"{OIDC}/logout", {"client_id": CLIENT, "refresh_token": refreshed["refresh_token"]}
    )
    if status not in (200, 204):
        raise Failed(f"logout HTTP {status}")
    try:
        token(
            browser,
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT,
                "refresh_token": refreshed["refresh_token"],
            },
        )
    except Failed:
        step("logout; the refresh token is dead afterwards")
        return
    raise Failed("the refresh token still works after logout")


def main() -> int:
    state, username, cafile = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    try:
        sign_in(state, username, cafile)
    except (Failed, OSError, KeyError, ValueError) as error:
        print(f"  FAILED: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
