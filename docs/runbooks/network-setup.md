# Runbook — Home network setup for AetherSpark (Synology RT2600ac)

- **ADR:** [0012](../adr/0012-network-egress.md) · **Phase:** P3n · **Time:** ~2 hours, plan a quiet evening (internet drops during the update)
- **Status of this runbook:** written from Synology documentation and reviews of SRM 1.3; **menu labels marked `[verify]` have not been checked on your router yet.** When a label differs, note the real one and the runbook gets corrected.
- **Rule:** do one step, run its check, only then continue. On any unexpected result: stop, note the exact message.

## 0. Before you start
| Step | Synology SRM (browser) | Windows PowerShell (check) |
|---|---|---|
| 0.1 Note current settings: Home network IP range, Wi-Fi names, any port forwards | Network Center → Local Network `[verify]`; Network Center → Port Forwarding `[verify]` | `ipconfig` → note "IPv4 Address" and "Default Gateway" |
| 0.2 Save a configuration backup to the HDD (`D:\backups\router\`) | Control Panel → System → backup/restore configuration `[verify]` | `Test-Path D:\backups\router\*.dss` → `True` `[verify file extension]` |
| 0.3 Use the desktop **wired** (it is), keep phone data as fallback internet | — | `Get-NetAdapter \| ? Status -eq Up` → Ethernet up |

## 1. Update SRM 1.2.5 → 1.3
| Step | SRM | Check |
|---|---|---|
| 1.1 Check for updates and install the latest SRM 1.3.x; router reboots (internet down ~5–10 min) | Control Panel → System → Update `[verify]` | After reboot: Info Center shows **SRM 1.3.x** |
| 1.2 Confirm internet is back | — | `Test-NetConnection www.sec.gov -Port 443` → `TcpTestSucceeded : True` |

## 2. Basic hardening (Home network)
| Step | SRM | Check |
|---|---|---|
| 2.1 Turn **UPnP off** | Network Center → Port Forwarding / UPnP `[verify]` | Setting shows disabled |
| 2.2 Remove port forwards you don't need (keep a list of what you removed) | Network Center → Port Forwarding `[verify]` | List empty or only known entries |
| 2.3 Turn **WPS off** | Wi-Fi Connect → Wireless → WPS `[verify]` | Disabled |
| 2.4 SRM admin: HTTPS only, not reachable from the internet | Control Panel → System/Services → access settings `[verify]` | From phone on mobile data, `https://<your public IP>:8001` does **not** load |
| 2.5 Reserve a fixed IP for the desktop in Home | Network Center → Local Network → DHCP clients → reserve `[verify]` | `ipconfig` after `ipconfig /renew` shows the reserved IP |

## 3. Create the AetherSpark network (VLAN 20)
| Step | SRM | Check |
|---|---|---|
| 3.1 Create network: name `AetherSpark`, VLAN ID `20`, router IP `10.20.0.1`, mask `255.255.255.0`, DHCP on (range `10.20.0.100–10.20.0.199`) | Network Center → Local Network → **Create** (wizard page 1) | Network listed |
| 3.2 Bind **one LAN port** (e.g. LAN 4) to `AetherSpark` as an **untagged/access** port — the Spark plugs in here later | Wizard page 2 (port binding) | Port shows member of AetherSpark only |
| 3.3 **No Wi-Fi SSID** for this network (wired only) | Wizard page 3 → no SSID | — |
| 3.4 Block SRM admin access from this network; block access to other networks | Wizard option "block access to SRM" / isolation option `[verify]` | — |
| 3.5 Test with a laptop (or the desktop briefly) on LAN 4 | — | `ipconfig` → `10.20.0.1xx`; `Test-NetConnection 10.20.0.1 -Port 443` should **fail** (no SRM admin); `Test-NetConnection www.sec.gov -Port 443` → True; ping to the desktop's Home IP → fails |

## 4. Create the Guest/IoT network (VLAN 30)
| Step | SRM | Check |
|---|---|---|
| 4.1 Create network `GuestIoT`, VLAN `30`, `10.30.0.1/24`, DHCP on | Network Center → Local Network → Create | Listed |
| 4.2 New SSID (e.g. `Home-IoT`), WPA2/WPA3, no wired port | Wizard page 3 | SSID visible on phone |
| 4.3 Isolate from all local networks, block SRM admin | Isolation option `[verify]` | Phone on `Home-IoT`: internet works; `http://<desktop Home IP>` and SRM admin page do **not** load |
| 4.4 Move TV / smart devices to `Home-IoT` over the coming days | — | Devices online |

## 5. Firewall rules between networks
Order matters: allow rules first, then the deny.

| # | From | To | Protocol/port | Action | Why |
|---|---|---|---|---|---|
| R1 | Desktop reserved IP (Home) | AetherSpark `10.20.0.0/24` | TCP 22 | Allow | VS Code Remote-SSH, admin |
| R2 | Home network | AetherSpark `10.20.0.0/24` | TCP 443 | Allow | Grafana, Keycloak, services via Traefik |
| R3 | AetherSpark | Home network | any | Deny | Spark never reaches into the home |
| R4 | Home network | AetherSpark | any other | Deny | Only R1/R2 pass |

| Step | SRM | Check (after the Spark or a test laptop is on LAN 4) |
|---|---|---|
| 5.1 Create R1–R4 | Network Center → Security → Firewall `[verify]` | Rules listed in this order |
| 5.2 Verify from desktop | — | `Test-NetConnection <spark-ip> -Port 443` → True (once a service listens); `-Port 5432` → **False** |
| 5.3 Verify from AetherSpark device | — | connection to desktop Home IP on any port → fails |

## 6. Record and back up
| Step | SRM | Check |
|---|---|---|
| 6.1 Export configuration backup again → `D:\backups\router\after-p3n-<date>` | as 0.2 | File present |
| 6.2 Note real menu labels and any differences in this runbook (PR) | — | No `[verify]` left for steps you ran |

## Done when (Gate G3n, network part)
- [ ] SRM 1.3.x installed, UPnP/WPS off, no port forwards, admin not reachable from internet
- [ ] AetherSpark (VLAN 20, wired, LAN 4) and Guest/IoT (VLAN 30, Wi-Fi) exist and get DHCP addresses
- [ ] R1–R4 in place; checks 3.5, 4.3, 5.2, 5.3 pass
- [ ] Router configuration backed up to D:

## Rollback
Restore the configuration file from step 0.2 (Control Panel → System → restore `[verify]`). Worst case: hardware reset and restore.

## Not in this runbook (later phases)
Egress proxy + nftables on the Spark/desktop (P3a/P6), internal DNS `aether.lan` (P6), VPN Plus remote access (optional, after P6), NAS port 2 on the AetherSpark network + NAS firewall for rest-server (ADR-0013; done together with the backup setup).
