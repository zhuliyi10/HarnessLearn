#!/usr/bin/env python3
"""
s07_skill_loading - Skill Loading：用到时再加载，别全塞 prompt 里

问题：领域知识（代码规范、提交流程、报表模板……）越积越多，
全塞进 SYSTEM prompt 会撑爆上下文、稀释注意力。

解法：两级加载——
    1. 启动时只把技能"目录"（name + 一句话 description）注入 SYSTEM
    2. 模型判断某个技能相关时，调用 load_skill(name) 拉取全文

    skills/*.md                       SYSTEM prompt
    +---------------------+           只有目录:
    | --- frontmatter --- |           - git-commit: 撰写规范的提交信息
    | name: git-commit    |  扫描     - python-style: 代码风格约定
    | description: ...    | ------->  - csv-report: CSV 报表流程
    | --- 正文(全文) ---   |
    +---------------------+           用到时:
          |                           load_skill("git-commit")
          +----------------------------> 全文注入为 tool_result

技能文件格式: Markdown + YAML frontmatter（name/description 必填）。

运行:
    python s07_skill_loading/code.py
"""

import os
import re
import subprocess

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

# ============================================================
# 第一部分：SkillLoader —— 扫描 + 按需读取
# ============================================================

class SkillLoader:
    """从 skills 目录加载技能。启动时只解析 frontmatter 建目录。"""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.catalog = {}   # name -> {"description": ..., "path": ...}
        self._scan()

    def _scan(self):
        """扫描目录，只读 frontmatter，不读正文（省启动开销）。"""
        if not os.path.isdir(self.skills_dir):
            return
        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(self.skills_dir, fname)
            meta = self._parse_frontmatter(path)
            if meta and "name" in meta:
                self.catalog[meta["name"]] = {
                    "description": meta.get("description", "(无描述)"),
                    "path": path,
                }

    @staticmethod
    def _parse_frontmatter(path: str) -> dict | None:
        """解析文件开头的 --- yaml --- 块。"""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return None
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            return None
        try:
            return yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None

    def catalog_text(self) -> str:
        """生成注入 SYSTEM 的技能目录（只有名字和一句话）。"""
        if not self.catalog:
            return "(没有可用技能)"
        lines = [f"- {name}: {info['description']}"
                 for name, info in self.catalog.items()]
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """按需读取技能全文。"""
        info = self.catalog.get(name)
        if info is None:
            known = ", ".join(self.catalog) or "无"
            return f"错误: 技能 '{name}' 不存在。可用技能: {known}"
        with open(info["path"], encoding="utf-8") as f:
            return f.read()


LOADER = SkillLoader(SKILLS_DIR)

SYSTEM = f"""你是一个位于 {os.getcwd()} 的编程 agent。

可用技能（只列了目录，需要时用 load_skill 加载全文）:
{LOADER.catalog_text()}

工作纪律：任务与某个技能相关时，先 load_skill 拿到全文再动手，
严格按技能里的规范执行。不相关的技能不要加载。"""

# ============================================================
# 第二部分：基础工具 + load_skill 工具
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


def load_skill(name: str) -> str:
    """按需加载技能全文。"""
    content = LOADER.load(name)
    print(f"\033[34m[skill] 已加载技能: {name} ({len(content)} 字符)\033[0m")
    return content


TOOLS = [
    {"name": "load_skill",
     "description": "按名字加载技能的完整内容。任务与某技能相关时，动手前先加载它。",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string",
                                              "description": "技能名，见 SYSTEM 中的技能目录"}},
                      "required": ["name"]}},
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

TOOL_HANDLERS = {
    "load_skill": load_skill,
    "bash": run_bash,
    "read_file": read_file,
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
            if block.name != "load_skill":
                print(str(output)[:200])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print(f"s07: Skill Loading — 技能目录: {SKILLS_DIR}")
    print(f"已发现技能: {', '.join(LOADER.catalog) or '无'}")
    print("试试 '帮我写一条 commit message' 观察技能按需加载。q 退出\n")
    history = []
    while True:
        try:
            query = input("\033[36ms07 >> \033[0m")
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
