# Harness 学习项目 — 从 0 到 1 打造 Agent Harness

> 参考 learn-claude-code 的思想，用 17 个递进式章节从零构建一个完整的 Agent Harness。
> **Agency 来自模型，Agent 产品 = 模型 + Harness。模型是驾驶者，Harness 是载具。本项目教你造载具。**

## 什么是 Harness

模型（LLM）通过训练获得了感知、推理、行动的能力，这不需要任何外部代码。但一个能干活的 Agent 产品，需要模型和 Harness 缺一不可：

```
Harness = Tools + Knowledge + Observation + Action Interfaces + Permissions

    Tools:          文件读写、Shell、网络、数据库
    Knowledge:      领域资料、API 规范、风格指南（技能按需加载）
    Observation:    git diff、错误日志、执行结果
    Action:         CLI 命令、API 调用
    Permissions:    沙箱隔离、审批流程、信任边界
```

模型做决策，Harness 执行；模型做推理，Harness 提供上下文。

## 核心模式

所有 AI Agent 的内核都是同一个循环：

```python
def agent_loop(messages):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=messages, tools=TOOLS,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":   # 模型决定停止 → 结束
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = TOOL_HANDLERS[block.name](**block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

模型决定何时调用工具、何时停止；代码只是执行模型的要求。本项目的 17 章就是围绕这个循环，逐章添加一个 Harness 机制。

## 17 章路线图

| 章节 | 主题 | 一句话格言 | 关键概念 |
|---|---|---|---|
| [s01](./s01_agent_loop/) | Agent Loop | *"一个循环 + bash = 一个 Agent"* | `messages` / `while True` / `stop_reason` |
| [s02](./s02_tool_use/) | Tool Use | *"加一个工具，只加一个 handler"* | dispatch map / 工具注册 |
| [s03](./s03_permission/) | Permission | *"先划边界，再给自由"* | 规则匹配 / 审批管线 |
| [s04](./s04_hooks/) | Hooks | *"挂在循环上，不写进循环里"* | PreToolUse / PostToolUse 扩展点 |
| [s05](./s05_todo_write/) | TodoWrite | *"没有计划的 agent 走哪算哪"* | 先列步骤再动手 |
| [s06](./s06_subagent/) | Subagent | *"子任务给全新的 messages[]"* | 上下文隔离 / 最终文本回传 |
| [s07](./s07_skill_loading/) | Skill Loading | *"用到时再加载，别全塞 prompt 里"* | 技能目录 / 按需注入 |
| [s08](./s08_context_compact/) | Context Compact | *"上下文总会满，要有办法腾地方"* | 截断 / 摘要四步压缩 |
| [s09](./s09_memory/) | Memory | *"记住该记的，忘掉该忘的"* | 筛选 / 提取 / 整理 |
| [s10](./s10_task_system/) | Task System | *"大目标拆成小任务，排好序，持久化"* | 任务图 / 依赖 / 落盘 |
| [s11](./s11_background_tasks/) | Background Tasks | *"慢操作丢后台，agent 继续思考"* | 后台线程 / 通知注入 |
| [s12](./s12_cron_scheduler/) | Cron Scheduler | *"定时触发，不需要人推"* | 持久化调度 / 到点触发 |
| [s13](./s13_agent_teams/) | Agent Teams | *"一个 Agent 顾不过来，就让队友分工"* | 队友 / 消息投递 / 任务认领 |
| [s14](./s14_mcp_plugin/) | MCP Plugin | *"能力不够？插上 MCP"* | 工具发现 / 工具池组装 |
| [s15](./s15_integrated_harness/) | Integrated Harness | *"多种机制，一个循环"* | 全部机制合体 |
| [s16](./s16_workflow_runtime/) | Workflow Runtime | *"编排形状固定时，就把它写进代码"* | 步骤编排 / journal 续跑 |
| [s17](./s17_goal_loop/) | Goal Loop | *"目标决定循环什么时候真正结束"* | 目标闸门 / 审查 / 续轮 |

## 学习路径

```
阶段 1-3（基础）      阶段 4-6（进阶）           阶段 7（收口）
能动手                能长期运行                  能编排并收尾
┌─────────────┐      ┌─────────────┐            ┌─────────────┐
│ s01-s04     │      │ s10-s14     │            │ s16-s17     │
│ 循环/工具/   │ ---> │ 任务/后台/   │    --->    │ 工作流/      │
│ 权限/钩子    │      │ 调度/团队/MCP│            │ 目标闭环     │
│ s05-s09     │      │             │            │             │
│ 计划/子agent │      │ s15 集成合体 │            │             │
│ 技能/压缩/记忆│      │             │            │             │
└─────────────┘      └─────────────┘            └─────────────┘
```

## 项目结构

```
HarnessLearn/
  README.md                # 本文件
  requirements.txt
  .env.example             # API Key 配置模板
  s01_agent_loop/
    README.md              #   中文教程
    code.py                #   独立可运行代码
  s02_tool_use/
  ...
  s15_integrated_harness/
  s16_workflow_runtime/
  s17_goal_loop/           # 终点章
  skills/                  # s07 使用的示例技能文件
```

每章的 `code.py` 都是**独立可运行**的，只依赖 `anthropic` + `python-dotenv`，不跨章 import，方便单独阅读和实验。

## 快速开始

```sh
cd HarnessLearn
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 编辑 .env 填入你的 ANTHROPIC_API_KEY

python s01_agent_loop/code.py           # 起点：一个循环 + bash
python s08_context_compact/code.py      # 复杂章：上下文压缩
python s17_goal_loop/code.py            # 终点章：用目标闭合循环
```

支持任何 Anthropic 兼容协议的 API（见 `.env.example` 中的提供商列表）。

## 每章怎么学

1. **读 README.md** — 理解本章要解决的 Harness 问题与设计思想；
2. **读 code.py** — 顶部有结构图，代码带逐行中文注释；
3. **跑起来** — 输入真实任务，观察模型的行动序列；
4. **动手改** — 每章结尾有"动手练习"，改一处看效果。

## 学完之后

走完 17 章，你会理解：

- **循环属于 agent，机制属于 harness。** 不要试图用编排代码替模型做决策。
- Harness 工程的五个抓手：实现工具、策划知识、管理上下文、控制权限、收集轨迹数据。
- 同一套模式可以泛化到任何领域 —— 编程 agent 的 harness 是 IDE 和终端，农业 agent 的 harness 是传感器和灌溉控制，酒店 agent 的 harness 是预订系统和客户渠道。

**造好 Harness，模型会完成剩下的。**
