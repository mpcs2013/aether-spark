# ADR-0008: MCP architecture & tool security
- **Status:** Accepted (with changes: no `fetch` server in v1; template-only queries) · **Date:** 2026-10-03

## In plain words
AI agents never touch databases or files directly. They ask **MCP servers** ("bank counters"), which check the agent's ID (Keycloak token), decide, fetch, and log.

## Decision
- MCP servers in **.NET 10 (official C# MCP SDK)**, one bounded context per server: `filings`, `fundamentals`, `vector-search`, `repo` (ADR-0002: security layer in C#).
- Transport: **streamable HTTP** behind Traefik with OAuth 2.1 tokens from Keycloak (MCP authorization spec); every agent is its own Keycloak client with scopes (ADR-0017). **stdio** only for local dev clients.
- Least privilege: read-only DB roles per server; `repo` path allowlist; write tools are separate, flagged and require human confirmation.
- Every tool call audit-logged + OTel span (NFR-SEC-06). Outputs size-capped and paginated.

### Change 1 — no web access for agents in v1
- **No `fetch` server.** No planned use case needs agent web browsing; SEC data is downloaded by the C# EDGAR ingester (deterministic code), not by agents.
- Removes the main exfiltration path for a prompt-injected agent (threat T1/T12). Re-adding web access requires its own ADR.

### Change 2 — template-only queries
- `fundamentals` exposes a **fixed catalogue of parameterised query templates** (e.g. `metric_series(cik, concept, from, to, as_of)`, `peer_compare(ciks, concept, period, as_of)`); agents fill parameters, never write SQL.
- Every template requires `as_of` (point-in-time, ADR-0007). DB role adds `statement_timeout` and row limits as a second line of defence.
- New templates are added by code change through the dev pipeline (G2 architect + G6 review).

## Consequences
- Good: uniform auth, logging and limits; no free SQL and no web path for injected instructions.
- Bad: new research questions sometimes need a new template (a code change) before an agent can answer them; slightly more setup than stdio-only.

## Alternatives considered
Allowlisted `fetch` server (rejected for v1, change 1); free read-only SQL with row limits (rejected, change 2: still allows expensive or unintended queries and look-ahead by omitting `as_of`).
