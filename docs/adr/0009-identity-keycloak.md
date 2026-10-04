# ADR-0009: Identity — Keycloak OIDC everywhere
- **Status:** Accepted (with changes 1–3) · **Date:** 2026-10-03

## In plain words
Keycloak is the reception desk: people and agents check in once and get a badge (token); every other component only checks the badge.

## Decision
- Single realm `aetherspark`; OIDC clients for Grafana, LiteLLM UI, agent host, MCP servers.
- One **client-credentials service account per runtime agent** with only its scopes (ADR-0017).
- Human users: **MFA required** — WebAuthn (security key / passkey, works in Firefox) preferred, TOTP fallback.
- Traefik forward-auth for UIs without native OIDC.
- No BFF: AetherSpark has no browser SPA of its own (UIs are Grafana/LiteLLM with native OIDC or forward-auth).

### Change 1 — short-lived tokens
Access tokens ≤ 5 min, RS256/ES256 only; validation of `iss`, `aud`, `exp`, algorithm allow-list (same rule as Decisya CLAUDE.md). Agents renew automatically via client credentials.

### Change 2 — realm as code
Realm export (clients, roles, scopes, agent service accounts, flows) in `deploy/keycloak/aetherspark-realm.json`, imported at start; no manual admin-console changes. Secrets are not in the file (generated and kept off git, ADR-0010). Realm guard tests (pattern from Decisya `realm-guard-cases.json`) fail CI on risky settings (e.g. implicit flow, wildcard redirect URIs, long token lifetimes).

### Change 3 — break-glass admin
One emergency admin account in the `master` realm, credentials stored offline, never used day to day; use is logged and reviewed. Runbook: `docs/runbooks/keycloak-break-glass.md` (P3a).

### Extraction-ready (reuse)
Identity code (JWT validation, client-credentials token handling, realm-as-code fixtures and guard tests) lives in a self-contained `AetherSpark.Identity` project with no domain dependencies, so it can later move into a shared identity kit together with Decisya's (see roadmap, "Future initiatives").

## Consequences
Good: one login system, reproducible from git, stolen tokens expire fast. Bad: Keycloak becomes a hard dependency at startup; the break-glass process needs discipline.
