import pytest

from claude_session_sync import cli

from .fakes import Env


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setattr(cli, "JOURNAL_DIR", e.journal)
    # 默认假装桌面端没在运行，避免开发机上开着桌面端时测试失败
    monkeypatch.setattr(cli, "desktop_running", lambda: False)
    # 不读取开发机上真实的 ~/.claude.json，需要的测试自己设置
    monkeypatch.setattr(cli, "cli_login_account", lambda: None)
    return e


@pytest.fixture(autouse=True)
def english_output(monkeypatch):
    """默认按英文输出断言；需要中文的测试自己改。"""
    monkeypatch.setattr(cli, "_LANG_ZH", False)


@pytest.fixture(autouse=True)
def isolated_sync_dir(tmp_path, monkeypatch):
    """不读写开发机上真实的 ~/.claude-desktop-sync（config.toml 会改变默认参数和语言）。"""
    monkeypatch.setattr(cli, "JOURNAL_DIR", tmp_path / "sync-home")
