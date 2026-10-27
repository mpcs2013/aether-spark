# Runbooks
| Runbook | Phase | Status |
|---|---|---|
| [dev-setup.md](dev-setup.md) | P2 | **Ready** (2026-10-22): Windows host tools, git hooks, dev certificate, `just ci`, AppHost check; WSL2, GPU passthrough and driver pin; dev container |
| [network-setup.md](network-setup.md) | P3n | **Run** 2026-10-10 / 2026-10-11, all checks ticked. Public summary only; full runbook in `ops.local/runbooks/` |
| [nas-setup.md](nas-setup.md) | P3n | **Run** in the desktop phase 2026-10-11 (steps 1–11); step 12, the workstation backup (WP3n.5), **in progress** since 2026-10-22. Public summary only; full runbook in `ops.local/runbooks/` |
| [spark-host.md](spark-host.md) | P6 (WP6.2) | **Ready for arrival**, not run yet: set up the Spark's host after first boot (observe, copy the settings, bootstrap, apply the role at the console), ops sessions |
| [spark-console.md](spark-console.md) | P6 (WP6.2) | **Ready for arrival**, printed next to the Spark, rehearsed once at arrival: the way back when the firewall locks you out or a check fails |

Planned (P3a–P7): `bootstrap.md`, `secrets.md`, `keycloak-break-glass.md`, `agent-kill-switch.md`, `cutover.md`, `restore-drill.md`, `model-update.md`, `patching.md`.
