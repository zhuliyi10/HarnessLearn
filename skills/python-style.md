---
name: python-style
description: 本项目 Python 代码风格与质量约定。编写或修改 Python 代码前建议先加载。
---

# Python 风格约定

## 基本

1. 遵循 PEP 8；行宽 100。
2. 函数必须有 type hints（参数 + 返回值）。
3. 公开函数必须有 docstring（一行摘要 + 必要时补参数说明）。

## 错误处理

1. 只捕获具体的异常类型，禁止裸 `except:`。
2. 面向用户的错误信息用中文，以"错误: "开头。
3. 工具类函数失败时返回错误字符串而不是抛异常（供 agent 循环消化）。

## 命名

- 模块/函数/变量：snake_case
- 类：PascalCase
- 常量：UPPER_SNAKE_CASE
- 私有成员前缀单下划线 `_`

## 测试

1. 测试文件命名 `test_<模块>.py`，与源码同目录或 tests/ 下。
2. 每个公开函数至少一个正常路径 + 一个边界用例。
3. 用 pytest，断言写清楚失败消息。
