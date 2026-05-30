# agent-team

通用、配置驱动的 Claude Code agent 团队编排插件。在你的项目里用 `/agent-team:agent-team start <任务>` 启动，多个专业 agent 自动协作、按依赖调度、产出可审核的交付候选。

> ⚠️ **先读这一段，理解它的本质（诚实声明）**
>
> agent-team 是一套**提示词驱动的编排脚手架**，不是确定性引擎。下文的"DAG 调度""失败重试"
> "Wave 并发"等，绝大多数是**写给 LLM 的指令**——主会话 LLM 扮演 Coordinator/Scheduler 去遵循，
> 而非有代码强制执行。可靠性依赖模型遵从，**不保证每次都严格按描述发生**。
>
> 真正由**代码强制**、不依赖 LLM 自觉的只有 hooks：
> - `hooks/files-scope-guard.py` —— strict 模式下越界写入 deny（需 README 配 `files_scope_enforcement: strict`）
> - `hooks/office-snapshot-guard.py` —— office 覆盖既有文件前自动写前快照
>
> 想验证某次运行是否**真的**按描述执行（而非 LLM 叙述了成功），用机器校验而非读它的 log：
> `bash pressure-test/verify/run-all.sh`（退出码 0 才算数）。背景见 `docs/16-诚实复审报告`。

## 核心特性

- **4 种项目场景**：software development / content creation / research / office tasks
- **DAG 调度**（提示词层）：按依赖顺序自动并发，无依赖的 agent 同一波次并行
- **隔离策略**：git-worktree（代码）/ directory-fork（内容/调研）/ none（办公）
- **失败处理**（提示词层）：retry 立即重派并附失败上下文（无系统定时退避）→ 超阈值升级给用户选择（retry / swap / skip / takeover）
- **审核流**：产物聚合为 PublishCandidate，用户 accept 才真正 apply
- **真实强制层**（代码）：files-scope strict 守卫 + office 写前快照（PreToolUse hooks）

---

## 安装

将 `agent-team.zip` 上传到 Claude Code 插件管理界面（本地上传）。安装后通过以下入口使用：

```
/agent-team:agent-team start <任务描述>
```

或通过用户级 `/team` 快捷命令（若已配置 `~/.claude/commands/team.md`）：

```
/team start <任务描述>
```

---

## 快速开始

### 1. 配置项目

在项目根目录 `README.md` 中添加 `team-config` 块：

```yaml
## team-config

```yaml
kind: development          # development | content | research | office
mode_default: commander    # commander | observer
isolation: git-worktree    # 由 kind 决定，可显式覆盖
publish_strategy: pr-style # pr-style | overlay | direct
apply_policy: require-review # require-review | auto-apply | dry-run

derivation_rules:
  - pattern: "feat|add|impl"
    roles: [developer]
  - pattern: "test|spec"
    roles: [tester]
  - fallback: coordinator

retry:
  max_attempts: 3
  backoff: exponential
```
```

### 2. 定义业务 agent

在 `.claude/agents/` 目录下为每个角色建一个 md 文件：

```markdown
---
name: developer
description: TypeScript 开发者，实现功能代码
capabilities: [typescript, node]
files_scope:
  write: ["src/**/*.ts"]
  read: ["src/**", "package.json"]
triggers:
  - "implement"
  - "create"
---

你是项目的 TypeScript 开发者。[具体职责说明...]
```

### 3. 启动任务

```
/agent-team:agent-team start 为 string-utils 库添加 slugify 和 truncate 函数，含单测和文档
```

Coordinator 自动判断复杂度、读取配置、派生 agent 集合，交 Scheduler 拆任务 + DAG 调度。

### 4. 审核产物

任务完成后收到 PublishCandidate 报告：

```
✅ quality: passed · 6/6 子任务通过 · 0 冲突
待 apply：src/slugify.ts, src/truncate.ts, tests/...

/agent-team accept   ← 确认 apply
/agent-team reject   ← 丢弃，保留 .agent-team/ 供复盘
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `/agent-team:agent-team start <任务>` | 启动完整编排流程（Coordinator→Scheduler→Merger） |
| `/agent-team:agent-team status` | 查看当前 DAG 进度和各子任务状态 |
| `/agent-team:agent-team accept` | 确认 PublishCandidate，执行 apply |
| `/agent-team:agent-team reject` | 丢弃 PublishCandidate，保留记录 |
| `/agent-team:agent-team resume` | 恢复中断的任务（session 断开后续跑） |
| `/agent-team:agent-team mode commander` | 切换为 Commander 模式（每步审批） |
| `/agent-team:agent-team mode observer` | 切换为 Observer 模式（仅失败/合并前询问） |

> **注**：若已配置 `~/.claude/commands/team.md` 快捷入口，可用 `/team <子命令>` 替代上述所有命令。

---

## 4 种场景默认配置

| kind | isolation | publish_strategy | apply_policy | 特性 |
|------|-----------|-----------------|-------------|------|
| **development** | git-worktree | pr-style | require-review | 各 agent 独立分支，Merger 做 git merge |
| **content** | directory-fork | overlay | require-review | 各 agent 独立目录，accept 时覆盖写入主目录 |
| **research** | directory-fork | overlay | require-review | 与 content 同构，差异在业务配置 |
| **office** | none | direct | dry-run | 共享工作区，默认串行，dry-run 不执行外部副作用 |

---

## 项目工作目录结构

任务运行时在 `.agent-team/` 下产生：

```
.agent-team/
  tasks/
    index.md          ← DAG 状态总览
    01-<name>.md      ← 各子任务文件（含 status / outputs / quality_gate）
  log.md              ← 调度日志（每轮一行）
  decisions.md        ← Coordinator 派生决策缓存
  candidate.md        ← Merger 产出的 PublishCandidate
  state.md            ← 当前模式（commander/observer）+ checkpoint
  forks/              ← content/research 场景的 directory-fork 目录
  snapshots/          ← office 场景的写前快照
  history/            ← overlay accept 时旧版文件备份
```

---

## 失败处理

当某个子任务失败时，Scheduler 先自动重试（最多 3 次，指数退避）。用尽后升级给用户选择：

| 选项 | 行为 |
|------|------|
| retry | 再给 N 次机会（可补充上下文） |
| swap_agent | 换一个能力相近的 agent 继续 |
| skip | 标记跳过，看下游能否继续 |
| user_takeover | 切 Commander，用户手动接手 |

---

## 许可证

MIT
