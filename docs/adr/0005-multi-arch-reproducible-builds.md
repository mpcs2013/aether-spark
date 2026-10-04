# ADR-0005: Multi-arch, reproducible builds
- **Status:** Accepted · **Date:** 2026-10-03
## Decision
- All custom images built with `docker buildx` for `linux/arm64` (primary) and `linux/amd64` (dev). Before Spark arrives, arm64 builds run under QEMU; on Spark they build natively.
- Base images pinned by digest; .NET central package management with lock files; Python `uv.lock`.
- Image CVE scan with **Grype**, run from a digest-pinned image (fail on High/Critical with a fix available); every CI action and hook dependency pinned by commit SHA or digest. Aligned with Decisya ADR-0014/0015 (amended 2026-10-03, was Trivy). Dependabot for updates (same as Decisya; replaces Renovate).
- `.gitattributes` enforces LF for all text files (repo lives on Windows).
## Consequences
Slower arm64 builds on the PC (QEMU); acceptable until cutover.
