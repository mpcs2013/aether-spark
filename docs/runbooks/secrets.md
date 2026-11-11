# Secrets — runbook (WP3.3)

How AetherSpark's secrets are created, sealed, opened into memory and rotated on the desktop. The rules are [ADR-0010](../adr/0010-secrets-sops-age.md) and the design note's "Secrets" section ([compose-base.md](../architecture/compose-base.md)); the Spark's production store follows with WP6.4.

**In plain words.** Every password the stack needs is made by a machine, never typed. It is stored sealed (encrypted with `age`) and opened only into a folder that lives in memory, just before the stack starts; that folder disappears when the stack stops or WSL restarts. Git holds only the list of names.

## What lives where (desktop)
| What | Where | Notes |
|---|---|---|
| The list (manifest): names, consumers, rotation, generator rule | `deploy/secrets.manifest.json` in git | No values |
| Sealed values | WSL2: `~/.aetherspark/secrets/dev/<name>.age` | Encrypted to the dev key |
| The dev key | WSL2: `~/.aetherspark/keys/dev.key`, mode 0600 | Opens dev values only, never production ones |
| Opened values | WSL2: `$XDG_RUNTIME_DIR/aetherspark/secrets/<name>`, folder 0700, files 0400 | In memory (tmpfs); gone after `clear` or `wsl --shutdown` |

**The dev key has an off-line copy** (from WP3.4, [backup-job.md](../architecture/backup-job.md)), kept with the recovery sheet: with it, a lost desktop is restored from the backup with the same CA and data. Only if both the key and its copy are lost, run `generate` again and reset the dev stack's volumes (the CA and the databases were made with the old values). Nothing from the desktop is ever used on the Spark.

## One-time setup in WSL2 (Ubuntu 24.04)
Run in a WSL2 terminal (`wsl -d Ubuntu-24.04`):

```bash
sudo apt-get update && sudo apt-get install -y age just
```

`sudo` asks for your WSL password. `age` seals and opens the values; `just` runs the recipes. Docker comes from Docker Desktop's WSL integration (Settings → Resources → WSL integration).

**Prove the in-memory folder once** on this machine (and after Docker Desktop or WSL updates):

```bash
bash tests/secret-store/desktop-proof.sh
```

Every line must say `PASS`. Then, in PowerShell, `wsl --shutdown`, open WSL again, and:

```bash
bash tests/secret-store/desktop-proof.sh --after-restart
```

It must say `PASS  5b`. The proof never prints a secret.

## Everyday commands (WSL2, in the repository)
| Command | What it does |
|---|---|
| `just secrets generate` | Creates every secret in the manifest that the store lacks, sealed; creates the dev key on first use. Changes nothing that exists |
| `just secrets unseal` | Opens every secret into the in-memory folder (refuses if that folder is not memory-backed) |
| `just secrets clear` | Empties and removes the in-memory folder |
| `just secrets rotate <name>` | Replaces one secret's value; then `unseal` and restart the services that read it |
| `just secrets check <rendered.json>` | CI's check: the manifest and the rendered Compose agree |

`just up` and `just down` call `generate`, `unseal` and `clear` themselves (from WP3.1's first services).

## Adding a secret
1. Add an entry to `deploy/secrets.manifest.json`:
   - `name`: lower-case, the file name the service sees under `/run/secrets/`;
   - `consumers`: the Compose services allowed to read it; a tool on the host that reads it from the in-memory folder is named `host:<tool>` (for example `host:keycloak-admin`), and a secret read only by host tools needs no Compose file;
   - `rotation_days`: optional;
   - either `generator` (`{"type": "random", "bytes": 32}`, at least 16 bytes) or `derived`, a file built from other secrets, such as `"db-password={keycloak-db-password}\n"`.
2. In the Compose file: a top-level secret with the same name and `file:` in the in-memory folder; only its consumers list it.
3. The lint refuses any other source, and CI's check refuses a secret outside the manifest or an entry nobody uses.

## Rules that never bend
- **No value on screen, in a file on disk, in an argument or in an environment variable.** Images that cannot read a file get a configuration file from the in-memory folder instead (Keycloak: `--config-file`).
- **Every service that reads a secret runs as the stack's user** (on the desktop, your WSL user, uid 1000), because the files are 0400 and belong to that user; each file is mounted only into its own service.
- **Hand-made secrets are only two:** the age keys and the Keycloak break-glass password (ADR-0010 change 3).

## When something is wrong
| Situation | What you see | What to do |
|---|---|---|
| Docker, the desktop or WSL restarted while the stack was up (no `just down` before) | `just up` (at unseal) or `just down` (at clear) stops: `… holds folders Docker created where the secret files belong …`, then the command to run | The folders are empty and hold no secret. Run the command the message gives, which removes the folder it names as root: on the desktop from PowerShell (`wsl -d <your distribution> -u root -- rm -r '<the folder>'`), on the Spark with `sudo`. Then `just up` again, or nothing more after `just down`. Habit: `just down` before shutting the desktop down |

Why it happens: the in-memory folder is empty after a restart of WSL or Docker. Docker then created an empty root-owned folder at each missing secret file's path, and often the folder above them too. Since 2026-11-09 the services that read secrets no longer restart by themselves (`restart: "no"`), yet on 2026-11-10 Docker still made the folders for exactly those services' secrets at its restart, while they stayed stopped; the cause is not known. So the message names the topmost such folder, and removing it is enough.

## Concepts
- **age:** a small encryption tool. A key pair: the public half seals, the private half (the dev key) opens.
- **tmpfs:** a folder in memory. Nothing written there reaches a disk; a restart empties it.
- **Manifest:** the list of secrets by name and purpose; safe to publish because it holds no values.
