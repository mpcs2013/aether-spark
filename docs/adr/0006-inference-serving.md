# ADR-0006: Inference serving — vLLM primary, LiteLLM gateway
- **Status:** Proposed · **Date:** 2026-10-03
## Decision
- **vLLM** (NVIDIA-provided arm64/Blackwell image) for production serving: OpenAI-compatible API, continuous batching, FP8/NVFP4, prefix caching.
- **Ollama** for dev on the PC only (CPU/small models).
- **LiteLLM** as single gateway: model aliases (`interactive`, `coder`, `reasoner`, `embed`, `rerank`), per-user virtual keys, usage logs to Postgres. Clients and agents only know aliases.
- **Swap policy:** one resident primary model (alias `interactive`). `reasoner` jobs are queued (Wolverine) and executed in a batch window that stops `interactive`, loads the reasoner, drains the queue and restores.
- Weights: safetensors only, pinned revision SHA, checksum verified, stored on local NVMe (`/srv/models`), never in images.
## Alternatives considered
SGLang (strong on Spark per LMSYS benchmarks — run in WP1.4 benchmark as challenger); TensorRT-LLM (best raw perf, heavier build pipeline); Ollama in prod (simpler, weaker batching/observability).
## Revisit trigger
WP1.4 benchmark: challenger beats vLLM by > 20 % on NFR-PERF-01/02.
