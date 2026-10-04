# ADR-0011: Observability — OpenTelemetry end to end
- **Status:** Accepted (with changes 1–4) · **Date:** 2026-10-03

## In plain words
Logs = the diary, metrics = the gauges, traces = a parcel-tracking number that follows one request through every service. All in one standard (OpenTelemetry), shown in Grafana.

## Decision
- All .NET services via Aspire ServiceDefaults (OTel); Python via OTel SDK; vLLM/LiteLLM Prometheus metrics scraped; DCGM exporter for GPU, node-exporter for unified-memory pressure.
- Dev: Aspire dashboard. Prod: OTel Collector → Grafana stack.
- Dashboards and alert rules as code in `deploy/observability/`.

### Change 1 — no research content in telemetry
Logs, metrics and traces carry events, sizes, timings, ids and token counts only — never prompts, model outputs, filing text or research numbers. OTel GenAI content capture stays **off**. PII/secret masking as in Decisya (`[Sensitive]`, no tokens/cookies/claims in logs). Enforced by a log-processor filter and a test that fails on known content fields.

### Change 2 — one all-in-one container first
`grafana/otel-lgtm` (Grafana, Loki, Tempo, Prometheus/Mimir, OTel Collector in one image) within the 8 GB platform budget; operational retention **30 days**. Split into separate services only on measured need.

### Change 3 — few, actionable alerts to the phone
Disk > 85 %, unified-memory pressure, nightly backup failed, certificate expiring < 14 days, service down > 5 min, plus the agent alerts of ADR-0019. Delivery via self-hosted ntfy (or email); the message states what broke, never data.

### Change 4 — separate agent activity log (ADR-0019)
- Its own append-only stream (`agent-audit`), apart from operational logs: every agent tool call (who, tool, arguments hash, result size, verdict, latency, run id), every deny, every budget overrun, every honeytoken touch.
- Written by MCP servers / gateway / hooks — never by the agent itself.
- Retention **1 year**; included in nightly backups; dashboard "Agent behaviour" (calls, denies, budget overruns, verifier BLOCKs per agent and run).

## Consequences
Good: one view across C# and Python; research data never duplicated into telemetry; agent behaviour reviewable for a year. Bad: debugging without content is harder (use run ids to look up content in the primary store, access-controlled).
