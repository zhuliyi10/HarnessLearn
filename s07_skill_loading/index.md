# s07 · Skill Loading — 用到时再加载，别全塞 prompt 里

> *"知识按需加载，目录常驻，全文懒加载。"*

## 本章要解决的问题

agent 要懂领域知识：代码规范、提交流程、报表模板、API 约定……这些知识会越积越多。最朴素的做法是把它们全塞进 SYSTEM prompt——但这有三个致命问题：

1. **撑爆上下文**：几十个技能全文就是几万 token；
2. **稀释注意力**：不相关的知识混在 prompt 里，干扰模型判断；
3. **每轮都付费**：SYSTEM 每轮请求都完整发送，浪费 token。

## 解法：两级加载

```
    skills/*.md                       SYSTEM prompt
    +---------------------+           只注入"目录":
    | --- frontmatter --- |           - git-commit: 撰写规范的提交信息
    | name / description  |  扫描     - python-style: 代码风格约定
    | --- 正文(全文) ---   | ------->  - csv-report: CSV 报表流程
    +---------------------+
                                      模型判断相关时:
                                      load_skill("git-commit")
                                            ↓ 全文作为 tool_result 注入
```

- **目录层**：启动时扫描 `skills/` 目录，只解析每个文件的 frontmatter（`name` + `description`），拼成一段简短目录放进 SYSTEM。成本极低，模型随时知道"有什么可用"。
- **全文层**：模型判断某技能与当前任务相关，调用 `load_skill(name)`，全文才作为 `tool_result` 进入上下文。

模型自己决定加载哪个技能——这依然是"模型决策、harness 执行"的原则。

## 技能文件格式

每个技能是一个 Markdown 文件，头部带 YAML frontmatter：

```markdown
---
name: git-commit
description: 撰写规范的 Conventional Commits 提交信息。当需要生成或检查 git commit message 时使用。
---

# Git Commit 规范
（正文 = 技能的完整操作说明）
```

**`description` 是技能的"钩子"**——模型靠它判断"这个技能和当前任务有关吗"。写得越具体，触发越准。项目自带三个示例技能：`git-commit`、`python-style`、`csv-report`。

## 代码要点

`SkillLoader._scan` 只解析 frontmatter 不读正文，`load` 时才真正读全文——懒加载。

`load` 找不到技能时，返回**可用技能列表**作为错误信息，帮模型纠正拼写：

```python
known = ", ".join(self.catalog) or "无"
return f"错误: 技能 '{name}' 不存在。可用技能: {known}"
```

## 试一试

```sh
python s07_skill_loading/code.py
```

```
s07 >> 帮我写一条 commit message：我修好了登录页面点击无反应的 bug
```

观察蓝色的 `[skill] 已加载技能: git-commit`——模型先加载技能，再按规范产出提交信息。再问一个和技能无关的问题，观察它不加载任何技能。

## 动手练习

1. 在 `skills/` 下新建一个你自己的技能文件，重启后验证它出现在目录里并能被加载。
2. 给技能加 `version` 字段，并在 `load_skill` 返回的开头附加版本信息。
3. 思考：如果技能全文也很长（几千行），该怎么进一步处理？（提示：目录可以再分层，或者结合 s06 委派给 subagent 消化）

## 下一章

[s08 Context Compact](../s08_context_compact/) — 上下文总会满，要有办法腾地方。
