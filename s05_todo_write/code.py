#!/usr/bin/env python3
"""
s05_todo_write - TodoWrite：先列步骤再动手，完成率翻倍

复杂任务中，模型容易"干着干着忘了自己要干嘛"。
s05 的解法：给模型一个 todo_write 工具，让它：
    1. 动手前先列出全部步骤（pending）
    2. 做每一步前把它标为 in_progress（同时只能有一个）
    3. 做完标为 completed

todo 列表就是模型的"外部工作记忆"——每次更新都会把最新列表
回传给模型，帮它对齐目标，也让人类随时看清进度。

    用户任务
       |
       v
    todo_write([全部步骤, pending])          <- 先规划
       |
       v
    todo_write(step1=in_progress) --> 执行 step1 的实际工具调用
       |
       v
    todo_write(step1=completed, step2=in_progress) --> 执行 step2 ...
       |
       v
    全部 completed --> 汇总回复用户

运行:
    python s05_todo_write/code.py
"""

import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
工作纪律：
1. 收到多步骤任务时，先用 todo_write 列出完整计划（每项 status=pending）。
2. 开始做某一步时，调用 todo_write 把它改为 in_progress（同时只能有一个 in_progress）。
3. 做完一步立刻改为 completed，再开始下一步。
4. 全部完成后向用户汇总。
简单的一步任务不需要 todo。"""

# ============================================================
# 第一部分：Todo 状态存储
# ============================================================

TODOS = []   # [{"content": str, "status": "pending|in_progress|completed"}]

VALID_STATUS = ("pending", "in_progress", "completed")
STATUS_ICON = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


def render_todos() -> str:
    """把 todo 列表渲染成人类和模型都能读的文本。"""
    if not TODOS:
        return "(当前没有 todo)"
    lines = []
    for i, item in enumerate(TODOS, 1):
        lines.append(f"{i}. {STATUS_ICON[item['status']]} {item['content']}")
    done = sum(1 for t in TODOS if t["status"] == "completed")
    lines.append(f"进度: {done}/{len(TODOS)}")
    return "\n".join(lines)


def todo_write(todos: list) -> str:
    """整表替换。每次调用传入完整列表。"""
    global TODOS
    new_list = []
    for item in todos:
        content = item.get("content", "").strip()
        status = item.get("status", "pending")
        if not content:
            return "错误: 每个 todo 必须有 content"
        if status not in VALID_STATUS:
            return f"错误: status 必须是 {VALID_STATUS} 之一，收到 {status}"
        new_list.append({"content": content, "status": status})

    # 约束：in_progress 最多一个
    in_progress = [t for t in new_list if t["status"] == "in_progress"]
    if len(in_progress) > 1:
        return "错误: 同时只能有一个 in_progress 的 todo，请一次只做一件事"

    TODOS = new_list
    print(f"\033[32m--- todo 更新 ---\n{render_todos()}\033[0m")
    return "todo 已更新:\n" + render_todos()


# ============================================================
# 第二部分：其他基础工具
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


def write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}"
    except OSError as e:
        return f"错误: {e}"


TOOLS = [
    {
        "name": "todo_write",
        "description": ("写入/更新任务清单。参数 todos 是完整列表，"
                        "每项 {content: 描述, status: pending|in_progress|completed}。"
                        "多步骤任务必须先列计划，再逐项推进。"),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string",
                                       "enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "读取文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "写入文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
]

TOOL_HANDLERS = {
    "todo_write": todo_write,
    "bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
}


# ============================================================
# 第三部分：标准循环
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
                output = f"错误: 工具执行失败 - {e}"
            if block.name != "todo_write":
                print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s05: TodoWrite — 给一个多步骤任务，看模型先列计划。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print(f"\033[2m最终 todo 状态:\n{render_todos()}\033[0m\n")
