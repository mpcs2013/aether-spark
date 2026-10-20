# ADR-0005: Multi-arch, reproducible builds
- **Status:** Accepted · **Date:** 2026-10-03 · **Amendment** 2026-10-16 (native arm64 runners) · **Amendment 2** Accepted 2026-10-20 (Aspire AppHost without lock file)
## Decision
- All custom images built with `docker buildx` for `linux/arm64` (primary) and `linux/amd64` (dev). Before Spark arrives, arm64 builds run under QEMU; on Spark they build natively.
- Base images pinned by digest; .NET central package management with lock files; Python `uv.lock`.
- Image CVE scan with **Grype**, run from a digest-pinned image (fail on High/Critical with a fix available); every CI action and hook dependency pinned by commit SHA or digest. Aligned with Decisya ADR-0014/0015 (amended 2026-10-03, was Trivy). Dependabot for updates (same as Decisya; replaces Renovate).
- `.gitattributes` enforces LF for all text files (repo lives on Windows).
## Consequences
Slower arm64 builds on the PC (QEMU); acceptable until cutover.

## Amendment (2026-10-16) — native arm64 runners
arm64 builds and tests run on GitHub-hosted native arm64 runners (ADR-0004, roadmap WP2.5); QEMU is not used. Production images build natively on the Spark from a release tag. Replaces "before Spark arrives, arm64 builds run under QEMU" above, and the consequence "slower arm64 builds on the PC".

## Amendment 2 (2026-10-20) — Aspire AppHost without lock file
The Aspire AppHost SDK adds its dashboard and orchestration (DCP) packages for the platform the build runs on (`Aspire.Dashboard.Sdk.<rid>`, `Aspire.Hosting.Orchestration.<rid>`). A lock file written on Windows therefore fails the locked restore on the Linux amd64 and arm64 runners, and no single lock file fits all three. The first CI run on `main` failed this way.

- `AetherSpark.AppHost` restores without a lock file. Its direct package versions stay pinned by central package management; only its transitive graph is not locked.
- Every other .NET project keeps its lock file and the locked-mode restore.
- The AppHost must never reference an `Ostium.*` package, as ADR-0009 checks those hashes against the lock file. Its build stops with an error if one is added.
- Accepted because the AppHost is a development and `aspire publish` tool; it is not part of any deployed image.
- Rejected: explicit references for every platform (`SkipAddAspireDefaultReferences`), which downloads three platforms' packages on every restore and lets their build properties compete over which binary runs.

Revisit when Aspire offers a lock-file-friendly way to restore these packages, or if the AppHost ever ships inside a deployed image.
