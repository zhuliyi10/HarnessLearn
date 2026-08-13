# s06 · Subagent — 子任务给全新的 messages[]

> *"过程留在子对话，结论回到主对话。"*

## 本章要解决的问题

探索性任务（"这个仓库里哪些地方用到了 X？""调研一下 Y 怎么实现"）会产生**大量中间输出**——grep 结果、文件内容、反复尝试。如果都在主对话里做，主 `messages[]` 很快被垃圾淹没，模型对"真正重要的事"的注意力被稀释。

解法：**把这类子任务委派给一个 subagent**。

```
    主 agent (messages[])               subagent (全新 messages[])
          |                                    |
          |  task("调研X")                      |
          +----------------------------------->|
          |                                    | 跑自己的 loop
          |                                    | 10 轮工具调用
          |                                    | 中间过程全留在这边
          |     只有一条精炼结论回来             |
          |<-----------------------------------+
          v
    主上下文只多了一条结论
```

## 两个关键点

### 1. subagent 有全新的 messages[]

它**看不到**主对话的历史。所以主 agent 委派任务时，必须把目标写得完整自足——这正是工具 schema 里强调的：

```
"description 要写清楚目标，subagent 看不到你的对话历史。"
```

这反而成了好事：强迫主 agent 把任务想清楚、表述清楚。

### 2. 只返回最终文本

subagent 跑完自己的 loop，`stop_reason != "tool_use"` 时，把它的最终文本作为**一条 `tool_result`** 返回给主 agent。中间几十轮的工具调用过程，主 agent 一个字都看不到。

```python
if response.stop_reason != "tool_use":
    final = "".join(b.text for b in response.content if b.type == "text")
    return final      # <-- 只有这个回到主对话
```

## 什么时候用 subagent？

| 适合委派 | 不适合委派 |
|---|---|
| 大范围搜索、调研 | 单步、明确的操作 |
| 会产生大量中间输出的任务 | 需要主对话上下文才能做的判断 |
| 可以独立完成、结论自足的工作 | 需要和用户来回确认的事 |

SYSTEM prompt 里特意写了"简单的单步操作自己做，不要什么都委派"——委派本身有成本（额外的 token 和延迟），滥用会适得其反。

## 一个保护：max_turns

subagent 是无人值守的，必须防止它无限循环。`run_subagent` 有 `max_turns` 上限，超了就返回一条让主 agent 知难而退的结果。

## 试一试

```sh
python s06_subagent/code.py
```

```
s06 >> 先派 subagent 调研当前目录里所有 .py 文件各是干什么的，然后你根据调研结果写一个 OVERVIEW.md
```

观察紫色的 `>> 派出 subagent` 和 `sub:` 前缀——那是隔离上下文里的活动；主对话只会收到结论。

## 动手练习

1. 给 subagent 加一个不同的工具池（比如只给只读工具，不给 `write_file`），做成"只读调研员"。
2. 让 `task` 支持并行：主 agent 一次传多个子任务，用线程并发跑多个 subagent。
3. 思考：subagent 的最终文本太长怎么办？可以接什么机制？（提示：s08）

## 下一章

[s07 Skill Loading](../s07_skill_loading/) — 用到时再加载，别全塞 prompt 里。
