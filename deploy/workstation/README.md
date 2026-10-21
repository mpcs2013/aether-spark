# Workstation jobs (owner-run)

Jobs the owner schedules on the desktop under their own account. A Claude session writes and tests them here on fixtures only and never runs them against a project folder (ADR-0013 amendments 5–6, threat T17).

## Usage-capture copy job — `capture_copy.py`
Copies each project's usage captures one way into Greenlight's intake folder (exchange G-004). Standard library only, Python 3.12 or later. Besides the file rules it checks each capture's content against G-004 format 1.0.0, so only issue numbers and token counts can reach Greenlight; free-text fields are capped more tightly than the schema.

| Argument | Value |
|---|---|
| `--folders` | Folder list: one line per project, `<project id><TAB><capture folder>`; `#` lines are comments |
| `--intake` | Greenlight's intake root (must exist; one subfolder per project id is created) |
| `--log` | Run log, appended as JSON lines: copied, skipped, refused, ignored, conflict, with hashes; no paths |

Exit codes: `0` done, `2` done with refused or ignored files (look at the log), `1` stopped on an error. The wrapper `run-capture-copy.ps1` adds `3` (hash mismatch) and `4` (interpreter or job file missing).

### Why the setup is elevated
A Claude session that runs Bash works under the same Windows account as the scheduled task. Anything that account can write, a session can change: a job file in a user folder, the hash in a task the user registered, or a `.pth` file in a per-user Python. So the job, its wrapper and the interpreter live where only Administrators can write, and the task is registered from an elevated shell. The account still only reads and runs them.

### Install or update (elevated PowerShell, outside every project clone)
1. **Job folder.** Create it under `C:\ProgramData\` and limit writes to Administrators and SYSTEM:
   `icacls '<job folder>' /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' '<owner account>:(OI)(CI)RX'`
2. **Interpreter.** An all-users Python 3.12+ under `C:\Program Files\` (the python.org installer with "Install for all users"). Not a per-user or uv-managed Python, and not a project's `.venv`.
3. **Files from GitHub, not from the local clone** (a session can change the clone's git configuration and commits). Note the merge commit of the reviewed pull request, then:
   - check that it is on `main`: `gh api repos/<owner>/aether-spark/compare/<merge commit>...main --jq .status` prints `ahead` or `identical`;
   - download `deploy/workstation/capture_copy.py` and `deploy/workstation/run-capture-copy.ps1` from `https://raw.githubusercontent.com/<owner>/aether-spark/<merge commit>/…` into the job folder;
   - record `Get-FileHash '<job folder>\capture_copy.py' -Algorithm SHA256` (not `git show | sha256sum`: PowerShell 5.1 re-encodes piped text).
4. **Wrapper and list.** Fill in the values in the wrapper's copy, including the hash from step 3. Put the folder list the task reads into the job folder (the local site files keep the source).
5. **Log folder.** A folder of the owner's own outside every project; the log holds no paths or content.
6. **Task.** Register it from the elevated shell with `Register-ScheduledTask`, running as the owner account with limited rights, daily next to the workstation backup, with catch-up after a missed start. Action: `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '<job folder>\run-capture-copy.ps1'`. A non-zero exit raises the same alert as the workstation backup's desktop job (local runbook).
7. **Refusal test, from an unelevated shell.** Each must fail: writing a file in the job folder, writing a file in the interpreter's `Lib` folder, and changing the task (`Set-ScheduledTask` or `schtasks /Change`). If one succeeds, the job is not protected (T17): fix it before the first run. Repeat after Windows updates.

A new version goes through steps 3, 4 and 7 again: review, merge, download, record the new hash in the wrapper.
