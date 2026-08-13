# s01 · Agent Loop — 一个循环 + bash = 一个 Agent

> *"One loop and Bash is all you need."*

## 本章要解决的问题

一个"能自己干活"的 AI 编程助手，代码上到底需要什么？答案出人意料地简单：**一个 while 循环**。

```
用户提问 --> messages[] --> LLM --> response
                                     |
                          stop_reason == "tool_use"?
                          /                         \
                        是                           否
                         |                            |
                   执行工具                      返回最终文本
                   结果塞回 messages[]
                   回到循环开头
```

关键点：

1. **`messages` 是唯一的记忆。** 模型是无状态的，每次请求都要把完整对话历史传过去。
2. **工具结果以 `user` 角色回传。** 在 API 协议里，`tool_result` 属于 user 消息的一部分。
3. **模型自己决定何时停止。** `stop_reason` 不是 `"tool_use"` 时，说明模型认为任务完成了。harness 不替模型做这个判断。

## 代码走读

[code.py](./code.py) 只有三块：

### 1. 工具定义 `TOOLS`

告诉模型"你有什么"。schema 用 JSON Schema 描述参数，`description` 是给模型看的使用说明书——写得越清楚，模型用得越对。

### 2. 工具实现 `run_bash`

真正干活的函数。注意两个 harness 级别的最小保护：

- **危险词拦截**：`rm -rf /`、`sudo` 等直接拒绝。这是 s03 Permission 的雏形。
- **超时控制**：120 秒强制结束，避免一条命令卡死整个 agent。

### 3. 核心循环 `agent_loop`

```python
def agent_loop(messages):
    while True:
        response = client.messages.create(...)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return                      # 模型决定停止
        results = [执行每个 tool_use]
        messages.append({"role": "user", "content": results})   # 结果回传
```

就这么多。不要在这里加 if-else 路由、不要加"第一步做什么第二步做什么"的编排——那些是模型的工作。

## 试一试

> **安全提示**：代码会执行模型生成的 shell 命令。建议在一个临时测试目录中运行，避免影响你的项目文件。s03 会加入权限控制。

**准备**（首次运行）：

```sh
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY 和 MODEL_ID
```

**运行**：

```sh
python s01_agent_loop/code.py
```

试试这些 prompt：

1. `新建一个 today.txt 文件，把今天的日期写进去`
2. `统计本项目每个章节目录下 code.py 的代码行数，按行数排序`
3. `当前 git 仓库的远程地址是什么？`

**观察重点**：模型什么时候调用工具（循环继续），什么时候不调用（循环结束）？

- Prompt 1：模型一般会先 `date` 拿日期再写文件，写完还可能 `cat` 读回验证——没人教它，这是训练出来的 agency。
- Prompt 2：这是个多步任务，观察模型如何组合 `find` / `wc` / `sort` 一条条试错调整——工具结果是新的观察。
- Prompt 3：如果目录不是 git 仓库，命令会报错。注意模型不会瞎编一个地址，而是诊断环境、如实汇报——错误也是观察。

## 这一章的局限

- 只有一个工具，工具调用是写死的 `if block.type == "tool_use": run_bash(...)` → **s02 解决**
- 模型可以执行任何命令，没有真正的权限边界 → **s03 解决**
- 上下文无限增长，迟早撑爆窗口 → **s08 解决**

## 动手练习

1. 把 `timeout` 从 120 改成 5，让模型跑 `sleep 10`，观察 agent 收到超时错误后会不会自己换个方式。
2. 在 `run_bash` 里把输出截断长度从 50000 改成 200，给模型一个大输出任务（比如 `cat` 一个大文件），看它如何应对被截断的观察结果。
3. 思考：为什么工具结果要用 `{"role": "user", "content": results}` 回传，而不是单独的角色？

## 下一章

[s02 Tool Use](../s02_tool_use/) — 加一个工具，只加一个 handler。
