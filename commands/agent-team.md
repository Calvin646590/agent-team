---
description: agent-team 强制入口。/agent-team start <任务> 强制走 Coordinator→Scheduler→Merger 全流程（不许自己直接做）；还支持 status / mode / accept / reject / resume。
argument-hint: start <任务描述> | status | mode <commander|observer> | accept | reject | resume
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Agent, Skill]
---

# /agent-team —— agent-team 强制编排入口

用户用 `/agent-team` **显式要求**用 agent-team 框架。这是**命令，不是建议**。

> ⛔ **最重要的约束**：你（主会话）**不得自己把任务直接做掉**，**不得**"读几个文件就并行派几个 agent 了事"。你**必须**严格执行下面的框架协议。如果你觉得任务简单到不值得走框架——那也只能在"复杂度闸门"那一步**显式询问用户**，不能擅自跳过。绕过框架 = 本命令失败。

本次参数：`$ARGUMENTS`

按第一个词分发：

---

## `start <任务描述>`

你现在**必须以 Coordinator 身份运行**。先用 Skill 工具加载并遵循 `coordinator` skill 的完整协议。以下是**不可妥协**的执行清单（即便 coordinator skill 没自动加载，你也要照这个走）：

1. **复杂度闸门**（ADR-0040）：判断任务是否真需要多角色协作。
   - 真不需要 → **显式告诉用户**"这个任务单 agent 更划算，是否仍要用 team？"，等用户回答。**不要默默自己干。**
2. **读配置**：`.claude/agents/` 是否存在（否则报 NoAgentsDir + 提示 /agent-team init）；读 README team-config（kind / mode / derivation_rules / retry / files_scope_enforcement）。
3. **派生决策**：规则匹配 → LLM 兜底，定出本次业务 agent 集合（保守，能少则少）。
4. **进入 Scheduler**：用 Skill 工具调 `scheduler` skill，**必须真的做这些事**（缺一不可，否则视为绕过框架）：
   - 把任务拆成子任务，**逐个写 `.agent-team/tasks/<id>.md`**（含 `depends_on` / `owner` / `status` / `outputs` / `quality_gate`）
   - 写 `.agent-team/tasks/index.md`（DAG + 状态）
   - **按 DAG 调度**：有依赖的子任务**绝不并行**——上游 done 后才启动下游。无依赖的才并发
   - development 场景：业务 subagent 用 `isolation: worktree` 派发；上游分支先 merge 进 base，下游 worktree 再从更新后 base 派生（ADR-0042）
   - 失败由 Scheduler 跑 retry loop，超阈值才转 mediator
5. **收尾**：全部完成 → 用 Agent 工具派 `agent-team:merger` subagent（subagent_type: `"agent-team:merger"`）组装 PublishCandidate（pr-style diff / 整合分支）。**T3 中途不要真改主分支/不真发布**（apply 批量后置，ADR-0041）。
6. **present**：按 apply_policy + mode 把 candidate 给用户。**default require-review → 等用户 /agent-team accept 才 apply。**

> 自检：跑完后 `.agent-team/` 应有 tasks/ + index.md + log.md；development 应能看到 worktree 痕迹；content/research 应有 `.agent-team/forks/` 目录；office 应有 `.agent-team/snapshots/` 目录；最终交付是"待审 candidate"而非"已经偷偷改完 main"。如果这些都没有，说明你又绕过框架了——重来。

## `status`
读 `.agent-team/tasks/index.md` + `.agent-team/log.md`，给用户当前 DAG 进度（各子任务状态 + 当前 ready/blocked）。

## `mode <commander|observer>`
用 Skill 工具调 `mediator`（action: switch_mode）。

## `accept`
读 `.agent-team/candidate.md` 获取待审 PublishCandidate（schema 详见 `agents/merger.md` 步骤 4，字段含 `quality` / `task_failures` / `integration_errors` / `quality_unknowns` / `artifacts` / `conflicts` / `rollback_hint`）。确认 `quality` 为 `passed`（或用户明确接受 `failed` / `unknown` 风险）；按 `publish_strategy` 执行 apply：

- **`pr-style`**（development）→ `git merge --no-ff` 将整合分支并入目标分支；apply 后删除 integration 分支 + 清理临时 worktree
- **`overlay`**（content / research）→ 从 `.agent-team/forks/` 中各 fork 取 outputs 声明的文件，覆盖写入主工作区对应路径；旧版文件备份到 `.agent-team/history/<timestamp>/`；apply 后**只删 `.agent-team/forks/`**（ADR-0054：**绝不** `rm -rf .agent-team/`——`.agent-team/evidence/forks.jsonl` 是隔离证据，须保留以供事后审计）
- **`direct`**（office）→ 产物已在项目根目录；若 candidate 含 `external_actions`（如发邮件、写日历）且 `apply_policy != dry-run`，逐条执行并记录结果；`dry-run` 模式下仅标记完成 + 列出"若真执行会做的动作"；apply 后保留 snapshots（供后续回滚）

apply 完成后记 `.agent-team/log.md`（apply 时间 + strategy + 结果）。

## `reject`
读 `.agent-team/candidate.md` 确认有待审 candidate；按 `publish_strategy` 执行回滚：
- **`pr-style`**：删除 integration 分支 + 清理 worktree（`git worktree remove` + `git branch -D`）
- **`overlay`**：删除 `.agent-team/forks/`（产物未覆盖主工作区，无需回滚文件）
- **`direct`**：若有写前快照（`.agent-team/snapshots/`），提示可用 `cp` 回滚；无快照 → 提示"产物已在项目根目录，请人工确认是否需要手动撤销"

`.agent-team/` 目录**完整保留**供复盘和重跑。

## `resume`
读 `.agent-team/tasks/index.md` 确认有未完成子任务（至少一个 `pending` / `in_progress` / `blocked`）；读 `.agent-team/decisions.md` 恢复最近一次派生方案（`task_description` / `agents` / `context`）；用 Skill 工具调 `scheduler` skill，**明确告知**：任务文件已存在，跳过步骤 0-2（不覆盖现有 task 文件和 index.md），从步骤 3 DAG 主循环继续。

> 无 `.agent-team/` 或 index.md 显示全部完成 → 提示"无可恢复任务，请用 `/agent-team start` 开始新任务"。

---

无参数或无法识别 → 提示用法：`/agent-team start <任务> | status | mode <m> | accept | reject | resume`。
