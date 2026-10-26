# Spark host — design (WP6.2)

**Status:** proposed 2026-10-26, accepted together with the ADR-0012 amendment of 2026-10-26 (Spark host flows) and ADR-0013 amendment 7 (UPS signal); to be built in WP6.2. Implements the host part of [ADR-0012](../adr/0012-network-egress.md) and the [egress design](egress.md) ("Layer 3 rules": host `INPUT` and `OUTPUT` on the Spark, bootstrap). Requirements on the home network, the NAS and the UPS come from the home-infrastructure owner's contract; addresses are settings, never in this repository.

**Inputs.** Vendor facts from NVIDIA's DGX Spark and DGX OS 7 documentation, NVIDIA's apt index, and the Ubuntu, systemd, NetworkManager, Docker and fwupd documentation (Facts); an independent list of host flows written without this design and compared with it.

**Reviews.** 2026-10-26: `architect` CONSISTENT WITH FIXES, `security-reviewer` (coverage audit) GO WITH FIXES; findings folded in.

**Owner decisions (2026-10-26):** development stays on the desktop; the host role is an Ansible role run locally on the Spark; the host fails closed; no graphical session; unneeded snaps removed and snapd masked; SSH with a hardware security key; an admin password for the console and `sudo` only; signed release tags. New to these terms? See Concepts at the end.

## Goal and scope
On the Spark, no host process sends anything to the LAN or the internet except the flows named below, and nothing reaches the host from the LAN except SSH from the admin desktop. Container traffic stays as in the egress design (`DOCKER-USER`, the proxies).

- **In scope:** DGX OS on the Spark after first boot: network and radios, resolver, time, packages, firmware, NVIDIA's own services, the admin account and SSH, host metrics and logs, the UPS shutdown signal, the host firewall, the host's age key.
- **Out of scope:** first boot itself (WP6.1), deploying the stack (WP6.4: a systemd unit that runs `just up`), the router, the NAS and the UPS (home infrastructure).

## Decisions
- **Development stays on the desktop.** Code, git, Claude Code, package restores and tests run on the desktop. The Spark is a runtime host: SSH for inspection and owner-started ops sessions only. If VS Code Remote-SSH is used, the desktop downloads the VS Code server and copies it over SSH (`remote.SSH.localServerDownload`); no extensions are installed on the Spark (ADR-0018 note).
- **Ansible, run locally on the Spark:** `ansible-playbook -c local` from a root-owned checkout of a signed release tag (`git verify-tag` against the owner's key before every run; release tags are signed with the owner's existing SSH tag key, from the first tag used on the Spark), with the `ansible` package (core plus the collections the role uses) pinned in `uv.lock`, fetched from PyPI through the ops proxy; no Galaxy. `uv` itself is installed from a pinned release with its SHA-256 checked. No automation credential over SSH, no become password in any file. The role lives in `deploy/ansible/` and applies `deploy/host/`.
- **Runtime host, no desktop session:** default target `multi-user.target`. DGX OS's desktop packages stay installed (NVIDIA's meta-packages need them); no graphical session runs.
- **Fail closed where it can.** On the Spark the built-in `INPUT` and `OUTPUT` policies are `DROP` (IPv4 and IPv6). The rule unit sets these policies first, before it reads its settings, so a unit that stops early at boot leaves the host closed. A deleted or flushed rule list then leaves the host closed too. Deleting the tables themselves (`nft flush ruleset`, or an `iptables-restore` without our policies) removes the policies with them and opens the host: no packet filter can prevent that, so `check` detects it (it compares the built-in policies and the full `INPUT`/`OUTPUT` contents, IPv4 and IPv6) and runs on a timer with an alert. In CI and on other hosts the policies stay as they are.
- **Pinned host packages:** `docker-ce`, `containerd.io` and the NVIDIA container toolkit are held (`apt-mark hold`); a bump is a deliberate change with the egress suite re-run (ADR-0012).

## Host flows

| # | Flow | Who | Allowed | Notes |
|---|---|---|---|---|
| H1 | Loopback | all | `-i lo` / `-o lo` | By interface, not by `127.0.0.0/8` |
| H2 | Replies | all | `ct state established,related` first, `invalid` dropped | `related` carries ICMP errors (path MTU) |
| H3 | SSH in | `sshd` | TCP 22 from the admin desktop's address, LAN interface only | See SSH |
| H4 | DHCP | NetworkManager's internal client | UDP 68 → 67 to the router and to broadcast; replies 67 → 68 | The first request uses a packet socket that iptables does not see; renewals are plain UDP. Without this rule the lease cannot be renewed and the address drops out each time the lease ends |
| H5 | DNS | `systemd-resolved` only (owner match) | UDP and TCP 53 to the router | Resolves `~home.arpa` only: the router as the LAN link's DNS server with the route-only domain `~home.arpa` (not a default route), `FallbackDNS=` empty, LLMNR and mDNS off. The router must answer every `home.arpa` name itself and never forward one (requested from the infrastructure owner) |
| H6 | Time | `systemd-timesyncd` only (owner match) | UDP 123 to the router | `NTP=` the router, `FallbackNTP=` empty. Needs the router's time service (due 2026-10-30) |
| H7 | Ops egress | root and `_apt` only (owner match): apt, the Docker daemon's pulls, fwupd, `uv` for the role | TCP to the ops proxy's fixed address and port on `egress-ops` | Works only while the ops proxy runs. apt sources on HTTPS (a plain-HTTP source would be a proxy `GET`, which the proxy refuses) |
| H8 | Host telemetry into the stack | the host OTel collector's user only (owner match) | TCP to the stack collector's OTLP port at its fixed address on one named `obs-` network | The host collector scrapes `node-exporter` on loopback and reads the journal. The stack collector's address is pinned like the proxies' |
| H9 | UPS signal | NUT `upsmon` (owner match) | TCP to the NAS's AetherSpark-network address, UPS status port | The Spark connects out; nothing inbound. The NAS side, its port and a read-only UPS account are the infrastructure owner's (ADR-0013 amendment). `upsmon.conf` root:nut 0640, its password from the sealed secrets |
| H10 | Published ports | Traefik (Docker) | Not host `INPUT`: forwarded, `DOCKER-USER` (egress design) | Published on the LAN address only |
| H11 | Backup | restic container | Not host `OUTPUT`: forwarded, `DOCKER-USER` `backup` rule | |

Everything else in and out is logged (prefix `aetherspark-host-drop:`, `hashlimit` budget per pair, as in the egress design) and dropped. Host → containers is limited to H7 and H8 (this narrows the egress design's "host → Docker bridges"): a host process cannot use the runtime proxy or reach other containers' ports.

**Interfaces.** The rules name the LAN interface (setting `AS_LAN_IF`, the 10 GbE port) and drop anything on an interface other than the LAN interface, loopback and Docker bridges; a second default route would otherwise fail open. Wi-Fi, Bluetooth and the four ConnectX-7 ports: NetworkManager leaves them unmanaged, the Wi-Fi and Bluetooth kernel modules are blacklisted and the radios blocked with `rfkill` at every boot (NVIDIA documents no hardware switch). IPv6: `ipv6.method disabled` on the LAN connection (NetworkManager would otherwise reset `disable_ipv6`), `disable_ipv6` on every interface, and the `DROP` policies.

**Boot order.** The rule unit runs with `DefaultDependencies=no`, `Before=network-pre.target` and `Wants=network-pre.target`, so it loads before any interface comes up, and before `docker.service` as in the egress design.

## SSH and the admin account
- **One admin account** (the owner), in `sudo`, with a password kept in the password manager: for the local console and for `sudo`, never for SSH. Root login off.
- **Key:** a hardware security key (`ed25519-sk`, a touch for every login): the key that leads to root on the Spark then cannot be copied off the desktop, where Claude Code and the dev containers run. The public key in `authorized_keys` carries `from=` the desktop's address. The key is bought before the Spark's first SSH login.
- **sshd settings:** `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowUsers` the admin, `AllowTcpForwarding local`, `AllowStreamLocalForwarding local`, `AllowAgentForwarding no`, `PermitTunnel no`, `X11Forwarding no`, `GatewayPorts no`. Remote forwarding would give Spark processes a way out through the desktop; agent forwarding would let root on the Spark use the desktop's key.
- **Host key:** its fingerprint is read at the Spark's console and compared at the first SSH login from the desktop.
- **`docker` group:** nobody (membership is root-equivalent).

## Secrets on the host
- **Age key (ADR-0010):** generated on the Spark at bootstrap, root-only `0400`, outside every backup path; its offline recovery copy goes with the other offline key (ADR-0010). The role checks owner, mode and the backup exclusion.
- **Host settings** (`/etc/aetherspark/egress-rules.env` and the role's addresses): copied by the owner from the desktop's local site folder over SSH, root-only `0600`. A rebuild (NFR-OPS-03) uses the same copy.

## Services switched off or redirected

| Service (DGX OS 7 / Ubuntu 24.04) | Contacts | Change |
|---|---|---|
| NVIDIA telemetry (`nvidia-dgx-telemetry`), first-boot consent | NVIDIA, endpoints not documented | Consent declined at first boot; service disabled if installed |
| DGX Dashboard (`dgx-dashboard.service`, localhost) | update checks, Wi-Fi assistant | Disabled; Grafana is the dashboard |
| OTA check (`nvidia-spark-ota-check`), `nvidia-spark-run-apt-upgrade` (upgrade at next boot) | NVIDIA, apt | Disabled; updates only in ops sessions |
| `unattended-upgrades`, `apt-daily*` timers | Ubuntu mirrors | Off (`APT::Periodic` values 0); patching is an owner duty (ADR-0012) |
| `fwupd-refresh.timer`, `passim` (if installed) | LVFS, LAN peers | Off; `fwupdmgr refresh` / `upgrade` by hand through the ops proxy |
| snapd and the default snaps | Snap Store | Remove the snaps a runtime host does not need, then mask `snapd` (owner decision); a snap found necessary at arrival is a design change |
| Ubuntu Pro client: APT News, `ua-timer`, `esm-cache`, the ESM source | Canonical | `apt_news` off, timers masked, the ESM source removed unless the Spark is attached |
| `motd-news` | Canonical | Off |
| cloud-init (if present) | metadata services | Disabled after first boot |
| NetworkManager connectivity check | Ubuntu | `[connectivity] enabled=false` |
| avahi (`.local` names), `lldpd` (layer-2 frames iptables cannot filter) | LAN multicast, the switch port | Masked; NVIDIA Sync is not used |
| GNOME location and software | various | Not started under `multi-user.target` |
| apport, whoopsie, popularity-contest, kerneloops, cups-browsed, update-notifier | various | Removed or masked if present (checked at arrival) |

**Drift.** Ansible skips `command` and `shell` tasks in check mode, so every setting above also gets a read-only assertion task that runs in check mode and fails when the setting has changed (a unit enabled again, a radio unblocked, a snap back, a policy not `DROP`). CI has a break-it test per assertion. Check mode runs at the end of every ops session and on a weekly timer with an alert.

## Packages, images and firmware
- **apt** through the ops proxy (`Acquire::https::Proxy` with an `http://` proxy URL), every source on HTTPS. Sources seen on the Spark: Ubuntu ports, NVIDIA's DGX repository, CUDA, the NVIDIA container toolkit, Docker, three Launchpad PPAs of Canonical's NVIDIA team (desktop edge, Wi-Fi firmware, Vulkan), NVIDIA AI Workbench, the HPC SDK, ESM. At arrival, `apt-cache policy` shows which installed packages come from each; sources that serve nothing installed are removed, the rest go on the ops allowlist.
- **Ops allowlist additions** (egress design table, L2 checks them): `repo.download.nvidia.com` and `developer.download.nvidia.com` (replacing "NVIDIA apt repository host"), `nvidia.github.io`, `cdn.fwupd.org` and `fwupd.org` (replacing "LVFS host, *to confirm*"); the PPA host (`ppa.launchpadcontent.net` or a snapshot host, *to confirm*) only if a PPA stays.
- **Docker daemon** (`daemon.json`): `proxies` to the ops proxy (pulls only; container traffic is unaffected), `dns`, `userland-proxy: false`, the iptables firewall backend. DGX OS 7.6 ships Engine 29.6; held as above.
- **Firmware** through LVFS (NVIDIA is an LVFS vendor); fwupd gets the proxy through its service environment. ConnectX firmware (`nvidia-spark-mlnx-firmware-manager`) only in an owner session.
- **No proxy, no patch.** The egress proxy image is never pruned on the Spark, so the ops proxy can always start without the internet. If it is broken, recovery re-applies the last release tag at the console; egress is never opened around it.

## Logs and alerts
- The journal stays on the Spark (persistent, size-capped); the host OTel collector ships it into the stack's Loki (H8) through ADR-0011's content filter. From WP5a.5 the `aetherspark-*drop:` and `spoof:` lines also go to the `agent-audit` stream (ADR-0019), as in the egress design.
- **Alerts** (ADR-0011): any `aetherspark-host-drop:` line in steady state; `check` failing; check mode reporting drift; a host-key, policy or unit change.

## Order
1. **First boot (WP6.1)** on the AetherSpark network, whose router allows outbound internet: first boot needs it, downloads the full software image and cannot be interrupted. Telemetry consent declined. Then firmware and OS updates, disk layout, the Docker and NVIDIA container toolkit check. (The Spark joins the AetherSpark network here; the roadmap's WP6.3 is folded into WP6.1.)
2. **Observe:** record the unknowns below.
3. **Bootstrap, at the console:** read the host-key fingerprint; copy the settings from the desktop; check out the release tag and verify it; install the pinned `uv` and the role's packages; generate the age key; build the egress proxy image and pull the pinned images while the host still has direct access.
4. **Apply the role at the console**, with host `OUTPUT` default-deny as its last step, so a mistake never locks out a remote session. From then on, everything goes through the ops proxy.
5. **Tests on the Spark:** S1–S12 below, and the egress design's Spark tests E12, E14 (host part), E17 and E18; the rest of the egress tests at Gate G6.
6. **Rehearse recovery once** with the console runbook (below), with the owner at the keyboard.

## Observe at arrival (unknown before the Spark is here)
`resolvectl status` (systemd-resolved in use); `systemctl list-unit-files --state=enabled` and `list-timers`; `snap list`; `apt-cache policy` and every source file; `nmcli device`; which user `upsmon` runs as; whether `nvidia-dgx-telemetry`, cloud-init and `passim` are installed; whether the Spark keeps time without a network (real-time clock), and which services must wait for `time-sync.target`. After the role is applied: the drop log, and `resolvectl monitor` for an hour (any name outside `home.arpa` is a finding).

## Tests (acceptance of WP6.2)

| # | Action | Expected |
|---|---|---|
| S1 | From the admin desktop: SSH with the key; with a password; as root; with a remote forward. `sshd -T` shows the settings above. At the console, set the admin address to an unused one and retry from the desktop, then revert | Works; refused; refused; refused; matches; refused (proves the host rule, since the router alone lets the desktop through) |
| S2 | DHCP renewal forced on the Spark | Lease renewed, address unchanged |
| S3 | As root: DNS for a `home.arpa` name; for a random made-up `home.arpa` name; for an external name; a direct query to the router for an external name | Resolves; not found, and the router does not forward it; fails; fails (owner match) |
| S4 | Time sync against the router; with the router's time service unreachable | Synced; no fallback server contacted |
| S5 | `apt update`, a Docker pull, `fwupdmgr refresh`, all with the ops proxy down, then up; the ops proxy from an unprivileged user | Fail; work; refused |
| S6 | Direct HTTPS from the host to a public address; to the runtime proxy; any IPv6 connection | Fail; fail; fail |
| S7 | UPS on battery (with the infrastructure owner) | Signal received, clean shutdown with time to spare |
| S8 | Reboot; `systemctl restart docker`; reloading the rules; the unit order (`systemd-analyze`) | Policies `DROP` throughout, the rules loaded before any interface and before Docker, S1–S6 unchanged |
| S9 | Lab: flush the rule lists; delete the tables | Host closed; `check` fails and alerts |
| S10 | Wi-Fi and Bluetooth after reboot; a cable in a ConnectX port | Blocked, modules not loaded; no address, nothing forwarded |
| S11 | After an apt upgrade: the role in check mode; the held packages | Drift reported by the assertions; held packages unchanged |
| S12 | The host collector delivers to the stack collector; another host user to the same port; the host to another container's port | Delivered; refused; refused |

Every test opens new connections: existing ones survive a rule reload in connection tracking. Before arrival, CI covers the rules: the rule script's host mode runs in a lab network namespace (a privileged container stands in for the Spark, with veth peers for the router, the NAS and the desktop), so S1 (network part), S2–S6, S8, S9 and S12 run on every change without cutting the runner's own connection, and the break-it test (M1) also covers host mode. The role is checked in a container with systemd, with a break-it test per assertion.

## Console runbook
A printed page next to the Spark, written for the owner and rehearsed once at arrival: log in at the console; run `check`; re-apply the last release tag; put the rules back to the last good state; reach the boot menu and recovery mode; what each expected output means. It lives in `docs/runbooks/spark-console.md`.

## How it is built
1. This note; ADR-0012 and ADR-0013 amendments; the ADR-0018 note; the egress design's changes (host → containers narrowed, allowlist rows and the ops allowlist file); the roadmap (WP6.3 folded into WP6.1; hand-overs to WP6.4 and G6).
2. Host mode in `deploy/host/egress-rules.sh` (policies first, `INPUT`/`OUTPUT` chains, owner matches, `check` extended to built-in policies, unit order, settings `AS_LAN_IF`, `AS_ROUTER`, `AS_ADMIN_ADDR`, `AS_UPS_PORT`, `AS_COLLECTOR`) and the lab tests in CI.
3. The Ansible role (`deploy/ansible/`), its container test and assertions in CI; the console runbook.
4. At arrival: first boot, observe, bootstrap, apply, tests, rehearsal.

## Residual risks
- **Tables deleted** (not just flushed) open the host; `check` on its timer finds it within one interval, and `install.sh` refuses an `nftables.service` that flushes the rule set.
- **Packet sockets:** root processes or ones with `CAP_NET_RAW` can send through packet sockets, which bypass `OUTPUT` (this is how the first DHCP request works). Controls: no unknown root services (observe, check mode), and the router capture (P1) repeated after every DGX OS update.
- **Ops sessions:** while the ops proxy runs, root host processes reach the ops allowlist (as in the egress design, E5).
- **`home.arpa` lookups** reach the router; they stay there only if the router never forwards `home.arpa` (requested from the infrastructure owner, tested in S3).
- **A compromised desktop** reaches the Spark's admin account; the hardware key's touch and the `sudo` password stand between it and root.
- **Patching** depends on owner sessions (ADR-0012); check mode and a weekly ops session are the control.
- **NVIDIA's services** are not documented end to end; the drop log, `resolvectl monitor` and the assertions are the check, and a new NVIDIA package can add a unit (S11).
- **Fail closed** means a broken rule set needs the local console (runbook above).

## Facts
NVIDIA: DGX Spark first boot, hardware, DGX Dashboard, OS and component update, porting guide (DGX OS 7 = Ubuntu 24.04 server with desktop packages); DGX OS 7 release notes (7.6.0: Ubuntu 24.04.5, Engine 29.6.2, Container Toolkit 1.20.0) and air-gapped installation appendix (apt sources); NVIDIA's apt index (`dgx-oobe`, `nvidia-dgx-telemetry`, `nvidia-spark-ota-check`, `nvidia-spark-run-apt-upgrade`, `dgx-spark-ota-update-meta`, avahi and lldpd dependencies). Ubuntu: timesyncd guide, automatic updates, Pro client network requirements and APT News, `systemd-resolved`(8) and `resolved.conf`(5), `apt-transport-https`(1). NetworkManager.conf (connectivity check). Docker: daemon proxy configuration (`proxies`, Engine ≥ 23.0). fwupd: `lvfs.conf`, LVFS download documentation, LVFS vendor list.

## Concepts
For the owner, who runs this host without a networking background. Each term is used above.

- **Packet filter, chains, policy.** The Linux kernel checks every network packet against lists of rules (*chains*). `INPUT` sees packets addressed to the host itself, `OUTPUT` packets the host sends, `FORWARD` packets passing through the host, such as container traffic (Docker routes it, so it is forwarded). Rules are read top to bottom; the first rule that accepts or drops a packet decides (other rules only log it, or jump to another list and come back). A chain's *policy* is what happens when no rule decides: `ACCEPT` or `DROP`. `iptables` is the tool that writes these rules (here its `nft` variant, the current kernel interface).
- **Default-deny.** The policy is `DROP` and only the flows in the table are allowed. The opposite, allow everything and block what is known to be bad, misses whatever nobody thought of.
- **Connection tracking (conntrack), established/related.** The kernel remembers each connection. A rule that allows a connection's first packet (`NEW`) lets its replies through with one `established` rule at the top, so no rule has to allow "replies from anywhere". `related` covers error messages about a connection, such as "packet too big". Existing connections survive a rule change, which is why tests open new ones.
- **Fail open, fail closed.** What happens when the rules disappear (a bug, a tool that removes them). With `ACCEPT` policies the host is then open: fail open. With `DROP` policies it is cut off: fail closed. Here a host whose rule lists are emptied is cut off, and recovery needs the Spark's own screen and keyboard. A tool that deletes the tables themselves, not just the rules, removes the policies too and opens the host; the `check` timer finds that and alerts.
- **Owner match.** A rule can match the local user a packet comes from (`-m owner --uid-owner`, `OUTPUT` only). That is how only the resolver may ask the router for names, and only the UPS client may reach the UPS port.
- **Resolver, route-only domain.** `systemd-resolved` answers every program's name lookups. A *route-only domain* (`~home.arpa`) sends only names ending in `home.arpa` to the router; with no default route and no fallback servers, any other name gets no answer. The router could resolve internet names, so the firewall rule and the resolver setting together keep the host itself off the internet's names.
- **DHCP lease and renewal.** The router hands out the Spark's address (a fixed reservation) for a limited time. The first request is sent before the host has an address, through a path the packet filter does not see; renewals are ordinary UDP packets and need rule H4. Without it, every renewal fails: the address drops out each time the lease ends and comes back only when the client starts over, so the Spark has short outages hours or days apart.
- **Proxy (HTTP `CONNECT`).** A program asks the proxy to open a connection to a named host on port 443; the proxy checks the name against its allowlist and then only relays encrypted bytes. The host has no other way out, so apt, Docker and fwupd work only while the ops proxy runs.
- **systemd units, targets, masking.** Services (`.service`) and schedules (`.timer`) are *units*. A *target* is a set of units started together; `multi-user.target` is a full system without a graphical session. *Disable* stops a unit starting at boot; *mask* also stops anything else from starting it.
- **Snaps.** Ubuntu's second package system, with its own store and automatic updates (snapd); not needed on a runtime host.
- **Check mode (Ansible).** `--check --diff` shows what the role would change without changing it; a task reported as "changed" is drift: something switched back on or changed. Ansible skips command tasks in check mode, which is why each setting also has a read-only assertion that fails when it has drifted.
- **Hardware security key.** A small USB device that holds the SSH key and never releases it; every login needs a touch on the device. Malware on the desktop can then neither copy the key nor use it unnoticed.
- **Lab network namespace.** A separate network stack inside one Linux system, with its own interfaces and rules. CI builds a small fake network in one (the Spark, the router, the NAS, the desktop) and tests the rules there, because default-deny on the CI machine itself would cut its connection to GitHub.
