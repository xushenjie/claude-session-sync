# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository. `CLAUDE.md` is a symlink to this file.

## What this is

`claude-session-sync` (PyPI package `claude-session-sync-cli`) syncs local Claude Code CLI sessions into the Claude desktop app's Code tab sidebar. It does this by writing missing index files for every desktop account/organization; it never edits `.jsonl` transcripts, except that `--purge-archived` deletes archived ones after backing them up.

## Commands

```bash
uv run pytest                                   # all tests (temp dirs only; never touches real data)
uv run pytest tests/test_sync.py::test_name     # one test
uv run pytest -k alias                          # tests matching a keyword
uv run ruff check . && uv run ruff format --check .   # what CI runs; `ruff format .` to fix
python src/claude_session_sync/cli.py --help    # run from source as a single file
```

CI (`.github/workflows/ci.yml`) runs tests on Linux/macOS/Windows × Python 3.9 and 3.14. It also checks that the version headings in `CHANGELOG.md` and `CHANGELOG.zh-CN.md` match exactly.

## Hard constraints

- **Stdlib only, one file.** All code lives in `src/claude_session_sync/cli.py`. Users may `curl` that file and run it with the system Python, so don't add dependencies or split it into modules it imports.
- **Python 3.9 compatible.** That is macOS's `/usr/bin/python3`, so no `match`, no `X | Y` types at runtime, and so on.
- **Every user-facing string goes through `t(en, zh)`.** Tests assert on English by default.
- **Anything that writes or deletes must be undoable** through the journal (`--undo`). It also needs a test that applies the change and then undoes it.
- **Test data must be hand-written fakes** (`tests/fakes.py`). Never copy real `claude-code-sessions/` or `~/.claude/projects/` files. Docs and README samples must use made-up account/org IDs and emails.
- **Never expose personal data or secrets.**
  - Real emails, account/org IDs, local paths, session titles or prompt text must not appear in code, tests, docs, commit messages, PR text or screenshots. Use made-up values such as `1a2b3c4d` or `dan***@example.com`.
  - Any new place that prints an email must pass it through `mask_email()`/`mask_emails_in()`, since output gets pasted into issues.
  - From `~/.claude.json`, read only the `oauthAccount` fields `cli_login_account()` already uses: account ID, org ID, email, org name. Never read, print or store anything else from it, tokens above all.
  - `~/.claude-desktop-sync/accounts.json` holds emails. Keep writing it with mode `0o600`, and don't dump its contents in debug output or copy them into tests.
  - Releases publish through PyPI Trusted Publishing, so no token belongs in the repo or workflows.
- Conventional Commits. Add user-visible changes under `## [Unreleased]` in **both** `CHANGELOG.md` and `CHANGELOG.zh-CN.md`, line for line. Keep `README.md` and `README.zh-CN.md` in sync too.
- Changes to desktop index format detection (`is_archived()`, `load_markers()`, `build_entry()`) should note the desktop app version the format was observed in.
- Releasing: bump `__version__` in `cli.py` (hatch reads the version from there), roll both changelogs, then push a `vX.Y.Z` tag (see CONTRIBUTING.md).

## On-disk data the tool works with

- **Desktop index:** `<desktop root>/claude-code-sessions/<account-id>/<org-id>/` contains `local_<uuid>.json` index entries (`cliSessionId` points at a transcript), `archived-sessions.idx`, and `deleted_<sessionId>` markers.
  - Roots are `Claude` and `Claude-3p` under the platform's app-support directory (`candidate_desktop_roots()`).
  - A "row" or "account" in the code is usually one account/org directory.
- **Transcripts:** `~/.claude/projects/<project>/<sessionId>.jsonl` are shared by all accounts.
- **The tool's own directory:** `~/.claude-desktop-sync/` (`JOURNAL_DIR`) contains:
  - `journal-*.json` and `trash-*/` (undo data)
  - `config.toml` (saved defaults)
  - `exclude.txt`
  - `accounts.json`, the known-account book: aliases, CLI account email/org name read from `~/.claude.json`, and first/last-seen times of every account dir. It is written on every run, previews included. Gone accounts are recovered from this file and from journals.

## Architecture (the main flow in `run()`)

1. **Parse args, merging `config.toml` defaults.** Options given without a value are set to the `ASK` sentinel and resolved by interactive pickers (`resolve_asks`). `setup_terminal()` then sets the module globals `_COLOR`, `_UNICODE`, `_ICONS`, `_BOXES`.
2. **`load_context()` → `Context`.** Finds roots and account dirs, loads and updates the account book, and computes `missing` (known accounts whose dir is gone). Management commands (`--set-alias`, `--forget-account`, `--undo`, `--show-template`) exit here.
3. **`build_plan()` → `SyncReport`.** Reads every index and decides per org dir which CLI sessions are missing. Sessions are skipped if archived or deleted in *any* account, excluded, or still being written (within `RUNNING_GRACE_SEC`). It also picks a template entry (own, borrowed from another account, or none). Nothing is written at this step.
4. **`SyncReport` renders the preview.** There are two layouts sharing the same content methods:
   - Boxed: `boxed_*` methods built on `panel()`/`table()`.
   - Plain: `print_*` methods.

   The account section is a client → account → org tree (`account_tree()` / `account_lines()`) with a per-org "this run" action. `to_json()` gives the `--json` output.
5. **Interactive terminal: the hub menu.** Without `-a`, `hub_menu()` loops. Management actions rebuild the plan and re-preview. `decide()` handles the non-interactive path.
6. **`write_plan()`.** Requires the desktop app to be quit (`ensure_desktop_stopped`). It writes entries via `build_entry(template, session)` and optional purge (`execute_purge`), then records a journal (`write_journal`).

Terminal UI helpers:
- `sym(name)` maps through `SYMBOLS`/`EMOJI` for the icon themes.
- `style()` and its helpers (`ok`, `todo`, `muted`, …) handle color.
- `disp_width()` and `fit()` measure and truncate text by terminal display width (CJK/emoji are 2 columns, ANSI codes are 0).
- `select_one`/`select_many`/`edit_line` are raw-key pickers. `_UI = "simple"` switches them to numbered input.

## Testing patterns

- **Fixtures** (`tests/conftest.py`) redirect `JOURNAL_DIR` to a temp dir and force English. They also stub `desktop_running` and `cli_login_account`; set the latter yourself to simulate a signed-in CLI account.
- **`env` fixture** (`tests/fakes.py: Env`) builds fake projects and a desktop root:
  - `env.add_cli_session()`, `env.add_index()`, `env.org_dir()` create data.
  - `env.run(*args)` calls `cli.main` with `--desktop-root`/`--projects-dir` pointing at them.
- **Simulating keys:** use the `press(monkeypatch, "down", "enter", …)` helper, which sets `cli._keys`. Simple numbered-input mode uses `fake_terminal(monkeypatch, [...])` together with `cli._UI = "simple"`.
- **Layout tests** set `COLUMNS` and `--layout boxed`, then check `cli.disp_width(line)` of box lines against the panel width. `squash()` collapses whitespace for column-independent assertions.
