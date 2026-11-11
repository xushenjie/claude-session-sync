# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xushenjie/claude-session-sync/releases/tag/v0.1.0
