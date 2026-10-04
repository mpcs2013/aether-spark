# ADR-0013: Backup & restore — restic to the NAS, append-only, 3-2-1
- **Status:** Accepted · **Date:** 2026-10-04 (answers roadmap Q2: Synology DS918+)

## In plain words
Every night the Spark sends a backup to the NAS. The NAS accepts new backups but never lets the Spark delete old ones. Once a week a copy goes to a USB disk that is unplugged afterwards.

## Decision
### Copies (3-2-1)
| Copy | Where | Medium |
|---|---|---|
| 1 — live | DGX Spark (`/srv`) | NVMe |
| 2 — nightly | NAS DS918+, **Volume 2** (SSD, 425 GB), shared folder `aetherspark-backup` | SATA SSD |
| 3 — weekly, offline | External USB hard disk (2 TB, to buy), unplugged between runs; may also hold a copy of NAS Volume 3 (Marco's projects) | HDD |

### How
- **restic** with the **rest-server** container on the NAS (Container Manager), started with `--append-only`: the Spark can add snapshots but cannot delete or rewrite them.
- **Prune/forget** runs on a schedule from a separate context with a **separate credential** the Spark does not hold. Retention: 7 daily, 4 weekly, 12 monthly.
- **Content:** `pg_dump` (custom format), ClickHouse `BACKUP`, configs, sealed secrets from `/srv/secrets` (ADR-0010), Keycloak realm export, Grafana dashboards. **Excluded:** model weights, raw EDGAR downloads, licensed price data (re-downloadable; a manifest with checksums is backed up instead).
- **Sizing:** first backup ≈ 30–50 GB; with retention ≈ 100–150 GB (fits 425 GB).
- **Encryption:** restic repository password + the sealed secrets stay encrypted; repository password stored like the other generated secrets, recovery copy with the offline age key (ADR-0010).

### Network
- NAS **port 1** stays on Home; NAS **port 2** joins the AetherSpark network (VLAN 20) on the router. All four router LAN ports are then used (desktop, NAS 1, NAS 2, Spark); a small 1 GbE switch on the Home side if more wired devices are needed.
- NAS firewall: rest-server port reachable **only from the Spark's IP** on port 2; DSM admin not reachable from VLAN 20. DSM IP forwarding stays off (the NAS never routes between networks).

### Verification
- Nightly: `restic check` (structure) + alert on failure (ADR-0011 change 3).
- Monthly: `restic check --read-data-subset=5%`.
- **Quarterly restore drill** (NFR-OPS-03): restore to a clean machine/VM, start the stack, run smoke tests; result recorded in `docs/runbooks/restore-drill.md`.

### Desktop phase (Track A)
Desktop backups go to the same NAS target (tests the real path early); local HDD `D:` as fallback.

## Consequences
- Good: a compromised Spark cannot destroy its backups; one copy is always offline; no new NAS hardware.
- Bad: Volume 2 is a single SSD without redundancy (acceptable: it is a copy, and copy 3 exists); the NAS is connected to two networks (mitigated by NAS firewall and no routing); the DS918+ is an older model — if DSM security updates stop, replace the backup target (design unaffected).

## Related
NAS Volume 1 is 97 % full (partner's growing backup, being cleaned up) — outside AetherSpark, but keep it below ~85 %.
