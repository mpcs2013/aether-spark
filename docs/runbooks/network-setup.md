# Runbook — Home network setup (site-specific, kept local)

The step-by-step network runbook contains the concrete router model, firmware, subnets, ports and firewall rules of the home installation. Because this repository is **public**, that runbook lives in `ops.local/runbooks/network-setup.md` (git-ignored, backed up with the desktop backup — ADR-0013).

Public summary of what it does (ADR-0012):
1. Back up the router configuration; update firmware to a version with VLAN / multi-network support.
2. Harden: UPnP off, WPS off, no port forwards, admin only from the Home network.
3. Create the **AetherSpark** network (VLAN 20, wired only) and a **Guest/IoT** network (VLAN 30, Wi-Fi, isolated).
4. Firewall: Home → AetherSpark only HTTPS (and SSH from the admin desktop); AetherSpark → Home denied.
5. Verify each step with PowerShell checks; back up the configuration again.

Status: **Pending** (paused 2026-10-03).
