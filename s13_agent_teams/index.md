# s13 · Agent Teams — 一个 Agent 顾不过来，就让队友分工协作

> *"共享看板 + 原子认领 + 独立工作区 = 一个能并行的 agent 团队。"*

## 本章要解决的问题

单 agent 是串行的：一次只能干一件事。把大任务拆给多个 agent 并行，速度成倍提升——但立刻引出三个新问题：

1. **任务怎么分？** 谁干什么，干完了别人怎么知道？
2. **怎么防止撞车？** 两个队友同时看中一个任务怎么办？
3. **怎么防止互踩？** 两个队友同时改同一个文件怎么办？

## 架构总览

```
    用户
      |
    主 agent（协调者）: 拆任务图 -> 启动队友 -> 等收工 -> 汇总
      |
    +------------+-------------+
    |            |             |
 worker-a     worker-b      worker-c        <- 每人一个线程
 workspace-a  workspace-b   workspace-c     <- 每人一个目录
    \            |            /
     +-----------+-----------+
     |   tasks/ 共享任务池     |   <-- 原子认领 (O_CREAT|O_EXCL)
     +-----------------------+
```

四个机制各解决一个问题：

### 1. 共享任务池（复用 s10）

任务文件就是团队的**共享看板**。协调者只负责"拆任务、派人"，不指挥具体动作；队友自己来看板上找活干。去中心化领取比中心化指派健壮得多——某个队友卡住不影响其他人。

### 2. 原子认领：一行系统调用解决撞车

```python
def try_claim(task_id, worker):
    lock_path = tasks_dir / f"{task_id}.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False    # 别人先抢到了
    ...                 # 抢到了，标记 in_progress + owner
```

`O_CREAT | O_EXCL` 保证"文件已存在就失败"是**原子**的——多线程、多进程并发抢同一个任务，也只有一个能成功。这比先读后写的"检查再行动"可靠无数倍。

### 3. 任务绑定的工作区：物理隔离防互踩

每个队友的 bash/读写**全部限制在自己的 `workspace` 目录**：

```python
full = os.path.abspath(os.path.join(workspace, path))
if not full.startswith(workspace):
    return "错误: 禁止访问工作目录之外的路径"
```

bash 的 `cwd` 也设成工作区。两个队友永远碰不到同一个文件。

### 4. 消息邮箱：队友间的异步通信

`send_message(to, text)` 把消息追加到收件人的 `inbox.jsonl`，收件人下一轮开工时 `drain_inbox` 读空。这是**异步邮箱**模式：发件人不等回复，收件人按自己的节奏消费——比同步 RPC 更适合 agent 团队。

## 协调者的克制

注意协调者的工具集：`task_create`、`spawn_team`、`wait_for_team`、`team_activity`……**没有任何"命令某个队友执行某条命令"的工具**。协调者只做三件事：拆解、派人、汇总。具体怎么干，是队友和模型自己的事。

这正是 harness 工程的立场：**代码定义协作的规则和设施，不定义协作的过程。**

## 试一试

> **安全提示**：代码会执行模型生成的 shell 命令。建议在一个临时测试目录中运行。

```sh
python s13_agent_teams/code.py
```

试试这些 prompt：

1. `派 3 个队友并行工作：一个写日期工具模块，一个写字符串工具模块，一个写数学工具模块，各自带测试`
2. `任务池现在什么状态？谁在干什么？`
3. （等大家收工后）`把三个模块汇总成一个 __init__.py 统一导出`

**观察重点**：`[worker-x] 认领...` 的日志交错出现——多个 agent 在同一个任务池里真正并行。

- Prompt 1：协调者 `spawn_team` 开出 3 个 worker，每个 worker 有自己的上下文，从共享任务池里认领任务干活——任务池就是团队协作的"共享内存"。
- Prompt 2：`task_list` / `team_activity` 返回的是全局视角——只有协调者（你对话的这个 agent）能统览全局，worker 只看到自己的任务。
- Prompt 3：最后协调者汇总各队友的产出。注意 worker 之间互不通信，信息都通过任务池流转——想想这和 s06 的 subagent 有什么区别。

## 动手练习

1. 给队友加"求助"机制：worker 遇到困难时给 lead 发消息，lead 的下一轮能看到并回复。
2. 把工作区从"目录"升级为 git worktree（`git worktree add`），让队友们的产出最终能合并进同一个仓库。
3. 思考：`wait_for_team` 用 5 秒轮询判断"收工"。如果队友完成任务和认领新任务之间有时间差，会不会误判收工？怎么改？

## 下一章

[s14 MCP Plugin](../s14_mcp_plugin/) — 能力不够？插上 MCP。
