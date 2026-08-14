# s15 · Integrated Harness — 多种机制，一个循环

> *"循环还是那个循环，只是外围设施变全了。"*

## 本章要解决的问题

前 14 章各自教了一个机制，但真实产品里它们必须**同时在线、协同工作**。本章把它们全部接回同一个 agent loop，验证一个核心论断：

**这些机制是正交、可组合的——循环不变，设施叠加。**

## 集成清单

| 来源 | 机制 | 在 s15 中的位置 |
|---|---|---|
| s02 | 工具池 dispatch map | `TOOLS` + `TOOL_HANDLERS` |
| s03 | 权限闸门 | 执行管线第 2 步 |
| s04 | 前后置 hooks | 执行管线第 1 / 4 步 |
| s05 | TodoWrite | `todo_write` 工具 |
| s06 | Subagent | `subagent` 工具 |
| s07 | 技能按需加载 | `load_skill` + SYSTEM 目录 |
| s08 | 上下文压缩 | 循环开头的 `maybe_compact` |
| s09 | 记忆 | 启动注入 SYSTEM + 退出沉淀 |
| s10 | 任务系统 | `task_*` 工具 + SYSTEM 展示 |
| s11 | 后台任务 | `bash_background` + 通知注入 |

## 一次工具调用的完整旅程

```
    tool_use 到达
        |
    [s04] pre hooks      审计日志落盘
        |
    [s03] permission     deny/ask/allow 三态判定
        |
    [s02] dispatch       TOOL_HANDLERS[name](**input) 执行
        |
    [s04] post hooks     错误打标、输出加工
        |
    tool_result 回传
```

```python
def execute_tool(tool_name, tool_input):
    for hook in PRE_HOOKS: ...           # s04
    decision = GATE.check(...)           # s03
    if decision == "deny": return ...
    if decision == "ask" and not GATE.ask_user(...): return ...
    output = TOOL_HANDLERS[tool_name](**tool_input)   # s02
    for hook in POST_HOOKS: ...          # s04
    return output
```

## 循环开头的三道"准备"

每轮 LLM 调用前依次做三件事：

```python
def agent_loop(messages):
    while True:
        maybe_compact(messages)             # s08: 上下文超预算先压缩
        inject_bg_notifications(messages)   # s11: 后台结果先送达
        response = client.messages.create(...)
```

这样模型每次"睁眼"看到的都是：容量可控的上下文 + 最新的后台进展。

## SYSTEM 是"常驻设施"的汇合点

启动时一次性注入的三样东西都来自各设施：

```python
SYSTEM = f"""...
可用技能目录: {SKILL_CATALOG_TEXT}     # s07
{render_memory(MEMORY)}                # s09
持久化任务池: {task_list()}             # s10
"""
```

注意取舍：**常驻 SYSTEM 的只有"目录/摘要/清单"这类轻量信息**，重内容（技能全文、任务详情）都是按需通过工具拉取——这是贯穿全课程的原则。

## 试一试

> **安全提示**：代码会执行模型生成的 shell 命令。建议在一个临时测试目录中运行。

```sh
python s15_integrated_harness/code.py
```

试试这些 prompt：

1. `帮我调研当前目录结构，然后制定计划，把 Python 文件清单写成 report.md；如果有 .md 文件顺便统计字数；涉及写文件前注意权限确认`
2. `后台统计所有 code.py 的总行数，等待期间读一下 timeline.md`
3. `我们项目规定：文档统一用中文写`（退出前说，看它会不会被记住）

**观察重点**：一条 prompt 串起多个机制——todo 列计划 → 权限确认弹窗 → 后台/subagent 可选 → 退出时沉淀记忆。

- Prompt 1：综合任务。模型会自己编排：调研可能派 subagent，多步骤会先 `todo_write`，写 `report.md` 前触发权限确认——没人告诉它用哪个机制，它按 SYSTEM 里的纪律自己选。
- Prompt 2：慢命令走 `bash_background`，等待期间不闲着——s11 的通知机制和 s02 的工具池在同一个循环里配合。
- Prompt 3：退出（q）时观察 `[memory]` 日志——s09 的记忆沉淀也集成进来了。各章机制不是孤岛，拼装起来就是一个完整 harness。

## 动手练习

1. 把 s14 的 MCP 接入也并进来：`register_mcp_plugins()` 之后把发现的工具加进 `TOOLS` 和 `TOOL_HANDLERS`。
2. 给执行管线加一个 PreToolUse hook：把每次工具调用也写进任务系统的"活动日志"。
3. 思考：为什么 `maybe_compact` 要放在 `inject_bg_notifications` 之前？如果顺序反了会有什么问题？

## 下一章

[s16 Workflow Runtime](../s16_workflow_runtime/) — 编排形状固定时，就把它写进代码。
