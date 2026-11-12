# Changelog

English | [简体中文](https://github.com/xushenjie/claude-session-sync/blob/main/CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-11-12

### Added
- `accounts.json` is now a book of every account/organization directory the tool has seen. An account whose directory is gone (signed out of the desktop app, or its data was cleared) stays in the list, dimmed as "directory gone" with when it was last seen, instead of silently disappearing. Upgrading recovers past accounts from the write journals.
- `--forget-account` and the menu key `g` remove a gone account from `accounts.json`; `--json` lists gone accounts under `missing_accounts`.
- A **this run** column says what happens to each organization: add N sessions, in sync, skipped (no template, or directory gone), or not processed because `--account` filtered it out. `--json` adds the same as `action` to each account.
- `--set-alias` and the menu key `n` can name a whole account or a single organization from a tree-shaped list.

### Changed
- Every run, previews included, records the account directories and the current CLI account in `accounts.json`. Nothing under the desktop app's data is written by a preview.
- The account table is a tree of client → account → organization, so several organizations of one account no longer look like separate accounts. Account rows show the alias and masked email; organization rows show the organization name, with personal organizations shown as "personal org" (or whose they are). The header counts accounts and organizations separately.
- Account pickers label each organization as `account › organization`.
- Boxed panels are separated by a blank line.
- Sessions get their own icon (`◇`, `💬` with `--icons emoji`); `⏳` now only marks running sessions.
- Session times always include the year (`2026-11-12 11:22`); sessions from earlier years keep their time of day.

## [0.4.0] - 2026-11-12

### Added
- Short options: `-a` (`--apply`), `-p` (`--purge-archived`), `-r` (`--include-recent`), `-u` (`--undo`).
- `--completion zsh` prints a zsh completion script for options and their values.
- `--icons symbols|emoji|ascii` (also `icons` in `config.toml`) picks the status icons.
- In a terminal, the preview always ends with a menu instead of "Write now? [y/N]": Enter writes, `2` picks accounts and sessions first, `3` undoes the most recent write, `f` narrows to some accounts or a project, `n` names accounts, `x` edits the exclude list, `s` edits defaults, `q` quits. `-a` skips it.
- Lists are picked with the keyboard: `↑` `↓` (or `j` `k`) move, Space ticks, `a` ticks everything, `/` filters, digits and letters jump, Enter confirms the highlighted default, Esc goes back. Terminals that can't read single keys, or `ui = "simple"` in `config.toml`, get numbered input instead.
- Options left without a value in a terminal open a list: `--lang`, `--color`, `--icons`, `--layout`, `--account`, `--project`, `--exclude`, `--exclude-file`, `--set-alias` (which pre-fills the current name). Outside a terminal they exit with code 2.
- `--layout auto|boxed|plain` (also `layout` in `config.toml`): output in framed panels and tables that fit the terminal width, or plain text. `auto` draws frames only in a terminal; terminals without box-drawing characters get ASCII frames.
- `--save-defaults` on its own opens a settings form in a terminal.
- Questions come up when they matter: a terminal run asks whether to sync sessions written in the last 2 minutes, and offers to purge archived sessions when you write. `--[no-]include-recent` and `--[no-]purge-archived` answer in advance.
- `~/.claude-desktop-sync/config.toml` holds defaults for `lang`, `color`, `icons`, `layout`, `ui`, `purge_archived`, `include_recent` and `per_account_archive`; `--save-defaults` saves the current run's values.

### Changed
- `--help` starts with five common commands and groups the options into Common, Accounts and filters, Output, Advanced, and Other; help text wraps by display width so Chinese lines fit the terminal.
- Clearer output: a one-line header with the key counts; an aligned account table with a green `●` on the current CLI login (dim `●` for the others, yellow `⚠` without a template), ID prefixes, and a notes column explaining why counts differ (archived, excluded, running, orphaned index entries, command-only sessions); sessions grouped by working directory with aligned titles; colors with fixed meanings (green done, cyan to add, yellow attention, red delete); a colored menu; a short summary after writing; and dim tips when everything is in sync.
- Pressing Enter after the preview now writes; type `q` or `n` to leave without writing.
- `-i` / `--interactive` uses the keyboard pickers instead of numbered lists.
- Ctrl-C quits cleanly with exit code 130 and restores the terminal.

### Fixed
- Sessions that start with a slash command (such as `/implement`) or an IDE notice are no longer skipped as empty. A session now counts when it has user text or a real model reply; sessions that only ran a local command such as `/model` are still skipped. Titles fall back to the session's AI title, then to the command and its arguments.

## [0.3.2] - 2026-11-11

### Added
- A Simplified Chinese changelog, `CHANGELOG.zh-CN.md`. CI checks that its version headings match the English one.

## [0.3.1] - 2026-11-11

### Fixed
- On Windows, working directories recorded with forward slashes are now shortened to `~` as well.

## [0.3.0] - 2026-11-11

### Added
- In a terminal, the preview ends with "Write now? [y/N]", so you can write without running again. While the desktop app is running it waits for you to quit it instead of exiting. Scripts, pipes, and CI are never asked; `--no-input` turns prompts off in a terminal too.
- `-i` / `--interactive`: pick accounts and sessions from numbered lists (`1-5,8`, `!3`, `none`). Sessions left out can be remembered in `~/.claude-desktop-sync/exclude.txt`, which is read on every run.
- `-q` / `--quiet`, `-v` / `--verbose`, `--json`, and `--color auto|always|never` (honors `NO_COLOR`).
- In a terminal, `--purge-archived` only deletes after you type `yes`, and `--undo` asks first.

### Changed
- The output lists each session once, with the accounts it goes to, instead of once per account; long lists are capped at 20 unless `-v` is given. Colors and symbols are used in terminals, with an ASCII fallback, and titles and paths are fitted to the terminal width.
- Exclude files accept `#` comments, and a missing `--exclude-file` is reported clearly.

## [0.2.0] - 2026-11-11

### Added
- Account names in the output: the account the CLI is signed in to is recognized from `~/.claude.json`, every `--apply` remembers it in `~/.claude-desktop-sync/accounts.json`, and `--set-alias <id>=<name>` names any account. Emails are always masked.
- `--account` accepts an ID prefix (such as the 8 characters shown in the output) and an optional `/<org-id-prefix>`; ambiguous prefixes are rejected.

## [0.1.3] - 2026-11-11

### Added
- `--lang en|zh` to pick the output language for a single run; it overrides `CLAUDE_SESSION_SYNC_LANG` and the locale.
- The English `--help` now shows a one-line hint in Chinese on how to switch, and the Chinese `--help` shows the English equivalent.

## [0.1.2] - 2026-11-11

### Fixed
- Links in the README (Chinese version, CONTRIBUTING, SECURITY, LICENSE) were relative and returned 404 on the PyPI project page; they now point to GitHub.

## [0.1.1] - 2026-11-11

### Added
- `--include-recent`: also sync sessions written in the last 2 minutes instead of skipping them. Such sessions are marked in the output, and `--purge-archived` still never touches them.

## [0.1.0] - 2026-11-11

### Added
- Sync CLI sessions into the desktop app sidebar, across multiple accounts and organizations.
- Honor the desktop app's archived state (`isArchived`, `archived-sessions.idx`) and deletion markers (`deleted_<id>`), so those sessions are never re-added.
- Borrow a template from another account when an account has no sessions yet.
- `--purge-archived`: permanently delete archived sessions after syncing, moving files to a backup folder first. Respects `--exclude`, `--exclude-file`, and `--project`, and skips sessions whose transcript was written in the last 2 minutes.
- `--undo`: revert the most recent write or deletion, including restoring backups.
- Refuse `--apply` and `--undo` while the desktop app is running unless `--force` is given. On Windows, where the desktop app and the Claude Code CLI are both `claude.exe`, they are told apart by install path and unknown processes only trigger a warning; detection on Linux is best effort.
- `--version` and `--help` with examples.
- English and Chinese output, chosen from the locale; override with `CLAUDE_SESSION_SYNC_LANG=en|zh`.

[Unreleased]: https://github.com/xushenjie/claude-session-sync/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xushenjie/claude-session-sync/releases/tag/v0.1.0
