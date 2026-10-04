# ADR-0014: Quant compute — Polars with GPU engine, CPU as reference
- **Status:** Accepted (with CPU/GPU parity test) · **Date:** 2026-10-03

## In plain words
Polars is a very fast spreadsheet engine for code. The same code runs on the CPU or, with one setting, on the GPU. The CPU result is the truth; the GPU must prove it gives the same answer.

## Decision
- Polars LazyFrame is the single dataframe API. Pandas only at library boundaries; NumPy for vectorised numerics; no Python row loops in pipelines (lint rule + review).
- GPU: `collect(engine="gpu")` (cuDF backend) with automatic CPU fallback for unsupported operations. Desktop RTX 2060 (Turing) for early tests, DGX Spark (Blackwell) in production.
- **CPU is the reference implementation.** A **parity test** runs the golden test set (WP4.4) on CPU and GPU:
  - reported values (`Decimal`, ADR-0002 rule 4): **exact** match;
  - derived values (float64 ratios, returns, factors): match within a documented tolerance (default relative 1e-9);
  - row counts, nulls and ordering of keys: exact.
- A step that fails parity is pinned to CPU (`engine="cpu"` with a comment linking the issue) until fixed.
- Parity runs: first on the desktop GPU (P4a, after WSL2 GPU passthrough and RAPIDS support for Turing are verified in P2), again on the Spark at cutover (WP6.4), then nightly on the Spark. G5v (quant-validator) checks it for every `data-pipeline` change.

## Consequences
- Good: one code path for dev and prod; GPU speed without trusting it blindly; financial numbers never depend on GPU rounding.
- Bad: GPU engine memory comes from the unified pool (covered by the 12 GB headroom, large jobs in the batch window); some steps may stay CPU-only.

## Revisit trigger
GPU engine coverage insufficient for core transforms → direct cuDF for those stages, under the same parity test.
