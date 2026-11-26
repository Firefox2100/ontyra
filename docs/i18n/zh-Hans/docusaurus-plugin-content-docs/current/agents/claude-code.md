---
title: Claude Code
---

Claude Code 插件为每次会话增加三样东西：

- 会话开始时（以及恢复或压缩之后）的**简报**：你所在位置适用的规则；
- 每条提示的召回：与你的消息相关的记忆，作为上下文注入；
- **记忆工具**（`recall`、`check`、`briefing`、`ask`、`get`、`remember`、`concepts`、`propose_ontology_change`），以及一个告诉智能体何时使用它们的技能（skill）。

如果守护进程宕机或响应缓慢，会话会在没有记忆的情况下继续（钩子在失败时放行）。

## 前提条件

一个启用了 MCP 的运行中的守护进程（[快速开始](../quick-start.md)），以及 `curl` 和 `jq`。

## 安装

在本仓库的克隆目录中，于 Claude Code 内执行：

```text
/plugin marketplace add /path/to/ontyra/integrations
/plugin install ontyra@ontyra
/reload-plugins
```

或者不安装，仅在一次会话中试用：

```bash
claude --plugin-dir /path/to/ontyra/integrations/claude-code
```

使用其他守护进程地址：启动 Claude Code 之前先 `export ONTYRA_URL=http://host:port`。

## 检查是否工作

在某个项目里开始会话，问“你对这个项目记得什么？”。智能体会调用 `recall`，或者直接根据注入的简报回答。插件工具显示为 `mcp__plugin_ontyra_ontyra__*`。

## 让智能体提议记忆

智能体无需你要求，就会提议值得长期保留的内容。当一条提示读起来像规则、偏好、纠正或决策时，以及每隔几条提示（`ONTYRA_CAPTURE_EVERY`，默认 5），会在随提示发送的上下文中追加一行提醒。它不会多花一轮对话，也不会多调用一次模型：智能体在同一轮里写出提案，或什么都不做。同一条提醒还会请智能体对明显有帮助或造成误导的已召回记忆给出 `feedback` 评价（有帮助、错误、过时、无关）；随注入记忆一同显示的召回追踪 id，让守护进程能够据此学习（见[从反馈中学习](../guides/feedback-learning.md)）。用 `ONTYRA_CAPTURE=0` 可关闭。除非你选择了 `auto_commit`，否则没有你的批准，什么都不会被存储；见[审核与策略](../guides/review.md)。

## 设置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `ONTYRA_URL` | `http://127.0.0.1:8787` | 守护进程所在地址 |
| `ONTYRA_TOKEN` | 未设置 | 守护进程要求令牌时所发送的令牌 |
| `ONTYRA_CAPTURE` | `1` | 在注入的上下文中提醒记录已经定下来的内容（`0` 关闭） |
| `ONTYRA_CAPTURE_EVERY` | `5` | 两次定期提醒之间间隔的提示数（读起来像规则的提示会立即触发） |
| `ONTYRA_LOG_EPISODES` | `1` | 把提示和回复记录为会话记录（记忆的 `quote` 证据就是与之匹配的；见[证据](../guides/evidence.md)） |
| `ONTYRA_BUDGET_TOKENS` | 守护进程默认值 | 注入上下文的大小上限 |
| `ONTYRA_PROFILE` | `compact` | `compact` 把每条记忆渲染为带 id 的一行（指令显示为简短规则；`get` 给出完整文本和理由）；`rich` 另加完整文本、锚点和分数 |
