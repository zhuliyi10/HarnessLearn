#!/usr/bin/env python3
"""
s03_permission - Permission：先划边界，再给自由

s02 的问题：模型想执行什么都行，harness 没有把关人。
s03 的解法：在工具执行之前插一层 PermissionGate（权限闸门）——

    tool_use 到达
        |
        v
    +----------------+     allow      +----------------+
    | PermissionGate | -------------> |  执行工具       |
    |  规则匹配       |                +----------------+
    +-------+--------+
            | deny              | ask
            v                   v
    返回"已拒绝"          询问用户 y/n
    让模型改道           （本次会话可记住）

规则三态: allow / ask / deny
规则按顺序匹配，第一条命中的生效；都没命中走默认策略。

运行:
    python s03_permission/code.py
"""

import os
import re
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
有些操作会被权限系统拦截或要求用户确认，被拒绝时换个安全的方式继续。"""

# ============================================================
# 第一部分：权限规则
# ============================================================

# 一条规则 = (工具名, 参数匹配正则, 决定)
# 按顺序匹配，第一条命中即生效
PERMISSION_RULES = [
    # --- deny：永远禁止，不给商量余地 ---
    ("bash", r"rm\s+-rf\s+/",            "deny"),
    ("bash", r"(sudo|shutdown|reboot)",  "deny"),
    ("bash", r"git\s+push\s+.*--force",  "deny"),
    ("bash", r">\s*/dev/",               "deny"),

    # --- ask：破坏性/不可逆操作，需要用户点头 ---
    ("bash", r"\brm\b",                  "ask"),
    ("bash", r"git\s+(push|reset|rebase)", "ask"),
    ("bash", r"pip\s+(uninstall|install)", "ask"),
    ("write_file", r"\.env",             "ask"),   # 碰密钥文件要确认

    # --- allow：只读操作直接放行 ---
    ("bash", r"^(ls|cat|pwd|echo|head|tail|grep|wc|find|git status|git diff|git log)", "allow"),
    ("read_file",  r".*",                "allow"),
    ("list_files", r".*",                "allow"),
]

DEFAULT_POLICY = "ask"   # 没有规则命中时的默认策略


class PermissionGate:
    """权限闸门：匹配规则 + 会话内记忆用户的批准决定。"""

    def __init__(self, rules, default="ask"):
        self.rules = rules
        self.default = default
        # 用户说过"允许"的模式，本次会话不再重复询问
        self.session_grants = set()

    def check(self, tool_name: str, tool_input: dict) -> str:
        """返回 'allow' | 'ask' | 'deny'"""
        arg_str = " ".join(str(v) for v in tool_input.values())

        # 会话内已批准过相同 (工具, 参数) 的直接放行
        if (tool_name, arg_str) in self.session_grants:
            return "allow"

        for tool, pattern, decision in self.rules:
            if tool == tool_name and re.search(pattern, arg_str):
                return decision
        return self.default

    def grant(self, tool_name: str, tool_input: dict, remember: bool):
        if remember:
            arg_str = " ".join(str(v) for v in tool_input.values())
            self.session_grants.add((tool_name, arg_str))


GATE = PermissionGate(PERMISSION_RULES, DEFAULT_POLICY)


def ask_user(tool_name: str, tool_input: dict) -> bool:
    """在终端询问用户。支持 y / n / a(本次会话一直允许此调用)。"""
    arg_str = str(tool_input)[:300]
    print(f"\033[35m⚠ 需要确认: {tool_name} {arg_str}\033[0m")
    try:
        answer = input("  允许执行? [y]允许 / [n]拒绝 / [a]本次会话总是允许: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer == "a":
        GATE.grant(tool_name, tool_input, remember=True)
        return True
    return answer in ("y", "yes")


# ============================================================
# 第二部分：工具实现（与 s02 相同）
# ============================================================

def run_bash(command: str) -> str:
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
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}"
    except OSError as e:
        return f"错误: {e}"


def list_files(path: str = ".") -> str:
    try:
        return "\n".join(sorted(os.listdir(path or "."))) or "(空目录)"
    except OSError as e:
        return f"错误: {e}"


TOOLS = [
    {"name": "bash", "description": "执行 shell 命令。危险操作会被权限系统拦截或要求确认。",
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
    {"name": "list_files", "description": "列出目录。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string", "default": "."}}}},
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
}


# ============================================================
# 第三部分：循环 —— 在执行前过一遍权限闸门
# ============================================================

def execute_with_permission(tool_name: str, tool_input: dict) -> str:
    """本章核心：先问闸门，再执行。"""
    decision = GATE.check(tool_name, tool_input)

    if decision == "deny":
        return "权限拒绝: 该操作被安全策略禁止，请换用其他方式。"

    if decision == "ask" and not ask_user(tool_name, tool_input):
        return "权限拒绝: 用户不允许此操作，请换用其他方式。"

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return f"错误: 不存在的工具 {tool_name}"
    try:
        return handler(**tool_input)
    except (TypeError, KeyError, OSError) as e:
        return f"错误: 工具执行失败 - {e}"


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
            output = execute_with_permission(block.name, block.input)
            print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s03: Permission — 试试 '删除 temp 目录' 或 'rm 一个文件' 观察审批流程")
    print("输入任务，q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
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
