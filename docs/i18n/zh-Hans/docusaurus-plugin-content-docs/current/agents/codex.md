---
title: Codex
---

Codex 插件与 [Claude Code](./claude-code.md) 插件提供相同的核心功能：会话开始时加载简报，每次发送消息时加入相关记忆，并通过 MCP 提供记忆工具和配套技能（skill）。

## 前提条件

一个已启用 MCP 的 Ontyra 服务（参阅[快速开始](../quick-start.md)）、`curl`、`jq` 和 Codex CLI。

## 安装

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
codex plugin marketplace add .
codex plugin add ontyra@ontyra
```

开启一次新的 Codex 会话，然后：

1. 运行 `/hooks`，并**信任**三个 Ontyra 钩子。Codex 会跳过你尚未审阅的钩子，脚本变化后会再次询问。
2. 运行 `/mcp`：列表中应当有 `ontyra`。

## 验证安装

问“你对这个项目记得什么？”。也可以先发送一条消息，再检查 Ontyra：如果已经保存了规则，运行 `curl -s localhost:8787/v1/briefing -X POST -H 'content-type: application/json' -d '{"context":{"uris":[],"signals":[]}}'` 应能看到相关内容。智能体提交的提案会出现在运维控制台的“审核”页。

## 连接需要令牌的服务

启动 Codex 之前先导出 `ONTYRA_TOKEN`（智能体凭据，见[认证](../guides/authentication.md)）。钩子会发送它，MCP 服务条目则通过 `bearer_token_env_var` 读取它。如果没有设置，Codex 会不带凭据地连接。

## 使用其他服务地址

编辑插件 `.mcp.json` 中的 `url`，并为钩子导出 `ONTYRA_URL`。

## 移除

```bash
codex plugin remove ontyra@ontyra
codex plugin marketplace remove ontyra
```
