#!/usr/bin/env python3
"""
agent-team git-worktree 运行时证据守卫（诚实复审方案二·收尾 / ADR-0055）。
PreToolUse hook（matcher: Write|Edit|NotebookEdit）。

背景：development 用 git worktree 隔离——各 subagent 在自己的 worktree（独立分支）里工作。
但 pr-style accept 后会 `git worktree remove` + 删分支 → 事后无证据证明隔离真发生过：
LLM 可不建 worktree、直接在主工作树上改，再谎称"worktree 隔离 ✅"，而我们无法戳穿。

worktree 路径由 Agent 工具动态创建、位置不固定，无法靠路径前缀识别。但 git 有**规范信号**：
linked worktree 的根目录是一个 `.git` **文件**（非目录），内容形如
    gitdir: /abs/main-repo/.git/worktrees/<wtname>
据此可判定"本次写入发生在某 worktree 内"，并解析出主仓库根 + 分支名。
留痕写到**主仓库**的 .agent-team/evidence/worktrees.jsonl，worktree 被删也带不走。

永远 allow（只记录，不阻塞）；fail-open。
"""
import sys, os, json, re
from datetime import datetime


def allow():
    sys.exit(0)


def find_dotgit(start_dir):
    """从 start_dir 向上找 .git（文件或目录）。返回 (path, is_file) 或 (None, None)。"""
    cur = start_dir
    while True:
        dg = os.path.join(cur, ".git")
        if os.path.exists(dg):
            return dg, os.path.isfile(dg)
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, None
        cur = parent


def parse_branch(gitdir_path):
    """读 worktree 的 HEAD（在 gitdir_path 下），返回分支名或短 SHA。"""
    head = os.path.join(gitdir_path, "HEAD")
    try:
        with open(head, "r", encoding="utf-8") as f:
            txt = f.read().strip()
    except Exception:
        return "unknown"
    m = re.match(r"ref:\s*refs/heads/(.+)$", txt)
    if m:
        return m.group(1)
    return txt[:12] if txt else "detached"


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
    abs_target = os.path.abspath(os.path.join(cwd, target))

    dg, is_file = find_dotgit(os.path.dirname(abs_target))
    if dg is None or not is_file:
        allow()  # 不在 git 仓库内，或在主工作树（.git 是目录）→ 非 worktree，无关

    # 解析 .git 文件中的 gitdir 指向
    try:
        with open(dg, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except Exception:
        allow()
    m = re.match(r"gitdir:\s*(.+)$", content)
    if not m:
        allow()
    gitdir_path = m.group(1).strip()

    # 主仓库根 = gitdir 路径中 "/.git/worktrees/" 之前的部分
    parts = gitdir_path.split("/.git/worktrees/")
    if len(parts) != 2:
        allow()  # 非标准 linked-worktree 结构
    main_root = parts[0]
    wtname = parts[1].split("/")[0]

    worktree_root = os.path.dirname(dg)  # 含 .git 文件的目录 = worktree 根
    try:
        rel = os.path.relpath(abs_target, worktree_root).replace(os.sep, "/")
    except Exception:
        rel = os.path.basename(abs_target)
    branch = parse_branch(gitdir_path)
    agent_type = payload.get("agent_type") or "main"
    session = payload.get("session_id") or "nosession"

    evidence_dir = os.path.join(main_root, ".agent-team", "evidence")
    try:
        os.makedirs(evidence_dir, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": str(session),
            "agent_type": agent_type,
            "worktree": wtname,
            "branch": branch,
            "file": rel,
        }
        with open(os.path.join(evidence_dir, "worktrees.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        allow()

    allow()


if __name__ == "__main__":
    main()
