# 02 — Threat Model (STRIDE-lite)

## Assets
A1 proprietary research & prompts · A2 derived datasets/signals · A3 source code · A4 credentials · A5 model weights (integrity)

## Trust boundaries
```mermaid
flowchart LR
  subgraph WAN[Internet - untrusted]
    EDGAR[SEC EDGAR]; HF[HF / NGC registries]; GH[GitHub]
  end
  subgraph LAN[Home LAN]
    subgraph CLI[Home network clients]
      PC[Dev PC / VS Code]; Phone[Mobile]
    end
    subgraph SRV[AetherSpark network - VLAN 20, wired]
      PXR[Runtime egress proxy]; PXO[Ops egress proxy<br/>owner-started only]; RP[Reverse proxy + OIDC]; SPARK[DGX Spark services]
    end
    NAS[(Backup NAS - Home + VLAN 20, no routing)]
    subgraph GST[Guest/IoT - VLAN 30, isolated]
      IOT[TV / IoT / visitors]
    end
  end
  PC -->|HTTPS + OIDC| RP --> SPARK
  SPARK -->|ingestion jobs, runtime list| PXR --> EDGAR
  SPARK -->|owner-started jobs, ops list| PXO --> HF
  PC -->|git push| GH
  SPARK -->|restic, append-only| NAS
  PC -->|restic, append-only (workstation backup)| NAS
```

## Threats & controls
| # | Threat | STRIDE | Control | ADR |
|---|---|---|---|---|
| T1 | Prompt/data exfiltration via an agent tool making outbound HTTP | I | No route out for agent, MCP, serving and database containers (internal networks; host firewall in CI and on the Spark); proxy only for ingestion jobs (runtime list, identifiers from the universe only) and owner-started jobs (ops list); no `fetch` MCP server. The router itself allows outbound from VLAN 20, so the control is on the host ([egress design](architecture/egress.md)) | 0012, 0008 |
| T2 | Prompt injection in filings/web content causes destructive tool calls | T/E | MCP read-only by default; write tools require human confirmation; tool allowlist per agent | 0008 |
| T3 | Malicious/compromised model weights or pickle payloads | T | safetensors only; pin revision SHA; checksum verify; no `trust_remote_code` unless reviewed | 0006 |
| T4 | Supply chain in containers / Python / NuGet | T | Pinned digests and SHAs, lockfiles, Grype scan in CI, Dependabot PRs; agents cannot add packages | 0005, 0016 |
| T4b | Private package feed or shared-kit repo abused: dependency confusion, tampered or deleted release, stolen restore token, fork access to granted packages | T/I/D | Package source mapping (private prefix to the private feed only); exact pins + locked restore with content hashes; signed tags, SBOM and source commit per package; signed package hashes checked by the owner against a pinned key on every version change; no version deletion + local feed copy in backups; read-only, expiring restore token denied to dev agents; owner 2FA and the kit repo's protected main branch and tag ruleset; accepted residual for forks (compiled packages only) | 0009, 0004, 0005, 0010 |
| T5 | Secret leakage to GitHub | I | Secrets never in git (manifest only), gitleaks pre-commit + CI, runner without cloud creds | 0004, 0010 |
| T6 | Strangers' PRs run code via CI (public repo) | E | No self-hosted runner; GitHub-hosted runners only; fork-PR workflows need owner approval; read-only `GITHUB_TOKEN`; no `pull_request_target` with PR checkout | 0004 |
| T6b | Site details of the home installation published in a public repo | I | Public-content rule; site details only in git-ignored `ops.local/` | 0004 |
| T7 | Lateral movement from IoT/guest Wi-Fi | E | VLAN isolation; inter-VLAN deny except client→reverse proxy | 0012 |
| T8 | Ransomware / accidental deletion | D | Append-only restic repo on NAS + offline rotating disks; time-window retention + new-snapshot guard against forged snapshots; the desktop's local-only data in the workstation backup with the same controls (ADR-0013 Amendment 5) | 0013 |
| T9 | Unauthenticated access to model APIs | S | All APIs behind gateway with OIDC/API keys from Keycloak | 0009 |
| T9b | Token for one MCP server replayed at another, or an agent client given extra audiences | S/E | ≤ 5-min tokens; one optional audience scope per MCP server, requested per token; fail-closed audience check per server; realm guard case: audience scopes = MCP servers in `policy/agents.yaml`; `resource` is sent but not relied on | 0009, 0017 |

## Agent-specific threats (see [docs/ai/agent-system.md](ai/agent-system.md))
| # | Threat | STRIDE | Control | ADR |
|---|---|---|---|---|
| T10 | Research data or results sent to a cloud LLM by a dev agent | I | Read denies + `data_guard.py`; synthetic/public fixtures only; data outside repo | 0015 |
| T11 | Dev agent writes outside its lane or self-approves a gate | T/E | `agent_boundaries.py`; verdict written only by owner; CI accepts skips only with owner approval on the PR | 0016 |
| T12 | Prompt injection in a filing steers a runtime agent to exfiltrate or alter data | T/I/E | Quarantined reader (no tools beyond read, schema-only output); scope-based authZ in MCP; egress deny (agents join no proxy network; no agent tool enqueues ingestion) | 0017, 0012 |
| T13 | Fluent but wrong numbers in reports (hallucination, look-ahead) | T | Claims verifier with `fact_id` + `as_of`; G5v point-in-time tests | 0017, 0016 |
| T14 | Runaway agent loop exhausts unified memory / GPU | D | Per-step token, tool-call and time budgets; LiteLLM rate limits; container memory limits | 0017, 0006 |
| T15 | Agent routes around a guardrail (e.g. rebuilds a denied command in Python, writes to `$TEMP`) — observed in Decisya | T/E | No interpreters for subagents; deny → freeze; full audit log outside the agent's reach; honeytokens; bypass = High finding | 0019 |
| T16 | Backup targets (AetherSpark and the owner's workstation backup, ADR-0013 Amendment 5) compromised: via a NAS administrator account, a co-hosted workload, or the NAS routing between its networks | I/T/D | NAS admins human-only with 2FA, no vendor relay/remote access, SSH per session; the NAS per-interface firewall as the inbound control, forwarding off on the AetherSpark interface, backup container without outbound connections, all checked nightly; separate folders, accounts and container networks per workload; the workstation backup is a separate Home-side service with its own folder, keys, port and quota, allowed from the desktop only, and the AetherSpark interface allows none of its ports, checked nightly; offline copy kept off-site. Accepted: NAS root can read backup content of both repositories (prune/copy keys live there); further residual risks in ADR-0013 Amendment 4 | 0013 |
| T17 | An owner-run scheduled job (workstation backup, usage-capture copy) runs code a dev agent changed, with the owner's rights over every project's folders and secrets | T/I/E | Job code reviewed per PR; job, wrapper and folder list in a folder only Administrators can write, all-users Python under Program Files started with `-I -S`, task registered elevated, refusal test from an unelevated shell at setup and after Windows updates; SHA-256 taken from GitHub at the reviewed merge commit (never the local clone) and checked before each run; standard library only; the copy job only reads on the source side, takes only regular single-link capture files of at most 1 MiB, and refuses any content outside G-004 format 1.0.0. Accepted: an elevated or compromised owner account defeats all of it; numbers and short capped strings in a valid capture remain a narrow covert channel | 0013 |
