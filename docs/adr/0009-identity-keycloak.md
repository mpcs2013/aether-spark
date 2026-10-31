# ADR-0009: Identity — Keycloak OIDC everywhere
- **Status:** Accepted (with changes 1–3) · **Date:** 2026-10-03 · **Amendment 1** Accepted 2026-10-11 (identity code from the shared kit Ostium) · **Amendment 2** Accepted 2026-10-16 (signed release evidence) · **Amendment 3** Accepted 2026-10-31 (Keycloak image from Ostium's baseline)

## In plain words
Keycloak is the reception desk: people and agents check in once and get a badge (token); every other component only checks the badge.

## Decision
- Single realm `aetherspark`; OIDC clients for Grafana, LiteLLM UI, agent host, MCP servers.
- One **client-credentials service account per runtime agent** with only its scopes (ADR-0017).
- Human users: **MFA required** — WebAuthn (security key / passkey, works in Firefox) preferred, TOTP fallback.
- Traefik forward-auth for UIs without native OIDC.
- No BFF: AetherSpark has no browser SPA of its own (UIs are Grafana/LiteLLM with native OIDC or forward-auth).

### Change 1 — short-lived tokens
Access tokens ≤ 5 min, RS256/ES256 only; validation of `iss`, `aud`, `exp`, algorithm allow-list (same rule as Decisya CLAUDE.md). Agents renew automatically via client credentials.

### Change 2 — realm as code
Realm export (clients, roles, scopes, agent service accounts, flows) in `deploy/keycloak/aetherspark-realm.json`, imported at start; no manual admin-console changes. Secrets are not in the file (generated and kept off git, ADR-0010). Realm guard tests (pattern from Decisya `realm-guard-cases.json`) fail CI on risky settings (e.g. implicit flow, wildcard redirect URIs, long token lifetimes). *(Guard engine from Ostium; AetherSpark keeps its cases: amendment 1.)*

### Change 3 — break-glass admin
One emergency admin account in the `master` realm, credentials stored offline, never used day to day; use is logged and reviewed. Runbook: `docs/runbooks/keycloak-break-glass.md` (P3a).

### Extraction-ready (reuse) — superseded by amendment 1
Identity code (JWT validation, client-credentials token handling, realm-as-code fixtures and guard tests) lives in a self-contained `AetherSpark.Identity` project with no domain dependencies, so it can later move into a shared identity kit together with Decisya's (see roadmap, "Future initiatives").

## Consequences
Good: one login system, reproducible from git, stolen tokens expire fast. Bad: Keycloak becomes a hard dependency at startup; the break-glass process needs discipline.

## Amendment 1 — identity code from the shared kit Ostium (2026-10-11)
In plain words: the badge checks and the robot logins are no longer built here. AetherSpark takes them from Ostium, the shared identity kit, and only says how they are set up.

- **What AetherSpark consumes:** packages from Ostium, a private repository with a private GitHub Packages feed.
  - `Ostium.Authentication.Jwt`: token validation for MCP servers and the agent host (change 1 rules), with a required, fail-closed audience per MCP server.
  - `Ostium.Backchannel`: trust of the internal CA root, read from a file, for Keycloak metadata and keys.
  - `Ostium.Keycloak.Realm`: the realm guard engine and per-client invariants. AetherSpark supplies its own cases and an explicit allow-list of agent service-account clients (confidential, client credentials only, no redirect URIs).
  - `Ostium.Keycloak.Testing`: the Keycloak test fixture, fed with AetherSpark's realm file and image pins.
  - `Ostium.ServiceAccounts`: client-credentials token handling for agents. One named client per agent, a per-process cache keyed by client and MCP server, renewal before expiry, and fail-closed when Keycloak is unreachable (bounded retries, never an expired token). Each token request carries the MCP server's `resource` and its audience scope. Keycloak is not expected to honour `resource`; Ostium verifies this. The audience therefore comes from one optional client scope per MCP server with an audience mapper, requested per token. A realm guard case checks that every agent client's optional audience scopes equal its MCP servers in `policy/agents.yaml` (ADR-0017). Revisit when Keycloak supports resource indicators. Ostium designs the package from AetherSpark's requirements; AetherSpark validates it with real agents in P5a.
- **What stays in AetherSpark:** `AetherSpark.Identity` holds composition only: configuration (audiences, names, the agent client list), the realm JSON and its guard cases, and each MCP server's protected-resource metadata and `WWW-Authenticate` value (MCP authorization). No reusable identity mechanism is written here.
- **No BFF, for now:** if AetherSpark builds its own .NET web interface, it uses `Ostium.Bff` and amends this ADR.
- **Supply chain:**
  - Exact version pins; package source mapping sends `Ostium.*` to the private feed only; NuGet lock files with locked-mode restore.
  - CI restores with `GITHUB_TOKEN` (`packages: read`, granted per package through Actions access); Dependabot reuses the same grant, so there is no stored secret (ADR-0004). GitHub warns that forks of a public repository may be able to access private packages granted to it. Accepted residual: only compiled packages are exposed; Ostium's source stays private.
  - Local restores use a dedicated, expiring token with only `read:packages`, not the general GitHub login. It stays in the OS credential store or a keyring, is never written in plain text to a NuGet config, and is never given to services or dev agents (`secret_guard.py` denies reading it). ADR-0010 lists it as a developer credential.
  - Production builds on the Spark (ADR-0004) run in an interactive session started by the owner. The token is passed as a build secret, never stored in an image layer, a Compose file or the manifest. The feed host is on the egress allowlist for builds only (ADR-0012).
  - Ostium never deletes or overwrites a published version. The one exception is a version containing a secret or personal data, which Ostium announces first, with a replacement. The pinned `Ostium.*` packages of each release tag are kept in a local folder feed that is backed up (ADR-0013), so a rebuild works without the private feed; a withdrawn version is removed from it.
  - Release evidence: signed tags, checked by Ostium's publish workflow; a CycloneDX SBOM inside each package; the source commit in each package's metadata. Consumers verify what they restore through lock-file content hashes. They cannot check the tag signature themselves (accepted residual). Ostium fixes do not appear in GitHub's advisory database: Ostium announces each security release to its consumers, with the affected and fixed versions, the severity and whether to update before the next deployment, and Dependabot proposes the update.
- **When:** Ostium stays at 0.x until AetherSpark and Decisya have consumed and validated the shared blocks. AetherSpark consumes a block once Ostium signals it ready for AetherSpark. If a block AetherSpark needs is not ready, the AetherSpark phase waits; no local copy is written. Before a block runs on the Spark, its tests have run on linux-arm64.
- **Replaces:** the "Extraction-ready" section above; "pattern from Decisya `realm-guard-cases.json`" in change 2, since the guard engine now comes from Ostium; "JWT validation ported from Decisya" in ADR-0002's component split.
- **Consequences:**
  - Good: one copy of identity code for all projects; security fixes reach AetherSpark as package updates; agent token handling is built once, with the kit's tests.
  - Bad: outsiders can read the public repo but not build it (private packages); forks may be able to restore the compiled packages (accepted); while Ostium is at 0.x, AetherSpark depends on its release pace.

## Amendment 2 — signed release evidence (2026-10-16)
In plain words: the owner checks, by hand and against a pinned key, that each Ostium package is exactly the one Ostium released.

- **Signed hashes:** each Ostium release has a checksums file with one line per package and its SHA-512, as in the lock file. The file is signed with the owner's tag key in the SSH namespace `ostium-checksums` and committed to Ostium's `main` through a reviewed pull request. The key is held outside GitHub and outside Ostium's CI.
- **Pinned key:** AetherSpark pins the key in an allowed-signers file in this repository, allowed for the namespaces `git` and `ostium-checksums`, and does not rely on GitHub's "Verified" badge. The owner checks the fingerprint against the key on his own machine before pinning it. A key change is announced before the first release signed with the new key. A suspected compromise is announced at once, with the versions signed since the last known-good release, and says for each whether it is re-signed or must not be used.
- **Check per version change:** every `Ostium.*` version change is owner-only (agent-system D15). For each one, the owner runs the check script kept in Ostium on the development machine: the tag signature, the checksums signature, each hash against the lock file, and the SourceLink commit against the tag commit. The result is recorded in the pull request before merge.
- **Replaces:** in amendment 1's release-evidence bullet, "They cannot check the tag signature themselves (accepted residual)."
- **Consequences:** one manual step per version change; in exchange, a package that does not match the signed release is caught before it is merged.

## Amendment 3 — Keycloak image from Ostium's baseline (2026-10-31, accepted, exchange D-008, #44)
In plain words: AetherSpark no longer tracks Keycloak releases itself. Ostium picks one, tests and scans it, and posts a release notice in the exchange; AetherSpark pins that image by default and still checks it itself before using it.

- **The baseline:** Ostium owns the Keycloak baseline: the version and image digest AetherSpark pins by default. For each release it adopts, after its update cooldown, Ostium posts a release notice thread in the exchange with:
  - the version, the multi-architecture index digest (amd64 and arm64) and the registry;
  - its pinned scanner and the scanner's database date, every High and Critical finding with its fix status, and the bundled-library advisories the release fixes;
  - its package suite's result on x64 and linux-arm64, and what the suite does not cover: it runs a test container with a realm import, not a production configuration.
- **What AetherSpark pins:** the index digest from Ostium's latest notice, as `<registry>/<image>@sha256:<digest>`, in the Compose base (WP3.1) and for `Ostium.Keycloak.Testing`. An index digest names one image per architecture, so the same pin works on the desktop (amd64) and on the Spark (arm64).
- **What stays in AetherSpark:**
  - the realm content (`deploy/keycloak/aetherspark-realm.json`) and its guard cases;
  - the run settings (hostname, TLS, proxy headers, production start) and the deployment;
  - each pin bump, in its own issue naming the notice's version and digest;
  - a confirming Grype scan of the bumped image on linux/arm64 and linux/amd64, in CI on the pin-bump pull request, under ADR-0005's fail rule as then in force (applied here to a third-party image). Ostium's scan informs; this scan is the gate;
  - the scheduled scan of the pinned digest between bumps (#31). Ostium does not re-scan between releases, so a new finding on the running pin comes from this scan; it is reported once in the thread of Ostium's current notice and handled under the security-first rule or as an exception;
  - exception decisions, approved by the owner and recorded in the pin-bump issue with the advisory, why it does not apply or is accepted, and a review date. ADR-0005 (#31) defines the general rule.
- **Order:** #31 (the scan job and its exception rule) is settled before the first Keycloak pin in WP3.1.
- **Registry:** the notice names it. If it is not in the ops list of the egress design (today `quay.io`, its CDN still to confirm; ADR-0012), a separate pull request adds it to the design and the proxy list (L2) before the bump.
- **Security first:** for an urgent fix, AetherSpark bumps to a newer digest before Ostium's notice. The confirming scan and AetherSpark's tests with `Ostium.Keycloak.Testing` pass on amd64 and arm64, and the bump is reported in the thread of Ostium's current notice. The baseline never blocks a security bump.
- **No shared Keycloak:** AetherSpark runs its own instance; a shared one would need its own decision.
- **No notice yet:** if Ostium has published none when WP3.1 starts, AetherSpark asks in the exchange, and the Keycloak part of WP3.1 waits; AetherSpark does not choose a version on its own. The rest of WP3.1 continues.
- **Dependabot:** when the Docker ecosystem is added (WP3.1), an ignore rule keeps it from proposing Keycloak image bumps; they come from the notice or the security-first rule.
- **Replaces:** in amendment 1, "image pins" for `Ostium.Keycloak.Testing` now means the baseline digest.
- **Consequences:**
  - Good: the release scan and the package suite on x64 and arm64 run once, in Ostium; AetherSpark scans only the image it bumps and the pin it runs; known vendor gaps are named once, in the notice.
  - Bad: a new Keycloak release reaches AetherSpark only after Ostium's cooldown and notice, except under the security-first rule; the first pin waits for Ostium's first notice, which comes only after Ostium has added its scanner (WP3.1, Gate G3's OIDC login).
