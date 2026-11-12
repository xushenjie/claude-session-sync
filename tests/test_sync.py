import json
import re

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
    env.add_cli_session("cmd-only", text="<command-name>/model</command-name>", reply=False)
    import time

    env.add_cli_session("running", mtime=time.time())
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing"]


def test_syncs_sessions_started_by_slash_command(env):
    env.add_index("existing")
    env.add_cli_session(
        "slash",
        text="<command-message>implement</command-message>\n<command-name>/implement</command-name>\n"
        "<command-args>issues/01.md</command-args>",
    )
    env.add_cli_session("ide", text="<ide_opened_file>/work/demo/a.py</ide_opened_file>")
    env.add_cli_session(
        "ai-titled", text="<command-name>/implement</command-name>", extra=[{"type": "ai-title", "aiTitle": "AI 标题"}]
    )
    env.run("--apply")
    titles = {e["cliSessionId"]: e["title"] for e in env.index_entries()}
    assert titles["slash"] == "/implement issues/01.md"
    assert titles["ide"] == "ide"  # 没有可用标题时退回文件名
    assert titles["ai-titled"] == "AI 标题"


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
    assert "待补" in out and "aaaaaaaa/bbbbbbbb 1 +1" in squash(out) and "预览模式" in out


def test_english_output(env, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    out = capsys.readouterr().out
    assert "to add" in out and "aaaaaaaa/bbbbbbbb 1 +1" in squash(out) and "Preview only" in out
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
    assert out.count(f"{cli.sym('run')} still being written") == 1  # 只标注最近写入的那个
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
    assert "● al***@example.com current CLI login aaaaaaaa/bbbbbbbb" in squash(out)
    assert "alice@example.com" not in out  # 完整邮箱不能出现，组织名里的也不行
    assert "● aaaaaaaa/bbbbbbbb 0 0" in squash(out)  # 另一个组织没有名称，只显示 ID
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
    assert "Acme · al***@example.com aaaaaaaa/bbbbbbbb" in squash(capsys.readouterr().out)


def test_set_alias_and_remove(env, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.run("--set-alias", "aaaaaaaa=work")  # 只写账号前缀：对该账号的所有组织生效
    env.run("--set-alias", "aaaaaaaa/bbbbbbbb-0000-4000-8000-000000000002=side")  # 组织级别优先
    capsys.readouterr()
    env.run()
    out = capsys.readouterr().out
    assert "work aaaaaaaa/bbbbbbbb 1" in squash(out)
    assert "side aaaaaaaa/bbbbbbbb 0" in squash(out)
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
    fake_terminal(monkeypatch, ["q"])
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


# ---------------------------------------------------------------- 菜单、当场询问、默认设置


def test_menu_enter_writes(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, [""])
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    assert "What next?" in capsys.readouterr().out


def test_menu_undo_option(env, monkeypatch, capsys):
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    env.add_cli_session("new-2")
    fake_terminal(monkeypatch, ["3", "y"])  # 菜单选撤销，再确认
    env.run()
    assert cli_ids(env.index_entries()) == ["existing"]
    assert "Undid" in capsys.readouterr().out


def test_menu_hides_undo_without_journal(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, ["3", "q"])  # 没有日志时 3 不是有效选项
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    out = capsys.readouterr().out
    assert "Undo the most recent write" not in out
    assert "Please choose one of the options above." in out
    assert cli_ids(env.index_entries()) == ["existing"]


def test_menu_pick_then_write(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("a", text="task a")
    env.add_cli_session("b", text="task b")
    fake_terminal(monkeypatch, ["2", "1", "n"])  # 挑第 1 个，不记住另一个
    env.run()
    assert len(cli_ids(env.index_entries())) == 2


def test_asks_about_recent_sessions(env, monkeypatch, capsys):
    import time

    env.add_index("existing")
    env.add_cli_session("running", mtime=time.time())
    fake_terminal(monkeypatch, ["y", ""])  # 一并同步，然后菜单回车写入
    env.run()
    assert cli_ids(env.index_entries()) == ["existing", "running"]
    assert "may still be running" in capsys.readouterr().out


def test_declining_recent_keeps_them_back(env, monkeypatch):
    import time

    env.add_index("existing")
    env.add_cli_session("running", mtime=time.time())
    fake_terminal(monkeypatch, [""])  # 默认不同步；之后没有可写的，不再出菜单
    env.run()
    assert cli_ids(env.index_entries()) == ["existing"]


def test_no_include_recent_never_asks(env, monkeypatch, capsys):
    import time

    env.add_index("existing")
    env.add_cli_session("running", mtime=time.time())
    fake_terminal(monkeypatch, [])
    env.run("--no-include-recent")
    assert "may still be running" not in capsys.readouterr().out


def test_offers_purge_in_terminal(env, monkeypatch):
    arch = env.add_index("old", archived=True)
    env.add_index("keep")
    env.add_cli_session("new-1")
    fake_terminal(monkeypatch, ["", ""])  # 菜单写入，清除问题回车 = 保留
    env.run()
    assert arch.exists()
    assert "new-1" in cli_ids(env.index_entries())
    fake_terminal(monkeypatch, ["", "yes"])
    env.run()
    assert not arch.exists()


def test_purge_only_skipped_writes_nothing(env, monkeypatch):
    env.add_index("old", archived=True)
    fake_terminal(monkeypatch, ["", ""])
    env.run()
    assert not env.journal.exists()


def test_no_purge_flag_and_scripts_do_not_offer_purge(env, monkeypatch, capsys):
    env.add_index("old", archived=True)
    env.add_index("keep")
    env.run()  # 非终端：不计算清除
    assert "Purge archived sessions" not in capsys.readouterr().out
    fake_terminal(monkeypatch, [])
    env.run("--no-purge-archived")
    assert "Purge archived sessions" not in capsys.readouterr().out


def test_config_sets_defaults(env, monkeypatch, capsys):
    import time

    cli.JOURNAL_DIR.mkdir(parents=True)
    cli.config_file().write_text('lang = "zh"\ninclude_recent = true  # 注释\n', encoding="utf-8")
    monkeypatch.delenv(cli.LANG_ENV, raising=False)
    env.add_index("existing")
    env.add_cli_session("running", mtime=time.time())
    env.run("--apply")
    assert cli_ids(env.index_entries()) == ["existing", "running"]
    assert "已写入" in capsys.readouterr().out
    env.run("--lang", "en")  # 命令行优先
    assert "Everything is in sync" in capsys.readouterr().out


def test_env_lang_beats_config(monkeypatch, capsys):
    cli.JOURNAL_DIR.mkdir(parents=True)
    cli.config_file().write_text('lang = "zh"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_LANG_ZH", None)
    monkeypatch.setenv(cli.LANG_ENV, "en")
    cli.apply_lang_flag([], cli.load_config())
    assert cli.t("en", "zh") == "en"


def test_bad_config_values_are_ignored(capsys):
    cli.JOURNAL_DIR.mkdir(parents=True)
    cli.config_file().write_text('lang = "fr"\nunknown = true\npurge_archived = true\n', encoding="utf-8")
    assert cli.load_config() == {"purge_archived": True}
    out = capsys.readouterr().out
    assert "lang" in out and "unknown" in out


def test_unparsable_config_is_ignored(capsys):
    cli.JOURNAL_DIR.mkdir(parents=True)
    cli.config_file().write_text("this is not toml\n", encoding="utf-8")
    assert cli.load_config() == {}
    assert "cannot parse" in capsys.readouterr().out


def test_simple_toml_fallback():
    assert cli._parse_simple_toml("# c\nlang = 'zh'\npurge_archived = false # x\n") == {
        "lang": "zh",
        "purge_archived": False,
    }
    with pytest.raises(ValueError):
        cli._parse_simple_toml("x = 1\n")


def test_save_defaults_roundtrip(env, capsys):
    env.add_index("existing")
    env.run("--save-defaults", "--lang", "en", "--purge-archived", "--no-include-recent")
    assert "Saved defaults" in capsys.readouterr().out
    assert cli.load_config() == {
        "lang": "en",
        "color": "auto",
        "icons": "symbols",
        "layout": "auto",
        "purge_archived": True,
        "include_recent": False,
        "per_account_archive": False,
    }
    env.run("--save-defaults", "--no-purge-archived")  # 再次保存时保留没改的项
    cfg = cli.load_config()
    assert cfg["purge_archived"] is False and cfg["lang"] == "en" and cfg["include_recent"] is False


# ---------------------------------------------------------------- 输出样式


def test_account_notes_explain_count_differences(env, capsys):
    env.add_index("gone")  # 会话记录已不存在：孤立索引
    env.add_index("old", archived=True, org=ORG_EMPTY)
    env.add_index("keep")
    env.add_index("keep", org=ORG_EMPTY)
    env.add_cli_session("keep")
    env.add_cli_session("old")
    env.add_cli_session("cmd", text="<command-name>/model</command-name>", reply=False)
    env.add_index("cmd")
    env.run()
    out = squash(capsys.readouterr().out)
    assert "+1 orphaned · +1 command-only/empty" in out
    assert "−1 archived" in out  # 在另一个组织归档过，所以这里也不补
    assert "point to a deleted transcript" in out


def test_sessions_grouped_by_project(env, capsys):
    env.add_index("existing")
    env.add_cli_session("a", cwd="/work/alpha", project="-work-alpha", text="task a")
    env.add_cli_session("b", cwd="/work/beta", project="-work-beta", text="task b")
    env.run()
    out = capsys.readouterr().out
    assert "▸ /work/alpha" in out and "▸ /work/beta" in out
    assert out.index("/work/alpha") < out.index("task a") < out.index("/work/beta") < out.index("task b")


def test_emoji_icons_and_alignment(env, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.add_cli_session("new-1")
    env.run("--icons", "emoji")
    lines = capsys.readouterr().out.splitlines()
    rows = [line for line in lines if "aaaaaaaa/bbbbbbbb" in line]
    assert rows[0].lstrip().startswith("⚪") and rows[1].lstrip().startswith("⚪")  # 都不是当前 CLI 账号
    head = next(line for line in lines if "existing" in line)
    # 表头"existing"的右端和数字的右端对齐（emoji 占两格）
    end = cli.disp_width(head[: head.index("existing")]) + len("existing")
    for row in rows:
        i = row.index("aaaaaaaa/bbbbbbbb") + len("aaaaaaaa/bbbbbbbb")
        num = row[i:].split()[0]
        assert cli.disp_width(row[: i + row[i:].index(num)]) + len(num) == end


def test_ascii_icons(env, capsys):
    env.add_index("existing")
    env.run("--icons", "ascii")
    out = capsys.readouterr().out
    assert "o aaaaaaaa/bbbbbbbb" in out and "●" not in out and "✓" not in out


def test_disp_width_ignores_colors_and_handles_emoji():
    assert cli.disp_width("\033[1;32mok\033[0m") == 2
    assert cli.disp_width("⚠️") == 2 and cli.disp_width("⚠") == 1
    assert cli.disp_width("✅中") == 4
    assert cli.rpad("已有", 6) == "  已有"


def test_write_summary_and_tips(env, capsys):
    env.add_index("existing")
    env.add_cli_session("existing")
    env.add_cli_session("new-1")
    env.run("--apply")
    out = capsys.readouterr().out
    assert "✓ Done" in out and "Created 1 file(s)" in out and "› journal:" in out
    env.run()
    out = capsys.readouterr().out
    assert "Everything is in sync" in out and "last write" in out and "--save-defaults" in out


# ---------------------------------------------------------------- 帮助、短参数、补全


def test_help_groups_and_examples(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")
    with pytest.raises(SystemExit):
        cli.main(["--lang", "zh", "-h"])
    out = capsys.readouterr().out
    order = [out.index(x) for x in ("常用命令", "常用:", "账号与筛选:", "输出:", "高级选项:", "其他:")]
    assert order == sorted(order)
    assert out.index("--show-template") > out.index("高级选项:")  # 少用的参数在后面
    assert out.index("-a, --apply") < out.index("--account")
    assert all(cli.disp_width(line) <= 80 for line in out.splitlines())  # 中文也不超出终端宽度
    assert "--[no-]purge-archived" in out  # 不在连字符处断开参数名
    with pytest.raises(SystemExit):
        cli.main(["--lang", "en", "-h"])
    assert all(cli.disp_width(line) <= 80 for line in capsys.readouterr().out.splitlines())


def test_wrap_display():
    assert cli.wrap_display("中文中文中文", 6) == ["中文中", "文中文"]
    assert cli.wrap_display("aa --no-purge-archived bb", 12) == ["aa", "--no-purge-archived", "bb"]
    assert cli.wrap_display("aaaa bbbb", 8) == ["aaaa", "bbbb"]  # 算上中间的空格是 9，必须换行
    assert cli.wrap_display("加 /<组织ID前缀>", 15) == ["加 /<组织ID前缀>"]  # 闭合符号不单独放到行首


def test_short_flags(env, monkeypatch):
    import time

    arch = env.add_index("old", archived=True)
    env.add_index("existing")
    env.add_cli_session("busy", mtime=time.time())
    fake_terminal(monkeypatch, ["yes"])  # -p 仍然要输入 yes 确认
    env.run("-a", "-p", "-r")
    assert cli_ids(env.index_entries()) == ["busy", "existing"]
    assert not arch.exists()
    fake_terminal(monkeypatch, ["y"])
    env.run("-u")
    assert arch.exists()


def test_zsh_completion_script(capsys):
    cli.main(["--lang", "en", "--completion", "zsh"])
    out = capsys.readouterr().out
    assert out.startswith("#compdef claude-session-sync")
    assert "'(-a --apply)'{-a,--apply}" in out
    assert "'(-q --quiet -v --verbose)'{-v,--verbose}" in out  # 互斥组
    assert ":icons:(symbols emoji ascii)" in out
    assert "--exclude-file[" in out and ":FILE:_files" in out
    assert "'*--account[" in out  # 可重复的参数
    assert "compdef _claude_session_sync claude-session-sync" in out


def test_zsh_completion_parses(tmp_path, capsys):
    import shutil
    import subprocess

    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    cli.main(["--lang", "zh", "--completion", "zsh"])
    script = tmp_path / "_css.zsh"
    script.write_text(capsys.readouterr().out, encoding="utf-8")
    subprocess.run(["zsh", "-n", str(script)], check=True)


# ---------------------------------------------------------------- 框线布局


def boxed_lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_boxed_layout_panels_are_aligned(env, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(cli, "cli_login_account", lambda: dict(CLI_ACCT))
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.add_cli_session("new-1", text="一个很长的中文标题" * 8)
    env.run("--layout", "boxed")
    lines = boxed_lines(capsys)
    boxed = [line for line in lines if line[0] in "╭│├╰"]
    assert all(cli.disp_width(line) == 80 for line in boxed)  # 所有面板一样宽，中文也对齐
    out = "\n".join(lines)
    for title in ("Accounts", "Sessions to add (1)", "Plan"):
        assert f"─ {title} ─" in out
    assert "al***@example.com  CLI" in out and "aaaaaaaa/bbbbbbbb" in out
    assert "…" in out  # 长标题按宽度截断


def test_boxed_layout_drops_id_column_when_narrow(env, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "60")
    monkeypatch.setattr(cli, "cli_login_account", lambda: dict(CLI_ACCT))
    env.add_index("existing")
    env.run("--layout", "boxed")
    lines = boxed_lines(capsys)
    assert all(cli.disp_width(line) == 60 for line in lines if line[0] in "╭│├╰")
    assert "aaaaaaaa/bbbbbbbb" not in "\n".join(lines)


def test_boxed_ascii_fallback(env, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")
    env.add_index("existing")
    env.run("--layout", "boxed", "--icons", "ascii")
    out = capsys.readouterr().out
    assert "+- Accounts -" in out and "╭" not in out and "│" not in out


def test_layout_auto_is_plain_when_piped(env, capsys):
    env.add_index("existing")
    env.run()
    assert "╭" not in capsys.readouterr().out


def test_boxed_in_sync_shows_tips_in_plan_panel(env, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")
    env.add_index("existing")
    env.add_cli_session("existing")
    env.run("--layout", "boxed")
    out = capsys.readouterr().out
    assert "Everything is in sync" in out and "› save options you always pass" in out


def test_table_shrinks_flex_column_before_dropping(monkeypatch):
    monkeypatch.setattr(cli, "_UNICODE", True)
    cols = [{"h": "a"}, {"h": "id", "drop": True}, {"h": "notes", "flex": True}]
    rows = [[cli.cell("x"), cli.cell("12345678"), cli.cell("n" * 60)]]
    lines = cli.table("T", cols, rows, width=40)
    assert all(cli.disp_width(line) == 40 for line in lines)
    assert "12345678" in lines[3] and "…" in lines[3]


# ---------------------------------------------------------------- 方向键选择


def press(monkeypatch, *keys):
    """模拟在终端里按键（方向键模式）。"""
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_keys", iter(keys))


def test_select_one_with_keys(monkeypatch, capsys):
    opts = [{"label": "a", "value": "A"}, {"label": "group", "sep": True}, {"label": "b", "value": "B"}]
    press(monkeypatch, "down", "enter")
    assert cli.select_one("Pick", opts) == "B"  # 跳过分隔行
    press(monkeypatch, "up", "up", "enter")
    assert cli.select_one("Pick", opts) == "A"  # 循环移动
    press(monkeypatch, "2")
    assert cli.select_one("Pick", opts) == "B"  # 数字键直接选
    press(monkeypatch, "esc")
    assert cli.select_one("Pick", opts) is None
    press(monkeypatch, "enter")
    assert cli.select_one("Pick", opts, default=2) == "B"  # 回车 = 默认项


def test_select_many_with_keys(monkeypatch):
    opts = [{"label": "alpha"}, {"label": "beta"}, {"label": "gamma"}]
    press(monkeypatch, "space", "enter")
    assert cli.select_many("Pick", opts) == [1, 2]
    press(monkeypatch, "a", "down", "space", "enter")
    assert cli.select_many("Pick", opts) == [1]  # a 全不选，再勾一个
    press(monkeypatch, "/", "g", "a", "enter", "a", "enter")
    assert cli.select_many("Pick", opts, selected=[]) == [2]  # 筛选后 a 只作用于筛出来的项
    press(monkeypatch, "esc")
    assert cli.select_many("Pick", opts) is None


def test_edit_form_with_keys(monkeypatch):
    fields = [
        {"label": "lang", "choices": [("en", "English"), ("zh", "中文")], "value": "en"},
        {"label": "icons", "choices": [("symbols", "s"), ("emoji", "e"), ("ascii", "a")], "value": "symbols"},
    ]
    press(monkeypatch, "right", "down", "left", "enter")
    assert cli.edit_form("Settings", fields) == {"lang": "zh", "icons": "ascii"}
    press(monkeypatch, "right", "esc")
    assert cli.edit_form("Settings", fields) is None


def test_confirm_key(monkeypatch):
    press(monkeypatch, "x", "y")
    assert cli.confirm_key("Go?") is True
    press(monkeypatch, "enter")
    assert cli.confirm_key("Go?", default=True) is True
    press(monkeypatch, "esc")
    assert cli.confirm_key("Go?", default=True) is False


def test_menu_with_keys(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("new-1")
    press(monkeypatch, "enter")  # 回车 = 按计划写入
    env.run()
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]
    env.add_cli_session("new-2")
    press(monkeypatch, "down", "down", "enter", "y")  # 移到"撤销"并确认
    env.run()
    assert cli_ids(env.index_entries()) == ["existing"]


def test_interactive_pick_with_keys(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("a", text="task a")
    env.add_cli_session("b", text="task b")
    press(monkeypatch, "space", "enter", "n", "y")  # 去掉第一个，不记住，确认写入
    env.run("-i")
    assert len(cli_ids(env.index_entries())) == 2


def test_screen_redraw_clips_long_lines(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setattr(cli, "_COLOR", False)
    s = cli.Screen()
    s.draw(["x" * 100, "中" * 50])
    s.draw(["short"])
    out = capsys.readouterr().out
    assert "\033[2F" in out  # 第二次重画先回到区域开头
    lines = [re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", line) for line in out.split("\n")]
    assert all(cli.disp_width(line) <= cli.term_width() - 1 for line in lines)  # 不会被终端自动折行


def test_keys_fall_back_to_numbers_when_scripted(env, monkeypatch, capsys):
    fake_terminal(monkeypatch, ["1"])  # 输入来自脚本：仍然用编号
    env.add_index("existing")
    env.add_cli_session("new-1")
    env.run()
    assert "[1]" in capsys.readouterr().out
    assert cli_ids(env.index_entries()) == ["existing", "new-1"]


def test_ctrl_c_exits_cleanly(env, monkeypatch, capsys):
    def keys():
        raise KeyboardInterrupt
        yield

    env.add_index("existing")
    env.add_cli_session("new-1")
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_keys", keys())
    with pytest.raises(SystemExit) as e:
        env.run()
    assert e.value.code == 130
    assert "Cancelled" in capsys.readouterr().err
    assert cli_ids(env.index_entries()) == ["existing"]


# ---------------------------------------------------------------- 参数不写值时交互选择


def answers(monkeypatch, *lines):
    """方向键之外需要输入文字的地方（如别名）。"""
    it = iter(lines)
    monkeypatch.setattr(cli, "_input", lambda prompt="": next(it))


@pytest.mark.parametrize("flag", ["--lang", "--account", "--set-alias", "--project", "--exclude", "--icons"])
def test_valueless_flag_needs_terminal(env, capsys, flag):
    env.add_index("existing")
    with pytest.raises(SystemExit) as e:
        env.run(flag)
    assert e.value.code == 2
    assert f"{flag} needs a value" in capsys.readouterr().err


def test_valueless_account_picks_accounts(env, monkeypatch):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    env.add_cli_session("new-1")
    press(monkeypatch, "space", "enter", "enter")  # 去掉第一个组织，确认；菜单回车写入
    env.run("--account")
    assert cli_ids(env.index_entries()) == ["existing"]
    assert cli_ids(env.index_entries(ACCT, ORG_EMPTY)) == ["new-1"]


def test_valueless_set_alias_wizard(env, monkeypatch, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    # 第 1 个起名 work，第 2 个起名 side，完成
    press(monkeypatch, "enter", *"work", "enter", "down", "enter", *"side", "enter", "q")
    env.run("--set-alias")
    aliases = json.loads((env.journal / "accounts.json").read_text())["aliases"]
    assert aliases == {f"{ACCT}/{ORG}": "work", f"{ACCT}/{ORG_EMPTY}": "side"}
    # 输入框预先填好 "work"：退格两次改成 "wo" + "w" = "wow"
    press(monkeypatch, "enter", "backspace", "backspace", "w", "enter", "q")
    env.run("--set-alias")
    assert json.loads((env.journal / "accounts.json").read_text())["aliases"][f"{ACCT}/{ORG}"] == "wow"
    press(monkeypatch, "enter", "\x15", "enter", "q")  # Ctrl-U 清空 = 删除别名
    env.run("--set-alias")
    aliases = json.loads((env.journal / "accounts.json").read_text())["aliases"]
    assert aliases == {f"{ACCT}/{ORG_EMPTY}": "side"}


def test_remove_account_level_alias_keeps_other_orgs(env):
    book = {"aliases": {ACCT: "shared"}, "accounts": {}}
    accts = [(None, ACCT, ORG, None), (None, ACCT, ORG_EMPTY, None)]
    cli.remove_alias(book, accts, ACCT, ORG)
    assert book["aliases"] == {f"{ACCT}/{ORG_EMPTY}": "shared"}


def test_valueless_project_picks_project(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("a", cwd="/work/alpha", project="-work-alpha")
    env.add_cli_session("b", cwd="/work/alpha", project="-work-alpha")
    env.add_cli_session("c", cwd="/work/beta", project="-work-beta")
    press(monkeypatch, "down", "down", "enter", "enter")  # 第 3 项是 /work/beta（第 1 项是全部）
    env.run("--project")
    assert cli_ids(env.index_entries()) == ["c", "existing"]


def test_valueless_exclude_and_manage_list(env, monkeypatch):
    env.add_index("existing")
    env.add_cli_session("a", text="task a")
    env.add_cli_session("b", text="task b")
    press(monkeypatch, "space", "enter", "y", "enter")  # 排除第 1 个并记住；写入
    env.run("--exclude")
    assert len(cli_ids(env.index_entries())) == 2
    assert len(cli.read_exclude_file(cli.default_exclude_file())) == 1
    press(monkeypatch, "space", "enter", "enter")  # 从排除列表移除它，然后写入
    env.run("--exclude-file")
    assert cli.read_exclude_file(cli.default_exclude_file()) == set()
    assert len(cli_ids(env.index_entries())) == 3


def test_valueless_save_defaults_opens_form(env, monkeypatch):
    env.add_index("existing")
    press(monkeypatch, "right", "right", "down", "right", "enter")  # 语言 → English，图标 → emoji
    env.run("--save-defaults")
    assert cli.load_config() == {"lang": "en", "icons": "emoji", "layout": "auto", "color": "auto", "ui": "keys"}


def test_save_defaults_with_values_skips_form(env, monkeypatch):
    env.add_index("existing")
    press(monkeypatch)  # 不应该读任何按键
    env.run("--save-defaults", "--lang", "zh")
    assert cli.load_config()["lang"] == "zh"


def test_valueless_icons_and_lang(env, monkeypatch, capsys):
    env.add_index("existing")
    press(monkeypatch, "down", "enter", "down", "enter")  # 语言选 English（第 2 项），图标选 emoji
    env.run("--lang", "--icons")
    assert "⚪" in capsys.readouterr().out


def test_show_template_picks_account(env, monkeypatch, capsys):
    env.add_index("existing")
    env.org_dir(ACCT, ORG_EMPTY)
    press(monkeypatch, "down", "enter")
    env.run("--show-template")
    out = capsys.readouterr().out
    assert ORG_EMPTY in out and f"[{ACCT}/{ORG}]" not in out


def test_completion_marks_optional_values(capsys):
    cli.main(["--lang", "en", "--completion"])
    out = capsys.readouterr().out
    assert "--account[" in out and "::ID:" in out and "::icons:(symbols emoji ascii)" in out


def test_edit_line_moves_cursor_and_handles_cjk(monkeypatch):
    press(monkeypatch, "left", "left", "中", "end", "space", "!", "enter")
    assert cli.edit_line("> ", "abc") == "a中bc !"
    press(monkeypatch, "x", "esc")
    assert cli.edit_line("> ", "abc") is None


def test_ask_text_without_keys_keeps_default(monkeypatch):
    answers(monkeypatch, "")
    assert cli.ask_text("> ", "old") == "old"
    answers(monkeypatch, " new ")
    assert cli.ask_text("> ", "old") == "new"
