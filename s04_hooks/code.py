#!/usr/bin/env python3
"""
s04_hooks - Hooks：挂在循环上，不写进循环里

s03 把权限逻辑写进了执行函数。但如果还想要：日志审计、耗时统计、
自动格式化、敏感词过滤……都往循环里塞吗？

s04 的解法：在工具执行的"前"和"后"留两个标准插口（hook 点），
任何横切关注点都注册成 hook，主循环保持干净——

    tool_use
       |
       v
    PreToolUse hooks  ---> 任何一个 hook 可以改写参数或直接拦截
       |
       v
    执行工具
       |
       v
    PostToolUse hooks ---> 可以改写输出、记录日志、统计耗时
       |
       v
    tool_result

hook 签名约定:
    PreToolUse(tool_name, tool_input) -> ("continue"|"block", new_input_or_msg)
    PostToolUse(tool_name, tool_input, output) -> new_output

运行:
    python s04_hooks/code.py
"""

import json
import os
import subprocess
import time

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"你是一个位于 {os.getcwd()} 的编程 agent，用 bash 和文件工具完成任务。"

# ============================================================
# 第一部分：Hook 注册表
# ============================================================

PRE_HOOKS = []    # 执行前
POST_HOOKS = []   # 执行后


def pre_hook(fn):
    """装饰器：注册一个 PreToolUse hook。"""
    PRE_HOOKS.append(fn)
    return fn


def post_hook(fn):
    """装饰器：注册一个 PostToolUse hook。"""
    POST_HOOKS.append(fn)
    return fn


def run_pre_hooks(tool_name: str, tool_input: dict):
    """依次跑所有前置 hook。任何一个返回 block 即拦截。
    返回 (是否继续执行, tool_input 或 拦截消息)。"""
    for hook in PRE_HOOKS:
        action, payload = hook(tool_name, tool_input)
        if action == "block":
            return False, payload
        if action == "continue" and isinstance(payload, dict):
            tool_input = payload   # hook 可以改写参数
    return True, tool_input


def run_post_hooks(tool_name: str, tool_input: dict, output: str) -> str:
    """依次跑所有后置 hook，每个都能改写输出。"""
    for hook in POST_HOOKS:
        output = hook(tool_name, tool_input, output)
    return output


# ============================================================
# 第二部分：示例 hooks —— 展示插口的各种用法
# ============================================================

@pre_hook
def hook_deny_dangerous(tool_name, tool_input):
    """用法 1：拦截。比 s03 的硬编码规则更灵活，可随时增删。"""
    arg_str = " ".join(str(v) for v in tool_input.values())
    for bad in ("rm -rf /", "sudo", "shutdown"):
        if bad in arg_str:
            return "block", f"hook 拦截: 检测到危险操作 '{bad}'"
    return "continue", tool_input


@pre_hook
def hook_log_request(tool_name, tool_input):
    """用法 2：审计日志。每次调用落盘，方便事后追溯 agent 干了什么。"""
    with open("hook_audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "tool": tool_name,
                            "input": tool_input}, ensure_ascii=False) + "\n")
    return "continue", tool_input


@pre_hook
def hook_limit_output_tools(tool_name, tool_input):
    """用法 3：改写参数。给 bash 自动追加输出限制。"""
    if tool_name == "bash" and "|" not in tool_input.get("command", ""):
        tool_input = dict(tool_input)
        tool_input["command"] += " 2>&1 | head -c 50000"
    return "continue", tool_input


@post_hook
def hook_timing(tool_name, tool_input, output):
    """用法 4：后置处理依赖执行时记录的耗时（见 execute 函数）。"""
    return output


@post_hook
def hook_flag_errors(tool_name, tool_input, output):
    """用法 5：给输出打标记，帮助模型更快识别失败。"""
    if output.startswith("错误") or "Traceback" in output:
        return f"[工具执行失败]\n{output}"
    return output


# ============================================================
# 第三部分：工具实现（与前面章节一致）
# ============================================================

def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out if out else "(无输出)"
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
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}"
    except OSError as e:
        return f"错误: {e}"


TOOLS = [
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

TOOL_HANDLERS = {"bash": run_bash, "read_file": read_file, "write_file": write_file}


# ============================================================
# 第四部分：执行器 —— 前置 hook -> 工具 -> 后置 hook
# ============================================================

def execute_with_hooks(tool_name: str, tool_input: dict) -> str:
    # 前置 hooks：可拦截、可改写参数
    proceed, payload = run_pre_hooks(tool_name, tool_input)
    if not proceed:
        print(f"\033[31m✗ {payload}\033[0m")
        return str(payload)
    tool_input = payload

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return f"错误: 不存在的工具 {tool_name}"

    t0 = time.time()
    try:
        output = handler(**tool_input)
    except (TypeError, KeyError, OSError) as e:
        output = f"错误: 工具执行失败 - {e}"
    elapsed = time.time() - t0
    print(f"\033[2m⏱ {tool_name} 耗时 {elapsed:.2f}s\033[0m")

    # 后置 hooks：可改写输出
    return run_post_hooks(tool_name, tool_input, output)


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
            output = execute_with_hooks(block.name, block.input)
            print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s04: Hooks — 观察 hook_audit.jsonl 的审计记录。输入任务，q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms04 >> \033[0m")
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
