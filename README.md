# Project AetherSpark

Local-first AI and quantitative research platform. 100 % data privacy, zero cloud inference/API dependency.
Target host: **NVIDIA DGX Spark** (GB10 Grace Blackwell, 128 GB unified LPDDR5x, ARM64, DGX OS).

> **Status:** Planning (Phase 0–1). No infrastructure is deployed yet.

## Document index

| Doc | Purpose |
|---|---|
| [docs/00-vision.md](docs/00-vision.md) | Goals, scope, non-goals, use cases |
| [docs/01-nfr.md](docs/01-nfr.md) | Measurable non-functional requirements |
| [docs/02-threat-model.md](docs/02-threat-model.md) | Trust boundaries, threats, controls |
| [docs/03-roadmap.md](docs/03-roadmap.md) | Phases, work packages, exit gates |
| [docs/architecture/c4.md](docs/architecture/c4.md) | Context, container, deployment views; memory budget |
| [docs/ai/agent-system.md](docs/ai/agent-system.md) | Multi-agent design: dev plane + runtime plane, one governance model |
| [docs/adr/](docs/adr/) | Architecture Decision Records |

## Working rules

1. Every foundational choice is an ADR. Status: `Proposed` → `Accepted` → (`Superseded`).
2. No phase starts before the previous phase's exit gate is met (see roadmap).
3. All images are built `linux/arm64` (primary) + `linux/amd64` (dev).
4. No data, model weights or plaintext secrets in git.
