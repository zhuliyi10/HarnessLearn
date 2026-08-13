#!/usr/bin/env python3
"""
s10_task_system - Task System：大目标拆成小任务，排好序，持久化

s05 的 todo 只在内存里，进程一退出就没了；它也没有依赖关系。
s10 把"计划"升级为真正的任务系统：

    TaskRecord = {id, title, status, blocked_by, created_at, updated_at}

    - 依赖图: 任务可以声明 blocked_by=[...], 依赖没完成就不能开工
    - 持久化: 每个任务落盘成 tasks/<id>.json，重启不丢
    - 就绪判定: 一个任务所有依赖都 completed 时才是 "ready"

    +--------+     blocked_by     +--------+
    | task-2 | -----------------> | task-1 |
    | pending|                    |  done  |
    +--------+                    +--------+
    task-2 在 task-1 完成前一直是 blocked

任务系统是后面一切"长期运行"机制的地基：
后台任务(s11)、调度(s12)、团队协作(s13)都消费任务。

运行:
    python s10_task_system/code.py
"""

import json
import os
import subprocess
import time
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")

# ============================================================
# 第一部分：任务存储（每个任务一个 JSON 文件）
# ============================================================

class TaskStore:
    STATUSES = ("pending", "in_progress", "completed", "failed")

    def __init__(self, tasks_dir: str):
        self.dir = tasks_dir
        os.makedirs(tasks_dir, exist_ok=True)

    def _path(self, task_id: str) -> str:
        # 防路径穿越
        safe = os.path.basename(task_id)
        return os.path.join(self.dir, f"{safe}.json")

    def create(self, title: str, blocked_by: list | None = None) -> dict:
        task = {
            "id": f"task-{uuid.uuid4().hex[:8]}",
            "title": title,
            "status": "pending",
            "blocked_by": blocked_by or [],
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        self._write(task)
        return task

    def get(self, task_id: str) -> dict | None:
        try:
            with open(self._path(task_id), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def all(self) -> list:
        tasks = []
        for fname in sorted(os.listdir(self.dir)):
            if fname.endswith(".json"):
                t = self.get(fname[:-5])
                if t:
                    tasks.append(t)
        return tasks

    def update_status(self, task_id: str, status: str):
        if status not in self.STATUSES:
            raise ValueError(f"非法状态: {status}")
        task = self.get(task_id)
        if task is None:
            raise ValueError(f"任务不存在: {task_id}")
        task["status"] = status
        task["updated_at"] = int(time.time())
        self._write(task)
        return task

    def is_ready(self, task: dict) -> bool:
        """就绪 = pending 且所有依赖都 completed。"""
        if task["status"] != "pending":
            return False
        for dep_id in task["blocked_by"]:
            dep = self.get(dep_id)
            if dep is None or dep["status"] != "completed":
                return False
        return True

    def render(self) -> str:
        tasks = self.all()
        if not tasks:
            return "(任务列表为空)"
        icon = {"pending": "[ ]", "in_progress": "[~]",
                "completed": "[x]", "failed": "[!]"}
        lines = []
        for t in tasks:
            mark = icon[t["status"]]
            ready = " (ready)" if self.is_ready(t) else ""
            deps = f"  依赖: {','.join(t['blocked_by'])}" if t["blocked_by"] else ""
            lines.append(f"{t['id']} {mark} {t['title']}{ready}{deps}")
        return "\n".join(lines)

    def _write(self, task: dict):
        with open(self._path(task["id"]), "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)


STORE = TaskStore(TASKS_DIR)

# ============================================================
# 第二部分：任务工具
# ============================================================

def task_create(title: str, blocked_by: list | None = None) -> str:
    """创建任务。blocked_by 是依赖任务的 id 列表。"""
    # 校验依赖存在
    for dep_id in blocked_by or []:
        if STORE.get(dep_id) is None:
            return f"错误: 依赖的任务不存在: {dep_id}"
    task = STORE.create(title, blocked_by)
    print(f"\033[32m[task] 创建 {task['id']}: {title}\033[0m")
    return f"已创建任务 {task['id']}。\n当前任务列表:\n{STORE.render()}"


def task_list() -> str:
    return STORE.render()


def task_update(task_id: str, status: str) -> str:
    """更新任务状态: pending / in_progress / completed / failed。"""
    task = STORE.get(task_id)
    if task is None:
        return f"错误: 任务不存在: {task_id}"
    if status == "in_progress" and not STORE.is_ready(task):
        return (f"错误: {task_id} 尚有依赖未完成或不是 pending，不能开工。"
                f"\n当前任务列表:\n{STORE.render()}")
    try:
        STORE.update_status(task_id, status)
    except ValueError as e:
        return f"错误: {e}"
    print(f"\033[32m[task] {task_id} -> {status}\033[0m")
    return f"{task_id} 已更新为 {status}。\n当前任务列表:\n{STORE.render()}"


# ============================================================
# 第三部分：基础工具 + SYSTEM
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


SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent，带有一个持久化任务系统。
工作纪律：
1. 收到复杂任务时，先用 task_create 拆成带依赖关系的任务图（想清楚谁 blocked_by 谁）。
2. 开工前 task_list 找到 ready 的任务，task_update 标为 in_progress。
3. 做完立刻标 completed，解锁后续任务。
当前任务状态:
{STORE.render()}"""

TOOLS = [
    {"name": "task_create", "description": "创建任务。title 是任务描述，blocked_by 是依赖任务 id 列表（可选）。",
     "input_schema": {"type": "object",
                      "properties": {"title": {"type": "string"},
                                     "blocked_by": {"type": "array",
                                                    "items": {"type": "string"}}},
                      "required": ["title"]}},
    {"name": "task_list", "description": "查看所有任务及依赖状态。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "task_update", "description": "更新任务状态。status: pending|in_progress|completed|failed。",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"},
                                     "status": {"type": "string",
                                                "enum": ["pending", "in_progress",
                                                         "completed", "failed"]}},
                      "required": ["task_id", "status"]}},
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
]

TOOL_HANDLERS = {
    "task_create": task_create,
    "task_list": task_list,
    "task_update": task_update,
    "bash": run_bash,
}


# ============================================================
# 第四部分：标准循环
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
            if block.name not in ("task_create", "task_update"):
                print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print(f"s10: Task System — 任务目录: {TASKS_DIR}（重启后任务仍在）")
    print("给一个多步任务，观察任务图构建与依赖推进。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms10 >> \033[0m")
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
