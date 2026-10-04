# ADR-0016: Dev-time agent pipeline adapted from Decisya
- **Status:** Accepted (design) · implementation in WP2.6 · **Date:** 2026-10-03
## Context
Decisya runs a proven issue → gates → verdict pipeline with lanes and hooks (Decisya `docs/ai/README.md`, `agents-review.md`). Its review found that prose-only pipelines silently skip steps (F-01, F-04); its later issues added proportion rules (#84) after artifacts grew large.
## Decision
1. Adopt Decisya techniques D1–D15 ([agent-system.md §2](../ai/agent-system.md#2-what-we-take-from-decisya-proven-there)).
2. **Copy and adapt** the scripts (`gates.py`, `lint.py`, `roster.py`, `prepush.py`, `commitlint.py`, hooks) into this repo; no shared package. Divergence is logged in agent-system.md §8 with `port`-labelled mirror issues.
3. Add improvements I1–I4, I9–I11: size budgets, identity-tied skip approvals in CI, `data_guard.py`, G5v domain-verification gate, dual roster rendering, budgets, Python tool lanes.
4. Roster of 11 agents re-cut to this domain (agent-system.md §4.1); two new change classes `data-pipeline` and `model-config`.
5. Host development by default; sandbox optional on Decisya ADR-0011 triggers.
## Consequences
- Good: day-one discipline without Decisya's retrofit cost; same mental model across both repos.
- Bad: copied code drifts; mitigated by the divergence log. More gates per feature (G5v) cost time on data work — accepted, because look-ahead bias and unit errors are silent and expensive.
## Alternatives considered
Shared governance kit (rejected 2026-10-03 for now: extra versioning overhead); minimal setup without gates (rejected: Decisya F-01 shows skipped steps go unnoticed).
## Revisit trigger
More than three `port` issues in a quarter → reconsider extracting a shared kit.
