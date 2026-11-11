# Changelog

English | [简体中文](https://github.com/xushenjie/claude-session-sync/blob/main/CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xushenjie/claude-session-sync/releases/tag/v0.1.0
