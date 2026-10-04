# 00 — Vision & Scope

## Problem
Quantitative financial research and AI-assisted software engineering currently depend on cloud LLM APIs, which conflicts with the requirement that proprietary data, research and code context never leave the owner's infrastructure.

## Goal
A self-hosted platform where local LLMs reason over local data (filings, market data, code) through MCP servers, with production-grade reliability, observability and security.

## Primary use cases

| ID | Use case | Actors | Key components |
|---|---|---|---|
| UC-01 | Ingest SEC filings (10-K/10-Q/8-K, XBRL) into a normalized fundamentals store | Pipeline | EDGAR ingester, Polars, ClickHouse, Postgres |
| UC-02 | Ask research questions over filings incl. footnotes ("RAG over filings") | Researcher, agent | Gateway, LLM, pgvector, MCP |
| UC-03 | Quantitative screens & factor computations over historical data | Researcher | ClickHouse, Polars (GPU engine on Spark) |
| UC-04 | Anomaly detection: restatements, footnote changes, XBRL inconsistencies | Pipeline, agent | Validation rules, LLM summarization |
| UC-05 | Local coding assistant over own repos (VS Code / CLI) | Developer | Coder model, MCP filesystem/git |
| UC-06 | Scheduled agent workflows (e.g. new-filing digest) | Scheduler | Wolverine jobs, agents, MCP |

## In scope
- Single-node deployment on DGX Spark; interim dev on Windows 11 + WSL2 (lightweight components only).
- Inference, embeddings, reranking, vector search, relational + columnar stores, MCP layer, agent orchestration.
- Identity, secrets, observability, backup/restore.

## Out of scope (v1)
- Multi-node / clustering (second Spark via ConnectX-7) — revisit trigger in ADR-0003.
- Model training / full fine-tuning (LoRA experiments allowed, not production).
- Live trading / order execution. Platform is **research-only**.
- Public internet exposure of any service.

## Explicit exceptions to "zero cloud"
| Exception | Justification | Control |
|---|---|---|
| Source code on GitHub | Collaboration, history, Actions syntax | ADR-0004: no data/weights/plaintext secrets in repo |
| Outbound fetch of public data (SEC EDGAR, model weights from Hugging Face/NGC) | Data and models must be obtained once | ADR-0012: allowlisted egress proxy; pull-only; no outbound payloads with private data |
| OS/container package updates | Security patching | Same egress proxy, allowlisted registries |
| Dev agents on Claude (code only) | Dev productivity; code already on GitHub | ADR-0015: no data, results or secrets in cloud prompts; `data_guard.py` |
