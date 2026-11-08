# Certificates — runbook (WP3.1)

How AetherSpark's own certificate authority (CA) is made, watched, renewed and trusted on the desktop. The design is the "Certificates" section of [compose-base.md](../architecture/compose-base.md); the secrets it uses are in [secrets.md](secrets.md). The Spark gets its own CA, for `*.aether.home.arpa`, with WP6.4.

**In plain words.** Every connection inside the stack is encrypted, and every program checks who it is talking to. For that, each service holds a certificate: a signed note saying "I am `postgres-auth.svc.aether.localhost`". AetherSpark signs these notes itself with step-ca, so nothing internal is ever announced to a public CA. The CA's certificates may vouch only for names under `aether.localhost`, so even a stolen CA key cannot fake `github.com` to a client that checks this limit. Certificates last 24 hours and are renewed automatically. A short life means a stolen certificate is soon useless; the price is that renewal must keep working, which the tests prove.

## What lives where (desktop)
| What | Where | Notes |
|---|---|---|
| Root certificate (10 years) | Docker volume `aetherspark_step-ca`, `certs/root_ca.crt` | Public; the one certificate browsers and clients trust |
| **Root key** | WSL2: `~/.aetherspark/secrets/dev/step-ca-root-key.age` | Sealed with the dev key, never in the volume. Needed only to sign a new intermediate |
| Intermediate certificate (1 year) | volume, `certs/intermediate_ca.crt` | Signs the 24-hour certificates every day |
| Intermediate key | volume, `secrets/intermediate_ca_key` | Encrypted with the secret `step-ca-password` |
| step-ca's configuration | volume, `config/ca.json` | Written by `deploy/ca/ca.py`: name rules, provisioners. No password in it |
| postgres-auth's certificate and key | volume `aetherspark_postgres-auth-tls` | Written by the bootstrap step, then by its renewer; postgres-auth reads them read-only and re-reads them every hour |
| Keycloak's certificate and key | volume `aetherspark_keycloak-tls` | The same, with `keycloak-renewer`; Keycloak re-reads them every hour itself |
| Traefik's certificates | Traefik's memory (tmpfs) | Ordered from step-ca at every start, renewed by Traefik |

The root key exists unencrypted only for a moment: in a throw-away container's in-memory folder and in the tool's memory, on its way into `age`. It never touches a disk unencrypted.

## Everyday (in the repository)
In PowerShell or in WSL2: from PowerShell, these `just` commands run in WSL2 by themselves (in the distribution `Ubuntu-24.04`; another one through the variable `AETHERSPARK_WSL_DISTRO`).

| Command | What it does |
|---|---|
| `just up` | Opens the secrets into memory, creates the CA on first use, gives each service a certificate if it has none or an expired one, starts everything and waits until healthy |
| `just down` | Stops the stack and empties the in-memory secrets folder. Volumes, so the CA, stay |
| `just ca status` | The intermediate's remaining days and the root's fingerprint; exit 1 below 60 days |
| `just ca root` | Prints the root certificate (for the trust steps below) |
| `just stack-test` | Tests C3, C3b, C5 (postgres-auth, Keycloak), C9a, C9b, C10 (what exists so far) and C11 in a throw-away copy of the stack (about 20 minutes; needs port 443, so `just down` first) |

`just up` prints `ca status` at the end. Below 60 days it says so: renew the intermediate (below).

## Trust the root in your browsers (desktop, once per CA)
Only needed when you open a stack page in a browser (Gate G3's sign-in, PR 6); the stack itself does not need it. Do it on Windows, where the browsers run.

1. In PowerShell, in the repository, save the root certificate and show its fingerprint:

   ```powershell
   just ca root > "$env:USERPROFILE\Downloads\aetherspark-dev-root.crt"
   just ca status
   (Get-PfxCertificate "$env:USERPROFILE\Downloads\aetherspark-dev-root.crt").GetCertHashString('SHA256').ToLower()
   ```

   The last line must equal the `root fingerprint` that `ca status` prints. If not, stop: the file is not your CA's root.
2. **Edge and Chrome** use Windows' store. Import for your user only:

   ```powershell
   Import-Certificate -FilePath "$env:USERPROFILE\Downloads\aetherspark-dev-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
   ```

   Windows asks "Do you want to install this certificate?" and shows the same fingerprint (as SHA-1 there). Answer **Yes**.
3. **Firefox** keeps its own store: Settings → Privacy & Security → Certificates → **View Certificates…** → **Authorities** → **Import…** → choose the file → tick **"Trust this CA to identify websites"** only → OK.
4. Delete the downloaded file; `just ca root` gives it again whenever needed.

**To remove the trust again** (after a re-init, or when the CA is retired):

```powershell
Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like '*AetherSpark Root CA (localhost)*' | Remove-Item
```

In Firefox: View Certificates → Authorities → "AetherSpark Root CA (localhost)" → **Delete or Distrust…**.

## Renew the intermediate (yearly, or when `ca status` warns)
In WSL2, with the stack up or down:

```bash
just ca renew-intermediate
just ca status
```

It opens the sealed root key in memory, signs a new intermediate for one more year and restarts step-ca if it runs. The root, and so the browsers' trust, stay the same. Certificates already issued stay valid until they expire (within a day) and are renewed under the new intermediate.

## When something is wrong
| Situation | What you see | What to do |
|---|---|---|
| The desktop was off overnight | Nothing: `just up` renews before it reports healthy | Nothing |
| The stack was down for more than a day | `just up` prints `cert-bootstrap: postgres-auth issued` | Nothing: expired certificates are replaced at `just up`; Traefik orders new ones at start |
| The intermediate has expired | `just up` stops at `cert-bootstrap` ("does not verify"); `just ca status` shows negative days | `just ca renew-intermediate`, then `just up` |
| A renewer is unhealthy | `docker compose ps` shows it unhealthy: its certificate has less than 25 % of its life left | `docker compose logs postgres-auth-renewer` (or `keycloak-renewer`); usually step-ca is down. `just up` again |
| postgres-auth serves an old certificate | Keycloak's connection fails with an expired certificate although the renewer is healthy | postgres-auth re-reads its files every hour; `docker compose logs postgres-auth` shows `configuration reloaded`, and any error loading the files. `just up` restarts it |
| The CA volume is damaged or the intermediate key or `step-ca-password` is lost | step-ca does not start | Desktop: re-init (below). Spark: restore the volume from the backup (WP3.4) |
| The root key is lost or stolen, or the dev key is lost | — | Re-init (below), and remove the old root from every browser |

## Re-init the desktop CA (throws the old CA away)
Development values are throw-away (secrets.md), so a new CA is cheap. In WSL2:

```bash
just down
docker volume rm aetherspark_step-ca aetherspark_postgres-auth-tls aetherspark_keycloak-tls
mv ~/.aetherspark/secrets/dev/step-ca-root-key.age ~/.aetherspark/secrets/dev/step-ca-root-key.age.old
just up
```

`just up` refuses to make a new CA while the old root key is still in the store, so that no root key is overwritten by accident. After the new CA is up: remove the old root from the browsers, trust the new one (above), then delete the `.old` file.

## Concepts
- **Root and intermediate:** the root is the top signer that clients trust. It signs one intermediate and then goes off-line (sealed in the store). The intermediate does the daily signing; if it is lost, the root makes a new one, and nobody has to change what they trust.
- **Name constraint:** a limit inside the CA's own certificates: "only names under `aether.localhost`". Clients that check it (all the stack's clients and the browsers, which test C11 verifies) refuse anything else from this CA.
- **Provisioner:** step-ca's word for "who may ask for which certificate". Traefik asks over ACME and proves the name by answering on it. Each internal service has a JWK provisioner whose certificate always carries that service's own name, whatever the request says.
- **Bootstrap and renewal:** the first certificate needs a password (the provisioner's), used once by `just up`'s bootstrap step. Afterwards the renewer proves itself with the current certificate and needs no password. An expired certificate cannot be renewed, which is why `just up` bootstraps again after a long stop.
- **Fingerprint:** a short hash of a certificate. Two files with the same fingerprint are the same certificate, which is how you check that you trust the right root.
