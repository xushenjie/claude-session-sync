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

__version__ = "0.3.2"

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
_ICONS = "symbols"  # symbols | emoji | ascii，见 --icons
_BOXES = False  # 是否用框线面板和表格，见 --layout
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
    "todo": ("↓", "v"),
    "star": ("★", "*"),
    "run": ("⏵", ">"),
    "tip": ("›", ">"),
    "group": ("▸", ">"),
    "sep": ("│", "|"),
    "minus": ("−", "-"),
    "cur": ("●", "*"),  # 当前 CLI 登录的账号
    "acct": ("●", "o"),  # 其他账号
    "sel": ("❯", ">"),
}
# --icons emoji 时替换的符号；只选终端里固定占两格的 emoji，⚠️ 靠 U+FE0F 变成两格
EMOJI = {
    "ok": "✅",
    "warn": "⚠️",
    "err": "❌",
    "add": "➕",
    "del": "🧹",
    "minus": "🧹",
    "todo": "📥",
    "star": "⭐",
    "run": "⏳",
    "tip": "💡",
    "group": "📁",
    "cur": "🟢",
    "acct": "⚪",
}
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


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


def setup_terminal(color="auto", icons="symbols", layout="plain"):
    """layout：auto 时只在终端里画框线；管道和日志里保持纯文本，方便复制。"""
    global _COLOR, _UNICODE, _ICONS, _BOXES
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(u for u, _ in SYMBOLS.values()).encode(enc)
        _UNICODE = True
    except (LookupError, UnicodeEncodeError):
        _UNICODE = False
    _ICONS = icons
    if icons == "emoji":
        try:
            "".join(EMOJI.values()).encode(enc)
        except (LookupError, UnicodeEncodeError):
            _ICONS = "symbols"
    if color == "never":
        _COLOR = False
    elif color == "always":
        _enable_windows_ansi()
        _COLOR = True
    else:
        isatty = getattr(sys.stdout, "isatty", lambda: False)()
        _COLOR = isatty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb" and _enable_windows_ansi()
    if layout == "auto":
        isatty = getattr(sys.stdout, "isatty", lambda: False)()
        _BOXES = isatty and os.environ.get("TERM") != "dumb"
    else:
        _BOXES = layout == "boxed"


def style(text, *names):
    if not _COLOR or not names:
        return text
    codes = ";".join(ANSI[n] for n in names)
    return f"\033[{codes}m{text}\033[0m"


def sym(name):
    uni, ascii_ = SYMBOLS[name]
    if not _UNICODE or _ICONS == "ascii":
        return ascii_
    if _ICONS == "emoji" and name in EMOJI:
        return EMOJI[name]
    return uni


def char_width(ch):
    if unicodedata.combining(ch) or ch in "\u200d\ufe0e":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(text):
    """终端里的显示宽度：中日韩字符和 emoji 占两格，颜色控制码不占宽度。
    U+FE0F 把前一个本来占一格的字符（如 ⚠）变成两格的 emoji。"""
    total, prev = 0, 0
    for ch in ANSI_RE.sub("", text):
        if ch == "\ufe0f":
            total += 1 if prev == 1 else 0
            prev = 2
            continue
        prev = char_width(ch)
        total += prev
    return total


def ok(text):
    return style(text, "green")


def todo(text):
    return style(text, "cyan")


def danger(text):
    return style(text, "red")


def caution(text):
    return style(text, "yellow")


def muted(text):
    return style(text, "dim")


def tip(text):
    print(muted(f"  {sym('tip')} {text}"))


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


def rpad(text, width):
    """右对齐到 width 个显示宽度。"""
    return " " * max(0, width - disp_width(text)) + text


def term_width():
    return max(60, shutil.get_terminal_size((100, 24)).columns)


# ---------------------------------------------------------------- 面板与表格
#
# 单元格用 (文字, 样式...) 表示：先按显示宽度截断/补齐，再上色，颜色控制码不会打乱对齐。

PANEL_MAX = 100
BOX_UNICODE = {
    "tl": "╭",
    "tr": "╮",
    "bl": "╰",
    "br": "╯",
    "h": "─",
    "v": "│",
    "l": "├",
    "r": "┤",
    "t": "┬",
    "b": "┴",
    "x": "┼",
}
BOX_ASCII = {
    "tl": "+",
    "tr": "+",
    "bl": "+",
    "br": "+",
    "h": "-",
    "v": "|",
    "l": "+",
    "r": "+",
    "t": "+",
    "b": "+",
    "x": "+",
}


def box_chars():
    return BOX_UNICODE if _UNICODE and _ICONS != "ascii" else BOX_ASCII


def panel_width():
    return min(PANEL_MAX, term_width())


def cell(text, *styles):
    return (str(text), *styles)


def render_cell(c, width, align="l"):
    text, styles = c[0], c[1:]
    text = fit(text, width)
    padded = rpad(text, width) if align == "r" else pad(text, width)
    if not styles:
        return padded
    # 只给文字上色，补齐用的空格不上色（反色高亮时例外，见 render_line）
    lead = padded[: len(padded) - len(padded.lstrip(" "))] if align == "r" else ""
    body = padded.strip(" ") if align == "r" else padded.rstrip(" ")
    trail = "" if align == "r" else padded[len(body) :]
    return lead + style(body, *styles) + trail


def render_line(parts, width):
    """把一行里的若干 (cell, 宽度或 None) 依次拼起来；宽度为 None 的部分占满剩余宽度。"""
    assert sum(w is None for _, w in parts) <= 1, "render_line: at most one flexible part"
    fixed = sum(w for _, w in parts if w is not None) + 2 * (len(parts) - 1)
    out = []
    for c, w in parts:
        out.append(render_cell(c, w if w is not None else max(1, width - fixed)))
    line = "  ".join(out)
    return line + " " * max(0, width - disp_width(line))


def panel(title, lines, right="", title_styles=("bold",), width=None):
    """带标题的面板。lines 是已经上好色的字符串，宽度不超过 width - 4。"""
    b = box_chars()
    width = width or panel_width()
    inner = width - 2
    head = f"{b['h']} {title} " if title else b["h"]
    tail = f" {right} {b['h']}" if right else ""
    fill = max(0, inner - disp_width(head) - disp_width(tail))
    top = muted(b["tl"] + b["h"]) + (style(f" {title} ", *title_styles) if title else "") + muted(b["h"] * fill)
    top += (muted(f" {right} " + b["h"]) if right else "") + muted(b["tr"])
    out = [top]
    for line in lines:
        out.append(muted(b["v"]) + " " + line + " " * max(0, inner - 2 - disp_width(line)) + " " + muted(b["v"]))
    out.append(muted(b["bl"] + b["h"] * inner + b["br"]))
    return out


def table(title, columns, rows, width=None, right=""):
    """带标题的网格表格。columns：[{"h": 表头, "align": "l"/"r", "flex": 占剩余宽度, "drop": 窄屏时可省略}]；
    rows：每行一个 cell 列表。"""
    b = box_chars()
    width = width or panel_width()
    cols = list(columns)
    keep = list(range(len(cols)))

    def natural(i):
        return max(disp_width(cols[i]["h"]), *(disp_width(r[i][0]) for r in rows)) if rows else disp_width(cols[i]["h"])

    flex = next((i for i, c in enumerate(cols) if c.get("flex")), len(cols) - 1)
    flex_min = 16  # 灵活列先缩到这个宽度（截断加省略号），还放不下才去掉可省略的列

    def total(idx):
        return sum((min(natural(i), flex_min) if i == flex else natural(i)) + 3 for i in idx) + 1

    while total(keep) > width and any(cols[i].get("drop") for i in keep):  # 窄屏：先去掉可省略的列
        keep.remove(next(i for i in keep if cols[i].get("drop")))
    widths = {i: natural(i) for i in keep}
    spare = width - total(keep)
    widths[flex] = max(4, min(widths[flex], flex_min) + spare)  # 灵活列吸收多余或不足的宽度，表格和面板一样宽

    def hline(left, mid, right_):
        return muted(left + mid.join(b["h"] * (widths[i] + 2) for i in keep) + right_)

    def row_line(cells):
        parts = [" " + render_cell(cells[i], widths[i], cols[i].get("align", "l")) + " " for i in keep]
        return muted(b["v"]) + muted(b["v"]).join(parts) + muted(b["v"])

    inner = sum(widths[i] + 3 for i in keep) - 1
    head = f"{b['h']} {title} "
    tail = f" {right} {b['h']}" if right else ""
    top = (
        muted(b["tl"] + b["h"])
        + style(f" {title} ", "bold")
        + muted(b["h"] * max(0, inner - disp_width(head) - disp_width(tail)))
    )
    top += (muted(f" {right} " + b["h"]) if right else "") + muted(b["tr"])
    out = [top, row_line([cell(c["h"], "dim") for c in cols]), hline(b["l"], b["x"], b["r"])]
    out += [row_line(r) for r in rows]
    out.append(hline(b["bl"], b["b"], b["br"]))
    return out


def short_path(path):
    """把家目录缩写成 ~，让路径短一点。"""
    for home in dict.fromkeys((str(HOME), HOME.as_posix())):  # Windows 上记录里的路径可能用正斜杠
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


# ---------------------------------------------------------------- 方向键选择
#
# 只用标准库：POSIX 用 termios 逐键读取，Windows 用 msvcrt。读不了单个按键时（不是终端、
# TERM=dumb、ui = "simple"）退回到输入编号，功能一样。测试里把 _keys 换成按键序列。

_UI = "keys"  # keys | simple，见 config.toml 的 ui
_keys = None  # 测试用：按键名的迭代器

KEY_HINT_ONE = ("↑↓ move · Enter select · Esc back", "↑↓ 移动 · 回车确认 · Esc 返回")
KEY_HINT_MANY = (
    "↑↓ move · Space toggle · a all · / filter · Enter confirm · Esc back",
    "↑↓ 移动 · 空格勾选 · a 全选 · / 筛选 · 回车确认 · Esc 返回",
)
KEY_HINT_FORM = ("↑↓ field · ←→ change · Enter save · Esc discard", "↑↓ 选项 · ←→ 改值 · 回车保存 · Esc 放弃")


def keys_available():
    if _keys is not None:
        return True
    if _UI != "keys" or os.environ.get("TERM") == "dumb" or _input is not input:
        return False  # _input 被替换说明输入来自脚本（测试），按行读取
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return False
    try:
        if os.name == "nt":
            import msvcrt  # noqa: F401
        else:
            import termios  # noqa: F401
    except ImportError:
        return False
    return True


class RawKeys:
    """进入逐键读取模式，退出时一定恢复终端（Ctrl-C、出错也一样）。"""

    def __enter__(self):
        self.saved = None
        if _keys is None and os.name != "nt":
            import termios
            import tty

            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        sys.stdout.write("\033[?25l")  # 隐藏光标
        sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        return False


POSIX_KEYS = {
    "[A": "up",
    "[B": "down",
    "[C": "right",
    "[D": "left",
    "[H": "home",
    "[F": "end",
    "OA": "up",
    "OB": "down",
    "OC": "right",
    "OD": "left",
    "OH": "home",
    "OF": "end",
    "[1~": "home",
    "[4~": "end",
    "[5~": "pgup",
    "[6~": "pgdn",
}
WIN_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end", "I": "pgup", "Q": "pgdn"}


def read_key():
    """读一个按键，返回 up/down/left/right/home/end/pgup/pgdn/enter/space/esc/backspace/tab 或字符本身。"""
    if _keys is not None:
        return next(_keys, "esc")  # 测试用的按键序列用完后当作 Esc
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return WIN_KEYS.get(msvcrt.getwch(), "")
    else:
        import select

        fd = sys.stdin.fileno()
        b = os.read(fd, 1)
        if b == b"\x1b":
            seq = b""
            while select.select([fd], [], [], 0.03)[0]:
                seq += os.read(fd, 1)
                if seq[-1:].isalpha() or seq[-1:] == b"~":
                    break
            return POSIX_KEYS.get(seq.decode("ascii", "replace"), "esc" if not seq else "")
        if b and b[0] >= 0xC0:  # UTF-8 多字节字符
            need = 1 if b[0] < 0xE0 else 2 if b[0] < 0xF0 else 3
            b += os.read(fd, need)
        ch = b.decode("utf-8", "replace")
    if ch == "\x03":
        raise KeyboardInterrupt
    return {
        "\r": "enter",
        "\n": "enter",
        " ": "space",
        "\x1b": "esc",
        "\x7f": "backspace",
        "\x08": "backspace",
        "\t": "tab",
    }.get(ch, ch)


class Screen:
    """在原地重画一块区域：每次先把光标移回区域开头再清到屏幕底部。"""

    def __init__(self):
        self.height = 0

    def draw(self, lines):
        width = term_width()  # 面板和终端一样宽；写满一行再换行不会多折一行
        out = []
        if self.height:
            out.append(f"\033[{self.height}F")
        out.append("\033[J")
        out.append("\n".join(clip(line, width) for line in lines) + "\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self.height = len(lines)

    def clear(self):
        if self.height:
            sys.stdout.write(f"\033[{self.height}F\033[J")
            sys.stdout.flush()
        self.height = 0


def clip(line, width):
    """按显示宽度截断已上色的一行（跳过颜色控制码），避免终端自动折行打乱重画。"""
    if disp_width(line) <= width:
        return line
    out, used, i = [], 0, 0
    while i < len(line):
        m = ANSI_RE.match(line, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        w = char_width(line[i])
        if used + w > width:
            break
        out.append(line[i])
        used += w
        i += 1
    return "".join(out) + ("\033[0m" if _COLOR else "")


def frame(title, hint, body):
    """选择界面的外框：框线布局时画面板，按键提示放在标题栏右侧；否则标题 + 提示两行。"""
    if _BOXES:
        return panel(title, body, right=t(*hint))
    return [style(title, "bold") + "  " + muted(t(*hint))] + body


def visible_rows():
    return max(5, shutil.get_terminal_size((100, 24)).lines - 8)


def window(n, cursor, size):
    """长列表只显示光标附近的一段。"""
    if n <= size:
        return 0, n
    start = min(max(0, cursor - size // 2), n - size)
    return start, start + size


def option_line(o, selected, inner, mark=None, keyed=False):
    """一行选项：光标、快捷键、勾选框、标签、右侧说明。"""
    pointer = style(sym("sel"), "cyan", "bold") if selected else " "
    box = ""
    if keyed:  # 菜单里显示每项的快捷键
        box = muted(pad(o.get("key", ""), 2))
    if mark is not None:
        box += (todo("[x]") if mark else muted("[ ]")) + " "
    label = o["label"]
    note = o.get("note", "")
    left_w = inner - 2 - disp_width(box) - (disp_width(note) + 2 if note else 0)
    text = fit(label, max(8, left_w))
    if selected:
        text = style(text, "bold")
    line = f"{pointer} {box}{text}"
    if note:
        line += " " * max(2, inner - disp_width(line) - disp_width(note)) + muted(note)
    return line


def select_one(title, options, default=0):
    """单选。options：[{"label", "note"?, "key"?, "value"?, "sep"?}]；sep 为分隔行，不可选。
    返回选中项的 value（没有 value 时返回下标）；Esc / q 返回 None。"""
    choices = [i for i, o in enumerate(options) if not o.get("sep")]
    if not choices:
        return None
    if not keys_available():
        return select_one_simple(title, options, default)
    cursor = default if default in choices else choices[0]
    screen = Screen()
    with RawKeys():
        while True:
            inner = (panel_width() if _BOXES else term_width()) - 4
            start, end = window(len(options), cursor, visible_rows())
            body = []
            for i in range(start, end):
                o = options[i]
                keyed = any(x.get("key") for x in options)
                body.append(
                    muted("  " + o["label"]) if o.get("sep") else option_line(o, i == cursor, inner, keyed=keyed)
                )
            screen.draw(frame(title, KEY_HINT_ONE, body))
            key = read_key()
            pos = choices.index(cursor)
            if key in ("up", "k"):
                cursor = choices[(pos - 1) % len(choices)]
            elif key in ("down", "j", "tab"):
                cursor = choices[(pos + 1) % len(choices)]
            elif key in ("home", "pgup"):
                cursor = choices[0]
            elif key in ("end", "pgdn"):
                cursor = choices[-1]
            elif key == "enter":
                break
            elif key in ("esc", "q"):
                screen.clear()
                return None
            else:
                hit = next((i for i in choices if options[i].get("key") == key), None)
                if hit is None and key.isdigit() and 0 < int(key) <= len(choices):
                    hit = choices[int(key) - 1]
                if hit is not None:  # 数字键或快捷键直接选中
                    cursor = hit
                    break
    screen.clear()
    o = options[cursor]
    print(muted(f"{title}: ") + o["label"])
    return o.get("value", cursor)


def select_one_simple(title, options, default=0):
    """select_one 的编号输入版本：每项一个按键（默认是序号），回车选默认项。"""
    print()
    print(style(title, "bold"))
    keyed, n = [], 0
    for i, o in enumerate(options):
        if o.get("sep"):
            print(muted("  " + o["label"]))
            continue
        n += 1
        key = o.get("key") or str(n)
        keyed.append((key, i))
        enter = muted(t("  (Enter)", "  （回车）")) if i == default else ""
        note = muted(f"  {o['note']}") if o.get("note") else ""
        label = style(o["label"], "bold") if i == default else o["label"]
        print(f"  {style(f'[{key}]', 'bold')} {label}{note}{enter}")
    aliases = {"y": "yes", "n": "no"}
    while True:
        answer = ask(t("Choose: ", "请选择："))
        if answer is None:
            return None
        answer = answer.strip().lower()
        if not answer:
            idx = default
        else:
            answer = aliases.get(answer, answer)
            idx = next((i for k, i in keyed if k == answer or options[i].get("alias") == answer), None)
            if idx is None and answer in ("q", "quit"):
                return None
        if idx is not None and not options[idx].get("sep"):
            return options[idx].get("value", idx)
        warn(t("Please choose one of the options above.", "请输入上面列出的选项。"))


def select_many(title, options, selected=None):
    """多选。返回选中项的下标列表；Esc 返回 None。selected 默认全选。"""
    n = len(options)
    marks = set(range(n)) if selected is None else set(selected)
    if not keys_available():
        return select_many_simple(title, options)
    cursor, query, typing = 0, "", False
    screen = Screen()
    with RawKeys():
        while True:
            shown = [i for i in range(n) if query.lower() in options[i]["label"].lower()]
            if shown and cursor not in shown:
                cursor = shown[0]
            inner = (panel_width() if _BOXES else term_width()) - 4
            pos = shown.index(cursor) if shown else 0
            start, end = window(len(shown), pos, visible_rows())
            body = [option_line(options[i], i == cursor, inner, mark=i in marks) for i in shown[start:end]]
            status = t(f"{len(marks)}/{n} selected", f"已选 {len(marks)}/{n}")
            if query or typing:
                status += "   " + t("filter: ", "筛选：") + query + ("▏" if typing else "")
            if start > 0 or end < len(shown):
                status += "   " + t(f"{pos + 1}/{len(shown)}", f"第 {pos + 1}/{len(shown)} 项")
            body.append(muted(status))
            screen.draw(frame(title, KEY_HINT_MANY, body))
            key = read_key()
            if typing:  # 正在输入筛选文字
                if key == "enter" or key == "esc":
                    typing = False
                    if key == "esc":
                        query = ""
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                elif key == "space":
                    query += " "
                continue
            if key in ("up", "k") and shown:
                cursor = shown[(pos - 1) % len(shown)]
            elif key in ("down", "j", "tab") and shown:
                cursor = shown[(pos + 1) % len(shown)]
            elif key in ("pgup", "left") and shown:
                cursor = shown[max(0, pos - visible_rows())]
            elif key in ("pgdn", "right") and shown:
                cursor = shown[min(len(shown) - 1, pos + visible_rows())]
            elif key == "home" and shown:
                cursor = shown[0]
            elif key == "end" and shown:
                cursor = shown[-1]
            elif key == "space" and shown:
                marks ^= {cursor}
            elif key == "a":
                target = set(shown)
                marks = marks - target if target <= marks else marks | target
            elif key == "/":
                typing = True
            elif key == "enter":
                break
            elif key in ("esc", "q"):
                screen.clear()
                return None
    screen.clear()
    print(muted(f"{title}: ") + t(f"{len(marks)}/{n} selected", f"已选 {len(marks)}/{n}"))
    return sorted(marks)


def select_many_simple(title, options):
    print()
    print(style(title, "bold"))
    for i, o in enumerate(options, 1):
        note = muted(f"  {o['note']}") if o.get("note") else ""
        print(f"  {i:>3}. {o['label']}{note}")
    return choose(options, title)


def edit_form(title, fields):
    """设置表单：fields = [{"label", "choices": [(值, 显示文字)], "value": 当前值}]。
    ←→ 切换取值，回车保存（返回 {label: 值}），Esc 放弃（返回 None）。"""
    current = [next((i for i, (v, _) in enumerate(f["choices"]) if v == f["value"]), 0) for f in fields]
    if not keys_available():
        out = {}
        for f, cur in zip(fields, current):
            opts = [{"label": text, "value": v} for v, text in f["choices"]]
            v = select_one_simple(f["label"], opts, default=cur)
            if v is None:
                return None
            out[f["label"]] = v
        return out
    cursor = 0
    screen = Screen()
    label_w = max(disp_width(f["label"]) for f in fields)
    with RawKeys():
        while True:
            body = []
            for i, (f, cur) in enumerate(zip(fields, current)):
                pointer = style(sym("sel"), "cyan", "bold") if i == cursor else " "
                value = f"‹ {f['choices'][cur][1]} ›"
                value = style(value, "cyan", "bold") if i == cursor else value
                body.append(f"{pointer} {pad(f['label'], label_w)}   {value}")
            screen.draw(frame(title, KEY_HINT_FORM, body))
            key = read_key()
            if key in ("up", "k"):
                cursor = (cursor - 1) % len(fields)
            elif key in ("down", "j", "tab"):
                cursor = (cursor + 1) % len(fields)
            elif key in ("left", "h"):
                current[cursor] = (current[cursor] - 1) % len(fields[cursor]["choices"])
            elif key in ("right", "l", "space"):
                current[cursor] = (current[cursor] + 1) % len(fields[cursor]["choices"])
            elif key == "enter":
                break
            elif key in ("esc", "q"):
                screen.clear()
                return None
    screen.clear()
    return {f["label"]: f["choices"][cur][0] for f, cur in zip(fields, current)}


def edit_line(prompt, default=""):
    """单行编辑器（逐键读取）：预先填好 default；←→ 移动光标，退格删除，Home/End，Ctrl-U 清空，
    回车确认，Esc 取消（返回 None）。不依赖 readline（macOS 自带的 libedit 不支持预填）。"""
    buf, pos = list(default), len(default)
    with RawKeys():
        sys.stdout.write("\033[?25h")  # 编辑时需要看到光标
        while True:
            after = disp_width("".join(buf[pos:]))
            sys.stdout.write("\r\033[K" + prompt + "".join(buf) + (f"\033[{after}D" if after else ""))
            sys.stdout.flush()
            key = read_key()
            if key == "enter":
                break
            if key == "esc":
                sys.stdout.write("\r\033[K")
                return None
            if key == "backspace" and pos:
                del buf[pos - 1]
                pos -= 1
            elif key == "left" and pos:
                pos -= 1
            elif key == "right" and pos < len(buf):
                pos += 1
            elif key == "home":
                pos = 0
            elif key == "end":
                pos = len(buf)
            elif key == "\x15":  # Ctrl-U
                buf, pos = [], 0
            elif key == "space" or (len(key) == 1 and key.isprintable()):
                buf.insert(pos, " " if key == "space" else key)
                pos += 1
    print()
    return "".join(buf).strip()


def ask_text(prompt, default=""):
    """输入一行文字。能逐键读取时把默认值预先填进输入框（回车保留、退格修改）；
    否则提示默认值，直接回车保留。返回 None 表示取消。"""
    if keys_available():
        return edit_line(prompt, default)
    shown = prompt + (muted(t(f"[Enter keeps “{default}”] ", f"［回车保留“{default}”］")) if default else "")
    answer = ask(shown)
    if answer is None:
        return None
    return answer.strip() or default


def confirm_key(question, default=False):
    """是/否确认：能读单个按键时按 y / n / 回车即可，不用再按回车。"""
    if not keys_available():
        return confirm(question, default)
    hint = "[Y/n]" if default else "[y/N]"
    sys.stdout.write(f"{question} {hint} ")
    sys.stdout.flush()
    with RawKeys():
        while True:
            key = read_key()
            if key in ("y", "Y"):
                result = True
            elif key in ("n", "N", "esc"):
                result = False
            elif key == "enter":
                result = default
            else:
                continue
            break
    print(t("yes", "是") if result else t("no", "否"))
    return result


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


def is_model_reply(o):
    """真正由模型生成的回复（含文本或工具调用）；不算 CLI 本地命令产生的 <synthetic> 占位消息。"""
    msg = o.get("message") or {}
    if o.get("isSidechain") or msg.get("model") == "<synthetic>":
        return False
    content = msg.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") in ("text", "tool_use") for part in content
    )


def command_title(txt):
    """从斜杠命令消息里取出 "/命令 参数" 作为后备标题。"""
    name = re.search(r"<command-name>(.*?)</command-name>", txt, re.S)
    if not name:
        return None
    args = re.search(r"<command-args>(.*?)</command-args>", txt, re.S)
    return f"{name.group(1).strip()} {args.group(1).strip() if args else ''}".strip()


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
    """读取 .jsonl，返回会话信息；没有 cwd、或既没有用户正文也没有模型回复的返回 None。

    以斜杠命令（/implement 等）或 IDE 注入内容开头的会话，用户消息都以 "<" 开头，
    只要模型真的回复过就算有效会话；只执行过 /model、/clear 这类本地命令的仍然跳过。"""
    cwd = None
    first_ts = last_ts = None
    custom_title = summary = ai_title = first_user = first_command = None
    has_user = has_reply = False
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
                elif kind == "ai-title" and o.get("aiTitle"):
                    ai_title = o["aiTitle"]
                elif kind == "assistant" and not has_reply:
                    has_reply = is_model_reply(o)
                elif kind == "user" and not o.get("isSidechain") and not o.get("isMeta"):
                    msg = o.get("message") or {}
                    txt = text_of(msg.get("content")).strip()
                    if txt and not txt.startswith("<"):  # 跳过命令/系统注入内容
                        has_user = True
                        first_user = first_user or txt
                    elif txt:
                        first_command = first_command or command_title(txt)
    except OSError as e:
        print(t(f"  ! Cannot read {path}: {e}", f"  ! 无法读取 {path}: {e}"), file=sys.stderr)
        return None
    if not (has_user or has_reply) or not cwd:
        return None
    mtime_ms = int(path.stat().st_mtime * 1000)
    title = (custom_title or summary or ai_title or first_user or first_command or path.stem).replace("\n", " ").strip()
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
        if not confirm_key(t("Undo it?", "撤销这次运行？")):
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


def is_cli_account(acct, org, cli_acct):
    return bool(cli_acct and cli_acct["account"] == acct and cli_acct["org"] == org)


def account_label(acct, org, book, cli_acct, with_cli=True):
    """返回账号的可读名称（可能为空）。with_cli=False 时不带"当前 CLI 登录"标记。"""
    parts = []
    alias = book["aliases"].get(f"{acct}/{org}") or book["aliases"].get(acct)
    if alias:
        parts.append(alias)
    is_cli = is_cli_account(acct, org, cli_acct)
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
    if is_cli and with_cli:
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
    """今年的只显示月日，省出标题的宽度。"""
    lt = time.localtime(ms / 1000)
    return time.strftime("%m-%d %H:%M" if lt.tm_year == time.localtime().tm_year else "%Y-%m-%d", lt)


def pick_interactively(rows):
    """-i：先选账号（多于一个时），再选会话。直接修改 rows 里的 todo。"""
    writable = [r for r in rows if r["template"] and r["todo"]]
    if not writable:
        return
    if len(writable) > 1:
        options = [
            {
                "label": (r["name"].split(" · ")[0] if r.get("name") else r["ids"])
                + (f"  {r['ids']}" if r.get("name") else ""),
                "note": t(f"to add {len(r['todo'])}", f"待补 {len(r['todo'])}"),
            }
            for r in writable
        ]
        picked = select_many(t("Accounts to sync", "要同步的账号"), options)
        if picked is None:
            sys.exit(t(*CANCELLED))
        for i, r in enumerate(writable):
            if i not in picked:
                r["todo"] = []
        writable = [writable[i] for i in picked]

    order = sorted({s["id"]: s for r in writable for s in r["todo"]}.values(), key=lambda s: -s["lastActivityAt"])
    if not order:
        return
    options = [
        {"label": f"{format_time(s['lastActivityAt'])}  {s['title']}", "note": fit(short_path(s["cwd"]), 32)}
        for s in order
    ]
    picked = select_many(t(f"Sessions to sync ({len(order)})", f"要同步的会话（{len(order)} 个）"), options)
    if picked is None:
        sys.exit(t(*CANCELLED))
    keep = {order[i]["id"] for i in picked}
    for r in writable:
        r["todo"] = [s for s in r["todo"] if s["id"] in keep]
    dropped = [s for s in order if s["id"] not in keep]
    if dropped and confirm_key(
        t(
            f"Remember the {len(dropped)} session(s) you left out, so later runs skip them too?",
            f"记住没选的 {len(dropped)} 个会话，以后运行也自动跳过？",
        )
    ):
        path = add_to_exclude_file(dropped)
        print(
            muted(
                t(f"Saved to {path}; delete a line there to sync it again.", f"已记到 {path}；删掉对应行即可恢复同步。")
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
        return f"{sym('arrow')} {names}"

    def session_line(self, s, targets, verbose=False, title_w=None, mark_col=False):
        """title_w：同一批会话的标题统一占这么宽，后面的标记和目标账号就能对齐成一列。"""
        lead = f"    {sym('add')} "
        when = format_time(s["lastActivityAt"])
        tgt = self.targets_text(targets)
        mark = f"{sym('run')} " + t("still being written", "运行中") if s["id"] in self.recent else ""
        extra = s["id"] if verbose else ""
        fixed = sum(disp_width(x) + 2 for x in (lead, when, tgt, mark, extra) if x)
        title = fit(s["title"], max(16, term_width() - fixed))
        if title_w and (mark or tgt or extra):
            title = pad(title, min(title_w, max(16, term_width() - fixed)))
        parts = [todo(lead) + muted(when), title]
        if mark or (mark_col and (tgt or extra)):
            running = f"{sym('run')} " + t("still being written", "运行中")
            parts.append(caution(pad(mark, disp_width(running))))
        if tgt:
            parts.append(muted(tgt))
        if extra:
            parts.append(muted(extra))
        return "  ".join(parts)

    # ---- 内容：两种布局（框线 / 纯文本）共用

    def stats(self):
        return [
            (t("CLI sessions", "CLI 会话"), len(self.sessions)),
            (t("accounts", "账号"), len(self.rows)),
            (t("archived/deleted", "已归档/删除"), len(self.archived_global)),
        ]

    def verbose_stats(self):
        return t(
            f"skipped {self.skipped} empty or without a working directory; deletion markers: {len(self.deleted_global)}",
            f"跳过 {self.skipped} 个空会话/无 cwd 的记录；删除标记 {len(self.deleted_global)} 个",
        )

    def account_notes(self, r):
        """解释这个账号的数量为什么和别的账号不一样。"""
        notes = []
        if r.get("archived_n"):
            notes.append(t(f"−{r['archived_n']} archived", f"−{r['archived_n']} 已归档"))
        if r.get("excluded_n"):
            notes.append(t(f"−{r['excluded_n']} excluded", f"−{r['excluded_n']} 已排除"))
        if r.get("held_n"):
            notes.append(t(f"−{r['held_n']} running", f"−{r['held_n']} 运行中"))
        if r.get("orphans"):
            notes.append(t(f"+{r['orphans']} orphaned", f"+{r['orphans']} 孤立索引"))
        if r.get("unusable"):
            notes.append(t(f"+{r['unusable']} command-only/empty", f"+{r['unusable']} 仅命令/空会话"))
        return " · ".join(notes)

    def account_cells(self, r):
        """一个账号的各列：图标、名称、ID、已有、待补、说明，都是 cell。"""
        n = len(r["todo"])
        if n and not r["template"]:
            icon = cell(sym("warn"), "yellow")
        elif r.get("is_cli"):
            icon = cell(sym("cur"), "green")
        else:
            icon = cell(sym("acct"), "dim")
        if n and not r["template"]:
            count = cell(f"+{n}", "yellow")
        elif n:
            count = cell(f"+{n}", "cyan", "bold")
        else:
            count = cell("0", "dim")
        note = self.account_notes(r)
        if n and r["source"] == "borrowed":
            note = t("borrows a template", "借用其他账号的模板") + (" · " + note if note else "")
        note = (
            cell(t("no template, skipped", "无模板，跳过"), "yellow")
            if n and r["source"] == "none"
            else cell(note, "dim")
        )
        name = cell(r["name"], "bold") if r.get("name") else cell(r["ids"], "dim")
        ids = cell(r["ids"], "dim") if r.get("name") else cell("")
        return icon, name, ids, cell(r["known"]), count, note

    def cli_tag(self):
        return t("current CLI login", "当前 CLI 登录")

    def warnings(self, pending):
        out = []
        if any(len(r["todo"]) and r["source"] == "none" for r in self.rows):
            out.append(t("Create a Code session in the desktop app first.", "请先在桌面端建一个 Code 会话，作为模板。"))
        if self.held_back:
            out.append(
                t(
                    f"Skipped {len(self.held_back)} session(s) written in the last 2 minutes (probably still running). "
                    "Pass --include-recent to sync them anyway.",
                    f"跳过 {len(self.held_back)} 个最近 2 分钟内仍在写入的会话（可能正在运行）。"
                    "如需一并同步，加 --include-recent。",
                )
            )
        included = [s for s, _ in pending if s["id"] in self.recent]
        if included:
            out.append(
                t(
                    f"Included {len(included)} session(s) still being written. Their title and last-activity "
                    "time may be out of date, and don't type into the same session from the CLI and the desktop app "
                    "at the same time.",
                    f"包含了 {len(included)} 个最近仍在写入的会话。它们的标题和最后活动时间可能不是最新的，"
                    "也不要在 CLI 和桌面端同时向同一个会话输入。",
                )
            )
        return out

    def tips(self):
        out = []
        orphans = sum(r.get("orphans", 0) for r in self.rows)
        if orphans:
            out.append(
                t(
                    f"{orphans} index entr{'y' if orphans == 1 else 'ies'} point to a deleted transcript; "
                    "archive them in the desktop app, then --purge-archived removes them",
                    f"{orphans} 条索引的会话记录已不存在（孤立索引）；在桌面端归档后，--purge-archived 会清理掉",
                )
            )
        journals = sorted(JOURNAL_DIR.glob("journal-*.json")) if JOURNAL_DIR.exists() else []
        if journals:
            out.append(
                t(
                    f"last write {journal_time(journals[-1])}; undo with --undo",
                    f"上次写入 {journal_time(journals[-1])}；撤销用 --undo",
                )
            )
        if not config_file().exists():
            out.append(
                t(
                    "save options you always pass with --save-defaults (e.g. --lang zh --save-defaults)",
                    "常用参数可以用 --save-defaults 保存（如 --lang zh --save-defaults）",
                )
            )
        return out

    def purge_notes(self):
        _, running, filtered = self.purge
        out = []
        if running:
            out.append(
                t(
                    f"skipped {len(running)} archived session(s) still being written",
                    f"跳过 {len(running)} 个最近仍在写入的归档会话",
                )
            )
        if filtered:
            out.append(
                t(
                    f"skipped {filtered} archived session(s) because of --exclude/--project",
                    f"按 --exclude/--project 跳过 {filtered} 个归档会话",
                )
            )
        return out

    def plan_parts(self, pending):
        parts = []
        if self.n_files():
            k = sum(1 for r in self.writable() if r["todo"])
            parts.append(
                (
                    "add",
                    t(
                        f"add {len(pending)} session(s) ({self.n_files()} index file(s) in {k} account(s))",
                        f"补 {len(pending)} 个会话（{k} 个账号共 {self.n_files()} 个索引文件）",
                    ),
                )
            )
        if self.purge_plan():
            n = len(self.purge_plan())
            parts.append(("del", t(f"delete {n} archived session(s)", f"彻底删除 {n} 个已归档会话")))
        return parts

    def more_text(self, more):
        return t(
            f"{sym('ell')} and {more} more (pass -v to list all)", f"{sym('ell')} 还有 {more} 个（加 -v 查看全部）"
        )

    def in_sync_text(self):
        return f"{sym('ok')} " + t("Everything is in sync; nothing to add.", "已全部同步，没有需要补的会话。")

    # ---- 纯文本布局：管道、日志、--layout plain

    def print_header(self, verbose):
        sep = muted(f" {sym('sep')} ")
        print(
            style("claude-session-sync", "bold")
            + muted(f" v{__version__}")
            + "  "
            + sep.join(f"{muted(k)} {style(str(v), 'bold')}" for k, v in self.stats())
        )
        if verbose:
            print(muted("  " + self.verbose_stats()))

    def print_accounts(self):
        print()
        cells = [list(self.account_cells(r)) for r in self.rows]
        for r, c in zip(self.rows, cells):
            if r.get("is_cli") and r.get("name"):
                c[1] = cell(f"{c[1][0]}  {self.cli_tag()}", *c[1][1:])  # 没有颜色时也能认出当前账号
        w = [max(disp_width(c[i][0]) for c in cells) for i in range(6)]
        w[3] = max(w[3], disp_width(t("existing", "已有")))
        w[4] = max(w[4], disp_width(t("to add", "待补")))
        head = pad(t("Accounts", "账号"), 2 + w[0] + 1 + w[1] + 2 + w[2])
        cols = f"{rpad(t('existing', '已有'), w[3])}  {pad(t('to add', '待补'), w[4])}  {t('notes', '说明')}"
        print(style(head, "bold") + "  " + muted(cols))
        for r, c in zip(self.rows, cells):
            name = render_cell(c[1], w[1])
            if r.get("is_cli") and r.get("name"):  # 名称加粗，标记用绿色
                tag = self.cli_tag()
                name = (
                    style(r["name"], "bold")
                    + "  "
                    + ok(tag)
                    + " " * (w[1] - disp_width(r["name"]) - 2 - disp_width(tag))
                )
            line = (
                f"  {render_cell(c[0], w[0])} {name}  {render_cell(c[2], w[2])}  "
                f"{render_cell(c[3], w[3], 'r')}  {render_cell(c[4], w[4])}  {render_cell(c[5], disp_width(c[5][0]))}"
            )
            print(line.rstrip())

    def print_pending(self, pending, verbose, limit):
        print()
        print(style(t(f"Sessions to add ({len(pending)})", f"待补会话（{len(pending)} 个）"), "bold"))
        shown = pending[:limit]
        groups = {}
        for s, targets in shown:
            groups.setdefault(s["cwd"], []).append((s, targets))
        width = term_width() - 6
        title_w = max(disp_width(s["title"]) for s, _ in shown)
        mark_col = any(s["id"] in self.recent for s, _ in shown)
        for cwd, items in groups.items():
            print(muted(f"  {sym('group')} {fit(short_path(cwd), width)}"))
            for s, targets in items:
                print(self.session_line(s, targets, verbose, title_w, mark_col))
        if limit and len(pending) > limit:
            print(muted("  " + self.more_text(len(pending) - limit)))

    def print_purge(self, quiet, limit):
        plan = self.purge[0]
        print()
        head = t(f"Archived sessions to purge ({len(plan)})", f"清除已归档会话（{len(plan)} 个）")
        print(style(head, "bold", "red") if plan else muted(head))
        if not quiet:
            for cid, title, idx_hits, cli_hits in plan[:limit]:
                files = t(
                    f"{len(idx_hits)} index file(s), {len(cli_hits)} CLI file(s)",
                    f"索引 {len(idx_hits)} 个 · CLI 文件 {len(cli_hits)} 个",
                )
                print(f"    {danger(sym('minus'))} {fit(str(title), 50)}  " + muted(f"{files}  {cid}"))
            if limit and len(plan) > limit:
                print(muted("  " + self.more_text(len(plan) - limit)))
        for note in self.purge_notes():
            tip(note)

    def print_plain(self, verbose, quiet):
        pending = self.pending()
        limit = None if verbose else LIST_LIMIT
        if not quiet:
            self.print_header(verbose)
            self.print_accounts()
            if pending:
                self.print_pending(pending, verbose, limit)
        warnings = self.warnings(pending)
        if warnings:
            print()
        for w in warnings:
            warn(w)
        if self.purge is not None:
            self.print_purge(quiet, limit)
        print()
        if not self.has_work():
            print(style(self.in_sync_text(), "bold", "green"))
            if not quiet:
                for x in self.tips():
                    tip(x)
            return
        parts = [todo(text) if kind == "add" else danger(text) for kind, text in self.plan_parts(pending)]
        print(style(t("Plan: ", "计划："), "bold") + muted(t("; ", "；")).join(parts))

    # ---- 框线布局：终端里默认使用

    def boxed_accounts(self):
        cols = [
            {"h": ""},
            {"h": t("Name", "名称")},
            {"h": "ID", "drop": True},
            {"h": t("existing", "已有"), "align": "r"},
            {"h": t("to add", "待补"), "align": "r"},
            {"h": t("notes", "说明"), "flex": True},
        ]
        rows = []
        for r in self.rows:
            c = list(self.account_cells(r))
            if r.get("name"):  # 表格里只放主名称（别名或邮箱），完整信息太宽，会挤掉 ID 列
                c[1] = cell(r["name"].split(" · ")[0], *c[1][1:])
            if r.get("is_cli"):
                c[1] = cell(f"{c[1][0]}  {t('CLI', '当前 CLI')}", "bold", "green")
            rows.append(c)
        return table(t("Accounts", "账号"), cols, rows)

    def boxed_pending(self, pending, verbose, limit):
        inner = panel_width() - 4
        shown = pending[:limit]
        tgt_w = min(28, max(disp_width(self.targets_text(tg)) for _, tg in shown))
        running = f"{sym('run')} " + t("still being written", "运行中")
        mark_w = disp_width(running) if any(s["id"] in self.recent for s, _ in shown) else 0
        lines, groups = [], {}
        for s, targets in shown:
            groups.setdefault(s["cwd"], []).append((s, targets))
        for cwd, items in groups.items():
            lines.append(muted(fit(f"{sym('group')} {short_path(cwd)}", inner)))
            for s, targets in items:
                parts = [(cell(f"  {format_time(s['lastActivityAt'])}", "dim"), 13), (cell(s["title"]), None)]
                if mark_w:
                    parts.append((cell(running if s["id"] in self.recent else "", "yellow"), mark_w))
                if tgt_w:
                    parts.append((cell(self.targets_text(targets), "dim"), tgt_w))
                if verbose:
                    parts.append((cell(s["id"], "dim"), 36))
                lines.append(render_line(parts, inner))
        if limit and len(pending) > limit:
            lines.append(muted(self.more_text(len(pending) - limit)))
        return panel(t(f"Sessions to add ({len(pending)})", f"待补会话（{len(pending)} 个）"), lines)

    def boxed_purge(self, quiet, limit):
        plan = self.purge[0]
        inner = panel_width() - 4
        lines = []
        if not quiet:
            for cid, title, idx_hits, cli_hits in plan[:limit]:
                files = t(
                    f"{len(idx_hits)} index file(s), {len(cli_hits)} CLI file(s)",
                    f"索引 {len(idx_hits)} 个 · CLI 文件 {len(cli_hits)} 个",
                )
                info = f"{files}  {cid[:8]}"
                parts = [
                    (cell(sym("minus"), "red"), 1),
                    (cell(str(title)), None),
                    (cell(info, "dim"), disp_width(info)),
                ]
                lines.append(render_line(parts, inner))
            if limit and len(plan) > limit:
                lines.append(muted(self.more_text(len(plan) - limit)))
        lines += [muted(f"{sym('tip')} {x}") for x in self.purge_notes() for x in [fit(x, inner - 2)]]
        head = t(f"Archived sessions to purge ({len(plan)})", f"清除已归档会话（{len(plan)} 个）")
        return panel(
            head,
            lines or [muted(t("nothing to purge", "没有可清除的会话"))],
            title_styles=("bold", "red") if plan else ("dim",),
        )

    def boxed_plan(self, pending, quiet):
        inner = panel_width() - 4

        def wrapped(prefix, text, *styles):
            rows = wrap_display(text, inner - disp_width(prefix) - 1)
            return [style(prefix + " " + rows[0], *styles)] + [
                style(" " * (disp_width(prefix) + 1) + r, *styles) for r in rows[1:]
            ]

        lines = []
        if not self.has_work():
            lines.append(style(self.in_sync_text(), "bold", "green"))
        for kind, text in self.plan_parts(pending):
            lines += wrapped(sym("add") if kind == "add" else sym("minus"), text, "cyan" if kind == "add" else "red")
        for w in self.warnings(pending):
            lines += wrapped(sym("warn"), w, "yellow")
        if not self.has_work() and not quiet:
            for x in self.tips():
                lines += wrapped(sym("tip"), x, "dim")
        return panel(t("Plan", "计划"), lines)

    def print_boxed(self, verbose, quiet):
        pending = self.pending()
        limit = None if verbose else LIST_LIMIT
        out = []
        if not quiet:
            ver = f"claude-session-sync {__version__}"
            line = muted("  ·  ").join(f"{muted(k)} {style(str(v), 'bold')}" for k, v in self.stats())
            lines = [line] + ([muted(self.verbose_stats())] if verbose else [])
            out += panel(ver, lines)
            out += self.boxed_accounts()
            if pending:
                out += self.boxed_pending(pending, verbose, limit)
        if self.purge is not None and (self.purge[0] or not quiet):
            out += self.boxed_purge(quiet, limit)
        out += self.boxed_plan(pending, quiet)
        print("\n".join(out))

    def print_plan(self, verbose=False, quiet=False):
        if _BOXES:
            self.print_boxed(verbose, quiet)
        else:
            self.print_plain(verbose, quiet)

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


# ---------------------------------------------------------------- 默认设置
#
# ~/.claude-desktop-sync/config.toml 保存常用参数的默认值，命令行参数总是优先。
# 只支持下面几个键；purge_archived / include_recent 不写表示"在终端里每次询问"。

CONFIG_KEYS = {
    "lang": ("en", "zh"),
    "color": ("auto", "always", "never"),
    "icons": ("symbols", "emoji", "ascii"),
    "layout": ("auto", "boxed", "plain"),
    "ui": ("keys", "simple"),
    "purge_archived": bool,
    "include_recent": bool,
    "per_account_archive": bool,
}


def config_file():
    return JOURNAL_DIR / "config.toml"


def _parse_simple_toml(text):
    """Python 3.11 以前没有 tomllib：只解析 `键 = true/false/"字符串"` 这种平铺写法。"""
    data = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, val = (x.strip() for x in line.partition("="))
        if not sep:
            raise ValueError(raw)
        if val in ("true", "false"):
            data[key] = val == "true"
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            data[key] = val[1:-1]
        else:
            raise ValueError(raw)
    return data


def load_config():
    """读取默认设置；文件不存在返回空字典，无法识别的键或取值提示后忽略。"""
    p = config_file()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        try:
            import tomllib
        except ImportError:
            data = _parse_simple_toml(text)
        else:
            data = tomllib.loads(text)
    except ValueError as e:  # tomllib.TOMLDecodeError 也是 ValueError
        warn(t(f"Ignoring {p}: cannot parse it ({e}).", f"忽略 {p}：无法解析（{e}）。"))
        return {}
    cfg = {}
    for key, val in data.items():
        allowed = CONFIG_KEYS.get(key)
        ok = allowed is not None and (isinstance(val, bool) if allowed is bool else val in allowed)
        if ok:
            cfg[key] = val
        else:
            warn(t(f"Ignoring {key} = {val!r} in {p}.", f"忽略 {p} 里的 {key} = {val!r}。"))
    return cfg


def save_config(cfg):
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# claude-session-sync defaults; command-line options always win."]
    for key in CONFIG_KEYS:
        if key in cfg:
            val = cfg[key]
            lines.append(f"{key} = {str(val).lower() if isinstance(val, bool) else json.dumps(val)}")
    p = config_file()
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 交互式向导
#
# 需要值的参数不写值时（如只写 --account），在终端里弹出对应的选择；不在终端里时报错退出。


class _Ask:
    """参数没写值时填入的占位标记（不用字符串，免得被 argparse 拿去和 choices 比较）。"""

    def __repr__(self):
        return "ASK"


ASK = _Ask()

CONFIG_FLAGS = (
    "--lang", "--color", "--icons", "--layout", "-p", "--purge-archived", "--no-purge-archived",
    "-r", "--include-recent", "--no-include-recent", "--per-account-archive", "--no-per-account-archive",
)  # fmt: skip


def need_terminal(flag, interactive):
    if not interactive:
        print(
            t(
                f"{flag} needs a value here; run it in a terminal to pick one interactively.",
                f"{flag} 需要一个值；在终端里运行时可以不写值，从列表里选择。",
            ),
            file=sys.stderr,
        )
        sys.exit(2)


def ask_option(title, choices, current):
    """从 [(值, 显示文字)] 里选一个，光标停在当前值上；取消时保持当前值。"""
    options = [{"label": text, "value": v, "note": t("current", "当前") if v == current else ""} for v, text in choices]
    default = next((i for i, (v, _) in enumerate(choices) if v == current), 0)
    picked = select_one(title, options, default=default)
    return current if picked is None else picked


LANG_CHOICES = [("zh", "中文"), ("en", "English")]
COLOR_CHOICES = [("auto", "auto"), ("always", "always"), ("never", "never")]
ICON_CHOICES = [("symbols", "symbols  ● ✓ ⚠"), ("emoji", "emoji  🟢 ✅ ⚠️"), ("ascii", "ascii  * OK !")]
LAYOUT_CHOICES = [("auto", "auto"), ("boxed", "boxed"), ("plain", "plain")]


def account_option(acct, org, ctx, note=""):
    name = account_label(acct, org, ctx.book, ctx.cli_acct, with_cli=False).split(" · ")[0]
    ids = f"{acct[:8]}/{org[:8]}"
    label = f"{name}  {ids}" if name else ids
    if is_cli_account(acct, org, ctx.cli_acct):
        label += "  " + t("(current CLI login)", "（当前 CLI 登录）")
    return {"label": label, "note": note}


def pick_accounts(ctx):
    """多选账号，返回 "账号ID/组织ID" 列表（给 --account 用）；取消时退出。"""
    options = [account_option(a, o, ctx) for _, a, o, _ in ctx.all_accts]
    picked = select_many(t("Accounts to sync", "要同步的账号"), options)
    if picked is None:
        sys.exit(t(*CANCELLED))
    return [f"{ctx.all_accts[i][1]}/{ctx.all_accts[i][2]}" for i in picked]


def pick_project(ctx):
    """按工作目录列出会话数，选一个；返回 --project 的值（None 表示全部）。"""
    sessions, _ = scan_cli_sessions(ctx.projects)
    counts = {}
    for s in sessions:
        counts[s["cwd"]] = counts.get(s["cwd"], 0) + 1
    order = sorted(counts, key=lambda c: (-counts[c], c))
    options = [
        {
            "label": t("All projects", "全部项目"),
            "value": "",
            "note": t(f"{len(sessions)} sessions", f"{len(sessions)} 个会话"),
        }
    ]
    options += [
        {"label": short_path(c), "value": c, "note": t(f"{counts[c]} sessions", f"{counts[c]} 个会话")} for c in order
    ]
    picked = select_one(t("Only handle sessions in", "只处理哪个项目的会话"), options)
    if picked is None:
        sys.exit(t(*CANCELLED))
    return picked or None


def pick_excludes(ctx):
    """多选要排除的会话（默认都不选）；可以顺便记到 exclude.txt。返回会话 ID 列表。"""
    sessions, _ = scan_cli_sessions(ctx.projects)
    sessions.sort(key=lambda s: -s["lastActivityAt"])
    options = [
        {"label": f"{format_time(s['lastActivityAt'])}  {s['title']}", "note": fit(short_path(s["cwd"]), 32)}
        for s in sessions
    ]
    picked = select_many(t("Sessions to leave out", "要排除的会话"), options, selected=[])
    if picked is None:
        sys.exit(t(*CANCELLED))
    chosen = [sessions[i] for i in picked]
    if chosen and confirm_key(t("Also remember them for later runs?", "也记到排除列表，以后运行都跳过？")):
        path = add_to_exclude_file(chosen)
        print(muted(t(f"Saved to {path}.", f"已记到 {path}。")))
    return [s["id"] for s in chosen]


def manage_exclude_file():
    """查看 exclude.txt，勾选要移除的行（移除后这些会话会重新同步）。"""
    path = default_exclude_file()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    entries = [(i, ln) for i, ln in enumerate(lines) if ln.split("#", 1)[0].strip()]
    if not entries:
        print(muted(t(f"The exclude list is empty ({path}).", f"排除列表是空的（{path}）。")))
        return
    options = []
    for _, ln in entries:
        sid, _, comment = ln.partition("#")
        options.append({"label": comment.strip() or sid.strip(), "note": sid.strip()[:8]})
    picked = select_many(
        t(f"Remove from the exclude list ({len(entries)})", f"从排除列表移除（共 {len(entries)} 个）"),
        options,
        selected=[],
    )
    if not picked:
        return
    drop = {entries[i][0] for i in picked}
    path.write_text("".join(ln + "\n" for i, ln in enumerate(lines) if i not in drop), encoding="utf-8")
    print(
        ok(f"{sym('ok')} ")
        + t(f"Removed {len(drop)}; they will sync again.", f"已移除 {len(drop)} 个，之后会重新同步。")
    )


def current_alias(book, acct, org):
    return book["aliases"].get(f"{acct}/{org}") or book["aliases"].get(acct) or ""


def remove_alias(book, accts, acct, org):
    """删除某个组织的别名。如果名称是按账号设置的，把它下放到同账号的其他组织，免得它们一起丢名字。"""
    book["aliases"].pop(f"{acct}/{org}", None)
    shared = book["aliases"].pop(acct, None)
    if shared:
        for _, a, o, _ in accts:
            if a == acct and o != org:
                book["aliases"].setdefault(f"{a}/{o}", shared)


def alias_wizard(ctx):
    """给账号起名：选账号 → 输入名称（预先填好当前别名）；可以连续设置，Esc 或"完成"结束。"""
    book = ctx.book
    while True:
        options = []
        for _, a, o, _ in ctx.all_accts:
            alias = current_alias(book, a, o)
            info = account_label(a, o, book, ctx.cli_acct)
            if alias and info.startswith(alias):
                info = info[len(alias) :].lstrip(" ·")
            label = f"{a[:8]}/{o[:8]}" + (f"  {info}" if info else "")
            options.append({"label": label, "note": alias or t("(no name)", "（未命名）")})
        options.append({"label": t("Done", "完成"), "key": "q", "value": "done"})
        picked = select_one(t("Name which account?", "给哪个账号起名？"), options)
        if picked in (None, "done"):
            return
        _, a, o, _ = ctx.all_accts[picked]
        ids = f"{a[:8]}/{o[:8]}"
        name = ask_text(
            t(f"Name for {ids} (empty removes it): ", f"{ids} 的名称（清空 = 删除）："), current_alias(book, a, o)
        )
        if name is None:
            continue
        if name:
            book["aliases"][f"{a}/{o}"] = name
            print(ok(f"{sym('ok')} ") + t(f"Alias set: {ids} = {name}", f"已设置别名：{ids} = {name}"))
        else:
            remove_alias(book, ctx.all_accts, a, o)
            print(ok(f"{sym('ok')} ") + t(f"Alias removed: {ids}", f"已删除别名：{ids}"))
        save_account_book(book)


def settings_wizard(cfg):
    """默认设置表单：←→ 改值，回车保存到 config.toml。"每次询问""自动"表示不写这一项。"""
    ask_choices = [("ask", t("ask each time", "每次询问")), (True, t("always", "总是")), (False, t("never", "从不"))]
    fields = [
        {"key": "lang", "label": t("Language", "语言"), "choices": [("auto", t("system", "跟随系统"))] + LANG_CHOICES},
        {"key": "icons", "label": t("Icons", "图标"), "choices": ICON_CHOICES},
        {"key": "layout", "label": t("Layout", "布局"), "choices": LAYOUT_CHOICES},
        {"key": "color", "label": t("Colors", "颜色"), "choices": COLOR_CHOICES},
        {"key": "purge_archived", "label": t("Purge archived", "清除已归档"), "choices": ask_choices},
        {"key": "include_recent", "label": t("Running sessions", "运行中的会话"), "choices": ask_choices},
        {
            "key": "ui",
            "label": t("Pickers", "选择方式"),
            "choices": [("keys", t("arrow keys", "方向键")), ("simple", t("type numbers", "输入编号"))],
        },
    ]
    defaults = {
        "lang": "auto",
        "icons": "symbols",
        "layout": "auto",
        "color": "auto",
        "purge_archived": "ask",
        "include_recent": "ask",
        "ui": "keys",
    }
    for f in fields:
        f["value"] = cfg.get(f["key"], defaults[f["key"]])
    result = edit_form(t("Defaults", "默认设置"), fields)
    if result is None:
        print(muted(t("Not saved.", "未保存。")))
        return None
    new = dict(cfg)
    for f in fields:
        v = result[f["label"]]
        if v in ("auto", "ask") and f["key"] in ("lang", "purge_archived", "include_recent"):
            new.pop(f["key"], None)
        else:
            new[f["key"]] = v
    p = save_config(new)
    print(ok(f"{sym('ok')} ") + t(f"Saved to {p}", f"已保存到 {p}"))
    return new


def resolve_asks(args, ctx, cfg, interactive):
    """把参数里的 ASK 换成交互选择的结果。需要账号/会话数据的放在加载 ctx 之后。"""
    if args.account and ASK in args.account:
        need_terminal("--account", interactive)
        args.account = [a for a in args.account if a is not ASK] + pick_accounts(ctx)
    if args.project is ASK:
        need_terminal("--project", interactive)
        args.project = pick_project(ctx)
    if args.exclude and ASK in args.exclude:
        need_terminal("--exclude", interactive)
        args.exclude = [x for x in args.exclude if x is not ASK] + pick_excludes(ctx)
    if args.exclude_file is ASK:
        need_terminal("--exclude-file", interactive)
        manage_exclude_file()
        args.exclude_file = None


# ---------------------------------------------------------------- 主流程


def apply_lang_flag(argv, cfg=None):
    """在构建帮助文本之前先确定语言，让 --help 和后续输出都使用它。
    优先级：--lang > CLAUDE_SESSION_SYNC_LANG > 配置文件 > 系统语言。非法取值交给正式的解析器报错。"""
    global _LANG_ZH
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", nargs="?")
    known, _ = pre.parse_known_args(argv)
    if known.lang in ("en", "zh"):
        _LANG_ZH = known.lang == "zh"
    elif cfg and cfg.get("lang") and not os.environ.get(LANG_ENV):
        _LANG_ZH = cfg["lang"] == "zh"


def hub_menu(report, can_undo, filters="", excluded=0):
    """终端里预览之后的总菜单。返回 write / pick / undo / filter / alias / exclude / settings / quit。
    有待补会话时默认（回车）写入，已全部同步时默认退出。"""
    options = [{"label": t("Sync", "同步"), "sep": True}]
    if report.has_work():
        options.append(
            {"key": "1", "value": "write", "alias": "yes", "label": t("Write the plan above", "按上面的计划写入")}
        )
    if report.n_files():
        options.append(
            {
                "key": "2",
                "value": "pick",
                "label": t("Pick accounts/sessions first, then write", "先挑选账号/会话，再写入"),
            }
        )
    if can_undo:
        options.append({"key": "3", "value": "undo", "label": t("Undo the most recent write", "撤销最近一次写入")})
    options += [
        {"label": t("Manage", "管理"), "sep": True},
        {
            "key": "f",
            "value": "filter",
            "label": t("Only some accounts or a project", "只看某些账号/项目"),
            "note": filters,
        },
        {"key": "n", "value": "alias", "label": t("Name accounts", "给账号起名")},
        {
            "key": "x",
            "value": "exclude",
            "label": t("Exclude list", "排除列表"),
            "note": t(f"{excluded} sessions", f"{excluded} 个") if excluded else "",
        },
        {"key": "s", "value": "settings", "label": t("Defaults", "默认设置")},
        {
            "key": "q",
            "value": "quit",
            "alias": "no",
            "label": t("Quit without writing", "不写入，退出") if report.has_work() else t("Quit", "退出"),
        },
    ]
    default = next(i for i, o in enumerate(options) if o.get("value") == ("write" if report.has_work() else "quit"))
    if keys_available():
        print()
    return select_one(t("What next?", "接下来做什么？"), options, default=default) or "quit"


def filter_menu(args, ctx):
    """菜单里的"只看某些账号/项目"：改 args.account / args.project，然后重新预览。"""
    options = [
        {
            "value": "account",
            "label": t("Accounts", "按账号"),
            "note": t(f"{len(args.account)} selected", f"已选 {len(args.account)} 个")
            if args.account
            else t("all", "全部"),
        },
        {
            "value": "project",
            "label": t("Project", "按项目"),
            "note": short_path(args.project) if args.project else t("all", "全部"),
        },
        {"value": "clear", "label": t("Clear filters", "清除筛选")},
    ]
    picked = select_one(t("Filter", "筛选"), options)
    if picked == "account":
        args.account = pick_accounts(ctx) or None
    elif picked == "project":
        args.project = pick_project(ctx)
    elif picked == "clear":
        args.account, args.project = None, None


def apply_settings(args, cfg):
    """设置表单保存后，让这次运行也立即用上新设置。"""
    global _UI, _LANG_ZH
    for key in ("color", "icons", "layout"):
        setattr(args, key, cfg.get(key, {"color": "auto", "icons": "symbols", "layout": "auto"}[key]))
    args.purge_archived = cfg.get("purge_archived")
    args.include_recent = cfg.get("include_recent")
    _UI = cfg.get("ui", "keys")
    if cfg.get("lang"):
        _LANG_ZH = cfg["lang"] == "zh"
    setup_terminal(args.color, args.icons, args.layout)


def ask_include_recent(sessions):
    """有最近仍在写入的会话时询问是否一并同步，默认不同步。"""
    print(
        style(
            t(
                f"{len(sessions)} session(s) were written in the last 2 minutes and may still be running:",
                f"有 {len(sessions)} 个会话最近 2 分钟内仍在写入，可能正在运行：",
            ),
            "yellow",
        )
    )
    for s in sorted(sessions, key=lambda s: -s["lastActivityAt"])[:LIST_LIMIT]:
        print(f"  {caution(sym('run'))} {fit(s['title'], max(20, term_width() - 6))}")
    hint = t(
        "(set include_recent in config.toml to stop asking)",
        "（在 config.toml 里设置 include_recent 可不再询问）",
    )
    ok = confirm_key(t("Sync them too?", "一并同步？") + " " + style(hint, "dim"))
    print()
    return ok


def wrap_display(text, width):
    """按显示宽度折行：中文两格；在空格处或中文字符之间断开，不拆开 --no-xxx 这类参数名。"""
    lines, line = [], ""
    for token in re.findall(r"[\u2e80-\u9fff\uff00-\uffef]|[^\s\u2e80-\u9fff\uff00-\uffef]+|\s+", text):
        if token.isspace():
            token = " "
        closing = re.fullmatch(r"[>)\]）】」』，。、；：！？]+", token)  # 行首不放闭合标点
        if line and not closing and disp_width((line + token).rstrip()) > width:
            lines.append(line.rstrip())
            line = "" if token == " " else token
        else:
            line += token
    if line.strip():
        lines.append(line.rstrip())
    return lines or [""]


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """参数说明按显示宽度折行，中文帮助不会超出终端宽度。"""

    def __init__(self, prog):
        super().__init__(prog, max_help_position=26, width=min(100, shutil.get_terminal_size((100, 24)).columns))

    def _split_lines(self, text, width):
        return wrap_display(" ".join(text.split()), width - 2)  # 留出行尾闭合标点的位置


ZSH_FILE_ARGS = {"--exclude-file": "_files", "--projects-dir": "_directories", "--desktop-root": "_directories"}


def zsh_completion(ap):
    """根据参数定义生成 zsh 补全脚本：互斥的长短写法、取值列表、文件/目录参数都会补全。"""

    def esc(text):
        text = " ".join((text or "").split())
        text = re.sub(r"[\[\]]", lambda m: "（" if m.group(0) == "[" else "）", text)
        return text.replace("'", "'\\''").replace(":", "：")

    peers = {}  # 互斥组里的其他参数，补全时也一并排除
    for group in ap._mutually_exclusive_groups:
        names = [o for a in group._group_actions for o in a.option_strings]
        for a in group._group_actions:
            peers[a.dest] = names
    specs = []
    for action in ap._actions:
        opts = action.option_strings
        if not opts or action.help == argparse.SUPPRESS:
            continue
        desc = esc(action.help).split("。")[0].split(". ")[0]
        desc = fit(desc, 60)
        if isinstance(action, argparse.BooleanOptionalAction):
            pos = [o for o in opts if not o.startswith("--no-")]
            neg = [o for o in opts if o.startswith("--no-")]
            excl = " ".join(opts)
            specs.append(
                f"'({excl})'" + "{" + ",".join(pos) + "}" + f"'[{desc}]'"
                if len(pos) > 1
                else f"'({excl}){pos[0]}[{desc}]'"
            )
            specs.append(f"'({excl}){neg[0]}[{esc(t('do not', '不要'))} {pos[-1]}]'")
            continue
        repeat = isinstance(action, argparse._AppendAction)
        excl = "" if repeat else "(" + " ".join(peers.get(action.dest, opts)) + ")"
        if repeat:
            excl = "*"
        value = ""
        if action.nargs != 0:
            meta = action.metavar or action.dest
            colon = "::" if action.nargs == "?" else ":"  # 值可选：不写值时进入交互选择
            if action.choices:
                value = f"{colon}{meta}:(" + " ".join(map(str, action.choices)) + ")"
            else:
                value = f"{colon}{meta}:{ZSH_FILE_ARGS.get(opts[-1], ' ')}"
        if len(opts) > 1 and not repeat:
            specs.append(f"'{excl}'" + "{" + ",".join(opts) + "}" + f"'[{desc}]{value}'")
        else:
            for o in opts:
                specs.append(f"'{excl}{o}[{desc}]{value}'")
    body = " \\\n    ".join(specs)
    return f"""#compdef claude-session-sync
# claude-session-sync zsh completion. Load it with:
#   eval "$(claude-session-sync --completion zsh)"

_claude_session_sync() {{
  _arguments -s \\
    {body}
}}

if (( $+functions[compdef] )); then
  compdef _claude_session_sync claude-session-sync
fi
"""


def build_parser():
    """命令行参数。按用途分组，常用的放前面；--help 开头先给几个常用例子。"""
    ap = argparse.ArgumentParser(
        prog="claude-session-sync",
        usage=t("%(prog)s [options]", "%(prog)s [选项]"),
        add_help=False,
        formatter_class=HelpFormatter,
        description=t(
            """\
Sync local Claude Code CLI sessions into the Claude desktop app's Code sidebar,
for every account. Skips archived and deleted sessions. Previews by default.
中文输出：加 --lang zh，或设置环境变量 CLAUDE_SESSION_SYNC_LANG=zh

Common commands:
  %(prog)s          preview, then a menu in a terminal (Enter writes)
  %(prog)s -a       write without asking (quit the desktop app first)
  %(prog)s -i       pick accounts and sessions, then write
  %(prog)s -a -p    write, then delete archived sessions (undoable)
  %(prog)s -u       undo the most recent write""",
            """\
把本地 Claude Code CLI 会话同步到 Claude 桌面端 Code 标签页的侧边栏（多账号）。
自动排除已归档、桌面端已删除的会话。默认只预览。
English output: pass --lang en, or set CLAUDE_SESSION_SYNC_LANG=en

常用命令：
  %(prog)s          预览；在终端里随后给出菜单（回车即写入）
  %(prog)s -a       不询问直接写入（先 ⌘Q 彻底退出桌面端）
  %(prog)s -i       从列表里挑选账号和会话后写入
  %(prog)s -a -p    写入后彻底删除已归档会话（可撤销）
  %(prog)s -u       撤销最近一次写入""",
        ),
        epilog=t(
            """\
Notes:
  * Quit the desktop app completely before writing, and reopen it afterwards.
  * Journals and backups live in ~/.claude-desktop-sync/; --undo steps back one
    run at a time.
  * Defaults live in ~/.claude-desktop-sync/config.toml (lang, color, icons,
    purge_archived, include_recent, per_account_archive); --save-defaults saves
    the current options. If purge_archived or include_recent is unset, a
    terminal run asks each time it matters.
  * Options that take a value can be given without one in a terminal
    (e.g. --account, --set-alias, --lang): you then pick from a list.
  * Output language: --lang, CLAUDE_SESSION_SYNC_LANG, config.toml, the locale.
  * The desktop index format is undocumented and may break with desktop updates.
""",
            """\
注意：
  * 写入前请彻底退出桌面端（菜单栏 ⌘Q），写完重新打开。
  * 日志与备份在 ~/.claude-desktop-sync/，--undo 按时间倒序逐次撤销。
  * 默认设置在 ~/.claude-desktop-sync/config.toml（lang、color、icons、
    purge_archived、include_recent、per_account_archive），
    可以用 --save-defaults 保存当前参数。
    purge_archived、include_recent 没设置时，在终端里遇到相关情况会当场询问。
  * 需要值的参数在终端里可以不写值（如 --account、--set-alias、--lang），
    运行时从列表里选择。
  * 输出语言：依次看 --lang、CLAUDE_SESSION_SYNC_LANG、config.toml、系统语言。
  * 桌面端索引格式无官方文档，桌面端更新后可能失效。
""",
        ),
    )

    common = ap.add_argument_group(t("Common", "常用"))
    common.add_argument(
        "-a", "--apply", action="store_true", help=t("actually write (default: preview only)", "实际写入（默认只预览）")
    )
    common.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help=t("pick accounts and sessions from a list before writing", "写入前从列表里挑选账号和会话"),
    )
    common.add_argument(
        "-p",
        "--purge-archived",
        action=argparse.BooleanOptionalAction,
        help=t(
            "after syncing, permanently delete archived sessions (index + CLI .jsonl, backed up, undoable). "
            "Unset: offer it in a terminal when there are any",
            "同步后彻底删除已归档会话（索引 + CLI 的 .jsonl，备份到日志目录，可 --undo）。"
            "不指定时，在终端里发现已归档会话会询问",
        ),
    )
    common.add_argument(
        "-r",
        "--include-recent",
        action=argparse.BooleanOptionalAction,
        help=t(
            "also sync sessions written in the last 2 minutes (possibly still running); "
            "--purge-archived still skips them. Unset: ask in a terminal",
            "同步时不跳过最近 2 分钟内仍在写入的会话（可能正在运行）；--purge-archived 仍然会跳过。"
            "不指定时在终端里询问",
        ),
    )
    common.add_argument("-u", "--undo", action="store_true", help=t("undo the most recent write", "撤销最近一次写入"))
    common.add_argument(
        "--lang",
        choices=["en", "zh"],
        nargs="?",
        const=ASK,
        help=t(
            "output language (default: from CLAUDE_SESSION_SYNC_LANG, config.toml or the locale)",
            "输出语言（默认：按 CLAUDE_SESSION_SYNC_LANG、config.toml 或系统语言）",
        ),
    )
    common.add_argument(
        "--save-defaults",
        action="store_true",
        help=t(
            "save --lang, --color, --icons, --layout, --[no-]purge-archived, --[no-]include-recent and "
            "--[no-]per-account-archive of this run to ~/.claude-desktop-sync/config.toml, then continue",
            "把本次的 --lang、--color、--icons、--layout、--[no-]purge-archived、--[no-]include-recent、"
            "--[no-]per-account-archive 保存到 ~/.claude-desktop-sync/config.toml 作为默认值，然后照常运行",
        ),
    )

    pick = ap.add_argument_group(t("Accounts and filters", "账号与筛选"))
    pick.add_argument(
        "--account",
        action="append",
        nargs="?",
        const=ASK,
        metavar="ID",
        help=t(
            "only sync this account; an ID prefix such as the 8 characters shown in the output works, "
            "add /<org-id-prefix> to pick one organization (repeatable)",
            "只同步指定账号；可以只写输出里显示的前 8 位，加 /<组织ID前缀> 可只选一个组织（可多次使用）",
        ),
    )
    pick.add_argument(
        "--set-alias",
        action="append",
        nargs="?",
        const=ASK,
        metavar="ID=NAME",
        help=t(
            "give an account a display name, e.g. b8073c71=work (ID prefix, optionally /<org-id-prefix>); "
            "an empty name removes it; repeatable, then exits",
            "给账号起显示名称，如 b8073c71=工作号（ID 前缀，可加 /<组织ID前缀>）；名称留空则删除；可多次使用，设置完即退出",
        ),
    )
    pick.add_argument(
        "--project",
        nargs="?",
        const=ASK,
        metavar="TEXT",
        help=t(
            "only handle sessions whose working directory contains this string (sync and purge)",
            "只处理工作目录包含该字符串的会话（同步和清除都生效）",
        ),
    )
    pick.add_argument(
        "--exclude",
        action="append",
        nargs="?",
        const=ASK,
        default=[],
        metavar="SESSION_ID",
        help=t("session ID to skip in sync and purge (repeatable)", "排除的会话ID，同步和清除都跳过（可多次使用）"),
    )
    pick.add_argument(
        "--exclude-file",
        nargs="?",
        const=ASK,
        metavar="FILE",
        help=t("file with session IDs to exclude, one per line", "排除列表文件，每行一个会话ID"),
    )

    out = ap.add_argument_group(t("Output", "输出"))
    verbosity = out.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help=t("only print the summary", "只输出汇总"))
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=t("print every session and extra statistics", "列出全部会话和更多统计"),
    )
    out.add_argument(
        "--json",
        action="store_true",
        help=t(
            "print the result as JSON (implies --no-input and no colors)", "以 JSON 输出结果（同时不提问、不带颜色）"
        ),
    )
    out.add_argument(
        "--no-input",
        action="store_true",
        help=t(
            "never ask questions, even in a terminal (behaves like a script)",
            "即使在终端里也不提问（和脚本里运行一样）",
        ),
    )
    out.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        nargs="?",
        const=ASK,
        default="auto",
        help=t(
            "colored output (default: auto, only in a terminal; NO_COLOR is honored)",
            "彩色输出（默认 auto：只在终端里启用，遵循 NO_COLOR）",
        ),
    )
    out.add_argument(
        "--icons",
        choices=["symbols", "emoji", "ascii"],
        nargs="?",
        const=ASK,
        default="symbols",
        help=t(
            "status icons: symbols (✓ ↓ ⚠, default), emoji (✅ 📥 ⚠️), or ascii",
            "状态图标：symbols（✓ ↓ ⚠，默认）、emoji（✅ 📥 ⚠️）或 ascii",
        ),
    )

    out.add_argument(
        "--layout",
        choices=["auto", "boxed", "plain"],
        nargs="?",
        const=ASK,
        default="auto",
        help=t(
            "boxed panels and tables, or plain text (default: auto, boxed only in a terminal)",
            "框线面板和表格，或纯文本（默认 auto：只在终端里画框线）",
        ),
    )

    adv = ap.add_argument_group(t("Advanced", "高级选项"))
    adv.add_argument(
        "--per-account-archive",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=t(
            "only exclude sessions archived in the same account (default: archived anywhere = excluded everywhere)",
            "只按本账号的归档状态排除（默认：任一账号归档即全部排除）",
        ),
    )
    adv.add_argument(
        "--force",
        action="store_true",
        help=t(
            "run --apply / --undo even while the desktop app is running (not recommended)",
            "桌面端运行中也强制执行 --apply / --undo（不推荐）",
        ),
    )
    adv.add_argument(
        "--projects-dir",
        metavar="DIR",
        help=t("CLI sessions directory (default: ~/.claude/projects)", "CLI 会话目录（默认 ~/.claude/projects）"),
    )
    adv.add_argument(
        "--desktop-root",
        action="append",
        metavar="DIR",
        help=t(
            "path to a claude-code-sessions directory (repeatable)", "手动指定 claude-code-sessions 目录（可多次使用）"
        ),
    )
    adv.add_argument(
        "--show-template",
        action="store_true",
        help=t("print each account's template fields and exit", "显示各账号模板字段后退出"),
    )
    other = ap.add_argument_group(t("Other", "其他"))
    other.add_argument(
        "--completion",
        choices=["zsh"],
        nargs="?",
        const="zsh",
        help=t(
            'print a shell completion script and exit, e.g. eval "$(claude-session-sync --completion zsh)"',
            '输出 shell 补全脚本后退出，例如 eval "$(claude-session-sync --completion zsh)"',
        ),
    )
    other.add_argument("-h", "--help", action="help", help=t("show this help and exit", "显示帮助后退出"))
    other.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=t("show the version and exit", "显示版本号后退出"),
    )
    return ap


class Context:
    """一次运行要用的环境：目录、账号、账号名称、排除列表。筛选条件改变后可以重新生成计划，不必重新加载。"""

    def __init__(self, projects, roots, all_accts, book, cli_acct):
        self.projects = projects
        self.roots = roots
        self.all_accts = all_accts
        self.book = book
        self.cli_acct = cli_acct

    def accounts(self, args):
        """按 --account 筛选后的账号目录。"""
        if not args.account:
            return self.all_accts
        wanted = [resolve_account_key(v, self.all_accts) for v in args.account]
        return [a for a in self.all_accts if any(a[1] == ac and (og is None or a[2] == og) for ac, og in wanted)]

    def excluded(self, args):
        excluded = set(args.exclude) | read_exclude_file(default_exclude_file())
        if args.exclude_file:
            excluded |= read_exclude_file(Path(args.exclude_file).expanduser(), must_exist=True)
        return excluded


def load_context(args):
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
    all_accts = account_dirs(roots)
    if not all_accts:
        sys.exit(t("No account directories found.", "没有找到任何账号目录。"))
    return Context(projects, roots, all_accts, load_account_book(), cli_login_account())


def read_indexes(accts):
    """读取各账号索引；返回 (indexes, 任一账号归档/删除过的, 删除标记)。"""
    indexes, archived_global, deleted_global = {}, set(), set()
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
    return indexes, archived_global, deleted_global


def fallback_templates(ctx, accts, indexes):
    """账号目录为空时借用的后备模板：返回 ({root: 模板}, 任意一个模板)，优先同一 root 下的。"""
    fallback = {}
    for root, _acct, _org, d in accts:
        if root not in fallback:
            tpl = pick_template(indexes[d][2])
            if tpl:
                fallback[root] = tpl
    any_fallback = next(iter(fallback.values()), None)
    if any_fallback is None:  # --account 过滤后可能没有，再从全部账号里找
        for _, _, _, d in ctx.all_accts:
            any_fallback = pick_template(load_index(d))
            if any_fallback:
                break
    return fallback, any_fallback


def show_templates(ctx, accts):
    indexes, _, _ = read_indexes(accts)
    for _d, (acct, org, entries, _, _) in indexes.items():
        tpl = pick_template(entries)
        label = account_label(acct, org, ctx.book, ctx.cli_acct)
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
                    {k: (v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}>") for k, v in tpl.items()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(t("  (no usable template)", "  （无可用模板）"))


def plan_purge_all(ctx, excluded, project):
    """清除时始终覆盖全部账号，避免 --account 过滤后其他账号残留指向已删 .jsonl 的索引；
    --exclude 和 --project 仍然生效，正在写入的会话按 .jsonl 修改时间单独判断。"""
    all_idx, all_archived = {}, set()
    for _root, acct, org, d in ctx.all_accts:
        entries = load_index(d)
        _, archived_local = load_markers(d)
        all_archived |= {
            e.get("cliSessionId")
            for f, e in entries
            if isinstance(e, dict)
            and (is_archived(e) or f.stem in archived_local or e.get("sessionId") in archived_local)
        }
        all_idx[d] = (acct, org, entries, None, None)
    return plan_purge(all_idx, all_archived, ctx.projects, excluded=excluded, project=project)


def build_plan(args, ctx, interactive):
    """生成同步计划（SyncReport）：每个账号要补哪些会话、要清除哪些已归档会话。不写任何文件。"""
    accts = ctx.accounts(args)
    excluded = ctx.excluded(args)
    indexes, archived_global, deleted_global = read_indexes(accts)
    fallback, any_fallback = fallback_templates(ctx, accts, indexes)
    root_of = {d: root for root, _, _, d in accts}
    book, cli_acct = ctx.book, ctx.cli_acct

    sessions, skipped = scan_cli_sessions(ctx.projects)
    if args.project:
        sessions = [s for s in sessions if args.project in s["cwd"]]
    now = time.time()
    recent = {s["id"] for s in sessions if now - s["mtime"] < RUNNING_GRACE_SEC}
    usable_ids = {s["id"] for s in sessions}
    all_stems = {f.stem for proj in ctx.projects.iterdir() if proj.is_dir() for f in proj.glob("*.jsonl")}

    def build_rows(skip_recent):
        """每个账号要补哪些会话；返回 (rows, 因为最近仍在写入而暂缓的会话 ID)。"""
        rows, held_back = [], set()
        for d, (acct, org, entries, known, archived) in indexes.items():
            template, source = pick_template(entries), "own"
            if not template:
                template = fallback.get(root_of[d]) or any_fallback
                source = "borrowed" if template else "none"
            arch = archived if args.per_account_archive else archived_global
            unknown = [s for s in sessions if s["id"] not in known]
            missing = [s for s in unknown if s["id"] not in arch and s["id"] not in excluded]
            held = {s["id"] for s in missing if s["id"] in skip_recent}
            held_back |= held
            extra = known - usable_ids - {None}
            rows.append(
                {
                    "dir": d,
                    "acct": acct,
                    "org": org,
                    "known": len(known),
                    "name": account_label(acct, org, book, cli_acct, with_cli=False),
                    "is_cli": is_cli_account(acct, org, cli_acct),
                    "ids": f"{acct[:8]}/{org[:8]}",
                    "archived_n": sum(1 for s in unknown if s["id"] in arch),
                    "excluded_n": sum(1 for s in unknown if s["id"] not in arch and s["id"] in excluded),
                    "held_n": len(held),
                    # --project 过滤后看不到全部会话，这两项就不统计，免得误报
                    "orphans": 0 if args.project else sum(1 for x in extra if x not in all_stems),
                    "unusable": 0 if args.project else sum(1 for x in extra if x in all_stems),
                    "todo": [s for s in missing if s["id"] not in skip_recent],
                    "template": template,
                    "source": source,
                    "label": account_label(acct, org, book, cli_acct),
                    "heading": account_heading(acct, org, book, cli_acct),
                    "short": account_short(acct, org, book, cli_acct),
                }
            )
        return rows, held_back

    # 没指定 --[no-]include-recent 时，在终端里询问是否一并同步正在写入的会话
    rows, held_back = build_rows(set() if args.include_recent else recent)
    if held_back and args.include_recent is None and interactive:
        args.include_recent = ask_include_recent([s for s in sessions if s["id"] in held_back])  # 重新预览时不再问
        if args.include_recent:
            rows, held_back = build_rows(set())

    # 没指定 --[no-]purge-archived 时，只在终端里找出可清除的会话，写入时再询问
    want_purge = args.purge_archived if args.purge_archived is not None else interactive
    purge = None
    if want_purge:
        purge = plan_purge_all(ctx, excluded, args.project)
        if args.purge_archived is None and not purge[0]:
            purge = None  # 没要求清除、也没有可清除的，就不在预览里多显示一节
    return SyncReport(rows, sessions, skipped, recent, held_back, purge, archived_global, deleted_global)


def confirm_purge(args, report):
    """写入前确认清除：要输入 yes。返回是否清除。"""
    n = len(report.purge_plan())
    if args.purge_archived:
        prompt = t(
            f"Type yes to permanently delete {n} archived session(s) (backed up, --undo restores them): ",
            f"输入 yes 确认彻底删除 {n} 个已归档会话（会先备份，可 --undo 还原）：",
        )
    else:
        prompt = t(
            f"Also permanently delete the {n} archived session(s) above? "
            "Type yes to delete (backed up, --undo restores them), Enter to keep them: ",
            f"顺便彻底删除上面 {n} 个已归档会话吗？输入 yes 删除（会先备份，可 --undo 还原），回车保留：",
        )
    if (ask(prompt) or "").strip().lower() == "yes":
        return True
    warn(t("Purge skipped; archived sessions are kept.", "已跳过清除，已归档会话保留。"))
    return False


def write_plan(args, ctx, report, do_purge, interactive):
    """执行写入（和清除），打印结果；返回写入摘要。"""
    ensure_desktop_stopped(args.force, interactive)
    if remember_cli_account(ctx.book, ctx.cli_acct):
        save_account_book(ctx.book)
    created, moved = [], []
    for r in report.rows:
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
        c2, moved = execute_purge(report.purge_plan(), trash)
        created += c2
    journal = write_journal(created, moved, stamp) if created or moved else None
    if not args.json:
        print()
        if journal:
            summary = [todo(t(f"Created {len(created)} file(s)", f"已写入 {len(created)} 个文件"))]
            if moved:
                summary.append(danger(t(f"moved {len(moved)} to backup", f"移除 {len(moved)} 个文件/目录")))
            print(style(ok(f"{sym('ok')} " + t("Done", "完成")), "bold") + "  " + muted(" · ").join(summary))
            if moved:
                tip(t(f"backup: {trash}", f"备份：{trash}"))
            tip(t(f"journal: {journal}", f"日志：{journal}"))
            tip(
                t(
                    "reopen the desktop app to check; if anything looks wrong, run --undo",
                    "请重新打开桌面端查看；如有问题运行 --undo 撤销",
                )
            )
        else:
            print(t("Nothing to write.", "没有需要写入的会话。"))
    return {"created": len(created), "moved": len(moved), "journal": str(journal) if journal else None}


def decide(args, report, interactive):
    """菜单以外的情况（--apply、-i、脚本里）决定是否写入、是否清除。返回 (动作, 是否清除)，
    动作是 "write" / "none"。"""
    apply, do_purge = args.apply, bool(report.purge_plan())
    if not (interactive and report.has_work()):
        return ("write" if apply else "none"), do_purge
    if not apply:
        print()
        apply = confirm_key(t("Write now?", "现在写入？"))
    if apply and do_purge:
        do_purge = confirm_purge(args, report)
        if not do_purge and not report.n_files():
            apply = False  # 只剩清除而又跳过了，就不必再检查桌面端、写日志
    return ("write" if apply else "none"), do_purge


def main(argv=None):
    try:
        run(argv)
    except KeyboardInterrupt:  # Ctrl-C：终端已由 RawKeys 恢复，不打印 Python 的回溯信息
        print()
        print(t(*CANCELLED), file=sys.stderr)
        sys.exit(130)


def run(argv=None):
    use_safe_stdio()
    cfg = load_config()
    apply_lang_flag(argv, cfg)
    ap = build_parser()
    ap.set_defaults(**cfg)
    args = ap.parse_args(argv)
    if args.completion:
        print(zsh_completion(ap), end="")
        return
    global _UI, _LANG_ZH
    _UI = cfg.get("ui", "keys")
    interactive = not args.no_input and not args.json and is_interactive()

    def terminal():
        def val(name, default):
            v = getattr(args, name)
            return default if v is ASK else v

        color, icons, layout = val("color", "auto"), val("icons", "symbols"), val("layout", "auto")
        setup_terminal("never" if args.json else color, icons, "plain" if args.json else layout)

    terminal()
    # 只写了参数名、没写值：当场选择
    if args.lang is ASK:
        need_terminal("--lang", interactive)
        args.lang = ask_option(t("Output language", "输出语言"), LANG_CHOICES, "zh" if t("en", "zh") == "zh" else "en")
        _LANG_ZH = args.lang == "zh"
    for name, choices, title in (
        ("color", COLOR_CHOICES, t("Colors", "颜色")),
        ("icons", ICON_CHOICES, t("Icons", "图标")),
        ("layout", LAYOUT_CHOICES, t("Layout", "布局")),
    ):
        if getattr(args, name) is ASK:
            need_terminal(f"--{name}", interactive)
            current = cfg.get(name) or {"color": "auto", "icons": "symbols", "layout": "auto"}[name]
            setattr(args, name, ask_option(title, choices, current))
            terminal()
    argv_list = sys.argv[1:] if argv is None else list(argv)
    if args.save_defaults and interactive and not any(a.split("=")[0] in CONFIG_FLAGS for a in argv_list):
        settings_wizard(cfg)  # 只写了 --save-defaults：打开设置表单
        return
    if args.save_defaults:
        saved = {k: getattr(args, k, None) for k in CONFIG_KEYS if getattr(args, k, None) is not None}
        p = save_config(saved)
        if not args.json:
            items = ", ".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in saved.items())
            print(style(sym("ok"), "green"), t(f"Saved defaults to {p}: {items}", f"已保存默认设置到 {p}：{items}"))
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

    ctx = load_context(args)
    if args.set_alias:
        given = [x for x in args.set_alias if x is not ASK]
        if given:
            set_aliases(given, ctx.all_accts)
        if ASK in args.set_alias:
            need_terminal("--set-alias", interactive)
            alias_wizard(ctx)
        return
    if args.show_template:
        accts = ctx.accounts(args) if args.account and ASK not in args.account else ctx.all_accts
        if interactive and len(accts) > 1 and not args.account:
            options = [account_option(a, o, ctx) for _, a, o, _ in accts] + [
                {"label": t("All accounts", "全部账号"), "value": "all"}
            ]
            picked = select_one(t("Show the template of", "查看哪个账号的模板"), options)
            if picked is None:
                return
            accts = accts if picked == "all" else [accts[picked]]
        show_templates(ctx, accts)
        return
    resolve_asks(args, ctx, cfg, interactive)

    # 终端里没写 -a / -i：预览后进入总菜单，管理类操作做完重新预览
    hub = interactive and not args.apply and not args.interactive
    first = True
    while True:
        if hub and not first and _keys is None and keys_available():
            sys.stdout.write("\033[H\033[J")  # 重新预览前清屏，输出不会越堆越长
        report = build_plan(args, ctx, interactive)
        if args.interactive and first:
            pick_interactively(report.rows)
        first = False
        if not args.json:
            report.print_plan(verbose=args.verbose, quiet=args.quiet)
        if not hub:
            action, do_purge = decide(args, report, interactive)
            break
        can_undo = JOURNAL_DIR.exists() and any(JOURNAL_DIR.glob("journal-*.json"))
        filters = " · ".join(
            x
            for x in (
                t(f"{len(args.account)} account(s)", f"{len(args.account)} 个账号") if args.account else "",
                short_path(args.project) if args.project else "",
            )
            if x
        )
        choice = hub_menu(report, can_undo, filters, len(read_exclude_file(default_exclude_file())))
        if choice == "undo":
            print()
            undo(args.force, interactive)
            return
        if choice == "filter":
            filter_menu(args, ctx)
        elif choice == "alias":
            alias_wizard(ctx)
        elif choice == "exclude":
            manage_exclude_file()
        elif choice == "settings":
            new = settings_wizard(load_config())
            if new is not None:
                apply_settings(args, new)
        else:
            if choice == "pick":
                pick_interactively(report.rows)
            do_purge = choice in ("write", "pick") and bool(report.purge_plan())
            action = "write" if choice in ("write", "pick") and report.has_work() else "none"
            if action == "write" and do_purge:
                do_purge = confirm_purge(args, report)
                if not do_purge and not report.n_files():
                    action = "none"
            break

    written = write_plan(args, ctx, report, do_purge, interactive) if action == "write" else None
    if action != "write" and not args.json and report.has_work():
        if interactive:
            print(t("Nothing was written.", "没有写入任何文件。"))
        else:
            print(
                muted(
                    t(
                        "\n(Preview only; nothing was written. Run again with --apply to write.)",
                        "\n（预览模式，未写入任何文件。确认无误后加 --apply 执行。）",
                    )
                )
            )
    if args.json:
        print(json.dumps(report.to_json(action == "write", do_purge, written), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
