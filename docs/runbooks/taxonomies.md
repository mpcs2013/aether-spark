# Taxonomy session
How the XBRL taxonomies get into the taxonomy store, which the pipeline reads offline. The design is [data-plane.md](../architecture/data-plane.md), section 4 and "As built", and [egress.md](../architecture/egress.md), "Model store and taxonomies".

## Concepts
- **A taxonomy** is the dictionary an XBRL filing's numbers are tagged with: US GAAP, SRT, the SEC's `dei` and a few small SEC lists, a version per year. A filing names the files it uses by their web address (for example `https://xbrl.fasb.org/us-gaap/2024/elts/us-gaap-2024.xsd`).
- **The store** keeps a copy of each such file, in the folder layout Arelle, the XBRL reader, looks in when it is offline: `<store>/https/xbrl.fasb.org/us-gaap/2024/...`. On the desktop it is `E:\aetherspark\taxonomies`; on the Spark `/srv/taxonomies`.
- **Why a session:** the pipeline reads untrusted filings and has no way out to the internet. So the files come in beforehand, in a session you start, through the ops proxy, from three hosts only: `xbrl.fasb.org`, `xbrl.sec.gov`, `www.xbrl.org`. The job never reads a filing: it starts from a committed list, `deploy/taxonomies/entry-points.txt`, and follows what those files refer to.
- **The SEC contact:** `xbrl.sec.gov` asks every client for a contact in its User-Agent, as the SEC's other sites do. The job uses the sealed one the ingester uses; it is never printed or written to the store.
- **The manifest:** `MANIFEST.sha256` in the store lists every file with its SHA-256. The hosts do not publish a checksum for every file, so the first session's manifest is what later sessions compare with (trust on first use).
- **When a filing needs a file the store lacks,** its job fails with that file's address. Then the list grows and the session runs again; files already stored are kept, so only new ones are fetched.

## 1. Before you start
- The stack is up (`just up`), so the SEC contact is unsealed. If it was never entered: `just secrets enter sec-user-agent-contact` (secrets.md).
- On the Spark only: in an ops session (spark-host.md). The Spark's run comes with WP6.4; the desktop's first.

## 2. Run it
In PowerShell, in the repository:
```powershell
just taxonomies
```
It builds the pipeline's image if needed, starts the ops proxy, runs the job, and removes the ops proxy again, whatever the outcome. It prints a line every 250 files, then a summary:
```
taxonomies: 170 entry files, into /taxonomies
  250 fetched, ... bytes so far
  ...
taxonomies: N fetched, 0 already stored, ... bytes
taxonomies: the store holds N files, ...
```
- **`outside the hosts, not fetched: www.w3.org (...)`** is expected: Arelle brings those base files itself.
- **`FAILED <address>: HTTP 404`**: a file the host does not have. Note it; the job still stores the rest and ends with exit 1.
- **`STOPPED: xbrl.sec.gov answered 403`**: the SEC refuses the client; the job stopped at once rather than asking again. Check the contact, then tell me before running it again; what was stored stays.

## 3. After the run
1. Send me the summary lines (counts, size, any FAILED or STOPPED line). They are recorded in data-plane.md.
2. Keep the store's manifest as it is; the next session compares with it.

## 4. Again later
- After a walk season, or when jobs fail with "Could not load file … <address>": the entry list is measured again from the stored filings and grows in a pull request; then this session runs again.
