"""在临时目录里构造假的 CLI 会话和桌面端索引，全部是手写的假数据。"""

import json
import os
import time

from claude_session_sync import cli

ACCT = "aaaaaaaa-0000-4000-8000-000000000001"
ORG = "bbbbbbbb-0000-4000-8000-000000000001"
ORG_EMPTY = "bbbbbbbb-0000-4000-8000-000000000002"
OLD = time.time() - 3600  # 超过 RUNNING_GRACE_SEC，不会被当成"正在运行"


class Env:
    def __init__(self, root):
        self.root = root
        self.projects = root / "projects"
        self.desktop = root / "claude-code-sessions"
        self.journal = root / "journal"
        self.projects.mkdir()
        self.desktop.mkdir()

    def org_dir(self, acct=ACCT, org=ORG):
        d = self.desktop / acct / org
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_cli_session(
        self, sid, cwd="/work/demo", text="hello", project="-work-demo", mtime=OLD, extra=(), reply=True
    ):
        """reply=False 时助手消息是本地命令留下的 <synthetic> 占位，不算模型回复。"""
        d = self.projects / project
        d.mkdir(exist_ok=True)
        lines = [
            {
                "type": "user",
                "cwd": cwd,
                "timestamp": "2026-01-01T00:00:00.000Z",
                "message": {"role": "user", "content": text},
            },
            {
                "type": "assistant",
                "cwd": cwd,
                "timestamp": "2026-01-01T00:01:00.000Z",
                "message": {
                    "role": "assistant",
                    "model": "test-model" if reply else "<synthetic>",
                    "content": [{"type": "text", "text": "hi"}],
                },
            },
            *extra,
        ]
        p = d / f"{sid}.jsonl"
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def add_index(self, cli_id, local_id=None, archived=False, acct=ACCT, org=ORG, cwd="/work/demo"):
        local_id = local_id or f"local_{cli_id}"
        entry = {
            "sessionId": local_id,
            "cliSessionId": cli_id,
            "cwd": cwd,
            "originCwd": cwd,
            "createdAt": 1,
            "lastActivityAt": 2,
            "isArchived": archived,
            "title": f"title of {cli_id}",
            "model": "test-model",
        }
        p = self.org_dir(acct, org) / f"{local_id}.json"
        p.write_text(json.dumps(entry), encoding="utf-8")
        return p

    def index_entries(self, acct=ACCT, org=ORG):
        d = self.desktop / acct / org
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("local_*.json"))]

    def run(self, *args):
        cli.main(["--desktop-root", str(self.desktop), "--projects-dir", str(self.projects), *args])
