# ADR index
| # | Title | Status |
|---|---|---|
| 0001 | Record architecture decisions | Accepted |
| 0002 | Hybrid .NET 10 / Aspire 13 + Python (C# control plane, Python compute plane) | Accepted; amendment 1 Accepted |
| 0003 | Docker Compose single-node runtime | Accepted |
| 0004 | GitHub (private) + self-hosted runner | Accepted |
| 0005 | Multi-arch, reproducible builds | Accepted |
| 0006 | Inference: vLLM + LiteLLM gateway | Proposed |
| 0007 | Data stores: Postgres 18/pgvector, ClickHouse; Redis optional | Accepted |
| 0008 | MCP architecture & tool security (no web fetch in v1, template-only queries) | Accepted |
| 0009 | Identity: Keycloak OIDC (5-min tokens, realm as code, break-glass) | Accepted |
| 0010 | Secrets: generated, age-encrypted, kept off git (manifest only) | Accepted |
| 0011 | Observability: OpenTelemetry → Grafana (no research content; agent activity log 1 y) | Accepted |
| 0012 | Network: RT2600ac (SRM 1.3) with 3 networks; egress allowlist on the Spark; 1 GbE v1 | Accepted |
| 0013 | Backup: restic to NAS Volume 2 (append-only), weekly offline USB disk | Accepted |
| 0014 | Quant compute: Polars GPU engine, CPU reference + parity test | Accepted |
| 0015 | Cloud LLM (Claude) for code only, never data | Accepted |
| 0016 | Dev-time agent pipeline adapted from Decisya | Accepted (design) |
| 0017 | Runtime agent governance: server-side lanes, quarantine, verifier; roster grows by evidence | Accepted (conditional) |
| 0018 | VS Code as the primary IDE (docs: VS Code \| CLI) | Accepted |
| 0019 | Agent oversight: prevent, detect, respond (no interpreters, deny → freeze, honeytokens, kill switch) | Accepted |
| 0020 | Research universe: float ≥ USD 700M point-in-time, two tiers, FY2011+, CIK identity | Accepted |
