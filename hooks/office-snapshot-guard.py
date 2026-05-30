#!/usr/bin/env python3
"""
agent-team office 写前快照守卫（ADR-0029 的真实代码化 / 诚实复审 item 6）。
PreToolUse hook（matcher: Write|Edit|NotebookEdit）。

背景：原设计把"office 写前快照"写成 scheduler 的 prompt 指令，靠 LLM 自觉执行
`mkdir`/`cp`。诚实复审证明这在实测中被跳过却被报告成功（空目录 + 伪造时间戳
20260526T000000）。本 hook 把该机制从"提示词剧场"改为**强制代码**：
office 场景下，任何子任务在覆盖一个**已存在**的文件之前，先由本 hook 自动把原文件
拷进 .agent-team/snapshots/<session>/，并在 MANIFEST 记**真实时间戳**。无需 LLM 配合。

语义（与 ADR-0029 一致）：
- 只快照「即将被覆盖的、已存在的」文件——新建文件无前态可存，不快照（这是正确行为）。
- 仅 office（isolation: none）生效；其它隔离策略有各自机制，本 hook 放行不干预。
- 永远 allow（快照是副作用，绝不阻塞写入）；任何异常 fail-open。
"""
import sys, json, os, re, shutil
from datetime import datetime


def allow():
    # 不返回 permissionDecision：表示"不干预"，让写操作正常进行
    sys.exit(0)


def project_root_from(cwd):
    return cwd or os.getcwd()


def is_office(project_root):
    """README team-config 是否 office 场景（kind: office 或 isolation: none）。"""
    readme = os.path.join(project_root, "README.md")
    try:
        with open(readme, "r", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return False
    # 粗取 team-config 段，避免误命中正文
    return bool(re.search(r"kind:\s*office", txt) or
                re.search(r"isolation:\s*none", txt))


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
    project_root = project_root_from(cwd)

    if not is_office(project_root):
        allow()  # 非 office：本 hook 不负责，放行

    # 解析目标为项目内相对路径
    abs_target = os.path.abspath(os.path.join(project_root, target))
    try:
        rel = os.path.relpath(abs_target, os.path.abspath(project_root))
    except Exception:
        allow()
    rel = rel.replace(os.sep, "/")

    # 跳过：项目外、.agent-team/ 内部、不存在（新建文件无前态）
    if rel.startswith("..") or rel.startswith(".agent-team/"):
        allow()
    if not os.path.isfile(abs_target):
        allow()  # 新建文件，无可快照前态——正确地不动作

    # 会话级分组：用 session_id（V-13 证实 payload 含此字段，跨同次会话稳定）
    session = payload.get("session_id") or "nosession"
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session))[:64]
    agent_type = payload.get("agent_type") or "main"

    snap_dir = os.path.join(project_root, ".agent-team", "snapshots", session)
    snap_path = os.path.join(snap_dir, rel)
    try:
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        # 已快照过同一文件的最早前态则不覆盖（保留任务开始前的版本）
        if not os.path.exists(snap_path):
            shutil.copy2(abs_target, snap_path)
            # 记真实时间戳到 MANIFEST（datetime.now，绝非手打占位符）
            manifest = os.path.join(snap_dir, "MANIFEST.jsonl")
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "agent_type": agent_type,
                "original": rel,
                "snapshot": os.path.relpath(snap_path, project_root).replace(os.sep, "/"),
            }
            with open(manifest, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        allow()  # fail-open：快照失败绝不拖累正常写入

    allow()


if __name__ == "__main__":
    main()
