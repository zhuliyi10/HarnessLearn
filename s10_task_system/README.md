# s10 · Task System — 大目标拆成小任务，排好序，持久化

> *"todo 活在内存里，任务活在磁盘上。"*

## 本章要解决的问题

s05 的 todo 有两个硬伤：

1. **进程一退出就没了**——长任务跨天执行就抓瞎；
2. **没有依赖概念**——无法表达"先建表再写接口"这种顺序约束。

本章把"计划"升级为真正的**任务系统**：带依赖图、落盘持久化、有就绪判定。它是后面所有"长期运行"机制（后台任务 s11、调度 s12、团队协作 s13）的地基。

## 数据模型

```python
TaskRecord = {
    "id":         "task-a1b2c3d4",
    "title":      "写接口层",
    "status":     "pending | in_progress | completed | failed",
    "blocked_by": ["task-xxxx"],     # 依赖的任务 id 列表
    "created_at": 1712345678,
    "updated_at": 1712345700,
}
```

**每个任务一个 JSON 文件**（`tasks/<id>.json`）。为什么不用一个大 JSON？因为多写者（s13 的多个队友 agent）并发写同一文件会互相覆盖；一任务一文件把冲突面缩到最小。

## 就绪判定：依赖图的心脏

```python
def is_ready(self, task):
    """就绪 = pending 且所有依赖都 completed。"""
    if task["status"] != "pending":
        return False
    for dep_id in task["blocked_by"]:
        dep = self.get(dep_id)
        if dep is None or dep["status"] != "completed":
            return False
    return True
```

`task_update` 想开工（改成 `in_progress`）时会先过这道闸：

```
错误: task-xxx 尚有依赖未完成或不是 pending，不能开工。
```

这个校验在 **harness 层强制执行**，而不是靠 prompt 请求模型遵守——规则写在代码里才算规则。

## 三个任务工具

| 工具 | 作用 |
|---|---|
| `task_create` | 创建任务，可声明 `blocked_by` |
| `task_list` | 查看全部任务、状态、依赖、就绪标记 |
| `task_update` | 推进状态（带就绪校验） |

每次变更都把**完整任务列表**渲染回传给模型，让它随时看清全局——和 s05 的 todo 同一个思路：外部工作记忆。

## 试一试

```sh
python s10_task_system/code.py
```

```
s10 >> 做一个博客后端：先设计数据模型，再实现 API，最后写测试
```

观察模型建出带依赖的任务链，然后按 ready 顺序推进。中途 Ctrl+C 退出再重启，任务还在（SYSTEM 里的任务状态直接来自磁盘）。

## 动手练习

1. 加一个 `task_delete` 工具，注意：删除前要先解除其他任务对它的依赖。
2. 给任务加 `notes` 字段，让模型在完成任务时记录关键产出（文件路径、结论），供后续任务和 s09 记忆使用。
3. 思考：如果两个任务互为依赖（环），当前代码会发生什么？该如何在 `task_create` 时检测？

## 下一章

[s11 Background Tasks](../s11_background_tasks/) — 慢操作丢后台，agent 继续思考。
