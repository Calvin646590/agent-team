#!/usr/bin/env python3
"""
agent-team directory-fork 运行时证据守卫（诚实复审方案二 / ADR-0054）。
PreToolUse hook（matcher: Write|Edit|NotebookEdit）。

背景：content/research 用 directory-fork 隔离——各 agent 在 .agent-team/forks/<fork>/ 内独立工作。
但 overlay accept 后 forks/ 被删，事后无任何证据能证明"隔离真发生过"：一个 LLM 完全可以
根本不建 fork、直接往主目录写，再谎称 directory-fork ✅，而我们无法戳穿（同 office 快照旧病）。

本 hook 在**写入 fork 的当下自动留痕**（追加到 .agent-team/evidence/forks.jsonl），记录
"哪个 agent、在哪个 fork、写了哪个文件、真实时间戳"。证据独立于 forks/ 目录，accept 删 fork
也带不走它。于是 verify_mechanism.py 能把该机制从 UNVERIFIABLE 升级为可后验：
  - fork 真的被创建并使用（没绕过隔离直写主目录）
  - 每个 agent 写在自己的 fork 里（隔离边界成立）
  - 时间戳真实（非事后编造）

设计：纯按路径结构识别（路径含 /.agent-team/forks/<fork>/），无需读 README；
永远 allow（只记录，绝不阻塞写入）；fail-open。
"""
import sys, os, json, re
from datetime import datetime


def allow():
    sys.exit(0)  # 不返回 decision = 不干预


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        allow()

    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        allow()

    cwd = payload.get("cwd") or os.getcwd()
    abs_target = os.path.abspath(os.path.join(cwd, target)).replace(os.sep, "/")

    # 识别 .../.agent-team/forks/<fork>/<rel> 结构
    m = re.search(r"^(?P<root>.*)/\.agent-team/forks/(?P<fork>[^/]+)/(?P<rel>.+)$", abs_target)
    if not m:
        allow()  # 不是写 fork → 与本 hook 无关

    project_root = m.group("root")
    fork = m.group("fork")
    rel = m.group("rel")
    agent_type = payload.get("agent_type") or "main"
    session = payload.get("session_id") or "nosession"

    evidence_dir = os.path.join(project_root, ".agent-team", "evidence")
    try:
        os.makedirs(evidence_dir, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": str(session),
            "agent_type": agent_type,
            "fork": fork,
            "file": rel,
        }
        with open(os.path.join(evidence_dir, "forks.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        allow()  # fail-open：留痕失败绝不拖累正常写入

    allow()


if __name__ == "__main__":
    main()
