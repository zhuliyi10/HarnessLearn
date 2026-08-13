#!/usr/bin/env python3
"""
s01_agent_loop - Agent Loop：一切 Agent 的内核

一个 AI Agent 的全部秘密，就是下面这个循环：

    +----------+      +-------+      +---------+
    |   用户    | ---> |  LLM  | ---> |  执行    |
    |   提问    |      |  决策  |      |  工具    |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |  tool_result  |
                          +---------------+
                           (循环继续)

    while stop_reason == "tool_use":
        response = LLM(messages, tools)
        执行工具，把结果塞回 messages

模型决定何时调用工具、何时停止。代码只是执行模型的要求。
本章之后的所有章节，都是在这个循环外面添加 harness 机制。

运行:
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY=... python s01_agent_loop/code.py
"""

import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"你是一个位于 {os.getcwd()} 的编程 agent。用 bash 工具解决任务。直接行动，不要只解释。"

# -- 工具定义：只有一个 bash --
TOOLS = [{
    "name": "bash",
    "description": "执行一条 shell 命令，返回 stdout + stderr。",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的命令"}},
        "required": ["command"],
    },
}]


# -- 工具实现 --
def run_bash(command: str) -> str:
    """执行命令。注意两个 harness 级别的保护：危险词拦截 + 超时。"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "错误: 危险命令已被 harness 拦截"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 命令超时 (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"错误: {e}"


# -- 核心模式：一个 while 循环，不断调用工具直到模型停止 --
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 把 assistant 的完整回复（文本 + tool_use 块）追加进历史
        messages.append({"role": "assistant", "content": response.content})

        # 模型没有调用工具 → 它认为任务完成，循环结束
        if response.stop_reason != "tool_use":
            return

        # 执行本轮的每个工具调用，收集结果
        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\033[33m$ {block.input['command']}\033[0m")
                output = run_bash(block.input["command"])
                print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,   # 必须与 tool_use 的 id 对应
                    "content": output,
                })

        # 工具结果以 user 角色塞回去，循环继续
        messages.append({"role": "user", "content": results})


# -- 入口：简单的 REPL --
if __name__ == "__main__":
    print("s01: Agent Loop — 输入任务，q 退出\n")

    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # 打印模型的最终文本回答
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
