# Ostium's packages
How AetherSpark gets Ostium's private packages on the owner's Windows machine, and how a version is pinned and checked (ADR-0009 amendments 1 and 2, ADR-0010's developer credentials). CI needs nothing from you: its jobs use their own read-only token.

Today one package: `Ostium.Keycloak.Realm`, in the test project `tests/dotnet/AetherSpark.Realm.Tests` only (the realm file's checks and the realm guard).

## Concepts
- **The feed.** Ostium publishes its packages to a private GitHub Packages feed. `nuget.config` names it as the source `ostium` and sends every `Ostium.*` package there and nowhere else (source mapping), and every other package to nuget.org only. So nobody can slip a fake `Ostium.*` package in through nuget.org.
- **The feed token.** Reading the feed needs a GitHub token. Yours is a *classic* token (GitHub Packages accepts no fine-grained token) with one permission, `read:packages`, and an expiry. It is kept in Windows Credential Manager, not in a file. `just ostium-feed restore` puts it into the environment of one `dotnet restore` and removes it afterwards. It is never given to services, containers or dev agents.
- **The lock file.** `packages.lock.json` records each package's exact version and hash. CI and `just ci` restore in locked mode: a package whose hash differs fails the restore.
- **The pin and its check.** Changing an `Ostium.*` version is yours alone (agent-system D15). For each change you run Ostium's consumer check (exchange O-010 point 3; its call as in O-039). It proves that what was restored is what Ostium signed: the tag's and the checksums file's signatures with the key pinned in `.github/allowed-signers/ostium`, each hash in the lock files and in the restored packages, and each package's source commit. The PR records its `RESULT: PASS` before merge.
- **The expected fingerprint.** The check also compares the pinned key's fingerprint with a value you give it (since Ostium's `main` at `e005bc0`, exchange O-039). That value must come from outside this repository: if it came from the same place as the pinned key, a swapped key would bring its own matching value along. So you compare three independent places before every check (step 3).
- **After the first restore** the package sits in your NuGet folder (`%USERPROFILE%\.nuget\packages`), so `just ci` restores without the token until the version changes.

## 1. The feed token (once; again before it expires)
1. GitHub → your picture → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token** → **Generate new token (classic)**.
2. **Note:** `AetherSpark - Ostium feed (read)`. **Expiration:** 90 days. **Scopes:** tick only **`read:packages`**. **Generate token**, and copy it.
3. In PowerShell, in the repository:
   ```powershell
   just ostium-feed save
   ```
   Paste the token at the prompt (nothing is shown) and press Enter. Expected: `ostium-feed: saved in Windows Credential Manager as aetherspark/ostium-feed`.
4. Do not keep the token anywhere else. When it expires, create a new one and run step 3 again (it replaces the old one). `just ostium-feed forget` removes it.

## 2. Pin a version (a first package or a version change)
1. In `Directory.Packages.props`, add the line or change its version, in square brackets so that it is exact (without them NuGet reads a minimum), for example:
   ```xml
   <PackageVersion Include="Ostium.Keycloak.Realm" Version="[0.5.0]" />
   ```
2. Restore with the token, writing the lock files, then once more in locked mode:
   ```powershell
   just ostium-feed restore -Pin
   just ostium-feed restore
   ```
   Expected: `ostium-feed: restore done (--force-evaluate)`, then `ostium-feed: restore done (--locked-mode)`. `git status` shows the changed `packages.lock.json` of each project that uses the package.
3. `just ci` passes, and the change is committed on your own branch. The check reads the signer file and the lock files from the committed `HEAD`.

## 3. The consumer check
In PowerShell. The commands assume your Ostium clone next to this one; change `$ostium` if yours is elsewhere.
1. **Your Ostium clone:** clean, on `main`, with its tags. The first command must print nothing:
   ```powershell
   $ostium = "$HOME\source\repos\dev\ostium"
   git -C $ostium status --short
   git -C $ostium switch main
   git -C $ostium pull --ff-only
   git -C $ostium fetch origin --tags
   ```
2. **The expected fingerprint, from three places.** Never from this repository, the PR under check or its description. Compare:
   - the table "Expected signer fingerprint" in Ostium's release runbook, read in the clone you just updated;
   - your own comment on the release's issue in Ostium's repository: it counts only if your account posted it and it was never edited;
   - your own record of the key.

   If all three are the same, type it into the session (`SHA256:` and 43 more characters):
   ```powershell
   $fp = Read-Host 'Expected signer fingerprint'
   ```
   If any of them differs: stop. Treat it as a suspected compromise and tell Ostium in the exchange.
3. **A locked restore of this clone,** at the commit to check; `git log` must show it:
   ```powershell
   cd "$HOME\source\repos\dev\aether-spark"
   git log --oneline -1
   dotnet restore --locked-mode
   ```
4. **The check,** with the new version (here `0.5.0`):
   ```powershell
   python "$ostium\tools\release\checksums.py" check --ostium $ostium --version 0.5.0 --consumer . --allowed-signers .github/allowed-signers/ostium --expect-fingerprint $fp --packages "$env:USERPROFILE\.nuget\packages"
   ```
   - Exit 0 is `RESULT: PASS`, exit 1 is `RESULT: FAIL`.
   - Exit 2 means a missing or malformed option, or that `git` or `ssh-keygen` is not found. For the latter, add `--ssh-keygen "C:\Program Files\Git\usr\bin\ssh-keygen.exe"` (Git for Windows' copy).
   - `FAIL signer file: signer fingerprint does not match --expect-fingerprint` means the key pinned here is not the one you compared. Nothing else was checked. Stop, never re-pin the key from the PR under check, and tell Ostium.
5. **Record it:** paste the whole printed block into the PR. Merge only on `RESULT: PASS`. On `RESULT: FAIL`, do not merge, and tell Ostium in the exchange.

Run it only on your own branch or a Dependabot PR: the restore runs the branch's build code on the machine that holds Ostium's release key. A version change in any other PR is redone on your own branch first.

## What is not here yet
- The local folder feed of the pinned packages, kept with the backup so a rebuild works without the private feed (ADR-0009 amendment 1; WP3.5, with the workstation backup).
- The token for the production builds on the Spark, passed as a build secret (WP6.4).
