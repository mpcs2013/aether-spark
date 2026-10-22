# Dev setup (P2, interim desktop)

Once per machine, before the first `just ci`. Windows 11 host for now; the Ubuntu WSL2 distro and the devcontainer (WP2.4) repeat the certificate step on their side. GPU passthrough and the pinned NVIDIA driver build are added here when WP2.4 verifies them (roadmap §Interim desktop).

## Tools
| Tool | Version | Install (Windows) | Check |
|---|---|---|---|
| .NET SDK | 10.0.401, pinned in `global.json` (no prereleases) | Visual Studio installer or `winget install Microsoft.DotNet.SDK.10` | `dotnet --version` in the repo prints `10.0.4xx` |
| uv | current | `winget install astral-sh.uv` | `uv --version` |
| just | current | `winget install Casey.Just` | `just --version` |
| gitleaks | 8.30.1, the version CI pins in `.github/workflows/ci.yml` | `winget install Gitleaks.Gitleaks --version 8.30.1` | `gitleaks version` prints `8.30.1` |

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

## Done when
- [ ] Tool checks above pass
- [ ] `git config core.hooksPath` prints `.githooks`
- [ ] `dotnet dev-certs https --check --trust` exits 0
- [ ] `just ci` green
- [ ] Dashboard opens at port 17080 without a resource-service error
