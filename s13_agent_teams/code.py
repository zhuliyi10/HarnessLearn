#!/usr/bin/env python3
r"""
s13_agent_teams - Agent Teams：一个 Agent 顾不过来，就让队友分工协作

单个 agent 串行干活太慢。s13 引入团队：

    - 任务池: 复用 s10 的任务文件(带依赖)，是团队协作的共享看板
    - 原子认领: 多个队友并发抢任务，用 lockfile 保证一个任务只归一个人
    - 独立工作区: 每个队友有自己的 workspace 目录，互不干扰
    - 消息邮箱: 队友间可以发消息，收件人下一轮"睁眼"时读到

    用户
      |
    主 agent(协调者): 拆任务图 -> 启动队友 -> 汇报
      |
    +------------+-------------+
    |            |             |
    worker-a   worker-b    worker-c     <- 每人一个线程
    workspace-a workspace-b workspace-c  <- 每人一个目录
      \           |           /
       +----------+----------+
       |   tasks/ 共享任务池   |  <-- 原子认领 (O_CREAT|O_EXCL)
       +---------------------+

运行:
    python s13_agent_teams/code.py
    (试试: 让协调者安排 3 个队友分别写三个独立的模块)
"""

import json
import os
import subprocess
import threading
import time
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
TEAM_DIR = os.path.join(BASE_DIR, "team")

os.makedirs(TASKS_DIR, exist_ok=True)
os.makedirs(TEAM_DIR, exist_ok=True)


# ============================================================
# 第一部分：共享任务池（复用 s10 的文件布局，加原子认领）
# ============================================================

def task_create(title: str, blocked_by: list | None = None) -> dict:
    task = {"id": f"task-{uuid.uuid4().hex[:8]}", "title": title,
            "status": "pending", "blocked_by": blocked_by or [],
            "owner": None, "result": "", "updated_at": int(time.time())}
    with open(os.path.join(TASKS_DIR, task["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
    return task


def task_get(task_id: str) -> dict | None:
    try:
        with open(os.path.join(TASKS_DIR, task_id + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def task_all() -> list:
    tasks = []
    for fname in sorted(os.listdir(TASKS_DIR)):
        if fname.endswith(".json"):
            t = task_get(fname[:-5])
            if t:
                tasks.append(t)
    return tasks


def task_save(task: dict):
    task["updated_at"] = int(time.time())
    with open(os.path.join(TASKS_DIR, task["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)


def is_ready(task: dict) -> bool:
    if task["status"] != "pending":
        return False
    for dep_id in task["blocked_by"]:
        dep = task_get(dep_id)
        if dep is None or dep["status"] != "completed":
            return False
    return True


def try_claim(task_id: str, worker: str) -> bool:
    """原子认领：利用 O_CREAT|O_EXCL 的文件锁，多 worker 并发安全。"""
    lock_path = os.path.join(TASKS_DIR, task_id + ".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, worker.encode())
        os.close(fd)
    except FileExistsError:
        return False          # 别人先抢到了
    task = task_get(task_id)
    if task is None:
        os.unlink(lock_path)
        return False
    task["status"] = "in_progress"
    task["owner"] = worker
    task_save(task)
    return True


def complete_task(task_id: str, result: str):
    task = task_get(task_id)
    if task is None:
        return
    task["status"] = "completed"
    task["result"] = result[:2000]
    task_save(task)
    lock = os.path.join(TASKS_DIR, task_id + ".lock")
    if os.path.exists(lock):
        os.unlink(lock)


# ============================================================
# 第二部分：消息邮箱
# ============================================================

def send_message(to: str, text: str, sender: str = "lead"):
    """把消息追加到收件人的 inbox.jsonl。"""
    inbox_dir = os.path.join(TEAM_DIR, to)
    os.makedirs(inbox_dir, exist_ok=True)
    entry = {"from": sender, "text": text, "ts": int(time.time())}
    with open(os.path.join(inbox_dir, "inbox.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def drain_inbox(name: str) -> str:
    """读空收件箱，返回汇总文本。"""
    inbox_path = os.path.join(TEAM_DIR, name, "inbox.jsonl")
    if not os.path.exists(inbox_path):
        return ""
    with open(inbox_path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    if not lines:
        return ""
    os.remove(inbox_path)
    return "\n".join(f"[来自 {m['from']}] {m['text']}" for m in lines)


# ============================================================
# 第三部分：worker 队友 —— 每人一个线程 + 独立工作区
# ============================================================

WORKER_TOOLS = [
    {"name": "bash", "description": "在你的工作目录内执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "write_file", "description": "在你的工作目录内写文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "read_file", "description": "读取你工作目录内的文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
]

MAX_WORKER_TURNS = 15


def worker_loop(name: str, log: list):
    """一个队友的完整生命周期：领任务 -> 干活 -> 交付，直到没有就绪任务。"""
    workspace = os.path.join(TEAM_DIR, name, "workspace")
    os.makedirs(workspace, exist_ok=True)

    system = (f"你是团队成员 {name}，工作目录是 {workspace}。"
              f"只在自己的工作目录内工作。完成当前任务后输出简明总结。")

    while True:
        # 1. 认领一个就绪任务（原子）
        claimed = None
        for task in task_all():
            if is_ready(task) and try_claim(task["id"], name):
                claimed = task
                break
        if claimed is None:
            log.append(f"[{name}] 没有就绪任务，收工")
            return

        log.append(f"[{name}] 认领 {claimed['id']}: {claimed['title']}")

        # 2. 干活：独立 messages + 独立循环（s06 的思路）
        inbox = drain_inbox(name)
        briefing = f"你的任务: {claimed['title']}\n"
        if inbox:
            briefing += f"收到的队友消息:\n{inbox}\n"
        briefing += "完成后直接输出总结，不要调用额外工具。"

        messages = [{"role": "user", "content": briefing}]
        result = "(未完成)"
        for _ in range(MAX_WORKER_TURNS):
            resp = client.messages.create(
                model=MODEL, system=system, messages=messages,
                tools=WORKER_TOOLS, max_tokens=8000,
            )
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                result = "".join(b.text for b in resp.content
                                 if getattr(b, "type", None) == "text")
                break
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                output = worker_exec(name, workspace, block.name, block.input)
                log.append(f"[{name}]   {block.name}: {str(block.input)[:80]}")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id, "content": output})
            messages.append({"role": "user", "content": results})

        # 3. 交付
        complete_task(claimed["id"], result)
        log.append(f"[{name}] 完成 {claimed['id']}")


def worker_exec(name: str, workspace: str, tool_name: str, tool_input: dict) -> str:
    """worker 的工具执行器，全部限制在自己的 workspace 里。"""
    if tool_name == "bash":
        try:
            r = subprocess.run(tool_input.get("command", ""), shell=True,
                               cwd=workspace, capture_output=True,
                               text=True, timeout=120)
            out = (r.stdout + r.stderr).strip()
            return out[:20000] if out else "(无输出)"
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"错误: {e}"
    if tool_name in ("write_file", "read_file"):
        path = tool_input.get("path", "")
        full = os.path.abspath(os.path.join(workspace, path))
        if not full.startswith(workspace):
            return "错误: 禁止访问工作目录之外的路径"
        if tool_name == "write_file":
            os.makedirs(os.path.dirname(full) or workspace, exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(tool_input.get("content", ""))
            return f"已写入 {path}"
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read()[:20000] or "(空文件)"
        except OSError as e:
            return f"错误: {e}"
    return f"错误: 不存在的工具 {tool_name}"


def spawn_team(names: list, log: list):
    """并发启动多个队友线程。"""
    threads = []
    for name in names:
        t = threading.Thread(target=worker_loop, args=(name, log), daemon=True)
        t.start()
        threads.append(t)
    return threads


# ============================================================
# 第四部分：协调者（主 agent）
# ============================================================

TEAM_LOG = []   # 线程共享的活动日志

LEAD_TOOLS = [
    {"name": "task_create",
     "description": "创建团队任务。title 是任务描述，blocked_by 是依赖任务 id 列表。",
     "input_schema": {"type": "object",
                      "properties": {"title": {"type": "string"},
                                     "blocked_by": {"type": "array",
                                                    "items": {"type": "string"}}},
                      "required": ["title"]}},
    {"name": "task_list", "description": "查看任务池状态。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spawn_team",
     "description": "启动队友开始干活。names 是队友名列表（如 ['worker-a','worker-b']）。任务池里的就绪任务会被自动认领。",
     "input_schema": {"type": "object",
                      "properties": {"names": {"type": "array",
                                               "items": {"type": "string"}}},
                      "required": ["names"]}},
    {"name": "send_message",
     "description": "给某个队友发消息（他下一轮会读到）。",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "text": {"type": "string"}},
                      "required": ["to", "text"]}},
    {"name": "team_activity", "description": "查看团队活动日志。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "wait_for_team", "description": "等待所有队友收工（阻塞直到无进行中任务）。",
     "input_schema": {"type": "object", "properties": {}}},
]


def lead_task_create(title: str, blocked_by: list | None = None) -> str:
    task = task_create(title, blocked_by)
    return f"已创建 {task['id']}。"


def lead_task_list() -> str:
    icon = {"pending": "[ ]", "in_progress": "[~]",
            "completed": "[x]", "failed": "[!]"}
    lines = []
    for t in task_all():
        owner = f" @{t['owner']}" if t.get("owner") else ""
        ready = " ready" if is_ready(t) else ""
        lines.append(f"{t['id']} {icon.get(t['status'])} {t['title']}{owner}{ready}")
    return "\n".join(lines) or "(任务池为空)"


def lead_spawn_team(names: list) -> str:
    if not names or len(names) > 5:
        return "错误: 队友数量需在 1-5 之间"
    spawn_team(names, TEAM_LOG)
    return f"已启动队友: {', '.join(names)}。他们会自动认领就绪任务。用 team_activity 观察进度。"


def lead_send_message(to: str, text: str) -> str:
    send_message(to, text, sender="lead")
    return f"已发送给 {to}。"


def lead_team_activity() -> str:
    return "\n".join(TEAM_LOG[-30:]) or "(暂无活动)"


def lead_wait_for_team() -> str:
    """轮询直到没有 in_progress 任务且没有就绪任务。"""
    for _ in range(120):   # 最多等 10 分钟
        tasks = task_all()
        busy = [t for t in tasks if t["status"] == "in_progress"]
        ready = [t for t in tasks if is_ready(t)]
        if not busy and not ready:
            done = [t for t in tasks if t["status"] == "completed"]
            summary = "\n".join(f"- {t['title']} @{t.get('owner')}: {t.get('result', '')[:200]}"
                                for t in done)
            return f"团队已收工，{len(done)} 个任务完成:\n{summary}"
        time.sleep(5)
    return "等待超时，仍有任务未完成。可用 team_activity 查看现状。"


LEAD_HANDLERS = {
    "task_create": lead_task_create,
    "task_list": lead_task_list,
    "spawn_team": lead_spawn_team,
    "send_message": lead_send_message,
    "team_activity": lead_team_activity,
    "wait_for_team": lead_wait_for_team,
}

LEAD_SYSTEM = f"""你是团队协调者，位于 {os.getcwd()}。
工作流程: 收到复杂任务后，用 task_create 拆成带依赖的任务图（任务之间尽量独立以便并行），
然后 spawn_team 启动队友（任务多就多派人），用 wait_for_team 等收工，最后向用户汇总。
当前任务池:
{lead_task_list()}"""


def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=LEAD_SYSTEM, messages=messages,
            tools=LEAD_TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[33m[{block.name}] {str(block.input)[:120]}\033[0m")
            handler = LEAD_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 不存在的工具 {block.name}"
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            print(str(output)[:300])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s13: Agent Teams — 协调者 + 并发队友 + 共享任务池")
    print("试试: 让协调者派 3 个队友各写一个独立的小工具模块。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms13 >> \033[0m")
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
