# s02 · Tool Use — 加一个工具，只加一个 handler

> *"循环不用动，新工具注册进 dispatch map 就行。"*

## 本章要解决的问题

s01 里只有一个 bash 工具，调用逻辑直接写死在循环里。一旦要加"读文件""写文件""列目录"，难道往循环里堆 `if name == ... elif ...`？

不。正确的做法是**数据驱动**：用一个字典把"工具名 → 处理函数"映射起来，循环只负责查表分发。

```python
TOOL_HANDLERS = {
    "bash":       run_bash,
    "read_file":  read_file,
    "write_file": write_file,
    "list_files": list_files,
}

# 循环里的分发，永远只有这一行
output = TOOL_HANDLERS.get(block.name)(**block.input)
```

从此，**加一个新工具 = 写一个函数 + 加一段 schema + 注册一行**，主循环零改动。

## 一个工具的两半

每个工具在 harness 里有两份东西，缺一不可：

| 部分 | 给谁看 | 作用 |
|---|---|---|
| `TOOLS` 里的 JSON Schema | **模型** | 告诉模型这个工具叫什么、参数是什么、什么时候该用 |
| `TOOL_HANDLERS` 里的函数 | **harness 自己** | 真正执行 |

两份要对齐：schema 里的字段名必须和函数签名一致，因为分发用的是 `handler(**block.input)` —— 模型给的参数直接展开成关键字参数。

## 三个工程细节

### 1. 工具描述是给模型看的说明书

`description` 不是写给人看的注释，而是模型的**使用手册**。写得越明确，模型用得越准。对比：

```
差:  "读取文件"
好:  "读取文件内容，返回带行号的文本。可指定 start_line/end_line 只读一段。"
```

### 2. 错误不要抛异常，要变成 tool_result 返回

模型给的参数可能错、文件可能不存在、命令可能失败。**不要让异常炸掉循环**，而是捕获后作为 `tool_result` 回传——模型读到错误信息，往往能自己换条路再来。

```python
try:
    output = handler(**block.input)
except (TypeError, KeyError, OSError) as e:
    output = f"错误: 工具执行失败 - {e}"
```

### 3. 未知工具名也要优雅处理

```python
handler = TOOL_HANDLERS.get(block.name)
if handler is None:
    output = f"错误: 不存在的工具 {block.name}"
```

## 试一试

```sh
python s02_tool_use/code.py
```

```
s02 >> 新建 utils.py 写一个计算斐波那契的函数，然后读回来检查一遍
```

观察输出里 `[bash]` `[write_file]` `[read_file]` 的前缀——模型在不同的工具间自主切换。

## 动手练习

1. 加一个 `grep` 工具：参数 `pattern` 和 `path`，返回匹配行。体会"循环一行不用改"。
2. 故意把 `write_file` 的 schema 里 `content` 字段名改成 `text`，但函数签名不改，观察模型调用时发生什么。
3. 思考：如果一次 response 里有多个 `tool_use` 块（并行调用），当前代码是怎么处理的？顺序执行安全吗？

## 下一章

[s03 Permission](../s03_permission/) — 先划边界，再给自由。
