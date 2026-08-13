#!/usr/bin/env python3
"""
s14_mcp_plugin - MCP Plugin：能力不够？插上 MCP

Harness 内置的工具终究有限。MCP（Model Context Protocol）让 agent
能以标准协议接入任意外部工具服务器——不用改循环、不用改分发逻辑。

    harness（客户端）                        MCP 服务器（外部进程）
        |                                        |
        |--- initialize --------------------->   |
        |<--- serverInfo ----------------------  |
        |--- tools/list ---------------------->  |
        |<--- [{name, description, schema}] ---  |   工具发现
        |                                        |
        |  加命名空间前缀后并入 TOOLS 工具池       |
        |                                        |
        |  模型调用 mcp__weather__get_weather     |
        |--- tools/call {name, arguments} ---->  |   工具执行
        |<--- content -------------------------  |

本章要点：
    1. 工具发现: tools/list 拿到的 schema 与本地工具格式完全一致
    2. 命名空间: mcp__<服务器>__<工具>，防止不同插件工具重名
    3. 统一分发: MCP 工具在 TOOL_HANDLERS 里就是一个转发函数，循环无感

运行:
    python s14_mcp_plugin/code.py
    (问 agent "北京天气怎么样"，看它调用 MCP 工具)
"""

import json
import os
import subprocess
import threading

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
除了本地工具，你还可以通过 mcp__ 前缀的工具使用外部服务。"""

# ============================================================
# 第一部分：MCP 客户端 —— 与外部工具服务器对话
# ============================================================

class McpClient:
    """最小 MCP 客户端：stdio + JSON-RPC（每行一条消息）。"""

    def __init__(self, server_name: str, command: list):
        self.server_name = server_name
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        self._lock = threading.Lock()
        self._req_id = 0

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        """发送一次 JSON-RPC 请求并等待响应。"""
        with self._lock:
            self._req_id += 1
            req = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
            if params is not None:
                req["params"] = params
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                raise ConnectionError(f"MCP 服务器 {self.server_name} 已断开")
            resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message", "MCP 错误"))
        return resp.get("result", {})

    def initialize(self) -> dict:
        return self._rpc("initialize", {})

    def list_tools(self) -> list:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        # MCP 结果格式: {"content": [{"type": "text", "text": ...}]}
        parts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)

    def close(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


# ============================================================
# 第二部分：插件注册表 —— 配置即接线
# ============================================================

# 每个插件 = 名字 + 启动命令。加新插件只需加一行配置。
MCP_SERVERS = {
    "weather": [os.path.join(HERE, "demo_mcp_server.py")],
    # 示例：接入其他 MCP 服务器
    # "filesystem": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
}

CLIENTS: dict[str, McpClient] = {}
TOOLS = []
TOOL_HANDLERS = {}


def register_mcp_plugins():
    """启动所有插件服务器，发现工具，并入统一工具池。"""
    for server_name, command in MCP_SERVERS.items():
        cmd = ["python3"] + command if command[0].endswith(".py") else command
        try:
            mcp = McpClient(server_name, cmd)
            info = mcp.initialize()
            tools = mcp.list_tools()
        except (OSError, ConnectionError, RuntimeError, json.JSONDecodeError) as e:
            print(f"\033[31m[mcp] 插件 {server_name} 启动失败: {e}\033[0m")
            continue

        CLIENTS[server_name] = mcp
        server_label = info.get("serverInfo", {}).get("name", server_name)
        print(f"\033[34m[mcp] 已连接 {server_label}，发现 {len(tools)} 个工具\033[0m")

        for tool in tools:
            # 命名空间: mcp__<服务器>__<工具>
            full_name = f"mcp__{server_name}__{tool['name']}"
            TOOLS.append({
                "name": full_name,
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema",
                                         {"type": "object", "properties": {}}),
            })

            # 分发函数：闭包捕获 server 与原工具名
            def make_handler(srv=mcp, orig_name=tool["name"]):
                def handler(**kwargs):
                    try:
                        return srv.call_tool(orig_name, kwargs)
                    except (ConnectionError, RuntimeError, OSError) as e:
                        return f"错误: MCP 调用失败 - {e}"
                return handler
            TOOL_HANDLERS[full_name] = make_handler()


# ============================================================
# 第三部分：本地工具（与前面章节一致）
# ============================================================

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "错误: 危险命令已被拦截"
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 超时 (120s)"
    except OSError as e:
        return f"错误: {e}"


def read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[:50000] or "(空文件)"
    except OSError as e:
        return f"错误: {e}"


TOOLS.extend([
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "读取文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
])
TOOL_HANDLERS.update({"bash": run_bash, "read_file": read_file})


# ============================================================
# 第四部分：标准循环 —— 对 MCP 工具完全无感
# ============================================================

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[33m[{block.name}] {str(block.input)[:120]}\033[0m")
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 不存在的工具 {block.name}"
            except (TypeError, KeyError) as e:
                output = f"错误: 工具参数错误 - {e}"
            print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s14: MCP Plugin — 正在连接插件服务器...")
    register_mcp_plugins()
    print(f"工具池共 {len(TOOLS)} 个工具: "
          f"{', '.join(t['name'] for t in TOOLS)}\n")
    print("试试问天气，观察 mcp__ 工具被调用。q 退出\n")

    history = []
    try:
        while True:
            try:
                query = input("\033[36ms14 >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break
            history.append({"role": "user", "content": query})
            agent_loop(history)
            for block in history[-1]["content"]:
                if getattr(block, "type", None) == "text":
                    print(block.text)
            print()
    finally:
        for mcp in CLIENTS.values():
            mcp.close()
