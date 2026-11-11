# Contributing

Thanks for helping out! Issues and pull requests in English or Chinese are both welcome.

## Development setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/xushenjie/claude-session-sync && cd claude-session-sync
uv run pytest                    # tests run in temp dirs and never touch your real data
uv run ruff check .              # lint
uv run ruff format .             # format
```

The `dev` dependency group in `pyproject.toml` pins the ruff version used by CI.

## Ground rules

- **The tool must stay stdlib-only and work as a single file.** README tells users they can `curl` `cli.py` and run it with the system Python, so don't add dependencies or split `cli.py` into modules that it imports.
- **Keep Python 3.9 compatible** — it's the `python3` that ships with macOS.
- **User-facing messages go through `t(en, zh)`** so both languages stay in sync. Tests assert on English output by default.
- **Test data must be hand-written fakes.** Never copy files from your own `claude-code-sessions/` or `~/.claude/projects/`; they contain your paths, prompts, and account IDs.
- **Anything that writes or deletes must be undoable** via the journal (`--undo`), and must be covered by a test that applies and then undoes it.
- Changes to the desktop index format detection (`is_archived()`, `load_markers()`, `build_entry()`) should mention the desktop app version you observed the format in.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:` …
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for user-visible changes.
- Make sure `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` pass.

## Releasing (maintainers)

1. Bump `__version__` in `src/claude_session_sync/cli.py`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD`, add a fresh `## [Unreleased]`, and update the compare links at the bottom.
3. Commit, then tag and push:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

The `Release` workflow checks that the tag matches `__version__` and that the changelog has an entry, builds the package, publishes it to PyPI, and creates a GitHub Release with the changelog section as notes.

One-time setup before the first release: on PyPI, add a [Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) for this repository with workflow `release.yml` and environment `pypi`, and create an environment named `pypi` under the repository's Settings → Environments.
