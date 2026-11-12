# claude-session-sync

**English** | [简体中文](https://github.com/xushenjie/claude-session-sync/blob/main/README.zh-CN.md)

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
uv tool install claude-session-sync-cli
```

Or with pipx:

```bash
pipx install claude-session-sync-cli
```

The PyPI package is named `claude-session-sync-cli`; the command it installs is `claude-session-sync`. To install the latest unreleased code instead, use `uv tool install git+https://github.com/xushenjie/claude-session-sync`.

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

> If it says the desktop app is running, the app is still alive in the menu bar or system tray and **nothing was written**. Quit it fully and run again (in a terminal it waits for you instead).

In a terminal you can skip step 2: the preview ends with a menu, and Enter writes. See [Output and interactive use](https://github.com/xushenjie/claude-session-sync#output-and-interactive-use).

Output is in English, or in Chinese when your locale (`LC_ALL` / `LC_MESSAGES` / `LANG`, or the Windows display language) is Chinese. Pass `--lang en` or `--lang zh` for a single run, or make it the default with `--lang zh --save-defaults` (see [Defaults](https://github.com/xushenjie/claude-session-sync#defaults-configtoml)) or `CLAUDE_SESSION_SYNC_LANG=zh` in your shell profile.

## Output and interactive use

Run it with no options in a terminal and everything else is a key press away: the preview ends with a menu, and options you leave without a value open a list to pick from. A preview looks like this (made-up data):

```text
╭─ claude-session-sync 0.4.0 ──────────────────────────────────────────────────────╮
│ CLI sessions 4  ·  accounts 3  ·  archived/deleted 1                             │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ Accounts ───────────────────────────────────────────────────────────────────────╮
│   │ Name                   │ ID                │ existing │ to add │ notes       │
├───┼────────────────────────┼───────────────────┼──────────┼────────┼─────────────┤
│ ● │ work                   │ 1a2b3c4d/9f8e7d6c │        2 │     +2 │             │
│ ● │ al***@example.com  CLI │ 5e6f7a8b/0c1d2e3f │        2 │     +1 │ −1 archived │
│ ● │ 5e6f7a8b/9f8e7d6c      │                   │        2 │     +1 │ −1 archived │
╰───┴────────────────────────┴───────────────────┴──────────┴────────┴─────────────╯
╭─ Sessions to add (2) ────────────────────────────────────────────────────────────╮
│ ▸ ~/work/docs                                                                    │
│   11-12 08:38  Draft release notes                                → work         │
│ ▸ ~/work/webapp                                                                  │
│   11-12 08:38  Fix login redirect                                 → all accounts │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ Archived sessions to purge (1) ─────────────────────────────────────────────────╮
│ −  Old experiment                       1 index file(s), 1 CLI file(s)  4d5e6f70 │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ Plan ───────────────────────────────────────────────────────────────────────────╮
│ + add 2 session(s) (4 index file(s) in 3 account(s))                             │
│ − delete 1 archived session(s)                                                   │
╰──────────────────────────────────────────────────────────────────────────────────╯

╭─ What next? ───────────────────────────────── ↑↓ move · Enter select · Esc back ─╮
│   Sync                                                                           │
│ ❯ 1 Write the plan above                                                         │
│   2 Pick accounts/sessions first, then write                                     │
│   3 Undo the most recent write                                                   │
│   Manage                                                                         │
│   f Only some accounts or a project                                              │
│   n Name accounts                                                                │
│   x Exclude list                                                                 │
│   s Defaults                                                                     │
│   q Quit without writing                                                         │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

- The header panel counts usable CLI sessions, desktop accounts, and sessions archived or deleted anywhere. In the account table a green `●` marks the current CLI login (its name is followed by `CLI`), a dim `●` the other accounts, and a yellow `⚠` an account with no template. The ID column gives the prefix for `--account`, and the **notes** column explains why counts differ: `−N archived` (archived or deleted in some account, so not added), `−N excluded`, `−N running`, `+N orphaned` (the index points to a transcript that no longer exists), `+N command-only/empty`.
- Sessions are grouped by working directory (`▸`) and listed once, with the accounts they go to (`→`). Up to 20 are shown; `-v` lists all of them, plus session IDs and extra statistics. `-q` prints only the summary and warnings.
- Panels fill the terminal up to 100 columns and shrink on narrow screens; long titles are cut with `…`. `--layout plain` (or `layout = "plain"` in `config.toml`) prints the same content without frames, which is also what pipes and files get by default. Terminals that can't show box-drawing characters, and `--icons ascii`, get ASCII frames.

### The menu

When both input and output are a terminal, the preview always ends with the **What next?** menu, even when everything is in sync (then Enter just quits):

| Key | Does |
|---|---|
| `1` / Enter | Write the plan above |
| `2` | Pick accounts and sessions first, then write |
| `3` | Undo the most recent write (shown when there is one) |
| `f` | Only some accounts or a project; the preview is rebuilt |
| `n` | Name accounts (see [Account names](https://github.com/xushenjie/claude-session-sync#account-names)) |
| `x` | Exclude list: untick sessions to sync them again |
| `s` | Defaults: language, colors, icons, layout, and the questions below, saved to `config.toml` |
| `q` / Esc | Quit without writing |

`-a` / `--apply` skips the menu and writes. Scripts, pipes, and CI never get a menu or a question; `--no-input` turns them off in a terminal too. If the desktop app is still running when you write, it waits for you to quit it and press Enter.

### Keys in lists

| Key | Does |
|---|---|
| `↑` `↓` (or `k` `j`) | Move |
| `←` `→` | Previous / next page; in the settings form, change the value |
| Space | Tick or untick (lists where you pick several) |
| `a` | Tick or untick everything shown |
| `/` | Filter by typing; Enter keeps the filter, Esc clears it |
| `1`–`9`, letters | Pick that item directly |
| Enter | Confirm; the highlighted item is the default |
| Esc / `q` | Go back without changing anything |

If the terminal can't read single key presses (for example `TERM=dumb`), or `ui = "simple"` is set in `config.toml`, every list becomes numbered: type a number (`1-5,8`, `!3` to leave one out, `none`) and press Enter.

### Options without a value

These options open a list when you leave out the value: `--lang`, `--color`, `--icons`, `--layout`, `--account`, `--project`, `--exclude`, `--exclude-file`, and `--set-alias`. For example, `claude-session-sync --account` lets you tick accounts, and `--set-alias` asks which account to name and pre-fills its current name. Outside a terminal they still need a value and exit with code 2.

### Questions

- **They come up only when they matter.** If sessions were written in the last 2 minutes, a terminal run lists them and asks whether to sync them too (default: no). If archived sessions can be purged, it offers to delete them when you write (default: keep). `--[no-]include-recent` and `--[no-]purge-archived`, or the same keys in `config.toml`, answer these in advance.
- `-i` / `--interactive` opens the account list (when more than one account has something to add) and the session list before writing. Sessions you untick can be remembered in `~/.claude-desktop-sync/exclude.txt`, which every later run reads automatically; the `x` menu entry (or deleting a line there) syncs them again.
- Purging only happens after you confirm it, and `--undo` shows what the last run did and asks first.
- Ctrl-C quits at any point without writing (exit code 130).

### Other output

- `--json` prints the plan (and, with `--apply`, what was written) as JSON for scripts. It never prompts and has no colors or frames.
- Colors carry meaning: green = current account, in sync or done; cyan = will be added; yellow = needs attention; red = will be deleted; dim = details. They are used only in a terminal; `NO_COLOR` is honored and `--color always|never` overrides.
- `--icons emoji` swaps the symbols for emoji (🟢 ⚪ ✅ 📥 ⚠️ 🧹 💡); `--icons ascii` uses plain ASCII. If the terminal can't show the symbols, ASCII is used automatically.
- When everything is in sync, dim `›` tips point out orphaned index entries, when the last write happened, and `--save-defaults` if you have no `config.toml` yet.

---

## Sync rules

| Case | What happens |
|---|---|
| The session is already in an account's list (archived or not) | Not added to that account again |
| The session is archived in **any** account (`isArchived` field or `archived-sessions.idx`) | Not added to any account. Use `--per-account-archive` to only consider each account's own archive state |
| The session was deleted in the desktop app in any account (`deleted_<session-id>` marker) | Not added to any account |
| The session is listed via `--exclude` or `--exclude-file` | Not added |
| The transcript was written within the last 2 minutes (probably still running) | Skipped; pass `--include-recent` to sync it anyway |
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
- sessions whose `.jsonl` was modified in the last 2 minutes are always skipped, even with `--include-recent`;
- in a terminal, deletion only happens after you type `yes`; anything else keeps the archived sessions and still runs the sync;
- backups are not cleaned up automatically; once you're happy, delete `~/.claude-desktop-sync/trash-*` yourself.

### Defaults (`config.toml`)

Options you'd otherwise type every time can live in `~/.claude-desktop-sync/config.toml`. Command-line options always win. The easiest way to edit it is `s` in the menu (or `--save-defaults` on its own): a form where `←` `→` change each value and Enter saves.

```toml
lang = "zh"                 # en | zh
color = "auto"              # auto | always | never
icons = "emoji"             # symbols | emoji | ascii
layout = "auto"             # auto | boxed | plain
ui = "keys"                 # keys (arrow-key lists) | simple (numbered input)
purge_archived = true       # leave out to be asked in a terminal
include_recent = false      # leave out to be asked in a terminal
per_account_archive = false
```

`--save-defaults` writes the current run's values of these options (except `ui`) to the file, for example `claude-session-sync --lang zh --no-include-recent --save-defaults`. Delete a line to go back to the built-in default. Language is picked from `--lang`, then `CLAUDE_SESSION_SYNC_LANG`, then `config.toml`, then the locale.

---

## Options

Run `claude-session-sync -h` for the same list, grouped the same way, with common commands at the top. A value in `[brackets]` can be left out in a terminal to pick it from a list (see [Options without a value](https://github.com/xushenjie/claude-session-sync#options-without-a-value)).

**Common**

| Option | Description |
|---|---|
| (no options) | Preview; in a terminal the [menu](https://github.com/xushenjie/claude-session-sync#the-menu) follows |
| `-a`, `--apply` | Write without showing the menu |
| `-i`, `--interactive` | Pick accounts and sessions from a list before writing (terminal only) |
| `-p`, `--[no-]purge-archived` | Permanently delete (or never delete) archived sessions after syncing (see above). Unset: offered in a terminal when there are any |
| `-r`, `--[no-]include-recent` | Also sync (or never sync) sessions written in the last 2 minutes (possibly still running). They are marked in the output; purging still skips them. Unset: asked in a terminal |
| `-u`, `--undo` | Undo the most recent write or deletion |
| `--lang [en\|zh]` | Output language; overrides `CLAUDE_SESSION_SYNC_LANG`, `config.toml`, and the locale |
| `--save-defaults` | Save this run's `--lang`, `--color`, `--icons`, `--layout`, and the `--[no-]…` options to `config.toml`, then continue |

**Accounts and filters**

| Option | Description |
|---|---|
| `--account [<account-id>]` | Only sync this account. A prefix such as the 8 characters in the output is enough; add `/<org-id-prefix>` to pick one organization. Repeatable |
| `--set-alias [<id>=<name>]` | Give an account a display name (see [Account names](https://github.com/xushenjie/claude-session-sync#account-names)); an empty name removes it. Repeatable; exits after saving |
| `--project [<string>]` | Only handle sessions whose working directory contains this string (sync and purge) |
| `--exclude [<session-id>]` | Exclude a session from sync and purge; repeatable |
| `--exclude-file [<file>]` | Read excluded session IDs from a file, one per line; text after `#` is a comment. `~/.claude-desktop-sync/exclude.txt` is always read as well |

**Output**

| Option | Description |
|---|---|
| `-q`, `--quiet` | Only print the summary and warnings |
| `-v`, `--verbose` | List every session, with session IDs and extra statistics |
| `--json` | Print the result as JSON; implies `--no-input` and no colors |
| `--no-input` | Never ask questions, even in a terminal |
| `--color [auto\|always\|never]` | Colored output; `auto` (default) only colors a terminal and honors `NO_COLOR` |
| `--icons [symbols\|emoji\|ascii]` | Status icons: `● ✓ ⚠` (default), emoji, or plain ASCII |
| `--layout [auto\|boxed\|plain]` | Boxed panels and tables, or plain text; `auto` (default) draws boxes only in a terminal |

**Advanced**

| Option | Description |
|---|---|
| `--[no-]per-account-archive` | Only exclude sessions archived in the same account |
| `--force` | Run `--apply` or `--undo` even while the desktop app is running (not recommended; the app may overwrite the changes) |
| `--projects-dir <path>` | CLI sessions directory; defaults to `~/.claude/projects` and honors `CLAUDE_CONFIG_DIR` |
| `--desktop-root <path>` | Path to a `claude-code-sessions` directory; repeatable |
| `--show-template` | Print each account's template fields and exit (for troubleshooting) |

**Other**

| Option | Description |
|---|---|
| `--completion zsh` | Print a zsh completion script and exit (see [Shell completion](https://github.com/xushenjie/claude-session-sync#shell-completion)) |
| `-h`, `--help` | Show help, common commands, and caveats |
| `-V`, `--version` | Show the version |

Examples:

```bash
# Sync only the myapp project into one account
claude-session-sync --account 1a2b3c4d-... --project myapp --apply

# Leave out sessions you don't want
claude-session-sync --exclude-file ~/no-sync.txt --apply
```

A session ID is the file name of a `.jsonl` under `~/.claude/projects/<project>/`. An account ID is a top-level directory name under `claude-code-sessions/`; the preview shows the first 8 characters, which `--account` and `--set-alias` accept as-is.

### Shell completion

zsh can complete options and their values (`--icons <Tab>` lists `symbols emoji ascii`, file options complete paths). Add this to `~/.zshrc`, after `compinit`:

```bash
eval "$(claude-session-sync --completion zsh)"
```

If you run it through an alias (for example straight from a checkout), turn on `complete_aliases` and point the alias at the same completion:

```bash
setopt complete_aliases
alias css='python3 ~/Projects/claude-session-sync/src/claude_session_sync/cli.py'
eval "$(css --completion zsh)"
compdef css=claude-session-sync
```

Without `complete_aliases`, zsh expands the alias first and completes `python3`'s arguments instead.

### Account names

The desktop app stores accounts by ID only, so the output shows names where it can find them:

```text
│ ● │ work · Acme Inc · dan***@example.com  CLI │ 1a2b3c4d/0c1d2e3f │
│ ● │ 3c4d5e6f/00000000                         │                   │
```

- **The account the CLI is signed in to** is recognized from `~/.claude.json` (only the account and organization IDs, email and organization name are read).
- **Accounts the CLI was signed in to before**: every `--apply` remembers the current CLI account in `~/.claude-desktop-sync/accounts.json`, so it keeps its name after you switch.
- **Any account**, with an alias you choose: press `n` in the menu, or run `claude-session-sync --set-alias` to pick the account from a list and edit its current name; `--set-alias 1a2b3c4d=work` does it in one go. Use `<account>/<org>` prefixes to name a single organization; an empty name removes the alias.

Emails are always shown masked (`dan***@example.com`), because this output often ends up in bug reports. Previews never write `accounts.json`.

---

## Undo and journal

Every `--apply` records a journal at `~/.claude-desktop-sync/journal-<timestamp>.json` (microsecond precision, so back-to-back runs never overwrite each other):

- `created`: files created by this run (index files, `deleted_` markers);
- `moved`: files moved to the backup folder, plus backups of `archived-sessions.idx` taken before it was rewritten.

`--undo` deletes the created files, moves backups back into place, and renames the journal to `.undone`. Run `--undo` repeatedly to step back through runs, newest first. In a terminal it first shows when the last run happened and how many files it touched, and asks before undoing.

Undo only touches files the tool itself changed, never the desktop app's existing sessions. Like `--apply`, `--undo` requires the desktop app to be fully quit.

---

## Upgrade and uninstall

Use the package name `claude-session-sync-cli` (not the command name) with your installer:

| Installed with | Upgrade | Uninstall |
|---|---|---|
| uv | `uv tool upgrade claude-session-sync-cli` | `uv tool uninstall claude-session-sync-cli` |
| pipx | `pipx upgrade claude-session-sync-cli` | `pipx uninstall claude-session-sync-cli` |
| single file | re-run the `curl` command from [Install](https://github.com/xushenjie/claude-session-sync#install) | delete `cli.py` |

Not sure which one you used? `uv tool list` or `pipx list` will show it.

- **Upgrading** replaces the old version in place; the command name stays the same. Running `uv tool install` again does not upgrade — use `upgrade`. Journals and backups in `~/.claude-desktop-sync/` are kept, so `--undo` still works across upgrades. Run `claude-session-sync --version` to confirm.
- **Uninstalling removes only the program.** Sessions it added to the desktop sidebar and everything in `~/.claude-desktop-sync/` stay. To revert those too, quit the desktop app and run `claude-session-sync --undo` repeatedly *before* uninstalling, until it reports nothing left to undo; then delete `~/.claude-desktop-sync/` if you no longer need the backups and saved account names.

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

Issues and pull requests are welcome, in English or Chinese. See [CONTRIBUTING.md](https://github.com/xushenjie/claude-session-sync/blob/main/CONTRIBUTING.md) for development setup, tests, commit conventions, and releasing. Please report anything that could lose or leak data privately, as described in [SECURITY.md](https://github.com/xushenjie/claude-session-sync/blob/main/SECURITY.md).

## License

[MIT](https://github.com/xushenjie/claude-session-sync/blob/main/LICENSE). Claude is a trademark of Anthropic. This project is not affiliated with Anthropic in any way.
