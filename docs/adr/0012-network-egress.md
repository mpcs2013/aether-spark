# ADR-0012: Network segmentation & egress control
- **Status:** Accepted · **Date:** 2026-10-03 (revised with the Q1 answer; site details in `ops.local/site.md`)

## In plain words
The router separates the home into three networks and keeps the internet out. The strict "only these websites" rule lives on the Spark itself, so it works with any router.

## Decision
### Home router (model and firmware: `ops.local/site.md`)
- Firmware updated to a version with VLAN / multi-network support.
- Three networks:

| Network | VLAN | Subnet | Members | Access |
|---|---|---|---|---|
| Home | default (untagged) | existing | desktop, phones, laptops | may reach AetherSpark only on TCP 443; desktop (reserved IP) also TCP 22 |
| AetherSpark | 20 | `<aether subnet>` | Spark + NAS port 2 (backup target, ADR-0013); wired only, no SSID | no access to Home or Guest; no router admin access; internet outbound allowed at router level |
| Guest/IoT | 30 | `<guest subnet>` | TV, smart devices, visitors (Wi-Fi only) | isolated from all local networks; no router admin access |

- No port forwarding, UPnP off, WPS off, router admin only from Home over HTTPS.
- Remote access (later, optional): the router's VPN server into Home; nothing exposed directly.
- Step-by-step: site-specific runbook in `ops.local/runbooks/network-setup.md` (summary: [runbooks/network-setup.md](../runbooks/network-setup.md)).

### Egress allowlist (on the Spark, and the same on the desktop in dev)
- All AetherSpark containers attach only to **internal Docker networks** (no route out).
- One **egress proxy container** (Squid) is the only component on an outbound network; it allows only: `www.sec.gov`, `data.sec.gov`, `huggingface.co` (+ its CDN hosts), `nvcr.io`, `ghcr.io`, `github.com`, Ubuntu/NVIDIA apt mirrors, `pypi.org`/`files.pythonhosted.org`, `api.nuget.org`, the price provider's API host (ADR Q4).
- Host firewall (nftables, `DOCKER-USER` chain) denies all other outbound traffic from containers; proxy deny/allow decisions are logged to the agent/ops audit (ADR-0011).

### Bandwidth
**1 GbE for v1** (router ports are 1 GbE; Spark's 10 GbE port negotiates down). Payloads are small (UI traffic, nightly backups of tens of GB, internet-limited downloads). 10 GbE switch only when a second Spark or heavy NAS traffic appears. ConnectX-7 QSFP stays reserved.

### Names and certificates
Internal names under the reserved home zone **`home.arpa`** (RFC 8375): AetherSpark uses `*.aether.home.arpa`. The zone is served by the **router's local DNS server** (amendment 2026-10-04, replaces DNS on the Spark), so names keep working when the Spark or NAS is down. Internal CA via step-ca (ADR-0009/P3a), kept separate from any other project's CA on the home network.

## Consequences
- Good: no new router or firewall hardware; the allowlist is enforced next to the workloads and is testable in CI on the desktop; router replacement would not change the design.
- Bad: the router is an older model; if the vendor stops security updates, replace the router (design unaffected). Router-level controls are coarse (IP/port only).

## Revisit trigger
No router firmware security update for 12 months, or a second Spark / NAS-heavy workload (→ 10 GbE switch).

## Amendment (2026-10-04)
- The router's operating-system branch is reported to leave vendor maintenance at the end of 2026, with no extended life announced yet (dates in the local site file). The 12-month trigger above stays; plan a replacement from 2027. The design is unaffected.
- Inside the router, the AetherSpark network uses explicit firewall rules, not the router's "network isolation" option, because that option overrides firewall rules and would also block the allowed Home → Spark traffic. The Guest/IoT network uses isolation.
- Any host attached to two networks (the backup target) must not route between them (ADR-0013 Amendment 2).
