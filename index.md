---
layout: home

hero:
  name: HarnessLearn
  text: 从 0 到 1 构建 Agent Harness
  tagline: 每次只加一个机制，17 章递进式中文教程
  actions:
    - theme: brand
      text: 开始学习 →
      link: /s01_agent_loop/
    - theme: alt
      text: 学习路径
      link: /timeline
    - theme: alt
      text: GitHub
      link: https://github.com/zhuliyi10/HarnessLearn

features:
  - icon: 🔄
    title: 一个循环统治一切
    details: 所有 AI Agent 共享同一个核心循环：调用模型 → 执行工具 → 回传结果。从这里开始。
  - icon: 🧱
    title: 每章只加一个机制
    details: 17 章递进式设计，从最小 Agent Loop 到完整多 Agent Harness，每章独立可运行。
  - icon: 🇨🇳
    title: 中文原创教程
    details: 每章配备详细中文 README 教程和带注释的可运行代码，零基础友好。
  - icon: 🛠️
    title: 动手实验
    details: 每章代码独立运行，只需 anthropic + python-dotenv，支持任何 OpenAI 兼容提供商。
  - icon: 🏗️
    title: 正交可组合
    details: 每个机制都是独立的：权限、hooks、记忆、子 Agent 可自由组合叠加。
  - icon: 🚀
    title: 生产级参考
    details: s15 集成章把前 14 章所有机制融合为一个完整的 Harness，接近生产系统。
---

## 核心模式

所有 AI 编程 Agent 共享同一个循环：

```python
while True:
    response = client.messages.create(messages=messages, tools=tools)
    if response.stop_reason != "tool_use":
        break
    for tool_call in response.content:
        result = execute_tool(tool_call.name, tool_call.input)
        messages.append(result)
```

> **Agency 来自模型，Agent 产品 = 模型 + Harness。**
> 模型是驾驶者，Harness 是载具。本项目教你造载具。
