# 学习路径

s01 到 s17：渐进式 Agent Harness 设计

## 总览

| # | 章节 | 主题 | 格言 | 核心机制 |
|---|------|------|------|----------|
| 01 | [Agent Loop](/s01_agent_loop/) | 循环 | 一个循环统治一切 | while + stop_reason |
| 02 | [Tool Use](/s02_tool_use/) | 分发 | 循环不动，只加一行注册 | dispatch map |
| 03 | [Permission](/s03_permission/) | 权限 | 危险动作必须在 shell 之前拦住 | allow/ask/deny 三态 |
| 04 | [Hooks](/s04_hooks/) | 钩子 | 横切行为挂在循环外 | pre/post hooks |
| 05 | [TodoWrite](/s05_todo_write/) | 计划 | 没有计划的 Agent 会跑偏 | 整表替换 + 外部状态 |
| 06 | [Subagent](/s06_subagent/) | 隔离 | 子任务值得拥有干净的上下文 | 独立 messages + 摘要回传 |
| 07 | [Skill Loading](/s07_skill_loading/) | 技能 | 知识按需加载 | frontmatter + lazy load |
| 08 | [Context Compact](/s08_context_compact/) | 压缩 | 上下文会满 | 四级压缩 |
| 09 | [Memory](/s09_memory/) | 记忆 | 有些事实要活过压缩和会话 | 筛选 + 提取 + 整理 |
| 10 | [Task System](/s10_task_system/) | 任务 | 大目标要拆成小任务 | 依赖图 + 就绪判定 |
| 11 | [Background Tasks](/s11_background_tasks/) | 后台 | 慢操作不该阻塞思考 | 通知队列 |
| 12 | [Cron Scheduler](/s12_cron_scheduler/) | 调度 | 定期工作由 harness 创建 | 持久化 jobs + 轮询 |
| 13 | [Agent Teams](/s13_agent_teams/) | 协作 | 一个 Agent 不够就组团队 | 原子认领 + 工作区隔离 |
| 14 | [MCP Plugin](/s14_mcp_plugin/) | 协议 | 外部服务走标准协议接入 | JSON-RPC + 命名空间 |
| 15 | [Integrated Harness](/s15_integrated_harness/) | 集成 | 机制叠加而非重写循环 | 执行管线组合 |
| 16 | [Workflow Runtime](/s16_workflow_runtime/) | 编排 | 确定性流程写进代码 | journal + 断点续跑 |
| 17 | [Goal Loop](/s17_goal_loop/) | 自治 | 循环何时结束不由执行者说了算 | 独立审查器 + 续轮 |

## 学习路径图

```
  第一站 · 地基          第二站 · 手脚          第三站 · 大脑
┌──────────────┐   ┌──────────────────┐   ┌──────────────┐
│ s01 循环      │   │ s05 计划          │   │ s09 记忆      │
│ s02 工具      │ → │ s06 子代理        │ → │ s10 任务系统   │
│ s03 权限      │   │ s07 技能加载      │   │ s11 后台      │
│ s04 钩子      │   │ s08 上下文压缩    │   │ s12 定时调度   │
└──────────────┘   └──────────────────┘   └──────┬───────┘
                                                  │
  第五站 · 收口          第四站 · 协作 ←────────────┘
┌──────────────┐   ┌──────────────────┐
│ s15 集成      │   │ s13 团队协作      │
│ s16 工作流    │ ← │ s14 MCP 插件      │
│ s17 目标循环  │   └──────────────────┘
└──────────────┘
```

## 架构层次

五个正交关注点组合成完整的 Agent：

### 🔧 Tools & Execution（4 章）
[s01: Agent Loop](/s01_agent_loop/) · [s02: Tool Use](/s02_tool_use/) · [s03: Permission](/s03_permission/) · [s04: Hooks](/s04_hooks/)

### 📋 Planning & Control（4 章）
[s05: TodoWrite](/s05_todo_write/) · [s06: Subagent](/s06_subagent/) · [s07: Skill Loading](/s07_skill_loading/) · [s08: Context Compact](/s08_context_compact/)

### 🧠 Memory Management（1 章）
[s09: Memory](/s09_memory/)

### ⏱️ Concurrency & Scheduling（3 章）
[s10: Task System](/s10_task_system/) · [s11: Background Tasks](/s11_background_tasks/) · [s12: Cron Scheduler](/s12_cron_scheduler/)

### 🤝 Multi-Agent Platform（2 章）
[s13: Agent Teams](/s13_agent_teams/) · [s14: MCP Plugin](/s14_mcp_plugin/)

### 🏗️ Integration（3 章）
[s15: Integrated Harness](/s15_integrated_harness/) · [s16: Workflow Runtime](/s16_workflow_runtime/) · [s17: Goal Loop](/s17_goal_loop/)

## 代码量增长

从 s01 的 120 行到 s15 的 713 行，每一行都有出处：

| 章节 | 行数 | 说明 |
|------|------|------|
| s01 | 120 | 最小循环 |
| s02 | 218 | +dispatch map |
| s03 | 247 | +权限门 |
| s04 | 254 | +hooks |
| s05 | 231 | +计划管理 |
| s06 | 217 | +子代理 |
| s07 | 237 | +技能加载 |
| s08 | 304 | +压缩 |
| s09 | 271 | +记忆 |
| s10 | 267 | +任务系统 |
| s11 | 268 | +后台任务 |
| s12 | 291 | +调度器 |
| s13 | 413 | +团队协作 |
| s14 | 248 | +MCP |
| s15 | **713** | 集成所有机制 |
| s16 | 249 | +工作流 |
| s17 | 249 | +目标循环 |
