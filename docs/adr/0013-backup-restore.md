# ADR-0013: Backup & restore — restic to the NAS, append-only, 3-2-1
- **Status:** Accepted · **Date:** 2026-10-04 (answers roadmap Q2; NAS details in `ops.local/site.md`)

## In plain words
Every night the Spark sends a backup to the NAS. The NAS accepts new backups but never lets the Spark delete old ones. Once a week a copy goes to a USB disk that is unplugged afterwards.

## Decision
### Copies (3-2-1)
| Copy | Where | Medium |
|---|---|---|
| 1 — live | DGX Spark (`/srv`) | NVMe |
| 2 — nightly | home NAS, **dedicated SSD volume** (≥ 400 GB), shared folder `aetherspark-backup` | SATA SSD |
| 3 — weekly, offline + off-site | **Two** external USB hard disks (2 TB each, to buy), rotated monthly: one at home (weekly copy, unplugged between runs), one kept away from home (amendment 2026-10-04). May also hold other NAS data chosen by the owner, but never another project's unencrypted app data | HDD |

### How
- **restic** with the **rest-server** container on the NAS (Container Manager), started with `--append-only`: the Spark can add snapshots but cannot delete or rewrite them.
- **Prune/forget** runs on a schedule from a separate context with a **separate credential** the Spark does not hold. Retention: 7 daily, 4 weekly, 12 monthly *(implemented as time windows, see Amendment 2)*.
- **Content:** `pg_dump` (custom format), ClickHouse `BACKUP`, configs, sealed secrets from `/srv/secrets` (ADR-0010), Keycloak realm export, Grafana dashboards. **Excluded:** model weights, raw EDGAR downloads, licensed price data (re-downloadable; a manifest with checksums is backed up instead).
- **Sizing:** first backup ≈ 30–50 GB; with retention ≈ 100–150 GB (fits the SSD volume).
- **Encryption:** restic repository password + the sealed secrets stay encrypted; repository password stored like the other generated secrets, recovery copy with the offline age key (ADR-0010).

### Network
- NAS **port 1** stays on Home; NAS **port 2** joins the AetherSpark network (VLAN 20) on the router. All four router LAN ports are then used (desktop, NAS 1, NAS 2, Spark); a small 1 GbE switch on the Home side if more wired devices are needed.
- NAS firewall: rest-server port reachable **only from the Spark's IP** on port 2; DSM admin not reachable from VLAN 20. DSM IP forwarding stays off (the NAS never routes between networks). *(Amended: see Amendment 2.)*

### Verification
- Nightly: `restic check` (structure) + alert on failure (ADR-0011 change 3).
- Monthly: `restic check --read-data-subset=5%`.
- **Quarterly restore drill** (NFR-OPS-03): restore to a clean machine/VM, start the stack, run smoke tests; result recorded in `docs/runbooks/restore-drill.md`.

### Desktop phase (Track A)
Desktop backups go to the same NAS target (tests the real path early); local HDD `D:` as fallback.

## Consequences
- Good: a compromised Spark cannot destroy its backups; one copy is always offline; no new NAS hardware.
- Bad: the backup volume is a single SSD without redundancy (acceptable: it is a copy, and copy 3 exists); the NAS is connected to two networks (mitigated by NAS firewall and no routing); the NAS is an older model — if DSM security updates stop, replace the backup target (design unaffected).

## Amendment 2 (2026-10-04) — the NAS as a dual-homed container host
Found while reviewing the site runbook (read-only `infra-admin`, `architect` and `security-reviewer` agents).
- **Routing.** The container runtime on the NAS switches IP forwarding on. "IP forwarding stays off" is replaced by: the NAS must still never route between its networks. Controls: forwarding policy default-deny; packet-filter drops for traffic that arrives on one interface but is addressed to the other interface's address; only the backup port, from the AetherSpark network, reaches the backup container. A boot-time task installs these rules and the nightly maintenance job checks them; a failure raises an alert.
- **Source filtering.** The NAS firewall does not filter ports published by containers. "Reachable only from the Spark's IP" is enforced by a packet-filter rule in front of the container network. Further layers: the port is published on the NAS's AetherSpark address only, the network is wired with no SSID, per-repository authentication, append-only mode.
- **Desktop phase.** A temporary second binding on the NAS's Home address serves only the desktop's repository. It is removed at cutover.
- **Transport.** Plain HTTP inside the AetherSpark network during the desktop phase (restic encrypts and authenticates content). TLS with an internal-CA certificate from cutover (NFR-SEC-05, roadmap WP6.6).
- **Retention against fake snapshots.** An append-only client cannot delete, but it can upload snapshots with forged timestamps that steer a calendar-based `forget`. Retention therefore uses time windows (`--keep-within`: everything for 30 days, weekly for 3 months, monthly for 1 year), as restic recommends for append-only repositories. The maintenance and offline-copy jobs also stop and alert when more new snapshots arrive in a day than the schedule can produce.
- **Keys on the NAS (accepted risk).** Prune, check and the offline copy run on the NAS with their own keys stored there. A NAS administrator or root compromise can therefore **read** backup content, not only delete it (threat model T16). NAS administrator accounts are for humans only, use 2FA, are never exposed through a vendor relay or remote access, and are never used by automated deployments, including other projects' workloads on the same NAS.
- **Offline-copy password.** It must be recoverable without the NAS or the Spark: password manager plus a printed copy stored off-site with the offline recovery key (ADR-0010).

## Revisit trigger
The vendor ends security updates for the NAS operating system (dates in the local site file), the backup volume goes above 80 %, or a co-hosted workload breaks its memory trigger.
