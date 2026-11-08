# Keycloak administration — runbook (WP3.1)

How the owner becomes Keycloak's administrator on the desktop, and the way back in when that account is lost. The design is in [compose-base.md](../architecture/compose-base.md) ("Keycloak exposure", "Recovery and lock-out"); the certificate steps are in [certificates.md](certificates.md).

**In plain words.** Keycloak starts with a temporary administrator whose password only the machine knows. One command locks the administrators' side down and creates your own account. You then sign in once in the browser, choose your password and register your YubiKey, and delete the temporary account. From then on the admin console needs your password **and** a touch of the YubiKey. It answers only on `auth-admin.aether.localhost`, only from this machine. If you ever lose the account or both keys, the break-glass command below writes a new temporary administrator straight into Keycloak's database.

## What protects the admin side
| Protection | Where it is set |
|---|---|
| The admin console and the administrators' sign-in only on `auth-admin.aether.<place>`, only from the allowed source (on the desktop: this machine) | Traefik's routes; the master realm's own address (Frontend URL) |
| Password **and** security key (WebAuthn) for every administrator; a new one registers the key at the first sign-in | the master realm's login flow `browser-security-key` |
| Wrong passwords: after 5, waits of up to 15 minutes (never a permanent lock) | brute-force detection |
| No sign-in with a password alone, by any program (no "direct grants") | every client of the master realm |
| Sign-ins and admin changes are recorded (30 days) | login and admin events |

`just stack-test` checks every line of this table, partly by reading Keycloak's database directly.

## First time: become the administrator (desktop, about 30 minutes)
**Before:** the stack is up (`just up`), YubiKey 1 is at hand, and you know the username you want (it stays local: write it in your password manager, not in the repository).

1. **Trust the CA's root in your browser** (once per CA): [certificates.md](certificates.md), "Trust the root in your browsers". Without it, the browser warns at the admin page.
2. **Harden the realm and create your account.** In PowerShell, in the repository (`<username>` is your chosen name):

   ```powershell
   just keycloak setup <username>
   ```

   It asks twice for a **temporary password** (at least 14 characters; nothing is shown as you type). You use it only once, in step 3. It ends with `master realm hardened … administrator <username> created`. It works only once: afterwards it can no longer sign in, and says so.
3. **Sign in, set your password, register the key.** Open `https://auth-admin.aether.localhost/admin/master/console/`:
   1. sign in with `<username>` and the temporary password;
   2. **Update password:** choose your real password (14+ characters), and save it in your password manager;
   3. **Passkey Registration:** click **Register**, then touch YubiKey 1 when it blinks. Give it a label such as "YubiKey 1".

   The console opens.
4. **Delete the temporary administrator.** In the console: **Users** → `bootstrap-admin` → **Delete**. Keycloak marks it as temporary until then.
5. **Check:** sign out (top right), sign in again: password, then the YubiKey touch.

**YubiKey 2**, when it is at home: sign in, open your own account page (your name, top right), and under its signing-in or security section add a second security key; touch YubiKey 2 and label it "YubiKey 2". The exact menu names are confirmed and written in here at that session. With two keys registered, losing one does not lock you out.

## Break-glass: no administrator can sign in
For a lost account, a forgotten password with no key left, or both keys lost. It does not need the old account.

1. The stack is up (`just up`).
2. In PowerShell, in the repository, with a new name (not an existing user, for example `breakglass-<date>`):

   ```powershell
   just keycloak-break-glass breakglass-20261201
   ```

   Keycloak stops for about half a minute. Its `bootstrap-admin` command asks for the new account's password at the prompt (nothing is shown as you type), creates the account in its database, and Keycloak starts again whatever happens.
3. Open `https://auth-admin.aether.localhost/admin/master/console/` and sign in with the new name and that password. It must register a security key too: a YubiKey, or Windows Hello if no key is left.
4. In the console, repair what was lost (reset your own account's password or keys under **Users** → your user → **Credentials**), sign in with your own account again, then **delete the break-glass user**.

Keycloak shows the break-glass user as temporary. It is the only account that can be created without signing in, and only by someone who can run commands on this machine with the stack up.

## When something is wrong
| Situation | What you see | What to do |
|---|---|---|
| `just keycloak setup` says `password sign-in is already off` | setup has run before | Nothing: sign in in the browser. If no one can: break-glass |
| `just keycloak setup` says `the flow browser-security-key exists already` | an earlier setup stopped half-way | Development values are throw-away: `just down`, `docker volume rm aetherspark_postgres-auth-data`, `just up`, run setup again |
| The admin page says "Not found" | the address is not exactly `auth-admin.aether.localhost`, or the path is not `/admin/…` | Use the address above; the sign-in name `auth.aether.localhost` never serves the admin side |
| The admin page says "Forbidden" | the request does not come from the allowed source | On the desktop it must come from this machine. On the Spark: `AETHERSPARK_ADMIN_SOURCE` (WP6.4) |
| The browser refuses the security key | the page is not trusted (certificate warning) | Trust the root first (step 1) |
| Too many wrong passwords | sign-in fails even with the right password | Wait 15 minutes; brute-force detection lets you in again |

## Concepts
- **Realm:** Keycloak's word for a set of users, apps and rules. `master` holds the administrators; `aetherspark` holds the people who use AetherSpark. They never mix.
- **Security key (WebAuthn, "passkey"):** the browser asks the YubiKey to sign a challenge for exactly this site's name. A fake site gets nothing usable, so it cannot be phished like a password or a code.
- **Direct grant:** a program sending a username and password straight to Keycloak for a token, with no browser and no second factor. Switched off for administrators, so a stolen password alone is worthless.
- **Break-glass:** a sealed emergency way in, used only when the normal one is lost; afterwards everything goes back to normal and the emergency account is deleted.
