# ADR-0016: Dev-time agent pipeline adapted from Decisya
- **Status:** Accepted (design) · implementation in WP2.6 · **Date:** 2026-10-03 · **Amendment 1** Accepted 2026-10-16 (shared core Norma instead of a copy)
## Context
Decisya runs a proven issue → gates → verdict pipeline with lanes and hooks (Decisya `docs/ai/README.md`, `agents-review.md`). Its review found that prose-only pipelines silently skip steps (F-01, F-04); its later issues added proportion rules (#84) after artifacts grew large.
## Decision
1. Adopt Decisya techniques D1–D15 ([agent-system.md §2](../ai/agent-system.md#2-what-we-take-from-decisya-proven-there)).
2. **Copy and adapt** the scripts (`gates.py`, `lint.py`, `roster.py`, `prepush.py`, `commitlint.py`, hooks) into this repo; no shared package. Divergence is logged in agent-system.md §8 with `port`-labelled mirror issues. *(Superseded by amendment 1.)*
3. Add improvements I1–I4, I9–I11: size budgets, identity-tied skip approvals in CI, `data_guard.py`, G5v domain-verification gate, dual roster rendering, budgets, Python tool lanes.
4. Roster of 11 agents re-cut to this domain (agent-system.md §4.1); two new change classes `data-pipeline` and `model-config`. *(2026-10-04: `infra-admin` added, so 12 agents, and a third new change class `infra`; agent-system.md §4.1, §4.3.)*
5. Host development by default; sandbox optional on Decisya ADR-0011 triggers.
## Consequences
- Good: day-one discipline without Decisya's retrofit cost; same mental model across both repos.
- Bad: copied code drifts; mitigated by the divergence log. More gates per feature (G5v) cost time on data work — accepted, because look-ahead bias and unit errors are silent and expensive.
## Alternatives considered
Shared governance kit (rejected 2026-10-03 for now: extra versioning overhead; reversed by amendment 1); minimal setup without gates (rejected: Decisya F-01 shows skipped steps go unnoticed).
## Revisit trigger
More than three `port` issues in a quarter → reconsider extracting a shared kit.

## Amendment 1 — shared core Norma instead of a copy (2026-10-16)
In plain words: AetherSpark does not copy Decisya's agent setup. It waits for Norma, a shared core that each project copies as a signed, locked version, and adds only its own layer.

- **Why:** the revisit trigger fired before the copy was made. Five days after another project copied Decisya's setup, most of the shared files had already drifted, and fixes travelled by hand. A third copy would drift the same way.
- **What changes:** item 2 is replaced. WP2.6 takes the first version of Norma that covers AetherSpark's needs. Norma is a private repository whose content is treated as public.
  - The version is copied into `.claude/` through a reviewed PR. `.claude/core.lock.json` holds a SHA-256 for each core file, and a test in CI and pre-push fails if a locked file is edited or missing.
  - The owner verifies the tag signature against a Norma signer line (namespace `git`), kept in this repository next to the Ostium checksums line, and records the result in the PR. No CI step fetches Norma, and no token is needed.
- **Layers:**
  - Norma's core is never edited here: the hook engines, the `gates.py` engine, `lint.py`, `roster.py`, prepush and prereqs, commitlint, the process skills, the role agents, the core rules, and a base `settings.json` deny list with the hook registration.
  - AetherSpark's project layer holds `project.json`, `boundaries.json`, `CLAUDE.md`, the domain skills, the implementer agents and its own guards.
  - The additions from items 3–4 (`data_guard.py`, budgets, identity-tied approvals, G5v, Python tool lanes, `infra-admin`) come as a Python pack, overlay guards, project-layer agents or requested core options.
  - Core rules may be tightened, never loosened without an ADR with an end date.
- **Until then:** AetherSpark keeps its three interim read-only agents and adds no hooks or gate scripts. Changes go through owner review, with those agents as reviewers. When AetherSpark locks Norma, the core `architect` and `security-reviewer` replace the interim ones, and AetherSpark's checks move into their project addenda; `infra-admin` stays in the project layer.
- **Updates:** one reviewed issue per Norma version bump. The locked copy is never edited; a needed difference becomes a request for a core option.
- **Replaces:** item 2; the divergence log in agent-system.md §8 (with no copy, there is nothing to port); the rejection of a shared kit under "Alternatives considered"; the revisit trigger above.
- **Consequences:**
  - Good: one maintained core; fixes arrive as version bumps.
  - Bad: WP2.6 depends on Norma's timing, and until then this repo has no gated pipeline.
- **Revisit trigger:** if the missing pipeline blocks a later phase gate, a new amendment decides whether WP2.6 keeps waiting or takes a reduced setup.
