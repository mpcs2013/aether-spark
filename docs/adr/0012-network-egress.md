# ADR-0012: Network segmentation & egress control
- **Status:** Accepted · **Date:** 2026-10-03 (revised with the Q1 answer; site details in `ops.local/site.md`) · **Amendments** 2026-10-04 (router support dates, explicit firewall rules), 2026-10-11 (router and upstream as set up; private package feed) · **Amendment 2026-10-23 Accepted** (two proxies, named flows, host default-deny, SNI check) · **Amendment 2026-10-26 Accepted** (Spark host flows) · **Amendment 2026-10-30 proposed** (host time from public servers)

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

## Amendment (2026-10-11) — router and upstream, as set up
Found while running the site runbook.
- The router's "network isolation" option was found switched on for the Home network after the firmware update. It must be off on Home and AetherSpark: it silently drops all traffic between networks before any firewall rule is evaluated. Separation comes from the explicit rules; Guest/IoT keeps isolation.
- The router's firewall has no "all protocols" choice. Each deny rule therefore exists for TCP/UDP and as an ICMP twin. The router's own allow-all rules between local networks stay below the explicit rules.
- Home ports carry no tags of the other networks. The router's automatic trunking was on by default and is off; otherwise a Home host could send tagged frames into AetherSpark.
- UPnP is checked per network after every firmware update (it was found on for Home).
- The local DNS server forwards only to a privacy-respecting, filtering public resolver; Guest/IoT clients use that resolver directly. Installing the DNS package offers to open its port to the internet. That offer is always declined (a rule created by accident on 2026-10-11 was removed): the router exposes no service to the internet.
- The upstream ISP router has no port sharing, exposed host, UPnP or remote access, and installs firmware updates automatically.

## Amendment (2026-10-11) — private package feed
The private package feed of the shared identity kit (ADR-0009 amendment 1) is allowed for build-time restores only. Runtime containers never reach it.

## Amendment (2026-10-23, accepted) — two proxies, named flows, host default-deny
Design: [architecture/egress.md](../architecture/egress.md) (WP3n.3). Reviewed in two rounds on 2026-10-23: `architect` and `security-reviewer`, then `architect` again, `security-reviewer` as attacker, `infra-admin` against vendor documentation, and an independent flow list written without the design; all findings are in the design. Owner decisions the same day: Ollama in dev as a container; a separate ops proxy; host default-deny on the Spark; SNI check on the proxies; host DNS limited to `home.arpa`; XBRL taxonomies offline; a UI content-security policy.

**Replaces, in "Egress allowlist" above:** "All AetherSpark containers attach only to internal Docker networks" (now: all except Traefik, the two proxies' outbound side and the restic job); the single proxy and its single list; "and the same on the desktop in dev"; "Host firewall (nftables, `DOCKER-USER` chain)"; "proxy deny/allow decisions are logged" (now: both proxies' decisions and host-firewall drops). In Consequences, "testable in CI on the desktop" becomes "testable on the desktop (layers 1–2) and in CI (all layers)".

- **Scope:** the containers of the Compose stack (`just up`) and, on the Spark, the host. `aspire run` (its host processes and the containers it starts) and dev tooling (dev container, Claude Code, CI build jobs) are outside it.
- **Two proxies, two allowlists, SNI checked.** Several hosts above accept uploads from anyone with an account, and Squid sees only the host inside a `CONNECT`. The **runtime proxy** (SEC hosts, the price provider) serves the ingestion jobs only. The **ops proxy** (model, image, package and OS hosts, the private feed, taxonomy packages) is a separate container in profile `egress-ops` that runs only while an owner-started job runs. Both check the TLS site name without decrypting. Agents, model serving, MCP servers, the verifier and databases join neither.
- **Named flows only:** ingress (Traefik, TCP 443; from WP6.6 also NAS alerts), runtime egress, ops egress, backup to the NAS rest-server, the proxies' DNS, the Spark host's DNS (`home.arpa`) and NTP to the router, internal pairs per the network membership matrix. Phone delivery of alerts is decided with ADR-0011 change 3 before WP6.6. Everything else has no route.
- **Layer 3:** Docker Engine ≥ 26.0 with the iptables backend (`DOCKER-USER`), IPv4 and IPv6; host `INPUT` default-deny from Docker bridges (containers on internal networks can otherwise reach the host); on the Spark host `OUTPUT` default-deny (apt and the Docker daemon's pulls only through the ops proxy). The rule set loads before Docker at boot. Drops are logged, to the agent audit from WP5a.5.
- **Model store** written only by pull jobs that verify and never load models; serving mounts it read-only. **XBRL taxonomies** are offline packages; the pipeline has no proxy route.
- **Inputs:** the runtime jobs take identifiers only from the owner-maintained universe (ADR-0020); no agent tool enqueues ingestion.
- **Lints in CI**, among others: no host networking, shared network namespaces, `NET_ADMIN`, Docker socket mounts, undeclared networks or matrix violations; allowlists equal the design note; Squid configs parse.
- **Desktop:** layers 1 and 2 run; layer 3 does not (Docker Desktop's VM is not under our control). CI runs all three layers on amd64 and arm64; the Spark from P6 (WP6.2).
- **Acceptance:** tests E1–E20, L1–L3, M1 (break-it) and P1 in the design note, per environment as listed there; repeated after every Docker, DGX OS or firmware update.
- **Consequences (bad):** security updates on the Spark need an owner-started ops session, so patch latency is an owner duty; Docker Engine version and backend are pinned; the desktop has no layer 3; uploads are possible to ops hosts during ops sessions; two Squid configurations to maintain. Accepted residuals are listed in the design note.
- **Revisit trigger (added):** Docker's nftables backend leaves experimental status; a new runtime component needs an internet host (design change first, then L2).

## Amendment (2026-10-26, accepted) — Spark host flows
Design: [architecture/spark-host.md](../architecture/spark-host.md) (WP6.2). Reviewed 2026-10-26 by `architect` and `security-reviewer` (coverage audit), with vendor facts from NVIDIA's DGX OS documentation and an independent flow list; owner decisions the same day.
- **Named flows (added, Spark host):** SSH in from the admin desktop only; DHCP with the router; the UPS status signal from the host's UPS client to the backup target; host telemetry from the host collector into the stack's collector; the host role's packages through the ops proxy. Host DNS and NTP stay as above, each limited to its own service user.
- **Host `INPUT`** is default-deny on every interface, not only from Docker bridges. **Host → containers** reaches only the ops proxy and the stack collector (narrows "host → Docker bridges" in the design note).
- **Fail closed:** on the Spark the built-in `INPUT` and `OUTPUT` policies are `DROP` and are set before any other rule, so emptied rule lists leave the host closed; deleted tables (which take the policies with them) are found by `check` on a timer with an alert.
- **Development** happens on the desktop; the Spark is a runtime host (ADR-0018 note).
- **Consequences (bad):** a broken rule set needs the local console (runbook `docs/runbooks/spark-console.md`); packet sockets of root processes bypass `OUTPUT`, covered by keeping root services known and repeating the router capture (P1) after DGX OS updates.

## Amendment (2026-10-30, proposed) — the Spark host's time from public servers
The router does not answer time requests from the AetherSpark network, and the home infrastructure keeps router management closed there (exchange A-012, 2026-10-29). Design: [architecture/spark-host.md](../architecture/spark-host.md), flow H6.
- **Replaces** "NTP to the router" in the named flows of the amendments of 2026-10-23 and 2026-10-26: the Spark host's time service sends UDP 123 only to listed public time servers, the only process allowed to; DNS stays with the router.
- **By address:** the host resolves no internet names, so the servers are named by address in the host's settings: Cloudflare's public time service (`time.cloudflare.com`), at the two addresses Cloudflare publishes for it. If they stop answering, the host role's "clock synchronized" check fails and the setting changes.
- **Consequences (bad):** the clock comes from the internet over plain NTP (timesyncd has no authenticated time), so it can be shifted by an attacker on the path; addresses can change without notice. Authenticated time (NTS) would need chrony instead of timesyncd, decided later if the threat model asks for it. If the router later answers time requests on that network, the host returns to the router.
