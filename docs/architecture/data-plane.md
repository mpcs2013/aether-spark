# Data plane (P4a) — design

**Status:** accepted 2026-11-19 (#112); being built (As built below). Implements the roadmap's P4a (WP4.1–WP4.5) on the desktop with the dev universe, under [ADR-0002](../adr/0002-hybrid-dotnet-python.md), [ADR-0007](../adr/0007-data-stores.md), [ADR-0012](../adr/0012-network-egress.md) and the [egress design](egress.md), [ADR-0014](../adr/0014-quant-dataframe-engine.md), [ADR-0020](../adr/0020-research-universe.md). Requirements: NFR-DQ-01…04, NFR-SEC-01/04/05, NFR-OPS-01/02/06 ([NFRs](../01-nfr.md)); threat T20 ([threat model](../02-threat-model.md)).

**Reviews (2026-11-19).** `architect` CONSISTENT WITH FIXES (7 major, 4 minor), `security-reviewer` GO WITH FIXES (5 major, 5 minor). All findings are folded in; two of them became owner decisions 4 and 5.

## In plain words
The data plane fetches the companies' annual and quarterly reports from the SEC, reads every reported number out of them, checks the numbers, and stores each one with two dates: the period it describes, and the moment the SEC accepted the report. A question "what did we know on day D?" then only ever sees numbers accepted by D, so a backtest cannot peek into the future. A corrected report (restatement) adds new rows; the old numbers stay. Numbers that break a rule (assets not equal to liabilities plus equity, say) are set aside with a reason, and the rest of the report goes on.

Two programs do the work. A .NET **ingester** downloads filings, politely and only from the SEC, through the runtime proxy. A Python **pipeline** turns each filing into rows. They never call each other: the ingester writes a job row into a table, and the pipeline picks it up (ADR-0002's "data, not calls"). The filings are written by strangers, so the pipeline treats them as hostile: it has no way out, reads only the files of its job, and can only add rows.

## Goal and scope
- **In scope (P4a, desktop, dev universe):** the data Postgres and ClickHouse in the stack; the reference tables, universe membership and the fact table (WP4.1); the EDGAR ingester (WP4.2); the XBRL pipeline (WP4.3); validation and quarantine with the golden set (WP4.4); filing text chunks with their metadata (WP4.5, without embeddings); the runtime proxy in `just up`; Gate G4.
- **Out of scope:** embeddings and the recall test (decision 3; P5a/P5b, decision 5); the full-universe ingest (WP5b.2, on the Spark); prices (Q4's price job, a later package); any API (the first token-validating API is WP5a.1, exchange O-037 of 2026-11-19); deployment on the Spark (WP6.4).

## Owner's decisions (2026-11-19)
1. **The ingester starts the pipeline by a row, not a container.** Nothing in the stack may reach the Docker API (egress design, L1), so .NET cannot start a container. The pipeline is a long-running Python **worker** that polls a `job_runs` table; it has no listener. ADR-0002 amendment 2 records it.
2. **Numbers come from the XBRL instances, parsed by Arelle**, as ADR-0002 plans: full contexts and dimensions, our own concept mapping, scaling and sign rules. Taxonomy packages are downloaded once in an ops session with the owner, checksummed, and mounted read-only (egress.md, "Model store and taxonomies").
3. **No embeddings in P4a.** Every model call goes through LiteLLM (ADR-0006), which arrives in P5a, and the recall test needs the golden question set of WP5a.4. WP4.5 keeps chunking and metadata. The Spark is here, and P5b runs the real recall test there.
4. **The P4a services go into the hand-written Compose file**, like step-ca and Keycloak: `postgres-data`, `clickhouse`, `ingester`, `pipeline`, with digest pins, secrets as files and the matrix's networks. `aspire publish` would hand connection strings and passwords to services as environment variables (NFR-SEC-04 forbids it), and publishing a Python service and its arm64 build were never tested (ADR-0002 note of 2026-10-22). Aspire stays the development loop (`aspire run`, with a stub SEC and the golden files only, decision 13). ADR-0002 amendment 2 and an ADR-0003 note record it; `aspire publish` is revisited when it can deliver secrets as files.
5. **Embeddings get their own worker (P5a), never the pipeline.** The pipeline parses untrusted filings, so it never joins `llm`: an embed worker reads finished chunks from Postgres and is the only data-plane service on `llm`. The lint forbids the pipeline to share a network with the agent host, LLM serving, LiteLLM or the MCP servers.

**Proposed in this note** (the owner confirms them by accepting it):

6. **Images.** Each new image is a target of the image scan on both architectures (ADR-0005 amendment 3): `postgres-data` from the official Postgres Alpine image with pgvector built in, like `postgres-auth`; `clickhouse` from the upstream image pinned by digest, its base chosen in PR 2 by the scan (an Alpine build from the release binary if the upstream image carries High or Critical findings, as for forward-auth); `ingester` and `pipeline` built here. The data Postgres is a server of its own: identity data never shares a server with research data (compose-base.md).
7. **Rights.** One **migration owner** per store, whose secret only `just up`'s migration step mounts; the running programs get row rights only (D8):
   - the ingester: `ref` and `ingest_ops` (its Wolverine tables are created by the migrations; Wolverine's own schema creation is off);
   - the pipeline: insert into `pipeline.*` and ClickHouse's `facts` and its staging table; on `ingest_ops.job_runs` select and an update of the status columns only; nothing on `ref`;
   - no role may create in `public` or hold Postgres's `pg_*` roles; ClickHouse's `default` user is removed, and table functions (`url`, `file`, `remote`, `s3`, `executable`, …) are off for every user.
   This replaces the matrix's "write-only roles" (egress.md), which could not hold Wolverine's state or the duplicate check.
8. **Secrets** (all generated and sealed, files only, NFR-SEC-04), each with its consumer, entered in the manifest with PR 2 and PR 3: the data Postgres's superuser (`postgres-data`), its migration owner and ClickHouse's (`just up`'s migration step), the ingester's and the pipeline's logins to each store, the backup reader's (with the backup extension), one JWK provisioner per new server (`cert-bootstrap`). The **SEC contact for the User-Agent** is entered by the owner (ADR-0010 note of 2026-11-19): personal data, never in the public repository, never in a log or trace, and a dummy value in CI. Each program builds its connection string from its secret file in code; ClickHouse gets its own start script and a `users.d` file with password hashes, delivered as a secret, so its image's environment-variable password is never used. C5 covers all four services.
9. **Raw downloads live outside Docker's disk:** a host folder (`AETHERSPARK_RAW_DIR`, on the desktop on `E:`, roadmap storage layout), bound read-write into the ingester and read-only into the pipeline, no other service; checked by the lint (section 7). It is not backed up; a checksum manifest is (ADR-0013).
10. **Golden filings are public SEC excerpts in the repository**, each under 1 MB with its source (accession number, file name, SHA-256) and its expected rows, as ADR-0015 allows. The quant-validator's lane edits only the manifests (agent-system.md).
11. **G5v and Gate G4 are checked by hand** until the quant-validator agent comes with WP2.6: every data-pipeline pull request gets the owner's G5v check (golden set, point in time, units, scale, sign), not only the gate.
12. **The dev universe:** ~100 hard companies × 10 fiscal years (FY2015–FY2024, proposed), a reviewed file in the repository (CIK, name, why it is hard). Public data only.
13. **Real filings only under `just up`.** `aspire run` is outside the egress control (egress.md, scope), so in development the ingester talks to a stub SEC and the pipeline reads the golden files; real downloads and parsing run in the stack, behind the proxy and the internal networks.

## How it works
```
SEC (www.sec.gov, data.sec.gov)
  │  HTTPS through the runtime proxy (egress-runtime → out-runtime), SNI checked
ingester (.NET, Wolverine) ──writes──▶ raw folder (by accession) + data Postgres: ref.*, ingest_ops.job_runs (job contract)
                                                                       │ (row: queued)
pipeline worker (Python: Arelle offline + Polars) ◀────────polls──────┘
  │ job folder in memory → staging → dedup by fact_hash → insert
  ▼
ClickHouse: facts (MergeTree)            data Postgres: pipeline.membership, pipeline.anomalies, pipeline.chunks
```
### 1. Universe and identity (WP4.1)
- **Issuers by CIK** (ADR-0020); tickers are rows with validity dates, never keys (`ref.issuers`, `ref.tickers`, from the SEC's submissions index).
- **Filings:** `ref.filings` per accession number: CIK, form, `known_at`, filed date, the files and their SHA-256.
- **Membership is computed by the pipeline** (`pipeline.membership(cik, fiscal_year, public_float, known_at)`), because `dei:EntityPublicFloat` is an XBRL fact and only the Python side parses XBRL (ADR-0002). In the dev universe it is computed and shown, not used to filter.
### 2. The fact table (WP4.1)
- **ClickHouse `facts`**, plain `MergeTree`, `ORDER BY (cik, concept, period_end, known_at)`, append-only (ADR-0007 change 3). Columns: `cik`, `accession_no`, `form`, `concept` (namespace and name), `unit`, `value` `Decimal(38, 6)` (reported values exact, ADR-0002 rule 4; six places, proposed, cover per-share values; a value with more places is quarantined, never rounded), `decimals` as reported, `period_start`, `period_end` (instants: equal), `dimensions` (sorted axis/member pairs; empty for the default context), `known_at` `DateTime64(3, 'UTC')`, `filed_date`, `fiscal_year`, `fiscal_period`, `fact_hash`, `ingested_at`.
- **A fact's identity across versions** is its full context: `(cik, concept, unit, period_start, period_end, dimensions)`. "Value as known on D" is the row with the latest `known_at <= D` **per full context** (ADR-0007 note of 2026-11-19): per `period_end` alone, a 10-Q's three-month and year-to-date values, or two units, or two segments, would replace each other.
- **`known_at` is the SEC acceptance datetime in UTC** (ADR-0007 change 2), taken from the filing's own header, which states it in US Eastern time, and converted with daylight-saving rules. Where the SEC's JSON index also carries it, the two must agree (D3); no time-zone label is trusted on its own.
- **The roadmap's `valid_date`/`known_date` are these two columns:** `period_end` (valid) and `known_at` (known); the roadmap uses ADR-0007's names now.
- **Lineage (NFR-DQ-01)** is every row's `accession_no`, `filed_date`, `known_at`, `concept`, `unit` and context (`period_*`, `dimensions`), with `ref.filings` holding the raw files' SHA-256.
- **Migrations:** EF Core for the data Postgres, versioned SQL files for ClickHouse (ADR-0007), applied by `just up`'s migration step as the migration owner, and tested from an empty database.
### 3. The ingester (WP4.2)
- **.NET 10 worker with Wolverine**, durable state in `ingest_ops` (ADR-0007 change 4).
- **What it fetches:** for each universe CIK, the SEC's submissions index; for each 10-K, 10-Q and amendment in the period, the filing index, then from it the XBRL instance, its extension schema and linkbases, and the primary document. URLs are built only from the CIK and from accession numbers and file names taken from the SEC's index for that CIK, each validated (accession `^\d{10}-\d{2}-\d{6}$`, file name `^[A-Za-z0-9._-]+$`, the CIK path checked); a URL found inside a filing is never followed (egress.md).
- **Politeness:** at most 5 requests per second (the SEC's published limit is 10), the declared User-Agent (decision 8), retry with exponential backoff and jitter on 429 and 5xx, and a stop after repeated refusals (Polly).
- **Idempotent by accession number:** a filing already in `ref.filings` with the same file hashes is skipped; re-running a year changes nothing (NFR-DQ-03).
- **Hand-off:** after a filing's files are stored and their SHA-256 verified, one transaction writes its `ref.filings` row and a `job_runs` row with the **job contract** (`contracts/`, ADR-0002 rule 2): the accession number, CIK, form, `known_at`, filed date, and per file its validated name and SHA-256. Nothing else, no paths: the pipeline builds every path itself.
- **Network:** `egress-runtime` (to the runtime proxy) and `ingest`. No listener.
### 4. The pipeline worker (WP4.3)
- **Polling:** `SELECT … FOR UPDATE SKIP LOCKED` on `job_runs`, one job at a time; status `running`, `done` or `failed` with a reason; a job stuck in `running` past a timeout returns to `queued` once, then fails.
- **The job's files:** the worker checks the contract's formats, copies the named files from the raw folder into an in-memory job folder, and re-hashes each against the contract; a mismatch fails the job. This also closes the gap between the ingester's check and the parse, since the raw folder is writable from Windows.
- **Parsing:** Arelle offline, with a resolver that allows only the job folder and the read-only taxonomy mount: no network, no other local path (no `/run/secrets`, no `/proc`, no `..`, no `file://` outside the two). XML limits: no external entities, no DTD loads, a size limit per file. Each job's parse runs in a child process with a memory limit (`RLIMIT_AS`) and a timeout; the container has memory and process limits and a read-only root filesystem (T20).
- **Normalising (Polars LazyFrame, no row loops, ADR-0014):** concept mapping to the house concept list, unit scaling from `decimals`/scale, sign conventions per concept, duplicate contexts collapsed when equal and quarantined when not.
- **Loading:** rows go to a staging table, duplicates are removed by `fact_hash` (which includes `accession_no`), then inserted into `facts` in one batch per filing; anomalies and chunks are inserted only if their natural key is absent, so re-runs add nothing (D2).
- **Network:** `ingest` and its telemetry network only. No listener, no proxy route, never `auth-ingest` (no Keycloak or JWT dependency in Python, ADR-0002 rule 3), never `llm` (decision 5).
### 5. Validation and quarantine (WP4.4)
- **Rules (NFR-DQ-04):** balance-sheet identity within the filing's rounding; sign conventions; unit scale (a value 1 000× off its own history); duplicate contexts with different values; more decimal places than `Decimal(38, 6)` holds; a period end that does not fit the fiscal calendar. A breach writes a `pipeline.anomalies` row (rule, fact, reason) and keeps the fact out of `facts`; the rest of the filing loads.
- **Consistency check (a reader role, after each batch):** every fact's `accession_no`, `cik` and `known_at` match its `ref.filings` row. It catches a pipeline that writes facts for another company or another time (T20, tampering).
- **Golden set:** at least 30 filings from the dev universe, chosen for the hard cases, each with its expected rows and anomalies (decision 10).
### 6. Text chunks (WP4.5, without embeddings)
- The primary document's text, split by 10-K/10-Q item and by footnote, stored as `pipeline.chunks` with CIK, accession number, item, position, `known_at` and a content hash (natural key: accession, item, position). The embed worker (P5a) adds the vectors.
### 7. Stack, networks, certificates and lint
- **Services and roles:** `postgres-data`, `clickhouse`, `ingester`, `pipeline` in the hand-written Compose file (decision 4), profile `data`. policy.json already names `clickhouse`, `ingester` and `pipeline`; today's single role `postgres` is allowed on both `db-auth` and `ingest`, so one container could join Keycloak's database network and the research data's. PR 2 splits it into `postgres-auth` (`db-auth` only) and `postgres-data` (`ingest`, later `data-read`), with postgres-auth's label.
- **Networks:** `ingest` (ingester, pipeline, postgres-data, clickhouse); `egress-runtime` (ingester); `pki` (their renewers); `obs-ingest` with WP3.2. `data-read` joins in P5a with the MCP servers.
- **TLS on every listener (NFR-SEC-05):** certificates from step-ca through a renewer each, as for postgres-auth; ClickHouse with its secure ports only (HTTPS and native TLS) and every other port off, the plaintext HTTP and native ports and the MySQL and PostgreSQL wire ports included; postgres-data with a `pg_hba.conf` like postgres-auth's: `hostssl` per role and the `ingest` subnet, everything else rejected. Clients verify name and chain.
- **The runtime proxy joins `just up`:** the egress overlay becomes part of the stack (its own Compose project until now), with only the SEC hosts on its list.
- **Lint additions (L1)**, each with a fixture that fails without it: the data stores never join `db-auth`, `auth-front` or any `out-*`; only the ingester joins `egress-runtime`; the pipeline on no proxy network and not on `auth-ingest`; the pipeline never shares a network with the agent host, LLM serving, LiteLLM or the MCP servers; the raw folder bound exactly, read-write for the ingester and read-only for the pipeline, for no other role (like `memory_bind_dirs`); the taxonomy store writable by the taxonomy job only (like the model store). egress.md's rule list grows with them.
- **Images** are build or pull targets of the image scan (decision 6).
### 8. Backup and observability
- **Backup:** the data Postgres as `pg_dump`, run inside its container by a backup reader role (never the ingester's or the pipeline's), ClickHouse as `BACKUP` to a backup disk of its own, plus the raw folder's checksum manifest. Staging (size, disk or memory) and the snapshot's new paths are decided with the backup job's change, after a request to the home infrastructure about size and retention (exchange A-015 fixed the job's four paths). Until then the recovery path for the data stores is rebuilding from the SEC and the raw manifest; the restore test comes with the backup change.
- **Observability:** the services are instrumented (ServiceDefaults; OpenTelemetry in Python), never with filing content or the User-Agent, and export nothing until WP3.2 brings the collector.

## Tests
| ID | What it proves | Where |
|---|---|---|
| D1 | Point in time, with the reference query of section 2 (latest `known_at <= as_of` per full context): for random `as_of` dates no fact with a later `known_at` is returned; a restatement is a second row and both stay; a 10-Q's three-month and year-to-date values for the same period end are both returned | CI, golden set |
| D2 | Idempotency: ingesting and processing the same filings twice leaves `facts`, `ref.*` and `pipeline.*` identical (counts and a table hash), anomalies and chunks included | CI |
| D3 | `known_at`: the header's Eastern time, converted to UTC, equals the JSON index's value for every golden filing, including one across a daylight-saving change and one accepted after 17:30 ET, which keeps its true time, not the next day's filing date | CI |
| D4 | Politeness: against a stub SEC, never more than 5 requests per second, the User-Agent present (a dummy in CI) and absent from logs and traces, backoff on 429 and 503, stop after repeated refusals | CI |
| D5 | No reach beyond the job: a filing with links and remote schema references, served by a **reachable** stub, makes the ingester request nothing outside its built URLs and the pipeline zero requests; references to `/run/secrets/…`, `file:///proc/…`, `../` and a traversing file name are refused; a file whose hash differs from the contract fails the job | CI |
| D6 | Hostile XML: entity expansion, external entities, an oversized file and a parse past its memory or time limit are stopped; the worker survives and marks the job failed | CI |
| D7 | Golden set: every golden filing gives exactly its expected rows and anomalies (reported values exact, Decimal) | CI |
| D8 | Rights: runtime roles cannot create, alter or drop; the ingester cannot read ClickHouse or `pipeline.*`; the pipeline reads `ingest_ops.job_runs`, updates only its status columns, and has no right on `ref.*`; no role creates in `public` or holds a `pg_*` role; ClickHouse has no `default` user and refuses table functions | CI |
| D9 | Quarantine: a seeded identity breach, sign error, scale error, excess precision and conflicting duplicate each give one anomaly row, and the filing's other facts load; the consistency check flags a fact whose `known_at` or `cik` differs from its filing | CI |
| D10 | Certificates, ports and networks: C3/C3b/C11 extended to postgres-data and clickhouse, every plaintext port refused; C5 for the four services; the L1 fixtures of section 7 fail; neither the ingester nor the pipeline listens on a socket; the runtime proxy lets the ingester reach only the SEC hosts | CI, desktop |
| D11 | CPU baseline: facts per second for the dev universe, recorded as a regression baseline, not as NFR-PERF-05 (a Spark target) | desktop |
| D12 | Parity (ADR-0014): the golden set on Polars CPU and GPU, if RAPIDS supports the RTX 2060; otherwise first on the Spark at WP6.4 | desktop or Spark |

**Gate G4:** D1, D2, D7 green; D11 recorded; the owner's hand check (decision 11).

## Order of work (pull requests)
1. This design note and its document changes.
2. **Stores:** postgres-data and clickhouse (images and scans, TLS, ports, roles and migration owners, secrets, `just up`'s migration step), the `postgres` role split, the lint additions; D8, D10 (stores).
3. **Ingester:** the runtime proxy in `just up`, the ingester, the job contract, the User-Agent secret, the raw folder, the dev-universe file, the stub SEC; D3, D4, D5 (ingester side).
4. **Pipeline:** the taxonomy download (an ops session with the owner), the worker, normalising, loading, membership; D1, D2, D5, D6.
5. **Validation and golden set:** rules, quarantine, the consistency check, ≥ 30 golden filings; D7, D9; D11, D12.
6. **Chunks;** then the backup extension, after the home infrastructure's answer.

**Dates:** P4a planned 10 Nov – 1 Dec, started 19 Nov after Gate G3; re-planned to **≈ 19 Nov – 23 Dec**.

## As built
- **PR 2 is split** like WP3.1's PR 5: **2a** the data Postgres, **2b** ClickHouse.
- **2a (2026-11-19), the data Postgres:**
  - **Image** `deploy/compose/stack/postgres-data/`: postgres-auth's pin and package fixes, with pgvector 0.8.7 compiled from its tagged source (archive checked by SHA-256; `OPTFLAGS=""` so it runs on any CPU of its architecture, no LLVM). The extension loads (`CREATE EXTENSION vector`, `halfvec`), but is created in the database only with the embeddings (P5a). A build target of the image scan.
  - **Roles and rights** (decision 7): `data_owner` owns the database `research` and its schemas and signs in only on the server's own socket; `ingester` and `pipeline` sign in over TLS from `ingest` only. The start script sets all three from their secret files at every start; `0001_schemas.sql` creates `ref`, `ingest_ops` and `pipeline` with default privileges: the ingester rows in `ref` and `ingest_ops`, the pipeline select and insert in `pipeline`; nobody creates in any schema or uses `public`.
  - **The migration step** is part of the server's start (`migrate.sh`, before it reports healthy), as the migration owner: each file of `migrations/` applied once, in one transaction, recorded with its SHA-256 in `meta.migrations`; a changed file stops the start. So the owner's secret is mounted only into the server, which needs it anyway to set the owner's password, and the services that need the tables start after them. The EF Core migrations of PR 3 arrive as such SQL files.
  - **No `data` profile:** the data Postgres starts with every `just up`, like the infrastructure; a profile would make `just up`, `just down`, the backup and the tests each name it. *Replaces*, in decision 4 and ADR-0003's note of 2026-11-19, "profile `data`".
  - **Network `ingest`** at 172.30.70.0/24, the subnet pg_hba.conf admits (checked by the stack test); the role `postgres` is split into `postgres-auth` and `postgres-data` in policy.json, with lint fixtures that fail when either joins the other's network, and when the pipeline joins `auth-ingest` or `llm`.
  - **Tests** in `tests/stack/run.sh`: C3/C3b (TLS checked by name and root, foreign root and address refused, plain refused), sign-in only for the two runtime roles over the network, D8 (rows yes, tables never, no schema crossing, no other role held), the migrations recorded, C5 for its four secrets, the served certificate, C6 from its namespace.
- **2b (2026-11-19), ClickHouse:**
  - **Image** `deploy/compose/stack/clickhouse/`: the upstream image's **Alpine variant** of the 26.8 LTS line, pinned by digest, with zlib and OpenSSL raised to Alpine's fixed releases (as postgres-auth). Chosen by the scan of 2026-11-19 (decision 6): the Debian-based distroless variant carried five libc6 findings, three with no fix; the Alpine variant only the two packages fixed here. No build from the release binary was needed. A build target of the image scan.
  - **The whole configuration is this repository's** (`config.xml`): the image's `/etc/clickhouse-server` is not used, so only the two TLS ports listen, HTTPS 8443 and native 9440; the plaintext HTTP and native ports, MySQL, PostgreSQL, interserver, gRPC and Prometheus are absent. TLS 1.0 and 1.1 disabled; the certificate from the renewer's volume. ClickHouse re-reads it on `SYSTEM RELOAD CONFIG`, which the start script runs every hour (measured on 26.8: both ports serve the new file after it).
  - **Users and rights** (decision 7) in `users.xml`, a template the start script fills with each password's SHA-256 from its secret file and writes to the in-memory `/tmp` at every start: `data_owner`, the migration owner, from the container's loopback only; `pipeline`, select and insert on `research.facts` only, from `ingest` only. **No `default` user, no ingester user** (it never reads ClickHouse), **no source right** (URL, FILE, REMOTE, S3, MySQL, PostgreSQL, …), so table functions that reach outside are refused by right for everyone (measured: `url`, `file`, `remote`, `s3`, `executable`, `mysql`, `postgresql`); users from a configuration file cannot be changed through SQL. Its secrets: the owner's and the pipeline's passwords, consumer `clickhouse` (the pipeline is added as a consumer with PR 4); no superuser.
  - **The migration step** as postgres-data's (`migrate.sh` in the start, before healthy, `meta.migrations` with SHA-256), with one difference: ClickHouse has no transactions for these statements, so every statement must be safe to repeat (`IF NOT EXISTS`) and a file that failed half-way is applied whole at the next start. `0001_facts.sql` creates `research.facts` as section 2 gives it (`decimals` NULL for INF, `fact_hash` the SHA-256 in hex); the pipeline's staging table comes with PR 4.
  - **The owner's client** connects to `127.0.0.1` over TLS without checking the certificate: the connection never leaves the container (as postgres-data's owner on its socket), and the certificate names the service. `clickhouse-client` checks the chain but not the name, so the stack test checks names with Python's TLS; the pipeline's client (PR 4) must check both, tested there.
  - **Memory** 2 GB on the desktop (`mem_limit`; the server stops queries at 80 % of it); the Spark's 12 GB (c4.md) comes with WP6.4. `pids_limit` 4096 above the server's own thread pool limit (2000), since threads count as processes.
  - **Tests** in `tests/stack/run.sh`: C3/C3b on both ports (chain and name checked; foreign root and address refused), only 8443 and 9440 listen and the upstream ports are closed, a wrong password, the `default` user and the owner over the network refused, `users.xml`'s network equals the `ingest` subnet, the migrations recorded, D8 (the pipeline inserts and reads facts; no drop, create, delete, truncate, no `meta`, no user list, seven table functions refused), C5 for its two secrets, the served certificate after reloads, C6 from its namespace.

## Document changes (in this note's pull request)
- **ADR-0002 amendment 2:** rule 1's trigger is a `job_runs` row picked up by a long-running worker without a listener (decision 1); the services built here go into the hand-written Compose file, not `aspire publish` (decision 4). ADR index row.
- **ADR-0003, a dated note:** the Compose files are the hand-written stack file (P4a's services included) and the checked-in overlays; `aspire publish` is not used until it delivers secrets as files.
- **ADR-0007, a dated note:** "latest row" is per full context (section 2).
- **ADR-0010, a dated note:** an entered secret may also be the owner's own value that must stay out of the repository (the SEC contact).
- **egress.md:** scope (no `aspire publish` file); the `ingest` row's own-schema roles; the `llm` row's embed worker; the pipeline out of the `auth-` groups. **policy.json, with it:** `pipeline` off `llm` and `auth-ingest`, and a `never_share_a_network` rule for the pipeline. The `postgres` split and the lint additions of section 7 change policy.json in PR 2, each with its fixture.
- **compose-base.md and c4.md:** "`aspire publish` output from P4a" becomes the hand-written file; compose-base's ".NET APIs (P4a)" becomes "(P5a, WP5a.1)".
- **Roadmap:** P4a (dates, WP4.1 names, WP4.2 hand-off, WP4.5 without embeddings, G4's baseline, the hand check); P5a gains the embed worker and the recall-test preparation.
- **Threat model:** T20, a crafted filing against the parser and the data (tampering included).
- **agent-system.md:** the P4a row: G5v checked by the owner until the quant-validator comes with WP2.6.

## Residual risks
- **Arelle is a large parser of untrusted input.** Contained by the worker's isolation (no route out, the job folder only, row rights only, limits), not by trusting the parser.
- **A compromised pipeline or ingester can still write false facts** for any company and time: rows are append-only and the consistency check flags facts that disagree with their filing, but a fact with a wrong value and a right filing is caught only by the validation rules and the golden set.
- **The SEC can block an address that misbehaves.** The rate limit sits well below the published one, and the ingester stops after repeated refusals.
- **The desktop has no layer 3,** and the runtime proxy now runs with every `just up`; the SEC-only allowlist with its SNI check and the internal networks remain.
- **Desktop disk:** Docker's disk on `C:` is limited to 64 GB; the dev universe's facts are small, and raw files live on `E:`.

## Concepts
- **XBRL:** the machine-readable part of an SEC filing: each number tagged with a concept (for example `us-gaap:Assets`), a unit, a period and optional dimensions.
- **Taxonomy:** the dictionary of concepts XBRL uses; downloaded once, kept offline.
- **Acceptance time (`known_at`):** the moment the SEC accepted a filing; the first moment the market could know its numbers.
- **Point in time:** answering "as known on day D" with only what was accepted by D.
- **Restatement:** a later filing that corrects earlier numbers; stored as new rows, never as an overwrite.
- **Context:** what a number is about: its period, unit and dimensions (for example "Europe segment"). Two numbers are versions of each other only if their whole context matches.
- **Idempotent:** doing the same work twice leaves the same result as doing it once.
- **Quarantine:** numbers that break a rule are kept aside with the reason instead of stopping the load.
- **Golden file:** a real filing excerpt with the exact rows the pipeline must produce from it.
- **Migration owner:** the database account that may change tables; the running programs only read and write rows.
