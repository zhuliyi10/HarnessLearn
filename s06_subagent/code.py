#!/usr/bin/env python3
"""
s06_subagent - Subagent：子任务给全新的 messages[]

问题：探索性、搜索类、长输出的子任务会产生大量中间过程，
全部堆在主对话的 messages[] 里，污染主上下文。

解法：spawn 一个 subagent ——
    - 它有一份全新的 messages[]（只含它自己的任务描述）
    - 它跑自己的 agent loop，可以调用工具、绕很多圈
    - 最后只把"最终文本回答"作为一条 tool_result 返回给主 agent

    主 agent                         subagent
    messages[]                      fresh messages[]
        |                                |
        |  task("调研X")                  |
        +------------------------------->|
        |                                | 自己跑 loop（多轮工具调用）
        |                                | 大量中间过程留在这边
        |   只有最终文本回来               |
        |<-------------------------------+
        v
    主上下文只多了一条精炼的结论

这就是"上下文隔离"：过程留在子对话里，结论回到主对话。

运行:
    python s06_subagent/code.py
"""

import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程主 agent。
你有一个 subagent 工具：适合把探索性、搜索类、会产生大量输出的子任务委派给它。
subagent 独立工作，只返回最终结论，不会污染你的上下文。
简单的单步操作自己做，不要什么都委派。"""

SUBAGENT_SYSTEM = f"""你是一个位于 {os.getcwd()} 的调研型 subagent。
你被主 agent 委派了一个具体任务。专注完成它，用工具探索，
最后给出简洁、准确、可直接使用的结论。不要反问。"""

# ============================================================
# 第一部分：基础工具（主 agent 和 subagent 共用）
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


def list_files(path: str = ".") -> str:
    try:
        return "\n".join(sorted(os.listdir(path or "."))) or "(空目录)"
    except OSError as e:
        return f"错误: {e}"


BASE_TOOLS = [
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "读取文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "list_files", "description": "列出目录。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string", "default": "."}}}},
]

BASE_HANDLERS = {"bash": run_bash, "read_file": read_file, "list_files": list_files}


# ============================================================
# 第二部分：subagent 的核心 —— 一个独立的 loop 函数
# ============================================================

def run_subagent(task: str, max_turns: int = 20) -> str:
    """spawn 一个 subagent：全新 messages，自己的循环，只返回最终文本。"""
    print(f"\033[35m>> 派出 subagent: {task[:100]}\033[0m")

    # 关键 1：全新的 messages[]，与主对话完全隔离
    sub_messages = [{"role": "user", "content": task}]

    for turn in range(max_turns):
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM,
            messages=sub_messages, tools=BASE_TOOLS, max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})

        # 关键 2：subagent 停止时，把它的最终文本作为返回值
        if response.stop_reason != "tool_use":
            final = "".join(b.text for b in response.content
                            if getattr(b, "type", None) == "text")
            print(f"\033[35m<< subagent 完成（{turn + 1} 轮）\033[0m")
            return final or "(subagent 未返回结论)"

        # subagent 自己的工具执行（过程只留在 sub_messages 里）
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[2m   sub: [{block.name}] {str(block.input)[:80]}\033[0m")
            handler = BASE_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 不存在的工具 {block.name}"
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        sub_messages.append({"role": "user", "content": results})

    return f"(subagent 超过 {max_turns} 轮仍未完成，请缩小任务范围)"


# ============================================================
# 第三部分：主 agent 的工具池 = 基础工具 + subagent 工具
# ============================================================

def task(description: str) -> str:
    """暴露给主 agent 的 subagent 工具。"""
    return run_subagent(description)


TOOLS = BASE_TOOLS + [{
    "name": "task",
    "description": ("委派一个子任务给独立的 subagent。适合探索、调研、"
                    "大范围搜索等会产生大量中间输出的工作。"
                    "description 要写清楚目标，subagent 看不到你的对话历史。"),
    "input_schema": {
        "type": "object",
        "properties": {"description": {"type": "string",
                                       "description": "给 subagent 的完整任务描述"}},
        "required": ["description"],
    },
}]

TOOL_HANDLERS = dict(BASE_HANDLERS)
TOOL_HANDLERS["task"] = task


# ============================================================
# 第四部分：主 agent 的标准循环
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
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            print(str(output)[:300])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s06: Subagent — 给一个需要调研的任务，观察委派过程。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
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
