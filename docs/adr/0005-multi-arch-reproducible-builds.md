# ADR-0005: Multi-arch, reproducible builds
- **Status:** Accepted · **Date:** 2026-10-03 · **Amendment** 2026-10-16 (native arm64 runners) · **Amendment 2** Accepted 2026-10-20 (Aspire AppHost without lock file) · **Amendment 3** Accepted 2026-10-31 (image scan policy)
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

## Amendment 3 (2026-10-31, accepted) — image scan policy (#31)
In plain words: every image the repository builds or runs is checked for known vulnerabilities, on both architectures. A High or Critical finding stops the merge whether or not a fix exists, unless the owner has approved a dated exception for it.

- **Scanner:** Grype from a digest-pinned image (`deploy/images/grype.Dockerfile`, so Dependabot proposes its updates). Its database is downloaded at each run, and the report names Grype's version and the database date. Built images reach Grype as a saved archive, so Grype gets no access to the Docker socket.
- **Targets**, found in the tracked files, so a new one cannot skip the scan:
  - every Dockerfile or Containerfile (any name variant) is a build target in `deploy/images/targets.json`, or is excluded there with a reason; anything else fails the scan;
  - every `image` in any Compose file (block or flow style) must be pinned by digest, or be the local tag of an image the repository builds, or the scan fails; each pinned image is a target, test-only images included;
  - base images used only as build stages are covered through the images built from them.
  - Each target is scanned for `linux/amd64` and `linux/arm64`, built natively on that architecture's runner.
  - A result must be for that architecture, a pulled image's result for its pinned digest, and Grype must have detected a distribution (a target may opt out, for example a scratch image); otherwise the scan fails rather than counting an empty result as clean.
- **Fail rule:** any High or Critical finding fails, whatever its fix state (fixed, not fixed, won't fix, unknown), unless an exception covers it. The severity is Grype's (for OS packages, the distribution's rating). Medium and lower, including Unknown, are counted in the report, not failed; an unexpected severity value fails the scan. The rule is code with offline tests (`deploy/images/scan_policy.py`, standard library only), not Grype's ignore-file semantics.
- **Exceptions:**
  - **Where:** `deploy/images/exceptions.json`. The owner's merge of the pull request that adds or extends one is the approval; the pull request's description lists each added or extended exception. An agent may draft one; only the owner merges.
  - **Each names:** the advisory (CVE, GHSA or Go id; a finding's related ids match too), the target, the package, the reason, the issue, the approval date and an expiry at most 90 days later, and optionally the architectures.
  - **Lifecycle:**
    - an expired exception no longer covers its finding, so the finding fails again;
    - one that matches nothing on an architecture is reported, so that its `arches` are narrowed or it is removed;
    - one due within 14 days is reported.
  - **Fail closed:** a malformed file, or an exception for a target that is not scanned, fails the scan.
- **Runs:**
  - on every pull request that changes an image input (Dockerfiles, Compose files, the image folders, the scan itself or the workflows);
  - weekly on `main`;
  - by hand.
  - **Who sees a new advisory:** it fails the weekly run, not unrelated pull requests. GitHub notifies the user who last changed the schedule (the owner, who merges every change), and disables schedules in a public repository after 60 days without activity; the owner re-enables it then. The fix, a bump or an exception, is its own pull request.
- **Updates:** Dependabot proposes image updates weekly (the `docker` and `docker-compose` ecosystems, #46). Where upstream has no fixed release, AetherSpark may build the tool itself from its tagged source, as for gitleaks (#46).
- **Replaces:** in the decision above, "(fail on High/Critical with a fix available)". The alignment with Decisya now holds for its ADR-0015 item 3 as well.
- **Consequences:**
  - Good: an unfixed High or Critical no longer passes silently; every exception is visible, owned and dated.
  - Bad: exceptions need a review at least every 90 days; a vendor gap (a fixed library that no image release carries yet) needs an exception or an own build; each image-changing pull request adds a scan on both architectures.
