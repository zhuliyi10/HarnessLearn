# s14 · MCP Plugin — 能力不够？插上 MCP

> *"工具的实现方与使用方彻底解耦，harness 只管接线。"*

## 本章要解决的问题

Harness 内置工具再多也有限：查天气、连数据库、操作浏览器、访问公司内部系统……不可能全写进 harness。

**MCP（Model Context Protocol）** 给出了标准答案：工具以独立"服务器"进程存在，harness 作为客户端按标准协议去**发现**并**调用**它们。装一个新能力 = 加一行配置，不碰循环、不碰分发。

## 协议交互

```
    harness（客户端）                      MCP 服务器（外部进程）
        |--- initialize --------------->   |
        |<-- serverInfo ----------------   |
        |--- tools/list ---------------->  |
        |<-- [{name, description, schema}] |   ① 工具发现
        |                                  |
        |  加命名空间前缀并入 TOOLS 工具池   |
        |  模型调用 mcp__weather__get_weather
        |--- tools/call {name, arguments}> |   ② 工具执行
        |<-- content -------------------   |
```

两个关键动作：

1. **工具发现**：`tools/list` 返回的每个工具自带 `name / description / input_schema`——格式和本地工具**一模一样**，所以能无缝并入 `TOOLS`。
2. **工具调用**：`tools/call` 传入工具名和参数，返回内容。

## 三个设计点

### 1. 命名空间防冲突

不同插件可能都有叫 `search` 的工具。所以并入工具池时统一加前缀：

```python
full_name = f"mcp__{server_name}__{tool['name']}"
```

模型看到的是 `mcp__weather__get_weather`，一眼能分辨来源。

### 2. 统一分发：MCP 工具就是普通 handler

对主循环而言，MCP 工具和本地工具没有区别——`TOOL_HANDLERS[full_name]` 只是一个**转发闭包**：

```python
def handler(**kwargs):
    return mcp_client.call_tool(orig_name, kwargs)
TOOL_HANDLERS[full_name] = handler
```

于是 `agent_loop` 一行都不用改，这就是"插上"的含义。

### 3. 配置即接线

```python
MCP_SERVERS = {
    "weather": [os.path.join(HERE, "demo_mcp_server.py")],
    # "filesystem": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
}
```

加新插件 = 往这个字典加一行，`register_mcp_plugins()` 启动时自动连上并发现工具。

## 附带的演示服务器

[demo_mcp_server.py](./demo_mcp_server.py) 是一个最小的 MCP 服务器：用 JSON-RPC 2.0 over stdio 实现了 `initialize / tools/list / tools/call` 三个方法，提供假的"天气"工具。读它能理解协议另一端长什么样。

## 试一试

```sh
python s14_mcp_plugin/code.py
```

启动时会打印 `[mcp] 已连接 demo-weather，发现 2 个工具`。然后：

```
s14 >> 北京今天天气怎么样？
```

观察模型调用 `mcp__weather__get_weather`——数据来自外部进程。

## 动手练习

1. 给 `demo_mcp_server.py` 加一个新工具（比如 `get_news`），重启 harness 验证它自动被发现。
2. 接入一个真实的 MCP 服务器（如 filesystem），体会"加一行配置"的扩展方式。
3. 思考：如果 MCP 服务器中途崩溃，`call_tool` 会抛 `ConnectionError`。当前代码把它变成错误信息返回给模型。如果要自动重连，该在哪一层做？

## 下一章

[s15 Integrated Harness](../s15_integrated_harness/) — 多种机制，一个循环。
