# ADR-0002: Hybrid .NET 10 / Aspire 13 + Python
- **Status:** Accepted · **Date:** 2026-10-03 · **Amendment 1** Accepted 2026-10-03 (language split and boundary rules)
## Context
Owner's core expertise and existing stack is .NET 10, Aspire 13, Wolverine, EF Core 10, Keycloak, xUnit; Decisya provides reusable .NET security, observability and agent-pipeline code. The ML/quant ecosystem (vLLM, Polars, cuDF/RAPIDS, Arelle for XBRL) is Python-native, and the Spark's GPU dataframe path has no .NET equivalent.

## Decision
**C# is the control plane** (orchestration, APIs, security boundary, durable workflows). **Python is the compute plane** (data, analytics, ML). Neither does the other's job.

### Component split
| Component | Language | Reason |
|---|---|---|
| Aspire AppHost (dev loop, `aspire publish` → Compose) | C# | Orchestrates both languages; Python integration with `uv` verified in WP2.2 |
| MCP servers | C# | Official C# MCP SDK; Keycloak scope authZ; JWT validation from Ostium (note below) |
| Agent host (run records, budgets, durable workflows) | C# | Wolverine sagas, Microsoft.Extensions.AI (Decisya ADR-0004 pattern) |
| Claims verifier | C# | Deterministic re-query; exact `decimal` comparison |
| EDGAR fetcher and scheduler | C# | Rate limiting, retry, idempotent jobs (Polly, Wolverine) |
| ServiceDefaults (OTel), Testcontainers fixtures, architecture tests | C# | Reused from Decisya; Keycloak fixture from Ostium (note below) |
| XBRL parsing and normalization | Python | Arelle; Polars |
| Factors, screens, backtests, anomaly statistics | Python | Polars, NumPy; GPU engine (cuDF) on the Spark |
| Evaluation harness, notebooks | Python | Metrics and analysis ecosystem |
| Model serving and gateway | Neither | Upstream vLLM / LiteLLM containers; configuration only |

*Note (2026-10-11):* JWT validation and the Keycloak test fixture come from the shared identity kit Ostium, no longer ported from Decisya (ADR-0009 amendment 1).

*Note (2026-10-22), WP2.2 check of the AppHost row:* `Aspire.Hosting.Python` 13.6.2 runs a workspace module as a job from the AppHost on the desktop (Windows): `AddPythonModule` from the repository root with `WithUv(args: ["sync", "--all-packages", "--locked"])`. The job ran in the uv-managed `.venv` (Python 3.12), imported the workspace package, and received the OTLP endpoint, service name and resource attributes, so its telemetry reaches the dashboard without extra wiring. `--all-packages` is required, because a plain `uv sync` installs no workspace members; `--locked` keeps the job on `uv.lock`. The check was a job that exits, matching boundary rule 1; no Python listener was tried. Not yet checked: `aspire publish` of a Python resource to Compose and its arm64 build, which come with Compose publishing (ADR-0003); the revisit trigger stays open until then. The check's code was not kept: the first Python job (P4a) adds the package and its resource.

### Boundary rules
1. **The boundary is data, not calls.** Python writes to ClickHouse, Postgres or Parquet; C# reads through read-only roles. C# may *trigger* a Python job (Wolverine-scheduled container run with arguments) and read its status from a `job_runs` table and the exit code. No HTTP or RPC API between C# and Python code; no pickles.
2. **Contracts are defined once.** JSON Schema files in `contracts/` are the source for shared types; C# records and Pydantic models are generated from them. CI fails when generated code is stale (`just contracts --check`). ClickHouse/Postgres DDL is the contract for tables.
3. **No analytics in C#, no security in Python.** C# never computes financial metrics (ratios, factors, returns, statistics). Python never makes authorization decisions and exposes no network listener; it runs behind C# MCP servers and DB roles.
4. **Exact values for reported numbers.** Reported financial-statement values are `Decimal(38, s)` in ClickHouse, `decimal` in C#, Decimal in Polars. Derived analytics (returns, factors) use float64. The verifier compares reported values exactly and derived values within a documented tolerance.

### Enforcement
| Rule | Enforced by |
|---|---|
| 1 | Compose lint: Python services publish no ports and join no ingress network; architecture test: no `HttpClient` registration targeting Python services |
| 2 | CI codegen check; G2 architect note lists any new contract |
| 3 | Architecture test bans numeric/statistics packages (e.g. MathNet) in C# projects; Python services carry no Keycloak/JWT dependencies (`uv` dependency check); G2 architect and G6 security-reviewer check the split |
| 4 | Migration test: columns tagged `reported` must be `Decimal`; G5v quant-validator checks round-trips |

## Consequences
- Good: the security-critical layer is in the owner's strongest language and reuses Decisya; the compute layer uses the only ecosystem with GPU dataframes; .NET 10 runs natively on linux-arm64.
- Bad: two toolchains in CI (`dotnet` + `uv`), a codegen step for shared schemas, and two dependency-update streams. Strict typing on both sides (`<Nullable>enable</Nullable>` + warnings as errors; `mypy --strict`).

## Alternatives considered
- Python-only: one toolchain, but loses the owner's productivity, Decisya reuse and Aspire; the security layer would be written in the weaker language for the owner.
- .NET-only: fights the ML ecosystem; no GPU dataframe path, no mature XBRL processor.
- C# ↔ Python HTTP API (original wording of this ADR): rejected in amendment 1; it creates a second security surface in Python and hides the data contract.

## Revisit trigger
- Aspire's Python integration blocks arm64 publishing or GPU device mapping.
- Rule 3 is broken more than twice in a quarter (exceptions recorded in G6 reviews) → reconsider the split.
