import json

import pytest

from claude_session_sync import __version__, cli

from .fakes import ACCT, ORG, ORG_EMPTY


def squash(text):
    """把连续空白合成一个空格，断言时不受列对齐影响。"""
    return " ".join(text.split())


def cli_ids(entries):
    return sorted(e["cliSessionId"] for e in entries)


def test_preview_writes_nothing(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    before = sorted(p.name for p in env.desktop.rglob("*"))
    env.run()
    assert sorted(p.name for p in env.desktop.rglob("*")) == before
    assert not env.journal.exists()


def test_apply_adds_missing_sessions_from_template(env):
    env.add_index("existing")
    env.add_cli_session("existing")
    env.add_cli_session("new-1", cwd="/work/other", text="fix the bug")
    env.run("--apply")
    entries = {e["cliSessionId"]: e for e in env.index_entries()}
    assert set(entries) == {"existing", "new-1"}
    new = entries["new-1"]
    assert new["cwd"] == new["originCwd"] == "/work/other"
    assert new["title"] == "fix the bug"
    assert new["model"] == "test-model"  # 其他字段沿用模板
    assert new["sessionId"].startswith("local_")


def test_rerun_is_idempotent(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]


def test_skips_archived_and_deleted(env):
    env.add_index("existing")
    env.add_index("arch-flag", archived=True)
    env.add_index("arch-idx", local_id="local_x1")
    (env.org_dir() / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": ["local_x1"]}))
    (env.org_dir() / "deleted_gone").write_text("1")
    for sid in ("arch-flag", "arch-idx", "gone", "new-1"):
        env.add_cli_session(sid)
    # 另一个组织里没有这些会话，但归档/删除是全局排除的
    env.org_dir(ACCT, ORG_EMPTY)
    env.run("--apply")
    assert cli_ids(env.index_entries(ACCT, ORG_EMPTY)) == ["new-1"]


def test_skips_empty_and_running_sessions(env):
    env.add_index("existing")
    env.add_cli_session("cmd-only", text="<command-name>/model</command-name>")
    import time

    env.add_cli_session("running", mtime=time.time())
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing"]


def test_empty_account_borrows_template(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.org_dir(ACCT, ORG_EMPTY)
    env.run("--apply")
    assert cli_ids(env.index_entries(ACCT, ORG_EMPTY)) == ["new-1"]


def test_exclude(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.add_cli_session("new-2")
    env.run("--apply", "--exclude", "new-2")
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]


def test_undo_removes_created_files(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing"]


def test_purge_archived_and_undo(env):
    env.add_index("keep")
    arch = env.add_index("old", local_id="local_old")
    (env.org_dir() / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": ["local_old"]}))
    jsonl = env.add_cli_session("old")
    subdir = jsonl.parent / "old" / "subagents"
    subdir.mkdir(parents=True)
    # 另一个组织里也有指向它的索引，也要一并清掉
    other = env.add_index("old", local_id="local_old2", org=ORG_EMPTY)

    env.run("--purge-archived", "--apply")
    d = env.org_dir()
    assert not arch.exists() and not other.exists()
    assert not jsonl.exists() and not subdir.parent.exists()
    assert (d / "deleted_old").exists()
    assert json.loads((d / "archived-sessions.idx").read_text())["archived"] == []
    assert cli_ids(env.index_entries()) == ["keep"]

    cli.main(["--undo"])
    assert arch.exists() and other.exists() and jsonl.exists() and subdir.exists()
    assert not (d / "deleted_old").exists()
    assert json.loads((d / "archived-sessions.idx").read_text())["archived"] == ["local_old"]


def test_purge_preview_deletes_nothing(env):
    arch = env.add_index("old", archived=True)
    jsonl = env.add_cli_session("old")
    env.run("--purge-archived")
    assert arch.exists() and jsonl.exists()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "--purge-archived" in capsys.readouterr().out


# ---------------------------------------------------------------- 标题


def test_title_prefers_custom_title_then_summary(env):
    env.add_index("existing")
    env.add_cli_session(
        "titled",
        extra=[{"type": "summary", "summary": "the summary"}, {"type": "custom-title", "customTitle": "my title"}],
    )
    env.add_cli_session("summarized", extra=[{"type": "summary", "summary": "the summary"}])
    env.add_cli_session("plain", text="line one\nline two")
    env.run("--apply")
    titles = {e["cliSessionId"]: e["title"] for e in env.index_entries()}
    assert titles["titled"] == "my title"
    assert titles["summarized"] == "the summary"
    assert titles["plain"] == "line one line two"


def test_long_title_is_truncated(env):
    env.add_index("existing")
    env.add_cli_session("long", text="x" * 200)
    env.run("--apply")
    title = next(e["title"] for e in env.index_entries() if e["cliSessionId"] == "long")
    assert len(title) == cli.TITLE_MAX and title.endswith("…")


# ---------------------------------------------------------------- 账号与归档范围


def test_per_account_archive(env):
    env.add_index("existing")
    env.add_index("arch", archived=True)
    env.add_index("existing", org=ORG_EMPTY)
    env.add_cli_session("arch")
    env.run("--apply", "--per-account-archive")
    # 只在原组织归档，另一个组织照常补
    assert cli_ids(env.index_entries(ACCT, ORG_EMPTY)) == ["arch", "existing"]
    assert cli_ids(env.index_entries(ACCT, ORG)) == ["arch", "existing"]  # 原有的归档索引，没有重复添加


def test_account_filter(env):
    other = "cccccccc-0000-4000-8000-000000000001"
    env.add_index("existing")
    env.add_index("existing", acct=other)
    env.add_cli_session("new-1")
    env.run("--apply", "--account", other)
    assert cli_ids(env.index_entries(other, ORG)) == ["existing", "new-1"]
    assert cli_ids(env.index_entries(ACCT, ORG)) == ["existing"]


def test_project_filter(env):
    env.add_index("existing")
    env.add_cli_session("in-demo", cwd="/work/demo")
    env.add_cli_session("in-other", cwd="/work/other", project="-work-other")
    env.run("--apply", "--project", "demo")
    assert cli_ids(env.index_entries()) == ["existing", "in-demo"]


# ---------------------------------------------------------------- 桌面端运行检测


def test_apply_refuses_when_desktop_running(env, monkeypatch):
    monkeypatch.setattr(cli, "desktop_running", lambda: True)
    env.add_index("existing")
    env.add_cli_session("new-1")
    with pytest.raises(SystemExit) as e:
        env.run("--apply")
    assert "desktop app is running" in str(e.value.code)
    assert cli_ids(env.index_entries()) == ["existing"]
    env.run("--apply", "--force")
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]


def test_undo_refuses_when_desktop_running(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    monkeypatch.setattr(cli, "desktop_running", lambda: True)
    with pytest.raises(SystemExit):
        cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    cli.main(["--undo", "--force"])
    assert cli_ids(env.index_entries()) == ["existing"]


def test_linux_detection_matches_exact_process_names(monkeypatch):
    calls = []

    class Result:
        def __init__(self, code):
            self.returncode = code

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return Result(0 if cmd[-1] == "claude-desktop" else 1)

    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    # conftest 没有对这个测试打补丁，这里直接测真实实现
    assert cli.desktop_running() is True
    assert all(c[:2] == ["pgrep", "-x"] for c in calls)  # 不能用 -f，否则会把 CLI 误判为桌面端


# ---------------------------------------------------------------- 日志


def test_two_quick_applies_keep_separate_journals(env):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    env.add_cli_session("new-2")
    env.run("--apply")
    assert len(list(env.journal.glob("journal-*.json"))) == 2
    cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing"]


# ---------------------------------------------------------------- 清除的范围


def test_purge_respects_exclude(env):
    arch = env.add_index("old", archived=True)
    jsonl = env.add_cli_session("old")
    env.run("--purge-archived", "--apply", "--exclude", "old")
    assert arch.exists() and jsonl.exists()


def test_purge_respects_project(env):
    keep = env.add_index("other", archived=True, cwd="/work/other")
    keep_jsonl = env.add_cli_session("other", cwd="/work/other", project="-work-other")
    gone = env.add_index("old", archived=True)
    gone_jsonl = env.add_cli_session("old")
    env.run("--purge-archived", "--apply", "--project", "demo")
    assert keep.exists() and keep_jsonl.exists()
    assert not gone.exists() and not gone_jsonl.exists()


def test_purge_skips_recently_written_even_if_not_a_valid_session(env):
    # 只有命令、没有正常用户消息的会话不会出现在同步列表里，以前因此漏掉了"正在运行"判断
    import time

    arch = env.add_index("busy", archived=True)
    jsonl = env.add_cli_session("busy", text="<command-name>/model</command-name>", mtime=time.time())
    env.run("--purge-archived", "--apply")
    assert arch.exists() and jsonl.exists()


# ---------------------------------------------------------------- 输出语言


@pytest.mark.parametrize(
    ("env_vars", "expected"),
    [
        ({"LANG": "zh_CN.UTF-8"}, True),
        ({"LANG": "en_US.UTF-8"}, False),
        ({"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}, True),  # LC_ALL 优先
        ({"LANG": "zh_CN.UTF-8", "CLAUDE_SESSION_SYNC_LANG": "en"}, False),  # 显式指定优先
        ({"CLAUDE_SESSION_SYNC_LANG": "zh"}, True),
        ({}, False),  # 什么都没设时默认英文
    ],
)
def test_language_detection(monkeypatch, env_vars, expected):
    for key in ("CLAUDE_SESSION_SYNC_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    for key, val in env_vars.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    assert cli._detect_zh() is expected


def test_chinese_output(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_LANG_ZH", True)
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    out = capsys.readouterr().out
    assert "待补 1" in out and "预览模式" in out


def test_english_output(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    out = capsys.readouterr().out
    assert "to add 1" in out and "Preview only" in out
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in out.replace("…", ""))


def test_show_template(env, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.run("--show-template")
    out = capsys.readouterr().out
    assert '"cliSessionId": "existing"' in out
    assert "(no usable template)" in out


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/work/demo", True),
        ("C:\\work\\demo", True),
        ("\\\\server\\share\\demo", True),
        ("relative/demo", False),
        ("C:relative", False),
        ("", False),
        (None, False),
    ],
)
def test_is_abs_any(path, expected):
    assert cli.is_abs_any(path) is expected


def test_windows_style_cwd_is_accepted(env):
    env.add_index("existing")
    env.add_cli_session("win", cwd="C:\\work\\demo")
    env.run("--apply")
    entry = next(e for e in env.index_entries() if e["cliSessionId"] == "win")
    assert entry["cwd"] == "C:\\work\\demo"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([r"C:\Users\me\AppData\Local\AnthropicClaude\app-1.2.3\claude.exe"], True),
        ([r"C:\Program Files\WindowsApps\Claude_1.2.3.0_x64__abc\app\claude.exe"], True),
        ([r"C:\Users\me\.local\bin\claude.exe"], False),  # 原生安装的 CLI
        ([r"C:\Users\me\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\claude.exe"], False),
        ([r"C:\Users\me\.local\bin\claude.exe", r"C:\Users\me\AppData\Local\AnthropicClaude\claude.exe"], True),
        ([], False),
    ],
)
def test_windows_desktop_detection(paths, expected):
    assert cli.windows_desktop_running(paths) is expected


def test_windows_unknown_process_warns_but_does_not_block(capsys):
    assert cli.windows_desktop_running(["", r"D:\tools\claude.exe"]) is False
    assert "may or may not be the desktop app" in capsys.readouterr().err


# ---------------------------------------------------------------- --include-recent


def test_recent_session_skipped_with_hint(env, capsys):
    import time

    env.add_index("existing")
    env.add_cli_session("busy", mtime=time.time())
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing"]
    assert "--include-recent" in capsys.readouterr().out


def test_include_recent_syncs_and_warns(env, capsys):
    import time

    env.add_index("existing")
    env.add_cli_session("busy", mtime=time.time())
    env.add_cli_session("old")
    env.run("--apply", "--include-recent")
    assert cli_ids(env.index_entries()) == ["busy", "existing", "old"]
    out = capsys.readouterr().out
    assert out.count("[still being written]") == 1  # 只标注最近写入的那个
    assert "Included 1 session(s) still being written" in out
    assert "Skipped" not in out


def test_include_recent_does_not_affect_purge(env):
    import time

    arch = env.add_index("busy", archived=True)
    jsonl = env.add_cli_session("busy", mtime=time.time())
    env.run("--purge-archived", "--apply", "--include-recent")
    assert arch.exists() and jsonl.exists()


# ---------------------------------------------------------------- --lang


def test_lang_flag_switches_help_to_chinese(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_SESSION_SYNC_LANG", "en")  # --lang 优先于环境变量
    with pytest.raises(SystemExit):
        cli.main(["--lang", "zh", "--help"])
    out = capsys.readouterr().out
    assert "实际写入" in out and "English output: pass --lang en" in out


def test_english_help_shows_chinese_hint(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "actually write" in out
    assert "中文输出：加 --lang zh" in out


def test_lang_flag_switches_run_output(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--lang=zh")
    assert "预览模式" in capsys.readouterr().out


def test_invalid_lang_is_rejected(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--lang", "fr"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# ---------------------------------------------------------------- 账号名称、别名、前缀


CLI_ACCT = {"account": ACCT, "org": ORG, "email": "alice@example.com", "org_name": "alice@example.com's Organization"}


@pytest.mark.parametrize(
    ("email", "masked"),
    [("alice@example.com", "al***@example.com"), ("ab@x.io", "a***@x.io"), ("a@x.io", "a***@x.io")],
)
def test_mask_email(email, masked):
    assert cli.mask_email(email) == masked


def test_cli_login_account_reads_only_needed_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": ACCT,
                    "organizationUuid": ORG,
                    "emailAddress": "alice@example.com",
                    "organizationName": "Acme",
                    "billingType": "secret-ish",
                }
            }
        )
    )
    assert cli.cli_login_account() == {"account": ACCT, "org": ORG, "email": "alice@example.com", "org_name": "Acme"}


def test_heading_shows_masked_cli_account(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "cli_login_account", lambda: dict(CLI_ACCT))
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.run()
    out = capsys.readouterr().out
    assert "[al***@example.com · current CLI login] (aaaaaaaa…/bbbbbbbb…)" in out
    assert "alice@example.com" not in out  # 完整邮箱不能出现，组织名里的也不行
    assert "[aaaaaaaa…/bbbbbbbb…] existing 0" in squash(out)  # 另一个组织没有名称
    assert not env.journal.exists()  # 预览不写账号记录


def test_apply_remembers_cli_account(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "cli_login_account", lambda: dict(CLI_ACCT, org_name="Acme"))
    env.add_index("existing")
    env.run("--apply")
    saved = json.loads((env.journal / "accounts.json").read_text())
    assert saved["accounts"][f"{ACCT}/{ORG}"]["email"] == "alice@example.com"
    # 之后切换到别的 CLI 账号，仍然能显示记下来的名称
    monkeypatch.setattr(cli, "cli_login_account", lambda: None)
    capsys.readouterr()
    env.run()
    assert "[Acme · al***@example.com] (aaaaaaaa…/bbbbbbbb…)" in capsys.readouterr().out


def test_set_alias_and_remove(env, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.run("--set-alias", "aaaaaaaa=work")  # 只写账号前缀：对该账号的所有组织生效
    env.run("--set-alias", "aaaaaaaa/bbbbbbbb-0000-4000-8000-000000000002=side")  # 组织级别优先
    capsys.readouterr()
    env.run()
    out = capsys.readouterr().out
    assert "[work] (aaaaaaaa…/bbbbbbbb…) existing 1" in squash(out)
    assert "[side] (aaaaaaaa…/bbbbbbbb…) existing 0" in squash(out)
    env.run("--set-alias", "aaaaaaaa=")
    saved = json.loads((env.journal / "accounts.json").read_text())
    assert ACCT not in saved["aliases"] and f"{ACCT}/{ORG_EMPTY}" in saved["aliases"]


def test_set_alias_errors(env):
    env.add_index("existing")
    with pytest.raises(SystemExit) as e:
        env.run("--set-alias", "zzzz=x")
    assert "No account matches" in str(e.value.code)
    with pytest.raises(SystemExit) as e:
        env.run("--set-alias", "aaaaaaaa")
    assert "ID=NAME" in str(e.value.code)


def test_account_prefix_filter(env):
    other = "cccccccc-0000-4000-8000-000000000001"
    env.add_index("existing")
    env.add_index("existing", acct=other)
    env.add_cli_session("new-1")
    env.run("--apply", "--account", "cccccccc")
    assert cli_ids(env.index_entries(other, ORG)) == ["existing", "new-1"]
    assert cli_ids(env.index_entries(ACCT, ORG)) == ["existing"]


def test_ambiguous_account_prefix_is_rejected(env):
    env.add_index("existing")
    env.add_index("existing", acct="aaaaaaaa-1111-4000-8000-000000000001")
    with pytest.raises(SystemExit) as e:
        env.run("--account", "aaaaaaaa")
    assert "matches several accounts" in str(e.value.code)


# ---------------------------------------------------------------- 输出与交互


def fake_terminal(monkeypatch, answers):
    """假装在终端里运行，依次返回 answers 作为用户输入。"""
    it = iter(answers)
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_input", lambda prompt="": next(it))


def test_sessions_listed_once_across_accounts(env, capsys):
    env.add_index("existing")
    env.add_index("existing", org=ORG_EMPTY)
    env.add_cli_session("new-1", text="unique title here")
    env.run()
    out = capsys.readouterr().out
    assert out.count("unique title here") == 1
    assert "all accounts" in out
    assert "add 1 session(s) (2 index file(s) in 2 account(s))" in out


def test_long_list_is_capped_unless_verbose(env, capsys):
    env.add_index("existing")
    for i in range(cli.LIST_LIMIT + 3):
        env.add_cli_session(f"s-{i:02d}", text=f"task {i:02d}")
    env.run()
    assert "and 3 more (pass -v to list all)" in capsys.readouterr().out
    env.run("-v")
    out = capsys.readouterr().out
    assert "more (pass -v" not in out and "task 22" in out and "s-22" in out


def test_quiet_prints_only_summary(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1", text="secret title")
    env.run("-q")
    out = capsys.readouterr().out
    assert "secret title" not in out and "Accounts" not in out
    assert "add 1 session(s)" in out


def test_in_sync_message(env, capsys):
    env.add_index("existing")
    env.add_cli_session("existing")
    env.run()
    out = capsys.readouterr().out
    assert "Everything is in sync" in out and "Preview only" not in out


def test_json_output(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1", text="fix the bug")
    env.run("--json")
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "preview" and data["written"] is None
    assert [s["id"] for s in data["sessions"]] == ["new-1"]
    assert data["accounts"][0]["to_add"] == ["new-1"]
    env.run("--json", "--apply")
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "apply" and data["written"]["created"] == 1
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]


def test_json_never_prompts(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, [])  # 一旦提问就会 StopIteration
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--json")
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"


def test_no_color_when_not_a_terminal(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    assert "\033[" not in capsys.readouterr().out
    env.run("--color", "always")
    assert "\033[" in capsys.readouterr().out


def test_ascii_fallback_and_width_helpers(monkeypatch):
    monkeypatch.setattr(cli, "_UNICODE", False)
    assert cli.sym("ok") == "OK" and cli.sym("ell") == "..."
    monkeypatch.setattr(cli, "_UNICODE", True)
    assert cli.disp_width("中文ab") == 6
    assert cli.fit("中文中文中文", 7) == "中文中…"
    assert cli.disp_width(cli.fit("中文中文中文", 7)) <= 7
    assert cli.pad("中", 4) == "中  "


def test_short_path(monkeypatch):
    monkeypatch.setattr(cli, "HOME", cli.Path("/home/u"))
    assert cli.short_path("/home/u/work") == "~/work"
    assert cli.short_path("/home/user2") == "/home/user2"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", [0, 1, 2, 3, 4]),
        ("all", [0, 1, 2, 3, 4]),
        ("1-3", [0, 1, 2]),
        ("3-1, 5", [0, 1, 2, 4]),
        ("!2", [0, 2, 3, 4]),
        ("1-4 !2", [0, 2, 3]),
        ("none", []),
        ("6", None),
        ("0", None),
        ("abc", None),
    ],
)
def test_parse_selection(text, expected):
    assert cli.parse_selection(text, 5) == expected


def test_preview_then_confirm_writes(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, ["y"])
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    assert "Created 1 file(s)" in capsys.readouterr().out


def test_preview_then_decline_writes_nothing(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, [""])  # 回车 = 默认 N
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    assert cli_ids(env.index_entries()) == ["existing"]
    assert not env.journal.exists()
    assert "Nothing was written." in capsys.readouterr().out


def test_no_input_flag_never_prompts(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, [])
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--no-input")
    assert "Preview only" in capsys.readouterr().out


def test_waits_for_desktop_to_quit(env, monkeypatch, capsys):
    running = iter([True, False])
    monkeypatch.setattr(cli, "desktop_running", lambda: next(running))
    fake_terminal(monkeypatch, [""])  # 提示退出桌面端后按回车
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    assert "press Enter to retry" in capsys.readouterr().out


def test_cancel_while_desktop_running(env, monkeypatch):
    monkeypatch.setattr(cli, "desktop_running", lambda: True)
    fake_terminal(monkeypatch, ["q"])
    env.add_index("existing")
    env.add_cli_session("new-1")
    with pytest.raises(SystemExit) as e:
        env.run("--apply")
    assert "Cancelled" in str(e.value.code)
    assert cli_ids(env.index_entries()) == ["existing"]


def test_purge_needs_typed_yes_in_terminal(env, monkeypatch, capsys):
    arch = env.add_index("old", archived=True)
    env.add_index("keep")
    env.add_cli_session("new-1")
    fake_terminal(monkeypatch, ["y"])  # --apply 不再问"现在写入"，"y" 不等于 "yes"
    env.run("--purge-archived", "--apply")
    assert arch.exists()
    assert cli_ids(env.index_entries()) == ["keep", "new-1", "old"]  # 同步照常进行
    assert "Purge skipped" in capsys.readouterr().out
    fake_terminal(monkeypatch, ["yes"])
    env.run("--purge-archived", "--apply")
    assert not arch.exists()


def test_interactive_pick_and_remember(env, monkeypatch, capsys):
    import time

    env.add_index("existing")
    env.add_cli_session("older", text="older task", mtime=time.time() - 7200)
    env.add_cli_session("newer", text="newer task")
    # 会话按最后活动时间倒序：两个会话时间戳相同时按扫描顺序，所以只挑第 1 个并记住另一个
    fake_terminal(monkeypatch, ["1", "y", "y"])
    env.run("-i")
    added = set(cli_ids(env.index_entries())) - {"existing"}
    assert len(added) == 1
    remembered = cli.read_exclude_file(cli.default_exclude_file())
    assert remembered == {"older", "newer"} - added
    # 下次运行自动跳过记住的会话
    capsys.readouterr()
    env.run()
    assert "Everything is in sync" in capsys.readouterr().out


def test_interactive_cancel(env, monkeypatch):
    fake_terminal(monkeypatch, ["q"])
    env.add_index("existing")
    env.add_cli_session("new-1")
    with pytest.raises(SystemExit):
        env.run("-i")
    assert cli_ids(env.index_entries()) == ["existing"]


def test_interactive_requires_terminal(env):
    env.add_index("existing")
    with pytest.raises(SystemExit) as e:
        env.run("-i")
    assert "--interactive needs a terminal" in str(e.value.code)


def test_undo_asks_first_in_terminal(env, monkeypatch, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    fake_terminal(monkeypatch, ["n"])
    cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    assert "Most recent run" in capsys.readouterr().out
    fake_terminal(monkeypatch, ["y"])
    cli.main(["--undo"])
    assert cli_ids(env.index_entries()) == ["existing"]


def test_exclude_file_comments_and_missing(env, tmp_path):
    f = tmp_path / "ex.txt"
    f.write_text("new-1  # some title\n\n# comment\n", encoding="utf-8")
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply", "--exclude-file", str(f))
    assert cli_ids(env.index_entries()) == ["existing"]
    with pytest.raises(SystemExit) as e:
        env.run("--exclude-file", str(tmp_path / "nope.txt"))
    assert "Exclude file not found" in str(e.value.code)
