# 01 — Non-Functional Requirements

All targets apply to the DGX Spark production host unless marked *(dev)*. Each NFR has a verification method; un-verifiable NFRs are not allowed.

## Privacy & security
| ID | Requirement | Verification |
|---|---|---|
| NFR-SEC-01 | No inference request, prompt, document or query result leaves the LAN | Egress firewall default-deny; egress proxy logs show only allowlisted hosts; quarterly test with blocked WAN |
| NFR-SEC-02 | All UIs and APIs require OIDC auth (Keycloak); no anonymous endpoints except health checks on internal VLAN | Automated endpoint scan in CI |
| NFR-SEC-03 | MCP servers use least-privilege credentials (read-only DB roles by default; filesystem allowlist) | Integration tests attempt writes / path traversal → must fail |
| NFR-SEC-04 | No secret material in git (only the manifest); values age-encrypted on the Spark, read from files, never env vars or images (ADR-0010) | `gitleaks` pre-commit + CI; manifest check in CI |
| NFR-SEC-05 | All internal traffic TLS (internal CA) | Certificate inventory check |
| NFR-SEC-06 | Every MCP tool call is audit-logged (who, tool, args hash, result size, latency) | Audit log query in smoke test |

## Performance (initial targets, re-baselined after Phase 6 benchmarks)
Physics note: decode speed is bounded by memory bandwidth (~273 GB/s). Upper bound ≈ 273 GB/s ÷ bytes of *active* weights read per token.

| ID | Requirement | Rationale |
|---|---|---|
| NFR-PERF-01 | Interactive model: ≥ 40 tok/s single-stream decode | MoE with ~3–5 B active params (theoretical ceiling ~60–90 tok/s at FP8/FP4) |
| NFR-PERF-02 | Interactive model: TTFT ≤ 2 s for 8k-token prompt | Prefill is compute-bound; Blackwell FP4/FP8 is strong here |
| NFR-PERF-03 | Batch reasoning model (dense ≤ 70 B, FP4): runs unattended; no interactive SLA | 70 B FP4 ≈ 35 GB/token → ceiling ~7 tok/s |
| NFR-PERF-04 | Embedding throughput ≥ 1 000 chunks/s (512 tokens) | Full re-index of 10 years of filings for a 500-company universe in < 4 h |
| NFR-PERF-05 | XBRL ingest ≥ 50 000 facts/s into ClickHouse | Polars + batched inserts |
| NFR-PERF-06 | Vector search p95 ≤ 50 ms at 10 M vectors (top-k 50, filtered) | HNSW in pgvector; revisit ADR-0007 if missed |

## Reliability & operability
| ID | Requirement | Verification |
|---|---|---|
| NFR-OPS-01 | Full stack from zero with one command (`just up`) | CI smoke job on self-hosted runner |
| NFR-OPS-02 | RPO ≤ 24 h for databases and configs; models/raw data re-downloadable (RPO n/a) | Nightly restic snapshot + verify |
| NFR-OPS-03 | RTO ≤ 4 h from bare DGX OS to working stack | Restore drill per quarter (runbook) |
| NFR-OPS-04 | All services emit OTel traces/metrics/logs with correlation id across gateway → MCP → DB | Trace present in Grafana for smoke query |
| NFR-OPS-05 | Unified-memory budget enforced per service (container limits) — no OOM kill of databases under inference load | Load test: max-context inference + full ingest concurrently |
| NFR-OPS-06 | Reproducible builds: pinned image digests, lockfiles (`uv.lock`, `packages.lock.json`) | CI fails on unpinned dependency |

## Data quality (quant)
| ID | Requirement |
|---|---|
| NFR-DQ-01 | Every fact keeps lineage: accession number, filing date, as-of date, taxonomy concept, unit, context |
| NFR-DQ-02 | Point-in-time correctness: queries can be evaluated "as known on date D" (no look-ahead bias); restatements are new versions, never overwrites |
| NFR-DQ-03 | Ingest is idempotent (re-running a filing yields identical state) |
| NFR-DQ-04 | Validation rules (balance-sheet identity, sign conventions, unit scale, duplicate contexts) produce a quarantined anomaly record instead of failing the batch |
