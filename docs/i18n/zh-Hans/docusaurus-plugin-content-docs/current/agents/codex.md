---
title: Codex
---

Codex 插件的功能与 [Claude Code](./claude-code.md) 插件相同：会话开始时的简报、每条提示的召回、通过 MCP 提供的记忆工具，外加技能（skill）。

## 前提条件

一个启用了 MCP 的运行中的守护进程（[快速开始](../quick-start.md)）、`curl` 和 `jq`，以及 Codex CLI。

## 安装

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
codex plugin marketplace add .
codex plugin add ontyra@ontyra
```

开启一次新的 Codex 会话，然后：

1. 运行 `/hooks`，并**信任**三个 Ontyra 钩子。Codex 会跳过你尚未审阅的钩子，脚本变化后会再次询问。
2. 运行 `/mcp`：列表中应当有 `ontyra`。

## 检查是否工作

问“你对这个项目记得什么？”。或者发一条提示，然后查看守护进程：在已存有规则的情况下，运行 `curl -s localhost:8787/v1/briefing -X POST -H 'content-type: application/json' -d '{"context":{"uris":[],"signals":[]}}'`。智能体提出的任何提案都会出现在运维控制台的“审核”页。

## 要求令牌的守护进程

启动 Codex 之前先导出 `ONTYRA_TOKEN`（智能体凭据，见[认证](../guides/authentication.md)）。钩子会发送它，MCP 服务条目则通过 `bearer_token_env_var` 读取它。如果没有设置，Codex 会不带凭据地连接。

## 使用其他守护进程地址

编辑插件 `.mcp.json` 中的 `url`，并为钩子导出 `ONTYRA_URL`。

## 移除

```bash
codex plugin remove ontyra@ontyra
codex plugin marketplace remove ontyra
```
