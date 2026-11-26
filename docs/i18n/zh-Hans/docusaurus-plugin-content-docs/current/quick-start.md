---
sidebar_position: 2
title: 快速开始
---

本指南使用内置数据库启动 Ontyra 服务，不需要 Docker 或模型服务。内置的词法嵌入器只提供基础检索能力；如需提高效果，请参阅[嵌入](./guides/embeddings.md)。

## 前提条件

[uv](https://docs.astral.sh/uv/)（它会替你安装 Python 3.12）、`curl` 和 `jq`。

## 1. 启动 Ontyra 服务

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
uv sync --extra embedded --extra mcp

export ONTYRA_DB__URL="surrealkv://$HOME/.ontyra/ontyra.db"   # 数据存放的位置
export ONTYRA_SERVER__PORT=8787                                # 插件默认使用的端口
export ONTYRA_MCP__ENABLED=true                                # 在 /mcp/ 提供 MCP 端点
export ONTYRA_UI__ENABLED=true                                 # 在 /ui/ 提供运维控制台
export ONTYRA_POLICY__DEFAULT=auto_commit                      # 个人使用：写入立即提交
uv run ontyra serve                                            # http://127.0.0.1:8787
```

检查一下：`curl -s localhost:8787/health`。交互式 API 文档在 `/docs`。

:::tip 用审核代替自动提交
在个人设备上，`auto_commit` 用起来更省事。默认值 `review` 会暂存每项变更，等待人工批准。允许智能体写入时，建议保留默认值。详见[审核与策略](./guides/review.md)。
:::

## 2. 保存一条记忆

每次写入都以*变更提案*的形式提交，其中可以包含多项操作。`X-Ontyra-Actor-Kind: human` 表示请求来自人类所有者；智能体默认使用 `agent`。

```bash
curl -s -X POST localhost:8787/v1/proposals \
  -H 'content-type: application/json' \
  -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human' \
  -d '{"ops": [{"op": "add", "kind": "directive", "fields": {
        "text": "Use uv and Python 3.12 for Python projects.",
        "strength": "must",
        "rule": "Use uv and Python 3.12",
        "scope": "universal"}}]}' | jq
```

返回 `201` 且 `"status": "committed"`，就表示已存入。

## 3. 读取记忆

```bash
# 会话开始时适用的内容
curl -s -X POST localhost:8787/v1/briefing -H 'content-type: application/json' \
  -d '{"context": {"uris": [], "signals": []}}' | jq -r .rendered

# 与一条消息相关的内容
curl -s -X POST localhost:8787/v1/recall -H 'content-type: application/json' \
  -d '{"query": "set up a new python project", "context": {"uris": [], "signals": []}}' | jq -r .rendered
```

打开 `http://127.0.0.1:8787/ui/`，可以浏览记忆、根据位置信息测试调取结果，并审核变更提案。

## 4. 连接智能体

连接插件后，智能体会自动收到会话简报和相关记忆，也可以通过 MCP 写入：

- [Claude Code](./agents/claude-code.md)
- [Codex](./agents/codex.md)
- [DSH](./agents/dsh.md)
- [其他客户端](./agents/other.md)
