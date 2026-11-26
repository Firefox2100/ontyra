---
title: 其他客户端（MCP 与 REST）
---

任何支持基于可流式 HTTP 的 MCP，或普通 HTTP 的客户端，都可以使用 Ontyra。

## MCP

端点：`http://127.0.0.1:8787/mcp/`（用 `ONTYRA_MCP__ENABLED=true` 启用）。

当守护进程要求时，使用 `Authorization: Bearer <token>` 认证（[认证](../guides/authentication.md)）。没有账户时，调用方通过请求头自报身份（这只是标签，不是凭据；启用账户后会被忽略）：

| 请求头 | 含义 |
|---|---|
| `X-Ontyra-Actor` | 一个名字，例如 `my-agent` |
| `X-Ontyra-Actor-Kind` | `agent`（默认）或 `human` |
| `X-Ontyra-Harness` | 宿主程序 |

用 `ONTYRA_MCP__PROFILE` 选择两种工具档之一：

- `lite`（默认）：`recall`、`check`、`briefing`、`ask`、`consult`、`get`、`remember`、`feedback`、`check_source`、`concepts`、`propose_ontology_change`、`pending_proposals` 和 `amend`。
- `full`：增加搜索与浏览、重复检测、维护任务、反馈审核、本体历史，以及面向人类的审核工具。当前源码树的确切清单见生成文件 `integrations/contract/mcp-tools-full.json`。

请把 `ontyra-memory` 技能（仓库中的 `integrations/skills/ontyra-memory/SKILL.md`；`integrations/skills/install.sh` 会把它复制到 `~/.agents/skills`）交给智能体，让它知道何时召回、如何限定所写内容的适用范围，以及在产生副作用之前先检查。

## REST

REST API 还提供 `consult`、来源检查、本体管理、维护任务、反馈审核、重排器管理和账户管理。运行守护进程后打开 `/docs` 可看到交互式参考，或用 `/openapi.json` 获取模式。仓库在 `integrations/contract/openapi.json` 保留了一份生成的副本，并用测试检查它是否漂移。

写入的状态码：`201` 已提交，`202` 搁置待审核，`422` 被拒绝（响应体会列出原因）。
