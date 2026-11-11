"""Proves that the backup target refuses a delete (docs/architecture/backup-job.md, test B3).

    python3 -I deploy/backup/append_only.py REPOSITORY_FILE SNAPSHOT_ID

Sends one HTTP DELETE for the snapshot to the rest-server named in the repository file (as restic
reads it: `rest:http://name:password@host:port/path/`). The target must answer 403. Standard
library only; the login is read from the file and never printed. restic has no plain delete, and
`forget` is never run against the home infrastructure's target (exchange A-015, condition a).

Exit codes: 0 refused (append-only holds), 1 not refused or no answer, 2 unusable input.
"""

from __future__ import annotations

import base64
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")


class InputError(Exception):
    """An unusable repository file or snapshot id; nothing was sent."""


def target(repository: str, snapshot: str) -> tuple[str, str]:
    """The snapshot's URL without the login, and the Basic authorization value."""
    if not repository.startswith("rest:"):
        raise InputError("the repository file does not name a rest-server (rest:http...)")
    if not SNAPSHOT_ID.match(snapshot):
        raise InputError("a snapshot id is 64 hexadecimal characters")
    url = urllib.parse.urlsplit(repository.removeprefix("rest:"))
    if url.scheme not in ("http", "https") or not url.hostname or url.username is None:
        raise InputError("the repository file has no http(s) address with a login")
    login = f"{urllib.parse.unquote(url.username)}:{urllib.parse.unquote(url.password or '')}"
    netloc = url.hostname + (f":{url.port}" if url.port else "")
    path = url.path if url.path.endswith("/") else url.path + "/"
    clean = urllib.parse.urlunsplit((url.scheme, netloc, f"{path}snapshots/{snapshot}", "", ""))
    return clean, "Basic " + base64.b64encode(login.encode()).decode()


def delete_status(url: str, authorization: str) -> int:
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked in target()
        url, method="DELETE", headers={"Authorization": authorization}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    try:
        url, authorization = target(Path(args[0]).read_text(encoding="utf-8").strip(), args[1])
    except (InputError, OSError, ValueError) as error:
        print(f"append-only: {error}", file=sys.stderr)
        return 2
    try:
        status = delete_status(url, authorization)
    except OSError as error:
        print(
            f"append-only: no answer from the backup target ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1
    if status == 403:
        print(f"append-only: a delete of snapshot {args[1][:8]} is refused (HTTP 403)")
        return 0
    print(f"append-only: NOT REFUSED: the delete answered HTTP {status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
