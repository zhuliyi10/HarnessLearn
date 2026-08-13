#!/usr/bin/env python3
"""
s02_tool_use - Tool Use：从单工具到工具池

s01 的问题：工具调用写死在循环里。
s02 的解法：一个 dispatch map（分发字典）——

    TOOL_HANDLERS = {"bash": run_bash, "read_file": ..., "write_file": ...}

    循环永远不变:
        output = TOOL_HANDLERS[block.name](**block.input)

    加一个新工具 = 写一个 handler 函数 + 注册一行。

    +------------------+     tool_use(name, input)     +------------------+
    |       LLM        | ----------------------------> |   TOOL_HANDLERS  |
    | (从 TOOLS 里挑)  |                               |   dispatch map   |
    +------------------+ <---------------------------- +------------------+
                               tool_result

运行:
    python s02_tool_use/code.py
"""

import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
可用工具: bash(执行命令), read_file(读文件), write_file(写文件), list_files(列目录)。
读文件优先用 read_file 而不是 cat。直接行动，不要只解释。"""

# ============================================================
# 第一部分：工具实现（每个工具一个独立函数）
# ============================================================

def run_bash(command: str) -> str:
    """执行 shell 命令。"""
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


def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """读文件，可选按行号范围。行号从 1 开始。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"错误: {e}"
    if start_line > 0:
        lines = lines[max(start_line - 1, 0):(end_line or len(lines))]
    if len(lines) > 2000:
        lines = lines[:2000] + ["... (已截断，请缩小范围)\n"]
    # 带行号输出，方便模型后续精确引用
    return "".join(f"{i:5d}| {line}" for i, line in enumerate(
        lines, start=(start_line if start_line > 0 else 1))) or "(空文件)"


def write_file(path: str, content: str) -> str:
    """写入文件（覆盖）。父目录不存在时自动创建。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path} ({len(content.splitlines())} 行)"
    except OSError as e:
        return f"错误: {e}"


def list_files(path: str = ".", pattern: str = "") -> str:
    """列目录，可选 glob 过滤（如 *.py）。"""
    import fnmatch
    try:
        names = sorted(os.listdir(path or "."))
    except OSError as e:
        return f"错误: {e}"
    if pattern:
        names = [n for n in names if fnmatch.fnmatch(n, pattern)]
    return "\n".join(names[:500]) or "(空目录)"


# ============================================================
# 第二部分：工具定义（给模型看的 schema）
# ============================================================

TOOLS = [
    {
        "name": "bash",
        "description": "执行一条 shell 命令。用于安装依赖、跑测试、git 操作等。",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取文件内容，返回带行号的文本。可指定 start_line/end_line 只读一段。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "description": "起始行(1开始)，省略则从头"},
                "end_line": {"type": "integer", "description": "结束行(含)，省略则到尾"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "创建或覆盖写入文件。写之前如果文件已存在，先 read_file 看清内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "列出目录内容，可用 glob 模式过滤。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "pattern": {"type": "string", "description": "如 *.py"},
            },
        },
    },
]

# ============================================================
# 第三部分：dispatch map —— 本章的核心
# ============================================================

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
}
# 加新工具只需：1) 写函数 2) 加 TOOLS schema 3) 在这里注册一行。循环零改动。


# ============================================================
# 第四部分：循环 —— 与 s01 几乎相同，只是分发变成了字典查表
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
            handler = TOOL_HANDLERS.get(block.name)
            if handler is None:
                # 未知工具：返回错误信息而不是崩溃，让模型有机会自我纠正
                output = f"错误: 不存在的工具 {block.name}"
            else:
                try:
                    # **block.input 直接把模型给的参数展开传给 handler
                    output = handler(**block.input)
                except (TypeError, KeyError, OSError) as e:
                    output = f"错误: 工具执行失败 - {e}"
            print(f"\033[33m[{block.name}] {str(block.input)[:120]}\033[0m")
            print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s02: Tool Use — 4 个工具，一个 dispatch map。输入任务，q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
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
