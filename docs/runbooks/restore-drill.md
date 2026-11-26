# Backup and restore drill
How AetherSpark's backup reaches the NAS, how its first snapshot is made, and how you prove, once now and then every quarter, that a lost desktop comes back from it. The design is [backup-job.md](../architecture/backup-job.md); the NAS side is the home infrastructure's (exchange A-012, A-015).

## Concepts
- **The repository** on the NAS holds AetherSpark's snapshots. The NAS accepts new ones and refuses to delete any (append-only). Old ones are thinned out by the NAS itself, never by AetherSpark.
- **Three keys open it.** The repository's original password is yours, in your password manager: the recovery key. AetherSpark's own key is generated and sealed with its other secrets: the job uses that one every day. The third is the NAS's maintenance key, which the home infrastructure uses for its own checks and for thinning out old snapshots.
- **A new key is a risk.** Whoever adds a key can read every snapshot. So each night (04:00) the NAS compares the repository's key list with a stored list of those three and mails you when a key was added or removed (A-015). It reports a change the next morning; it does not prevent one. The drill also compares the list by hand (section 3, step 7), in case the alert itself stops working.
- **What a snapshot holds:** the certificate authority's volume, a dump of Keycloak's database, the sealed secrets store, and the local folder feed of pinned Ostium packages. Everything else is rebuilt by `just up`.
- **Your off-line items.** If the desktop is lost, the NAS copy alone is not enough: the sealed store inside it opens only with the dev key. So you keep, away from the desktop:
  - the **dev key's off-line copy** (with the recovery sheet);
  - the **backup login** (name and password), the **repository's address and path**, and the **repository's original password**, in your password manager;
  - the **root CA's fingerprint**, written down, to check a restore against;
  - the **IDs of the repository's three keys**, in your password manager with the backup login, to compare the key list against.
- **Once a day:** `just up` makes a snapshot if none was made that day. If the NAS does not answer, `just up` only prints a warning. If no snapshot arrives for 7 days, the NAS mails you.

## 1. Your off-line items (once)
1. **The root fingerprint.** In PowerShell, in the repository: `just ca status`. Copy the 64 characters after `root fingerprint` into an entry of your password manager.
2. **The dev key.** In PowerShell:
   ```powershell
   wsl -d Ubuntu-24.04 -- sh -c 'cat "$HOME/.aetherspark/keys/dev.key"'
   ```
   (The single quotes keep PowerShell from turning `~` or `$HOME` into your Windows folder.)
   It shows three lines; the last starts with `AGE-SECRET-KEY-`. Store all three lines in a secure note of your password manager, and add the `AGE-SECRET-KEY-…` line to the printed recovery sheet that is kept away from home. Then clear the terminal (`Clear-Host`). Never paste it into a chat, a ticket or a file in a repository.
3. **The backup login, address and path** come from the home infrastructure (exchange A-015); keep them in your password manager, as for every other login.
4. **The key IDs**, once section 2b is done. In a WSL shell in the repository (section 2), with the stack's secrets unsealed (after `just up`):
   ```bash
   just backup keys
   ```
   It lists three keys, each with an 8-character ID; the one marked `*` is AetherSpark's own. Add the three IDs, with which is which, to the entry that holds the backup login. They are not secret, but they belong with the login, not in the repository: a key change would otherwise need a pull request.

## 2. The first snapshots
Everything here runs in a WSL shell in the repository, opened from PowerShell with:
```powershell
wsl -d Ubuntu-24.04 --cd "$HOME\source\repos\dev\aether-spark"
```
The sealed store and the dev key are in WSL; `just secrets` from PowerShell forwards there too.

**a. While the job is tried out (the scratch repository).** When the home infrastructure reports its scratch repository in A-015:
1. Enter its four values (each is asked twice and never shown):
   ```bash
   just secrets enter backup-login-name
   just secrets enter backup-login-password
   just secrets enter backup-address
   just secrets enter backup-repository-path
   ```
   `backup-address` is `host:port`, without `http://` and without a path; `backup-repository-path` is the path you were given, without a leading `/` (for the scratch repository: the login's name).
2. `just up` (it warns that the repository does not open yet: expected), then `just backup init`: the scratch repository gets AetherSpark's key as its first. Its output names the repository's ID; it never shows the password.
3. `just backup` as often as needed; `just backup snapshots` lists them; `just backup prove-append-only` checks that a delete is refused.

**b. The real one (the desktop-phase repository), once the trials are done:**
1. Enter the four real values the same way; each replaces the scratch value.
2. A new password for AetherSpark's key, so the real repository's key was never used in the scratch one:
   ```bash
   just secrets rotate backup-key-password
   ```
   From here on AetherSpark cannot open the scratch repository any more; the report of the build's end in A-015 (with step 7's key ID) lets the home infrastructure remove it.
3. `just up`. On a day without a snapshot yet it prints that the repository does not open yet: that is expected. On a day that already has one (from the scratch trial, say) it prints nothing about the backup.
4. `just backup key-add`: restic asks for the repository's original password; type it from your password manager. AetherSpark's own key is added.
5. `just backup`: the first real snapshot.
6. `just backup prove-append-only`: expected `append-only: a delete of snapshot … is refused (HTTP 403)`.
7. `just backup keys`: the key marked `*` is the one this command opened the repository with, AetherSpark's own. Its ID goes into A-015 with the end of the build.

## 3. The drill (now for Gate G3, then every quarter)
A fresh WSL distribution stands for a new machine: it has nothing of AetherSpark but what you bring. It uses its own Compose project name, so it never touches the live stack's volumes.

**Needs:** sections 1 and 2b done: your off-line items, and at least one snapshot in the real repository (`just backup snapshots` lists it). Without a snapshot there is nothing to restore, and step 5 fails.

**Before:** `just down` in your normal distribution. The drill uses the same port 443, and step 1 changes Docker Desktop's settings, which restarts Docker: a stack still up then would leave Docker-made folders behind (secrets.md, "When something is wrong").

1. **A fresh distribution** (PowerShell, as yourself; no administrator window needed):
   ```powershell
   wsl --install Ubuntu-24.04 --name aetherspark-drill
   ```
   Create its user when asked: any name, and a new password (`sudo` asks for it later). Then check, in the new distribution:
   ```bash
   id -u
   echo "$XDG_RUNTIME_DIR"
   ```
   Expected: `1000` and `/run/user/1000` (a trailing `/` does no harm; step 2 removes it). The stack runs as this number and restores files owned by it, and the in-memory folders live in the second. If either differs, stop: `wsl --unregister aetherspark-drill` and ask before trying again.

   Then switch on Docker for it: Docker Desktop → Settings → Resources → WSL integration → `aetherspark-drill` on, **Apply & restart**. Settings' new design may show only the default distribution there; **Go to classic Settings** (top right) shows the list. If the window closes during setup, `wsl --unregister aetherspark-drill` and start this step again.
2. **Docker access, the drill's settings, tools and the repository**, in the new distribution. Docker's socket is open only to the `docker` group, and a new distribution's user is not in it yet:
   ```bash
   sudo usermod -aG docker "$USER"
   exit
   ```
   Then in PowerShell `wsl --terminate aetherspark-drill` and `wsl -d aetherspark-drill`, so the group counts. In the distribution again:
   ```bash
   cd ~
   docker ps                              # an empty table, no "permission denied"
   cat >> ~/.bashrc <<'EOF'
   # AetherSpark restore drill: own Compose project, same runtime path as the normal distribution
   export XDG_RUNTIME_DIR=/run/user/1000
   export COMPOSE_PROJECT_NAME=aetherspark-drill
   EOF
   source ~/.bashrc
   sudo apt-get update && sudo apt-get install -y age just git python3
   git clone https://github.com/mpcs2013/aether-spark.git ~/aether-spark
   cd ~/aether-spark
   ```
   The project name sits in `~/.bashrc`, so every later window of this distribution uses the drill's own volumes, never the live ones. Start in `~`, not in the folder the window opened in: that is your Windows copy of the repository, which a new machine would not have.
3. **The dev key** from your password manager:
   ```bash
   install -d -m 0700 ~/.aetherspark/keys
   nano ~/.aetherspark/keys/dev.key      # paste the three lines, save
   chmod 0600 ~/.aetherspark/keys/dev.key
   age-keygen -y ~/.aetherspark/keys/dev.key
   clear
   ```
   `age-keygen -y` prints the key's public half (`age1…`): it must be the one in your secure note. An error means a line was cut or joined while pasting.
4. **The backup login** again: the four `just secrets enter` commands of step 2a, with the real values.
5. **The sealed store and the local package feed**, opened with the repository's original password. restic asks twice; its prompt shows as a blank line, so paste the password and press Enter each time:
   ```bash
   just backup restore-secrets
   ```
   Expected: `… sealed files restored into the store` and `1 packages restored into the local feed`. `ls ~/.aetherspark/secrets/dev/` then lists the same names as in your normal distribution.
6. **The CA and Keycloak**, with the fingerprint from your password manager. No password is asked: AetherSpark's own key is in the restored store.
   ```bash
   just backup restore --expect-root <the fingerprint from your password manager>
   just up
   ```
   Expected: `the root fingerprint matches the expected one`, every service healthy, `the aetherspark realm matches the repository`, and no `backup: snapshot` line: the restore counts as the day's snapshot, so the drill writes nothing into the repository.
7. **The key list**, compared by hand:
   ```bash
   just backup keys
   ```
   Expected: exactly three keys, with the three IDs from your password manager, and AetherSpark's marked `*`. A fourth key, or a missing or different ID, that you did not change yourself (see "Changing a key on purpose"): stop the drill and treat it as an incident (below). Write "key list: 3, as recorded" in the record.
8. **Sign in** at `https://whoami.aether.localhost` in a Firefox **private window** (Ctrl+Shift+P), with your everyday user and your security key. A private window holds no sign-in from your normal stack, so Keycloak must ask. The browser trusts the page without any new step: it is the same root. Paste nothing from the page: it shows your user name and a session cookie.
9. **Record** the date, the snapshot it took, the key list, and the result in the table below; then tidy up:
   ```bash
   just down
   docker volume ls -q --filter name=aetherspark-drill_       # only aetherspark-drill_… names
   docker volume ls -q --filter name=aetherspark-drill_ | xargs -r docker volume rm
   exit
   ```
   and in PowerShell `wsl --unregister aetherspark-drill`, which removes the drill's copy of the dev key and the store with it. Then `just up` in your normal distribution when you need the stack.

**If a step fails, stop and keep the state**: nothing of the live stack is touched by the drill.

**Changing a key on purpose** (a new `just backup key-add`, a key removed, a new recovery password; none is planned): the NAS's stored key list must follow before its next nightly check, or the check fails, mails you, and that night's cleanup is skipped. So **the same day**, tell the home infrastructure in the exchange (a new thread replying to A-015, which is closed) which key changed, with its ID from `just backup keys`. Then update the IDs in your password manager. A mail about the key list that does not match a change you announced is an incident.

**After an incident** (the desktop or a backup key possibly compromised, or a key list you did not change): never take the newest snapshot blindly. `just backup snapshots` lists them; choose one from before the incident, check with the home infrastructure when the NAS received it, and give it to `restore --snapshot <id>`. Then follow backup-job.md, Residual risks: a new repository and login, new realm keys and client secrets, new app codes.

## Record
| Date | Kind | Snapshot | Result |
|---|---|---|---|
| 2026-11-17 | first drill (Gate G3) | `3027c734` (2026-11-17) | **Passed.** Fresh distribution; the sealed store (the same 17 files as the desktop's) and the feed with the owner's key, the CA and Keycloak with AetherSpark's; root fingerprint as recorded; stack healthy; signed in with the security key. Found: the `docker` group, the hidden password prompt, the drill's own snapshot (all fixed in this runbook and the job) |
