#!/usr/bin/env python3
"""
claude-session-sync
把本地 Claude Code CLI 会话同步到 Claude 桌面端 Code 标签页的侧边栏（支持多账号）。

原理：
  CLI 会话记录在 ~/.claude/projects/<项目>/<会话ID>.jsonl（两端共用）。
  桌面端侧边栏只读它自己的索引：
      claude-code-sessions/<账号ID>/<组织ID>/local_<uuid>.json
  索引里用 cliSessionId 指向 .jsonl。本脚本为缺失的会话补写索引文件，
  同步时不修改任何 .jsonl 原始记录（只有 --purge-archived 会把已归档会话的 .jsonl 移到备份目录）。

规则：
  * 遍历找到的每个 <账号>/<组织> 目录，分别补齐该账号缺少的会话。
  * 在任何账号里被归档或被桌面端删除（deleted_<id> 标记）的会话，所有账号都不再补
    （可用 --per-account-archive 改为只看本账号的归档状态）。
  * 已存在于该账号索引里的会话（无论是否归档）不会重复写入。
  * 新索引文件以该账号下已有的一个桌面端会话为模板，只替换会话相关字段；
    账号下没有会话时借用其他账号的会话作为模板。

注意：桌面端索引格式没有官方文档，桌面端更新后可能失效。
  * 默认只预览（dry-run），加 --apply 才写入。
  * 写入前请彻底退出桌面端，写完重新打开。
  * 每次写入都会记日志，可用 --undo 撤销最近一次。

用法：见 claude-session-sync --help
"""

import argparse
import copy
import datetime as dt
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

__version__ = "0.3.0"

HOME = Path.home()
JOURNAL_DIR = HOME / ".claude-desktop-sync"
TITLE_MAX = 60
RUNNING_GRACE_SEC = 120  # 最近 2 分钟内还在写入的会话视为正在运行，跳过


# ---------------------------------------------------------------- 输出语言

LANG_ENV = "CLAUDE_SESSION_SYNC_LANG"
_LANG_ZH = None  # None 表示还没检测；测试里可以直接设成 True/False


def _detect_zh():
    """CLAUDE_SESSION_SYNC_LANG > LC_ALL > LC_MESSAGES > LANG > Windows 界面语言，默认英文。"""
    forced = os.environ.get(LANG_ENV)
    if forced:
        return forced.lower().startswith("zh")
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(key)
        if val:
            return val.lower().startswith("zh")
    if platform.system() == "Windows":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return langid & 0x3FF == 0x04  # LANG_CHINESE
        except Exception:  # noqa: BLE001
            pass
    return False


def t(en, zh):
    """按当前语言返回英文或中文文案。"""
    global _LANG_ZH
    if _LANG_ZH is None:
        _LANG_ZH = _detect_zh()
    return zh if _LANG_ZH else en


# ---------------------------------------------------------------- 终端输出与交互
#
# 只用标准库：ANSI 颜色只在终端里启用（遵循 NO_COLOR 和 --color），
# 终端编码不支持时符号退回 ASCII；交互全部基于 input()，管道和脚本里不会提问。

_COLOR = False
_UNICODE = True
_input = input  # 测试里替换成假的输入

ANSI = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
SYMBOLS = {
    "ok": ("✓", "OK"),
    "warn": ("⚠", "!"),
    "err": ("✗", "x"),
    "add": ("+", "+"),
    "del": ("-", "-"),
    "arrow": ("→", "->"),
    "dot": ("●", "*"),
    "ell": ("…", "..."),
}


def _enable_windows_ansi():
    """Windows 10+ 的控制台需要打开 VT 模式才能显示 ANSI 颜色；失败就不用颜色。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        return False


def setup_terminal(color="auto"):
    global _COLOR, _UNICODE
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(u for u, _ in SYMBOLS.values()).encode(enc)
        _UNICODE = True
    except (LookupError, UnicodeEncodeError):
        _UNICODE = False
    if color == "never":
        _COLOR = False
    elif color == "always":
        _enable_windows_ansi()
        _COLOR = True
    else:
        isatty = getattr(sys.stdout, "isatty", lambda: False)()
        _COLOR = isatty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb" and _enable_windows_ansi()


def style(text, *names):
    if not _COLOR or not names:
        return text
    codes = ";".join(ANSI[n] for n in names)
    return f"\033[{codes}m{text}\033[0m"


def sym(name):
    uni, ascii_ = SYMBOLS[name]
    return uni if _UNICODE else ascii_


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(text):
    """终端里的显示宽度：中日韩字符占两格。"""
    return sum(char_width(ch) for ch in text)


def fit(text, width):
    """截断到 width 个显示宽度以内，超出时以省略号结尾。"""
    if disp_width(text) <= width:
        return text
    ell = sym("ell")
    limit = max(0, width - disp_width(ell))
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + ell


def pad(text, width):
    return text + " " * max(0, width - disp_width(text))


def term_width():
    return max(60, shutil.get_terminal_size((100, 24)).columns)


def short_path(path):
    """把家目录缩写成 ~，让路径短一点。"""
    home = str(HOME)
    if path == home:
        return "~"
    for sep in ("/", "\\"):
        if path.startswith(home + sep):
            return "~" + path[len(home) :]
    return path


def warn(msg):
    print(style(f"{sym('warn')} {msg}", "yellow"))


def is_interactive():
    """标准输入和输出都是终端时才提问；管道、重定向、CI 里一律不问。"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def ask(prompt):
    """读取一行输入；Ctrl-C / Ctrl-D 返回 None。"""
    try:
        return _input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def confirm(question, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{question} {hint} ")
        if answer is None:
            return False
        answer = answer.strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "是", "好"):
            return True
        if answer in ("n", "no", "否"):
            return False


def parse_selection(text, n):
    """解析 "1-5,8 !3" 这样的编号选择（从 1 开始），返回从 0 开始的下标列表；格式不对返回 None。
    空输入或只有排除项表示从全部开始；"none" 表示一个都不选。"""
    tokens = [x for x in re.split(r"[,\s]+", text.strip().lower()) if x]
    if tokens == ["none"]:
        return []
    include, exclude, has_include = set(), set(), False
    for tok in tokens:
        neg = tok.startswith("!")
        body = tok[1:] if neg else tok
        if body in ("all", "a"):
            picked = set(range(n))
        else:
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", body)
            if not m:
                return None
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            if lo > hi:
                lo, hi = hi, lo
            if lo < 1 or hi > n:
                return None
            picked = set(range(lo - 1, hi))
        if neg:
            exclude |= picked
        else:
            include |= picked
            has_include = True
    if not has_include:
        include = set(range(n))
    return sorted(include - exclude)


def choose(items, question):
    """列出编号后让用户选，返回选中的下标；取消时返回 None。"""
    hint = t(
        "Enter = all, e.g. 1-5,8 to pick, !3 to leave one out, none, q to cancel",
        "回车 = 全部；例如 1-5,8 选择，!3 排除某一项，none 一个都不选，q 取消",
    )
    while True:
        answer = ask(f"{question} ({hint}): ")
        if answer is None or answer.strip().lower() in ("q", "quit"):
            return None
        picked = parse_selection(answer, len(items))
        if picked is not None:
            return picked
        warn(t(f"Use numbers between 1 and {len(items)}.", f"请输入 1 到 {len(items)} 之间的编号。"))


# ---------------------------------------------------------------- 路径发现


def cli_projects_dir(override=None):
    if override:
        return Path(override).expanduser()
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg).expanduser() if cfg else HOME / ".claude"
    return base / "projects"


def candidate_desktop_roots():
    """返回可能存在的 claude-code-sessions 目录。"""
    sysname = platform.system()
    c = []
    if sysname == "Darwin":
        c.append(HOME / "Library/Application Support/Claude/claude-code-sessions")
        c.append(HOME / "Library/Application Support/Claude-3p/claude-code-sessions")
    elif sysname == "Windows":
        appdata = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
        c.append(appdata / "Claude/claude-code-sessions")
        c.append(appdata / "Claude-3p/claude-code-sessions")
        c.append(local / "Claude-3p/claude-code-sessions")
        # 微软商店 (MSIX) 安装版
        for p in glob.glob(str(local / "Packages/Claude_*/LocalCache/Roaming/Claude/claude-code-sessions")):
            c.append(Path(p))
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
        c.append(xdg / "Claude/claude-code-sessions")
    return [p for p in c if p.is_dir()]


def account_dirs(roots):
    """返回 [(root, account, org, dirpath)]。"""
    out = []
    for root in roots:
        for acct in sorted(root.iterdir()):
            if not acct.is_dir():
                continue
            for org in sorted(acct.iterdir()):
                if org.is_dir():
                    out.append((root, acct.name, org.name, org))
    return out


# ---------------------------------------------------------------- 桌面端索引


def load_index(dirpath):
    entries = []
    for f in sorted(dirpath.glob("local_*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                entries.append((f, json.load(fh)))
        except Exception as e:  # noqa: BLE001
            print(t(f"  ! Cannot read {f.name}: {e}", f"  ! 无法读取 {f.name}: {e}"), file=sys.stderr)
    return entries


def load_markers(dirpath):
    """读取桌面端的删除标记与归档索引。
    返回 (deleted_cli_ids, archived_local_ids)。
      * deleted_<cliSessionId>：桌面端删除会话后留下的标记文件
      * archived-sessions.idx：{"v":1,"archived":["local_..."]}
    """
    deleted = {f.name[len("deleted_") :] for f in dirpath.glob("deleted_*")}
    archived_local = set()
    idx = dirpath / "archived-sessions.idx"
    if idx.is_file():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            archived_local = set(data.get("archived") or [])
        except Exception as e:  # noqa: BLE001
            print(t(f"  ! Cannot read {idx}: {e}", f"  ! 无法读取 {idx}: {e}"), file=sys.stderr)
    return deleted, archived_local


def is_archived(entry):
    """兼容几种可能的归档字段写法。"""
    for k in ("isArchived", "archived"):
        if entry.get(k) is True:
            return True
    for k in ("archivedAt", "archived_at"):
        if entry.get(k):
            return True
    status = entry.get("status")
    if isinstance(status, str) and status.lower() == "archived":
        return True
    return False


def pick_template(entries):
    """优先选非归档、非 worktree（cwd == originCwd）、最近活动的条目。"""

    def score(e):
        d = e[1]
        return (
            not is_archived(d),
            d.get("cwd") == d.get("originCwd"),
            d.get("lastActivityAt") or 0,
        )

    usable = [e for e in entries if isinstance(e[1], dict) and "cliSessionId" in e[1]]
    return max(usable, key=score)[1] if usable else None


# ---------------------------------------------------------------- CLI 会话解析


def iso_to_ms(s):
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                return part["text"]
    return ""


def is_abs_any(p):
    """不管当前是什么系统，POSIX 或 Windows 风格的绝对路径都算。
    （Python 3.13 起 Windows 上 os.path.isabs("/x") 返回 False，而记录可能来自别的系统。）"""
    return isinstance(p, str) and (PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute())


def use_safe_stdio():
    """会话标题可能含任意字符；输出被重定向到非 UTF-8 编码时替换掉无法编码的字符，而不是报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def parse_session(path):
    """读取 .jsonl，返回会话信息；没有用户消息或没有 cwd 的返回 None。"""
    cwd = None
    first_ts = last_ts = None
    custom_title = summary = first_user = None
    has_user = False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(o, dict):
                    continue
                if not cwd and is_abs_any(o.get("cwd")):
                    cwd = o["cwd"]
                ts = o.get("timestamp")
                if isinstance(ts, str):
                    ms = iso_to_ms(ts)
                    if ms:
                        first_ts = first_ts or ms
                        last_ts = ms
                kind = o.get("type")
                if kind == "custom-title" and o.get("customTitle"):
                    custom_title = o["customTitle"]
                elif kind == "summary" and o.get("summary"):
                    summary = o["summary"]
                elif kind == "user" and not o.get("isSidechain") and not o.get("isMeta"):
                    msg = o.get("message") or {}
                    txt = text_of(msg.get("content")).strip()
                    if txt and not txt.startswith("<"):  # 跳过命令/系统注入内容
                        has_user = True
                        first_user = first_user or txt
    except OSError as e:
        print(t(f"  ! Cannot read {path}: {e}", f"  ! 无法读取 {path}: {e}"), file=sys.stderr)
        return None
    if not has_user or not cwd:
        return None
    mtime_ms = int(path.stat().st_mtime * 1000)
    title = (custom_title or summary or first_user or path.stem).replace("\n", " ").strip()
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1] + "…"
    return {
        "id": path.stem,
        "path": path,
        "cwd": cwd,
        "title": title,
        "createdAt": first_ts or mtime_ms,
        "lastActivityAt": max(last_ts or 0, mtime_ms),
        "mtime": path.stat().st_mtime,
    }


def scan_cli_sessions(projects):
    sessions = []
    skipped = 0
    for proj in sorted(projects.iterdir()):
        if not proj.is_dir():
            continue
        # 只取项目目录顶层的 .jsonl（子目录里是子代理记录等）
        for f in sorted(proj.glob("*.jsonl")):
            s = parse_session(f)
            if s:
                sessions.append(s)
            else:
                skipped += 1
    return sessions, skipped


# ---------------------------------------------------------------- 写入


def build_entry(template, s):
    e = copy.deepcopy(template)
    old_cwd = template.get("cwd")
    new_id = f"local_{uuid.uuid4()}"
    # 模板里其他字段若等于旧 cwd，一并替换（避免指向别的项目）
    for k, v in list(e.items()):
        if isinstance(v, str) and old_cwd and v == old_cwd:
            e[k] = s["cwd"]
    e.update(
        {
            "sessionId": new_id,
            "cliSessionId": s["id"],
            "cwd": s["cwd"],
            "originCwd": s["cwd"],
            "createdAt": s["createdAt"],
            "lastActivityAt": s["lastActivityAt"],
        }
    )
    for k in ("title", "name"):
        if k in e:
            e[k] = s["title"]
    if "title" not in e and "name" not in e:
        e["title"] = s["title"]
    # 清除归档相关状态
    for k in ("isArchived", "archived"):
        if k in e:
            e[k] = False
    for k in ("archivedAt", "archived_at"):
        if k in e:
            e[k] = None
    if isinstance(e.get("status"), str) and e["status"].lower() == "archived":
        e["status"] = "idle"
    return new_id, e


# Linux 上没有官方桌面端，社区打包版的进程名不统一，这里只按常见名字精确匹配。
# 不用 `pgrep -f claude`，否则会把正在运行的 CLI（claude）误判为桌面端。
LINUX_PROCESS_NAMES = ("Claude", "claude-desktop")


# Windows 上桌面端和原生安装的 CLI 都叫 claude.exe，只能按可执行文件路径区分。
# 以下规则来自常见安装位置，尚未在所有安装方式上验证。
WINDOWS_DESKTOP_HINTS = ("\\anthropicclaude\\", "\\windowsapps\\claude_", "\\programs\\claude\\", "\\claude-3p\\")
WINDOWS_CLI_HINTS = ("\\.local\\bin\\", "\\npm\\", "\\node_modules\\", "\\.claude\\", "\\scoop\\", "\\winget\\")


def windows_claude_exe_paths():
    """返回所有 claude.exe 进程的路径；拿不到路径（如权限不足）的进程记为空字符串。"""
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-Process -Name claude -ErrorAction SilentlyContinue | ForEach-Object { '' + $_.Path }",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [ln.strip() for ln in r.stdout.splitlines()]


def windows_desktop_running(paths):
    """根据 claude.exe 的路径判断桌面端是否在运行。
    明确是桌面端 → True；明确是 CLI → 忽略；分辨不出 → 打印提醒但不阻止（返回 False）。"""
    unknown = []
    for p in paths:
        low = p.lower()
        if any(h in low for h in WINDOWS_DESKTOP_HINTS):
            return True
        if low and any(h in low for h in WINDOWS_CLI_HINTS):
            continue
        unknown.append(p or "?")
    if unknown:
        print(
            t(
                "  ! Found claude.exe process(es) that may or may not be the desktop app: "
                f"{', '.join(unknown)}. Make sure the desktop app is fully quit.",
                f"  ! 发现无法判断是否为桌面端的 claude.exe 进程：{', '.join(unknown)}。请自行确认桌面端已彻底退出。",
            ),
            file=sys.stderr,
        )
    return False


def desktop_running():
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            r = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True)
            return r.returncode == 0
        if sysname == "Windows":
            return windows_desktop_running(windows_claude_exe_paths())
        for name in LINUX_PROCESS_NAMES:
            if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


DESKTOP_RUNNING_MSG = (
    "The Claude desktop app is running. Nothing was written. "
    "Quit it completely (from the menu bar / system tray) and try again, or pass --force.",
    "检测到桌面端正在运行，这次没有写入。请彻底退出（托盘/菜单栏里退出）后重试，或加 --force。",
)


def ensure_desktop_stopped(force, interactive=False):
    """桌面端运行时不做任何写入。交互模式下等用户退出桌面端后按回车重试，否则直接退出。"""
    if force:
        return
    while desktop_running():
        if not interactive:
            sys.exit(t(*DESKTOP_RUNNING_MSG))
        warn(
            t(
                "The Claude desktop app is running. Quit it completely (menu bar / system tray), "
                "then press Enter to retry, or type q to cancel.",
                "检测到桌面端正在运行。请彻底退出（托盘/菜单栏里退出）后按回车重试，输入 q 取消。",
            )
        )
        answer = ask("> ")
        if answer is None or answer.strip().lower() in ("q", "quit", "n", "no"):
            sys.exit(t("Cancelled. Nothing was written.", "已取消，没有写入任何文件。"))


def new_stamp():
    """日志/备份目录的时间戳，精确到微秒，避免同一秒内运行两次互相覆盖。"""
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def write_journal(created, moved=None, stamp=None):
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    p = JOURNAL_DIR / f"journal-{stamp or new_stamp()}.json"
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(
            {"created": [str(c) for c in created], "moved": [[str(a), str(b)] for a, b in (moved or [])]},
            fh,
            ensure_ascii=False,
            indent=2,
        )
    return p


def undo(force=False, interactive=False):
    journals = sorted(JOURNAL_DIR.glob("journal-*.json")) if JOURNAL_DIR.exists() else []
    if not journals:
        print(t("Nothing to undo.", "没有可撤销的记录。"))
        return
    j = journals[-1]
    data = json.loads(j.read_text(encoding="utf-8"))
    if interactive:
        when = journal_time(j)
        print(
            t(
                f"Most recent run: {when} ({j.name}): created {len(data.get('created', []))} file(s), "
                f"moved {len(data.get('moved', []))} file(s)/folder(s).",
                f"最近一次运行：{when}（{j.name}）：新建 {len(data.get('created', []))} 个文件，"
                f"移除 {len(data.get('moved', []))} 个文件/目录。",
            )
        )
        if not confirm(t("Undo it?", "撤销这次运行？")):
            print(t("Cancelled.", "已取消。"))
            return
    ensure_desktop_stopped(force, interactive)
    n = 0
    for f in data.get("created", []):
        fp = Path(f)
        if fp.exists():
            fp.unlink()
            n += 1
    m = 0
    for orig, backup in reversed(data.get("moved", [])):
        orig, backup = Path(orig), Path(backup)
        if backup.exists():
            orig.parent.mkdir(parents=True, exist_ok=True)
            if orig.is_file():
                orig.unlink()  # 被改写过的文件（如 archived-sessions.idx）先删掉再还原
            shutil.move(str(backup), str(orig))
            m += 1
    j.rename(j.with_suffix(".undone"))
    print(
        style(sym("ok"), "green"),
        t(
            f"Undid {j.name}: removed {n} created file(s), restored {m} file(s)/folder(s). Restart the desktop app.",
            f"已撤销 {j.name}：删除了 {n} 个新建文件，还原了 {m} 个文件/目录。请重启桌面端。",
        ),
    )


def journal_time(path):
    """journal-20261111-140200-123456.json → 2026-11-11 14:02:00；解析不了就返回文件名。"""
    try:
        stamp = path.stem.split("-", 1)[1]
        return dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f").strftime("%Y-%m-%d %H:%M:%S")
    except (IndexError, ValueError):
        return path.name


# ---------------------------------------------------------------- 清除已归档


def is_recently_written(paths, now=None):
    """任一 .jsonl 在最近 RUNNING_GRACE_SEC 秒内被修改过，就认为会话可能正在运行。"""
    now = now if now is not None else time.time()
    for p in paths:
        try:
            if p.is_file() and now - p.stat().st_mtime < RUNNING_GRACE_SEC:
                return True
        except OSError:
            return True  # 读不到状态时保守处理，宁可不删
    return False


def plan_purge(indexes, archived_ids, projects, excluded=(), project=None):
    """找出可以彻底删除的已归档会话，不做任何修改。
    excluded 里的会话、工作目录不包含 project 的会话、最近仍在写入的会话都会跳过。
    返回 (plan, running, filtered)：plan 是 [(cid, title, idx_hits, cli_hits)]，
    running 是跳过的仍在写入的会话 ID，filtered 是按 --exclude/--project 跳过的数量。
    """
    plan, running, filtered = [], [], 0
    for cid in sorted(i for i in archived_ids if i):
        if cid in excluded:
            filtered += 1
            continue
        idx_hits = []
        for d, (_acct, _org, entries, _, _) in indexes.items():
            for f, e in entries:
                if isinstance(e, dict) and e.get("cliSessionId") == cid and f.exists():
                    idx_hits.append((d, f, e))
        if project and not any(project in str(e.get("cwd") or "") for _, _, e in idx_hits):
            filtered += 1
            continue
        cli_hits = list(projects.glob(f"*/{cid}.jsonl")) + [p for p in projects.glob(f"*/{cid}") if p.is_dir()]
        if is_recently_written(cli_hits):
            running.append(cid)
            continue
        title = next((e.get("title") for _, _, e in idx_hits if e.get("title")), cid)
        plan.append((cid, title, idx_hits, cli_hits))
    return plan, running, filtered


def execute_purge(plan, trash):
    """按 plan_purge 的结果彻底删除，模仿桌面端"删除"的效果：
      * 删除所有账号里指向该会话的 local_*.json 索引
      * 从 archived-sessions.idx 中移除对应条目
      * 在原账号目录写入 deleted_<cliSessionId> 标记（防止被再次同步）
      * 删除 CLI 的 <会话ID>.jsonl 及同名子目录（子代理记录等）
    所有被删/被改的文件都先移动到 trash 目录，可用 --undo 还原。返回 (created, moved)。
    """
    created, moved = [], []
    now_ms = str(int(time.time() * 1000))
    idx_backed = set()

    def backup_path(path):
        dst = trash / path.relative_to(path.anchor)
        dst.parent.mkdir(parents=True, exist_ok=True)
        return dst

    def backup(path):
        dst = backup_path(path)
        shutil.move(str(path), str(dst))
        moved.append((path, dst))
        return dst

    for cid, _title, idx_hits, cli_hits in plan:
        local_ids_by_dir = {}
        for d, f, e in idx_hits:
            local_ids_by_dir.setdefault(d, set()).update({f.stem, e.get("sessionId")})
            backup(f)
        for d, local_ids in local_ids_by_dir.items():
            marker = d / f"deleted_{cid}"
            if not marker.exists():
                marker.write_text(now_ms, encoding="utf-8")
                created.append(marker)
            idx = d / "archived-sessions.idx"
            if idx.is_file():
                try:
                    data = json.loads(idx.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                arch = data.get("archived") or []
                kept = [x for x in arch if x not in local_ids]
                if len(kept) != len(arch):
                    if idx not in idx_backed:  # 每个 idx 只备份一次原始版本，undo 时覆盖回去
                        shutil.copy2(idx, backup_path(idx))
                        moved.append((idx, backup_path(idx)))
                        idx_backed.add(idx)
                    data["archived"] = kept
                    idx.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for p in cli_hits:
            backup(p)
    return created, moved


# ---------------------------------------------------------------- 账号名称
#
# 桌面端只按 ID 存放账号目录，本机没有公开的地方记录其他账号的邮箱或组织名。
# 名称来源（按优先级）：
#   1. 用户用 --set-alias 起的别名
#   2. CLI 当前登录的账号（~/.claude.json 的 oauthAccount）
#   3. 以前 --apply 时记下的 CLI 账号信息（~/.claude-desktop-sync/accounts.json）
# 邮箱一律打码后再显示，因为预览输出经常会被贴进 issue。

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_email(email):
    local, _, domain = email.partition("@")
    keep = min(3, max(1, len(local) // 2))
    return f"{local[:keep]}***@{domain}"


def mask_emails_in(text):
    return EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)


def accounts_file():
    return JOURNAL_DIR / "accounts.json"


def load_account_book():
    try:
        data = json.loads(accounts_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "accounts": data.get("accounts") if isinstance(data.get("accounts"), dict) else {},
        "aliases": data.get("aliases") if isinstance(data.get("aliases"), dict) else {},
    }


def save_account_book(book):
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    p = accounts_file()
    p.write_text(json.dumps({"version": 1, **book}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)  # 里面有邮箱，只让自己读
    except OSError:
        pass


def cli_login_account():
    """读取 CLI 当前登录账号，只取识别账号需要的字段。读不到返回 None。"""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = ([Path(cfg).expanduser() / ".claude.json"] if cfg else []) + [HOME / ".claude.json"]
    for f in candidates:
        try:
            a = json.loads(f.read_text(encoding="utf-8")).get("oauthAccount")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(a, dict) and isinstance(a.get("accountUuid"), str):
            return {
                "account": a["accountUuid"],
                "org": a.get("organizationUuid") or "",
                "email": a.get("emailAddress") or "",
                "org_name": a.get("organizationName") or "",
            }
    return None


def remember_cli_account(book, cli_acct):
    """把 CLI 当前账号记进账号簿；有变化返回 True。"""
    if not cli_acct:
        return False
    key = f"{cli_acct['account']}/{cli_acct['org']}"
    info = {"email": cli_acct["email"], "org_name": cli_acct["org_name"]}
    old = book["accounts"].get(key) or {}
    if {k: old.get(k) for k in info} == info:
        return False
    book["accounts"][key] = {**info, "seen_at": dt.datetime.now().isoformat(timespec="seconds")}
    return True


def account_label(acct, org, book, cli_acct):
    """返回账号的可读名称（可能为空）。"""
    parts = []
    alias = book["aliases"].get(f"{acct}/{org}") or book["aliases"].get(acct)
    if alias:
        parts.append(alias)
    is_cli = bool(cli_acct and cli_acct["account"] == acct and cli_acct["org"] == org)
    info = (
        {"email": cli_acct["email"], "org_name": cli_acct["org_name"]}
        if is_cli
        else book["accounts"].get(f"{acct}/{org}") or {}
    )
    email = mask_email(info["email"]) if EMAIL_RE.fullmatch(info.get("email") or "") else ""
    org_name = mask_emails_in(info.get("org_name") or "")
    if org_name and not (email and email in org_name):  # 个人组织名常常就是"邮箱's Organization"
        parts.append(org_name)
    if email:
        parts.append(email)
    if is_cli:
        parts.append(t("current CLI login", "当前 CLI 登录"))
    return " · ".join(parts)


def account_heading(acct, org, book, cli_acct):
    ell = sym("ell")
    ids = f"{acct[:8]}{ell}/{org[:8]}{ell}"
    label = account_label(acct, org, book, cli_acct)
    return f"[{label}] ({ids})" if label else f"[{ids}]"


def account_short(acct, org, book, cli_acct):
    """会话列表里"补到哪些账号"用的短名称。"""
    label = account_label(acct, org, book, cli_acct)
    return label.split(" · ")[0] if label else f"{acct[:8]}{sym('ell')}"


def resolve_account_key(key, all_accts):
    """把 <账号ID前缀>[/<组织ID前缀>] 解析成完整 ID。返回 (账号ID, 组织ID或None)；出错时退出。"""
    acct_part, _, org_part = key.partition("/")
    if not acct_part:
        sys.exit(t(f"Invalid account ID: {key!r}", f"账号 ID 无效：{key!r}"))
    pairs = {(a, o) for _, a, o, _ in all_accts if a.startswith(acct_part) and o.startswith(org_part)}
    acct_ids = {a for a, _ in pairs}
    if not pairs:
        sys.exit(t(f"No account matches {key!r}.", f"没有匹配 {key!r} 的账号。"))
    if len(acct_ids) > 1:
        sys.exit(
            t(
                f"{key!r} matches several accounts: {', '.join(sorted(x[:8] for x in acct_ids))}. Use a longer prefix.",
                f"{key!r} 匹配到多个账号：{', '.join(sorted(x[:8] for x in acct_ids))}。请写更长的前缀。",
            )
        )
    acct = acct_ids.pop()
    if not org_part:
        return acct, None
    if len(pairs) > 1:
        sys.exit(t(f"{key!r} matches several organizations.", f"{key!r} 匹配到多个组织。"))
    return acct, next(iter(pairs))[1]


def set_aliases(specs, all_accts):
    book = load_account_book()
    for spec in specs:
        key, sep, name = spec.partition("=")
        if not sep:
            sys.exit(
                t(f"--set-alias expects ID=NAME, got {spec!r}", f"--set-alias 的格式是 ID=名称，收到的是 {spec!r}")
            )
        acct, org = resolve_account_key(key.strip(), all_accts)
        full = f"{acct}/{org}" if org else acct
        name = name.strip()
        if name:
            book["aliases"][full] = name
            print(t(f"Alias set: {full} = {name}", f"已设置别名：{full} = {name}"))
        else:
            book["aliases"].pop(full, None)
            print(t(f"Alias removed: {full}", f"已删除别名：{full}"))
    save_account_book(book)


# ---------------------------------------------------------------- 排除列表与交互挑选

LIST_LIMIT = 20  # 预览时默认最多列出这么多待补会话，-v 显示全部
CANCELLED = ("Cancelled. Nothing was written.", "已取消，没有写入任何文件。")


def default_exclude_file():
    """-i 里选择"记住"的会话写到这里，之后每次运行都会自动排除。"""
    return JOURNAL_DIR / "exclude.txt"


def read_exclude_file(path, must_exist=False):
    """每行一个会话 ID，# 后面是注释。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if must_exist:
            sys.exit(t(f"Exclude file not found: {path}", f"找不到排除列表文件：{path}"))
        return set()
    return {ln.split("#", 1)[0].strip() for ln in text.splitlines()} - {""}


def add_to_exclude_file(sessions):
    path = default_exclude_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for s in sessions:
            title = " ".join(str(s["title"]).split())
            fh.write(f"{s['id']}  # {title}\n")
    return path


def format_time(ms):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def pick_interactively(rows):
    """-i：先选账号（多于一个时），再选会话。直接修改 rows 里的 todo。"""
    writable = [r for r in rows if r["template"] and r["todo"]]
    if not writable:
        return
    if len(writable) > 1:
        print(style(t("Accounts with sessions to add:", "有待补会话的账号："), "bold"))
        for i, r in enumerate(writable, 1):
            print(f"  {i:>3}. {r['heading']}  " + t(f"to add {len(r['todo'])}", f"待补 {len(r['todo'])}"))
        picked = choose(writable, t("Accounts to sync", "要同步的账号"))
        if picked is None:
            sys.exit(t(*CANCELLED))
        for i, r in enumerate(writable):
            if i not in picked:
                r["todo"] = []
        writable = [writable[i] for i in picked]
        print()

    order = sorted({s["id"]: s for r in writable for s in r["todo"]}.values(), key=lambda s: -s["lastActivityAt"])
    if not order:
        return
    print(style(t(f"Sessions to add ({len(order)}):", f"待补会话（{len(order)} 个）："), "bold"))
    col = max(16, (term_width() - 30) // 2)
    for i, s in enumerate(order, 1):
        cwd = style(fit(short_path(s["cwd"]), col), "dim")
        print(f"  {i:>3}. {format_time(s['lastActivityAt'])}  {pad(fit(s['title'], col), col)}  {cwd}")
    picked = choose(order, t("Sessions to sync", "要同步的会话"))
    if picked is None:
        sys.exit(t(*CANCELLED))
    keep = {order[i]["id"] for i in picked}
    for r in writable:
        r["todo"] = [s for s in r["todo"] if s["id"] in keep]
    dropped = [s for s in order if s["id"] not in keep]
    if dropped and confirm(
        t(
            f"Remember the {len(dropped)} session(s) you left out, so later runs skip them too?",
            f"记住没选的 {len(dropped)} 个会话，以后运行也自动跳过？",
        )
    ):
        path = add_to_exclude_file(dropped)
        print(
            style(
                t(
                    f"Saved to {path}; delete a line there to sync it again.",
                    f"已记到 {path}；删掉对应行即可恢复同步。",
                ),
                "dim",
            )
        )
    print()


# ---------------------------------------------------------------- 预览输出


class SyncReport:
    """一次运行的同步计划：负责普通/安静/详细三种文本输出和 JSON 输出。"""

    def __init__(self, rows, sessions, skipped, recent, held_back, purge, archived_global, deleted_global):
        self.rows = rows
        self.sessions = sessions
        self.skipped = skipped
        self.recent = recent
        self.held_back = held_back
        self.purge = purge  # None 表示没有加 --purge-archived，否则是 plan_purge 的结果
        self.archived_global = archived_global
        self.deleted_global = deleted_global

    def writable(self):
        return [r for r in self.rows if r["template"]]

    def pending(self):
        """[(会话, [要补进去的账号行])]，按最后活动时间倒序。"""
        found = {}
        for r in self.writable():
            for s in r["todo"]:
                found.setdefault(s["id"], (s, []))[1].append(r)
        return sorted(found.values(), key=lambda x: -x[0]["lastActivityAt"])

    def n_files(self):
        return sum(len(r["todo"]) for r in self.writable())

    def purge_plan(self):
        return self.purge[0] if self.purge else []

    def has_work(self):
        return self.n_files() > 0 or bool(self.purge_plan())

    def targets_text(self, targets):
        total = len(self.writable())
        if total <= 1:
            return ""
        names = t("all accounts", "全部账号") if len(targets) == total else ", ".join(r["short"] for r in targets)
        return f"  {sym('arrow')} {names}"

    def session_line(self, s, targets, verbose=False):
        lead = f"  {sym('add')} "
        when = format_time(s["lastActivityAt"])
        tgt = self.targets_text(targets)
        mark = t("  [still being written]", "  （最近仍在写入）") if s["id"] in self.recent else ""
        extra = f"  {s['id']}" if verbose else ""
        fixed = disp_width(lead) + disp_width(when) + 4 + disp_width(tgt) + disp_width(mark) + len(extra)
        room = max(24, term_width() - fixed)
        title_w = min(disp_width(s["title"]), max(12, room * 3 // 5))
        title = fit(s["title"], title_w)
        cwd = fit(short_path(s["cwd"]), max(8, room - disp_width(title)))
        return (
            style(lead, "green")
            + style(when, "dim")
            + "  "
            + title
            + "  "
            + style(cwd, "dim")
            + style(tgt, "cyan")
            + style(mark, "yellow")
            + style(extra, "dim")
        )

    def print_plan(self, verbose=False, quiet=False):
        pending = self.pending()
        limit = None if verbose else LIST_LIMIT
        ell = sym("ell")
        if not quiet:
            print(
                style(
                    t(
                        f"CLI sessions: {len(self.sessions)} usable · desktop accounts: {len(self.rows)}",
                        f"CLI 会话 {len(self.sessions)} 个 · 桌面端账号 {len(self.rows)} 个",
                    ),
                    "bold",
                )
            )
            if verbose:
                print(
                    style(
                        t(
                            f"  skipped {self.skipped} empty or without a working directory; archived/deleted in any "
                            f"account: {len(self.archived_global)} (deletion markers: {len(self.deleted_global)})",
                            f"  跳过 {self.skipped} 个空会话/无 cwd 的记录；任一账号中已归档/已删除 "
                            f"{len(self.archived_global)} 个（其中删除标记 {len(self.deleted_global)} 个）",
                        ),
                        "dim",
                    )
                )
            print()
            print(style(t("Accounts", "账号"), "bold"))
            width = max(disp_width(r["heading"]) for r in self.rows)
            for r in self.rows:
                n = len(r["todo"])
                count = t(f"existing {r['known']} · to add {n}", f"已有 {r['known']} · 待补 {n}")
                dot = style(sym("dot"), "green" if n and r["template"] else "dim")
                print(f"  {dot} {pad(r['heading'], width)}  {style(count, 'bold' if n else 'dim')}")
                if n and r["source"] == "borrowed":
                    print(
                        style(
                            "      · "
                            + t(
                                "No sessions in this account yet; borrowing a template from another account",
                                "该账号下暂无会话，借用其他账号的会话作为模板",
                            ),
                            "dim",
                        )
                    )
                if n and r["source"] == "none":
                    print(
                        "      "
                        + style(
                            f"{sym('warn')} "
                            + t(
                                "No session to use as a template; skipped. Create a Code session in the desktop app first.",
                                "找不到任何可用作模板的会话，跳过。请先在桌面端建一个 Code 会话。",
                            ),
                            "yellow",
                        )
                    )
            if pending:
                print()
                print(style(t(f"Sessions to add ({len(pending)})", f"待补会话（{len(pending)} 个）"), "bold"))
                for s, targets in pending[:limit]:
                    print(self.session_line(s, targets, verbose))
                if limit and len(pending) > limit:
                    more = len(pending) - limit
                    print(
                        style(
                            t(
                                f"  {ell} and {more} more (pass -v to list all)",
                                f"  {ell} 还有 {more} 个（加 -v 查看全部）",
                            ),
                            "dim",
                        )
                    )

        included = [s for s, _ in pending if s["id"] in self.recent]
        if self.held_back or included:
            print()
        if self.held_back:
            warn(
                t(
                    f"Skipped {len(self.held_back)} session(s) written in the last 2 minutes (probably still running). "
                    "Pass --include-recent to sync them anyway.",
                    f"跳过 {len(self.held_back)} 个最近 2 分钟内仍在写入的会话（可能正在运行）。"
                    "如需一并同步，加 --include-recent。",
                )
            )
        if included:
            warn(
                t(
                    f"Included {len(included)} session(s) still being written. Their title and last-activity "
                    "time may be out of date, and don't type into the same session from the CLI and the desktop app "
                    "at the same time.",
                    f"包含了 {len(included)} 个最近仍在写入的会话。它们的标题和最后活动时间可能不是最新的，"
                    "也不要在 CLI 和桌面端同时向同一个会话输入。",
                )
            )

        if self.purge is not None:
            plan, running, filtered = self.purge
            print()
            print(
                style(
                    t(f"Purge archived sessions: {len(plan)}", f"清除已归档会话：{len(plan)} 个"),
                    "bold",
                    "red" if plan else "dim",
                )
            )
            if not quiet:
                for cid, title, idx_hits, cli_hits in plan[:limit]:
                    files = t(
                        f"{len(idx_hits)} index file(s), {len(cli_hits)} CLI file(s)",
                        f"索引 {len(idx_hits)} 个，CLI 文件 {len(cli_hits)} 个",
                    )
                    print(f"  {style(sym('del'), 'red')} {fit(str(title), 50)}  " + style(f"[{cid}]  {files}", "dim"))
                if limit and len(plan) > limit:
                    more = len(plan) - limit
                    print(
                        style(
                            t(
                                f"  {ell} and {more} more (pass -v to list all)",
                                f"  {ell} 还有 {more} 个（加 -v 查看全部）",
                            ),
                            "dim",
                        )
                    )
            if running:
                print(
                    style(
                        t(
                            f"  (skipped {len(running)} archived session(s) still being written)",
                            f"  （跳过 {len(running)} 个最近仍在写入的归档会话）",
                        ),
                        "dim",
                    )
                )
            if filtered:
                print(
                    style(
                        t(
                            f"  (skipped {filtered} archived session(s) because of --exclude/--project)",
                            f"  （按 --exclude/--project 跳过 {filtered} 个归档会话）",
                        ),
                        "dim",
                    )
                )

        print()
        if not self.has_work():
            print(
                style(
                    f"{sym('ok')} " + t("Everything is in sync; nothing to add.", "已全部同步，没有需要补的会话。"),
                    "green",
                )
            )
            return
        parts = []
        if self.n_files():
            k = sum(1 for r in self.writable() if r["todo"])
            parts.append(
                t(
                    f"add {len(pending)} session(s) ({self.n_files()} index file(s) in {k} account(s))",
                    f"补 {len(pending)} 个会话（{k} 个账号共 {self.n_files()} 个索引文件）",
                )
            )
        if self.purge_plan():
            parts.append(
                t(
                    f"delete {len(self.purge_plan())} archived session(s)",
                    f"彻底删除 {len(self.purge_plan())} 个已归档会话",
                )
            )
        print(style(t("Plan: ", "计划：") + t("; ", "；").join(parts), "bold"))

    def to_json(self, applied, purged, written):
        def iso(ms):
            return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        purge = None
        if self.purge is not None:
            plan, running, filtered = self.purge
            purge = {
                "sessions": [
                    {"id": cid, "title": title, "index_files": len(idx), "cli_files": len(cli)}
                    for cid, title, idx, cli in plan
                ],
                "skipped_running": sorted(running),
                "skipped_filtered": filtered,
                "performed": bool(applied and purged and plan),
            }
        return {
            "version": __version__,
            "mode": "apply" if applied else "preview",
            "cli_sessions": len(self.sessions),
            "skipped_invalid": self.skipped,
            "accounts": [
                {
                    "account": r["acct"],
                    "org": r["org"],
                    "label": r["label"],
                    "existing": r["known"],
                    "to_add": [s["id"] for s in r["todo"]] if r["template"] else [],
                    "template": r["source"],
                }
                for r in self.rows
            ],
            "sessions": [
                {
                    "id": s["id"],
                    "title": s["title"],
                    "cwd": s["cwd"],
                    "last_activity": iso(s["lastActivityAt"]),
                    "still_being_written": s["id"] in self.recent,
                    "accounts": [f"{r['acct']}/{r['org']}" for r in targets],
                }
                for s, targets in self.pending()
            ],
            "skipped_recent": sorted(self.held_back),
            "purge": purge,
            "written": written,
        }


# ---------------------------------------------------------------- 主流程


def apply_lang_flag(argv):
    """在构建帮助文本之前先读取 --lang，让 --help 和后续输出都使用指定语言。
    非法取值交给正式的解析器报错。"""
    global _LANG_ZH
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    known, _ = pre.parse_known_args(argv)
    if known.lang in ("en", "zh"):
        _LANG_ZH = known.lang == "zh"


def main(argv=None):
    use_safe_stdio()
    apply_lang_flag(argv)
    ap = argparse.ArgumentParser(
        prog="claude-session-sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=t(
            "Sync local Claude Code CLI sessions into the Claude desktop app's Code sidebar (multi-account).\n"
            "Archived sessions and sessions deleted in the desktop app are skipped. "
            "Previews by default; pass --apply to write.\n"
            # 这一行固定用中文：给终端设成英文的中文用户看
            "中文输出：加 --lang zh，或设置环境变量 CLAUDE_SESSION_SYNC_LANG=zh",
            "把本地 Claude Code CLI 会话同步到 Claude 桌面端 Code 标签页的侧边栏（支持多账号）。\n"
            "自动排除已归档、桌面端已删除的会话。默认只预览，加 --apply 才写入。\n"
            "English output: pass --lang en, or set CLAUDE_SESSION_SYNC_LANG=en",
        ),
        epilog=t(
            """\
Typical workflow:
  %(prog)s                          preview what each account would get (in a terminal, asks to write)
  %(prog)s -i                       pick accounts and sessions from a list, then write
  %(prog)s --apply                  write (quit the desktop app completely first, e.g. ⌘Q)
  %(prog)s --undo                   undo the most recent write/purge

Examples:
  %(prog)s --account <account-id> --project myapp --apply
                                    sync only the myapp project into one account
  %(prog)s --exclude-file ~/no-sync.txt --apply
                                    leave out the sessions listed in a file
  %(prog)s --purge-archived         preview which archived sessions would be deleted
  %(prog)s --purge-archived --apply sync, then permanently delete archived sessions (undoable)

Notes:
  * Quit the desktop app completely before writing, and reopen it afterwards.
  * Journals and backups live in ~/.claude-desktop-sync/; --undo steps back one run at a time.
  * The desktop index format is undocumented and may break with desktop updates.
  * Output language: --lang, then CLAUDE_SESSION_SYNC_LANG, then LC_ALL / LC_MESSAGES / LANG.
""",
            """\
常用流程：
  %(prog)s                          预览每个账号会补哪些会话（在终端里会问是否写入）
  %(prog)s -i                       从列表里挑选账号和会话后写入
  %(prog)s --apply                  写入（先 ⌘Q 彻底退出桌面端）
  %(prog)s --undo                   撤销最近一次写入/删除

示例：
  %(prog)s --account <账号ID> --project myapp --apply
                                    只把 myapp 项目同步到指定账号
  %(prog)s --exclude-file ~/no-sync.txt --apply
                                    排除列表中的会话
  %(prog)s --purge-archived         预览会被彻底删除的已归档会话
  %(prog)s --purge-archived --apply 同步后彻底删除已归档会话（可 --undo）

注意：
  * 写入前请彻底退出桌面端（菜单栏 ⌘Q），写完重新打开。
  * 日志与备份在 ~/.claude-desktop-sync/，--undo 按时间倒序逐次撤销。
  * 桌面端索引格式无官方文档，桌面端更新后可能失效。
  * 输出语言：依次看 --lang、CLAUDE_SESSION_SYNC_LANG、LC_ALL / LC_MESSAGES / LANG。
""",
        ),
    )
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument(
        "--lang",
        choices=["en", "zh"],
        help=t(
            "output language (default: from CLAUDE_SESSION_SYNC_LANG or the locale)",
            "输出语言（默认：按 CLAUDE_SESSION_SYNC_LANG 或系统语言）",
        ),
    )
    ap.add_argument(
        "--apply", action="store_true", help=t("actually write (default: preview only)", "实际写入（默认只预览）")
    )
    ap.add_argument("--undo", action="store_true", help=t("undo the most recent write", "撤销最近一次写入"))
    ap.add_argument(
        "--show-template",
        action="store_true",
        help=t("print each account's template fields and exit", "显示各账号模板字段后退出"),
    )
    ap.add_argument(
        "--account",
        action="append",
        help=t(
            "only sync this account; an ID prefix such as the 8 characters shown in the output works, "
            "add /<org-id-prefix> to pick one organization (repeatable)",
            "只同步指定账号；可以只写输出里显示的前 8 位，加 /<组织ID前缀> 可只选一个组织（可多次使用）",
        ),
    )
    ap.add_argument(
        "--set-alias",
        action="append",
        metavar="ID=NAME",
        help=t(
            "give an account a display name, e.g. b8073c71=work (ID prefix, optionally /<org-id-prefix>); "
            "an empty name removes it; repeatable, then exits",
            "给账号起显示名称，如 b8073c71=工作号（ID 前缀，可加 /<组织ID前缀>）；名称留空则删除；可多次使用，设置完即退出",
        ),
    )
    ap.add_argument(
        "--project",
        help=t(
            "only handle sessions whose working directory contains this string (sync and purge)",
            "只处理工作目录包含该字符串的会话（同步和清除都生效）",
        ),
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help=t("session ID to skip in sync and purge (repeatable)", "排除的会话ID，同步和清除都跳过（可多次使用）"),
    )
    ap.add_argument(
        "--exclude-file",
        help=t("file with session IDs to exclude, one per line", "排除列表文件，每行一个会话ID"),
    )
    ap.add_argument(
        "--include-recent",
        action="store_true",
        help=t(
            "also sync sessions written in the last 2 minutes (possibly still running); "
            "--purge-archived still skips them",
            "同步时不跳过最近 2 分钟内仍在写入的会话（可能正在运行）；--purge-archived 仍然会跳过",
        ),
    )
    ap.add_argument(
        "--per-account-archive",
        action="store_true",
        help=t(
            "only exclude sessions archived in the same account (default: archived anywhere = excluded everywhere)",
            "只按本账号的归档状态排除（默认：任一账号归档即全部排除）",
        ),
    )
    ap.add_argument(
        "--projects-dir",
        help=t("CLI sessions directory (default: ~/.claude/projects)", "CLI 会话目录（默认 ~/.claude/projects）"),
    )
    ap.add_argument(
        "--desktop-root",
        action="append",
        help=t("path to a claude-code-sessions directory (repeatable)", "手动指定 claude-code-sessions 目录"),
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help=t(
            "run --apply / --undo even while the desktop app is running (not recommended)",
            "桌面端运行中也强制执行 --apply / --undo（不推荐）",
        ),
    )
    ap.add_argument(
        "--purge-archived",
        action="store_true",
        help=t(
            "after syncing, permanently delete archived sessions (index + CLI .jsonl, backed up, undoable)",
            "同步后彻底删除已归档会话（索引 + CLI 的 .jsonl，备份到日志目录，可 --undo）",
        ),
    )
    ap.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help=t(
            "pick accounts and sessions from a numbered list before writing",
            "写入前从编号列表里挑选账号和会话",
        ),
    )
    ap.add_argument(
        "--no-input",
        action="store_true",
        help=t(
            "never ask questions, even in a terminal (behaves like a script)",
            "即使在终端里也不提问（和脚本里运行一样）",
        ),
    )
    verbosity = ap.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help=t("only print the summary", "只输出汇总"))
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=t("print every session and extra statistics", "列出全部会话和更多统计"),
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help=t(
            "print the result as JSON (implies --no-input and no colors)",
            "以 JSON 输出结果（同时不提问、不带颜色）",
        ),
    )
    ap.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help=t(
            "colored output (default: auto, only in a terminal; NO_COLOR is honored)",
            "彩色输出（默认 auto：只在终端里启用，遵循 NO_COLOR）",
        ),
    )
    args = ap.parse_args(argv)
    setup_terminal("never" if args.json else args.color)
    interactive = not args.no_input and not args.json and is_interactive()
    if args.interactive and not interactive:
        sys.exit(
            t(
                "--interactive needs a terminal and cannot be combined with --json or --no-input.",
                "--interactive 需要在终端里运行，且不能和 --json、--no-input 一起用。",
            )
        )

    if args.undo:
        undo(args.force, interactive)
        return

    projects = cli_projects_dir(args.projects_dir)
    if not projects.is_dir():
        sys.exit(t(f"CLI sessions directory not found: {projects}", f"找不到 CLI 会话目录：{projects}"))

    roots = [Path(r).expanduser() for r in args.desktop_root] if args.desktop_root else candidate_desktop_roots()
    if not roots:
        sys.exit(
            t(
                "Cannot find the desktop app's claude-code-sessions directory. "
                "Create a Code session in the desktop app first, or pass --desktop-root.",
                "找不到桌面端的 claude-code-sessions 目录。请先在桌面端建一个 Code 会话，或用 --desktop-root 指定路径。",
            )
        )

    accts = account_dirs(roots)
    if not accts:
        sys.exit(t("No account directories found.", "没有找到任何账号目录。"))
    if args.set_alias:
        set_aliases(args.set_alias, accts)
        return
    if args.account:
        wanted = [resolve_account_key(v, accts) for v in args.account]
        accts = [a for a in accts if any(a[1] == ac and (og is None or a[2] == og) for ac, og in wanted)]

    book = load_account_book()
    cli_acct = cli_login_account()

    excluded = set(args.exclude) | read_exclude_file(default_exclude_file())
    if args.exclude_file:
        excluded |= read_exclude_file(Path(args.exclude_file).expanduser(), must_exist=True)

    # 读取各账号索引
    indexes = {}
    archived_global = set()
    deleted_global = set()
    for _root, acct, org, d in accts:
        entries = load_index(d)
        deleted, archived_local = load_markers(d)
        known = {e.get("cliSessionId") for _, e in entries if isinstance(e, dict)}
        archived = {
            e.get("cliSessionId")
            for f, e in entries
            if isinstance(e, dict)
            and (is_archived(e) or f.stem in archived_local or e.get("sessionId") in archived_local)
        }
        archived |= deleted  # 桌面端删除过的会话同样不再补
        archived_global |= archived
        deleted_global |= deleted
        indexes[d] = (acct, org, entries, known, archived)

    # 账号目录为空时借用的后备模板（优先同一 root 下的）
    fallback = {}
    for root, _acct, _org, d in accts:
        if root not in fallback:
            tpl = pick_template(indexes[d][2])
            if tpl:
                fallback[root] = tpl
    any_fallback = next(iter(fallback.values()), None)
    if any_fallback is None:  # --account 过滤后可能没有，再从全部账号里找
        for _, _, _, d in account_dirs(roots):
            any_fallback = pick_template(load_index(d))
            if any_fallback:
                break
    root_of = {d: root for root, _, _, d in accts}

    if args.show_template:
        for _d, (acct, org, entries, _, _) in indexes.items():
            tpl = pick_template(entries)
            label = account_label(acct, org, book, cli_acct)
            label = f" {label}" if label else ""
            print(
                t(
                    f"\n[{acct}/{org}]{label} {len(entries)} index file(s)",
                    f"\n[{acct}/{org}]{label} {len(entries)} 个索引文件",
                )
            )
            if tpl:
                print(
                    json.dumps(
                        {
                            k: (v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}>")
                            for k, v in tpl.items()
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(t("  (no usable template)", "  （无可用模板）"))
        return

    sessions, skipped = scan_cli_sessions(projects)
    if args.project:
        sessions = [s for s in sessions if args.project in s["cwd"]]
    now = time.time()
    recent = {s["id"] for s in sessions if now - s["mtime"] < RUNNING_GRACE_SEC}
    skip_recent = set() if args.include_recent else recent

    # 每个账号要补哪些会话
    rows, held_back = [], set()
    for d, (acct, org, entries, known, archived) in indexes.items():
        template, source = pick_template(entries), "own"
        if not template:
            template = fallback.get(root_of[d]) or any_fallback
            source = "borrowed" if template else "none"
        arch = archived if args.per_account_archive else archived_global
        missing = [s for s in sessions if s["id"] not in known and s["id"] not in arch and s["id"] not in excluded]
        held_back |= {s["id"] for s in missing if s["id"] in skip_recent}
        rows.append(
            {
                "dir": d,
                "acct": acct,
                "org": org,
                "known": len(known),
                "todo": [s for s in missing if s["id"] not in skip_recent],
                "template": template,
                "source": source,
                "label": account_label(acct, org, book, cli_acct),
                "heading": account_heading(acct, org, book, cli_acct),
                "short": account_short(acct, org, book, cli_acct),
            }
        )

    if args.interactive:
        pick_interactively(rows)

    purge = None
    if args.purge_archived:
        # 清除时始终覆盖全部账号，避免 --account 过滤后其他账号残留指向已删 .jsonl 的索引；
        # --exclude 和 --project 仍然生效，正在写入的会话按 .jsonl 修改时间单独判断
        all_idx, all_archived = {}, set()
        for _root, acct, org, d in account_dirs(roots):
            entries = load_index(d)
            _, archived_local = load_markers(d)
            all_archived |= {
                e.get("cliSessionId")
                for f, e in entries
                if isinstance(e, dict)
                and (is_archived(e) or f.stem in archived_local or e.get("sessionId") in archived_local)
            }
            all_idx[d] = (acct, org, entries, None, None)
        purge = plan_purge(all_idx, all_archived, projects, excluded=excluded, project=args.project)

    report = SyncReport(rows, sessions, skipped, recent, held_back, purge, archived_global, deleted_global)
    if not args.json:
        report.print_plan(verbose=args.verbose, quiet=args.quiet)

    # 决定是否写入：--apply，或者在终端里预览后确认
    apply, do_purge = args.apply, bool(purge and purge[0])
    if interactive and report.has_work():
        if not apply:
            print()
            apply = confirm(t("Write now?", "现在写入？"))
        if apply and do_purge:
            answer = ask(
                t(
                    f"Type yes to permanently delete {len(purge[0])} archived session(s) "
                    "(backed up, --undo restores them): ",
                    f"输入 yes 确认彻底删除 {len(purge[0])} 个已归档会话（会先备份，可 --undo 还原）：",
                )
            )
            if (answer or "").strip().lower() != "yes":
                do_purge = False
                warn(t("Purge skipped; archived sessions are kept.", "已跳过清除，已归档会话保留。"))

    written = None
    if apply:
        ensure_desktop_stopped(args.force, interactive)
        if remember_cli_account(book, cli_acct):
            save_account_book(book)
        created, moved = [], []
        for r in rows:
            if not r["template"]:
                continue
            for s in r["todo"]:
                new_id, entry = build_entry(r["template"], s)
                out = r["dir"] / f"{new_id}.json"
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(entry, fh, ensure_ascii=False, indent=2)
                created.append(out)
        stamp = new_stamp()
        trash = JOURNAL_DIR / f"trash-{stamp}"
        if do_purge:
            c2, moved = execute_purge(purge[0], trash)
            created += c2
        journal = write_journal(created, moved, stamp) if created or moved else None
        written = {"created": len(created), "moved": len(moved), "journal": str(journal) if journal else None}
        if not args.json:
            print()
            if journal and moved:
                print(
                    style(sym("ok"), "green"),
                    t(
                        f"Created {len(created)} file(s), moved {len(moved)} file(s)/folder(s) to {trash}. "
                        f"Journal: {journal}",
                        f"已写入/新建 {len(created)} 个文件，移除 {len(moved)} 个文件/目录（备份在 {trash}）。"
                        f"日志：{journal}",
                    ),
                )
            elif journal:
                print(
                    style(sym("ok"), "green"),
                    t(
                        f"Created {len(created)} file(s). Journal: {journal}",
                        f"已写入/新建 {len(created)} 个文件。日志：{journal}",
                    ),
                )
            else:
                print(t("Nothing to write.", "没有需要写入的会话。"))
            if journal:
                print(
                    t(
                        "Reopen the desktop app to check. If anything looks wrong, run --undo.",
                        "请重新打开桌面端查看；如有问题运行 --undo 撤销。",
                    )
                )
    elif not args.json and report.has_work():
        if interactive:
            print(t("Nothing was written.", "没有写入任何文件。"))
        else:
            print(
                style(
                    t(
                        "\n(Preview only; nothing was written. Run again with --apply to write.)",
                        "\n（预览模式，未写入任何文件。确认无误后加 --apply 执行。）",
                    ),
                    "dim",
                )
            )

    if args.json:
        print(json.dumps(report.to_json(apply, do_purge, written), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
