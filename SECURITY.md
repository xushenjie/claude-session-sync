# Security Policy

`claude-session-sync` reads and writes files in your Claude desktop app data directory and, with `--purge-archived`, moves Claude Code transcripts into a backup folder. Bugs that could delete, corrupt, or leak local data are treated as security issues.

## Supported versions

Only the latest release gets fixes.

## Reporting a vulnerability

**Please do not open a public issue.** Report it privately through
[GitHub Security Advisories](https://github.com/xushenjie/claude-session-sync/security/advisories/new).

Include the version (`claude-session-sync --version`), your OS, steps to reproduce, and what happened. **Redact** paths, project names, session titles, and never attach `.jsonl` transcript contents.

You can expect an initial response within 7 days. Reports in English or Chinese are both welcome.

## Scope

In scope:

- Files written, moved, or deleted outside the documented locations
- `--undo` failing to restore what an `--apply` changed
- Sync or purge touching sessions it should skip (excluded, running, deleted)

Out of scope: the Claude desktop app and Claude Code themselves — report those to Anthropic.
