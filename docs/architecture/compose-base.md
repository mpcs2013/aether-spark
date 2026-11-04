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
- **Out of scope:** the data Postgres with pgvector, which joins with its first user in P4a, like Redis; ClickHouse (P4a); the services this repository builds (P4a on); observability (WP3.2); the backup job (WP3.4); the Spark's deployment unit (WP6.4); the NAS certificate (WP6.6).

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

## Secrets (WP3.3, first)
As ADR-0010 decides, made buildable:

- **One in-memory folder per machine:** `/run/aetherspark/secrets/`, a tmpfs, mode 0700. `just up` decrypts each secret there (`age -d` to the file), and refuses to start if the folder is not a tmpfs with that mode. It is the only path under `/run` that the Compose lint accepts for a secret's source (`secret_source_dir` in `policy.json`, with an allowed and a refused fixture).
- **Mounted only into its service.** Compose file secrets are bind mounts, and Compose ignores their `uid`, `gid` and `mode` outside Swarm. So the file's own owner and mode on the host decide who reads it, and `just up` sets them per service.
- **Desktop proof first (PR 2, before any service uses it).** Docker Desktop runs containers in its own VM and reaches WSL2 files through a file-sharing layer, so "a tmpfs in WSL2" alone does not prove the container sees memory. The proof, scripted as `tests/secrets/desktop-proof.sh`, checks each step and fails closed:
  1. **Memory-backed at the source:** the folder in WSL2 is a tmpfs (`findmnt -T` reports `tmpfs`), mode 0700.
  2. **Memory-backed in the container:** the secret is read from that tmpfs and never copied to disk on the way: the mount type seen inside, and a random marker value found neither in Docker Desktop's VM disk nor in WSL2's own disk after a sync.
  3. **Permissions:** inside the container the file is readable by the service's user and by no other user; a second container without the secret cannot see it.
  4. **Not in metadata:** the marker appears in no `docker inspect`, no `docker compose config` output, no environment and no log.
  5. **Cleanup:** after `just down` the folder is empty; after `wsl --shutdown` and a restart it is gone; a killed `just up` leaves nothing on disk either.

  If a step fails, the design comes back with the alternative (a `tmpfs`-driver named volume filled over stdin) and the same proof, before any service depends on it. The Spark repeats steps 1 and 3 to 5 natively (WP6.4).
- **The secrets of WP3.1:**

  | Secret | Read by |
  |---|---|
  | `step-ca-password` | step-ca |
  | `step-ca-provisioner-keycloak`, `step-ca-provisioner-postgres-auth` | `just up`'s bootstrap step only |
  | `postgres-auth-superuser-password` | `postgres-auth`, setup only |
  | `keycloak-db.conf` (`db-password=…`) | Keycloak, `postgres-auth` setup |
  | `keycloak-bootstrap.conf` (first-start admin password) | Keycloak, first start only |
  | `forward-auth-client-secret`, `forward-auth-cookie-secret` | forward-auth |

- **Dev store** on the desktop: `~/.aetherspark/secrets/dev/`, age-encrypted with the dev key. `just up` on a clean clone creates the dev key and the missing secrets itself (`just secrets generate`), so "`just up` from clean WSL2" (NFR-OPS-01, Gate G3) holds.

## `postgres-auth`
- **Image:** the official Postgres 18 image, pinned by digest; no pgvector, since it holds only Keycloak's data.
- **Data volume** at `/var/lib/postgresql` (Postgres 18 images keep their data in `/var/lib/postgresql/18/docker`).
- **Access:** a reviewed `pg_hba.conf` (`-c hba_file=`): `hostssl keycloak keycloak <db-auth subnet> scram-sha-256`, the superuser on the local socket only, `reject` for everything else; `password_encryption=scram-sha-256`, `ssl_min_protocol_version=TLSv1.2`. Keycloak connects with `sslmode=verify-full` and step-ca's root as `sslrootcert`.
- **Memory:** about 512 MB, from the desktop's headroom and the Spark's 8 GB platform budget.

## Gate G3's test relying party
A pinned forward-auth container (an OIDC proxy) and a minimal "whoami" page. Traefik routes `whoami.aether.<place>` through forward-auth. Forward-auth signs the browser in at Keycloak (client `forward-auth`, confidential, MFA by the realm) and checks the token against Keycloak over the internal CA. The page shows the signed-in user's name. This proves the chain AetherSpark's own UIs will use, end to end.

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
- **Profiles:** everything of WP3.1 is in `core`. ADR-0003's `data` profile starts with the data Postgres in P4a; `postgres-auth` is identity infrastructure, needed for any sign-in, so it belongs to `core`.

## Order of work (pull requests)
1. **This design** and its document changes (below).
2. **WP3.3 secrets:**
   - the desktop proof (`tests/secrets/desktop-proof.sh`: memory-backed through WSL2 and Docker Desktop, permissions, cleanup), first;
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
| C6 | The egress suite passes with the WP3.1 services in the stack (Gate G3) | desktop, CI, Spark |
| C7 | The image scan covers every image of the stack, the Keycloak build and the renewer included, on both architectures (including an arm64 entry in each index digest) | CI |
| C8 | `whoami.aether.localhost` signs a test user in through Keycloak with MFA and shows the user's name | desktop |
| C9a | **Normal renewal**, with 10-minute certificates in CI: renewal at two thirds; the `postgres-auth` reload (shortened in the test) and Keycloak's reload pick up the new files with no failed connection; after a stop shorter than the lifetime (the overnight case), `just up` renews before reporting healthy | CI |
| C9b | **Recovery after expiry:** with every leaf expired, `just up` re-bootstraps and reaches healthy; with a short-lived test intermediate expired, issuance fails visibly and the runbook's renewal with the root restores it | CI |
| C10 | **Required functions work** through Traefik: discovery, the authorization code flow with PKCE and MFA, token and refresh, JWKS, userinfo, logout; client credentials and introspection on the back-channel entrypoint. **Admin is denied** on `auth.` and on the back-channel entrypoint: `/admin/`, `/realms/master/` and the management port, including `..`, encoded and doubled-slash variants; the admin route refuses an address outside its allowlist, with the real source address checked. **No bypass:** no container but Traefik reaches Keycloak's port (from `edge`, `auth-edge` and `pki` members); no response for an IP address, an unknown `Host`, an internal `*.svc` name on the public entrypoint, or an SNI that differs from `Host`; a forged `X-Forwarded-For` from a non-Traefik address is ignored. **Master realm:** MFA required, brute-force detection on, no direct grants | CI |
| C11 | **Name constraints, per client:** a certificate for a name outside the place's suffix, signed by a test intermediate set up like the real one, is refused by every client the stack uses (Traefik and the step CLI in Go, Keycloak's Java and its JDBC driver, the .NET back-channel client, curl/OpenSSL in the tests) and, in the trust-distribution runbook, by the owner's browsers (Firefox, Edge) | CI; desktop (browsers) |

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
