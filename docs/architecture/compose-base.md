# Compose base — design (WP3.1, with WP3.3 first)

**Status:** accepted 2026-11-04, with the notes to ADR-0002, 0003, 0009 and 0010 and ADR-0013 amendment 8; to be built in pull requests 2 to 6 (Order of work). Builds on [ADR-0002](../adr/0002-hybrid-dotnet-python.md), [ADR-0003](../adr/0003-docker-compose-runtime.md), [ADR-0007](../adr/0007-data-stores.md), [ADR-0009](../adr/0009-identity-keycloak.md), [ADR-0010](../adr/0010-secrets-sops-age.md), [ADR-0012](../adr/0012-network-egress.md) and the [egress design](egress.md).

**Reviews (2026-11-04, on the first draft):** `architect` CONSISTENT WITH AMENDMENTS (2 blockers, 7 major), `security-reviewer` (coverage audit) GO WITH FIXES (5 must, 4 should). Every finding is folded into this text; the product facts marked *to verify* are checked on the pinned versions in the pull request that uses them.

**Owner decisions (2026-11-04):**
1. The infrastructure services are a **hand-written, reviewed Compose file**; `aspire publish` is used later only for the services this repository builds.
2. **Secrets first:** the secrets mechanism of WP3.3 comes before the first service; no service ever starts with a secret in an environment variable or a Compose file.
3. **Desktop names `*.aether.localhost`**, Spark names `*.aether.home.arpa`.
4. **Redis is deferred** until its first user (LiteLLM, P5a), as ADR-0007 already calls it optional.
5. **Keycloak gets its own small Postgres** (`postgres-auth`): identity data never shares a server with research data.
6. **Gate G3's login is a test relying party** behind Traefik's forward-auth, not only Keycloak's own account console.
7. **The data Postgres with pgvector moves to P4a**, with its first user; no WP3.1 acceptance criterion depends on it.

**Owner review (2026-11-04), folded in:** the CA claim scoped to a name-constrained issuing intermediate and tested with the clients actually used; certificate recovery after expiry documented and tested apart from overnight renewal; C10 covering the protocol functions that must work, the admin denial, and bypasses by direct port or alternate name; the PR 2 desktop test proving that secrets stay in memory, with their permissions and cleanup; G3's timing in the roadmap. Closing the direct-port bypass changed the matrix: Traefik is the only way to Keycloak (see Network membership).

## Goal and scope
`just up` starts, from a clean desktop, an internal certificate authority, a reverse proxy that is the only way in, Keycloak with its own database, and a test page that signs in through Keycloak. All run on the networks of the egress matrix, speak verified TLS to each other (NFR-SEC-05) and read their secrets from in-memory files (ADR-0010).

- **In scope:** step-ca, Traefik, Keycloak, `postgres-auth`, the certificate renewers, the forward-auth test relying party; the secrets mechanism (WP3.3) they need; `just up` / `just down`; the same files on the desktop, in CI and on the Spark.
- **Out of scope:** the data Postgres with pgvector, which joins with its first user in P4a, like Redis; ClickHouse (P4a); the services this repository builds (P4a on); observability (WP3.2); the backup job (WP3.4, [backup-job.md](backup-job.md)); the Spark's deployment unit (WP6.4); the NAS certificate (WP6.6).

## Decisions
- **Hand-written infrastructure Compose** (decision 1): `deploy/compose/stack/compose.yaml` holds the services with digest pins, the hardening the Compose lint requires (L1) and only the matrix's networks. The egress overlay joins as a second file. From P4a, `aspire publish`'s output for built services is a third file, and the overlay removes its default network as the egress design says.
- **Traefik without the Docker API.** Routes come from a reviewed file (file provider); the lint already refuses the Docker socket everywhere. The dashboard and API are off, there is no port 80 entrypoint, and forwarded headers from outside are not trusted.
- **Verified TLS on every hop.** Every server certificate comes from AetherSpark's own CA, and **every client checks it**: name and issuer, never "accept any certificate". The checks are tests (C3, C3b).
- **Production start for Keycloak.** An image built here (`deploy/keycloak/Dockerfile`, `FROM` the baseline digest of Ostium's latest notice, `kc.sh build` for Postgres and health) runs `start --optimized`, never `start-dev`.

## Names
**Public names** (in the browser, through Traefik):

| Service | Desktop | Spark |
|---|---|---|
| Sign-in (Keycloak, realm `aetherspark`) | `auth.aether.localhost` | `auth.aether.home.arpa` |
| Keycloak admin console | `auth-admin.aether.localhost` | `auth-admin.aether.home.arpa` |
| Test relying party (G3) | `whoami.aether.localhost` | `whoami.aether.home.arpa` |

**Internal names** (container to container): `<service>.svc.aether.<place>`, set as Docker network aliases on that service's networks, for example `keycloak.svc.aether.localhost` on `auth-front` and `postgres-auth.svc.aether.localhost` on `db-auth`. Services never use a public name to reach each other, because some clients send every `*.localhost` name to their own loopback without asking DNS.

- **Desktop:** Traefik publishes only `127.0.0.1:443`. `*.localhost` names point to the machine itself (RFC 6761), so nothing is reachable from the home network, and the router's records for `*.aether.home.arpa` (already pointing to the Spark) stay untouched.
- **Spark:** Traefik publishes 443 on the Home-facing address only, and the host firewall admits only the Home subnet (two locks, not one).
- **Every published port names its address** from a variable that defaults to `127.0.0.1`; L1 refuses a published port without one, or with `0.0.0.0` or `::`.

## Network membership (changes to the matrix)
Changes to [egress.md](egress.md) and `deploy/compose/policy.json`, in this design's pull request:

| Network | Members | Why |
|---|---|---|
| `auth-front` (internal, **new**) | Traefik; Keycloak | The only network on which Keycloak's HTTPS listener can be reached. Every other caller, browser or service, reaches Keycloak through Traefik and its path rules |
| `edge` (internal, **Keycloak removed**) | Traefik; UI/API services; Grafana; ntfy | Routing to user-facing endpoints; Keycloak is reached through Traefik, never directly |
| `auth-<group>` (internal, **Keycloak replaced by Traefik**) | Traefik's back-channel entrypoint; the services of one group | OIDC discovery, JWKS, tokens, through Traefik's back-channel entrypoint, which forwards only the realm's protocol paths to Keycloak on `auth-front` |
| `db-auth` (internal, **new**) | Keycloak; `postgres-auth` | Keycloak's database on a server of its own (decision 5); no other service joins |
| `pki` (internal, members **added**) | step-ca; Traefik; **`cert-renewer`** (one per service that holds its own certificate) | Issuance and renewal. Keycloak and `postgres-auth` do not join; their renewer does, and hands the files over through a volume only the pair shares. Only Traefik carries network aliases on `pki` (its served names, for the ACME challenge) |

Placement in WP3.1:

| Service | Role (`aetherspark.role`) | Networks |
|---|---|---|
| Traefik | `traefik` | `ingress`, `edge`, `pki`, `auth-front`, `auth-edge` (the back-channel entrypoint; further `auth-<group>` networks as their groups get members) |
| step-ca | `step-ca` | `pki` |
| Keycloak | `keycloak` | `auth-front`, `db-auth`, nothing else |
| `postgres-auth` | `postgres` | `db-auth` |
| forward-auth (test relying party) | `ui-api` | `edge`, `auth-edge` |
| whoami page | `ui-api` | `edge` |
| Keycloak's renewer, `postgres-auth`'s renewer | `cert-renewer` | `pki` |

**Correction to the egress design.** Keycloak has **no separate admin listener**: its admin console and admin API are served on the same HTTPS port as sign-in, and `hostname-admin` does not stop the admin API from answering on the sign-in name (Keycloak's reverse-proxy and hostname guides). Path rules at Traefik only protect it if Traefik is the only way in. So Keycloak joins no network but `auth-front` and `db-auth`. The egress design's "admin API listens on a separate port", its group rule "Keycloak … joins every group's network" and the lint rule text built on them change: *Traefik joins the `auth-<group>` networks with a path-filtered back-channel entrypoint; Keycloak joins only `auth-front` and `db-auth`* (texts in "Document changes").

## Keycloak exposure
- **Sign-in route** (`auth.`, public entrypoint): Traefik forwards only `/realms/aetherspark/`, `/resources/` and `/.well-known/`; everything else, `/admin/` and `/realms/master/` included, is refused at Traefik. Paths are matched after Traefik's normalisation, so `..`, encoded and doubled-slash variants cannot reach a refused path.
- **Back-channel entrypoint** (Traefik on `auth-<group>`, its own port): only the realm's protocol endpoints (discovery, JWKS, token, introspection, userinfo, logout) under `/realms/aetherspark/protocol/openid-connect/` and `/realms/aetherspark/.well-known/`; each router also carries a source allowlist of that group's subnet.
- **One router per name and entrypoint:** a request whose `Host` does not match a configured router, whose SNI and `Host` differ, or that names an IP address, gets no route. Internal names (`*.svc.aether.<place>`) have no route on the public entrypoint.
- **Admin route** (`auth-admin.`, public entrypoint only): `/admin/` and `/realms/master/` only, behind a Traefik IP allowlist with the owner's desktop address (kept in `.local.ref/`, never in the repository). The allowlist needs the client's real source address: on the Spark, Docker's userland proxy must be off so that published ports keep the source address (*to verify* in the Spark's `daemon.json`; C10 checks it). On the desktop the route is on loopback anyway.
- **Forwarded headers:** `proxy-headers=xforwarded` with `proxy-trusted-addresses` set to Traefik's pinned address on `auth-front`; a forged `X-Forwarded-For` from anyone else is ignored (C10).
- **Master realm hardening** (realm-as-code covers only `aetherspark`): MFA for every admin account, brute-force detection on, no direct grants; a smoke check confirms each (C10).
- **Run settings:** `start --optimized`; `hostname` as a full URL (`https://auth.aether.<place>`) and `hostname-admin` likewise; `http-enabled=false`; health and metrics on the management port, never routed; certificates reloaded by Keycloak itself (`https-certificates-reload-period`, default one hour).
- **Secrets from a file, not the environment:** Keycloak has no `*_FILE` variables. It reads its database password and its first-start admin password from a configuration file in the in-memory secrets folder (`--config-file`; *to verify* on the pinned version), never from an environment variable or a wrapper that exports one.
- **Realm as code:** `deploy/keycloak/aetherspark-realm.json`, imported at start (ADR-0009 change 2). Its guard cases come from Ostium's realm package once it is released (not yet).
- **Image:** the Dockerfile's `FROM` names the baseline digest; the built image is the scan target on amd64 and arm64. At the pin, each High or Critical in it needs the owner's exception for at most 90 days, unless a later digest removes it (ADR-0005 amendment 3).
- **As built in PR 5a (2026-11-08),** where it differs from or adds to the plan above:
  1. **Image** `deploy/keycloak/Dockerfile`: `FROM` the index digest of Ostium's notice O-035 (26.8.1, build 26.8.1-2), then `kc.sh build` with Postgres, health checks on, metrics off (until WP3.2) and `cache=local`. AetherSpark's own scan found exactly the notice's six findings: 1 Critical in Java's default trust store (CVE-2026-91403), 1 High in guava, 4 High in the system SQLite library. The owner approved all six on 2026-11-08, until 2026-12-08 (issue #31). The reasons are in `deploy/images/exceptions.json`: Keycloak has no way out, clustering is off, and nothing in it loads SQLite.
  2. **One configuration file for both passwords.** Keycloak reads a single `--config-file`, so the planned `keycloak-db.conf` and `keycloak-bootstrap.conf` became one derived secret, `keycloak.conf` (`db-password`, `bootstrap-admin-password`), built from `keycloak-db-password` and the new `keycloak-bootstrap-admin-password`. Every other setting is not secret and is set in the Compose file.
  3. **Listeners:**
     - HTTPS on 8443, bound to Keycloak's `auth-front` address (`172.30.61.3`) only, so even `postgres-auth`, which shares `db-auth`, cannot open it;
     - the management interface (health) on `127.0.0.1:9000`, plain HTTP, inside the container only. The image has no `curl`, so the health check speaks HTTP through bash's `/dev/tcp`; Keycloak refuses a request without a `Host` line, and only the status code (200, else 503) counts;
     - `cache=local` at build and at run time, so there is no cluster discovery and no cluster port (with it at build time only, Keycloak 26.8 still started JGroups on 7800 and 57800).
  4. **`auth-front`** is `172.30.61.0/24`, with Traefik at `.2` and Keycloak at `.3`; `proxy-trusted-addresses` names Traefik's address only.
  5. **The admin route's source.** `ingress` now has a fixed subnet, `172.30.30.0/24`.
     - On Docker Desktop, the host's requests reach Traefik from that network's gateway, `172.30.30.1`, measured on 2026-11-08. So the allowlist's default, `172.30.30.1/32`, means "this machine", which on the desktop is the loopback-only route the plan describes.
     - On the Spark, `AETHERSPARK_ADMIN_SOURCE` must name the owner's desktop (in `.local.ref/`), and Docker's userland proxy must be off so that Traefik sees real source addresses (WP6.4). `just up` refuses to start for `home.arpa` without the variable.
  6. **Routes** `deploy/compose/stack/traefik/dynamic/keycloak.yml`, as planned; Traefik checks Keycloak's certificate against the root and the internal name. Measured refusals:
     - `/admin/`, `/realms/master/`, `..`, `%2e%2e` and doubled-slash variants, health and metrics get Traefik's own 404 on the sign-in name;
     - encoded slashes get Traefik's 400;
     - a `..;/` path starts with an allowed prefix, so it reaches Keycloak, which refuses it (400, `invalidCharacter`). The test records which of the two refuses each variant.
  7. **The realm file** carries the realm-wide settings: TLS required, no self-registration, brute-force detection, password policy, TOTP settings, events. Import keeps an existing realm (`IGNORE_EXISTING`), so a later change to the file reaches a new database only; how changes reach a running realm is decided with Ostium's realm checks (PR 5b).
  8. **Moved to later pull requests:**
     - **PR 5b:** the master-realm hardening, the break-glass runbook and the bootstrap-admin removal, a hands-on session with the owner, and the decision on Ostium's realm checks (done: see "Recovery and lock-out", as built in PR 5b, which also corrects item 6: the administrators' sign-in needed the master realm's own address);
     - **PR 6:** the back-channel entrypoint (with the answer to O-037 on the token issuer) and C10's protocol flows (authorization code with PKCE and MFA, refresh, userinfo, logout, client credentials, introspection), where the first client arrives.
  9. **Tests** in `tests/stack/run.sh` (C3, C3b, C5, C9a, C10 for what exists):
     - discovery, keys and the sign-in page through Traefik, with the right issuer;
     - each refused path, with Traefik answering itself;
     - the admin console from the allowed source; refused from another, also with a forged `X-Forwarded-For`;
     - Keycloak's certificate, its listening ports, and that `postgres-auth` cannot open its port;
     - its database client refusing a foreign root;
     - no secret in its processes' environments, `inspect` or log;
     - renewal with no failed request, and the renewed certificate served after Keycloak's own reload.

## Certificates (step-ca)
- **`just ca init`, once per machine,** creates:
  - an **issuing intermediate with an X.509 name constraint**: permitted DNS names under `aether.localhost` on the desktop, under `aether.home.arpa` on the Spark, nothing else. The root carries the same constraint.
  - **the root key off-line:** after signing the intermediate it leaves the volume, into the prod sealed store with the off-line recovery key on the Spark (ADR-0010), into the dev store on the desktop. Only the intermediate key stays on-line, encrypted with `step-ca-password`.
  - two different passwords from two secrets (`--password-file`, `--provisioner-password-file`); no password file remains in the volume. A CI check confirms both.
- **What the constraint protects, and what it does not:**
  - *A stolen intermediate key* (the on-line one) can sign certificates only under the place's suffix, for every client that checks name constraints on an intermediate. That is the claim, and it holds only for clients that check, which C11 verifies for every client the stack and the owner use.
  - *A stolen root key* is a different case: it can sign a new, unconstrained intermediate, and whether a client then still applies the root's own constraint differs between clients (a trust anchor's constraints are optional under RFC 5280). The root is protected by staying off-line, not by its constraint; its theft means a re-init (see Certificate expiry and recovery) and removing the old root everywhere.
  - Neither case lets the desktop's CA stand in for the Spark's names: each place's intermediate permits only its own suffix.
- **Provisioners:**
  - one **ACME** provisioner for Traefik, with a policy allowing only Traefik's public names;
  - **one JWK provisioner per service** (Keycloak, `postgres-auth`), each with a template fixing that service's internal name. Its password is at least 128 bits of entropy and is held by `just up`'s one-shot bootstrap step, never by a running container.
- **Bootstrap and renewal:**
  - At `just up`, a one-shot `cert-bootstrap` step mints a single-use token per service that lacks a valid certificate.
  - The renewers then run `step ca renew --daemon`, which authenticates with the current certificate and holds no password. step-ca refuses to renew an expired certificate, so a renewer down for longer than its lifetime is bootstrapped again by the next `just up`.
  - The renewer writes the key with the service's user and mode 0600; the service mounts the volume read-only.
- **Lifetimes:** leaf certificates 24 hours, renewed at two thirds. Traefik's ACME client is set to 24-hour certificates (`certificatesDuration: 24`; its default of 90 days would not match step-ca), its `caServer` is step-ca and its CA trust is step-ca's root only. Intermediate 1 year, root 10 years.
- **Expiry is watched, not remembered:** a check reports the intermediate's remaining validity and alerts at 60 days (ADR-0011); a runbook renews it with the off-line root.
- **Trust distribution:** a runbook step installs the root in the owner's browsers (Firefox keeps its own store) and devices, and says how to remove it again.
- **`postgres-auth`:** its renewer writes new files; Postgres re-reads certificates only on a reload, so its entrypoint wrapper reloads the configuration hourly, at least 8 hours before a certificate expires.
- **As built in PR 3 (2026-11-06),** where it differs from or adds to the plan above:
  1. **Our own tool writes the CA,** not `step ca init`: `deploy/ca/ca.py` (`just ca …`, standard library) runs the `step` CLI in throw-away containers (no network, read-only, all capabilities dropped, as the stack user) and writes `ca.json` itself. `just up` runs `ca prepare`: the volumes, the CA on first use, then `ca.json` again. So the provisioners follow the secrets manifest: one JWK provisioner per `step-ca-provisioner-<service>` secret. Keycloak's comes with PR 5, together with its service.
  2. **The root key has no password of its own.** It is created in a container's in-memory folder, leaves over a pipe into `age` and is sealed into the store like any secret (`step-ca-root-key.age`). Unencrypted it exists only in that container's memory and in the tool's process. The CA's files go into place only after the key is sealed, so a failure on the way leaves no half-made CA, and `init` refuses to overwrite an earlier root key.
  3. **Names are fixed by templates, not by provisioner policies.** In our probe, step-ca 0.30.2 did not restrict a JWK provisioner by its own policy. Each JWK provisioner's template sets the certificate's only name, whatever the request asks: requests for another service's name and for `github.com` both got the service's own name. The authority-wide policy allows `*.aether.<place>` and `*.svc.aether.<place>`; it needs two patterns because `*.` covers one label only. The ACME provisioner also keeps a policy for one label under the suffix, on top of the challenge.
  4. **step-ca and the step CLI are built here from source** (`deploy/compose/stack/step-ca/Dockerfile`, as for gitleaks in #31). The upstream image fails twice:
     - its latest release (0.30.2, 2026-03-23) carries Go 1.26.1, old modules and old Alpine packages: 223 fixed High and Critical findings at the scan of 2026-11-06;
     - it gives the `step-ca` binary a file capability, which the kernel refuses to run with `cap_drop: ALL` and `no-new-privileges`.

     The build:
     - takes the tagged source archives (step-ca 0.30.2, CLI 0.31.0), checked by SHA-256, and a current Go (1.27.3);
     - raises the flagged modules to their fixed versions, only upwards; the build fails if one stays below its target;
     - puts the two static binaries on Alpine 3.24, without `curl`, `jq` or the KMS plugin, and without the capability (step-ca listens on 9000).

     One finding remains: GHSA-mgcc-cww5-fp2q in `go-jose/v3` 3.0.5, a CPU denial of service from a crafted RSA key in a JWK. It has no fixed v3 release, and step's own `go.step.sm/crypto` still uses v3. The owner's exception: approved 2026-11-06, until 2026-12-06, issue #31 (ADR-0005 amendment 3; `deploy/images/exceptions.json`). step-ca, the bootstrap step and the renewers all use this one image, and `ca.py` runs it too; `just up` builds it first, and the first build takes a few minutes.
  5. **Traefik:**
     - static configuration from the environment only (Traefik reads one source, so the phone-home settings really apply);
     - its ACME account and certificates in memory (tmpfs), ordered again at every start;
     - runs as the stack user and binds 443 through its own network namespace's port floor (`net.ipv4.ip_unprivileged_port_start`), with no capability;
     - strict SNI, TLS 1.2 or newer;
     - on the public entrypoint, encoded path characters and header names that alias another header are refused, which prepares PR 5's path rules.

     *To verify:* when Traefik renews a 24-hour certificate (its documentation no longer gives the schedule). The tests prove renewal with short certificates; the desktop's first day shows the real schedule.
  6. **Renewer health:** unhealthy when less than 25 % of the certificate's life is left. step-ca backdates each certificate by a minute, and the daemon renews when a third of that lifetime is left, minus up to a twentieth as jitter, so never below 28 %. So after a stop past that point, `just up` reports healthy only once the renewal is done (C9a).

     The check is `step certificate needs-renewal --expires-in 75%`: the percentage is the part of the life that **has passed**, not the part left (verified on a half-way certificate). The first version used `30%` read the other way round, which would have marked every renewer unhealthy from hour 7 to hour 16 of each day. The desktop runs missed it; CI on arm64 caught it.
  7. **Volumes owned by the stack user:** `ca volumes` creates them with Compose's labels and gives their top folder to the stack user (a new volume belongs to root). It does this in a one-off container with only `CHOWN` and `FOWNER`.
  8. **Lint:** `role_images` may bind a repository (`traefik`), which then must be pinned by digest, so Dependabot's version bumps need no policy change. Local builds stay bound exactly.
  9. **Tests** C3, C3b, C9a, C9b and C11 for step-ca, Traefik and `postgres-auth`'s certificate: `tests/stack/run.sh` (`just stack-test`), CI job `stack-test` on amd64 and arm64. `postgres-auth` itself, and its part of C3 and C3b, came with PR 4 (see `postgres-auth`, as built in PR 4).
     Desktop runs (2026-11-06), with the upstream image and again with the image built here: all 37 checks pass, among them the CA volume check promised above (no password value and no root key in it; the intermediate key encrypted).
  10. **`just up` always recreates the containers** (`--force-recreate`). Found by C9a on Docker Desktop: a stopped container keeps the secret file it was created with, and `unseal` writes new files, so a restarted step-ca failed to mount `/run/secrets/step-ca-password`. The same happens after a WSL restart, which empties the in-memory folder. Recreating is cheap, and every service then reads the secrets just unsealed.

## Secrets (WP3.3, first)
As ADR-0010 decides, made buildable:

- **One in-memory folder per machine** (as built in PR 2, 2026-11-06):
  - **desktop:** `$XDG_RUNTIME_DIR/aetherspark/secrets/` in WSL2, that is `/run/user/<uid>/aetherspark/secrets/`: the WSL user's own tmpfs, so no `sudo` is needed;
  - **Spark:** `/run/aetherspark/secrets/`, created by the host's service (WP6.4).

  The folder is mode 0700 and each file 0400. `just secrets unseal` decrypts each secret there (`age -d`, the value over a pipe, never in an argument) and refuses a folder that is not a tmpfs (read from `/proc/self/mountinfo`). These two folders are the only sources the Compose lint accepts for a secret (`secret_source_dirs` in `policy.json`); a file below them, or a secret from the environment or inline content, is refused.
- **Every service that reads a secret runs as the stack's user** (desktop: the WSL user, uid 1000; Spark: a dedicated user from WP6.4), set with `user:` in Compose. Compose file secrets are bind mounts whose `uid`, `gid` and `mode` Compose ignores outside Swarm, so the host file's owner and mode decide who reads it. Without `sudo` the desktop cannot give a file another owner, and with `cap_drop: ALL` even root in a container cannot read another user's 0400 file. Each file is mounted only into its own service, so services stay separated by mount, not by owner.
- **The manifest is JSON** (`deploy/secrets.manifest.json`): read with Python's standard library only, also in WSL2 and on the Spark. A secret is either generated (random, at least 128 bits) or derived: a file such as `keycloak-db.conf` built at unseal from other secrets, so no value is stored twice. The manifest starts empty; the first entries come with step-ca (PR 3).
- **Desktop proof: passed on 2026-11-06** (`tests/secret-store/desktop-proof.sh`, Docker Desktop 29.9.2, WSL2 kernel 6.6). Docker Desktop's WSL integration mounts the very same tmpfs into the container: `/proc/mounts` inside shows `tmpfs`, with the WSL user's uid. The proof's steps:
  1. **Memory-backed at the source:** the folder is a tmpfs, mode 0700, the file 0400.
  2. **Memory-backed in the container, never copied:** the container's mount is tmpfs and reads the value (compared by hash); the value is not in the container's writable layer (`docker export`) and in no file on WSL2's disk. Docker Desktop's own data disk is not reachable from WSL without a privileged container, which the proof does not use; the shared tmpfs mount is the evidence that no copy is made.
  3. **Permissions:** the owner reads, another uid gets "Permission denied", a container without the secret does not see it.
  4. **Not in metadata:** the value is in no `docker inspect`, environment, log or `docker compose config` output.
  5. **Cleanup:** after cleanup the folder and the containers are gone (5a); after `wsl --shutdown` and a restart nothing of the run is left (5b).

  A disk-backed folder fails it at step 1 (checked). It is re-run after Docker Desktop or WSL updates ([secrets.md](../runbooks/secrets.md)); the Spark repeats steps 1 and 3 to 5 natively (WP6.4).
- **The secrets of WP3.1:**

  | Secret | Read by |
  |---|---|
  | `step-ca-password` | step-ca |
  | `step-ca-provisioner-postgres-auth` (PR 3), `step-ca-provisioner-keycloak` (PR 5) | `just up`'s bootstrap step only |
  | `postgres-auth-superuser-password` | `postgres-auth`, setup only |
  | `keycloak-db-password` (PR 4) | `postgres-auth`, which sets the `keycloak` role's password from it at every start |
  | `keycloak-bootstrap-admin-password` (PR 5a) | Keycloak, first start only, through `keycloak.conf` |
  | `keycloak.conf` (derived: `db-password=…`, `bootstrap-admin-password=…`; PR 5a, replacing the planned `keycloak-db.conf` and `keycloak-bootstrap.conf`) | Keycloak |
  | `forward-auth-client-secret`, `forward-auth-cookie-secret` | forward-auth |

- **Dev store** on the desktop: `~/.aetherspark/secrets/dev/`, age-encrypted with the dev key (`~/.aetherspark/keys/dev.key`, 0600). `just secrets generate` creates the dev key and the missing secrets on a clean clone, and `just up` will call it, so "`just up` from clean WSL2" (NFR-OPS-01, Gate G3) holds. `just secrets rotate <name>` replaces one value.

## `postgres-auth`
- **Image:** the official Postgres 18 image, pinned by digest; no pgvector, since it holds only Keycloak's data.
- **Data volume** at `/var/lib/postgresql` (Postgres 18 images keep their data in `/var/lib/postgresql/18/docker`).
- **Access:** a reviewed `pg_hba.conf` (`-c hba_file=`): `hostssl keycloak keycloak <db-auth subnet> scram-sha-256`, the superuser on the local socket only, `reject` for everything else; `password_encryption=scram-sha-256`, `ssl_min_protocol_version=TLSv1.2`. Keycloak connects with `sslmode=verify-full` and step-ca's root as `sslrootcert`.
- **Memory:** about 512 MB, from the desktop's headroom and the Spark's 8 GB platform budget.
- **As built in PR 4 (2026-11-08),** where it differs from or adds to the plan above:
  1. **Image:** built here (`deploy/compose/stack/postgres-auth/Dockerfile`), `FROM postgres:18.6-alpine3.24` pinned by its index digest (amd64 and arm64), with only zlib raised to the release that fixes CVE-2026-4738, as for step-ca. It has no High or Critical finding.
     - The Debian variant was tried first. CI's scan of 2026-11-08 found 30 advisories in it, on both architectures: 29 High and 1 Critical (libxml2), most marked "won't fix" by Debian. The Alpine variant had only zlib's.
     - Dependabot proposes new digests and minor releases of the base, never a new major version. A major version needs the data folder upgraded, a deliberate change with its own runbook.
  2. **Its own start script, not the image's entrypoint** (`deploy/compose/stack/postgres-auth/run.sh`). The image's entrypoint reads the superuser password from `POSTGRES_PASSWORD_FILE` into the variable `POSTGRES_PASSWORD` at every start, and that variable stays in the server's process environment, which C5 forbids. The script:
     - runs `initdb` itself on the first start, with the password file (`--pwfile`) and Postgres' own builtin `C.UTF-8` locale, which is the same whatever C library the image has. initdb insists on a passwd entry for its user; `nss_wrapper`, shipped in the image for that purpose, provides one for that call only;
     - starts the server with every setting on its command line (TLS files from the renewer's volume, `pg_hba.conf` from the repository, `password_encryption=scram-sha-256`, TLS 1.2 or newer, connections logged);
     - at every start, as the superuser over the container's own socket, creates the `keycloak` role and database if missing and sets the role's password from `keycloak-db-password`, so a rotation applies at the next start. That session logs no statement, not even a failing one. psql reads the value with `cat` into a variable, so it is in no argument; the superuser's password goes through a password file in the container's in-memory `/tmp`, deleted right after;
     - is healthy only once the role and database are in place, so Keycloak (PR 5) never meets a half-set-up server;
     - reloads the configuration every hour (`AETHERSPARK_PG_RELOAD_SECONDS`; 20 seconds in the tests), so Postgres re-reads the renewed certificate files.
  3. **The secrets:** `keycloak-db-password` is a generated secret of its own, mounted into `postgres-auth`. PR 5 derives Keycloak's `keycloak-db.conf` from it, so the value is stored once (table above).
  4. **`pg_hba.conf`** (`deploy/compose/stack/postgres-auth/pg_hba.conf`): `hostssl keycloak keycloak` from `db-auth`'s subnet, `172.30.60.0/24`. The superuser may connect only over the local socket, to the `postgres` database. Every other line is `reject`, plain connections included. The test checks that the file's subnet equals the network's.
  5. **The data volume** `postgres-auth-data` is given to the stack user by `ca volumes`, like the certificate volumes, and mounted with `nocopy`. Otherwise Docker would copy the image's folder into it, owner included, on first use.
  6. **Tests** in `tests/stack/run.sh`, as a stand-in for Keycloak until PR 5 (libpq's `verify-full` from a client on `db-auth`):
     - C3: the `keycloak` role signs in over TLS 1.2 or newer, verified against the CA's root and the name, and owns its database; a plain connection, a wrong password, the superuser over the network and another database are refused;
     - C3b: refused under a foreign root, and by address;
     - C5: no secret value in any process environment of the container, in `docker inspect` or in its log;
     - C9a: no failed connection while the certificate is renewed, and the server presents the renewed certificate after its reload (compared by fingerprint with a few lines of Python, since the Alpine image has no `openssl` command);
     - the data survives a restart, and a rotated `keycloak-db-password` signs in after the next `just up`.

     Keycloak's own JDBC client is checked in PR 5.

## Gate G3's test relying party
A pinned forward-auth container (an OIDC proxy) and a minimal "whoami" page. Traefik routes `whoami.aether.<place>` through forward-auth. Forward-auth signs the browser in at Keycloak (client `forward-auth`, confidential, MFA by the realm) and checks the token against Keycloak over the internal CA. The page shows the signed-in user's name. This proves the chain AetherSpark's own UIs will use, end to end.

PR 6 is built in three parts (owner's decisions of 2026-11-08): **6a**, the realm as code and its users' second factor; **6b**, forward-auth, the whoami page and the back-channel entrypoint; **6c**, Ostium's realm checks.

- **As built in PR 6a (2026-11-08):**
  1. **The realm as code, also on a running Keycloak.** Keycloak imports the realm file only into an empty database, and since PR 5b no administrator can sign in without the browser and a key. So `deploy/keycloak/admin.py` gained:
     - a **config service account**, `aetherspark-config`, which `setup` creates in `master`. It is a confidential client with client credentials only (no browser flow, no direct grants), its secret `keycloak-config-client-secret` in the store (read by the host tool, consumer `host:keycloak-admin`). Its roles are those of master's client `aetherspark-realm`: it administers the `aetherspark` realm, users included, and nothing in `master`. The stack test checks 403 on master's users, clients, roles and settings; Keycloak still answers it a short public summary of `master` (name, display name, a few flags), as for any admin account;
     - **`apply`**, as that account: the realm file's settings, the required actions and the login flow. `just up` runs it after every start (skipped before `setup`), so the running realm follows the repository. A flow that differs from the code is reported, never rebuilt;
     - **`user add NAME [--key]`**: a user with a temporary password and the second factor to set up.
  2. **The users' second factor**, the owner's choice of an app code (TOTP) or a security key (WebAuthn): the flow `browser-2fa` asks for the password, then whichever of the two the user has.
     - The second step is **conditional** on the user having one. A required step whose alternatives are all not set up yet fails the whole sign-in ("Invalid username or password", measured on 26.8.1), so a new user could never set one up.
     - So every new user gets **setting up an app code as a default action**, however the user was created. With `--key`, `user add` replaces it with the key's registration.
     - **Nobody may delete their own second factor** (`delete_credential` off).
     - What remains: an administrator who deletes a user's second factor in the console leaves that user with the password alone until one is set up again. The runbook says to add the required action at once.
     - Keycloak 26's **"verify profile"** action is off: it would ask every user for an email and names, and AetherSpark sends no email and shows the username.
  3. **Tests** (`tests/stack/run.sh`, with `tests/stack/oidc_flow.py`, a browser's sign-in in standard-library Python):
     - `apply` after `setup` changes nothing; the config account's limits; a user created plainly gets the app-code action;
     - a first sign-in through Traefik's sign-in route: authorization code with PKCE (S256), the app code set up from the form's secret (as an authenticator app scans it), a new password, the code for tokens, issuer and user, a refresh, userinfo, logout, and the refresh token dead afterwards;
     - a later sign-in asks for the app code (each run is a fresh browser);
     - a `--key` user stops at the key's registration;
     - from Keycloak's database: the bound flow, the default action, the two switched-off actions.

     C10's client credentials and introspection come with 6b's confidential client, and the code flow through forward-auth (C8) with its page.
  4. **The desktop** needs a fresh Keycloak database once: `setup` ran there before the config account existed. Development data is throw-away; the runbook has the steps, and the YubiKey is registered again.
  5. **Services that read a secret no longer restart by themselves** (step-ca, `postgres-auth`, Keycloak: `restart: "no"`). Found on 2026-11-09:
     - a stack left running across a restart of WSL came back without its in-memory secrets;
     - Docker restarted those containers anyway, and created an empty root-owned folder at each missing secret file's path;
     - the next unseal could not replace those folders.

     Those services cannot start without `just up` in any case, which unseals the secrets first. Renewers and Traefik, which read no secret, still restart by themselves. If such folders exist anyway, unseal now stops with a message naming the folder to remove as root, instead of a bare "permission denied". The habit stays `just down` before shutting the desktop down.
  6. **External security keys only** (follow-up, 2026-11-09): both realms' WebAuthn policy is `cross-platform` (master through `setup`, `aetherspark` through the realm file and `apply`).
     - At the owner's sign-in after the fresh database, Windows had saved the administrator's passkey in **Windows Hello** on the PC, offered in the same dialog as the security key: sign-in worked with the YubiKey unplugged.
     - The owner set the policy in the console, registered the YubiKey again and deleted the Windows Hello entry. The everyday user was then created with `--key`, on the YubiKey (checked: "USB, NFC").
     - The stack test reads both realms' policy from the database.
     - Email and names in the `aetherspark` realm's user profile are optional, since the account page marked them as missing.
- **As built in PR 6b (2026-11-09):**
  1. **The chain.** `whoami.aether.<place>` is routed through Traefik's `forwardAuth` to **forward-auth** (oauth2-proxy 7.15.6, on `edge`).
     - Without a session it answers with the redirect to Keycloak's sign-in on the public name: client `forward-auth`, authorization code with PKCE (S256).
     - After the sign-in it answers 202 and names the user in `X-Auth-Request-*` headers, which Traefik copies into the request to the page. Headers of the same name from the browser never reach the page (`trustForwardHeader: false`; tested).
     - Its own paths (`/oauth2/…`: the callback, sign-out) are routed to it directly on the same name.
     - Users have no email, so the username stands in for it (`--oidc-email-claim=preferred_username`).
  2. **The back-channel entrypoint.** Traefik's entrypoint `backchannel` (port 9443, never published) on `auth-edge`, under the internal name `auth-backchannel.aether.<place>` (an alias on `auth-edge`; its certificate from step-ca over ACME like the public names).
     - It routes only the `aetherspark` realm's protocol endpoints and discovery, only from `auth-edge`'s subnet; everything else gets 404, other networks 403.
     - forward-auth uses it to redeem codes and fetch keys (`--skip-oidc-discovery` with explicit URLs), while it checks that tokens name the **public** issuer. Keycloak issues them under the public name whatever address the request came in on. So the question of O-037 is settled here without Ostium's library; for .NET APIs (P4a) it stays as asked.
  3. **The realm's client.** `apply` creates or updates `forward-auth` in the `aetherspark` realm:
     - confidential, its secret `forward-auth-client-secret` from the store (also mounted into forward-auth);
     - the browser flow only, PKCE required, redirects only to the page's callback;
     - "full scope" off, so its tokens carry none of the user's roles. The page needs none, and Keycloak 26 warns that the switch is deprecated. The same warning names the config service account, whose roles reach its token through that switch; moving those roles to explicit scope mappings is left for a later change.

     The cookie key `forward-auth-cookie-secret` is a generated secret too.
     - **Introspection is allowed only for tokens issued for the asking client:** Keycloak 26 answers `active: false` otherwise ("not in the token audience", measured 2026-11-09). An API that checks its own tokens therefore needs an audience mapper on the client that requests them, as the test's service client has.
  4. **Certificates of programs that read them only at start.** oauth2-proxy and whoami both load their certificate once (read in their source, 2026-11-09). `deploy/compose/stack/cert-watch/cert-watch.sh` runs each one and restarts it when the renewer has replaced the file (checked every minute; the restart takes well under a second; sessions live in the browser's cookie). So both images are built here with a shell:
     - **forward-auth:** oauth2-proxy's static binary from the release image (pinned by digest) on the stack's Alpine, zlib fixed;
     - **whoami:** built from its tagged source with Go 1.27.3, modules raised as for step-ca. The release image (v1.12.0) carried Go 1.26.5 with 1 Critical and 16 High fixed findings.

     Both scan clean, so the PR needs no exception.
  5. **No secret in forward-auth's environment, inspect output or log**, and the callback (which carries the one-time authorization code in its URL) is left out of its request log. forward-auth reads two secrets, so like the other secret readers it does not restart by itself.
  6. **Tests** (`tests/stack/run.sh`, `tests/stack/oidc_flow.py --page`):
     - C8: the page redirects to Keycloak as `forward-auth` with PKCE; after password and app code it shows the user; a forged user header is ignored; after sign-out the page asks for sign-in again;
     - C10: on the back channel, discovery (public issuer), keys, client credentials and introspection (with a test client), and 404 for admin, master, account and other paths; 403 from `edge`;
     - C9a: both services renewed and serving the new certificate after cert-watch's restart.

     The test suite's own route moved to `whoami.aether.<place>/stack-test/`, since the name now belongs to the page.
- **As built in PR 6c (2026-11-09):**
  1. **The realm file holds the clients.** `forward-auth` moved from `admin.py` into `deploy/keycloak/aetherspark-realm.json`, so the file is the whole realm and can be checked as an export.
     - Its addresses use one placeholder, `${AETHERSPARK_PLACE}`. Keycloak's import fills it in from the keycloak service's environment (not secret), and `apply` fills it in itself. The stack test checks, before `setup`, that the imported client accepts its callback on `whoami.aether.localhost`.
     - The file holds no secret: a client's `secret` is exactly its placeholder, `${FORWARD_AUTH_CLIENT_SECRET}` (Ostium's rule). `apply` sets the store's secret (`CLIENT_SECRETS` in `admin.py` names it) and refuses a client whose `secret` is anything else.
     - Keycloak has no variable for that placeholder, on purpose (no secret in its environment, C5). Its import then keeps the placeholder's text as the secret (measured on 26.8.1, 2026-11-09). So between a fresh database's first start and `setup`, `forward-auth`'s secret is a known text. Accepted: in that window the realm has no user, and the client has no password grant and no service account, so the secret opens nothing. From `setup` on, `apply` sets the store's secret and checks it on every `just up`.
     - The file now also states what Keycloak would otherwise fill in by default: the device and CIBA grants off, RS256 for access and ID tokens. Ostium's rules count an absent value as a finding.
  2. **Ostium's realm checks** (`Ostium.Keycloak.Realm` 0.5.0, ADR-0009 amendment 1), in the test project `tests/dotnet/AetherSpark.Realm.Tests`, run by `just ci` on both architectures:
     - the realm file, with the place filled in, against the per-client rules (`forward-auth` declared as a confidential "BFF" client: PKCE S256, exact redirect and sign-out addresses, no web origins, no password, implicit, device or CIBA grant, no service account, full scope off, RS256, tokens of at most 5 minutes), and no client undeclared;
     - the file as committed: no secret and no placeholder other than the declared ones;
     - each rule really bites: a weakened copy of the file gives the finding;
     - **the realm guard**: no tracked file names the realm file or Keycloak's import folder, except the stack's Compose file (the one import), `admin.py`, the test project's profile and case table, and Markdown outside `.claude/`. A table of cases proves each exemption is needed and that, for example, a Dockerfile copying the file into the import folder or an agent's instructions naming it fail.
  3. **The package's supply chain** (ADR-0009 amendments 1 and 2; runbook [ostium-packages.md](../runbooks/ostium-packages.md)):
     - `nuget.config` sends `Ostium.*` to Ostium's feed only; the test project has a lock file and CI restores in locked mode;
     - CI's .NET jobs get `packages: read` and pass the job's token to NuGet in that step only (into the dev container as one variable of its run);
     - locally, the owner's read-only token lives in Windows Credential Manager, and `just ostium-feed restore` hands it to one restore;
     - Ostium's signer line is pinned in `.github/allowed-signers/ostium` (O-010); the owner's consumer check of 0.5.0 is recorded in the PR.
  4. **Left for later:** the running-realm checks (`CheckRunningRealmAsync`, with a realm account holding only `view-realm` and `view-clients`) and Ostium's live protocol flows, which need a test user and a throw-away Keycloak; the local folder feed of pinned packages with the backup (WP3.5).

## Certificate expiry and recovery
Two different situations, documented in `docs/runbooks/certificates.md` and tested separately (C9a, C9b):

| Situation | What happens | Recovery |
|---|---|---|
| **Overnight downtime** (desktop off for hours, certificates still valid) | At `just up` the renewers and Traefik renew whatever is past two thirds of its lifetime before the services are marked healthy | None needed; C9a |
| **Leaf certificates expired** (stack down longer than 24 hours) | step-ca refuses to renew an expired certificate; `just up`'s bootstrap step sees the expiry and issues a fresh certificate with a single-use token per service; Traefik's ACME client re-orders its own | Automatic at `just up`; C9b |
| **Intermediate expired or about to** | Nothing can be issued; existing leaves expire within a day | Runbook: bring the off-line root in, sign a new intermediate, take the root out again; the alert at 60 days should prevent it; C9b with a short-lived test intermediate |
| **Intermediate key or CA password lost, or CA volume damaged** | Restore the CA volume from the backup (WP3.4); the root stays the same | Runbook; restore drill |
| **Root key stolen or lost** | Re-init: new root and intermediate, new trust in every browser and client, old root removed | Runbook; not automated |

## Recovery and lock-out
- **Bootstrap admin to break-glass, in this order:**
  1. create the break-glass account in the master realm, with MFA;
  2. sign in with it;
  3. only then delete the first-start admin.

  The way back is `kc.sh bootstrap-admin` against the stack's database, in `docs/runbooks/keycloak-break-glass.md` (ADR-0009 change 3).
- **As built in PR 5b (2026-11-08),** with the owner's decisions of 2026-11-08: the administrators' second factor is a **security key** (WebAuthn: YubiKey 1, YubiKey 2 when at home), and Ostium's realm checks are adopted in PR 6.
  1. **A finding about PR 5a.** Keycloak's `hostname-admin` moves the admin console only. The `master` realm's sign-in pages still posted to the sign-in name, where Traefik refuses `/realms/master/` by design, so no administrator could sign in through the browser. The fix sets the master realm's own address (its Frontend URL attribute) to `https://auth-admin.aether.<place>`. The whole administrators' sign-in now lives on the admin name, behind its allowlist, and the sign-in name stays closed to it.
  2. **`deploy/keycloak/admin.py setup USERNAME`** (`just keycloak setup`; standard library) does the hardening in one run. It signs in as the first-start administrator, whose password it reads from the in-memory secrets folder, and talks only to Traefik's admin route, checking the certificate against AetherSpark's root. It:
     - sets the realm's address to the admin name, then signs in again (tokens carry the realm's address as issuer);
     - adds the login flow `browser-security-key`: the session cookie, or the password form followed by the WebAuthn authenticator, both required. An administrator without a key must register one before getting in;
     - sets brute-force detection (5 failures, waits up to 15 minutes, no permanent lock), login and admin events (30 days), TLS everywhere, a password policy (14+ characters, history 3);
     - creates the owner's administrator with the `admin` role, a temporary password (asked twice, never printed) and the required actions to set a password and register a key;
     - checks all of it through the admin API, and **last** turns off direct grants for every client of the realm, so neither the tool nor a stolen password can sign in without the browser and the key. A second run is refused.

     Keycloak's own `kcadm.sh` was not used: without a terminal it takes a password only in an argument or an environment variable, which the secrets rules forbid.
  3. **Deleting `bootstrap-admin`** stays a manual step in the console, after the owner's first sign-in with the key, in the order planned above.
  4. **Break-glass** (`deploy/keycloak/break-glass.sh`, `just keycloak-break-glass NAME`) stops Keycloak, runs `bootstrap-admin user` in a one-off container where the owner types the password at the prompt, and starts Keycloak again whatever happens (measured: 13 seconds for the command). The new account must register a security key at its first sign-in, like every administrator. The runbook is `docs/runbooks/keycloak-admin.md`; the planned `keycloak-break-glass.md` became that runbook's second part.
  5. **Tests** (`tests/stack/run.sh`):
     - setup succeeds and a second run is refused;
     - a password-only sign-in with the first-start administrator's real password gets `unauthorized_client`;
     - a first sign-in through the admin name, as a browser does it, posts to the admin name, demands a new password, then a security-key registration;
     - read from Keycloak's database, not through Keycloak: brute-force detection, the realm's address, the bound flow and its four steps, no client with direct grants, events on;
     - break-glass creates an administrator with Keycloak stopped, Keycloak returns healthy, and that account's first sign-in demands a security key too.

     What a test cannot do is touch a real key: the owner's first sign-in is the proof that registration and use work in the browsers. It ran on 2026-11-08 on the desktop:
     - Firefox, with the root in its own store and in Windows' store;
     - YubiKey 1 registered, sign-in with the password, the key's PIN (Windows asks, since the key has one) and a touch;
     - `bootstrap-admin` deleted.

     One lesson from it: `just` echoed the recipe's command line, which printed the administrator's name. The Keycloak recipes no longer echo, and the tool's messages no longer name the user.
  6. **On the Spark** (WP6.4): the admin route admits only `AETHERSPARK_ADMIN_SOURCE`, so `setup` runs where that route admits it. How, an SSH tunnel from the owner's desktop or a source address for the Spark itself, is decided with the Spark's deployment unit.
- **A lost CA password or root:** a re-init runbook (new CA, new root distributed, old root removed).
- **The intermediate's expiry:** the alert above and its runbook.
- **Observe, then allow:** each new service's first run follows the egress design's rule: it is observed first, then its flows are allowed.

## `just up` and `just down`
- **`just up`:**
  1. decrypt the secrets into the tmpfs folder (create the dev key and missing secrets on a clean desktop);
  2. `just ca init` if the CA does not exist yet;
  3. bootstrap missing or expired certificates;
  4. start both Compose files in profile `core`;
  5. wait until every service is healthy.
- **`just down`:** stops the stack and empties the tmpfs folder; volumes stay.
- **As built in PR 3:** `deploy/compose/stack/up.sh` and `down.sh` do these steps (step 2 is `ca prepare`; step 4 has one Compose file until the egress overlay joins). `just up` recreates every container (see Certificates, as built, item 10).
- **From PowerShell (PR 4):** `just up`, `just down`, `just ca …` and `just stack-test` run in WSL2 by themselves: on Windows each recipe calls `wsl -d Ubuntu-24.04 --cd <repository> -- just …`. The distribution can be changed with `AETHERSPARK_WSL_DISTRO`. The secrets folder, `age` and the dev key live in WSL2, so the work always runs there.
- **Profiles:** everything of WP3.1 is in `core`. ADR-0003's `data` profile starts with the data Postgres in P4a; `postgres-auth` is identity infrastructure, needed for any sign-in, so it belongs to `core`.

## Order of work (pull requests)
1. **This design** and its document changes (below).
2. **WP3.3 secrets:**
   - the desktop proof (`tests/secret-store/desktop-proof.sh`: memory-backed through WSL2 and Docker Desktop, permissions, cleanup), first;
   - the manifest, `just secrets generate`, the age dev store and the tmpfs folder;
   - the L1 rule for `secret_source_dir` and the published-address rule;
   - the CI manifest check;
   - `docs/runbooks/secrets.md`.
3. **step-ca and Traefik:** `just ca init` with name constraints and the off-line root, the ACME and JWK provisioners, the bootstrap step and renewers, Traefik's static and route files, the root's trust runbook, `docs/runbooks/certificates.md`, and tests C3, C3b, C9a, C9b and C11 on a test route.
4. **`postgres-auth`:** TLS through its renewer, `pg_hba`, the `keycloak` database and role.
5. **Keycloak:**
   - after Ostium's re-baseline notice (planned 2026-11-09, target 2026-11-13);
   - the image build and its exceptions, the realm import, the master-realm hardening, the routes with path rules;
   - the break-glass runbook and the bootstrap-admin removal;
   - tests C10.
6. **The test relying party** and test C8: Gate G3's login.

**Gate G3's date:** the Keycloak steps depend on Ostium's notice and the owner's exceptions, so G3's OIDC item will land after P3a's planned end (10 Nov). The roadmap says so.

**Hand-over to the Spark (WP6.4, WP6.6):** `just ca init` on the Spark with its own name constraint, the Spark root in the owner's browsers, the NAS certificate (the home infrastructure project prefers to install it by hand; the Spark CA's policy must allow its name).

## Document changes (in this design's pull request)
- **`deploy/compose/policy.json`:**
  - role `cert-renewer`; `pki` roles `["step-ca", "traefik", "cert-renewer"]`;
  - network `auth-front` (`as-auth-front`, internal, roles `traefik`, `keycloak`) and network `db-auth` (`as-db-auth`, internal, roles `keycloak`, `postgres`);
  - `keycloak` removed from `edge` and from every `auth-<group>`; `traefik` added to every `auth-<group>`;
  - fixtures in `test_lint.py` for the new networks;
  - later pull requests add `role_images` for `traefik`, `step-ca` and the renewer (PR 3) and `secret_source_dir` with its lint rule (PR 2).
- **`deploy/images/targets.json`** gets the Keycloak build and its local tag. **Dependabot** watches `/deploy/keycloak` (its Keycloak base ignored: the base moves only with Ostium's notice) and `/deploy/compose/stack`.
- **egress.md:**
  - **scope (line 10):** the stack is the hand-written infrastructure file, from P4a `aspire publish`'s output for built services, and the egress overlay;
  - **matrix:** the `db-auth` row and the `pki` change above;
  - **the `edge` row:** Keycloak removed;
  - **the `auth-<group>` row:** Traefik's back-channel entrypoint instead of Keycloak's listener; "OIDC discovery, JWKS, tokens, through Traefik, which forwards only the realm's protocol paths to Keycloak. Keycloak serves its admin API on its one listener, so it joins only `auth-front` and `db-auth`: no service reaches it but Traefik.";
  - **new rows** `auth-front` and `db-auth`;
  - **the groups paragraph:** "Traefik's back-channel entrypoint and the OTel collector join every group's network";
  - **the Internal flow:** Traefik → Keycloak on `auth-front`, Keycloak → `postgres-auth` on `db-auth`;
  - **the lint rule text:** "… share no network with LLM serving, MCP servers, the agent host or Grafana/Loki query APIs; only Traefik shares a network with Keycloak";
  - the threat-model line for the trusted root.
- **ADR-0002, a dated note (2026-11-04):** the AppHost row's `aspire publish` covers only the services built here, from P4a; the infrastructure services are a hand-written, reviewed Compose file (this note).
- **ADR-0003, a dated note:** Compose files are the hand-written infrastructure file, from P4a `aspire publish`'s output, and the checked-in overlays.
- **ADR-0009, a note to amendment 3:** the baseline digest is pinned in the `FROM` of the Keycloak image built here; that image is the scan target; Dependabot ignores the Keycloak base.
- **ADR-0010, a dated note:** decrypted values live in one tmpfs folder, the only path under `/run` the lint accepts for secrets; images without file support read a configuration file from that folder, never an environment variable.
- **ADR-0013, amendment 8:** the backup includes each machine's CA volume (the intermediate key, encrypted with a sealed secret; its configuration and issued-certificate database) and `postgres-auth`'s dump; a restore keeps the trusted root. Traefik's ACME store is not backed up (re-issued at start).
- **c4.md** (the dev row): hand-written infrastructure file + `aspire publish` output for built services + overlays; Keycloak's own Postgres.
- **Roadmap:**
  - WP3.3 before WP3.1;
  - Redis and the data Postgres deferred to their first users;
  - `postgres-auth`;
  - the test relying party;
  - Gate G3's OIDC item dated explicitly: it needs Ostium's Keycloak re-baseline notice (planned 2026-11-09, target 2026-11-13), AetherSpark's own scan of that digest on both architectures, and the owner's exceptions for its High and Critical findings; so it lands after P3a's planned end (about 10 Nov), at the earliest in the week after the notice.
- **Threat model:**
  - the internal root as a new trust anchor (name-constrained, off-line root);
  - Keycloak's admin endpoints and their controls.

## Tests (acceptance of WP3.1)
| # | Check | Where |
|---|---|---|
| C1 | `just up` from a clean clone (no dev key, no secrets, no CA) reaches healthy; `just down` leaves no secret on disk | desktop, CI |
| C2 | L1 passes on both files; a service outside the matrix, a published port without an explicit loopback or Home address, or a secret source outside `secret_source_dir` fails it | CI |
| C3 | Certificate inventory (NFR-SEC-05): every internal listener presents a certificate from AetherSpark's CA with its internal name; a plain connection to `postgres-auth` is refused | CI |
| C3b | Every client refuses a certificate from a foreign CA or with a wrong name: Traefik → Keycloak, Keycloak → `postgres-auth` (`verify-full`), renewer → step-ca, forward-auth → Keycloak | CI |
| C4 | Nothing is published except Traefik's 443, on `127.0.0.1` on the desktop | desktop, CI |
| C5 | No secret value in `docker inspect` of any container, in a Compose file, in an image or in a process environment | CI |
| C6 | The egress suite passes with the WP3.1 services in the stack (Gate G3). *As built (2026-11-10):* below the table | desktop, CI, Spark |
| C7 | The image scan covers every image of the stack, the Keycloak build and the renewer included, on both architectures (including an arm64 entry in each index digest) | CI |
| C8 | `whoami.aether.localhost` signs a test user in through Keycloak with MFA and shows the user's name | desktop |
| C9a | **Normal renewal**, with 10-minute certificates in CI: renewal at two thirds; the `postgres-auth` reload (shortened in the test) and Keycloak's reload pick up the new files with no failed connection; after a stop shorter than the lifetime (the overnight case), `just up` renews before reporting healthy | CI |
| C9b | **Recovery after expiry:** with every leaf expired, `just up` re-bootstraps and reaches healthy; with a short-lived test intermediate expired, issuance fails visibly and the runbook's renewal with the root restores it | CI |
| C10 | **Required functions work** through Traefik: discovery, the authorization code flow with PKCE and MFA, token and refresh, JWKS, userinfo, logout; client credentials and introspection on the back-channel entrypoint. **Admin is denied** on `auth.` and on the back-channel entrypoint: `/admin/`, `/realms/master/` and the management port, including `..`, encoded and doubled-slash variants; the admin route refuses an address outside its allowlist, with the real source address checked. **No bypass:** no container but Traefik reaches Keycloak's port (from `edge`, `auth-edge` and `pki` members); no response for an IP address, an unknown `Host`, an internal `*.svc` name on the public entrypoint, or an SNI that differs from `Host`; a forged `X-Forwarded-For` from a non-Traefik address is ignored. **Master realm:** MFA required, brute-force detection on, no direct grants | CI |
| C11 | **Name constraints, per client:** a certificate for a name outside the place's suffix, signed by a test intermediate set up like the real one, is refused by every client the stack uses (Traefik and the step CLI in Go, Keycloak's Java and its JDBC driver, the .NET back-channel client, curl/OpenSSL in the tests) and, in the trust-distribution runbook, by the owner's browsers (Firefox, Edge) | CI; desktop (browsers) |

**C6 as built (2026-11-10).** The egress suite's own clients stand in for kinds of containers; C6 checks the stack's real services, at the end of `tests/stack/run.sh`, after the whole run.
- **From inside each service's network namespace** (step-ca, a renewer, postgres-auth, Keycloak, forward-auth, the test page): a throw-away container joins the service's namespace (`--network container:`), so it has the service's routes and resolver whatever tools the service's image has. It checks, as E2, E7 and E8 do:
  - HTTPS to a public address and to a public name fails;
  - Docker's resolver gives no answer for an outside name, and a public resolver cannot be reached;
  - IPv6 out fails.
  Each service's control must work from the same place: a name of the stack it may resolve.
- **With the host rules (layer 3), in CI:** `tests/stack/layer3.sh` builds the images, installs the rules with test settings (as `tests/egress/layer3.sh` does), then runs the stack test under them, as on the Spark. Its extra checks:
  - **E14, adapted:** during the whole run (sign-ins, renewals, restarts, expiry and recovery), the rules dropped nothing from the stack's networks, so no service tried to reach anything outside;
  - **E16:** no service reaches a host listener through its network's gateway;
  - **Traefik**, on `ingress`, a network with a route out: the same probes fail, and the drops are logged on `ingress` (E9).
- **On the desktop** there is no layer 3. Traefik's probes are skipped with a visible line: on Docker Desktop it can reach the internet, as the egress design accepts.
- **On the Spark** C6 runs with the stack's first deployment (WP6.4).

## Residual risks
- **The intermediate key is on-line,** in step-ca's volume and encrypted. Its name constraint limits a theft to the place's suffix for the clients C11 verifies; the off-line root and the encrypted backup cover the rest.
- **A root-key theft is not limited by the constraint** for clients that ignore a trust anchor's constraints; the root is protected by staying off-line, and its theft means a re-init.
- **Keycloak's findings at the pin** are accepted only by the owner's exceptions, for at most 90 days.
- **The desktop has no layer 3** (Docker Desktop): layers 1 and 2 and development data only, as the egress design accepts.
- **24-hour certificates need the renewers:** a stopped renewer breaks TLS within a day, visibly; C9a and C9b prove renewal and recovery.

## Concepts
- **Certificate authority (CA):** the issuer whose signature a client checks before it trusts a server. AetherSpark runs its own (step-ca), so internal names need no public CA and nothing internal is ever announced publicly.
- **Name constraint:** a limit written into the CA's own certificate: it may only vouch for names under one suffix. Browsers enforce it, so even a stolen CA key cannot fake `github.com`.
- **Off-line root:** the top key signs the intermediate once, then leaves the machine. The intermediate does the daily work; if it is ever lost, the root, kept apart, makes a new one.
- **ACME:** the protocol a server uses to ask a CA for a certificate and prove it holds the name; Traefik speaks it to step-ca.
- **Reverse proxy:** the one door. Browsers talk only to Traefik; Traefik forwards to the service on an internal network, and decides which paths are allowed at all.
- **Forward-auth:** Traefik asks a small helper "is this browser signed in?" before it lets a request through; if not, the helper sends the browser to Keycloak.
- **Renewer:** a tiny container that asks step-ca for a fresh certificate before the old one expires, and writes it where its service reads it.
- **tmpfs:** a folder in memory only. A secret decrypted there never touches the disk and is gone at shutdown.
- **`*.localhost`:** names that by standard always mean "this machine", so a service named that way cannot be reached from another computer.
