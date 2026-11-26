# ontyra

[English](README.md) | 简体中文

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Coverage](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=coverage)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra) [![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=Firefox2100_ontyra&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=Firefox2100_ontyra)

面向智能体的实验性长期记忆系统：在一个守护进程中提供有类型、分层的记忆，智能体宿主（Claude Code、Codex、DSH 以及任何 MCP 客户端）通过 REST 或 MCP 与之通信。

状态：实验项目。认证是可选的（单个令牌，或按用户的账户）；没有认证时，请只在回环地址上运行。

## 试一试

```bash
uv sync --extra embedded --extra mcp
export ONTYRA_DB__URL="surrealkv://$HOME/.ontyra/ontyra.db" ONTYRA_SERVER__PORT=8787 \
  ONTYRA_MCP__ENABLED=true ONTYRA_UI__ENABLED=true ONTYRA_POLICY__DEFAULT=auto_commit
uv run ontyra serve        # http://127.0.0.1:8787  （控制台在 /ui/，API 文档在 /docs）
```

然后连接一个智能体：`integrations/claude-code`（Claude Code 插件）、`integrations/codex`（Codex 插件）或 `integrations/dsh`（DeepSeek Harness 插件）。若要把文档作为带来源的知识导入，请使用 `integrations/ingest` 中的导入客户端。完整指南有英文和中文两个版本，位于 `docs/`（`cd docs && pnpm install && pnpm start`）。
