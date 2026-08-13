#!/usr/bin/env python3
"""
s16_workflow_runtime - Workflow Runtime：编排形状固定时，就把它写进代码

s01-s15 的哲学是"让模型自由决策"。但有些流程的形状是**确定**的：
设计 -> 实现 -> 测试 -> 报告，每次都一样。这种时候让模型每轮重新
决策"下一步干嘛"既浪费 token 又不稳定。

正确做法：把固定编排写进代码（workflow），模型只负责每一步内部
的具体执行。这就是"编排归代码，执行归模型"的边界。

    Workflow 定义(代码):
        step1 design   --> agent 执行(自由发挥)
        step2 implement--> agent 执行
        step3 test     --> agent 执行
        step4 report   --> agent 执行

    journal.jsonl(每步完成即落盘):
        {"run_id":..., "step":"design", "status":"completed", "output":...}

    中断后重启 -> 读 journal -> 从第一个未完成步骤续跑

运行:
    python s16_workflow_runtime/code.py run "给 utils 库添加一个 slugify 函数"
    python s16_workflow_runtime/code.py resume        # 中断后续跑
"""

import json
import os
import subprocess
import sys
import time
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL_FILE = os.path.join(HERE, "journal.jsonl")

# ============================================================
# 第一部分：Workflow 定义 —— 固定编排写进代码
# ============================================================

# 每步 = (步骤名, prompt 模板)。{goal} 会被替换成用户目标，
# {prev_outputs} 会被替换成前面步骤的产出摘要。
WORKFLOW_STEPS = [
    ("design",
     "目标: {goal}\n\n你是设计阶段。请输出简明设计方案：要改/建哪些文件、"
     "函数签名、边界情况。不要写完整实现。\n\n已有上下文:\n{prev_outputs}"),
    ("implement",
     "目标: {goal}\n\n设计方案如下:\n{prev_outputs}\n\n"
     "你是实现阶段。按方案写代码，用工具实际创建/修改文件。"),
    ("test",
     "目标: {goal}\n\n已完成的实现:\n{prev_outputs}\n\n"
     "你是测试阶段。为刚才的实现写并运行测试，报告结果。"
     "测试失败就直接修复代码直到通过。"),
    ("report",
     "目标: {goal}\n\n全过程记录:\n{prev_outputs}\n\n"
     "你是总结阶段。输出最终交付报告：做了什么、改了哪些文件、测试结果。"),
]

STEP_TOOLS = [
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


STEP_HANDLERS = {"bash": run_bash, "read_file": read_file, "write_file": write_file}

# ============================================================
# 第二部分：单步执行 —— 步骤内部仍是自由 agent 循环
# ============================================================

MAX_STEP_TURNS = 20


def run_step(step_name: str, prompt: str) -> str:
    """一个步骤 = 一次受限的 agent loop。返回该步骤的最终文本产出。"""
    messages = [{"role": "user", "content": prompt}]
    for _ in range(MAX_STEP_TURNS):
        resp = client.messages.create(
            model=MODEL,
            system=f"你是 workflow 中 [{step_name}] 阶段的工作者，位于 {os.getcwd()}。"
                   f"专注完成本阶段职责，完成后输出本阶段的产出文本。",
            messages=messages, tools=STEP_TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", None) == "text")

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            print(f"\033[2m   [{step_name}] {block.name}: {str(block.input)[:80]}\033[0m")
            handler = STEP_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 无工具 {block.name}"
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
    return f"(步骤 {step_name} 超过 {MAX_STEP_TURNS} 轮未完成)"


# ============================================================
# 第三部分：journal —— 持久化进度，支持续跑
# ============================================================

def journal_read(run_id: str) -> dict:
    """读某次 run 的进度: {step_name: output}。"""
    progress = {}
    if not os.path.exists(JOURNAL_FILE):
        return progress
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("run_id") == run_id and entry.get("status") == "completed":
                progress[entry["step"]] = entry.get("output", "")
    return progress


def journal_write(run_id: str, step: str, output: str):
    with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "step": step,
                            "status": "completed", "output": output[:5000],
                            "ts": int(time.time())}, ensure_ascii=False) + "\n")


def latest_run_id() -> str | None:
    """找到 journal 里最近一次未完成的 run。"""
    if not os.path.exists(JOURNAL_FILE):
        return None
    runs = {}
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                runs[entry["run_id"]] = entry.get("ts", 0)
            except (json.JSONDecodeError, KeyError):
                continue
    for run_id in sorted(runs, key=runs.get, reverse=True):
        if len(journal_read(run_id)) < len(WORKFLOW_STEPS):
            return run_id     # 未跑完的最近一次
    return None


# ============================================================
# 第四部分：Runtime —— 按固定编排推进，跳过已完成步骤
# ============================================================

def run_workflow(goal: str, run_id: str):
    progress = journal_read(run_id)
    prev_outputs = "\n\n".join(
        f"## {name}\n{progress[name]}" for name, _ in WORKFLOW_STEPS
        if name in progress) or "(无)"

    print(f"\033[36m[workflow] run={run_id} 目标: {goal}\033[0m")

    for step_name, template in WORKFLOW_STEPS:
        if step_name in progress:
            print(f"\033[32m[workflow] ✓ {step_name}（journal 已有，跳过）\033[0m")
            continue

        print(f"\033[33m[workflow] ▶ {step_name} 开始\033[0m")
        prompt = template.format(goal=goal, prev_outputs=prev_outputs[-8000:])
        output = run_step(step_name, prompt)

        journal_write(run_id, step_name, output)    # 完成一步立刻落盘
        progress[step_name] = output
        prev_outputs += f"\n\n## {step_name}\n{output}"
        print(f"\033[32m[workflow] ✓ {step_name} 完成，已写入 journal\033[0m")
        print(f"\033[2m{output[:300]}\033[0m\n")

    print(f"\033[36m[workflow] 全部步骤完成 🎉\033[0m")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        goal = " ".join(sys.argv[2:])
        run_workflow(goal, run_id=f"run-{uuid.uuid4().hex[:8]}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "resume":
        run_id = latest_run_id()
        if run_id is None:
            print("没有可续跑的 workflow")
        else:
            print(f"续跑 {run_id}（已完成的步骤会被跳过）")
            print("原目标无法从 journal 恢复，请确认后续步骤的上下文。")
            goal = input("请重新输入该 workflow 的目标: ").strip()
            run_workflow(goal, run_id)
    else:
        print("用法:")
        print('  python code.py run "给 utils 库添加 slugify 函数并测试"')
        print("  python code.py resume")
