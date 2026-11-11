## What & why

<!-- What does this change, and which issue does it fix? e.g. Fixes #12 -->

## How was it tested?

<!-- Tests added/updated, and any manual check against a real desktop app (which version / OS?) -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check . && uv run ruff format --check .` passes
- [ ] Anything that writes or deletes files can be reverted with `--undo`, with a test
- [ ] `CHANGELOG.md` updated under `[Unreleased]` (for user-visible changes)
- [ ] No real session data, paths, or account IDs in tests, logs, or screenshots
