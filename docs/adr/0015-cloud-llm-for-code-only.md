# ADR-0015: Cloud LLM (Claude) allowed for code, never for data
- **Status:** Accepted · **Date:** 2026-10-03
## Context
The project mandates zero cloud dependency for **inference over research data**. Dev-time agents (plane A, [agent-system.md](../ai/agent-system.md)) are far more capable on Claude than on any model the Spark can serve, and source code already leaves the LAN via GitHub (ADR-0004).
## Decision
- Claude Code (Marco's subscription) is allowed for **source code, configs, docs and synthetic test fixtures**.
- Forbidden in any cloud prompt: datasets, query results, DB dumps, notebook outputs, model outputs over real data, research reports, secrets.
- Enforced by: Read denies + `data_guard.py` hook (I3), `.gitignore` of `data/`, CI check rejecting notebooks with outputs and files > 1 MB under `tests/golden/` (fixtures must be synthetic or public SEC excerpts with provenance).
- Runtime research agents (plane B) use **local models only**.
## Consequences
Strong dev productivity; a documented, bounded exception. Guardrails are pattern-based (same caveat as Decisya ADR-0011); the sandbox is recommended when an issue touches data-adjacent code paths with real samples.
## Alternatives considered
Local-only dev agents (much weaker; revisit below); Claude everywhere (violates the core mandate).
## Revisit trigger
Local coder model on the Spark reaches agreed quality on the plane-A eval (e.g. passes G4 build+tests unaided on ≥ 80 % of a reference issue set) → ADR to move routine G4 work local.
