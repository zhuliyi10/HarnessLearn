#!/usr/bin/env python3
"""
s15_integrated_harness - Integrated Harness：多种机制，一个循环

前面 14 章各自教一个机制。本章把它们全部接回同一个 agent loop，
证明这些机制是"正交可组合"的——循环还是那个循环，只是外围设施变全了。

    集成清单:
    s02 工具池 dispatch map      s07 技能按需加载
    s03 权限闸门                 s08 上下文压缩
    s04 前后置 hooks             s09 记忆(启动注入/退出沉淀)
    s05 TodoWrite               s10 任务系统
    s06 subagent                s11 后台任务 + 通知注入

    user --> messages[]
                |
            [s08 compact]  超预算先压缩
                |
            [s11 通知注入] 后台结果先送达
                |
              LLM 调用
                |
            tool_use? --否--> 结束
                |
    [s04 pre hooks] -> [s03 permission] -> [s02 dispatch 执行] -> [s04 post hooks]
                |
            tool_result 回传, 回到循环开头

运行:
    python s15_integrated_harness/code.py
"""

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TASKS_DIR = os.path.join(HERE, "tasks")
MEMORY_FILE = os.path.join(HERE, "memory", "memories.json")
SKILLS_DIR = os.path.join(ROOT, "skills")

TOKEN_BUDGET = 60000
RECENT_KEEP = 4
SNIP_THRESHOLD = 4000


# ============================================================
# 设施 1：权限闸门 (s03)
# ============================================================

PERMISSION_RULES = [
    ("bash", r"rm\s+-rf\s+/|sudo|shutdown|reboot", "deny"),
    ("bash", r"\brm\b|git\s+push", "ask"),
    ("bash", r"^(ls|cat|pwd|echo|head|tail|grep|wc|find|date|git status|git diff|git log|python)", "allow"),
    ("read_file", r".*", "allow"),
    ("list_files", r".*", "allow"),
    ("task_list", r".*", "allow"),
]


class PermissionGate:
    def __init__(self):
        self.grants = set()

    def check(self, tool_name, tool_input) -> str:
        arg_str = " ".join(str(v) for v in tool_input.values())
        if (tool_name, arg_str) in self.grants:
            return "allow"
        for tool, pattern, decision in PERMISSION_RULES:
            if tool == tool_name and re.search(pattern, arg_str):
                return decision
        return "ask"

    def ask_user(self, tool_name, tool_input) -> bool:
        arg_str = str(tool_input)[:200]
        print(f"\033[35m⚠ 权限确认: {tool_name} {arg_str}\033[0m")
        try:
            ans = input("  [y]允许 / [n]拒绝 / [a]本会话总是允许: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans == "a":
            self.grants.add((tool_name, " ".join(str(v) for v in tool_input.values())))
            return True
        return ans in ("y", "yes")


GATE = PermissionGate()

# ============================================================
# 设施 2：hooks (s04)
# ============================================================

PRE_HOOKS, POST_HOOKS = [], []


def pre_hook(fn):
    PRE_HOOKS.append(fn)
    return fn


def post_hook(fn):
    POST_HOOKS.append(fn)
    return fn


@pre_hook
def audit_log(tool_name, tool_input):
    with open(os.path.join(HERE, "audit.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "tool": tool_name,
                            "input": tool_input}, ensure_ascii=False)[:500] + "\n")
    return "continue", tool_input


@post_hook
def flag_errors(tool_name, tool_input, output):
    if str(output).startswith("错误"):
        return f"[执行失败]\n{output}"
    return output


# ============================================================
# 设施 3：技能加载 (s07)
# ============================================================

def load_skill_catalog() -> tuple[dict, str]:
    catalog = {}
    if os.path.isdir(SKILLS_DIR):
        for fname in sorted(os.listdir(SKILLS_DIR)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(SKILLS_DIR, fname)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
            if not m:
                continue
            try:
                meta = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if meta.get("name"):
                catalog[meta["name"]] = {"description": meta.get("description", ""),
                                         "path": path}
    lines = [f"- {n}: {i['description']}" for n, i in catalog.items()]
    return catalog, "\n".join(lines) or "(无)"


SKILL_CATALOG, SKILL_CATALOG_TEXT = load_skill_catalog()


def load_skill(name: str) -> str:
    info = SKILL_CATALOG.get(name)
    if not info:
        return f"错误: 技能 '{name}' 不存在。可用: {', '.join(SKILL_CATALOG) or '无'}"
    with open(info["path"], encoding="utf-8") as f:
        return f.read()


# ============================================================
# 设施 4：记忆 (s09)
# ============================================================

def load_memory() -> list:
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_memory(items: list):
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def render_memory(items: list) -> str:
    if not items:
        return ""
    return "你记得以下事实（来自过往会话）:\n" + "\n".join(
        f"- {it['content']}" for it in items)


MEMORY = load_memory()

# ============================================================
# 设施 5：todo (s05) + 任务系统 (s10)
# ============================================================

TODOS = []
STATUS_ICON = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


def todo_write(todos: list) -> str:
    global TODOS
    valid = ("pending", "in_progress", "completed")
    new_list = []
    for item in todos:
        status = item.get("status", "pending")
        if not item.get("content") or status not in valid:
            return "错误: todo 需要 content 和合法 status"
        new_list.append({"content": item["content"], "status": status})
    if sum(1 for t in new_list if t["status"] == "in_progress") > 1:
        return "错误: 同时只能有一个 in_progress"
    TODOS = new_list
    return "todo 已更新:\n" + render_todos()


def render_todos() -> str:
    if not TODOS:
        return "(无 todo)"
    lines = [f"{i}. {STATUS_ICON[t['status']]} {t['content']}"
             for i, t in enumerate(TODOS, 1)]
    return "\n".join(lines)


def _task_path(task_id: str) -> str:
    os.makedirs(TASKS_DIR, exist_ok=True)
    return os.path.join(TASKS_DIR, os.path.basename(task_id) + ".json")


def task_create(title: str, blocked_by: list | None = None) -> str:
    for dep in blocked_by or []:
        if not os.path.exists(_task_path(dep)):
            return f"错误: 依赖任务不存在: {dep}"
    task = {"id": f"task-{uuid.uuid4().hex[:8]}", "title": title,
            "status": "pending", "blocked_by": blocked_by or [],
            "updated_at": int(time.time())}
    with open(_task_path(task["id"]), "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
    return f"已创建 {task['id']}。\n{task_list()}"


def task_list() -> str:
    if not os.path.isdir(TASKS_DIR):
        return "(任务池为空)"
    lines = []
    for fname in sorted(os.listdir(TASKS_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(TASKS_DIR, fname), encoding="utf-8") as f:
                t = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        icon = {"pending": "[ ]", "in_progress": "[~]",
                "completed": "[x]", "failed": "[!]"}.get(t["status"], "[ ]")
        deps = f" 依赖:{','.join(t['blocked_by'])}" if t.get("blocked_by") else ""
        lines.append(f"{t['id']} {icon} {t['title']}{deps}")
    return "\n".join(lines) or "(任务池为空)"


def task_update(task_id: str, status: str) -> str:
    if status not in ("pending", "in_progress", "completed", "failed"):
        return "错误: 非法状态"
    try:
        with open(_task_path(task_id), encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, json.JSONDecodeError):
        return f"错误: 任务不存在 {task_id}"
    if status == "in_progress":
        for dep in t.get("blocked_by", []):
            try:
                with open(_task_path(dep), encoding="utf-8") as f:
                    dep_task = json.load(f)
                if dep_task["status"] != "completed":
                    return f"错误: 依赖 {dep} 未完成，不能开工"
            except (OSError, json.JSONDecodeError, KeyError):
                return f"错误: 依赖 {dep} 不存在"
    t["status"] = status
    t["updated_at"] = int(time.time())
    with open(_task_path(task_id), "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=2)
    return f"{task_id} -> {status}。\n{task_list()}"


# ============================================================
# 设施 6：后台任务 (s11)
# ============================================================

BG_JOBS = {}
BG_QUEUE = queue.Queue()


def bash_background(command: str) -> str:
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    job = {"id": job_id, "command": command, "status": "running", "output": ""}
    BG_JOBS[job_id] = job

    def _run():
        try:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, timeout=600)
            job["output"] = ((r.stdout + r.stderr).strip() or "(无输出)")[:20000]
            job["status"] = "completed" if r.returncode == 0 else "failed"
        except (subprocess.TimeoutExpired, OSError) as e:
            job["output"], job["status"] = str(e), "failed"
        BG_QUEUE.put(job_id)

    threading.Thread(target=_run, daemon=True).start()
    return f"后台任务 {job_id} 已启动，完成后自动通知。"


def check_job(job_id: str) -> str:
    job = BG_JOBS.get(job_id)
    if not job:
        return f"错误: 未知 {job_id}"
    if job["status"] == "running":
        return f"{job_id} 仍在运行"
    return f"{job_id} [{job['status']}]:\n{job['output']}"


def inject_bg_notifications(messages: list):
    done = []
    while True:
        try:
            done.append(BG_QUEUE.get_nowait())
        except queue.Empty:
            break
    if not done:
        return
    lines = [f"- {jid} [{BG_JOBS[jid]['status']}]: {BG_JOBS[jid]['output'][:1000]}"
             for jid in done]
    messages.append({"role": "user",
                     "content": "[harness 通知] 后台任务完成:\n" + "\n".join(lines)})


# ============================================================
# 设施 7：基础工具 + subagent (s06)
# ============================================================

def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 超时 (120s)。慢命令请用 bash_background。"
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
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}"
    except OSError as e:
        return f"错误: {e}"


def list_files(path: str = ".") -> str:
    try:
        return "\n".join(sorted(os.listdir(path or "."))[:500]) or "(空目录)"
    except OSError as e:
        return f"错误: {e}"


SUBAGENT_TOOLS = [
    {"name": "bash", "description": "执行 shell 命令。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "读取文件。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
]
SUBAGENT_HANDLERS = {"bash": run_bash, "read_file": read_file}


def run_subagent(description: str) -> str:
    """s06：全新 messages 的独立循环，只回传最终文本。"""
    print(f"\033[35m>> subagent: {description[:80]}\033[0m")
    sub_messages = [{"role": "user", "content": description}]
    for _ in range(15):
        resp = client.messages.create(
            model=MODEL,
            system=f"你是位于 {os.getcwd()} 的调研 subagent。专注完成任务，最后给出简明结论。",
            messages=sub_messages, tools=SUBAGENT_TOOLS, max_tokens=8000)
        sub_messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", None) == "text") or "(无结论)"
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            handler = SUBAGENT_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"错误: 无工具 {block.name}"
            except (TypeError, KeyError, OSError) as e:
                output = f"错误: {e}"
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        sub_messages.append({"role": "user", "content": results})
    return "(subagent 超时)"


# ============================================================
# 工具池组装 (s02 dispatch map)
# ============================================================

TOOLS = [
    {"name": "bash", "description": "执行 shell 命令（10 秒内的快命令）。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "bash_background", "description": "后台执行慢命令，完成后自动通知。",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "check_job", "description": "查询后台任务。",
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
    {"name": "list_files", "description": "列出目录。",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string", "default": "."}}}},
    {"name": "todo_write", "description": "更新本次会话的执行清单。参数 todos 为完整列表。",
     "input_schema": {"type": "object",
                      "properties": {"todos": {"type": "array", "items": {
                          "type": "object",
                          "properties": {"content": {"type": "string"},
                                         "status": {"type": "string",
                                                    "enum": ["pending", "in_progress", "completed"]}},
                          "required": ["content", "status"]}}},
                      "required": ["todos"]}},
    {"name": "task_create", "description": "创建持久化任务（跨会话），可声明依赖。",
     "input_schema": {"type": "object",
                      "properties": {"title": {"type": "string"},
                                     "blocked_by": {"type": "array", "items": {"type": "string"}}},
                      "required": ["title"]}},
    {"name": "task_list", "description": "查看持久化任务池。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "task_update", "description": "更新任务状态。",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"},
                                     "status": {"type": "string",
                                                "enum": ["pending", "in_progress", "completed", "failed"]}},
                      "required": ["task_id", "status"]}},
    {"name": "load_skill", "description": "按名字加载技能全文。",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "subagent", "description": "委派探索性/大输出子任务给独立 subagent，只返回结论。",
     "input_schema": {"type": "object",
                      "properties": {"description": {"type": "string"}},
                      "required": ["description"]}},
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "bash_background": bash_background,
    "check_job": check_job,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "todo_write": todo_write,
    "task_create": task_create,
    "task_list": task_list,
    "task_update": task_update,
    "load_skill": load_skill,
    "subagent": run_subagent,
}

# ============================================================
# SYSTEM：各设施的"常驻部分"在这里汇合
# ============================================================

SYSTEM = f"""你是一个位于 {os.getcwd()} 的完整编程 agent。

可用技能目录（用 load_skill 加载全文）:
{SKILL_CATALOG_TEXT}

{render_memory(MEMORY)}

持久化任务池:
{task_list()}

工作纪律：多步任务先 todo_write 列计划；慢命令用 bash_background；
探索性工作委派 subagent；相关技能先加载再动手。"""

# ============================================================
# 上下文压缩 (s08)
# ============================================================

def estimate_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    total += len(str(b.get("content", b.get("text", ""))))
                else:
                    total += len(str(getattr(b, "text", getattr(b, "content", ""))))
    return total // 3


def compact_snip(messages: list) -> bool:
    changed = False
    for msg in messages[:-RECENT_KEEP]:
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result" \
                    and isinstance(b.get("content"), str) \
                    and len(b["content"]) > SNIP_THRESHOLD:
                b["content"] = b["content"][:SNIP_THRESHOLD] + "\n...[已截断]"
                changed = True
    return changed


def compact_summary(messages: list) -> bool:
    if len(messages) <= RECENT_KEEP + 1:
        return False
    early = messages[:-RECENT_KEEP]
    transcript = "\n".join(
        f"[{m['role']}] {str(m.get('content'))[:3000]}" for m in early)
    resp = client.messages.create(
        model=MODEL,
        system="你是对话压缩器，输出信息密度高的中文摘要。",
        messages=[{"role": "user", "content":
                   "把这段 agent 历史压缩成摘要，保留目标、已完成工作、"
                   "关键事实、未尽事项:\n" + transcript}],
        max_tokens=1200)
    messages[:] = [{"role": "user",
                    "content": f"[harness 摘要]\n{resp.content[0].text}"
                    }] + messages[-RECENT_KEEP:]
    return True


def maybe_compact(messages: list):
    if estimate_tokens(messages) < TOKEN_BUDGET:
        return
    print("\033[35m[compact] 上下文超预算，开始压缩\033[0m")
    if compact_snip(messages) and estimate_tokens(messages) < TOKEN_BUDGET:
        return
    compact_summary(messages)


def normalize_history(messages: list):
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, list) and c and not isinstance(c[0], dict):
            new = []
            for b in c:
                btype = getattr(b, "type", None)
                if btype == "text":
                    new.append({"type": "text", "text": b.text})
                elif btype == "tool_use":
                    new.append({"type": "tool_use", "id": b.id,
                                "name": b.name, "input": b.input})
                elif btype == "tool_result":
                    new.append({"type": "tool_result",
                                "tool_use_id": b.tool_use_id, "content": b.content})
            msg["content"] = new


# ============================================================
# 工具执行管线：pre hooks -> 权限 -> 分发 -> post hooks
# ============================================================

def execute_tool(tool_name: str, tool_input: dict) -> str:
    # s04 前置 hooks
    for hook in PRE_HOOKS:
        action, payload = hook(tool_name, tool_input)
        if action == "block":
            return str(payload)
        if isinstance(payload, dict):
            tool_input = payload

    # s03 权限
    decision = GATE.check(tool_name, tool_input)
    if decision == "deny":
        return "权限拒绝: 该操作被安全策略禁止，请换用其他方式。"
    if decision == "ask" and not GATE.ask_user(tool_name, tool_input):
        return "权限拒绝: 用户不允许此操作，请换用其他方式。"

    # s02 分发执行
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return f"错误: 不存在的工具 {tool_name}"
    try:
        output = handler(**tool_input)
    except (TypeError, KeyError, OSError) as e:
        output = f"错误: 工具执行失败 - {e}"

    # s04 后置 hooks
    for hook in POST_HOOKS:
        output = hook(tool_name, tool_input, output)
    return output


# ============================================================
# 主循环：唯一的循环，所有机制围绕它
# ============================================================

def agent_loop(messages: list):
    while True:
        maybe_compact(messages)           # s08
        inject_bg_notifications(messages)  # s11
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
            output = execute_tool(block.name, block.input)
            print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


# ============================================================
# 会话结束沉淀记忆 (s09 简化版)
# ============================================================

def remember_session(messages: list):
    if len(messages) < 2:
        return
    transcript = "\n".join(f"[{m['role']}] {str(m.get('content'))[:2000]}"
                           for m in messages)[-20000:]
    resp = client.messages.create(
        model=MODEL,
        system="你是记忆提取器，只输出 NONE 或 JSON 数组。",
        messages=[{"role": "user", "content":
                   "从对话中提取值得跨会话记住的事实（用户偏好/项目约定/教训），"
                   f"最多 3 条，输出 [{{\"content\": ...}}]，没有则输出 NONE:\n{transcript}"}],
        max_tokens=600)
    text = resp.content[0].text.strip()
    try:
        start, end = text.index("["), text.rindex("]") + 1
        new_items = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return
    if not new_items:
        return
    for it in new_items:
        if isinstance(it, dict) and it.get("content") \
                and it["content"] not in [m["content"] for m in MEMORY]:
            MEMORY.append({"content": it["content"], "updated_at": int(time.time())})
    save_memory(MEMORY)
    print(f"\033[34m[memory] 沉淀 {len(new_items)} 条记忆\033[0m")


if __name__ == "__main__":
    print("s15: Integrated Harness — 权限/hooks/todo/任务/后台/技能/记忆/subagent 全部在线")
    print(f"技能: {', '.join(SKILL_CATALOG) or '无'} | 记忆: {len(MEMORY)} 条\n")
    history = []
    try:
        while True:
            try:
                query = input("\033[36ms15 >> \033[0m")
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
            print()
    finally:
        remember_session(history)
