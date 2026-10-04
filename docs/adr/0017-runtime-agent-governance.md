# ADR-0017: Runtime agent governance — lanes as server-side authorization
- **Status:** Accepted (conditional: the agent roster is a starting set and will grow, see "Roster growth") · **Date:** 2026-10-03
## Context
Runtime agents read untrusted documents (SEC filings; no web pages, ADR-0008) and query proprietary data. Prompt instructions are not a control. Decisya's dev-plane controls are guardrails; the runtime needs a real boundary.
## Decision
- One governance model with the dev plane: invariants, lanes, verdicts, gates, records ([agent-system.md §1](../ai/agent-system.md#1-the-idea)).
- Lanes are declared in `policy/agents.yaml` and enforced **server-side**: each agent is a Keycloak client with scopes; MCP servers authorise every tool call by scope; DB access via per-agent roles with `statement_timeout` and row limits; egress default-deny (ADR-0012).
- **Quarantined reader**: agents with `untrusted_input: true` have read-only tools and schema-only output; privileged agents never receive raw untrusted text.
- **Claims verifier**: deterministic re-query of every numeric claim (`fact_id`, value, unit, period, `known_date ≤ as_of`); unverified → BLOCK.
- Agent host: .NET 10, `Microsoft.Extensions.AI` against LiteLLM, Wolverine durable sagas for run records, OTel GenAI spans. Agent-framework library selected by a P5 spike.
- Policy lint in CI: no wildcard tools, no write scope with untrusted input, budgets and output schema mandatory.
## Consequences
Injection can at worst corrupt one quarantined extraction, which schema validation and the verifier bound. More components (verifier, policy lint) and more latency per answer.
## Alternatives considered
Single tool-using agent with full access (simplest; injection = full data access); prompt-only rules (not a control).
## Revisit trigger
Eval shows verifier BLOCK rate > 20 % on golden set → revisit claim granularity or extraction schema.

## Amendments at acceptance (2026-10-03)
1. Untrusted text = SEC filings only; agents have no web access (ADR-0008).
2. ADR-0019 applies in full: refused action, honeytoken touch or budget overrun stops the run and alerts; `just agents-off` kill switch.

## Roster growth
The roster in [agent-system.md §5.1](../ai/agent-system.md) is a **starting set**, not a fixed list. More specialised agents are expected. Every new agent follows the same rules:

| Rule | Meaning |
|---|---|
| One job, one badge | Own Keycloak client and scopes; own entry in `policy/agents.yaml` (lint-checked) |
| Evidence first | Added when the eval set shows a category the existing agents handle badly (e.g. citation precision or recall below threshold for that category), not on speculation |
| Untrusted text stays quarantined | An agent that reads raw filing text gets `untrusted_input: true`: read-only tools, schema-only output, no write scopes |
| Same model by default | Specialists reuse the resident `interactive` model with their own instructions, tools and output schema → no extra memory. A specialist needing a different model joins the batch window (model swap, ADR-0006) |
| Eval before production | New agent ships with its eval questions; G5v eval regression must pass |
| Hand-off cost | Each extra agent adds model calls and latency on a bandwidth-bound Spark; the planner may only add a specialist to a run when its category is in the question |

Candidate specialists (not built until evidence asks for them): statement-specific readers (income statement, balance sheet, cash flow), footnote/accounting-policy reader, risk-factor change detector (year-over-year 10-K diff), peer-comparison analyst, restatement investigator, **news/sentiment analyst** using a finance-tuned small model (e.g. FinGPT-style or another finance fine-tune, ~8B; noted 2026-10-03) — precondition: a licensed news dataset and its own ADR for news ingestion, since agents have no web access (ADR-0008).
