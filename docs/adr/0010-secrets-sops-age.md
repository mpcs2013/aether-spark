# ADR-0010: Secrets — generated, age-encrypted, kept off git
- **Status:** Accepted · **Date:** 2026-10-03 (replaces the Proposed "sops + age in git" version)

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
