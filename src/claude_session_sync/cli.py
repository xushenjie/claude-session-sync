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
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

__version__ = "0.1.1"

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


def ensure_desktop_stopped(force):
    """桌面端运行时直接退出，不做任何写入。"""
    if desktop_running() and not force:
        sys.exit(
            t(
                "The Claude desktop app is running. Nothing was written. "
                "Quit it completely (from the menu bar / system tray) and try again, or pass --force.",
                "检测到桌面端正在运行，这次没有写入。请彻底退出（托盘/菜单栏里退出）后重试，或加 --force。",
            )
        )


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


def undo(force=False):
    journals = sorted(JOURNAL_DIR.glob("journal-*.json")) if JOURNAL_DIR.exists() else []
    if not journals:
        print(t("Nothing to undo.", "没有可撤销的记录。"))
        return
    ensure_desktop_stopped(force)
    j = journals[-1]
    data = json.loads(j.read_text(encoding="utf-8"))
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
        t(
            f"Undid {j.name}: removed {n} created file(s), restored {m} file(s)/folder(s). Restart the desktop app.",
            f"已撤销 {j.name}：删除了 {n} 个新建文件，还原了 {m} 个文件/目录。请重启桌面端。",
        )
    )


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


def purge_archived(indexes, archived_ids, projects, apply, trash, excluded=(), project=None):
    """彻底删除已归档会话，模仿桌面端"删除"的效果：
      * 删除所有账号里指向该会话的 local_*.json 索引
      * 从 archived-sessions.idx 中移除对应条目
      * 在原账号目录写入 deleted_<cliSessionId> 标记（防止被再次同步）
      * 删除 CLI 的 <会话ID>.jsonl 及同名子目录（子代理记录等）
    所有被删/被改的文件都先移动到 trash 目录，可用 --undo 还原。
    excluded 里的会话、工作目录不包含 project 的会话、最近仍在写入的会话都会跳过。
    返回 (created, moved)。
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
        plan.append((cid, idx_hits, cli_hits))

    print(t(f"\n=== Purge archived sessions: {len(plan)} ===", f"\n=== 清除已归档会话：{len(plan)} 个 ==="))
    for cid, idx_hits, cli_hits in plan:
        title = next((e.get("title") for _, _, e in idx_hits if e.get("title")), cid)
        print(
            t(
                f"  - {title}  [{cid}]  {len(idx_hits)} index file(s), {len(cli_hits)} CLI file(s)",
                f"  - {title}  [{cid}]  索引 {len(idx_hits)} 个，CLI 文件 {len(cli_hits)} 个",
            )
        )
        if not apply:
            continue
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
    if running:
        print(
            t(
                f"  (skipped {len(running)} archived session(s) still being written)",
                f"  （跳过 {len(running)} 个最近仍在写入的归档会话）",
            )
        )
    if filtered:
        print(
            t(
                f"  (skipped {filtered} archived session(s) because of --exclude/--project)",
                f"  （按 --exclude/--project 跳过 {filtered} 个归档会话）",
            )
        )
    return created, moved


# ---------------------------------------------------------------- 主流程


def main(argv=None):
    use_safe_stdio()
    ap = argparse.ArgumentParser(
        prog="claude-session-sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=t(
            "Sync local Claude Code CLI sessions into the Claude desktop app's Code sidebar (multi-account).\n"
            "Archived sessions and sessions deleted in the desktop app are skipped. "
            "Previews by default; pass --apply to write.",
            "把本地 Claude Code CLI 会话同步到 Claude 桌面端 Code 标签页的侧边栏（支持多账号）。\n"
            "自动排除已归档、桌面端已删除的会话。默认只预览，加 --apply 才写入。",
        ),
        epilog=t(
            """\
Typical workflow:
  %(prog)s                          preview what each account would get
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
  * Output language follows LANG; set CLAUDE_SESSION_SYNC_LANG=en or zh to override.
""",
            """\
常用流程：
  %(prog)s                          预览每个账号会补哪些会话
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
  * 输出语言跟随 LANG，可用 CLAUDE_SESSION_SYNC_LANG=en 或 zh 强制指定。
""",
        ),
    )
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
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
        help=t("only sync this account ID (repeatable)", "只同步指定账号ID（可多次使用）"),
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
    args = ap.parse_args(argv)

    if args.undo:
        undo(args.force)
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
    if args.account:
        accts = [a for a in accts if a[1] in args.account]
    if not accts:
        sys.exit(t("No account directories found.", "没有找到任何账号目录。"))

    excluded = set(args.exclude)
    if args.exclude_file:
        excluded |= {
            ln.strip() for ln in Path(args.exclude_file).read_text(encoding="utf-8").splitlines() if ln.strip()
        }

    # 读取各账号索引
    indexes = {}
    archived_global = set()
    deleted_global = set()
    archived_only_global = set()
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
        archived_only_global |= archived
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
            print(t(f"\n[{acct}/{org}] {len(entries)} index file(s)", f"\n[{acct}/{org}] {len(entries)} 个索引文件"))
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
    included_recent = set()

    print(
        t(
            f"CLI sessions: {len(sessions)} usable (skipped {skipped} empty or without a working directory)",
            f"CLI 会话：{len(sessions)} 个有效（跳过 {skipped} 个空会话/无 cwd 的记录）",
        )
    )
    print(
        t(
            f"Desktop account directories: {len(indexes)}; archived/deleted in any account: "
            f"{len(archived_global)} (deletion markers: {len(deleted_global)})\n",
            f"桌面端账号目录：{len(indexes)} 个；任一账号中已归档/已删除："
            f"{len(archived_global)} 个（其中删除标记 {len(deleted_global)} 个）\n",
        )
    )

    if args.apply:
        ensure_desktop_stopped(args.force)

    created = []
    for d, (acct, org, entries, known, archived) in indexes.items():
        template = pick_template(entries)
        arch = archived if args.per_account_archive else archived_global
        todo = [
            s
            for s in sessions
            if s["id"] not in known and s["id"] not in arch and s["id"] not in excluded and s["id"] not in skip_recent
        ]
        print(
            t(
                f"[{acct[:8]}…/{org[:8]}…] existing {len(known)}, to add {len(todo)}",
                f"[{acct[:8]}…/{org[:8]}…] 已有 {len(known)}，待补 {len(todo)}",
            )
        )
        if not template:
            template = fallback.get(root_of[d]) or any_fallback
            if template and todo:
                print(
                    t(
                        "  · No sessions in this account yet; borrowing a template from another account",
                        "  · 该账号下暂无会话，借用其他账号的会话作为模板",
                    )
                )
        if not template:
            if todo:
                print(
                    t(
                        "  ! No session to use as a template; skipped. Create a Code session in the desktop app first.",
                        "  ! 找不到任何可用作模板的会话，跳过。请先在桌面端建一个 Code 会话。",
                    )
                )
            continue
        for s in sorted(todo, key=lambda x: -x["lastActivityAt"]):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["lastActivityAt"] / 1000))
            mark = ""
            if s["id"] in recent:
                included_recent.add(s["id"])
                mark = t("  [still being written]", "  （最近仍在写入）")
            print(f"  + {when}  {s['title']}  ({s['cwd']}){mark}")
            if args.apply:
                new_id, entry = build_entry(template, s)
                out = d / f"{new_id}.json"
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(entry, fh, ensure_ascii=False, indent=2)
                created.append(out)

    if skip_recent:
        print(
            t(
                f"\nSkipped {len(skip_recent)} session(s) written in the last 2 minutes (probably still running). "
                "Pass --include-recent to sync them anyway.",
                f"\n跳过 {len(skip_recent)} 个最近 2 分钟内仍在写入的会话（可能正在运行）。"
                "如需一并同步，加 --include-recent。",
            )
        )
    if included_recent:
        print(
            t(
                f"\n! Included {len(included_recent)} session(s) still being written. Their title and last-activity "
                "time may be out of date, and don't type into the same session from the CLI and the desktop app "
                "at the same time.",
                f"\n! 包含了 {len(included_recent)} 个最近仍在写入的会话。它们的标题和最后活动时间可能不是最新的，"
                "也不要在 CLI 和桌面端同时向同一个会话输入。",
            )
        )

    moved = []
    stamp = new_stamp()
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
        c2, moved = purge_archived(
            all_idx,
            all_archived,
            projects,
            args.apply,
            JOURNAL_DIR / f"trash-{stamp}",
            excluded=excluded,
            project=args.project,
        )
        created += c2

    if args.apply:
        if created or moved:
            j = write_journal(created, moved, stamp)
            trash = JOURNAL_DIR / f"trash-{stamp}"
            if moved:
                print(
                    t(
                        f"\nCreated {len(created)} file(s), moved {len(moved)} file(s)/folder(s) to {trash}. "
                        f"Journal: {j}",
                        f"\n已写入/新建 {len(created)} 个文件，移除 {len(moved)} 个文件/目录（备份在 {trash}）。日志：{j}",
                    )
                )
            else:
                print(
                    t(
                        f"\nCreated {len(created)} file(s). Journal: {j}",
                        f"\n已写入/新建 {len(created)} 个文件。日志：{j}",
                    )
                )
            print(
                t(
                    "Reopen the desktop app to check. If anything looks wrong, run --undo.",
                    "请重新打开桌面端查看；如有问题运行 --undo 撤销。",
                )
            )
        else:
            print(t("\nNothing to write.", "\n没有需要写入的会话。"))
    else:
        print(
            t(
                "\n(Preview only; nothing was written. Run again with --apply to write.)",
                "\n（预览模式，未写入任何文件。确认无误后加 --apply 执行。）",
            )
        )


if __name__ == "__main__":
    main()
