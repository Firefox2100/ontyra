---
title: Claude Code
---

Claude Code 插件会为每次会话提供三项功能：

- 会话开始时（以及恢复或压缩之后）的**简报**：你所在位置适用的规则；
- 每次发送消息时，自动把相关记忆加入上下文；
- **记忆工具**（`recall`、`check`、`briefing`、`ask`、`get`、`remember`、`concepts`、`propose_ontology_change`），以及一个告诉智能体何时使用它们的技能（skill）。

如果 Ontyra 服务不可用或响应过慢，会话仍会继续，只是这一次不会加入记忆。

## 前提条件

一个已启用 MCP 的 Ontyra 服务（参阅[快速开始](../quick-start.md)），以及 `curl` 和 `jq`。

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

如果 Ontyra 运行在其他地址，请在启动 Claude Code 前执行 `export ONTYRA_URL=http://host:port`。

## 验证安装

在任意项目中开始会话，然后问“你对这个项目记得什么？”。智能体会调用 `recall`，或直接根据会话简报回答。插件工具名称以 `mcp__plugin_ontyra_ontyra__` 开头。

## 让智能体提议记忆

智能体会主动提出值得长期保存的内容，不需要你逐次提醒。当消息看起来像规则、偏好、纠正或决策时，插件会在上下文中加入一行提示；此外还会按 `ONTYRA_CAPTURE_EVERY` 的间隔定期提醒，默认每 5 条消息一次。这个过程不会增加对话轮次或额外调用模型。智能体可以在当前回复中提交提案，也可以不处理。提醒还会要求智能体对明显有用或误导性的已调取记忆提交 `feedback`（有帮助、错误、过时或无关）。记忆旁的追踪 id 让 Ontyra 能把反馈关联到本次调取，详见[从反馈中学习](../guides/feedback-learning.md)。设置 `ONTYRA_CAPTURE=0` 可关闭此功能。除非策略设为 `auto_commit`，否则提案必须经过你的批准才会保存，详见[审核与策略](../guides/review.md)。

## 设置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `ONTYRA_URL` | `http://127.0.0.1:8787` | Ontyra 服务地址 |
| `ONTYRA_TOKEN` | 未设置 | Ontyra 要求认证时发送的令牌 |
| `ONTYRA_CAPTURE` | `1` | 在注入的上下文中提醒记录已经定下来的内容（`0` 关闭） |
| `ONTYRA_CAPTURE_EVERY` | `5` | 两次定期提醒之间间隔的提示数（读起来像规则的提示会立即触发） |
| `ONTYRA_LOG_EPISODES` | `1` | 把提示和回复记录为会话记录（记忆的 `quote` 证据就是与之匹配的；见[证据](../guides/evidence.md)） |
| `ONTYRA_BUDGET_TOKENS` | 服务端默认值 | 注入上下文的大小上限 |
| `ONTYRA_PROFILE` | `compact` | `compact` 把每条记忆渲染为带 id 的一行（指令显示为简短规则；`get` 给出完整文本和理由）；`rich` 另加完整文本、锚点和分数 |
