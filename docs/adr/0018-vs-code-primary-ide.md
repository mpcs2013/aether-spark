# ADR-0018: VS Code as the primary IDE
- **Status:** Accepted · **Date:** 2026-10-03
## Context
Decisya documents every step as *VS 2026 | CLI*. AetherSpark differs: about half the code is Python, the runtime target is ARM64 Linux (DGX OS), development runs in WSL2/devcontainers, and Claude Code is the main dev-agent tool.
## Decision
- **VS Code** is the primary IDE. Every developer step is documented as *VS Code | CLI* side by side.
- Baseline extensions (pinned in `.vscode/extensions.json`): C# Dev Kit, Python + Pylance, Ruff, Remote - WSL, Dev Containers, Remote - SSH, Docker/Container Tools, Claude Code, YAML, Even Better TOML, GitHub Actions. Aspire tooling for VS Code is checked in P2 (WP2.2); if debugging the AppHost from VS Code falls short, `dotnet run --project <AppHost>` + attach is the documented path.
- Workspace settings and recommended extensions are committed; `.vscode/settings.json` holds no secrets or machine paths.
- VS 2026 stays **optional** (e.g. profiler, advanced .NET diagnostics); nothing in the repo depends on it.
## Consequences
- Good: one IDE for .NET, Python, YAML/Compose and Markdown; native Remote-WSL, devcontainer and **Remote-SSH into the Spark** (VS 2026 cannot develop on ARM64 Linux); best Claude Code integration.
- Bad: weaker .NET refactoring/profiling than VS 2026; C# Dev Kit is licensed like VS Community (free for individuals; check if the use ever becomes commercial).
## Revisit trigger
.NET-heavy work where missing VS 2026 diagnostics costs more than a day.
