# Runbook — NAS setup as backup target (site-specific, kept local)

The step-by-step NAS runbook contains the concrete NAS model, OS version, addresses, accounts, firewall rules and schedules of the home installation. Because this repository is **public**, that runbook lives in `ops.local/runbooks/nas-setup.md` (git-ignored, backed up with the workstation backup — ADR-0013 amendment 5).

Public summary of what it does (ADR-0013, ADR-0010, ADR-0011, ADR-0012):
1. Back up the NAS configuration; update the NAS OS and its container runtime; confirm the vendor still ships security updates for the model.
2. Harden: two-factor authentication for all admin accounts, default admin disabled, auto-block on, vendor relay/remote access and dynamic DNS off, router port mapping (UPnP) removed, SSH off except during admin sessions.
3. Accounts: one dedicated admin for containers; one service account with access to the backup folder only.
4. Second NAS network port on the **AetherSpark** network. NAS firewall rules per interface, so the management UI and file sharing are never reachable from that network.
5. Dedicated shared folder on its own volume, no file-sharing protocols, not part of any other backup task.
6. **restic rest-server** container: append-only, private repositories, memory limit, read-only filesystem, no container-runtime socket, not privileged, no host networking, container network without address translation. The NAS's own per-interface firewall is the inbound control: on the AetherSpark interface only the backup port, from that network (from cutover: from the Spark only); on the Home interface only from the desktop, during the desktop phase. IP forwarding is off on the AetherSpark interface. The nightly maintenance job checks all of this (ADR-0013 Amendment 4).
7. Retention (`forget`/`prune`) runs on the NAS with a separate credential that the backup client never holds.
8. First backup, a proof that deletion is refused, and a full restore with hash comparison.
9. Schedules staggered with the other workloads; failure alerts that carry no data.
10. Weekly encrypted copy to two rotating offline USB disks (one kept away from home), readable without the NAS.
11. Coexistence checks with another project's workload on the same NAS (separate folders, accounts, networks and ports).
12. Workstation backup target for the desktop's local-only data of all projects (ADR-0013 Amendment 5): a second append-only rest-server with its own folder, quota, keys and maintenance, on the Home side and allowed from the desktop only, with refusal tests from another Home device and from the AetherSpark network. The desktop job runs from a folder only administrators can write, with a checksum-checked restic and a protected interpreter, registered elevated, plus a refusal test that the daily account cannot change it (threat T17). The weekly offline copy of this repository is a task of its own, so its guard never stops the other copies. The refusal tests are repeated after every NAS, container-runtime or Windows update.

Status: steps 1–11 **run** 2026-10-11 (written and reviewed 2026-10-04). Step 12 written and reviewed 2026-10-19, re-reviewed 2026-10-22 (`infra-admin`, `security-reviewer`, findings folded in); **in progress** since 2026-10-22: target, firewall and refusal tests done.
