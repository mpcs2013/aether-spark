# Backup job and restore drill (WP3.4)

**Status:** proposed 2026-11-11, for the owner's acceptance together with the notes to ADR-0003, ADR-0010 and ADR-0013 (Document changes). Reviewed by the read-only `architect` (consistent with amendments) and `security-reviewer` (go with fixes) on 2026-11-11; their findings are folded in. Builds on [ADR-0013](../adr/0013-backup-restore.md) (restic to the NAS, append-only; amendment 8: the CA volume and Keycloak's database), [ADR-0010](../adr/0010-secrets-sops-age.md) (secrets), the [egress design](egress.md) (the `backup` network) and [compose-base.md](compose-base.md) (the stack). The home infrastructure's side is agreed in exchange A-012 (requirements 7–9, 12, 15) and A-015.

## In plain words
Once a day, when you start the stack, AetherSpark copies what cannot be rebuilt (its certificate authority, Keycloak's users and settings, and its encrypted secrets) to the backup server on the NAS. The NAS accepts new copies and never lets AetherSpark delete one. If the desktop itself is lost, the NAS copy plus a few items you keep off-line (a key and two logins) bring everything back, the same certificate authority included. A restore drill proves it.

## Goal and scope
- **In scope (desktop phase):** the job, its secrets, `just backup`, the daily run from `just up`, `just restore`, the restore drill in CI on every stack test and by the owner from the NAS, the runbook `docs/runbooks/restore-drill.md`. Gate G3's "restore drill passes".
- **Later:** the Spark's nightly timer and its own repository (cutover, WP6.4/6.6); TLS to the backup target (WP6.6); the local folder feed of pinned `Ostium.*` packages fills its path with WP3.5; the data Postgres and ClickHouse with P4a.
- **Not AetherSpark's:** retention, checks, the offline copies and the freshness alert run on the NAS (the home infrastructure, A-012, A-015).

## Owner's decisions (2026-11-11)
1. **When it runs:** `just up` makes a snapshot once a day, after the stack is healthy, if none was made that day; `just backup` makes one by hand. On the Spark a nightly timer replaces this at cutover.
2. **How the restore is proven:** CI backs up to a stand-in backup server on every stack test, proves a delete is refused, and restores onto a clean state (volumes, sealed store and secrets folder removed) and signs in; the owner runs the real drill from the NAS in a fresh WSL distribution, recorded in the runbook. That drill also proves Gate G3's "`just up` from clean WSL2".
3. **A lost desktop restores everything:** an off-line copy of the dev key is kept with the recovery sheet, and the backup login and the repository's address in the password manager. The same root CA, users and security keys come back.
4. **The quarterly drill starts with Gate G3** (on the desktop until cutover, then on the Spark).

## What is backed up
Every snapshot has the same four paths from the first one on (A-015, condition c):

| Path | Item | How |
|---|---|---|
| `/backup/ca` | The CA volume (`step-ca`): the intermediate key (encrypted), the CA's configuration and its database of issued certificates. A restore keeps the trusted root (ADR-0013 amendment 8) | step-ca is stopped only while its volume is **copied into the in-memory staging folder** (its database, BadgerDB, is not safe to copy while it writes), then started again and checked healthy before the upload begins |
| `/backup/keycloak` | Keycloak's database | `pg_dump` (custom format) run **inside the running `postgres-auth` container** (`docker compose exec`): it connects as the `keycloak` role over TLS to its own address on `db-auth`, as `pg_hba` already allows, with the password it already holds. The dump streams to the staging folder. No other container joins `db-auth` and no secret gets a new consumer |
| `/backup/sealed` | The sealed secrets store (ADR-0010): every generated and entered secret, age-encrypted | Files, read-only. Never the in-memory secrets folder, never an age key |
| `/backup/feed` | The local folder feed of pinned `Ostium.*` packages (ADR-0009 amendment 1) | Empty until WP3.5 fills it |

- **The staging folder** is in memory, next to the secrets folder (desktop `$XDG_RUNTIME_DIR/aetherspark/backup/`, Spark `/run/aetherspark/backup/`), mode 0700, checked to be a tmpfs like the secrets folder, and removed when the job ends, however it ends. It is a folder of the host's memory, bind-mounted, as the secrets folder is (proven memory-backed in Docker Desktop, compose-base.md); a Docker tmpfs volume would be emptied as soon as the container that filled it exits.
- **Not backed up** (ADR-0013 amendment 8): Traefik's ACME store and every `*-tls` volume (re-issued at `just up`), Keycloak's own data folder (a tmpfs), the images (rebuilt from the repository).
- **Size:** about a megabyte today; well under 100 MB with the package feed.

## How the job runs
- **A one-shot container** with restic 0.19.1, the official image pinned by digest, scanned like every other image (`deploy/images/targets.json`); if the scan forces it, a local build as for step-ca. Compose profile `backup`, so `just up` never starts it as a service; `restart: "no"`; read-only root with a small tmpfs for restic's cache; no capabilities; as the stack's user.
- **Its only network is `backup`** (bridge `as-backup`, policy role `restic`), the one network with a way out to the backup target. On the Spark and in CI the host rules let it reach only the backup target's address and port (egress E9); on the desktop there is no layer 3 (the egress design's accepted residual).
- **No name lookups:** the target is reached by its address (on the Spark, layer 3 drops DNS from `backup`).
- **Selection (A-015, conditions b and c):** every snapshot carries the machine's host name (`aetherspark-desktop`; at cutover `aetherspark-spark`), the tag `aetherspark-stack` and the four paths above. Checks select by host and tag, never plain `latest`: the desktop-phase repository holds a test snapshot dated 2030.
- **Append-only (A-015, condition a):** the job only adds and reads. It never runs `forget` or `prune`; retention is the NAS's.
- **Once a day from `just up`:** after the stack is healthy, `just up` runs the job if the local record of the last good snapshot is not from today (dates in UTC). The record is a file on the desktop, not a query of the repository, so a forged snapshot cannot suppress the backup. If the target cannot be reached or an entered secret is still missing, `just up` prints a warning and still succeeds, like `keycloak apply --if-ready`.
- **Order of one run:** check that the target answers; dump Keycloak; stop step-ca, copy its volume, start it, wait until healthy; upload; check that the new snapshot is listed with the four paths; write the local record. On any exit (error, interrupt) a trap starts step-ca if it is stopped and removes the staging folder.

## Secrets
- **What restic needs:** the repository's location with the backup login, and a password for one of the repository's keys. Both are read from files in the in-memory secrets folder (`--repository-file`, `--password-file`), mounted into the job only: nothing in `docker inspect`, a process's arguments or its environment (as test C5 requires of the services).
- **A new kind: entered.** The backup login is issued by the home infrastructure, not generated here (A-015). The owner types it once at a terminal (`just secrets enter <name>`, asked twice, never echoed; never from a pipe or an argument), and it is sealed with the others. `generate` and `rotate` skip entered secrets; `unseal` skips a missing one and every derived file that names it, and says so by name, so `just up` from a clean clone still works (C1). CI enters its test values through a file descriptor that only the tests use. Entered here: the login's name and password, and the target's address and repository path. The repository file is derived from them, with the name and the password percent-encoded; the address and the path are checked for allowed characters only.
- **AetherSpark's own key, added before the first snapshot.** The repository was created with the owner's password, which stays in the owner's password manager as the recovery key (A-015). `just backup key-add` runs `restic key add` in an interactive container: restic asks for the existing password on the terminal (no file), and the new key's password comes from the sealed store. AetherSpark reports the key's ID in A-015.
- **The owner's recovery items (decision 3):** the dev key's off-line copy on the recovery sheet; the backup login, the repository's address and path, and the repository's original password in the password manager; the root CA's fingerprint written down. The repository password is the home infrastructure's (generated, A-012 requirement 12).

## Restore
- **`just restore`** (the stack down) refuses to run if the `step-ca` or `postgres-auth-data` volume is not empty. With `--replace` it first copies both aside (`<volume>-before-restore`) and names the copies.
- **Which snapshot:** by default the newest of this host and tag, after showing the candidates; it refuses one dated in the future. **After an incident** (the desktop or a backup key possibly compromised) it takes an explicit snapshot ID, chosen from before the incident and checked against the time the NAS received it (the home infrastructure's records).
- **Steps:** restore the snapshot into the staging folder; copy the CA files into the empty `step-ca` volume; start `postgres-auth` with what it needs (step-ca, the bootstrap step, its renewer); load the dump inside `postgres-auth` as the `keycloak` role (`--no-owner --no-acl --single-transaction --exit-on-error`); remove the staging folder; leave the rest to `just up`.
- **The sealed store** is restored next to the live one (`…/secrets/dev.restored-<date>`), never over it; on a machine without a store it is restored in place. Unsealing it needs the dev key (on a new machine: its off-line copy).
- **After the restore:** the root CA's fingerprint (`just ca root`) is compared with the written-down value; a difference means stop.
- **What a restore keeps:** the same root CA (no re-trust in browsers), the same users and second factors (security keys keep working: the credential is the key's, Keycloak keeps only its public part), the same realm.

## Tests
| # | What | Where |
|---|---|---|
| B1 | A snapshot holds exactly the four paths under the fixed host and tag; no `*-tls` volume, no file of the in-memory secrets folder, no age key | CI, desktop |
| B2 | No secret in `docker inspect`, arguments or environment of the job, nor of the dump's `exec` | CI |
| B3 | The job's login cannot delete: in CI `forget` and an HTTP DELETE of a snapshot are refused by the append-only stand-in; on the NAS one HTTP DELETE of the job's own new snapshot is refused (never `forget` there, A-015 condition a) | CI; NAS at the acceptance run |
| B4 | A second `just up` the same day makes no snapshot; with the stand-in stopped, `just up` warns and succeeds | CI |
| B5 | step-ca is healthy before the upload starts and after the job; an interrupted job leaves step-ca running and no staging folder | CI |
| B6 | **The drill:** volumes, sealed store and secrets folder removed; the repository opened with a test "owner" key; the store restored and unsealed with the test's copy of the dev key; `just restore`, `just up` healthy; the root fingerprint unchanged; a user created before the backup signs in with password and app code; `keycloak apply` reports the realm as the repository says. `just restore` refuses non-empty volumes and a future-dated snapshot | CI on every stack test; by the owner from the NAS in a fresh WSL distribution |
| B7 | The job joins only `backup` (lint L1); with the host rules, it reaches the backup target and nothing else, and the drops are logged (egress E9) | CI |

**CI's stand-in:** `restic/rest-server` 0.14.0 (pinned, scanned), append-only with private repositories and a login made by the test, as on the NAS, its password file readable only by its own user. It runs **outside Docker's bridges**, so the job's traffic crosses the host rules as on the Spark: in CI in a network namespace at the test settings' NAS address and port (`tests/stack/layer3.sh` already sets them; as `tests/egress/layer3.sh` does for E9). On the desktop, where the stack test runs without layer 3, it runs as a container on a test network of its own that the job joins for the test only.

## Order of work (pull requests)
1. **This design note** with the notes to ADR-0003, ADR-0010 and ADR-0013 and the change to the secrets runbook, accepted by the owner.
2. **Entered secrets:** the manifest kind, `just secrets enter`, `unseal` skipping missing entered secrets, the derived repository file with percent-encoding and checks; tests.
3. **The job and the restore:** the restic image and the stand-in in `targets.json`; `role_images` for `restic`; the `backup` network in the stack's Compose file; the staging folder; `just backup`, `just backup key-add`, `just restore`, the daily run in `just up`; tests B1–B7 in the stack test; the runbook `restore-drill.md`.
4. **On the desktop, by hand:**
   - once the home infrastructure reports the scratch repository (A-015 point 2), the owner enters its login under the same entry names, and the job is tried against it;
   - then the acceptance run against the desktop-phase repository, in this order: enter the real login (replacing the scratch values; their sealed files are deleted), `key-add`, one snapshot, one refused HTTP DELETE;
   - the key's ID and the build's end reported in A-015, so the home infrastructure removes the scratch repository and its login.
5. **The owner's drill** from the NAS in a fresh WSL distribution, recorded in `restore-drill.md`; the first scheduled run reported in A-015 (the freshness check starts); the roadmap's Gate G3 items "restore drill passes" and "`just up` from clean WSL2" closed.

## Document changes (in this note's pull request)
- **ADR-0003, a dated note:** the restic job is a one-shot service in its own profile, `backup`, started once a day by `just up` and by `just backup`. *Replaces*, in the note of 2026-10-23, "`ops` (observability, restic)" with "`ops` (observability)".
- **ADR-0010, a dated note** (entered secrets; the desktop-phase backup login; the dev key's off-line copy). *Replaces*, in change 3, "only the two age keys and the Keycloak break-glass password" with "the two age keys, the Keycloak break-glass password and entered secrets"; and, in the amendment of 2026-10-04, for the desktop-phase repository only, "never used by platform services".
- **ADR-0013, a dated note:** the desktop-phase job as above; the refused delete proven on the NAS by one delete request; restores by explicit snapshot ID after an incident; the incident steps (Residual risks); the quarterly drill from Gate G3; the `D:` fallback dropped (the NAS target and Amendment 5 replace it).
- **`docs/runbooks/secrets.md`:** "dev values are throw-away" is replaced: the dev key has an off-line copy (decision 3); regenerating stays the way out only if both the key and its copy are lost.
- **compose-base.md:** its "out of scope: the backup job (WP3.4)" points here.
- **With PR 3** (code): `deploy/compose/policy.json` (`role_images.restic`), `deploy/images/targets.json`, the manifest.

## Residual risks
- **The desktop backs up only on days the stack runs** (decision 1): the NAS's 7-day freshness check mails the owner otherwise (A-015). NFR-OPS-02's 24-hour RPO is met from cutover (the Spark's nightly run); the desktop holds development data.
- **The repository holds Keycloak's most sensitive data, encrypted:** password hashes, the app-code (TOTP) seeds, the realm's token-signing private keys and the client secrets. Anyone holding any repository key can read them: the NAS's maintenance keys (threat T16), a compromised desktop, anyone with the owner's password. All keys open one master key that cannot be changed, so replacing a key does not end an exposure. **After a compromise of the desktop or of any backup key:** the home infrastructure creates a new repository and login; AetherSpark rotates the realm's signing keys and the client secrets, and every user sets up a new app code. Asked of the home infrastructure: an alert when the repository's list of keys changes.
- **A forged snapshot:** a compromised desktop can add snapshots with any date, under the right host and tag. Restores after an incident take an explicit ID from before it, checked against the NAS's arrival times; the root fingerprint is compared after every restore.
- **Plain HTTP on the Home network until cutover** (ADR-0013): restic encrypts and authenticates the content; a Home device could see the login on the wire, which only adds and reads encrypted snapshots. A device in between that drops uploads is caught by the freshness check.
- **The plaintext dump in memory:** for the seconds of a run, in a tmpfs on the desktop that WSL2 may swap to its swap file on the Windows disk, like the secrets folder.
- **The desktop has no layer 3:** the job could reach other hosts from the desktop, as every container there can (accepted for development data).

## Concepts
- **restic:** a backup program. It cuts files into pieces, encrypts them on the client and sends only pieces the repository does not have yet. A *snapshot* is one backup run; a *repository* holds many.
- **Repository keys:** a repository can be opened with any of several passwords, each a *key*. Adding one does not change the others; the owner's stays the recovery key. All keys open the same master key inside, which never changes.
- **Append-only:** the backup server accepts new data and refuses to delete or overwrite. A stolen login can add junk, never destroy what is there; the NAS's guard notices floods of new snapshots.
- **Retention:** which old snapshots are kept (all for 30 days, then weekly, then monthly). It runs on the NAS with a key the job never holds.
- **Staging folder:** a short-lived folder in memory where the job puts the copies it sends, so nothing plaintext lands on a disk.
- **Restore drill:** proving a backup by actually restoring it, onto a clean machine, and using the result, not just by listing it.
