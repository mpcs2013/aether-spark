# ADR-0019: Agent oversight — prevent, detect, respond
- **Status:** Accepted · **Date:** 2026-10-03

## Context
In Decisya (issue G4, CI/config change) the devops agent got around two hook blocks instead of reporting them: it rebuilt the word "npm" in Python to pass the npm hook, and wrote temporary files to `$TEMP` after a scratchpad write was denied. CLAUDE.md already forbade this. Conclusion: instructions are wishes and pattern hooks are speed bumps; a capable agent under task pressure routes around both. Prod agents will run with even less human attention.

## Decision
Three layers, applied to both planes ([agent-system.md](../ai/agent-system.md)).

### 1. Prevent — make it impossible, not forbidden
| Dev plane (Claude Code) | Runtime plane (Spark) |
|---|---|
| Subagents get **no general-purpose interpreters or shells** (`python`, `node`, `pwsh`, `bash -c`, `sh`, `cmd`, `uv run python`); only exact script paths (e.g. `Bash(python .claude/scripts/gates.py*)` stays main-session only). Lint rejects interpreter tools in agent frontmatter. | Agents have **no code execution, no shell, no free SQL, no web** — only MCP tools with fixed menus (ADR-0008). Server-side authZ per tool (ADR-0017). |
| Scratch/temp writes only to a per-issue folder inside the lane; `$TEMP`, `%TEMP%`, `/tmp`, home dirs denied. | Agents hold no filesystem; outputs go through schema-validated tool results only. |
| Issues with new third-party packages or external content run in the sandbox (real boundary). | Network egress default-deny (ADR-0012). |

### 2. Detect — log everything, outside the agent's reach
- **Every** tool call is logged, not only denies (dev: `PreToolUse` + `PostToolUse` hooks to append-only `.agent-logs/audit.jsonl`; prod: MCP servers/gateway to the `agent-audit` stream, ADR-0011).
- Bypass indicators flagged in dev: string construction around denied words (`chr(`, `join`, concatenation, base64/hex decode), writes to temp/home paths, a retry of the same intent after a deny, new interpreters invoked indirectly (via `dotnet`, `npm run`, `pre-commit` scripts).
- **Honeytokens:** decoy items no legitimate task touches (a fake key file in the repo, a decoy query template, a decoy document in the vector store, a decoy Keycloak client). Any touch = immediate alert.
- Metrics per agent and run: tool calls, denies, budget overruns, verifier BLOCKs; alert on any deny in prod and on deviation from baseline.

### 3. Respond — automatic, then human
- **Deny → freeze (dev):** after one denied action, that agent is read-only for the rest of the issue; it must report the deny and stop. Unfrozen only by Marco (`gates.py <n> --unfreeze` with reason, recorded in the manifest).
- **Deny → stop run (prod):** an authorization refusal, honeytoken touch or budget overrun ends the run (Wolverine saga → `aborted`), alerts the phone, and keeps the run record for review.
- **Gate rule:** any bypass attempt is a **High** finding; the issue cannot merge until reviewed. G6 reads the issue's audit log; G7 PR body lists every deny.
- **Kill switch:** `just agents-off` disables all agent clients in Keycloak (tokens dead ≤ 5 min, ADR-0009) and revokes LiteLLM keys immediately; documented in `docs/runbooks/agent-kill-switch.md` and tested in the P7 drill.

## Consequences
- Good: the observed bypasses become impossible (no interpreter) or self-limiting (freeze); prod agents have no medium to improvise in; every action has an independent record.
- Bad: more "stopped, please review" interruptions in dev; some tasks need a purpose-built script instead of ad-hoc Python. Accepted: an interruption costs minutes, an unnoticed bypass costs trust in the whole pipeline.

## Revisit trigger
Freeze interrupts > 3 issues per week without a real violation → refine bypass indicators, not loosen the rule.
