"""AetherSpark's certificate authority (compose-base.md, "Certificates"). Standard library; needs
Docker and `age`; runs in WSL2, in CI and on the Spark.

    python3 -I deploy/ca/ca.py COMMAND

    init                 once per machine: root and intermediate with name constraints, the
                         root key sealed into the store, then `configure`
    configure            write step-ca's ca.json: policy, the ACME provisioner for Traefik and
                         one JWK provisioner per `step-ca-provisioner-<service>` secret
    volumes              create the volumes the stack user must own, and give them to it
    prepare              for `just up`: volumes, init when there is no CA, configure (restarts a
                         running step-ca when its ca.json changed)
    status               the intermediate's remaining validity; exit 1 below 60 days
    root                 print the root certificate (for the trust runbook)
    renew-intermediate   sign a new intermediate with the sealed root key

The place (`AETHERSPARK_PLACE`, default `localhost`) gives the suffix `aether.<place>` that the root
and the intermediate are constrained to. The root key exists unencrypted only in a container's
in-memory folder and in this process; it leaves over a pipe into `age`, never onto a disk.

Exit codes: 0 done, 1 a finding (expiry, already initialised), 2 unusable input or a missing tool.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy" / "secret-store"))
import secret_store  # noqa: E402

# The stack's step image (step-ca and the step CLI, built from source by Compose from
# deploy/compose/stack/step-ca/Dockerfile); `just up` builds it before this tool runs
IMAGE = "aetherspark/step-ca:local"
VOLUME = "step-ca"  # step-ca's Compose volume, mounted at /ca
# Other volumes a service running as the stack user writes into (a new volume is root's)
DATA_VOLUMES = ("postgres-auth-data",)
PLACES = ("localhost", "home.arpa")
PROVISIONER_PREFIX = "step-ca-provisioner-"
ROOT_KEY = "step-ca-root-key.age"  # in the store, next to the sealed secrets
CA_PASSWORD = "step-ca-password"  # noqa: S105 - a secret's name, not a value
ROOT_LIFETIME = "87600h"  # 10 years
INTERMEDIATE_LIFETIME = "8760h"  # 1 year
LEAF_LIFETIME = "24h"
EXPIRY_WARNING_DAYS = 60
DURATION = re.compile(r"^[1-9][0-9]*(m|h)$")


class InputError(Exception):
    """Unusable input, a missing tool or a refused state; nothing more is written."""


@functools.cache
def image() -> str:
    """The stack's step image; it must exist (`just up` builds it)."""
    probe = subprocess.run(  # noqa: S603 - fixed tool, no shell
        [_docker(), "image", "inspect", IMAGE], capture_output=True, check=False
    )
    if probe.returncode != 0:
        raise InputError(f"{IMAGE} is not built: run `just up` (or docker compose build step-ca)")
    return IMAGE


def place() -> str:
    value = os.environ.get("AETHERSPARK_PLACE", "localhost")
    if value not in PLACES:
        raise InputError(f"AETHERSPARK_PLACE={value!r}: one of {', '.join(PLACES)}")
    return value


def suffix(where: str) -> str:
    return f"aether.{where}"


def internal_name(service: str, where: str) -> str:
    return f"{service}.svc.{suffix(where)}"


def ca_url(where: str) -> str:
    return f"https://{internal_name('step-ca', where)}:9000"


def stack_user() -> tuple[int, int]:
    """The stack's user (compose-base.md, Secrets): AETHERSPARK_UID/GID, else the caller."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    uid = os.environ.get("AETHERSPARK_UID") or (str(getuid()) if getuid else "")
    gid = os.environ.get("AETHERSPARK_GID") or (str(getgid()) if getgid else "")
    if not (uid.isdigit() and gid.isdigit()) or uid == "0":
        raise InputError("set AETHERSPARK_UID and AETHERSPARK_GID to the stack's non-root user")
    return int(uid), int(gid)


def lifetime(variable: str, default: str) -> str:
    value = os.environ.get(variable, default)
    if not DURATION.match(value):
        raise InputError(f"{variable}={value!r}: a duration such as 24h or 10m")
    return value


# --- What the CA's files say (pure, tested without Docker) -----------------------------------


def ca_template(where: str, path_len: int, self_signed: bool) -> str:
    """The root's or the intermediate's certificate template: CA, constrained to the place."""
    lines = ['  "subject": {{ toJson .Subject }},']
    if self_signed:
        lines.append('  "issuer": {{ toJson .Subject }},')
    lines += [
        '  "keyUsage": ["certSign", "crlSign"],',
        f'  "basicConstraints": {{"isCA": true, "maxPathLen": {path_len}}},',
        '  "nameConstraints": {"critical": true, "permittedDNSDomains": ['
        + json.dumps(suffix(where))
        + "]}",
    ]
    return "{\n" + "\n".join(lines) + "\n}\n"


def leaf_template(name: str) -> str:
    """A JWK provisioner's template: the certificate names its service, whatever was asked."""
    return json.dumps(
        {
            "subject": {"commonName": name},
            "sans": [{"type": "dns", "value": name}],
            "keyUsage": ["digitalSignature"],
            "extKeyUsage": ["serverAuth", "clientAuth"],
        }
    )


def claims(leaf: str) -> dict[str, Any]:
    return {
        "defaultTLSCertDuration": leaf,
        "maxTLSCertDuration": leaf,
        "minTLSCertDuration": "1m",
        "disableRenewal": False,
        "allowRenewalAfterExpiry": False,
    }


def jwk_services(manifest: Sequence[secret_store.Secret]) -> list[str]:
    """One JWK provisioner per `step-ca-provisioner-<service>` secret, in manifest order."""
    return [
        s.name.removeprefix(PROVISIONER_PREFIX)
        for s in manifest
        if s.name.startswith(PROVISIONER_PREFIX)
    ]


def ca_config(where: str, keys: dict[str, dict[str, Any]], leaf: str) -> dict[str, Any]:
    """step-ca's ca.json; `keys` maps each JWK service to {"key": ..., "encryptedKey": ...}."""
    jwk = [
        {
            "type": "JWK",
            "name": service,
            "key": keys[service]["key"],
            "encryptedKey": keys[service]["encryptedKey"],
            "claims": claims(leaf),
            "options": {"x509": {"template": leaf_template(internal_name(service, where))}},
        }
        for service in keys
    ]
    acme = {
        "type": "ACME",
        "name": "traefik",
        "challenges": ["tls-alpn-01"],
        "claims": claims(leaf),
        # Traefik's public names are one label under the suffix (auth., whoami., ...)
        "policy": {"x509": {"allow": {"dns": [f"*.{suffix(where)}"]}}},
    }
    return {
        "root": "/ca/certs/root_ca.crt",
        "crt": "/ca/certs/intermediate_ca.crt",
        "key": "/ca/secrets/intermediate_ca_key",
        "address": ":9000",
        "dnsNames": [internal_name("step-ca", where)],
        "logger": {"format": "text"},
        "db": {"type": "badgerv2", "dataSource": "/ca/db"},
        "authority": {
            # `*.x` matches one label only, so the internal names need their own pattern
            "policy": {
                "x509": {"allow": {"dns": [f"*.{suffix(where)}", f"*.svc.{suffix(where)}"]}}
            },
            "provisioners": [acme, *jwk],
        },
        "tls": {"minVersion": 1.2, "maxVersion": 1.3, "renegotiation": False},
    }


def kept_keys(current: dict[str, Any] | None, services: Sequence[str]) -> dict[str, Any]:
    """The JWK keys of the current ca.json that are still wanted; other provisioners are dropped."""
    found: dict[str, Any] = {}
    for p in ((current or {}).get("authority") or {}).get("provisioners") or []:
        if p.get("type") == "JWK" and p.get("name") in services:
            found[p["name"]] = {"key": p["key"], "encryptedKey": p["encryptedKey"]}
    return found


def days_left(not_after: str, now: datetime) -> int:
    end = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
    return (end - now).days


# --- Docker -----------------------------------------------------------------------------------


def _docker() -> str:
    found = shutil.which("docker")
    if not found:
        raise InputError("docker not found: start Docker Desktop (WSL integration) or Docker")
    return found


def project() -> str:
    """The stack's Compose project: `aetherspark`, or COMPOSE_PROJECT_NAME as Compose reads it."""
    return os.environ.get("COMPOSE_PROJECT_NAME") or "aetherspark"


def volume_name(volume: str) -> str:
    return f"{project()}_{volume}"


def _ensure_volume(volume: str) -> None:
    """Creates a Compose volume with Compose's labels, so `docker compose` adopts it."""
    name = volume_name(volume)
    probe = subprocess.run(  # noqa: S603 - fixed tool, no shell
        [_docker(), "volume", "inspect", name], capture_output=True, check=False
    )
    if probe.returncode == 0:
        return
    subprocess.run(  # noqa: S603
        [
            _docker(),
            "volume",
            "create",
            "--label",
            f"com.docker.compose.project={project()}",
            "--label",
            f"com.docker.compose.volume={volume}",
            name,
        ],
        capture_output=True,
        check=True,
    )


def own_volumes(volumes: Sequence[str]) -> None:
    """Gives each volume's top folder to the stack user, mode 0700 (a new volume is root's)."""
    uid, gid = stack_user()
    for volume in volumes:
        _ensure_volume(volume)
        subprocess.run(  # noqa: S603
            [
                _docker(),
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--security-opt",
                "no-new-privileges:true",
                "-v",
                f"{volume_name(volume)}:/v",
                "--entrypoint",
                "sh",
                image(),
                "-c",
                f"chown {uid}:{gid} /v && chmod 0700 /v",
            ],
            capture_output=True,
            check=True,
        )


def run_in_ca(
    script: str,
    *,
    secrets: Sequence[str] = (),
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> bytes:
    """Runs `script` (sh) as the stack user with the CA volume at /ca, an in-memory /work, no
    network and the named secrets read-only under /secrets; returns its standard output."""
    uid, gid = stack_user()
    folder = secret_store.secret_dir()
    command = [
        _docker(),
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        f"{uid}:{gid}",
        "--tmpfs",
        f"/work:mode=0700,uid={uid},gid={gid}",
        "-e",
        "HOME=/work",
        "-e",
        "STEPPATH=/work/.step",
        "-v",
        f"{volume_name(VOLUME)}:/ca",
    ]
    for name in secrets:
        source = folder / name
        if not source.is_file():
            raise InputError(f"{name} is not in {folder}: run `just secrets unseal` first")
        command += ["-v", f"{source}:/secrets/{name}:ro"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    command += ["--entrypoint", "sh", image(), "-c", "set -eu; umask 077; cd /work; " + script]
    done = subprocess.run(command, input=stdin or b"", capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise InputError("step in the CA container failed: " + " | ".join(detail))
    return done.stdout


def _read_config() -> dict[str, Any] | None:
    text = run_in_ca("if [ -f /ca/config/ca.json ]; then cat /ca/config/ca.json; fi")
    return json.loads(text) if text.strip() else None


def _initialised() -> bool:
    return run_in_ca("[ -f /ca/certs/root_ca.crt ] && echo yes || true").strip() == b"yes"


# Signs a new intermediate into /ca/pending with the root key in /work/root.key and the root
# certificate $ROOT_CRT; the intermediate's key is encrypted with step-ca-password.
PENDING = "rm -rf /ca/pending; mkdir -p /ca/pending; "
SIGN_INTERMEDIATE = (
    'printf "%s" "$INT_TPL" > int.tpl; '
    'step certificate create "AetherSpark Intermediate CA ($PLACE)" '
    "/ca/pending/intermediate_ca.crt /ca/pending/intermediate_ca_key --template int.tpl "
    '--ca "$ROOT_CRT" --ca-key root.key '
    f"--password-file /secrets/{CA_PASSWORD} "
    '--kty EC --curve P-256 --not-after "$INT_LIFETIME" >&2; '
)
# Moves what is pending into place, the root certificate last: it marks an initialised CA
COMMIT = (
    "mkdir -p /ca/certs /ca/secrets /ca/config /ca/db; "
    "mv -f /ca/pending/intermediate_ca_key /ca/secrets/intermediate_ca_key; "
    "mv -f /ca/pending/intermediate_ca.crt /ca/certs/intermediate_ca.crt; "
    "if [ -f /ca/pending/root_ca.crt ]; then mv /ca/pending/root_ca.crt /ca/certs/root_ca.crt; fi; "
    "rmdir /ca/pending"
)


def _intermediate_env(where: str) -> dict[str, str]:
    return {
        "PLACE": where,
        "INT_TPL": ca_template(where, 0, self_signed=False),
        "INT_LIFETIME": lifetime("AETHERSPARK_CA_INTERMEDIATE_LIFETIME", INTERMEDIATE_LIFETIME),
    }


def init() -> None:
    """Root and intermediate into /ca/pending, the root key out over the pipe and sealed, and
    only then the CA's files into place: a failure on the way leaves no half-made CA."""
    where = place()
    if _initialised():
        raise InputError("the CA is already initialised (status, renew-intermediate or a re-init)")
    sealed = secret_store.store_dir() / ROOT_KEY
    if sealed.exists():
        raise InputError(f"{sealed} exists: a root key from an earlier CA; move it away first")
    own_volumes([VOLUME])
    root_key = run_in_ca(
        PENDING + 'printf "%s" "$ROOT_TPL" > root.tpl; '
        'step certificate create "AetherSpark Root CA ($PLACE)" /ca/pending/root_ca.crt root.key '
        f"--template root.tpl --kty EC --curve P-256 --not-after {ROOT_LIFETIME} "
        "--no-password --insecure >&2; " + SIGN_INTERMEDIATE + "cat root.key",
        secrets=[CA_PASSWORD],
        env={
            **_intermediate_env(where),
            "ROOT_TPL": ca_template(where, 1, self_signed=True),
            "ROOT_CRT": "/ca/pending/root_ca.crt",
        },
    )
    if b"PRIVATE KEY" not in root_key:
        raise InputError("the CA container returned no root key; nothing was sealed")
    secret_store.seal(root_key, sealed)
    run_in_ca(COMMIT)


def renew_intermediate() -> None:
    where = place()
    if not _initialised():
        raise InputError("no CA yet: run init")
    root_key = secret_store.open_sealed(secret_store.store_dir() / ROOT_KEY)
    run_in_ca(
        "cat > root.key; " + PENDING + SIGN_INTERMEDIATE + COMMIT,
        secrets=[CA_PASSWORD],
        env={**_intermediate_env(where), "ROOT_CRT": "/ca/certs/root_ca.crt"},
        stdin=root_key,
    )


def _new_jwk(service: str) -> dict[str, Any]:
    secret = PROVISIONER_PREFIX + service
    out = run_in_ca(
        "step crypto jwk create pub.json priv.json --kty EC --crv P-256 "
        f"--password-file /secrets/{secret} --force >&2; "
        "cat pub.json; echo; echo '--'; step crypto jose format < priv.json",
        secrets=[secret],
    ).decode("ascii")
    public, encrypted = out.rsplit("\n--\n", 1)
    return {"key": json.loads(public), "encryptedKey": encrypted.strip()}


def configure(manifest: Sequence[secret_store.Secret]) -> bool:
    """Writes ca.json; True if it changed (step-ca must then restart)."""
    where = place()
    if not _initialised():
        raise InputError("no CA yet: run init")
    current = _read_config()
    if current is not None and current.get("dnsNames") != [internal_name("step-ca", where)]:
        raise InputError(f"this CA is for {current.get('dnsNames')}, not the place {where}")
    services = jwk_services(manifest)
    keys = kept_keys(current, services)
    for service in services:
        if service not in keys:
            keys[service] = _new_jwk(service)
    leaf = lifetime("AETHERSPARK_CA_LEAF_LIFETIME", LEAF_LIFETIME)
    wanted = ca_config(where, {s: keys[s] for s in services}, leaf)
    if wanted == current:
        return False
    run_in_ca(
        "cat > /ca/config/ca.json.new; mv -f /ca/config/ca.json.new /ca/config/ca.json",
        stdin=json.dumps(wanted, indent=2).encode("utf-8"),
    )
    return True


def _restart() -> bool:
    """Restarts the project's step-ca container if it runs (it reads ca.json at start only)."""
    found = subprocess.run(  # noqa: S603 - fixed tool, no shell
        [
            _docker(),
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project()}",
            "--filter",
            "label=com.docker.compose.service=step-ca",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for container in found:
        subprocess.run([_docker(), "restart", container], capture_output=True, check=True)  # noqa: S603
    return bool(found)


def status(now: datetime | None = None) -> tuple[int, str]:
    out = run_in_ca(
        "step certificate inspect --format json /ca/certs/intermediate_ca.crt; echo '--'; "
        "step certificate fingerprint /ca/certs/root_ca.crt"
    ).decode("utf-8")
    inspected, fingerprint = out.rsplit("\n--\n", 1)
    not_after = json.loads(inspected)["validity"]["end"]
    return days_left(not_after, now or datetime.now(UTC)), fingerprint.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    commands = ("init", "configure", "volumes", "prepare", "status", "root", "renew-intermediate")
    for command in commands:
        sub.add_parser(command)
    args = parser.parse_args(argv)
    try:
        if args.command == "root":
            sys.stdout.write(run_in_ca("cat /ca/certs/root_ca.crt").decode("ascii"))
            return 0
        if args.command == "status":
            days, fingerprint = status()
            print(f"ca: intermediate valid for {days} more days; root fingerprint {fingerprint}")
            if days < EXPIRY_WARNING_DAYS:
                print(f"ca: below {EXPIRY_WARNING_DAYS} days: renew it (certificates.md)")
                return 1
            return 0
        manifest = secret_store.load_manifest()
        if args.command in ("volumes", "prepare"):
            volumes = [VOLUME, *(f"{s}-tls" for s in jwk_services(manifest)), *DATA_VOLUMES]
            own_volumes(volumes)
            print(f"ca: {len(volumes)} volumes owned by the stack user ({', '.join(volumes)})")
        if args.command == "volumes":
            # Only the volumes: a restore fills the CA's afterwards (deploy/backup/backup.sh). It
            # used to fall through to the last branch below, renew-intermediate (found 2026-11-12)
            return 0
        if args.command == "prepare":
            if not _initialised():
                init()
                print(f"ca: initialised for *.{suffix(place())}; root key sealed into the store")
            if configure(manifest):
                print("ca: ca.json written" + ("; step-ca restarted" if _restart() else ""))
        elif args.command == "init":
            if _initialised():
                print("ca: already initialised; nothing changed")
                return 1
            init()
            configure(manifest)
            print(f"ca: initialised for *.{suffix(place())}; root key sealed into the store")
        elif args.command == "configure":
            print("ca: ca.json written" if configure(manifest) else "ca: ca.json unchanged")
        else:
            renew_intermediate()
            restarted = _restart()
            print("ca: new intermediate signed" + ("; step-ca restarted" if restarted else ""))
    except (InputError, secret_store.InputError) as error:
        print(f"ca: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"ca: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
