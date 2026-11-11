# ADR-0010: Secrets — generated, age-encrypted, kept off git
- **Status:** Accepted · **Date:** 2026-10-03 (replaces the Proposed "sops + age in git" version) · **Amendments** 2026-10-04 (infrastructure credentials), 2026-10-11 (developer credentials), 2026-10-19 (workstation backup key)

## In plain words
Secrets are sealed envelopes. Git holds only the **list** of envelopes (names, owners, rotation), never the envelopes. The sealed envelopes live on the Spark and in backups on the LAN; only two keys can open them.

## Decision
### Where secrets live
| What | Where | Leaves the LAN? |
|---|---|---|
| Secret manifest: name, consuming service, rotation period, generator rule — **no values** | Git: `deploy/secrets.manifest.yaml` | Yes (harmless) |
| Sealed values (age-encrypted files) | Spark: `/srv/secrets/prod/` (root-only) | No |
| Backup of sealed values | NAS via restic (ADR-0013) + offline USB | No |
| Dev values (dev-only passwords) | Desktop/WSL2: `~/.aetherspark/secrets/dev/`, own dev key | No |

### Change 1 — two keys; dev and prod separated
- Prod files are encrypted to **two age recipients**: the Spark host key and an **offline recovery key** (USB in a safe). Losing the Spark never loses secrets.
- Dev files use a separate dev key; the dev key can never open prod files. Dev values are never reused in prod.

### Change 2 — files, not environment variables
- At `just up` the sealed values are decrypted into **tmpfs** and mounted as Docker secrets (`/run/secrets/<name>`), one file per secret, readable only by the consuming service.
- .NET services read them with the key-per-file configuration provider; Python jobs read the file path from configuration. No secret values in environment variables, Compose files, images or logs.
- Deliberate difference from Decisya's "secrets from environment variables" rule: stricter, because env vars leak via `docker inspect`, crash dumps and debug logs.

### Change 3 — machine-generated
- `just secrets generate` creates every manifest entry that is missing (random, length per manifest rule) and seals it immediately; `just secrets rotate <name>` replaces one.
- Hand-managed secrets: only the two age keys and the Keycloak break-glass password (ADR-0009).

### Checks
- CI: every secret referenced by Compose/AppHost exists in the manifest and vice versa; gitleaks on every commit; `/srv/secrets` paths and `*.age` files are git-ignored and blocked by the dev-agent hooks (`secret_guard.py`, ADR-0016).
- Rotation calendar in `docs/runbooks/secrets.md`.

## Consequences
- Good: no secret material on GitHub, encrypted or not; leaked git history exposes nothing; rebuild = clone + restore `/srv/secrets` from NAS (or regenerate); Spark loss recoverable with the offline key.
- Bad: secrets are not versioned with code (a new secret = a manifest entry + `just secrets generate`); the NAS backup becomes the only copy besides the Spark — covered by the restore drill (NFR-OPS-03).

## Alternatives considered
- sops + age files in git (original proposal): rejected — sealed secrets leave the LAN and stay in git history forever; history has no value for generated secrets.
- HashiCorp Vault / OpenBao: stronger (dynamic credentials), but another stateful service to run and unseal; revisit if dynamic DB credentials are needed.

## Amendment (2026-10-04) — infrastructure credentials
This ADR covers **application secrets**. Infrastructure credentials (router and NAS administrators, NAS service accounts, notification mail, the desktop-phase and offline-copy restic repositories) are managed by hand by the owner in an end-to-end encrypted password manager. They are never stored in git or in ops files, and never used by platform services. Exception: maintenance keys that a scheduled job on the NAS needs are generated on the NAS and stay there (ADR-0013 Amendment 2). Second-factor seeds are kept on a separate device; 2FA backup codes and the offline-copy password are printed and stored off-site with the offline recovery key. An interactive admin session may hold a secret in a session variable and is closed afterwards; services never do (change 2).

## Amendment (2026-10-11) — developer credentials
Developer credentials, such as the read-only token for private build packages (ADR-0009 amendment 1), are neither application secrets nor infrastructure credentials. They grant the least access possible, expire, stay in the developer's credential store, reach builds only as build secrets in an interactive session, and are never used by services or dev agents. They are not listed in the manifest.

## Amendment (2026-10-19) — workstation backup key
The infrastructure credentials of the 2026-10-04 amendment also cover the workstation backup (ADR-0013 Amendment 5): its repository password and backup-server login. Second exception: the scheduled backup job on the desktop reads both from files encrypted with the OS data-protection API for the owner's account (user-scoped, machine-bound). The job is not a platform service. The stored copy is outside every project folder and is never written to a file in plain form. Dev agents are denied reading it (`secret_guard.py`, from WP2.6); until then only per-command approval protects it. Both are rotated if the desktop is suspected compromised.

## Note (2026-11-04, accepted 2026-11-04, with WP3.1/WP3.3) — where decrypted values live
Decrypted values live in one tmpfs folder per machine (mode 0700), the only source the Compose lint accepts for a secret: `/run/aetherspark/secrets/` on the Spark, `$XDG_RUNTIME_DIR/aetherspark/secrets/` (`/run/user/<uid>/…`) in WSL2 on the desktop, which needs no `sudo` (amended 2026-11-06 with the desktop proof). Compose file secrets are bind mounts whose `uid`, `gid` and `mode` Compose ignores outside Swarm, so `just up` sets each file's owner and mode on the host, and each file is mounted only into its service. Images without file support read a configuration file from that folder (Keycloak: `--config-file`), never an environment variable. On the desktop, a scripted proof shows the values stay memory-backed through WSL2 and Docker Desktop, with their permissions and cleanup, before any service depends on it ([compose-base.md](../architecture/compose-base.md)).

## Note (2026-11-06, accepted 2026-11-06, with WP3.3) — the manifest as JSON; the stack's user
- The manifest is `deploy/secrets.manifest.json`, not `.yaml`: the secrets tool (`deploy/secret-store/secret_store.py`) reads it with Python's standard library only, also in WSL2 and on the Spark, where no YAML library is installed. Content as decided: names, consumers, rotation and generator rules, no values; a derived entry builds a file from other secrets at unseal.
- Every service that reads a secret runs as the stack's user (the WSL user on the desktop, a dedicated user on the Spark), with files at 0400: Compose ignores a file secret's owner and mode outside Swarm, and the desktop cannot change a file's owner without `sudo`.
- The desktop proof (`tests/secret-store/desktop-proof.sh`) passed on 2026-11-06: Docker Desktop mounts the same tmpfs into the container, with no copy in the writable layer, on WSL2's disk or in Docker's metadata, and nothing after a WSL restart ([secrets.md](../runbooks/secrets.md)).

## Note (2026-11-11, proposed, with WP3.4) — entered secrets; the desktop-phase backup login; the dev key's off-line copy
With the backup job ([backup-job.md](../architecture/backup-job.md); exchange A-015):
- **Entered secrets.** A manifest entry may be *entered*: a value issued by another party, typed in once by the owner at a terminal (asked twice, never echoed, never from a pipe or an argument; tests use a file descriptor of their own) and sealed with the others. `generate` and `rotate` skip it; `unseal` skips a missing one and every derived file that names it, and names them.
- **The desktop-phase backup login.** The backup job uses the desktop-phase repository's login, entered, and a repository key of AetherSpark's own, generated. The login and the repository's original password stay in the owner's password manager as the record and the recovery path. The original password reaches a container only once, at `just backup key-add`, typed at restic's own prompt.
- **The dev key has an off-line copy**, kept with the recovery sheet, so that a lost desktop can be restored from the backup with the same root CA and data (the owner's decision of 2026-11-11). Regenerating the dev values stays the way out only if the key and its copy are both lost.
- *Replaces*, in change 3, "only the two age keys and the Keycloak break-glass password" with "the two age keys, the Keycloak break-glass password and entered secrets"; and, in the amendment of 2026-10-04, for the desktop-phase repository only, "never used by platform services": the backup job uses its login as an entered secret.
