#!/usr/bin/env python3
"""
s11_background_tasks - Background Tasks：慢操作丢后台，agent 继续思考

问题：装依赖、跑完整测试、构建镜像……动辄几十秒上百秒。
同步执行时 agent 只能干等，整个循环被阻塞。

解法：后台任务 + 通知队列——

    bash_background("npm install")
        |
        v
    harness 起一个线程跑命令，立刻返回 job_id   <-- 模型拿到 id 继续干别的
        |
        ... 模型并行做其他工作 ...
        |
    命令跑完 -> 结果推入通知队列
        |
    下一轮 LLM 调用前 -> 队列里的通知注入为 user 消息
        v
    模型得知结果，决定下一步

    +-----------+   bg_run    +-----------+
    | agent loop| ----------> | 后台线程   |
    |  继续思考  |             | 跑慢命令   |
    +-----^-----+             +-----+-----+
          |  通知注入                | 完成
          +-------------------------+

运行:
    python s11_background_tasks/code.py
    (试试: 后台跑 sleep 5 && echo 完成，同时做别的事)
"""

import os
import queue
import subprocess
import threading
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。
预计超过 10 秒的命令（安装依赖、完整测试、构建）请用 bash_background 放后台，
拿到 job_id 后继续做其他事；后台任务完成时会自动通知你，也可以用 check_job 主动查询。"""

# ============================================================
# 第一部分：后台任务管理器
# ============================================================

class BackgroundManager:
    def __init__(self):
        self.jobs = {}                       # job_id -> 状态字典
        self.notifications = queue.Queue()   # 完成通知队列
        self._lock = threading.Lock()

    def start(self, command: str) -> str:
        """启动后台命令，立刻返回 job_id。"""
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = {"id": job_id, "command": command,
               "status": "running", "output": ""}
        with self._lock:
            self.jobs[job_id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job_id

    def _run(self, job: dict):
        """在独立线程里跑命令，完成后把通知推入队列。"""
        try:
            r = subprocess.run(job["command"], shell=True, capture_output=True,
                               text=True, timeout=600)
            out = (r.stdout + r.stderr).strip()
            job["output"] = out[:20000] if out else "(无输出)"
            job["status"] = "completed" if r.returncode == 0 else f"failed(exit={r.returncode})"
        except subprocess.TimeoutExpired:
            job["output"] = "(超时 600s)"
            job["status"] = "failed(timeout)"
        except OSError as e:
            job["output"] = str(e)
            job["status"] = "failed"
        # 完成 -> 通知队列
        self.notifications.put(job["id"])
        print(f"\033[35m[bg] {job['id']} {job['status']}\033[0m")

    def status(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def drain_notifications(self) -> list:
        """取走所有待投递的完成通知。"""
        done = []
        while True:
            try:
                done.append(self.notifications.get_nowait())
            except queue.Empty:
                break
        return done


BG = BackgroundManager()

# ============================================================
# 第二部分：工具实现
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
        return "错误: 超时 (120s)。考虑用 bash_background 放后台。"
    except OSError as e:
        return f"错误: {e}"


def bash_background(command: str) -> str:
    """后台执行，立即返回。"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "错误: 危险命令已被拦截"
    job_id = BG.start(command)
    print(f"\033[35m[bg] 启动后台任务 {job_id}: {command[:80]}\033[0m")
    return f"后台任务已启动: {job_id}。完成后会自动通知，也可用 check_job 查询。"


def check_job(job_id: str) -> str:
    job = BG.status(job_id)
    if job is None:
        return f"错误: 未知的 job_id: {job_id}"
    if job["status"] == "running":
        return f"{job_id} 仍在运行: {job['command']}"
    return f"{job_id} [{job['status']}]:\n{job['output']}"


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
    {"name": "bash", "description": "同步执行 shell 命令（适合 10 秒内的快命令）。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "bash_background",
     "description": "后台执行慢命令（安装依赖、完整测试、构建），立即返回 job_id。完成后会自动通知你。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "check_job", "description": "查询后台任务状态与结果。",
     "input_schema": {"type": "object",
                      "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
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
    "bash": run_bash,
    "bash_background": bash_background,
    "check_job": check_job,
    "read_file": read_file,
    "write_file": write_file,
}


# ============================================================
# 第三部分：通知注入 —— 本章的关键接线
# ============================================================

def inject_notifications(messages: list):
    """每轮 LLM 调用前，把完成的后台任务结果注入对话。"""
    done = BG.drain_notifications()
    if not done:
        return
    lines = []
    for job_id in done:
        job = BG.status(job_id)
        lines.append(f"- {job_id} [{job['status']}] `{job['command']}`\n{job['output'][:2000]}")
    notice = ("[harness 通知] 以下后台任务已完成:\n" + "\n".join(lines)
              + "\n请根据结果决定下一步。")
    messages.append({"role": "user", "content": notice})
    print(f"\033[35m[bg] 已注入 {len(done)} 条完成通知\033[0m")


# ============================================================
# 第四部分：循环
# ============================================================

def agent_loop(messages: list):
    while True:
        inject_notifications(messages)   # <-- 调用前投递后台结果
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


if __name__ == "__main__":
    print("s11: Background Tasks")
    print("试试: 后台运行 'sleep 5 && echo 构建完成'，同时让 agent 写点东西。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms11 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # 循环退出前再检查一次，避免遗漏刚完成的任务
        inject_notifications(history)
        for block in history[-1]["content"]:
            if isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    print(block.get("text", ""))
            elif getattr(block, "type", None) == "text":
                print(block.text)
        print()
