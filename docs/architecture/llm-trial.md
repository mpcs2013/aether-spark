# LLM serving, first light on the Spark

*Status: proposed 2026-11-07 for a first run on 2026-11-08 (owner's decision: AetherSpark's own serving first, as a trial before WP3.1 PR 4; personal use later through the same serving, decided separately).*

**In plain words.** The Spark runs a language model for the first time, the way AetherSpark will run models for good: the model's files come in through the ops proxy and are checked byte for byte; the model server runs in a container that has no way out and no door in; and we measure whether the Spark is as fast as the plan assumes. Nothing else of the stack is needed yet: no LiteLLM gateway, no Keycloak, no browser page. It is the first slice of WP5b.1 (the engine and model benchmark), done early.

## What it is, and what it is not
- **One engine, one model, one measurement.** vLLM, the default candidate of [ADR-0006](../adr/0006-inference-serving.md), from NVIDIA's container `nvcr.io/nvidia/vllm:26.10-py3` (pinned by digest, arm64 for the Spark). The model is **Qwen3-30B-A3B in FP8** (Apache 2.0), a shortlist model for the `interactive` alias ([c4.md](c4.md), "Model shortlist"), served under that alias name. The measurement is benchmark scenario 1 (one resident interactive model) against NFR-PERF-01 (at least 40 tokens/s single-stream decode) and NFR-PERF-02 (first token within 2 s for an 8k-token prompt).
- **Not** the benchmark itself (SGLang, other models, two-model and swap scenarios stay WP5b.1), **not** the gateway (LiteLLM and its keys come with WP5b.2), **not** a chat page, and **not** a decision of ADR-0006's pending part. Its numbers are recorded as a first data point for WP5b.1.

## How it is built
| Piece | Where | Why this way |
|---|---|---|
| The model's pin | `deploy/models/qwen3-30b-a3b-fp8.source` (repository and commit) and `.sha256` (all 17 files) | ADR-0006: pinned revision, checksum verified. Hugging Face publishes SHA-256 for the 9 large files; the 8 small ones were computed on 2026-11-07 and their sizes checked against Hugging Face's listing |
| The download | Service `model-pull` in the egress Compose (profile `model-pull`), script `deploy/compose/egress/model-pull.sh`, in the pinned curl image already used by the egress tests | A one-shot pull job on `egress-ops` (role `pull-job`), only while the ops proxy runs. It writes into `/srv/models/<repository>/<revision>.partial/`, checks every file, resumes after an interruption, and renames the folder only when all 17 files match. Root inside the container with every capability dropped: it can write only into the root-owned model store |
| The model store | `/srv/models` on the Spark, owned by root, mode 0755 | ADR-0006: models on `/srv/models`, never inside an image; servers mount it read-only |
| The server | `deploy/compose/llm-trial/compose.yaml`, service `vllm` (role `llm-serving`) | Only the internal `llm` network of the membership matrix (bridge `as-llm`), so no route out and no published port. GPU through the NVIDIA runtime, every capability dropped, `no-new-privileges`, phone-home settings off (lint L1). Half of the unified memory (`--gpu-memory-utilization=0.5`): the model (about 31 GB) and its cache stay inside the 70 GB LLM budget |
| Access | `docker compose exec vllm …` in an SSH session | No new way into the Spark; the measurement runs inside the container against `127.0.0.1:8000` |

**Two hosts added to the ops allowlist** (egress design, "Literal allowlists"; both observed on 2026-11-07 from the home network, as "observe, then allow" asks):
- `layers.nvcr.io`: NVIDIA's registry redirects image layers there (the vLLM image is 11.2 GB compressed for arm64);
- `us.aws.cdn.hf.co`: Hugging Face redirects the model's weight files there (32.5 GB). The download uses plain HTTPS, so Hugging Face's Xet hosts are not needed.

If the Spark is sent to other hosts on the day, the ops proxy refuses them and the pull stops; a new host is added only after it is seen in the proxy's log and recorded here.

## Image scan
The vLLM image is a pulled Compose image, so CI's scan covers it on both architectures (ADR-0005 amendment 3): every High or Critical finding needs the owner's exception for at most 90 days, unless a later digest removes it.

**Result (CI, 2026-11-07, both architectures alike): 12 findings, all High, none Critical.** The owner approved exceptions on 2026-11-08, until 2026-12-08 (issue #31):

| Findings | Why acceptable for the trial |
|---|---|
| ffmpeg: CVE-2026-52296, -52297, -70628, -70632, -96611 (fixed in ffmpeg 9), CVE-2026-107696 (no fix) | Audio and video decoding for multimodal requests; the trial's model is text-only and only the owner sends requests |
| vLLM: GHSA-6757-hggm-hp2w, GHSA-h3rc-j82p-2c3h (fixed in vLLM 0.32.0; NVIDIA's latest image, 26.10, ships 0.31) | Both need multimodal or image requests (a shared-memory multimodal cache the trial does not enable; image decoding) |
| apt, apt-utils, libapt-pkg: CVE-2026-109518; libbrotli1: CVE-2026-111497 | The same advisories as the other Ubuntu images; apt never runs in the server, nothing decompresses untrusted Brotli data |

The same CI run showed a new Critical in Alpine's zlib (CVE-2026-4738, 2026-11-08) in every Alpine-based image, unrelated to the trial: step-ca (built here) takes the fixed zlib 1.4-r0; Traefik and the curl and Python images get exceptions until their makers rebuild, also until 2026-12-08.

## Before and after the run
- **The home infrastructure is told first** (A-012: "AetherSpark says here before its first heavy jobs run"): the shelf is warmer than planned (Lares' 28 °C pause rule) and the UPS arrives on 2026-11-09. The Spark holds no data, so a power cut costs only the run.
- **Afterwards:** the server is stopped (`docker compose down`), the ops proxy too; the model stays in `/srv/models` for WP5b.1. The numbers go into this note.
- **Removal:** this Compose file goes when WP5b.1 brings the real serving behind LiteLLM.

## Concepts
- **LLM serving:** a program (here vLLM) loads a model's weights into the GPU's memory once and then answers requests, many at a time, in the OpenAI-compatible format other software already speaks.
- **Weights, FP8:** a model is billions of numbers. FP8 stores each in one byte instead of two, half the memory, with almost no loss; Blackwell computes FP8 natively.
- **Mixture of experts (the "A3B"):** the model has 30 billion numbers, but only about 3 billion are used per token. It needs memory like a 30B model and runs about as fast as a 3B one, which is why the plan expects 40+ tokens per second.
- **Tokens per second, time to first token:** how fast text comes out once it has started, and how long the first piece takes after a long question. The two numbers the plan's speed requirements name.
- **Pinned revision, checksum:** the exact version of the files, and a fingerprint of each file's bytes. If even one byte differs, the check fails and the file is not used.
