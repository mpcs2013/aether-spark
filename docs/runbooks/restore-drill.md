# Backup and restore drill
How AetherSpark's backup reaches the NAS, how its first snapshot is made, and how you prove, once now and then every quarter, that a lost desktop comes back from it. The design is [backup-job.md](../architecture/backup-job.md); the NAS side is the home infrastructure's (exchange A-012, A-015).

## Concepts
- **The repository** on the NAS holds AetherSpark's snapshots. The NAS accepts new ones and refuses to delete any (append-only). Old ones are thinned out by the NAS itself, never by AetherSpark.
- **Two keys open it.** The repository's original password is yours, in your password manager: the recovery key. AetherSpark's own key is generated and sealed with its other secrets: the job uses that one every day.
- **What a snapshot holds:** the certificate authority's volume, a dump of Keycloak's database, the sealed secrets store, and (later) the pinned Ostium packages. Everything else is rebuilt by `just up`.
- **Your off-line items.** If the desktop is lost, the NAS copy alone is not enough: the sealed store inside it opens only with the dev key. So you keep, away from the desktop:
  - the **dev key's off-line copy** (with the recovery sheet);
  - the **backup login** (name and password), the **repository's address and path**, and the **repository's original password**, in your password manager;
  - the **root CA's fingerprint**, written down, to check a restore against.
- **Once a day:** `just up` makes a snapshot if none was made that day. If the NAS does not answer, `just up` only prints a warning. If no snapshot arrives for 7 days, the NAS mails you.

## 1. Your off-line items (once)
1. **The root fingerprint.** In PowerShell, in the repository: `just ca status`. Copy the 64 characters after `root fingerprint` into an entry of your password manager.
2. **The dev key.** In PowerShell:
   ```powershell
   wsl -d Ubuntu-24.04 -- cat ~/.aetherspark/keys/dev.key
   ```
   It shows three lines; the last starts with `AGE-SECRET-KEY-`. Store all three lines in a secure note of your password manager, and add the `AGE-SECRET-KEY-…` line to the printed recovery sheet that is kept away from home. Then clear the terminal (`Clear-Host`). Never paste it into a chat, a ticket or a file in a repository.
3. **The backup login, address and path** come from the home infrastructure (exchange A-015); keep them in your password manager, as for every other login.

## 2. The first snapshots
**a. While the job is tried out (the scratch repository).** When the home infrastructure reports its scratch repository in A-015:
1. Enter its four values (each is asked twice and never shown):
   ```powershell
   just secrets enter backup-login-name
   just secrets enter backup-login-password
   just secrets enter backup-address
   just secrets enter backup-repository-path
   ```
   `backup-address` is `host:port`; `backup-repository-path` is the path in the address you were given, without a leading `/`.
2. `just up`, then `just backup init`: the scratch repository gets AetherSpark's key as its first.
3. `just backup` as often as needed; `just backup snapshots` lists them.

**b. The real one (the desktop-phase repository), once the trials are done:**
1. Enter the four real values the same way; each replaces the scratch value.
2. `just up` (it prints that the repository does not open yet: that is expected).
3. `just backup key-add`: restic asks for the repository's original password; type it from your password manager. AetherSpark's own key is added.
4. `just backup`: the first real snapshot.
5. `just backup prove-append-only`: expected `append-only: a delete of snapshot … is refused (HTTP 403)`.
6. `just backup keys`: the key marked `*` is the one this command opened the repository with, AetherSpark's own. Its ID goes into A-015 with the end of the build.

## 3. The drill (now for Gate G3, then every quarter)
A fresh WSL distribution stands for a new machine: it has nothing of AetherSpark but what you bring. It uses its own Compose project name, so it never touches the live stack's volumes.

**Before:** `just down` (the drill uses the same port 443).

1. **A fresh distribution** (PowerShell, as yourself):
   ```powershell
   wsl --install Ubuntu-24.04 --name aetherspark-drill
   ```
   Create its user, then in Docker Desktop → Settings → Resources → WSL integration, switch on `aetherspark-drill`.
2. **Tools and the repository**, in the new distribution (dev-setup.md, "One-time setup in WSL2"): `age`, `just`, Python 3, then clone the repository and `cd` into it.
3. **The dev key** from your password manager:
   ```bash
   install -d -m 0700 ~/.aetherspark/keys
   nano ~/.aetherspark/keys/dev.key      # paste the three lines, save
   chmod 0600 ~/.aetherspark/keys/dev.key
   ```
4. **The backup login** again: the four `just secrets enter` commands of step 2a, with the real values.
5. **The sealed store**, opened with the repository's original password (restic asks, twice):
   ```bash
   export COMPOSE_PROJECT_NAME=aetherspark-drill
   just backup restore-secrets
   ```
6. **The CA and Keycloak**, with your written-down fingerprint:
   ```bash
   just backup restore --expect-root <the fingerprint from your password manager>
   just up
   ```
7. **Sign in** at `https://whoami.aether.localhost` in Firefox with your everyday user and your security key. The browser trusts the page without any new step: it is the same root.
8. **Record** the date, the snapshot it took, and the result in the table below; then tidy up:
   ```bash
   just down
   docker volume ls -q --filter name=aetherspark-drill_ | xargs -r docker volume rm
   ```
   and in PowerShell `wsl --unregister aetherspark-drill`. Then `just up` in your normal distribution.

**If a step fails, stop and keep the state**: nothing of the live stack is touched by the drill.

**After an incident** (the desktop or a backup key possibly compromised): never take the newest snapshot blindly. `just backup snapshots` lists them; choose one from before the incident, check with the home infrastructure when the NAS received it, and give it to `restore --snapshot <id>`. Then follow backup-job.md, Residual risks: a new repository and login, new realm keys and client secrets, new app codes.

## Record
| Date | Kind | Snapshot | Result |
|---|---|---|---|
| | first drill (Gate G3) | | |
