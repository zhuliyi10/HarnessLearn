# s16 · Workflow Runtime — 编排形状固定时，就把它写进代码

> *"编排归代码，执行归模型。"*

## 本章要解决的问题

s01–s15 的哲学是"让模型自由决策"。但有些流程的**形状是确定的**：

```
设计 -> 实现 -> 测试 -> 报告
```

每次都一样。这种时候如果还让模型每轮重新决策"下一步该干嘛"，既浪费 token，又引入不必要的随机性（它可能跳步、跑偏）。

**判断标准**：编排形状会随任务变化 → 交给模型（agent loop）；编排形状固定 → 写进代码（workflow）。两者不是对立，而是分工。

## 架构

```
    Workflow 定义（代码，固定）          每一步内部（模型，自由）
    +------------------------+         +--------------------+
    | step1 design           | ------> | 受限 agent loop     |
    | step2 implement        | ------> | 工具: bash/read/write
    | step3 test             | ------> | 自由发挥最多 20 轮  |
    | step4 report           | ------> | 输出本步产出        |
    +------------------------+         +--------------------+
              |
    journal.jsonl（每步完成即落盘）
```

关键点：

1. **步骤顺序是代码写死的**——模型无权改变；
2. **每步内部仍然是自由 agent loop**——模型自由决定怎么完成该步；
3. **前一步的产出是后一步的输入**——`{prev_outputs}` 模板注入。

## journal：断点续跑

每完成一步立即写一行 JSONL：

```python
journal_write(run_id, step_name, output)   # 完成一步立刻落盘
```

中断（断网、Ctrl+C、崩溃）后重启：

```sh
python s16_workflow_runtime/code.py resume
```

`journal_read` 恢复已完成步骤的产出，主循环里直接跳过：

```python
if step_name in progress:
    print(f"✓ {step_name}（journal 已有，跳过）")
    continue
```

**journal 的本质：把"时间线上的进度"变成"磁盘上的事实"。** 同样的思想在 s10（任务落盘）和 s09（记忆落盘）里反复出现——长期运行的系统，状态必须离开内存。

## 试一试

```sh
python s16_workflow_runtime/code.py run "创建一个 slugify 函数库并测试"
```

观察四个阶段依次推进。跑到一半 Ctrl+C 中断，然后：

```sh
python s16_workflow_runtime/code.py resume
```

已完成的步骤会被跳过，从中断处继续。

## workflow vs agent loop：怎么选？

| 场景 | 选择 |
|---|---|
| 流程固定、重复执行（CI、报告生成、例行巡检） | workflow |
| 流程未知、需要根据中间结果调整路线 | agent loop |
| 固定流程里某些步骤内部很复杂 | workflow 套 agent loop（本章方案） |

## 动手练习

1. 把 `WORKFLOW_STEPS` 抽到 YAML 文件，让非程序员也能定义新 workflow。
2. 给步骤加 `on_failure` 策略：某步失败时重试一次，再失败就终止并保留现场。
3. 思考：journal 只存了每步的输出摘要（截断到 5000 字符）。如果 implement 步骤的产出很长，test 步骤会不会信息不足？该怎么权衡？

## 下一章

[s17 Goal Loop](../s17_goal_loop/) — 目标决定循环什么时候真正结束。
