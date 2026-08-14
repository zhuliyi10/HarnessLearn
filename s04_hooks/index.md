# s04 · Hooks — 挂在循环上，不写进循环里

> *"横切关注点注册成 hook，主循环保持干净。"*

## 本章要解决的问题

s03 把权限判断写进了执行函数。但很快你会发现需求没完没了：

- 想要**审计日志**，记录 agent 每次干了什么；
- 想要**耗时统计**；
- 想要**输出截断**；
- 想要**敏感信息过滤**。

这些都是**横切关注点**（cross-cutting concerns），如果全塞进循环或工具实现，代码会变成意大利面。

正确做法：在工具执行的**前后留两个标准插口**，任何横切逻辑都注册成 hook 挂上去。循环和工具实现完全不用改。

```
    tool_use
       |
       v
    PreToolUse hooks   --> 可拦截(block) / 可改写参数
       |
       v
    执行工具
       |
       v
    PostToolUse hooks  --> 可改写输出 / 记录日志 / 统计耗时
       |
       v
    tool_result
```

## hook 的约定

两种 hook，两种能力：

| Hook | 签名 | 能做什么 |
|---|---|---|
| `PreToolUse` | `(tool_name, tool_input) -> (action, payload)` | 拦截、改写参数、放行 |
| `PostToolUse` | `(tool_name, tool_input, output) -> output` | 改写输出、旁路记录 |

注册用装饰器，一行搞定：

```python
@pre_hook
def hook_deny_dangerous(tool_name, tool_input):
    if "sudo" in 参数串:
        return "block", "hook 拦截: 检测到危险操作"
    return "continue", tool_input
```

## 五种典型用法

代码里给了 5 个示例 hook，覆盖最常见场景：

1. **拦截** — `hook_deny_dangerous` 挡住危险命令。
2. **审计日志** — `hook_log_request` 把每次调用写进 `hook_audit.jsonl`。
3. **改写参数** — `hook_limit_output_tools` 自动给 bash 追加输出限制。
4. **耗时统计** — 在执行器里记录 `elapsed`。
5. **标记错误** — `hook_flag_errors` 给失败输出加 `[工具执行失败]` 前缀，帮模型更快识别。

## hook vs permission：什么时候用哪个？

- **Permission（s03）**：策略性的"能不能做"，需要用户参与决策，规则相对固定。
- **Hook（s04）**：机制性的"做的过程中顺带干点事"，无需用户介入，随装随卸。

两者不冲突，s15 集成时它们会同时挂在执行管线上。

## 试一试

> **安全提示**：代码会执行模型生成的 shell 命令。建议在一个临时测试目录中运行。

```sh
python s04_hooks/code.py
```

试试这些 prompt：

1. `列出当前目录的文件，然后读一下 package.json`
2. `读一下不存在的 nope.txt`
3. `刚才这些操作都被记录了吗？看看审计日志`

**观察重点**：主循环一行没加，所有横切逻辑都挂在 `PreToolUse` / `PostToolUse` 两个插口上。

- Prompt 1：每次调用后都有灰色的 `⏱ 耗时` 日志；`hook_limit_output_tools` 正在悄悄给 bash 命令追加 `| head -c 50000`——hook 可以改写参数。跑完后 `cat hook_audit.jsonl`，能看到完整的审计记录。
- Prompt 2：失败输出开头会多出 `[工具执行失败]` 标记——这是 `hook_flag_errors` 后置改写的结果，帮模型更快识别失败。
- Prompt 3：模型自己去 `cat hook_audit.jsonl`——审计日志本身就是普通文件，harness 的行为事后完全可追溯。

## 动手练习

1. 写一个 PostToolUse hook：如果工具输出超过 10000 字符，截断并附加 `...(已截断)`。
2. 写一个 PreToolUse hook：把 bash 命令里出现的 `rm` 自动改写成 `echo "[模拟] rm"`（dry-run 模式）。
3. 思考：PreToolUse hook 的执行顺序重要吗？如果一个 hook 改写了参数，后面的 hook 看到的是改写前还是改写后的？

## 下一章

[s05 TodoWrite](../s05_todo_write/) — 没有计划的 agent 走哪算哪。
