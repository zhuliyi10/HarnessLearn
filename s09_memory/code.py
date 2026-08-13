#!/usr/bin/env python3
"""
s09_memory - Memory：记住该记的，忘掉该忘的

s08 解决"会话内"的上下文容量；s09 解决"跨会话"的知识沉淀。
每次对话结束，用户的偏好、项目的约定、踩过的坑都会随着进程退出而消失。

解法：三个子系统——

    1. 筛选 selection     会话结束时，先判断这次对话有没有值得记的东西
    2. 提取 extraction    值得记的话，让 LLM 从对话里提取结构化记忆条目
    3. 整理 consolidation 新记忆入库前，与旧记忆去重、合并、淘汰过时项

    会话结束
        |
    selection: 有值得记的吗? --否--> 结束
        | 是
    extraction: 提取 [{content, keywords}]
        |
    consolidation: 与已有记忆合并(去重/更新/淘汰)
        |
    memory/memories.json

下一次会话启动时，把记忆库注入 SYSTEM，agent 就"记得"了。

运行:
    python s09_memory/code.py
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

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "memories.json")

# ============================================================
# 第一部分：记忆存储
# ============================================================

class MemoryStore:
    """最简单的文件记忆库：一个 JSON 数组，每项 {content, keywords, updated_at}。"""

    def __init__(self, path: str):
        self.path = path
        self.items = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.items = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.items = []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    def render(self) -> str:
        """注入 SYSTEM 用的文本。"""
        if not self.items:
            return ""
        lines = [f"- {item['content']}" for item in self.items]
        return "你记得以下关于用户和项目的事实（来自过往会话）:\n" + "\n".join(lines)


STORE = MemoryStore(MEMORY_FILE)

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
{STORE.render()}"""


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


TOOLS = [
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
]

TOOL_HANDLERS = {"bash": run_bash}


# ============================================================
# 第二部分：标准循环
# ============================================================

def flatten(content) -> str:
    """把消息内容拍平成文本，用于喂给记忆提取器。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif getattr(block, "type", None) == "tool_use":
            parts.append(f"(调用工具 {block.name}: {json.dumps(block.input, ensure_ascii=False)[:200]})")
        elif isinstance(block, dict):
            parts.append(str(block.get("content", block.get("text", "")))[:300])
    return "\n".join(parts)


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
            print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


# ============================================================
# 第三部分：记忆三子系统
# ============================================================

def transcript_of(messages: list, max_chars: int = 30000) -> str:
    lines = []
    for msg in messages:
        lines.append(f"[{msg['role']}] {flatten(msg['content'])}")
    return "\n".join(lines)[-max_chars:]


def select_and_extract(messages: list) -> list:
    """子系统 1+2：筛选 + 提取合并成一次调用（省 token）。
    让 LLM 判断对话里有没有长期价值的内容，有则提取为记忆条目。"""
    prompt = f"""审查下面这段对话，提取值得跨会话长期记住的内容。

值得记的: 用户明确表达的偏好/约定、项目的重要事实、踩坑教训。
不值得记的: 一次性任务细节、临时输出、闲聊。

如果没有值得记的，只输出: NONE
如果有，输出 JSON 数组，每项 {{"content": "一句话陈述事实", "keywords": "英文逗号分隔关键词"}}，
最多 5 条。content 必须脱离对话上下文也能看懂。只输出 NONE 或 JSON。

对话:
{transcript_of(messages)}"""

    resp = client.messages.create(
        model=MODEL,
        system="你是记忆提取器，只输出 NONE 或 JSON 数组，不输出其他内容。",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    text = resp.content[0].text.strip()
    if "NONE" in text and "[" not in text:
        return []
    try:
        start, end = text.index("["), text.rindex("]") + 1
        items = json.loads(text[start:end])
        return [it for it in items
                if isinstance(it, dict) and it.get("content")]
    except (ValueError, json.JSONDecodeError):
        return []


def consolidate(new_items: list):
    """子系统 3：整理。让 LLM 把新记忆与旧记忆合并，去重、更新、淘汰。"""
    existing = [{"content": it["content"], "keywords": it.get("keywords", "")}
                for it in STORE.items]
    prompt = f"""这是已有的记忆库:
{json.dumps(existing, ensure_ascii=False, indent=1)}

这是本次新提取的记忆:
{json.dumps(new_items, ensure_ascii=False, indent=1)}

请整理出最终记忆库，规则:
1. 新记忆与旧记忆重复 -> 只保留一条，可合并补充细节
2. 新记忆与旧记忆矛盾 -> 保留新的（更新的事实），删除旧的
3. 旧记忆明显过时或被新信息推翻 -> 删除
4. 总数控制在 20 条以内，优先保留高频有用的
只输出 JSON 数组，每项 {{"content": ..., "keywords": ...}}。"""

    resp = client.messages.create(
        model=MODEL,
        system="你是记忆整理器，只输出 JSON 数组。",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    text = resp.content[0].text.strip()
    try:
        start, end = text.index("["), text.rindex("]") + 1
        merged = json.loads(text[start:end])
        STORE.items = [{
            "content": it["content"],
            "keywords": it.get("keywords", ""),
            "updated_at": int(time.time()),
        } for it in merged if isinstance(it, dict) and it.get("content")]
        STORE.save()
        print(f"\033[34m[memory] 整理完成，记忆库现有 {len(STORE.items)} 条\033[0m")
    except (ValueError, json.JSONDecodeError):
        # 整理失败时降级：直接追加新记忆
        for it in new_items:
            STORE.items.append({**it, "updated_at": int(time.time())})
        STORE.save()
        print(f"\033[34m[memory] 整理器输出异常，直接追加 {len(new_items)} 条\033[0m")


def remember_session(messages: list):
    """会话结束时调用：筛选 -> 提取 -> 整理。"""
    if len(messages) < 2:
        return
    print("\033[34m[memory] 会话结束，正在回顾本次对话...\033[0m")
    new_items = select_and_extract(messages)
    if not new_items:
        print("\033[34m[memory] 本次对话没有值得长期记住的内容\033[0m")
        return
    print(f"\033[34m[memory] 提取到 {len(new_items)} 条候选记忆\033[0m")
    consolidate(new_items)


if __name__ == "__main__":
    print(f"s09: Memory — 记忆库: {MEMORY_FILE}")
    print(f"已有记忆 {len(STORE.items)} 条。试试告诉 agent 你的偏好。q 退出并触发记忆保存\n")
    history = []
    try:
        while True:
            try:
                query = input("\033[36ms09 >> \033[0m")
            except EOFError:
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
        remember_session(history)
