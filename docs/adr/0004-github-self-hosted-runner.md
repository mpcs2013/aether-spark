# ADR-0004: GitHub (private) + self-hosted runner
- **Status:** Accepted · **Date:** 2026-10-03
## Decision
- Private GitHub repo; branch protection on `main`; signed commits.
- All jobs run on a **self-hosted, ephemeral container runner** on the LAN (WSL2 now, Spark later). No GitHub-hosted runners touch data or images.
- Images pushed to a **local registry** (`registry:2` behind Traefik), not GHCR.
- Forbidden in git: datasets, model weights, plaintext secrets, `.env` files (gitleaks enforced).
## Consequences
Code leaves the LAN; data never does. Runner must be hardened (T6).
## Revisit trigger
Requirement to air-gap code → migrate to Forgejo (Actions-compatible workflows ease the move).
