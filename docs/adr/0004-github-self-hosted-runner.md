# ADR-0004: GitHub public repo + GitHub-hosted CI (no self-hosted runner)
- **Status:** Accepted · **Date:** 2026-10-03 · **Amendment 1** Accepted 2026-10-04 (repo made public; supersedes "private repo + self-hosted runner")

## Context
The repository was made **public** (GitHub Free: rulesets on private repos need a paid plan; public repos also get free secret scanning, push protection, CodeQL and unlimited GitHub-hosted Actions minutes). A self-hosted runner attached to a public repo would let pull requests from anyone run code on our machines.

## Decision
- **Public repo** on GitHub (`main` protected by ruleset; PR-only; no deletion or force-push).
- **CI runs only on GitHub-hosted runners** (free for public repos), incl. native `ubuntu-24.04-arm` runners for arm64 builds. CI builds and tests; it holds **no secrets beyond `GITHUB_TOKEN`** with read-only default permissions.
- **No self-hosted runner** is registered to this repo. Desktop/Spark-specific checks (GPU, memory budget, full stack) run locally with `just`, started by the owner — never triggered by GitHub.
- **Production images are built locally on the Spark** from a tagged commit (native arm64); no image registry needed for deployment.
- Fork pull-request workflows require owner approval; no `pull_request_target` workflows that check out PR code.
- Secret scanning + push protection enabled; Dependabot on; gitleaks pre-commit stays (ADR-0010).
- **Public-content rule** (README): no site-specific details (device models/firmware, IPs, ports, personal/employment info) in git — they live in `ops.local/` (git-ignored).
- Forbidden in git (unchanged): data, model weights, licensed prices, plaintext or sealed secrets, `.env` files.

## Consequences
- Good: zero CI cost; GitHub's free security scanning; no inbound path from strangers' PRs to our hardware.
- Bad: design docs are visible to anyone (acceptable: they contain no secrets or site details); site-specific runbooks are kept outside git and must be backed up separately; history before 2026-10-04 contains some site details (accepted, not rewritten).

## Revisit trigger
The repo needs non-public content (data, customer information, licensed material) → move to private + paid plan.
