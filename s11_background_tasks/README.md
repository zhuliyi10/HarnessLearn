# s11 · Background Tasks — 慢操作丢后台，agent 继续思考

> *"快命令同步跑，慢命令丢后台，完成了自动敲门。"*

## 本章要解决的问题

`npm install`、完整测试套件、Docker 构建——动辄几十秒。同步执行时，整个 agent loop 被一条命令卡住，模型什么也做不了。

解法：**后台线程 + 通知队列**。

```
    bash_background("npm install")
        |
        v
    harness 起线程跑命令，立刻返回 job_id   <-- 模型不阻塞，继续干别的
        |
        ... 模型并行推进其他工作 ...
        |
    命令跑完 -> 结果推入通知队列
        |
    下一轮 LLM 调用前 -> 通知注入为 user 消息
        v
    模型读到结果，决定下一步
```

## 两个工具 + 一处接线

| 部件 | 作用 |
|---|---|
| `bash_background` | 起线程跑命令，立即返回 `job_id` |
| `check_job` | 模型 impatient 时可主动查询 |
| `inject_notifications` | **关键接线**：每轮 LLM 调用前把完成通知注入对话 |

通知注入的时机有讲究——放在**每轮 API 调用之前**：

```python
def agent_loop(messages):
    while True:
        inject_notifications(messages)   # <-- 先投递后台结果
        response = client.messages.create(...)
```

这样模型在每次"睁眼"时都能第一时间看到后台进展，不需要轮询。通知以 `[harness 通知]` 开头的 user 消息形式注入，模型能自然理解并据此决策。

## 线程安全要点

- `jobs` 字典的读写加锁（`threading.Lock`），后台线程写、主线程读；
- 通知队列用线程安全的 `queue.Queue`，`drain_notifications` 用 `get_nowait` 一次取空；
- 后台线程是 daemon 的，进程退出不会被卡住；
- 后台命令也有超时（600s），防止僵尸任务。

## 试一试

```sh
python s11_background_tasks/code.py
```

```
s11 >> 后台运行 sleep 5 && echo '构建完成'，等待期间帮我写一个 notes.md 记录今天的工作，然后看看后台任务结果
```

观察模型：启动后台任务 → 先写文件 → 收到紫色 `[bg] 已注入 1 条完成通知` → 处理结果。

## 动手练习

1. 让 `check_job` 支持 `list`：列出所有后台任务的状态。
2. 给通知加优先级：失败的任务用更显眼的格式注入，确保模型不会忽略错误。
3. 思考：如果后台任务在用户还没输入下一句话时就完成了，通知何时才能送达？（提示：REPL 在等 input，这是本章实现的局限，s12 的调度器提供了一种解法）

## 下一章

[s12 Cron Scheduler](../s12_cron_scheduler/) — 定时触发，不需要人推。
