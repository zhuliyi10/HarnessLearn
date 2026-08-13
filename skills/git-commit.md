---
name: git-commit
description: 撰写规范的 Conventional Commits 提交信息。当需要生成或检查 git commit message 时使用。
---

# Git Commit 规范

## 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

## type 取值

| type | 用途 |
|---|---|
| feat | 新功能 |
| fix | 修 bug |
| docs | 只改文档 |
| refactor | 重构（不改行为） |
| test | 加测试 |
| chore | 构建/工具链杂务 |
| perf | 性能优化 |

## 规则

1. subject 用祈使句，不超过 50 字符，结尾不加句号。
2. body 解释"为什么改"而不是"改了什么"（diff 已经说明了改了什么）。
3. 破坏性变更在 footer 写 `BREAKING CHANGE: ...`。
4. 关联 issue 写 `Closes #123`。

## 示例

```
feat(auth): 支持 OAuth2 登录

原有密码登录无法满足企业 SSO 需求，接入 OAuth2 authorization code 流程。

Closes #42
```
