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

> 如果提示"检测到桌面端正在运行"，说明桌面端还挂在菜单栏或托盘里，**这次什么都没有写入**。彻底退出后重新运行即可。

系统语言（`LC_ALL`、`LC_MESSAGES`、`LANG`，或 Windows 的显示语言）是中文时输出中文，否则输出英文。可以用 `CLAUDE_SESSION_SYNC_LANG=zh` 或 `en` 强制指定。

---

## 同步规则

| 情况 | 处理方式 |
|---|---|
| 会话已经在某个账号的列表里（不管有没有归档） | 不会给这个账号重复添加 |
| 会话在**任意一个账号**里被归档过（`isArchived` 字段或 `archived-sessions.idx`） | 所有账号都不补。加 `--per-account-archive` 改为只看当前账号 |
| 会话在任意账号里被桌面端删除过（存在 `deleted_<会话ID>` 标记） | 所有账号都不补 |
| 会话在 `--exclude` 或 `--exclude-file` 里 | 不补 |
| 最近 2 分钟内还在写入的会话（可能正在运行） | 跳过 |
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
- 最近 2 分钟内还在写入的会话会跳过（按 `.jsonl` 的修改时间判断）；
- 备份目录不会自动清理，确认没问题后可以手动删除 `~/.claude-desktop-sync/trash-*`。

---

## 参数

| 参数 | 说明 |
|---|---|
| `-h`, `--help` | 显示帮助、示例和注意事项 |
| `-V`, `--version` | 显示版本号 |
| （不加参数） | 预览模式 |
| `--apply` | 实际写入 |
| `--undo` | 撤销最近一次写入或删除 |
| `--show-template` | 显示每个账号的模板字段后退出（排查用） |
| `--account <账号ID>` | 只同步指定账号，可以写多次 |
| `--project <字符串>` | 只处理工作目录里包含这个字符串的会话（同步和清除都生效） |
| `--exclude <会话ID>` | 排除某个会话，同步和清除都跳过，可以写多次 |
| `--exclude-file <文件>` | 从文件读取排除列表，每行一个会话 ID |
| `--per-account-archive` | 只按当前账号的归档状态来排除 |
| `--purge-archived` | 同步后彻底删除已归档会话（见上一节） |
| `--projects-dir <路径>` | CLI 会话目录，默认 `~/.claude/projects`，也会读取 `CLAUDE_CONFIG_DIR` |
| `--desktop-root <路径>` | 手动指定 `claude-code-sessions` 目录，可以写多次 |
| `--force` | 桌面端正在运行时也强制执行 `--apply` 或 `--undo`（不推荐，桌面端可能覆盖掉改动） |

示例：

```bash
# 只同步 myapp 项目到某个账号
claude-session-sync --account 1a2b3c4d-... --project myapp --apply

# 排除不想要的会话
claude-session-sync --exclude-file ~/no-sync.txt --apply
```

会话 ID 就是 `~/.claude/projects/<项目>/` 下 `.jsonl` 文件的文件名；账号 ID 是 `claude-code-sessions/` 下第一层目录名，预览输出里会显示前 8 位。

---

## 撤销与日志

每次 `--apply` 都会在 `~/.claude-desktop-sync/journal-<时间>.json` 里记录（时间精确到微秒，连续运行也不会互相覆盖）：

- `created`：这次新建的文件（索引文件、`deleted_` 标记）；
- `moved`：这次移到备份目录的文件，以及被改写前的 `archived-sessions.idx` 备份。

`--undo` 会删除新建的文件，把备份移回原位，然后把那份日志重命名为 `.undone`。多次运行 `--undo` 可以按时间倒序逐次撤销。

撤销只处理脚本自己操作过的文件，不会动桌面端原有的会话。和 `--apply` 一样，`--undo` 也要求先彻底退出桌面端。

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

欢迎提 issue 和 PR，中英文都可以。开发环境、测试、提交规范和发版流程见 [CONTRIBUTING.md](CONTRIBUTING.md)；可能导致数据丢失或泄露的问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

[MIT](LICENSE)。Claude 是 Anthropic 的商标，本项目与 Anthropic 没有任何关联。
