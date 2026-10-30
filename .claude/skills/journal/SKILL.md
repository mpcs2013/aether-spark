---
name: journal
description: Capture a notable development moment for the "My AI Journey" LinkedIn series. Only runs when the user types /journal.
disable-model-invocation: true
argument-hint: "[what happened]"
---

# /journal — capture a moment for "My AI Journey"

Core file: identical in every repo. Project differences live only in `.claude/journal.local.json`.

## Setup (read first)
Read `.claude/journal.local.json` in the repository root (git-ignored; it holds a local path):
```json
{ "project": "<project id>", "prefix": "<ID prefix>", "inbox": "~/<path to your journal folder>/inbox/<project id>.md" }
```
`~` in a path means the user's home folder; expand it to the absolute path before using the Read or Edit tool.
If the file is missing or any field is empty, stop and reply:
"/journal is not set up for this repo: .claude/journal.local.json is missing." Write nothing.

`.claude/settings.local.json` (also git-ignored) must ask before every edit in the journal folder.
Use the home-relative form so the file works in any user session:
```json
{ "permissions": { "ask": ["Edit(~/<path to your journal folder>/**)"] } }
```

Append exactly ONE entry to the `inbox` file. If the inbox file does not exist, stop and reply:
"Inbox not found: <path>. Register the project in the journal folder's project list first." Never create it.

## Rules
- Append only. Never edit, reorder or delete existing entries.
- Write only the `inbox` file named in `.claude/journal.local.json`. Never write to any other file or folder,
  including other projects' inboxes or any other file in the journal folder.
- Never run git commands that modify anything (no add, commit, stash, checkout, reset, push).
- Read-only git commands allowed: `git rev-parse --abbrev-ref HEAD`, `git rev-parse --short HEAD`, `git log -5 --oneline`.

## How to write (mandatory)
- Read the inbox with the Read tool. Append with the **Edit tool** only:
  `old_string` = everything from the last entry's `- ID:` line to the end of the file
  (for an inbox without entries: the header's final `---` line); `new_string` = the same text
  followed by the new entry.
- Never use Bash, PowerShell or any shell command or script to read or write the inbox or anything
  in the journal folder (no `>>`, `Add-Content`, `Out-File`, `tee`, `python -c`, …).
  The ask permission rule covers the Edit tool only; a shell write would skip the user's approval.
- If the Edit is rejected or fails, stop and report it. Do not retry another way.

## Entry ID
`<prefix>-<YYYYMMDD>-<NN>`: NN = 1 + the number of entries in the inbox whose ID starts with
`<prefix>-<YYYYMMDD>-`, two digits (`01`, `02`, …).

## Entry format
```
## <YYYY-MM-DD HH:mm> — <short title, max 8 words>
- ID: <prefix>-<YYYYMMDD>-<NN>
- Project: <project>
- Note: <$ARGUMENTS verbatim>
- Context: <3–5 lines from THIS session: what was attempted, what the agents/Claude did,
  the outcome, why it was surprising or useful>
- Lesson: <one line — the transferable insight, if any>
- Refs: branch <name>, HEAD <short-sha>, <related ADR ids / file paths>
- Tags: <2–4 of: agents, skills, sdlc, security, observability, architecture, testing, tooling, failure, win, community>
- Status: new

---
```
If the moment involves another project, name it in Context by its project id only, never by
anything more specific.

## Behavior
- If `$ARGUMENTS` is non-empty: write the entry, then reply with one line: "Journaled: <ID> <title>".
- If `$ARGUMENTS` is empty: draft the entry from the most recent notable event in this session,
  show it, and write it ONLY after the user confirms.

## Redaction (mandatory)
No secrets, keys, tokens, env values, connection strings, hostnames, IPs or internal URLs.
No raw logs or raw tool output. No personal or financial data. Nothing about the user's employer
or any work project. Describe security topics at pattern level only.
