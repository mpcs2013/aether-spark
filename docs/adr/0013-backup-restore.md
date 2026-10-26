# ADR-0013: Backup & restore — restic to the NAS, append-only, 3-2-1
- **Status:** Accepted · **Date:** 2026-10-04 (answers roadmap Q2; NAS details in `ops.local/site.md`) · **Amendments 1–2** Accepted 2026-10-04 · **Amendment 3** Accepted 2026-10-11 · **Amendment 4** Accepted 2026-10-12 (written 2026-10-11) · **Amendment 5** Accepted 2026-10-19 · **Amendment 6** Accepted 2026-10-21 (amended 2026-10-21 after the code review, and 2026-10-22, #22) · **Amendment 7** Accepted 2026-10-26 (UPS shutdown signal)

## In plain words
Every night the Spark sends a backup to the NAS. The NAS accepts new backups but never lets the Spark delete old ones. Once a week a copy goes to a USB disk that is unplugged afterwards.

## Decision
### Copies (3-2-1)
| Copy | Where | Medium |
|---|---|---|
| 1 — live | DGX Spark (`/srv`) | NVMe |
| 2 — nightly | home NAS, **dedicated SSD volume** (≥ 400 GB), shared folder `aetherspark-backup` | SATA SSD |
| 3 — weekly, offline + off-site | **Two** external USB hard disks (2 TB each, to buy), rotated monthly: one at home (weekly copy, unplugged between runs), one kept away from home (amendment 1, 2026-10-04). May also hold other NAS data chosen by the owner, but never another project's unencrypted app data | HDD |

### How
- **restic** with the **rest-server** container on the NAS (the NAS container runtime), started with `--append-only`: the Spark can add snapshots but cannot delete or rewrite them.
- **Prune/forget** runs on a schedule from a separate context with a **separate credential** the Spark does not hold. Retention: 7 daily, 4 weekly, 12 monthly *(implemented as time windows, see Amendment 2)*.
- **Content:** `pg_dump` (custom format), ClickHouse `BACKUP`, configs, sealed secrets from `/srv/secrets` (ADR-0010), Keycloak realm export, Grafana dashboards. **Excluded:** model weights, raw EDGAR downloads, licensed price data (re-downloadable; a manifest with checksums is backed up instead).
- **Sizing:** first backup ≈ 30–50 GB; with retention ≈ 100–150 GB (fits the SSD volume).
- **Encryption:** restic repository password + the sealed secrets stay encrypted; repository password stored like the other generated secrets, recovery copy with the offline age key (ADR-0010).

### Network
- NAS **port 1** stays on Home; NAS **port 2** joins the AetherSpark network (VLAN 20) on the router. All four router LAN ports are then used (desktop, NAS 1, NAS 2, Spark); a small 1 GbE switch on the Home side if more wired devices are needed.
- NAS firewall: rest-server port reachable **only from the Spark's IP** on port 2; the NAS admin UI not reachable from VLAN 20. IP forwarding on the NAS stays off (the NAS never routes between networks). *(Amended: see Amendments 2 and 4.)*

### Verification
- Nightly: `restic check` (structure) + alert on failure (ADR-0011 change 3).
- Monthly: `restic check --read-data-subset=5%`.
- **Quarterly restore drill** (NFR-OPS-03): restore to a clean machine/VM, start the stack, run smoke tests; result recorded in `docs/runbooks/restore-drill.md`.

### Desktop phase (Track A)
Desktop-phase backups of AetherSpark's own test data go to the same NAS target (tests the real path early); local HDD `D:` as fallback. The desktop's local-only data: Amendment 5.

## Consequences
- Good: a compromised Spark cannot destroy its backups; one copy is always offline; no new NAS hardware.
- Bad: the backup volume is a single SSD without redundancy (acceptable: it is a copy, and copy 3 exists); the NAS is connected to two networks (mitigated by NAS firewall and no routing); the NAS is an older model — if NAS operating-system security updates stop, replace the backup target (design unaffected).

## Amendment 2 (2026-10-04) — the NAS as a dual-homed container host
Found while reviewing the site runbook (read-only `infra-admin`, `architect` and `security-reviewer` agents).
- **Routing.** The container runtime on the NAS switches IP forwarding on. "IP forwarding stays off" is replaced by: the NAS must still never route between its networks. Controls: forwarding policy default-deny; packet-filter drops for traffic that arrives on one interface but is addressed to the other interface's address; only the backup port, from the AetherSpark network, reaches the backup container. A boot-time task installs these rules and the nightly maintenance job checks them; a failure raises an alert. *(Controls replaced by Amendment 4; the goal stands.)*
- **Source filtering.** *(Superseded by Amendment 4: on the NAS, published ports go through the userland proxy, so the NAS firewall does filter them.)* The NAS firewall does not filter ports published by containers. "Reachable only from the Spark's IP" is enforced by a packet-filter rule in front of the container network. Further layers: the port is published on the NAS's AetherSpark address only, the network is wired with no SSID, per-repository authentication, append-only mode.
- **Desktop phase.** A temporary second binding on the NAS's Home address serves only the desktop's repository. It is removed at cutover.
- **Transport.** Plain HTTP inside the AetherSpark network during the desktop phase (restic encrypts and authenticates content). TLS with an internal-CA certificate from cutover (NFR-SEC-05, roadmap WP6.6).
- **Retention against fake snapshots.** An append-only client cannot delete, but it can upload snapshots with forged timestamps that steer a calendar-based `forget`. Retention therefore uses time windows (`--keep-within`: everything for 30 days, weekly for 3 months, monthly for 1 year), as restic recommends for append-only repositories. The maintenance and offline-copy jobs also stop and alert when more new snapshots arrive in a day than the schedule can produce.
- **Keys on the NAS (accepted risk).** Prune, check and the offline copy run on the NAS with their own keys stored there. A NAS administrator or root compromise can therefore **read** backup content, not only delete it (threat model T16). NAS administrator accounts are for humans only, use 2FA, are never exposed through a vendor relay or remote access, and are never used by automated deployments, including other projects' workloads on the same NAS.
- **Offline-copy password.** It must be recoverable without the NAS or the Spark: password manager plus a printed copy stored off-site with the offline recovery key (ADR-0010).

## Amendment 3 (2026-10-11) — private build packages
Backup content also includes the local folder feed of pinned private packages (ADR-0009 amendment 1), so a release tag can be rebuilt without the private feed.

## Amendment 4 (2026-10-11) — network controls, as measured on the NAS
Found while running the site runbook; reviewed twice by the `security-reviewer` agent (accept with changes). Amendment 2 assumed that the container runtime's packet-filter chains sit in the forwarding path and that a raw packet-filter table exists. On the NAS neither holds: the NAS operating system owns the forwarding chain and rebuilds it on every firewall change, the runtime's chains are not consulted, there is no raw table, and published ports are served by the runtime's userland proxy. Inbound traffic to the backup port is therefore traffic to the NAS itself.
- **Inbound (replaces "Source filtering" in Amendment 2).** The NAS's own per-interface firewall is the main control. On the AetherSpark interface it allows only the backup port, from the AetherSpark network (from cutover: from the Spark only), and denies everything else. On the Home interface the backup port is allowed from the desktop only, during the desktop phase. Invariant: no other service publishes, on any NAS address, a port that the AetherSpark interface allows.
- **Routing (replaces the packet-filter drops in Amendment 2).** A boot-time task switches IP forwarding off for the AetherSpark interface, so the NAS routes nothing that arrives from that network, independent of the firewall.
- **Outbound.** The backup container's network runs without address translation, so the container cannot complete a connection to any host beyond the NAS itself. Maintenance containers (prune, check, offline copy) run without a network.
- **Backup layer.** Source and destination filter rules in the runtime's own chain stay installed, for the case that the runtime's chains are hooked in again.
- **Checks.** The nightly maintenance job verifies the AetherSpark interface's firewall rules, the forwarding setting, the missing address translation, the listening addresses of the backup port and the backup-layer rules. A failure raises an alert.
- **Accepted residual risks.** Until cutover, a host in the AetherSpark network that routes via the NAS reaches the same backup service on its Home address (same authentication). The container can still send one-way UDP/ICMP (no replies; content is encrypted by restic on the client) and can reach the NAS's own services over its internal bridge (controls: human-only admins with 2FA, SSH per session). A changed firewall or forwarding setting is noticed within a day. The runtime's process-count limit is not enforced on this NAS operating system; the memory limit is. Containers never see the real client address, so co-hosted workloads cannot filter by client IP: the NAS firewall is their control as well.
- **Verified (desktop phase, 2026-10-11).** Deleting through the backup service is refused (HTTP 403); a full restore is byte-identical; the fake-snapshot guard stops the maintenance job without deleting anything; a repository check with the NAS-side key finds no errors.

## Amendment 5 (2026-10-19) — desktop data in a shared workstation backup
The desktop holds data that exists nowhere else and is not in git: `ops.local/` (site details, ADR-0004), the router and NAS configuration backups, and from WP2.6 the git-ignored folder with development-usage records. The owner's other projects on the same desktop have the same kind of folders. Until now this data went into the desktop-phase repository by hand; that repository's scheduled job only starts with WP3.4, and its binding on the NAS Home address is removed at cutover (Amendment 2). Reviewed by the read-only `architect` and `security-reviewer` agents (2026-10-19).
- **One workstation backup for all projects.** The owner runs it, not AetherSpark. It covers the desktop-only folders of every project. Runtime data on a server keeps its own backup (for the Spark: this ADR).
- **Target.** A second restic rest-server on the NAS, append-only, with its own shared folder on a different volume from `aetherspark-backup`, its own repository, client login and prune key. It is published on the NAS Home address only, and the NAS firewall allows it from the desktop only. On the NAS it shares no port, folder, account, container network or key with the AetherSpark backup, and the Amendment 4 invariant holds: the AetherSpark interface allows none of its ports. As in Amendment 4, containers on the NAS can reach it over their bridge; per-repository authentication and append-only mode are the controls there. Its maintenance runs on the NAS like the AetherSpark one: time-window retention, the fake-snapshot guard, checks and failure mail. The nightly network check covers its port and container network as well, and compares the NAS's full set of inbound firewall rules with a recorded baseline. A folder quota keeps it and the other workloads on its volume from filling the volume for each other. Plain HTTP on the Home network until WP6.6, then TLS with an internal-CA certificate, as for the AetherSpark target.
- **Copies.** The weekly offline copy includes it as an encrypted repository under the shared offline-copy password; the copy-3 rule excludes only unencrypted app data. Its restore drill runs quarterly with the AetherSpark one. It is not part of NFR-OPS-03; its drill result is recorded in the local runbook.
- **Schedule.** Daily in the evening while the desktop is on, with catch-up after a missed start. The freshness check allows for days the desktop stays off.
- **Keys.** The repository password and the backup-server login are kept in the password manager; the repository password is also on the off-site recovery sheet (ADR-0010). The scheduled job reads both from files encrypted with the OS data-protection API for the owner's account (ADR-0010 amendment 2026-10-19), so any program running as the owner can use them. Dev agents are denied reading them by the secret guard (`secret_guard.py`, from WP2.6); until then only per-command approval protects them. They allow reading every snapshot of every project, history included, and adding snapshots (the fake-snapshot guard catches forged ones). They cannot delete or rewrite snapshots.
- **Content.** A folder list kept with the job; once each project's shared-core configuration names its git-ignored data folder, the list is read from there. Device configuration backups contain credential material and are kept only inside the encrypted repository. Never a project's secret files (sealed values, age keys, `.env`), never the stored job credentials, and never repositories that are already in git.
- **Order.** The workstation backup is set up first: until then the desktop's local-only data has no scheduled backup. It must be in place before WP2.6 (first usage records) and before WP6.6 (cutover). From then on the desktop-phase repository holds only AetherSpark's own test data until cutover.
- **Site details** (volume, port, accounts, quota, tasks, schedule) are in the local site files.
- Bad: one more container and one more port on the NAS, on the Home side; one repository holds every project's local-only data, so a desktop or NAS-administrator compromise reaches all projects (threat T16).

## Amendment 6 (2026-10-21) — usage-capture copy job (exchange G-004)
From WP2.6, every project on the shared core writes one usage capture per issue end into its git-ignored `usage.local/` folder (exchange G-003). Greenlight needs those captures, and no Claude session may read another project's folder. A one-way copy job next to the workstation backup (Amendment 5) brings them into Greenlight's local intake folder.
- **What it copies.** Daily, on the workstation backup's schedule: only regular files named `capture-*.json` directly in each listed folder, each at most 1 MiB, into the intake folder for that project id. Subfolders, symbolic links, junctions, other reparse points and larger files are refused and logged. Never the capture ledger, the status-tracking records or reports, or anything else.
- **Write-once.** It only reads on the source side and never deletes or changes anything there. A file already in the intake with the same SHA-256 is skipped; a known name with a different hash stops the run with an error. Every run logs the files copied, skipped and refused, with their hashes.
- **Folder list.** Project id → capture folder, in the local site files next to the workstation backup's folder list (Amendment 5, Content).
- **Code.** AetherSpark writes and maintains it: one Python file, standard library only, with no data and no paths, in `deploy/workstation/`, reviewed per pull request and tested on fixtures only. No Claude session runs it against a project folder.
- **What runs.** The owner copies the reviewed file into a folder of their own, outside every project and every session's working or additional directories, and records its SHA-256 from the reviewed commit. The scheduled task runs under the owner's account, checks that hash before each run, stops on a mismatch, and starts Python in isolated mode (`-I`), so no module from the working folder or the user site is loaded. A new version is reviewed, copied and recorded the same way.
- **Start.** When the first project writes captures, after the Norma release that ships the capture (G-004 point 5). AetherSpark's folder joins at WP2.6.
- Bad: one more owner-run job that reads every project's capture folder with the owner's rights (threat T17).

*Amended 2026-10-21, after the `security-reviewer` review of the job's code:*
- **Where it runs.** A folder of the owner's own is only a Claude Code permission boundary: a session running Bash under the same account could change the job, the recorded hash in the task, or a per-user Python. The job, its wrapper and its folder list therefore sit in a folder only Administrators can write; the interpreter is an all-users Python under Program Files, started with `-I -S`; the task is registered from an elevated shell, and an unelevated refusal test (job folder, interpreter, task) runs at setup and after Windows updates.
- **Where the hash comes from.** The file and its SHA-256 come from GitHub at the reviewed merge commit, checked to be on `main`, never from the local clone, whose git configuration a session can change.
- **Content.** Besides the file rules, the job checks every capture against G-004 format 1.0.0 and its cross-checks, with `project_id` equal to the folder list's and `capture_id` equal to the file name; free-text fields are capped more tightly than the schema. Anything else is refused, so only issue numbers and token counts can reach Greenlight.
- **Log.** Refused files are logged with their name and reason, not a hash, since they are never read; names that look like captures but do not match are counted. Copied files are made read-only in the intake.

*Amended 2026-10-22 (#22), after Norma and Greenlight reviewed the caps, and a second `security-reviewer` pass:*
- **Caps.** `agent` is a name or `plugin:name` (up to 100 characters); `model` may end in one bracketed suffix such as `[1m]` (up to 100 characters). The caps stay in the job until the capture format's next major version takes them in.
- **Refused markers.** Each refused file leaves a content-free marker in that project's intake folder, so Greenlight can list the gap: one per project, capture name and reason code, written once, holding the capture id (or none), a reason code from a fixed list and the first run time. It never holds content, a hash of the refused file, or a file name outside the capture form. Names outside the capture form share one marker per project and reason code, so a file name cannot carry data through the marker name. At most 100 refusals per project and run are marked; an existing marker must match the five fields, or the project stops.
- **Isolation.** An error in one project (a hash conflict, an invalid marker, an unexpected failure) stops that project only, and the run exits with an error; a crafted capture cannot stop the copy for the other projects.

## Amendment 7 (2026-10-26, accepted) — UPS shutdown signal
On a power cut the Spark shuts down cleanly before the UPS runs out, so its databases close cleanly. The UPS's status line goes to the backup target, which serves the UPS status over the network; the Spark's UPS client connects to it ([architecture/spark-host.md](../architecture/spark-host.md), flow H9). Agreed with the home-infrastructure owner, who designs and runs the backup target's side.
- **Inbound (extends Amendment 4):** on the AetherSpark interface the backup target's firewall allows the backup port and the UPS status port, both from the Spark only, and denies everything else. Amendment 4's invariant extends to that port: no other service uses it on any of the backup target's addresses.
- **Account:** the Spark's UPS account reads the status and receives the shutdown notice, never sends commands to the UPS.
- Port, protocol and account are in the local site files.

## Revisit trigger
The vendor ends security updates for the NAS operating system (dates in the local site file), either backup volume or the workstation folder quota goes above 80 %, or a co-hosted workload breaks its memory trigger.
