# claude-session-sync

**English** | [简体中文](README.zh-CN.md)

[![CI](https://github.com/xushenjie/claude-session-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/xushenjie/claude-session-sync/actions/workflows/ci.yml)

Make your local Claude Code **CLI sessions** show up in the sidebar of the **Claude desktop app's Code tab**, across multiple accounts and organizations. Sessions you archived or deleted in the desktop app are left out automatically. It can also permanently purge archived sessions.

> ⚠️ **Unofficial tool, not affiliated with Anthropic.** The desktop app's index format is undocumented and may change with any update. The tool previews by default, refuses to write while the desktop app is running, and every write or deletion can be undone.

---

## Why

Local CLI and desktop sessions share the same transcripts, but **each side keeps its own session list**:

```
Desktop sidebar index (one per account/org)
  claude-code-sessions/<account-id>/<org-id>/
    ├─ local_<uuid>.json        session index; cliSessionId points to a transcript below
    ├─ archived-sessions.idx    archived sessions (local_ IDs)
    └─ deleted_<session-id>     marker left when a session is deleted in the desktop app
        │
        ▼
Transcripts (shared by CLI and desktop, account-independent)
  ~/.claude/projects/<project-dir>/<session-id>.jsonl
        ▲
        └─ `claude --resume` scans these directly
```

As a result:

- the CLI sees sessions created in the desktop app, but the desktop app doesn't see sessions created in the CLI;
- after switching account or organization, the desktop app reads a different index directory, so the list changes;
- the official import paths (`/desktop`, `claude --desktop --resume <id>`, desktop Help → Import CLI sessions) only import into the current account, and sometimes miss sessions.

This tool adds the missing index files for **every account/org** so CLI sessions appear in the sidebar. Syncing **never modifies any `.jsonl` transcript**; only `--purge-archived` removes transcripts of archived sessions, and it backs them up first.

---

## Install

Requires Python 3.9+ (the `python3` that ships with macOS works). Standard library only, no dependencies.

```bash
uv tool install git+https://github.com/xushenjie/claude-session-sync
```

Or with pipx:

```bash
pipx install git+https://github.com/xushenjie/claude-session-sync
```

Or skip installing and run the single file directly — replace `claude-session-sync` below with `python3 cli.py`:

```bash
curl -LO https://raw.githubusercontent.com/xushenjie/claude-session-sync/main/src/claude_session_sync/cli.py
```

On Windows, use `py` instead of `python3`.

You also need **at least one Code session created in the desktop app** (any account), which is used as a template. Accounts/orgs that have no sessions yet borrow a template from another account.

Desktop data directories (found automatically):

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| macOS (third-party deployment) | `~/Library/Application Support/Claude-3p/claude-code-sessions` |
| Windows | `%APPDATA%\Claude\claude-code-sessions` |
| Windows (third-party deployment) | `%APPDATA%\Claude-3p\…` or `%LOCALAPPDATA%\Claude-3p\…` |
| Windows (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| Linux | `~/.config/Claude/claude-code-sessions` |

If yours is elsewhere, pass it with `--desktop-root`.

---

## Quick start

```bash
# Full help
claude-session-sync --help

# 1. Preview: list which sessions each account would get, write nothing
claude-session-sync

# 2. Fully quit the desktop app (⌘Q on macOS; closing the window or "restart" is not enough), then write
claude-session-sync --apply

# 3. Reopen the desktop app. If anything looks wrong, undo the last run
claude-session-sync --undo
```

> If it says the desktop app is running, the app is still alive in the menu bar or system tray and **nothing was written**. Quit it fully and run again.

Output is in English, or in Chinese when your locale (`LC_ALL` / `LC_MESSAGES` / `LANG`, or the Windows display language) is Chinese. Set `CLAUDE_SESSION_SYNC_LANG=en` or `zh` to override.

---

## Sync rules

| Case | What happens |
|---|---|
| The session is already in an account's list (archived or not) | Not added to that account again |
| The session is archived in **any** account (`isArchived` field or `archived-sessions.idx`) | Not added to any account. Use `--per-account-archive` to only consider each account's own archive state |
| The session was deleted in the desktop app in any account (`deleted_<session-id>` marker) | Not added to any account |
| The session is listed via `--exclude` or `--exclude-file` | Not added |
| The transcript was written within the last 2 minutes (probably still running) | Skipped |
| Empty sessions (no user message) or transcripts without a working directory | Skipped |
| `.jsonl` files in subfolders of a project directory (subagents etc.) | Not scanned |

Each new index file is **cloned from an existing desktop session in the same account**: only the session ID, `cliSessionId`, working directory, title, and timestamps are replaced, so the rest matches your current desktop version as closely as possible. Accounts without sessions borrow a template from another account, preferring the same desktop data directory.

Session titles come from, in order: custom title → summary → first user message, truncated to 60 characters.

Running it again is safe; already-synced sessions are skipped.

---

## Permanently purge archived sessions (`--purge-archived`)

Syncs as usual, then permanently deletes **archived sessions across all accounts**, the same way deleting them in the desktop app would:

1. removes every `local_*.json` index pointing to the session, in all accounts;
2. removes it from `archived-sessions.idx`;
3. writes a `deleted_<session-id>` marker in the original account, so future syncs won't bring it back;
4. removes the CLI's `<session-id>.jsonl` and the folder of the same name (subagent transcripts etc.).

```bash
claude-session-sync --purge-archived           # preview what would be deleted
claude-session-sync --purge-archived --apply   # do it
```

- Nothing is erased outright: files are moved to `~/.claude-desktop-sync/trash-<timestamp>/`, and `--undo` restores everything;
- purging always covers **all accounts**, even with `--account`, so no account is left with an index pointing to a removed transcript;
- `--exclude`, `--exclude-file`, and `--project` apply to purging too — excluded sessions are never deleted;
- sessions whose `.jsonl` was modified in the last 2 minutes are skipped;
- backups are not cleaned up automatically; once you're happy, delete `~/.claude-desktop-sync/trash-*` yourself.

---

## Options

| Option | Description |
|---|---|
| `-h`, `--help` | Show help, examples, and caveats |
| `-V`, `--version` | Show the version |
| (no options) | Preview mode |
| `--apply` | Actually write |
| `--undo` | Undo the most recent write or deletion |
| `--show-template` | Print each account's template fields and exit (for troubleshooting) |
| `--account <account-id>` | Only sync this account; repeatable |
| `--project <string>` | Only handle sessions whose working directory contains this string (sync and purge) |
| `--exclude <session-id>` | Exclude a session from sync and purge; repeatable |
| `--exclude-file <file>` | Read excluded session IDs from a file, one per line |
| `--per-account-archive` | Only exclude sessions archived in the same account |
| `--purge-archived` | Permanently delete archived sessions after syncing (see above) |
| `--projects-dir <path>` | CLI sessions directory; defaults to `~/.claude/projects` and honors `CLAUDE_CONFIG_DIR` |
| `--desktop-root <path>` | Path to a `claude-code-sessions` directory; repeatable |
| `--force` | Run `--apply` or `--undo` even while the desktop app is running (not recommended; the app may overwrite the changes) |

Examples:

```bash
# Sync only the myapp project into one account
claude-session-sync --account 1a2b3c4d-... --project myapp --apply

# Leave out sessions you don't want
claude-session-sync --exclude-file ~/no-sync.txt --apply
```

A session ID is the file name of a `.jsonl` under `~/.claude/projects/<project>/`. An account ID is a top-level directory name under `claude-code-sessions/`; the preview shows the first 8 characters.

---

## Undo and journal

Every `--apply` records a journal at `~/.claude-desktop-sync/journal-<timestamp>.json` (microsecond precision, so back-to-back runs never overwrite each other):

- `created`: files created by this run (index files, `deleted_` markers);
- `moved`: files moved to the backup folder, plus backups of `archived-sessions.idx` taken before it was rewritten.

`--undo` deletes the created files, moves backups back into place, and renames the journal to `.undone`. Run `--undo` repeatedly to step back through runs, newest first.

Undo only touches files the tool itself changed, never the desktop app's existing sessions. Like `--apply`, `--undo` requires the desktop app to be fully quit.

---

## Known limitations

- **The index format is inferred from existing files.** Archived state is recognized from `isArchived`, `archived`, `archivedAt`, `status: "archived"`, and `archived-sessions.idx`; deletions from `deleted_<session-id>` markers. If a desktop update changes the format, `is_archived()` or `load_markers()` need updating.
- **Templates copy most fields from the original session as-is** (e.g. `envScopeId`, `postTurnSummary`). If a session misbehaves after borrowing a template from another account, run `--undo` first.
- **Windows and Linux support is experimental.** Development and real-data testing happen on macOS; on Windows and Linux only the automated tests have run, so the desktop data paths and index format there are unverified. Reports (with redacted `--show-template` output) are very welcome.
- **On Windows, the desktop app and a natively installed CLI are both `claude.exe`**, so the tool tells them apart by install path. If it can't tell, it prints a warning instead of refusing — make sure the desktop app is quit yourself.
- **Running-app detection on Linux is best effort.** There is no official Linux desktop app; the tool only looks for processes named `Claude` or `claude-desktop`. Make sure the app is closed yourself.
- **Cloud sessions are not handled.** They live on claude.ai, not in local files.
- **Don't type into the same session from the CLI and the desktop app at the same time**, or the transcript may get interleaved. Switching back and forth is fine.
- Claude Code keeps transcripts for 30 days by default. To keep them longer, set this in `~/.claude/settings.json`:
  ```json
  { "cleanupPeriodDays": 3650 }
  ```

---

## FAQ

**Sessions still don't show up in the desktop app?**
1. Check whether the output says the desktop app is running — if so, nothing was written;
2. make sure the app was fully quit before writing, and reopen it afterwards;
3. make sure the account/org you're signed into is in the output's account list, and its "to add" count isn't 0;
4. check whether the session landed in a different project group (e.g. a subdirectory or worktree).

**A deleted session came back?**
The tool honors the desktop app's `deleted_` markers, so this shouldn't happen. If you deleted the `.jsonl` directly or some other way, exclude it with `--exclude`.

**The desktop app errors when opening a session?**
Run `--undo` first, then [open an issue](https://github.com/xushenjie/claude-session-sync/issues/new/choose) with the output of `--show-template` (**redact paths and titles first**).

**Want to import a single session the official way?**
```bash
claude --desktop --resume <session-id>
```

---

## Contributing

Issues and pull requests are welcome, in English or Chinese. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, commit conventions, and releasing. Please report anything that could lose or leak data privately, as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). Claude is a trademark of Anthropic. This project is not affiliated with Anthropic in any way.
