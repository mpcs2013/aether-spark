# Runbooks
| Runbook | Phase | Status |
|---|---|---|
| [dev-setup.md](dev-setup.md) | P2 | **Ready** (2026-10-22): Windows host tools, git hooks, dev certificate, `just ci`, AppHost check; WSL2, GPU passthrough and driver pin; dev container |
| [network-setup.md](network-setup.md) | P3n | **Run** 2026-10-10 / 2026-10-11, all checks ticked. Public summary only; full runbook in `.local.ref/runbooks/` |
| [nas-setup.md](nas-setup.md) | P3n | **Run** in the desktop phase 2026-10-11 (steps 1–11); step 12, the workstation backup (WP3n.5), **in progress** since 2026-10-22. Public summary only; full runbook in `.local.ref/runbooks/` |
| [spark-host.md](spark-host.md) | P6 (WP6.2) | **Ready for arrival**, not run yet: set up the Spark's host after first boot (observe, copy the settings, bootstrap, apply the role at the console), ops sessions |
| [spark-console.md](spark-console.md) | P6 (WP6.2) | **Ready for arrival**, printed next to the Spark, rehearsed once at arrival: the way back when the firewall locks you out or a check fails |
| [secrets.md](secrets.md) | P3a (WP3.3) | **Ready** (2026-11-06): one-time WSL2 setup (age, just), the desktop proof, `just secrets generate / unseal / clear / rotate`, adding a secret; the Spark's production store follows with WP6.4 |
| [llm-trial.md](llm-trial.md) | P5b (WP5b.1, early) | **Ready** (2026-11-07): LLM serving's first light on the Spark: model pull through the ops proxy, vLLM on an internal network, the first answer and the speed measurement |
| [keycloak-admin.md](keycloak-admin.md) | P3a (WP3.1) | **Run** on the desktop 2026-11-08 (first time, steps 1–5): harden the master realm and create the owner's administrator (`just keycloak setup`), first sign-in with the YubiKey, delete the temporary administrator; break-glass (`just keycloak-break-glass`, tested by the stack test); YubiKey 2 when it is home |
| [restore-drill.md](restore-drill.md) | P3a (WP3.4) | **Ready** (2026-11-12): your off-line items (dev key, root fingerprint, backup login), the first snapshots (scratch, then real: `key-add`, the refused delete), the drill in a fresh WSL distribution (Gate G3, then quarterly), after an incident |
| [ostium-packages.md](ostium-packages.md) | P3a (WP3.1, WP3.5) | **Ready** (2026-11-09): the read-only feed token in Windows Credential Manager (`just ostium-feed save`), pinning an `Ostium.*` version, Ostium's consumer check in the PR; the local folder feed (`just ostium-feed local-sync`, `local-check`; 2026-11-12) |
| [certificates.md](certificates.md) | P3a (WP3.1) | **Ready** (2026-11-06): the CA (`just up`, `just ca status / root / renew-intermediate`), trusting the root in Edge, Chrome and Firefox and removing it, what to do when something expires, re-init; the Spark's CA follows with WP6.4 |

Planned (P3a–P7): `bootstrap.md`, `agent-kill-switch.md`, `cutover.md`, `model-update.md`, `patching.md`.
