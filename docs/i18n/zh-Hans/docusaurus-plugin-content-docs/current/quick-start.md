---
sidebar_position: 2
title: 快速开始
---

本页使用内置数据库运行守护进程：不需要 Docker，也不需要模型服务。检索质量只是基础水平（内置的词法嵌入器）；如需升级，见[嵌入](./guides/embeddings.md)。

## 前提条件

[uv](https://docs.astral.sh/uv/)（它会替你安装 Python 3.12）、`curl` 和 `jq`。

## 1. 启动守护进程

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
`auto_commit` 在你自己的机器上很方便。默认值 `review` 会把每个提案都搁置，直到有人批准，在由智能体写入时应当使用它。见[审核与策略](./guides/review.md)。
:::

## 2. 存一条记忆

一次写入是一个由若干操作组成的*提案*。`X-Ontyra-Actor-Kind: human` 表示你是所有者（智能体使用默认值 `agent`）。

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

## 3. 读回来

```bash
# 会话开始时适用的内容
curl -s -X POST localhost:8787/v1/briefing -H 'content-type: application/json' \
  -d '{"context": {"uris": [], "signals": []}}' | jq -r .rendered

# 与一条消息相关的内容
curl -s -X POST localhost:8787/v1/recall -H 'content-type: application/json' \
  -d '{"query": "set up a new python project", "context": {"uris": [], "signals": []}}' | jq -r .rendered
```

打开 `http://127.0.0.1:8787/ui/` 可以浏览记忆、带着位置信息试用召回，并审核提案。

## 4. 连接智能体

智能体通过插件自动获得简报和召回，并可通过 MCP 写入：

- [Claude Code](./agents/claude-code.md)
- [Codex](./agents/codex.md)
- [DSH](./agents/dsh.md)
- [其他客户端](./agents/other.md)
