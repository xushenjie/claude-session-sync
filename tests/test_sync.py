import json

import pytest

from claude_session_sync import __version__, cli

from .fakes import ACCT, ORG, ORG_EMPTY


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
