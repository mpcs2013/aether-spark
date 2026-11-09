# Keycloak administration — runbook (WP3.1)

How the owner becomes Keycloak's administrator on the desktop, how the `aetherspark` realm and its users are managed, and the way back in when the administrator account is lost. The design is in [compose-base.md](../architecture/compose-base.md) ("Keycloak exposure", "Recovery and lock-out"); the certificate steps are in [certificates.md](certificates.md).

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

1. **Trust the CA's root in your browser** (once per CA): [certificates.md](certificates.md), "Trust the root in your browsers". Without it, the browser warns at the admin page. Firefox keeps its own store, so do that section's Firefox step too. To check: `https://auth.aether.localhost/realms/aetherspark/.well-known/openid-configuration` shows plain JSON with no warning.
2. **Harden the realm and create your account.** In PowerShell, in the repository (`<username>` is your chosen name):

   ```powershell
   just keycloak setup <username>
   ```

   It asks twice for a **temporary password** (at least 14 characters; nothing is shown as you type). You use it only once, in step 3. It ends with `master realm hardened … your administrator and the config service account are created; the aetherspark realm is applied.` It works only once: afterwards it can no longer sign in, and says so.

   Neither the command nor `just` repeats your username in its output. Still, before you paste any output elsewhere, check that it does not show the name.
3. **Sign in, set your password, register the key.** Open `https://auth-admin.aether.localhost/admin/master/console/`:
   1. sign in with `<username>` and the temporary password;
   2. **Update password:** choose your real password (14+ characters), and save it in your password manager;
   3. **Passkey Registration:** click **Register**. The browser hands over to a **Windows Security** window: choose **Security key**. If your key has a security-key PIN, Windows asks for it; then touch YubiKey 1 when it blinks. Give it a label such as "YubiKey 1". Labels must differ between one user's keys.

      Both realms accept only external security keys. Before that rule, on 2026-11-09, Windows saved the passkey in **Windows Hello** on the PC instead of the YubiKey: sign-in then worked with the key unplugged. To check: the sign-in page lists your key with **"USB, NFC"**, not "Windows Hello". If you are unsure, unplug the key and sign in: it must get stuck at the passkey step.

   The console opens.
4. **Delete the temporary administrator.** In the console: **Users** → `bootstrap-admin` → **Delete**. Keycloak marks it as temporary until then.
5. **Check:** sign out (top right), sign in again: username, password, then Windows Security asks for the key's PIN (if it has one) and a touch of the YubiKey. Keep the PIN: then someone would need your password, the key itself and the key's PIN.

Run on the desktop on 2026-11-08 (Firefox, YubiKey 1 with its PIN): all five steps as written.

**YubiKey 2**, when it is at home, for each of your accounts:
1. Open the account's own page: for the administrator `https://auth-admin.aether.localhost/realms/master/account/`, for your everyday user `https://auth.aether.localhost/realms/aetherspark/account/`.
2. Sign in, then **Account security** → **Signing in**, and set up another passkey (security key).
3. Choose **Security key**, PIN, touch YubiKey 2, label it "YubiKey 2".

With two keys registered, losing one does not lock you out.

## The realm as code, and everyday users
The `aetherspark` realm (the people who use AetherSpark) is described in the repository: `deploy/keycloak/aetherspark-realm.json` and `deploy/keycloak/admin.py`. Keycloak loads the realm file only into an empty database, so a change reaches a running Keycloak through **`just keycloak apply`**, which `just up` runs by itself after every start. It signs in as the **config service account** `aetherspark-config`, which `setup` creates. That account may change the `aetherspark` realm and nothing on the administrators' side, so its secret alone can never give anyone admin rights.

**Users sign in with a password and a second factor**, whichever they set up: an **app code** (an authenticator app on the phone) or a **security key**. Every new user must set one up at the first sign-in, and nobody can delete their own.

**Add a user** (for example your own everyday user, different from your administrator; the name stays local, like the administrator's):

```powershell
just keycloak user add <name>
```

It asks twice for a temporary password, used once. Then open `https://auth.aether.localhost/realms/aetherspark/account/` in the browser and sign in with it:
- **an app code** (the default): Keycloak shows a QR code; scan it with your authenticator app, type the 6-digit code it shows, then choose your real password;
- **a security key instead**: create the user with `just keycloak user add <name> --key`; at the first sign-in you register the YubiKey, as for the administrator.

At every later sign-in: the password, then the code from the app (or the key's touch).

The account page opens on **Personal info**. Email and names are optional, so there is nothing to fill in; AetherSpark sends no email.

Run on the desktop on 2026-11-09, after the fresh database (below): the administrator again (checked with the key unplugged: refused), then the owner's everyday user with `--key` (YubiKey 1; its key shown as "USB, NFC").

### Once, after PR 6a: a fresh Keycloak database on the desktop
The config service account is created only by `setup`, and on the desktop `setup` ran before it existed. Development data is throw-away, so the simplest way is to start Keycloak's database afresh and set it up again (about 15 minutes, YubiKey 1 at hand):

```powershell
just down
```
```powershell
wsl -d Ubuntu-24.04 -- docker volume rm aetherspark_postgres-auth-data
```
```powershell
just up
```

Then "First time" above, steps 2 to 5: `just keycloak setup <username>` (the same name or a new one), the first sign-in with the YubiKey, and deleting `bootstrap-admin`. The CA, the certificates and the browser's trust stay as they are. The old YubiKey registration went with the old database, so you register the key again.

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
| A red ❗ next to the email in the user list | the administrator has no email address | Nothing: administrators need none, and the stack sends no email |
| Registering a key says "Device already exists with the same name" | that user already has a key with that label | Choose another label |
| Sign-in works with the YubiKey unplugged | the passkey was saved in Windows Hello (before the security-key-only rule) | Register the YubiKey again (choose **Security key**, a new label), then delete the Windows Hello entry under **Users** → the user → **Credentials** |
| `just up` ends with `the realm does not match the repository` | `apply` found a difference it does not fix by itself (for example a login flow changed in the console) | Read the message above it. Undo the change in the console, or, with development data, start the database afresh (above) |
| `just keycloak apply` says `apply skipped: no config service account yet` | `setup` has not run on this database | Run `just keycloak setup <username>` |
| An administrator deleted a user's app code or key in the console | That user now signs in with the password alone: the second step applies to users who have one, and only new users get it as a default | At once, same page: **Users** → the user → **Required user actions**: add **Configure OTP** (or **Webauthn Register**), so the next sign-in sets one up again |

## Concepts
- **Realm:** Keycloak's word for a set of users, apps and rules. `master` holds the administrators; `aetherspark` holds the people who use AetherSpark. They never mix.
- **Security key (WebAuthn, "passkey"):** the browser asks the YubiKey to sign a challenge for exactly this site's name. A fake site gets nothing usable, so it cannot be phished like a password or a code.
- **Direct grant:** a program sending a username and password straight to Keycloak for a token, with no browser and no second factor. Switched off for administrators, so a stolen password alone is worthless.
- **Break-glass:** a sealed emergency way in, used only when the normal one is lost; afterwards everything goes back to normal and the emergency account is deleted.
