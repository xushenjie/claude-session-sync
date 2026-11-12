# claude-session-sync

[English](README.md) | **简体中文**

[![CI](https://github.com/xushenjie/claude-session-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/xushenjie/claude-session-sync/actions/workflows/ci.yml)

把本地 Claude Code **CLI 会话**同步到 **Claude 桌面端 Code 标签页**的侧边栏，支持多账号和多组织，自动排除已归档和在桌面端删除过的会话；也可以顺带彻底清除已归档的会话。

> ⚠️ **非官方工具，与 Anthropic 无关。** 桌面端的索引格式没有公开文档，桌面端更新后可能失效。默认只预览，写入前会检查桌面端是否在运行，所有写入和删除都可以撤销。

---

## 为什么需要它

CLI 和桌面端的本地会话共用同一份对话记录，但**会话列表是各自维护的**：

```
桌面端侧边栏索引（按 账号/组织 分开）
  claude-code-sessions/<账号ID>/<组织ID>/
    ├─ local_<uuid>.json        会话索引，cliSessionId 指向下面的记录文件
    ├─ archived-sessions.idx    已归档会话列表（local_ ID）
    └─ deleted_<会话ID>         桌面端删除会话后留下的标记
        │
        ▼
对话记录（CLI 和桌面端共用，与账号无关）
  ~/.claude/projects/<项目目录>/<会话ID>.jsonl
        ▲
        └─ CLI 的 claude --resume 直接扫描这里
```

所以：

- CLI 能看到桌面端建的本地会话，桌面端默认看不到 CLI 建的会话；
- 切换账号或组织后，桌面端读取的是另一个索引目录，列表会变化；
- 官方的导入方式（`/desktop`、`claude --desktop --resume <id>`、桌面端的 Help → Import CLI sessions）一次只能导入当前账号，而且有时会导不全。

这个脚本会给**每个账号/组织**补上缺少的索引文件，让 CLI 会话出现在侧边栏里。同步时**不会修改任何 `.jsonl` 对话记录**；只有 `--purge-archived` 会删除已归档会话的记录，并且会先备份。

---

## 安装

需要 Python 3.9 或更高版本（macOS 自带的 `python3` 就可以），只用标准库，没有第三方依赖。

```bash
uv tool install claude-session-sync-cli
```

或者用 pipx：

```bash
pipx install claude-session-sync-cli
```

PyPI 上的包名是 `claude-session-sync-cli`，安装后的命令是 `claude-session-sync`。想安装还没发版的最新代码，用 `uv tool install git+https://github.com/xushenjie/claude-session-sync`。

不想安装的话，也可以直接下载单个文件运行，下文的 `claude-session-sync` 换成 `python3 cli.py` 即可：

```bash
curl -LO https://raw.githubusercontent.com/xushenjie/claude-session-sync/main/src/claude_session_sync/cli.py
```

Windows 上把 `python3` 换成 `py`。

另外，桌面端里**至少要有一个用桌面端建的 Code 会话**（任意账号都行），脚本要用它当模板。某个账号/组织下还没有会话时，会借用其他账号的会话当模板。

支持的桌面端目录（会自动查找）：

| 系统 | 路径 |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| macOS（第三方部署版） | `~/Library/Application Support/Claude-3p/claude-code-sessions` |
| Windows | `%APPDATA%\Claude\claude-code-sessions` |
| Windows（第三方部署版） | `%APPDATA%\Claude-3p\…` 或 `%LOCALAPPDATA%\Claude-3p\…` |
| Windows（微软商店版） | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| Linux | `~/.config/Claude/claude-code-sessions` |

如果你的路径不在上面，用 `--desktop-root` 手动指定。

---

## 快速开始

```bash
# 查看完整帮助
claude-session-sync --help

# 1. 预览：列出每个账号会补哪些会话，不写任何文件
claude-session-sync

# 2. 彻底退出桌面端（macOS 上按 ⌘Q；只关窗口或"重启"不算），然后写入
claude-session-sync --apply

# 3. 重新打开桌面端查看。如果有问题，撤销最近一次操作
claude-session-sync --undo
```

> 如果提示"检测到桌面端正在运行"，说明桌面端还挂在菜单栏或托盘里，**这次什么都没有写入**。彻底退出后重新运行即可（在终端里会等你退出后继续）。

在终端里可以省掉第 2 步：预览最后会出现一个菜单，直接回车就写入。详见[输出与交互](#输出与交互)。

系统语言（`LC_ALL`、`LC_MESSAGES`、`LANG`，或 Windows 的显示语言）是中文时输出中文，否则输出英文。临时切换加 `--lang zh` 或 `--lang en`；想长期使用中文，又不想改系统语言，运行一次 `--lang zh --save-defaults`（见[默认设置](#默认设置configtoml)），或者在 shell 配置文件（如 `~/.zshrc`）里加上 `export CLAUDE_SESSION_SYNC_LANG=zh`。

## 输出与交互

在终端里不带任何参数运行，其余操作基本都能靠按键完成：预览最后有一个菜单，不写值的参数会弹出列表让你选。预览大概是这样（数据是虚构的）：

```text
╭─ claude-session-sync 0.3.2 ──────────────────────────────────────────────────────╮
│ CLI 会话 4  ·  账号 3  ·  已归档/删除 1                                          │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ 账号 ───────────────────────────────────────────────────────────────────────────╮
│   │ 名称                        │ ID                │ 已有 │ 待补 │ 说明         │
├───┼─────────────────────────────┼───────────────────┼──────┼──────┼──────────────┤
│ ● │ 工作号                      │ 1a2b3c4d/9f8e7d6c │    2 │   +2 │              │
│ ● │ al***@example.com  当前 CLI │ 5e6f7a8b/0c1d2e3f │    2 │   +1 │ −1 已归档    │
│ ● │ 5e6f7a8b/9f8e7d6c           │                   │    2 │   +1 │ −1 已归档    │
╰───┴─────────────────────────────┴───────────────────┴──────┴──────┴──────────────╯
╭─ 待补会话（2 个） ───────────────────────────────────────────────────────────────╮
│ ▸ ~/work/docs                                                                    │
│   11-12 08:38  起草发布说明                                           → 工作号   │
│ ▸ ~/work/webapp                                                                  │
│   11-12 08:38  修复登录跳转                                           → 全部账号 │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ 清除已归档会话（1 个） ─────────────────────────────────────────────────────────╮
│ −  旧实验                                    索引 1 个 · CLI 文件 1 个  4d5e6f70 │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ 计划 ───────────────────────────────────────────────────────────────────────────╮
│ + 补 2 个会话（3 个账号共 4 个索引文件）                                         │
│ − 彻底删除 1 个已归档会话                                                        │
╰──────────────────────────────────────────────────────────────────────────────────╯

╭─ 接下来做什么？ ───────────────────────────────── ↑↓ 移动 · 回车确认 · Esc 返回 ─╮
│   同步                                                                           │
│ ❯ 1 按上面的计划写入                                                             │
│   2 先挑选账号/会话，再写入                                                      │
│   3 撤销最近一次写入                                                             │
│   管理                                                                           │
│   f 只看某些账号/项目                                                            │
│   n 给账号起名                                                                   │
│   x 排除列表                                                                     │
│   s 默认设置                                                                     │
│   q 不写入，退出                                                                 │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

- 顶部面板统计可用的 CLI 会话、桌面端账号，以及在任一账号里归档或删除过的会话。账号表里，绿色 `●` 是当前 CLI 登录的账号（名称后面标有 `当前 CLI`），灰色 `●` 是其他账号，黄色 `⚠` 表示该账号没有模板。ID 列是可以传给 `--account` 的前缀；**说明**列解释数量为什么和别的账号不同：`−N 已归档`（在某个账号里归档或删除过，所以不补）、`−N 已排除`、`−N 运行中`、`+N 孤立索引`（索引指向的会话记录已经不存在）、`+N 仅命令/空会话`。
- 待补会话按工作目录分组（`▸`），每个会话只列一次，`→` 后面是它要补进去的账号。默认最多列 20 个；`-v` 列出全部，并显示会话 ID 和更多统计；`-q` 只输出汇总和提醒。
- 面板跟随终端宽度，最宽 100 列，窄屏时自动收窄，过长的标题用 `…` 截断。`--layout plain`（或 `config.toml` 里的 `layout = "plain"`）输出同样的内容但不画框线，管道和文件默认也是这样。终端显示不了框线字符或用了 `--icons ascii` 时，改用 ASCII 框线。

### 菜单

输入和输出都是终端时，预览最后总会出现**接下来做什么？**菜单，即使已经全部同步（这时回车直接退出）：

| 按键 | 作用 |
|---|---|
| `1` / 回车 | 按上面的计划写入 |
| `2` | 先挑选账号和会话，再写入 |
| `3` | 撤销最近一次写入（有可撤销的记录时才显示） |
| `f` | 只看某些账号或某个项目，然后重新预览 |
| `n` | 给账号起名（见[账号名称](#账号名称)） |
| `x` | 排除列表：勾选会话，让它们重新同步 |
| `s` | 默认设置：语言、颜色、图标、布局，以及下面那些提问的默认回答，保存到 `config.toml` |
| `q` / Esc | 不写入，退出 |

`-a` / `--apply` 跳过菜单直接写入。脚本、管道、CI 里永远不会出现菜单或提问；在终端里也不想被问，加 `--no-input`。写入时如果桌面端还开着，会等你退出后按回车继续。

### 列表里的按键

| 按键 | 作用 |
|---|---|
| `↑` `↓`（或 `k` `j`） | 移动 |
| `←` `→` | 上一页 / 下一页；在设置表单里切换取值 |
| 空格 | 勾选或取消（多选列表） |
| `a` | 全选或全不选当前显示的项 |
| `/` | 输入文字筛选；回车保留筛选，Esc 清除 |
| `1`–`9`、字母 | 直接选中对应项 |
| 回车 | 确认；光标所在的就是默认项 |
| Esc / `q` | 返回，不做改动 |

如果终端读不了单个按键（例如 `TERM=dumb`），或在 `config.toml` 里设置了 `ui = "simple"`，所有列表都会变成编号输入：输入编号（`1-5,8`，`!3` 排除某一项，`none` 一个都不选）后回车。

### 不带值的参数

这些参数不写值时会弹出列表：`--lang`、`--color`、`--icons`、`--layout`、`--account`、`--project`、`--exclude`、`--exclude-file`、`--set-alias`。比如 `claude-session-sync --account` 可以勾选账号，`--set-alias` 会先问给哪个账号起名，再预填它现在的名称。不在终端里时这些参数仍然必须写值，否则以退出码 2 结束。

### 提问

- **只在需要时才问。** 有最近 2 分钟内仍在写入的会话时，在终端里会列出来，问要不要一并同步（默认不同步）；有可以清除的已归档会话时，写入时会问要不要顺便删除（默认保留）。用 `--[no-]include-recent`、`--[no-]purge-archived`，或在 `config.toml` 里设置同名的键，就可以预先回答，不再询问。
- `-i` / `--interactive` 写入前会打开账号列表（有多个账号要补时）和会话列表。取消勾选的会话可以记到 `~/.claude-desktop-sync/exclude.txt`，以后每次运行都会自动排除；在菜单里按 `x`（或删掉对应行）即可恢复同步。
- 清除已归档会话要你确认后才会删除；`--undo` 会先显示上一次运行做了什么，确认后再撤销。
- 任何时候按 Ctrl-C 都会直接退出，不写入（退出码 130）。

### 其他输出

- `--json` 以 JSON 输出同步计划（加 `--apply` 时还包括写入结果），方便脚本使用；它不会提问，也不带颜色和框线。
- 颜色有固定含义：绿色 = 当前账号、已同步或已完成，青色 = 将要补的，黄色 = 需要注意，红色 = 将被删除，灰色 = 细节。只在终端里使用颜色，遵循 `NO_COLOR`，也可以用 `--color always|never` 指定。
- `--icons emoji` 把符号换成 emoji（🟢 ⚪ ✅ 📥 ⚠️ 🧹 💡），`--icons ascii` 只用 ASCII。终端显示不了这些符号时自动换成 ASCII。
- 已全部同步时，会用灰色 `›` 提示孤立索引、上次写入的时间，以及（还没有 `config.toml` 时）`--save-defaults`。

---

## 同步规则

| 情况 | 处理方式 |
|---|---|
| 会话已经在某个账号的列表里（不管有没有归档） | 不会给这个账号重复添加 |
| 会话在**任意一个账号**里被归档过（`isArchived` 字段或 `archived-sessions.idx`） | 所有账号都不补。加 `--per-account-archive` 改为只看当前账号 |
| 会话在任意账号里被桌面端删除过（存在 `deleted_<会话ID>` 标记） | 所有账号都不补 |
| 会话在 `--exclude` 或 `--exclude-file` 里 | 不补 |
| 最近 2 分钟内还在写入的会话（可能正在运行） | 跳过；加 `--include-recent` 可以照样同步 |
| 空会话（没有用户消息）、找不到工作目录的记录 | 跳过 |
| 项目目录下子文件夹里的 `.jsonl`（子代理等） | 不扫描 |

新写入的索引文件**以这个账号下已有的一个桌面端会话为模板**，只替换会话 ID、`cliSessionId`、工作目录、标题和时间，其他字段保持模板原样，尽量和你当前的桌面端版本保持一致。账号下没有会话时，优先借用同一个桌面端目录下其他账号的会话当模板。

会话标题的取值顺序：自定义标题 → 摘要 → 第一条用户消息，超过 60 个字会被截断。

脚本可以重复运行，已经同步过的会话会自动跳过。

---

## 彻底删除已归档会话（`--purge-archived`）

先照常同步，再把**所有账号里已归档的会话**彻底删除，效果和在桌面端手动删除一样：

1. 删除所有账号中指向该会话的 `local_*.json` 索引；
2. 把它从 `archived-sessions.idx` 里移除；
3. 在原账号目录写入 `deleted_<会话ID>` 标记，以后同步不会再把它加回来；
4. 删除 CLI 的 `<会话ID>.jsonl` 和同名子目录（子代理记录等）。

```bash
claude-session-sync --purge-archived           # 预览会删除哪些会话
claude-session-sync --purge-archived --apply   # 执行
```

- 被删的文件不会直接抹掉，而是移到 `~/.claude-desktop-sync/trash-<时间>/`，`--undo` 可以全部还原；
- 不管有没有加 `--account`，清除都会覆盖**全部账号**，避免别的账号留下指向已删文件的索引；
- `--exclude`、`--exclude-file` 和 `--project` 对清除同样生效，排除的会话不会被删；
- 最近 2 分钟内还在写入的会话一定会跳过（按 `.jsonl` 的修改时间判断），加了 `--include-recent` 也一样；
- 在终端里要输入 `yes` 才会删除，输入别的会保留已归档会话，同步照常进行；
- 备份目录不会自动清理，确认没问题后可以手动删除 `~/.claude-desktop-sync/trash-*`。

### 默认设置（`config.toml`）

每次都要输的参数，可以写进 `~/.claude-desktop-sync/config.toml`。命令行参数总是优先。最省事的改法是在菜单里按 `s`（或只运行 `--save-defaults`）：在表单里用 `←` `→` 切换取值，回车保存。

```toml
lang = "zh"                 # en | zh
color = "auto"              # auto | always | never
icons = "emoji"             # symbols | emoji | ascii
layout = "auto"             # auto | boxed | plain
ui = "keys"                 # keys（方向键列表）| simple（编号输入）
purge_archived = true       # 不写这一行：在终端里当场询问
include_recent = false      # 不写这一行：在终端里当场询问
per_account_archive = false
```

`--save-defaults` 会把本次运行中这几个参数（`ui` 除外）的取值写进文件，例如 `claude-session-sync --lang zh --no-include-recent --save-defaults`。删掉某一行就恢复内置默认值。输出语言依次看 `--lang`、`CLAUDE_SESSION_SYNC_LANG`、`config.toml`、系统语言。

---

## 参数

运行 `claude-session-sync -h` 可以看到同样分组的参数列表，开头是常用命令。写在 `[方括号]` 里的值在终端里可以省略，改为从列表里选（见[不带值的参数](#不带值的参数)）。

**常用**

| 参数 | 说明 |
|---|---|
| （不加参数） | 预览；在终端里随后给出[菜单](#菜单) |
| `-a`, `--apply` | 不显示菜单，直接写入 |
| `-i`, `--interactive` | 写入前从列表里挑选账号和会话（只能在终端里用） |
| `-p`, `--[no-]purge-archived` | 同步后彻底删除（或始终不删除）已归档会话（见上一节）。不指定时，在终端里发现可清除的会话会询问 |
| `-r`, `--[no-]include-recent` | 同步（或始终不同步）最近 2 分钟内仍在写入的会话（可能正在运行），输出里会单独标出；清除时仍然跳过。不指定时在终端里询问 |
| `-u`, `--undo` | 撤销最近一次写入或删除 |
| `--lang [en\|zh]` | 输出语言，优先于 `CLAUDE_SESSION_SYNC_LANG`、`config.toml` 和系统语言 |
| `--save-defaults` | 把本次的 `--lang`、`--color`、`--icons`、`--layout` 和几个 `--[no-]…` 参数保存到 `config.toml`，然后照常运行 |

**账号与筛选**

| 参数 | 说明 |
|---|---|
| `--account [<账号ID>]` | 只同步指定账号。写输出里显示的前 8 位就够了；加 `/<组织ID前缀>` 可以只选一个组织。可以写多次 |
| `--set-alias [<ID>=<名称>]` | 给账号起显示名称（见[账号名称](#账号名称)），名称留空则删除。可以写多次，保存后退出 |
| `--project [<字符串>]` | 只处理工作目录里包含这个字符串的会话（同步和清除都生效） |
| `--exclude [<会话ID>]` | 排除某个会话，同步和清除都跳过，可以写多次 |
| `--exclude-file [<文件>]` | 从文件读取排除列表，每行一个会话 ID，`#` 后面是注释。`~/.claude-desktop-sync/exclude.txt` 总是会一并读取 |

**输出**

| 参数 | 说明 |
|---|---|
| `-q`, `--quiet` | 只输出汇总和提醒 |
| `-v`, `--verbose` | 列出全部会话，并显示会话 ID 和更多统计 |
| `--json` | 以 JSON 输出结果，同时不提问、不带颜色 |
| `--no-input` | 即使在终端里也不提问 |
| `--color [auto\|always\|never]` | 彩色输出；默认 `auto`，只在终端里启用，遵循 `NO_COLOR` |
| `--icons [symbols\|emoji\|ascii]` | 状态图标：`● ✓ ⚠`（默认）、emoji 或纯 ASCII |
| `--layout [auto\|boxed\|plain]` | 框线面板和表格，或纯文本；默认 `auto`，只在终端里画框线 |

**高级选项**

| 参数 | 说明 |
|---|---|
| `--[no-]per-account-archive` | 只按当前账号的归档状态来排除 |
| `--force` | 桌面端正在运行时也强制执行 `--apply` 或 `--undo`（不推荐，桌面端可能覆盖掉改动） |
| `--projects-dir <路径>` | CLI 会话目录，默认 `~/.claude/projects`，也会读取 `CLAUDE_CONFIG_DIR` |
| `--desktop-root <路径>` | 手动指定 `claude-code-sessions` 目录，可以写多次 |
| `--show-template` | 显示每个账号的模板字段后退出（排查用） |

**其他**

| 参数 | 说明 |
|---|---|
| `--completion zsh` | 输出 zsh 补全脚本后退出（见 [Shell 补全](#shell-补全)） |
| `-h`, `--help` | 显示帮助、常用命令和注意事项 |
| `-V`, `--version` | 显示版本号 |

示例：

```bash
# 只同步 myapp 项目到某个账号
claude-session-sync --account 1a2b3c4d-... --project myapp --apply

# 排除不想要的会话
claude-session-sync --exclude-file ~/no-sync.txt --apply
```

会话 ID 就是 `~/.claude/projects/<项目>/` 下 `.jsonl` 文件的文件名；账号 ID 是 `claude-code-sessions/` 下第一层目录名，预览输出里会显示前 8 位，`--account` 和 `--set-alias` 直接用这 8 位就行。

### Shell 补全

zsh 可以补全参数和取值（`--icons <Tab>` 会列出 `symbols emoji ascii`，文件类参数会补全路径）。在 `~/.zshrc` 里 `compinit` 之后加上：

```bash
eval "$(claude-session-sync --completion zsh)"
```

如果通过别名运行（比如直接运行仓库里的源码），要打开 `complete_aliases`，并让别名使用同一套补全：

```bash
setopt complete_aliases
alias css='python3 ~/Projects/claude-session-sync/src/claude_session_sync/cli.py'
eval "$(css --completion zsh)"
compdef css=claude-session-sync
```

不打开 `complete_aliases` 的话，zsh 会先把别名展开，结果补全的是 `python3` 的参数。

### 账号名称

桌面端只按 ID 存放账号，所以输出里会尽量把能找到的名称显示出来：

```text
│ ● │ 工作号 · Acme Inc · fan***@example.com  当前 CLI │ b8073c71/677f8bdf │
│ ● │ 07ac42e2/00000000                                │                   │
```

- **CLI 当前登录的账号**：从 `~/.claude.json` 自动识别（只读取账号和组织 ID、邮箱、组织名）。
- **CLI 以前登录过的账号**：每次 `--apply` 都会把当前 CLI 账号记到 `~/.claude-desktop-sync/accounts.json`，切换账号后仍然能显示名称。
- **任意账号**：自己起别名。在菜单里按 `n`，或运行 `claude-session-sync --set-alias`，从列表里选账号后修改它现在的名称；也可以一步写完：`--set-alias b8073c71=工作号`。写成 `<账号>/<组织>` 前缀可以只给某个组织起名；名称留空则删除别名。

邮箱始终打码显示（如 `fan***@example.com`），因为这份输出经常会被贴进 issue。预览模式不会写入 `accounts.json`。

---

## 撤销与日志

每次 `--apply` 都会在 `~/.claude-desktop-sync/journal-<时间>.json` 里记录（时间精确到微秒，连续运行也不会互相覆盖）：

- `created`：这次新建的文件（索引文件、`deleted_` 标记）；
- `moved`：这次移到备份目录的文件，以及被改写前的 `archived-sessions.idx` 备份。

`--undo` 会删除新建的文件，把备份移回原位，然后把那份日志重命名为 `.undone`。多次运行 `--undo` 可以按时间倒序逐次撤销。在终端里会先显示上一次运行的时间和涉及的文件数，确认后再撤销。

撤销只处理脚本自己操作过的文件，不会动桌面端原有的会话。和 `--apply` 一样，`--undo` 也要求先彻底退出桌面端。

---

## 升级与卸载

用安装时的工具，配合**包名** `claude-session-sync-cli`（不是命令名）：

| 安装方式 | 升级 | 卸载 |
|---|---|---|
| uv | `uv tool upgrade claude-session-sync-cli` | `uv tool uninstall claude-session-sync-cli` |
| pipx | `pipx upgrade claude-session-sync-cli` | `pipx uninstall claude-session-sync-cli` |
| 单文件 | 重新运行[安装](#安装)一节里的 `curl` 命令 | 删除 `cli.py` |

不记得用哪种方式装的，运行 `uv tool list` 或 `pipx list` 看一下。

- **升级**会直接替换旧版本，命令名不变。再运行一次 `uv tool install` 不会升级，要用 `upgrade`。`~/.claude-desktop-sync/` 里的日志和备份会保留，升级后 `--undo` 依然可以撤销以前的操作。升级完可以用 `claude-session-sync --version` 确认版本。
- **卸载只删除程序本身。** 已经同步到桌面端侧边栏的会话和 `~/.claude-desktop-sync/` 都会保留。想一并撤销，请在**卸载前**退出桌面端，反复运行 `claude-session-sync --undo`，直到提示没有可撤销的记录；不再需要备份的话，再手动删除 `~/.claude-desktop-sync/`（里面也存着账号名称和别名）。

---

## 已知限制

- **索引格式是根据现有文件推测的**：归档会识别 `isArchived`、`archived`、`archivedAt`、`status: "archived"` 和 `archived-sessions.idx`；删除会识别 `deleted_<会话ID>` 标记。桌面端更新后如果格式变了，需要修改 `is_archived()` 或 `load_markers()`。
- **模板会原样复制原会话的大部分字段**（如 `envScopeId`、`postTurnSummary` 等）。借用其他账号的模板时，如果打开会话异常，先 `--undo`。
- **Windows 和 Linux 的支持是实验性的**：开发和真实数据测试都在 macOS 上进行，Windows 和 Linux 只跑过自动化测试，桌面端目录和索引格式都没有在真机上验证过。欢迎附上脱敏后的 `--show-template` 输出反馈。
- **Windows 上桌面端和原生安装的 CLI 都叫 `claude.exe`**，脚本按安装路径区分；分辨不出时只给出提醒、不会拒绝执行，请自己确认桌面端已经退出。
- **Linux 上的运行检测只是尽力而为**：Linux 没有官方桌面端，脚本只按 `Claude`、`claude-desktop` 两个进程名检测，检测不到不代表没在运行，写入前请自己确认已经退出。
- **不处理云端会话**：云端会话存在 claude.ai 上，跟本地文件无关。
- **不要在 CLI 和桌面端同时打开同一个会话并同时输入**，可能会把记录写乱。先后切换着用没问题。
- Claude Code 默认只保留 30 天的会话记录。想长期保留，在 `~/.claude/settings.json` 里设置：
  ```json
  { "cleanupPeriodDays": 3650 }
  ```

---

## 常见问题

**运行后桌面端还是看不到？**
1. 看输出里有没有"检测到桌面端正在运行"，有的话说明这次没写入；
2. 确认写入前已经彻底退出桌面端，写完重新打开；
3. 确认当前登录的账号/组织在输出的账号列表里，并且"待补"不是 0；
4. 检查会话是不是被分到了别的项目分组（比如子目录或 worktree）。

**删掉的会话又出现了？**
脚本会识别桌面端的 `deleted_` 标记，正常不会再加回来。如果是直接删除 `.jsonl` 或用其他方式删的，用 `--exclude` 排除。

**打开会话时报错？**
先运行 `--undo` 撤销，再[提交 issue](https://github.com/xushenjie/claude-session-sync/issues/new/choose)，附上 `--show-template` 的输出（**先把路径和标题脱敏**）。

**想用官方方式导入单个会话？**
```bash
claude --desktop --resume <会话ID>
```

---

## 参与贡献

欢迎提 issue 和 PR，中英文都可以。版本变化见[更新日志](CHANGELOG.zh-CN.md)；开发环境、测试、提交规范和发版流程见 [CONTRIBUTING.md](CONTRIBUTING.md)；可能导致数据丢失或泄露的问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

[MIT](LICENSE)。Claude 是 Anthropic 的商标，本项目与 Anthropic 没有任何关联。
