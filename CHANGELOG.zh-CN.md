# 更新日志

[English](https://github.com/xushenjie/claude-session-sync/blob/main/CHANGELOG.md) | 简体中文

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。本文件与英文版逐条对应，以英文版为准。

## [Unreleased]

### 新增
- `--repair` 和菜单按键 `r` 清理 0.5.0 及以前写入、带着别的会话状态的索引条目。只改本工具新建过的条目（按写入日志查找），原文件先备份，`--undo` 可以还原。

### 修复
- 同步的会话不再复制模板会话自己的状态。之前每条新索引都会继承模板会话的 PR、分支、git 锚点和回合总结，导致桌面端侧边栏的每个同步会话都显示别的仓库的 PR。现在新条目只沿用权限模式，其余字段交给桌面端补齐，和桌面端自己收养 CLI 会话时的做法一致（在桌面端 5.1231.2 观察到）。

## [0.5.0] - 2026-11-12

### 新增
- `accounts.json` 成为"已知账号簿"，记下工具见过的所有账号/组织目录。目录已经不存在的账号（桌面端退出登录或数据被清理）会继续出现在列表里，暗色标注"目录不存在"和最后见到的时间，不再悄悄消失。升级时会从写入日志里补回以前的账号。
- `--forget-account` 和菜单按键 `g` 可以把不在的账号从 `accounts.json` 删除；`--json` 输出里列在 `missing_accounts` 下。
- 新增"本次处理"列，说明每个组织这次会怎样：补 N 个会话、已同步、跳过（无模板或目录不存在），或因为 `--account` 没选中而不处理。`--json` 里每个账号也加了同样含义的 `action`。
- `--set-alias` 和菜单按键 `n` 可以在树形列表里给整个账号或单个组织起名。

### 变更
- 每次运行（包括预览）都会把账号目录和当前 CLI 账号记到 `accounts.json`；预览不会写桌面端的任何数据。
- 账号表改成 客户端 → 账号 → 组织 的树形结构，一个账号的多个组织不会再看起来像多个账号。账号行显示别名和打码的邮箱，组织行显示组织名，个人组织显示成"个人组织"（属于别的账号时写明是谁的）。顶部分别统计账号数和组织数。
- 挑选账号的列表把每个组织写成 `账号 › 组织`。
- 框线面板之间空一行。
- 会话有了自己的图标（`◇`，`--icons emoji` 时是 `💬`），`⏳` 只用来表示运行中。
- 会话时间总是带年份（`2026-11-12 11:22`），往年的会话也保留时分。

## [0.4.0] - 2026-11-12

### 新增
- 短参数：`-a`（`--apply`）、`-p`（`--purge-archived`）、`-r`（`--include-recent`）、`-u`（`--undo`）。
- `--completion zsh` 输出 zsh 补全脚本，可以补全参数和取值。
- `--icons symbols|emoji|ascii`（也可以在 `config.toml` 里设 `icons`）选择状态图标。
- 在终端里，预览最后总会出现菜单，不再是"现在写入？[y/N]"：回车写入，`2` 先挑选账号和会话，`3` 撤销最近一次写入，`f` 只看某些账号或项目，`n` 给账号起名，`x` 管理排除列表，`s` 修改默认设置，`q` 退出。`-a` 跳过菜单。
- 用键盘挑选列表：`↑` `↓`（或 `j` `k`）移动，空格勾选，`a` 全选，`/` 筛选，数字和字母直接跳到对应项，回车确认光标所在的默认项，Esc 返回。读不了单个按键的终端，或 `config.toml` 里设置了 `ui = "simple"` 时，改为编号输入。
- 在终端里不写值的参数会弹出列表：`--lang`、`--color`、`--icons`、`--layout`、`--account`、`--project`、`--exclude`、`--exclude-file`、`--set-alias`（会预填现在的名称）。不在终端里时以退出码 2 结束。
- `--layout auto|boxed|plain`（也可以在 `config.toml` 里设 `layout`）：用适应终端宽度的框线面板和表格输出，或输出纯文本。`auto` 只在终端里画框线；显示不了框线字符的终端改用 ASCII 框线。
- 在终端里只写 `--save-defaults` 会打开设置表单。
- 只在需要时才提问：在终端里遇到最近 2 分钟内仍在写入的会话会问是否一并同步，写入时发现可清除的已归档会话会问是否顺便删除。`--[no-]include-recent`、`--[no-]purge-archived` 可以预先回答。
- `~/.claude-desktop-sync/config.toml` 保存 `lang`、`color`、`icons`、`layout`、`ui`、`purge_archived`、`include_recent`、`per_account_archive` 的默认值；`--save-defaults` 保存本次运行的取值。

### 变更
- `--help` 开头先列出 5 个常用命令，参数分成常用、账号与筛选、输出、高级选项、其他几组；说明按显示宽度折行，中文不会超出终端宽度。
- 输出更清楚：一行汇总关键数量；账号表格对齐，当前 CLI 账号标绿色 `●`（其他账号灰色 `●`，没有模板的标黄色 `⚠`），带 ID 前缀，以及解释数量差异的说明列（已归档、已排除、运行中、孤立索引、仅命令会话）；待补会话按工作目录分组、标题对齐；颜色含义固定（绿色完成、青色待补、黄色注意、红色删除）；菜单带颜色；写入后给出简短汇总；已全部同步时给出灰色提示。
- 预览后直接回车现在会写入；不想写入请输入 `q` 或 `n`。
- `-i` / `--interactive` 改用键盘挑选，不再是编号列表。
- 按 Ctrl-C 会干净地退出（退出码 130）并恢复终端状态。

### 修复
- 以斜杠命令（如 `/implement`）或 IDE 提示开头的会话不再被当作空会话跳过。现在只要有用户正文或模型真正回复过就算有效会话；只执行过 `/model` 这类本地命令的会话仍然跳过。标题依次退回到会话的 AI 标题、命令及其参数。

## [0.3.2] - 2026-11-11

### 新增
- 新增简体中文更新日志 `CHANGELOG.zh-CN.md`，CI 会检查它的版本标题与英文版一致。

## [0.3.1] - 2026-11-11

### 修复
- Windows 上用正斜杠记录的工作目录现在也会缩写成 `~`。

## [0.3.0] - 2026-11-11

### 新增
- 在终端里预览结束后会询问"现在写入？[y/N]"，不用再运行一遍。桌面端正在运行时会等你退出，而不是直接退出。脚本、管道和 CI 中从不提问；`--no-input` 也可以在终端里关闭提问。
- `-i` / `--interactive`：从编号列表里挑选账号和会话（`1-5,8`、`!3`、`none`）。没选中的会话可以记到 `~/.claude-desktop-sync/exclude.txt`，之后每次运行都会读取。
- `-q` / `--quiet`、`-v` / `--verbose`、`--json`，以及 `--color auto|always|never`（遵循 `NO_COLOR`）。
- 在终端里，`--purge-archived` 需要输入 `yes` 才会删除，`--undo` 也会先确认。

### 变更
- 输出里每个会话只列一次，并标出会补到哪些账号，不再按账号重复列出；列表较长时只显示前 20 条，加 `-v` 显示全部。终端里使用颜色和符号（不支持时退回 ASCII），标题和路径按终端宽度截断。
- 排除列表文件支持 `#` 注释；`--exclude-file` 指定的文件不存在时会明确报错。

## [0.2.0] - 2026-11-11

### 新增
- 输出里显示账号名称：从 `~/.claude.json` 识别 CLI 当前登录的账号，每次 `--apply` 都会把它记到 `~/.claude-desktop-sync/accounts.json`，也可以用 `--set-alias <id>=<名称>` 给任意账号起名。邮箱一律打码显示。
- `--account` 支持 ID 前缀（比如输出里显示的 8 位），可以加 `/<组织ID前缀>`；前缀匹配到多个账号时会报错。

## [0.1.3] - 2026-11-11

### 新增
- `--lang en|zh`：为单次运行指定输出语言，优先于 `CLAUDE_SESSION_SYNC_LANG` 和系统语言。
- 英文 `--help` 中增加一行中文的切换提示，中文 `--help` 中增加对应的英文提示。

## [0.1.2] - 2026-11-11

### 修复
- README 中的链接（中文版、CONTRIBUTING、SECURITY、LICENSE）原来是相对路径，在 PyPI 项目页上会 404，现在改为指向 GitHub。

## [0.1.1] - 2026-11-11

### 新增
- `--include-recent`：同步时不跳过最近 2 分钟内写入的会话。这些会话在输出中会有标注，`--purge-archived` 依然不会处理它们。

## [0.1.0] - 2026-11-11

### 新增
- 把 CLI 会话同步到桌面端侧边栏，支持多个账号和组织。
- 遵循桌面端的归档状态（`isArchived`、`archived-sessions.idx`）和删除标记（`deleted_<id>`），这些会话不会被重新添加。
- 账号下还没有会话时，借用其他账号的会话作为模板。
- `--purge-archived`：同步后彻底删除已归档会话，删除前先把文件移到备份目录。遵循 `--exclude`、`--exclude-file` 和 `--project`，并跳过最近 2 分钟内写入过的会话。
- `--undo`：撤销最近一次写入或删除，包括还原备份。
- 桌面端运行时拒绝执行 `--apply` 和 `--undo`，除非加 `--force`。Windows 上桌面端和 Claude Code CLI 都叫 `claude.exe`，通过安装路径区分，无法识别的进程只给出警告；Linux 上的检测尽力而为。
- `--version`，以及带示例的 `--help`。
- 中英文输出，按系统语言自动选择；可用 `CLAUDE_SESSION_SYNC_LANG=en|zh` 覆盖。

[Unreleased]: https://github.com/xushenjie/claude-session-sync/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/xushenjie/claude-session-sync/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/xushenjie/claude-session-sync/releases/tag/v0.1.0
