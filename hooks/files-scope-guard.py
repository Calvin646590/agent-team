#!/usr/bin/env python3
"""
agent-team strict 模式守卫（ADR-0019 / 0043）。
PreToolUse hook（matcher: Write|Edit|NotebookEdit）。

机制（V-13 实测确立）：
- subagent 的工具调用 payload 含 agent_type；主会话调用无此字段。
- 据 agent_type 读 .claude/agents/<agent_type>.md 的 files_scope.write，
  把本次写目标与之比对，越界则 deny。

安全默认：只有当项目 README team-config 显式 `files_scope_enforcement: strict`
时才拦截；否则（advisory / 缺省）一律放行——避免误伤。
出错也一律放行（fail-open，hook 不该把正常流程搞挂）。
"""
import sys, json, os, re


def allow():
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def glob_to_regex(glob):
    # 把 files_scope glob 转正则：** → 任意（含/），* → 非/，? → 单字符
    out, i, n = [], 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and glob[i] == "/":
                    i += 1  # **/ 吃掉斜杠，匹配零或多层
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def extract_write_globs(agent_md_text):
    """从 agent md frontmatter 抽 files_scope.write 的 glob 列表。优先 pyyaml，回退正则。"""
    fm = agent_md_text.split("---", 2)
    if len(fm) < 3:
        return None  # 无 frontmatter
    front = fm[1]
    try:
        import yaml  # 可选
        data = yaml.safe_load(front) or {}
        fs = (data.get("files_scope") or {})
        w = fs.get("write")
        return list(w) if w else None
    except Exception:
        pass
    # 回退：找 write: [ ... ] 或 write 后的列表项
    m = re.search(r"write:\s*\[([^\]]*)\]", front)
    if m:
        items = [s.strip().strip('"\'') for s in m.group(1).split(",") if s.strip()]
        return items or None
    # 多行 list 形式
    m = re.search(r"write:\s*\n((?:\s*-\s*.+\n?)+)", front)
    if m:
        items = [re.sub(r'^\s*-\s*', '', ln).strip().strip('"\'')
                 for ln in m.group(1).splitlines() if ln.strip()]
        return items or None
    return None


def find_project_root(start):
    """从 start 向上逐级查找项目根（ADR-0052）。
    判据：该目录有 README.md 且其中含 `team-config`（agent-team 配置块的标志）。
    找到即返回；到文件系统根仍未找到返回 None。兼容 worktree / 任意子目录 cwd。
    """
    try:
        cur = os.path.abspath(start)
    except Exception:
        return None
    while True:
        readme = os.path.join(cur, "README.md")
        try:
            with open(readme, "r", encoding="utf-8") as f:
                if "team-config" in f.read():
                    return cur
        except Exception:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:  # 抵达文件系统根
            return None
        cur = parent


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        allow()

    agent_type = payload.get("agent_type")
    if not agent_type:
        allow()  # 主会话发起，不受 per-agent scope 约束

    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        allow()

    cwd = payload.get("cwd") or os.getcwd()
    # 找项目根（ADR-0052）：从 cwd 向上逐级找"含 .claude/agents/ 的 README.md 所在目录"。
    # 不能假设 cwd 就是项目根——subagent 的 cwd 可能是 worktree 路径或任意子目录，
    # 直接用 cwd 会读到错误/缺失的 README，导致 strict 静默 fail-open（唯一真强制形同虚设）。
    project_root = find_project_root(cwd)
    if project_root is None:
        allow()  # 向上都找不到带 team-config 的项目根 → 无从判断，放行

    # 仅在 README team-config 显式 strict 时才拦
    readme = os.path.join(project_root, "README.md")
    try:
        with open(readme, "r", encoding="utf-8") as f:
            readme_text = f.read()
    except Exception:
        allow()
    if not re.search(r"(?m)^[ \t]*files_scope_enforcement:\s*strict", readme_text):
        allow()  # advisory / 缺省 → 放行（行首锚定，避免正文/示例里的字符串误触发，P3-5 fix）

    agent_md = os.path.join(project_root, ".claude", "agents", f"{agent_type}.md")
    try:
        with open(agent_md, "r", encoding="utf-8") as f:
            agent_text = f.read()
    except Exception:
        allow()  # 找不到 agent 定义，不拦

    globs = extract_write_globs(agent_text)
    if not globs:
        allow()  # 该 agent 没声明 write scope，不拦

    # 目标转项目相对路径
    # 用 os.path.join(project_root, target) 保证相对 target 以 project_root 为基解析；
    # 若 target 已是绝对路径，os.path.join 自动忽略 project_root，行为等价。
    rel = os.path.relpath(os.path.abspath(os.path.join(project_root, target)),
                          os.path.abspath(project_root))
    rel = rel.replace(os.sep, "/")

    for g in globs:
        if glob_to_regex(g).match(rel):
            allow()

    deny(
        f"agent-team strict: '{agent_type}' 试图写 '{rel}'，超出其 files_scope.write "
        f"({', '.join(globs)})。如确需写入，请在 .claude/agents/{agent_type}.md 扩大 write 范围。"
    )


if __name__ == "__main__":
    main()
