#!/usr/bin/env python3
"""
s17_goal_loop - Goal Loop：目标决定循环什么时候真正结束

s01 的循环在"模型不再调用工具"时结束。但"不再调用工具"不等于
"目标达成"——模型可能中途放弃、自我感觉良好地宣布完成、或者遇到
错误就摊手。

s17 在每次"准备停止"时加一道目标闸门：

    执行 agent 跑一轮 (想收工了)
        |
        v
    独立判断器(另一个 LLM 调用)审查: 目标真的达成了吗?
        |
    +---+----------------+------------------+
    | done               | continue          | give_up
    v                    v                   v
    真正结束,            把审查意见作为        目标不可能/反复失败/
    输出最终答案         user 消息注入,        超过续轮上限
                        自动续跑下一轮        -> 控制权交还用户

    执行者只负责干活; 是否完成由"独立的眼睛"判断。
    续轮有上限(MAX_CONTINUATIONS), 永不无限循环。

运行:
    python s17_goal_loop/code.py
    (给一个需要验证才能算完成的目标, 观察审查与续轮)
"""

import json
import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

MAX_CONTINUATIONS = 3   # 最多自动续跑次数，防失控

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
你的输出会被独立审查器对照目标检查。只有真正达成目标才能收工：
完成任务后必须用工具验证结果（跑测试、检查文件），不要凭感觉宣布完成。"""

REVIEWER_SYSTEM = """你是目标达成审查器。你独立于执行者，只根据证据判断。
严格但公正：只在有明确证据表明目标达成时判 done。"""

# ============================================================
# 第一部分：执行 agent 的小工具池
# ============================================================

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "错误: 危险命令已被拦截"
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:30000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 超时 (120s)"
    except OSError as e:
        return f"错误: {e}"


def read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[:30000] or "(空文件)"
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


def list_files(path: str = ".") -> str:
    try:
        return "\n".join(sorted(os.listdir(path or "."))[:300]) or "(空目录)"
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
    {"name": "list_files", "description": "列出目录。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string", "default": "."}}}},
]

TOOL_HANDLERS = {"bash": run_bash, "read_file": read_file,
                 "write_file": write_file, "list_files": list_files}

# ============================================================
# 第二部分：执行一轮 —— 跑到模型想收工为止
# ============================================================

def run_execution_round(messages: list) -> str:
    """跑 agent loop 直到模型停止调用工具。返回它的收尾陈述。"""
    for _ in range(25):   # 单轮内部也有上限
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content
                           if getattr(b, "type", None) == "text")

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[33m[{block.name}] {str(block.input)[:100]}\033[0m")
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 无工具 {block.name}"
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            print(str(output)[:150])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
    return "(单轮执行超过上限)"


# ============================================================
# 第三部分：目标闸门 —— 独立判断器
# ============================================================

def review_goal(goal: str, messages: list, final_claim: str) -> dict:
    """独立 LLM 审查目标是否达成。返回 {status, feedback}。
    status: done(达成) / continue(没达成但可继续) / give_up(无法达成)。"""
    transcript = []
    for msg in messages[-20:]:
        c = msg["content"]
        if isinstance(c, str):
            transcript.append(f"[{msg['role']}] {c[:1500]}")
        elif isinstance(c, list):
            parts = []
            for b in c:
                if getattr(b, "type", None) == "text":
                    parts.append(b.text[:800])
                elif getattr(b, "type", None) == "tool_use":
                    parts.append(f"(工具 {b.name}: {json.dumps(b.input, ensure_ascii=False)[:200]})")
                elif getattr(b, "type", None) == "tool_result":
                    parts.append(f"(结果: {str(b.content)[:400]})")
            transcript.append(f"[{msg['role']}] " + " ".join(parts))

    prompt = f"""目标: {goal}

执行者的收尾陈述: {final_claim}

工作记录(最近):
{chr(10).join(transcript)[-12000:]}

判断目标是否已真正达成。只输出 JSON:
{{"status": "done|continue|give_up", "feedback": "一句话理由; continue 时写清下一步该做什么"}}"""

    resp = client.messages.create(
        model=MODEL, system=REVIEWER_SYSTEM,
        messages=[{"role": "user", "content": prompt}], max_tokens=500)
    text = resp.content[0].text.strip()
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        verdict = json.loads(text[start:end])
        if verdict.get("status") not in ("done", "continue", "give_up"):
            raise ValueError
        return verdict
    except (ValueError, json.JSONDecodeError):
        # 审查器输出异常时保守处理：当作未达成，但只允许一次
        return {"status": "continue", "feedback": "审查器输出异常，请继续核对目标。"}


# ============================================================
# 第四部分：Goal Loop —— 审查驱动的续轮
# ============================================================

def goal_loop(goal: str):
    print(f"\033[36m🎯 目标: {goal}\033[0m")
    messages = [{"role": "user", "content": f"目标: {goal}"}]

    for round_no in range(MAX_CONTINUATIONS + 1):
        if round_no > 0:
            print(f"\033[35m══ 续轮 {round_no}/{MAX_CONTINUATIONS} ══\033[0m")

        final_claim = run_execution_round(messages)
        print(f"\033[2m执行者陈述: {final_claim[:200]}\033[0m")
        print("\033[35m⚖ 审查中...\033[0m")

        verdict = review_goal(goal, messages, final_claim)
        status, feedback = verdict["status"], verdict.get("feedback", "")
        print(f"\033[35m⚖ 审查结论: {status} — {feedback}\033[0m")

        if status == "done":
            print(f"\033[32m✅ 目标达成（第 {round_no + 1} 轮通过审查）\033[0m")
            return final_claim

        if status == "give_up":
            print("\033[31m⛔ 审查器判定目标无法达成，交还控制权\033[0m")
            return f"无法完成目标: {feedback}"

        if round_no >= MAX_CONTINUATIONS:
            print("\033[31m⛔ 已达续轮上限，交还控制权\033[0m")
            return f"续轮上限内未达成目标。最后一次审查意见: {feedback}"

        # continue: 把审查意见注入为新的 user 消息，自动续跑
        messages.append({"role": "user", "content":
                         f"[审查意见] 目标尚未达成。{feedback}\n请继续。"})

    return "(不应到达此处)"


if __name__ == "__main__":
    print("s17: Goal Loop — 输入一个目标，观察执行-审查-续轮闭环。q 退出\n")
    while True:
        try:
            goal = input("\033[36ms17 目标 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if goal.strip().lower() in ("q", "exit", ""):
            break
        result = goal_loop(goal.strip())
        print(f"\n\033[1m最终结果:\033[0m\n{result}\n")
