# ADR-0006: Inference serving — LiteLLM gateway with model aliases; engine by benchmark
- **Status:** Accepted (gateway and aliases) · engine and models **pending the Spark benchmark** (WP1.4 / WP5b.1) · **Date:** 2026-10-04

## In plain words
The engine is the kitchen, the gateway is the waiter, the aliases are the menu. Apps and agents only ever talk to the waiter and order from the menu; which kitchen and which dish sit behind a menu name can change without code changes.

## Decision — accepted now
- **LiteLLM is the single gateway** (OpenAI-compatible). No app or agent calls an engine directly.
- **Model aliases** are the only names code uses: `interactive`, `coder`, `reasoner`, `embed`, `rerank`.
- **Per-agent and per-user virtual keys**, each tied to its Keycloak identity (ADR-0009); usage and budgets per key; revoking a key is part of the kill switch (ADR-0019).
- **No content in gateway logs:** LiteLLM stores caller, alias, token counts, latency and status only — never prompts or responses (ADR-0011 change 1). Message logging disabled in config and checked by a test.
- **Same aliases in dev and prod:**

| Alias | Desktop (dev) | Spark (prod) |
|---|---|---|
| `interactive`, `coder` | Ollama on Windows, ≤ 4B 4-bit test model (RTX 2060, models on `E:`) | engine + models chosen by the benchmark |
| `embed` | small embedding model on the RTX 2060 | embedding model on the engine |
| `rerank` | small reranker (CPU/GPU) | reranker on the engine |
| `reasoner` | not available — returns a clear "not available in dev" error | large reasoning model (batch window) |

- **Swap policy (default):** one resident `interactive` model; `reasoner` jobs are queued (Wolverine) and run in a batch window that unloads `interactive`, loads the reasoner, drains the queue and restores. Replaced if the benchmark's "two models resident" scenario fits the 70 GB LLM budget.
- **Model files:** safetensors only (GGUF allowed for Ollama in dev), pinned revision, checksum verified, no `trust_remote_code` without review; stored on `/srv/models` (Spark) or `E:` (desktop), never in images; downloaded only via the egress proxy (ADR-0012).

## Decision — pending benchmark (WP1.4 protocol, run in WP5b.1)
- Engine: **vLLM** (default candidate) vs **SGLang** (challenger); TensorRT-LLM only if both miss NFR-PERF-01/02.
- Models per alias from the shortlist in [c4.md](../architecture/c4.md), incl. scenarios single-resident, two-resident, swap batch.
- Acceptance: NFR-PERF-01/02 met, eval thresholds (G5) met, memory budget respected under concurrent ingest.

## Alternatives considered
Ollama in prod (simpler, weaker batching and observability); apps calling engines directly (no single point for auth, budgets, logging).
