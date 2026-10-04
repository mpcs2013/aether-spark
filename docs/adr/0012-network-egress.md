# ADR-0012: Network segmentation & egress control
- **Status:** Accepted · **Date:** 2026-10-03 (revised with Q1 answer: Synology RT2600ac)

## In plain words
The router separates the home into three networks and keeps the internet out. The strict "only these websites" rule lives on the Spark itself, so it works with any router.

## Decision
### Router (Synology RT2600ac, SRM ≥ 1.3)
- Update SRM 1.2.5 → 1.3 (adds VLAN / multiple networks; up to 5 networks on this model).
- Three networks:

| Network | VLAN | Subnet | Members | Access |
|---|---|---|---|---|
| Home | default (untagged) | existing | desktop, phones, laptops | may reach AetherSpark only on TCP 443; desktop (reserved IP) also TCP 22 |
| AetherSpark | 20 | 10.20.0.0/24 | Spark + NAS port 2 (backup target, ADR-0013); wired only, no SSID | no access to Home or Guest; no SRM admin access; internet outbound allowed at router level |
| Guest/IoT | 30 | 10.30.0.0/24 | TV, smart devices, visitors (Wi-Fi only) | isolated from all local networks; no SRM admin access |

- No port forwarding, UPnP off, WPS off, SRM admin only from Home over HTTPS.
- Remote access (later, optional): SRM VPN Plus (OpenVPN) into Home; nothing exposed directly.
- Step-by-step: [runbooks/network-setup.md](../runbooks/network-setup.md).

### Egress allowlist (on the Spark, and the same on the desktop in dev)
- All AetherSpark containers attach only to **internal Docker networks** (no route out).
- One **egress proxy container** (Squid) is the only component on an outbound network; it allows only: `www.sec.gov`, `data.sec.gov`, `huggingface.co` (+ its CDN hosts), `nvcr.io`, `ghcr.io`, `github.com`, Ubuntu/NVIDIA apt mirrors, `pypi.org`/`files.pythonhosted.org`, `api.nuget.org`, the price provider's API host (ADR Q4).
- Host firewall (nftables, `DOCKER-USER` chain) denies all other outbound traffic from containers; proxy deny/allow decisions are logged to the agent/ops audit (ADR-0011).

### Bandwidth
**1 GbE for v1** (RT2600ac ports are 1 GbE; Spark's 10 GbE port negotiates down). Payloads are small (UI traffic, nightly backups of tens of GB, internet-limited downloads). 10 GbE switch only when a second Spark or heavy NAS traffic appears. ConnectX-7 QSFP stays reserved.

### Names and certificates
Internal DNS zone `aether.lan` served on the Spark from P6 (until then: hosts file on the desktop); internal CA via step-ca (ADR-0009/P3a).

## Consequences
- Good: no new router or firewall hardware; the allowlist is enforced next to the workloads and is testable in CI on the desktop; router replacement would not change the design.
- Bad: the RT2600ac is an older model (Wi-Fi 5); if Synology stops security updates, replace the router (design unaffected). Router-level controls are coarse (IP/port only).

## Revisit trigger
No SRM security update for 12 months, or a second Spark / NAS-heavy workload (→ 10 GbE switch).
