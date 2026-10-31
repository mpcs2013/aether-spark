# Dev setup (P2, interim desktop)

Once per machine, before the first `just ci`. The Windows 11 host runs the dev loop directly; the Ubuntu WSL2 distro and the dev container (WP2.4) give the Linux userland of DGX OS (sections at the end).

## Tools
| Tool | Version | Install (Windows) | Check |
|---|---|---|---|
| .NET SDK | 10.0.401 or a later 10.0 patch or feature band, the minimum in `global.json` (no prereleases; the dev container uses a newer patch) | Visual Studio installer or `winget install Microsoft.DotNet.SDK.10` | `dotnet --version` in the repo prints `10.0.4xx` |
| uv | current | `winget install astral-sh.uv` | `uv --version` |
| just | current | `winget install Casey.Just` | `just --version` |
| gitleaks | 8.30.1, the version the dev container builds and CI runs (`.devcontainer/Dockerfile`) | `winget install Gitleaks.Gitleaks --version 8.30.1` | `gitleaks version` prints `8.30.1` |

uv fetches Python itself (the 3.12 floor is in `.python-version`); no system Python is needed. Open a new terminal after a winget install so the PATH change applies.

NuGet: the repo `nuget.config` clears inherited package sources and maps everything to nuget.org, so machine-level feeds (for example a private one for another project) do not take part in restore. The `Ostium.*` mapping follows in WP3.5.

## Turn on the git hooks
Once per clone: `just hooks`. It points git at `.githooks/`, whose `pre-commit` runs gitleaks on the staged changes and stops a commit that adds a secret (ADR-0004, ADR-0010). The hook refuses to run with a gitleaks version other than CI's, so a local pass means a CI pass. When CI's gitleaks pin changes, the hook's `expected` value and the version above change in the same pull request.

## Trust the dev certificate
The Aspire dashboard connects to the AppHost's resource service over HTTPS. With an untrusted certificate it fails with `Error #1 connecting to the resource service`.

```
dotnet dev-certs https --trust
dotnet dev-certs https --check --trust
```

The first command shows a Windows dialog (it adds the certificate to the current user's trusted root store). The check must exit 0. Do not work around it with an `http` profile or `ASPIRE_ALLOW_UNSECURED_TRANSPORT` (NFR-SEC-05).

## Verify
```
just ci
dotnet run --project src/dotnet/AetherSpark.AppHost
```

`just ci` is green. The AppHost prints the login URL; the dashboard opens and shows no resource-service error.

| Endpoint | URL (launch profile `https`) |
|---|---|
| Dashboard | https://localhost:17080 |
| OTLP/gRPC | https://localhost:21080 |
| OTLP/HTTP | https://localhost:21081 |
| Resource service | https://localhost:22080 |

The login token changes on every run.

## WSL2 and the GPU (WP2.4)
1. **Budget first.** `%USERPROFILE%\.wslconfig` holds the roadmap budget, before the distro is installed:
   ```ini
   [wsl2]
   memory=32GB
   processors=6
   ```
   Then `wsl --shutdown` (this also stops Docker Desktop's VM for a moment).
2. **Distro by name.** `wsl --install -d Ubuntu-24.04`, then `wsl --set-default Ubuntu-24.04`. Plain `Ubuntu` now installs a newer release than DGX OS uses.
3. **No sparse disk.** WSL refuses `--set-sparse` because of possible data corruption; do not force it with `--allow-unsafe`. The disk file grows and does not shrink by itself; if the system drive runs short, compact it by hand: `wsl --shutdown`, then in `diskpart`: `select vdisk file="<distro folder>\ext4.vhdx"`, `attach vdisk readonly`, `compact vdisk`, `detach vdisk`.
4. **Docker.** Docker Desktop → Settings → Resources → WSL integration → on for `Ubuntu-24.04`.
5. **Check, inside Ubuntu:** `nproc` prints `6`; `free -g` shows about 31 GB; `nvidia-smi` sees the GPU; `docker run --rm --gpus all ubuntu:24.04 nvidia-smi` sees it from a container.

**Driver pin.** GPU passthrough was verified on 2026-10-22 with NVIDIA driver **610.60**. A Windows update can change WSL2 or the driver (roadmap §Interim desktop): after one, repeat step 5; if it fails, roll the driver back to 610.60. Not yet verified: RAPIDS / the Polars GPU engine on Turing (with the first CPU/GPU parity test, ADR-0014).

## Dev container (WP2.4)
`.devcontainer/` builds Ubuntu 24.04 (the DGX OS userland) with the repo's toolchain, every input pinned by digest or checksum: the .NET SDK image (at or above `global.json`'s minimum), uv, just, and gitleaks built from its tagged source with a current Go (the same binary CI's gitleaks job runs). CI builds it and runs `just ci` inside on amd64 and arm64, so the same definition works on the Spark.
- **VS Code:** Command Palette → Dev Containers: Reopen in Container. The container installs the extensions it needs.
- **CLI:** `just container-ci` builds the image and runs `just ci` inside, with the repo mounted.
- **Kept apart from the host:** the container's Python environment (`/home/app/.venv`) and .NET build output (`/home/app/artifacts`) live outside the workspace, so the host's `.venv` and `bin/obj` are never touched.
- **Not yet in the container:** Docker access (needed when the AppHost starts containers, P3a) and the GPU (added with the first GPU job, P4a).

## Done when
- [ ] Tool checks above pass
- [ ] `git config core.hooksPath` prints `.githooks`
- [ ] `dotnet dev-certs https --check --trust` exits 0
- [ ] `just ci` green
- [ ] Dashboard opens at port 17080 without a resource-service error
- [ ] WSL2 checks (step 5) pass
- [ ] `just container-ci` green
