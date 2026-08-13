#!/usr/bin/env python3
"""
s12_cron_scheduler - Cron Scheduler：定时触发，不需要人推

到目前为止，agent 都是"踹一下动一下"：没人输入，它就闲着。
s12 给它装上闹钟：agent 可以给自己安排未来要做的事，到点自动执行。

    cron_add(prompt="检查服务健康状态", every_seconds=60)
        |
        v
    调度表持久化到 cron/jobs.json（重启不丢）
        |
    调度线程每秒巡检: 到期了吗?
        | 到期
    把该任务的 prompt 推入事件队列, 更新 next_run
        |
    主循环(不等用户输入)取出事件 --> 直接跑 agent_loop
        v
    agent 主动醒来干活

    +---------+   每秒巡检   +-----------+   到期    +------------+
    | 调度线程 | -----------> | jobs.json | -------> | 事件队列    |
    +---------+              +-----------+          +-----+------+
                                                          |
                                                          v
                                                    agent_loop(prompt)

运行:
    python s12_cron_scheduler/code.py
    (试试: 让 agent 每 30 秒检查一次当前时间并写进日志)
"""

import json
import os
import queue
import select
import sys
import threading
import time
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

CRON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cron", "jobs.json")

SYSTEM = f"""你是一个位于 {os.getcwd()} 的常驻编程 agent，带有定时调度能力。
你可以用 cron_add 给未来的自己安排任务（到点会自动唤醒你执行），
用 cron_list 查看安排，用 cron_remove 取消。
被定时任务唤醒时，你的消息开头会标注 [定时触发]。"""

# ============================================================
# 第一部分：调度表（持久化）
# ============================================================

class CronScheduler:
    def __init__(self, path: str):
        self.path = path
        self.jobs = []
        self.events = queue.Queue()   # 到期事件队列
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.jobs = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.jobs = []

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, ensure_ascii=False, indent=2)

    def add(self, prompt: str, every_seconds: int) -> dict:
        job = {
            "id": f"cron-{uuid.uuid4().hex[:8]}",
            "prompt": prompt,
            "every_seconds": max(int(every_seconds), 5),  # 最小 5 秒，防抖
            "next_run": int(time.time()) + max(int(every_seconds), 5),
            "enabled": True,
        }
        self.jobs.append(job)
        self._save()
        return job

    def remove(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j["id"] != job_id]
        self._save()
        return len(self.jobs) < before

    def render(self) -> str:
        active = [j for j in self.jobs if j["enabled"]]
        if not active:
            return "(没有定时任务)"
        lines = []
        for j in active:
            eta = max(j["next_run"] - int(time.time()), 0)
            lines.append(f"- {j['id']} 每 {j['every_seconds']}s "
                         f"(下次 {eta}s 后): {j['prompt'][:60]}")
        return "\n".join(lines)

    def tick(self):
        """巡检一次：到期的任务推入事件队列，并安排下一次。"""
        now = int(time.time())
        due = False
        for job in self.jobs:
            if job["enabled"] and now >= job["next_run"]:
                self.events.put(job)
                job["next_run"] = now + job["every_seconds"]
                due = True
        if due:
            self._save()

    def start_thread(self):
        """后台巡检线程，每秒 tick 一次。"""
        def _loop():
            while True:
                self.tick()
                time.sleep(1)
        threading.Thread(target=_loop, daemon=True).start()


CRON = CronScheduler(CRON_FILE)

# ============================================================
# 第二部分：工具实现
# ============================================================

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "错误: 危险命令已被拦截"
    import subprocess
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 超时 (120s)"
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


def cron_add(prompt: str, every_seconds: int) -> str:
    job = CRON.add(prompt, every_seconds)
    print(f"\033[34m[cron] 新增 {job['id']} 每 {job['every_seconds']}s\033[0m")
    return f"定时任务已创建: {job['id']}，每 {job['every_seconds']} 秒触发一次。\n当前安排:\n{CRON.render()}"


def cron_list() -> str:
    return CRON.render()


def cron_remove(job_id: str) -> str:
    if CRON.remove(job_id):
        return f"已取消 {job_id}。\n当前安排:\n{CRON.render()}"
    return f"错误: 找不到定时任务 {job_id}"


TOOLS = [
    {"name": "cron_add",
     "description": "创建定时任务：每隔 every_seconds 秒，harness 会自动唤醒你执行 prompt 描述的工作。最小间隔 5 秒。",
     "input_schema": {"type": "object",
                      "properties": {"prompt": {"type": "string",
                                                "description": "到点要执行的任务描述"},
                                     "every_seconds": {"type": "integer"}},
                      "required": ["prompt", "every_seconds"]}},
    {"name": "cron_list", "description": "查看所有定时任务。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cron_remove", "description": "取消一个定时任务。",
     "input_schema": {"type": "object",
                      "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "write_file", "description": "写入文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
]

TOOL_HANDLERS = {
    "cron_add": cron_add,
    "cron_list": cron_list,
    "cron_remove": cron_remove,
    "bash": run_bash,
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
                output = f"错误: {e}"
            print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


def print_final(messages: list):
    for block in messages[-1]["content"]:
        if getattr(block, "type", None) == "text":
            print(block.text)


# ============================================================
# 第四部分：主循环 —— 用户输入与定时事件双路驱动
# ============================================================

def main():
    CRON.start_thread()
    print(f"s12: Cron Scheduler — 调度表: {CRON_FILE}")
    print("试试: 安排一个每 30 秒执行的定时任务，然后静等它自动触发。q 退出\n")

    history = []
    while True:
        # 用 select 等用户输入，但每秒醒一次检查定时事件
        sys.stdout.write("\033[36ms12 >> \033[0m")
        sys.stdout.flush()
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
        except (KeyboardInterrupt, OSError):
            break

        if ready:
            query = sys.stdin.readline().strip()
            if query.lower() in ("q", "exit"):
                break
            if query:
                history.append({"role": "user", "content": query})
                agent_loop(history)
                print_final(history)
            continue

        # 没有用户输入：处理到期的定时任务
        while True:
            try:
                job = CRON.events.get_nowait()
            except queue.Empty:
                break
            print(f"\n\033[34m[cron] ⏰ 触发 {job['id']}: {job['prompt'][:60]}\033[0m")
            trigger = f"[定时触发 {job['id']}] 请执行你之前安排的任务: {job['prompt']}"
            history.append({"role": "user", "content": trigger})
            agent_loop(history)
            print_final(history)


if __name__ == "__main__":
    main()
