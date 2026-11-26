---
title: 其他客户端（MCP 与 REST）
---

任何支持 Streamable HTTP MCP 或普通 HTTP 的客户端都可以接入 Ontyra。

## MCP

端点：`http://127.0.0.1:8787/mcp/`（用 `ONTYRA_MCP__ENABLED=true` 启用）。

如果 Ontyra 要求认证，请发送 `Authorization: Bearer <token>`，详见[认证](../guides/authentication.md)。未启用账户时，调用方通过请求头声明身份。这些值只是标签，不是凭据；启用账户后会被忽略。

| 请求头 | 含义 |
|---|---|
| `X-Ontyra-Actor` | 一个名字，例如 `my-agent` |
| `X-Ontyra-Actor-Kind` | `agent`（默认）或 `human` |
| `X-Ontyra-Harness` | 宿主程序 |

用 `ONTYRA_MCP__PROFILE` 选择两种工具档之一：

- `lite`（默认）：`recall`、`check`、`briefing`、`ask`、`consult`、`get`、`remember`、`feedback`、`check_source`、`concepts`、`propose_ontology_change`、`pending_proposals` 和 `amend`。
- `full`：增加搜索与浏览、重复检测、维护任务、反馈审核、本体历史，以及面向人类的审核工具。当前源码树的确切清单见生成文件 `integrations/contract/mcp-tools-full.json`。

请为智能体安装 `ontyra-memory` 技能。文件位于 `integrations/skills/ontyra-memory/SKILL.md`，也可以运行 `integrations/skills/install.sh` 将它复制到 `~/.agents/skills`。该技能说明何时调取记忆、如何限定写入内容的适用范围，以及如何在执行有副作用的操作前进行检查。

## REST

REST API 还提供 `consult`、来源检查、本体管理、维护任务、反馈审核、重排器管理和账户管理。启动 Ontyra 后，打开 `/docs` 可以查看交互式 API 文档，`/openapi.json` 则提供 OpenAPI 模式。仓库中的 `integrations/contract/openapi.json` 保存了一份生成副本，并通过测试检查它是否与当前接口一致。

写入的状态码：`201` 已提交，`202` 搁置待审核，`422` 被拒绝（响应体会列出原因）。
