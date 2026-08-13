#!/usr/bin/env python3
"""
s08_context_compact - Context Compact：上下文总会满，要有办法腾地方

长会话中 messages[] 不断增长，迟早撞上模型的上下文窗口上限。
s08 的解法：四级递进的压缩管线，每轮调用前检查——

    估算 token 用量
        |
    超过 WARNING 阈值?  --> 级别1: snip     截断历史中过大的工具结果
        |                       (还超? 降级)
    超过 CRITICAL 阈值? --> 级别2: micro    只保留最近 N 轮，更早的粗摘要
        |                       (还超? 降级)
    仍然超限?           --> 级别3: summary  让 LLM 把早期历史浓缩成摘要
                                摘要替换原历史，循环继续

    设计原则:
    - 最近的对话永远保持原样（模型正在依赖它）
    - 越早的信息压缩得越狠
    - 摘要由 LLM 生成，保住"事实、决定、进度"，丢掉过程细节

运行:
    python s08_context_compact/code.py
    (把 TOKEN_BUDGET 调小可以快速触发各级压缩)
"""

import json
import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"你是一个位于 {os.getcwd()} 的编程 agent。上下文可能被压缩，重要结论请及时确认。"

# -- 预算配置（教学用，故意调小方便观察）--
TOKEN_BUDGET = 20000        # 软预算：超过就开始压缩
CRITICAL_BUDGET = 30000     # 硬预算：超过就上摘要
SNIP_THRESHOLD = 2000       # 单个工具结果超过这个字符数就截断
RECENT_KEEP = 4             # micro 压缩保留最近几条消息

TOOLS = [
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "读取文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
]


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


TOOL_HANDLERS = {"bash": run_bash, "read_file": read_file}


# ============================================================
# 第一部分：token 估算
# ============================================================

def message_text(msg: dict) -> str:
    """把一条消息拍平成文本，用于估算长度。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "tool_result":
                parts.append(str(block.get("content", "")))
            elif block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(json.dumps(block.get("input", {}), ensure_ascii=False))
        else:  # SDK 对象
            btype = getattr(block, "type", None)
            if btype == "tool_result":
                parts.append(str(getattr(block, "content", "")))
            elif btype == "text":
                parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                parts.append(json.dumps(getattr(block, "input", {}), ensure_ascii=False))
    return "\n".join(parts)


def estimate_tokens(messages: list) -> int:
    """粗略估算：总字符数 / 3（中英文混合场景的近似值）。"""
    total = sum(len(message_text(m)) for m in messages)
    return total // 3


# ============================================================
# 第二部分：级别1 - snip，截断历史中过大的工具结果
# ============================================================

def compact_snip(messages: list) -> bool:
    """原地截断较早消息里的超大 tool_result。返回是否有改动。"""
    changed = False
    # 最近 RECENT_KEEP 条不动
    for msg in messages[:-RECENT_KEEP]:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            # 只处理 dict 形式的 tool_result（历史已被规范化的）
            if isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content", "")
                if isinstance(c, str) and len(c) > SNIP_THRESHOLD:
                    block["content"] = (
                        c[:SNIP_THRESHOLD]
                        + f"\n...[harness: 旧结果已截断，原长 {len(c)} 字符，需要请重新获取]"
                    )
                    changed = True
    return changed


# ============================================================
# 第三部分：级别2 - micro，粗压缩早期消息
# ============================================================

def compact_micro(messages: list) -> bool:
    """把早期消息替换成占位文本，只保最近 RECENT_KEEP 条。"""
    if len(messages) <= RECENT_KEEP + 1:
        return False
    early = messages[:-RECENT_KEEP]
    summary_stub = {
        "role": "user",
        "content": f"[harness: 此前有 {len(early)} 条早期消息已被粗压缩，"
                   f"如需要其中信息请重新查询。]",
    }
    messages[:] = [summary_stub] + messages[-RECENT_KEEP:]
    return True


# ============================================================
# 第四部分：级别3 - summary，LLM 生成历史摘要
# ============================================================

def compact_summary(messages: list) -> bool:
    """让 LLM 把早期历史浓缩成一段摘要，替换原消息。"""
    if len(messages) <= RECENT_KEEP + 1:
        return False

    early = messages[:-RECENT_KEEP]
    transcript = []
    for msg in early:
        role = msg["role"]
        text = message_text(msg)[:4000]
        transcript.append(f"[{role}] {text}")

    prompt = (
        "下面是一段 agent 工作的早期历史。请把它压缩成一段简明摘要，"
        "必须保留：用户目标、已完成的工作、关键事实与文件路径、未尽事项。"
        "丢掉：中间过程、重复尝试、工具输出细节。只输出摘要本身。\n\n"
        + "\n".join(transcript)
    )

    resp = client.messages.create(
        model=MODEL,
        system="你是对话压缩器，输出高度凝练、信息密度高的中文摘要。",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
    )
    summary = resp.content[0].text

    messages[:] = [{
        "role": "user",
        "content": f"[harness: 以下是早期对话的摘要]\n{summary}",
    }] + messages[-RECENT_KEEP:]
    return True


# ============================================================
# 第五部分：压缩调度器 —— 每轮调用前跑一遍
# ============================================================

def maybe_compact(messages: list):
    tokens = estimate_tokens(messages)
    if tokens < TOKEN_BUDGET:
        return

    print(f"\033[35m[compact] 估算 {tokens} tokens，超过预算 {TOKEN_BUDGET}，开始压缩\033[0m")

    # 级别1：截断大结果
    if compact_snip(messages):
        tokens = estimate_tokens(messages)
        print(f"\033[35m[compact] 级别1 snip 完成 -> {tokens} tokens\033[0m")
        if tokens < TOKEN_BUDGET:
            return

    # 级别2：粗压缩（接近硬预算才用）
    if tokens >= CRITICAL_BUDGET:
        if compact_micro(messages):
            tokens = estimate_tokens(messages)
            print(f"\033[35m[compact] 级别2 micro 完成 -> {tokens} tokens\033[0m")
            if tokens < TOKEN_BUDGET:
                return

    # 级别3：LLM 摘要
    if compact_summary(messages):
        tokens = estimate_tokens(messages)
        print(f"\033[35m[compact] 级别3 summary 完成 -> {tokens} tokens\033[0m")


# ============================================================
# 第六部分：标准循环 + 每轮压缩检查
# ============================================================

def agent_loop(messages: list):
    while True:
        maybe_compact(messages)   # <-- 唯一新增：调用前先体检
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


def normalize_history(messages: list):
    """把 SDK 对象形态的历史规范化成 dict，便于压缩函数处理。"""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list) and content and not isinstance(content[0], dict):
            new_content = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    new_content.append({"type": "text", "text": block.text})
                elif btype == "tool_use":
                    new_content.append({"type": "tool_use", "id": block.id,
                                        "name": block.name, "input": block.input})
                elif btype == "tool_result":
                    new_content.append({"type": "tool_result",
                                        "tool_use_id": block.tool_use_id,
                                        "content": block.content})
                else:
                    new_content.append({"type": "text", "text": str(block)})
            msg["content"] = new_content


if __name__ == "__main__":
    print("s08: Context Compact — 预算已调小便于观察。多轮对话后看压缩触发。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        normalize_history(history)
        for block in history[-1]["content"]:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                print(text)
        print(f"\033[2m[当前历史 {len(history)} 条消息，估算 {estimate_tokens(history)} tokens]\033[0m\n")
