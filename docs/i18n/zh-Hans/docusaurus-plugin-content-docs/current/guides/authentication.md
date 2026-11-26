---
title: 认证与账户
---

Ontyra 提供三种认证模式。无论使用哪一种，**客户端都不能通过请求参数指定记忆所有者**。未启用账户时，记忆属于全局空间；启用账户后，所有者由经过认证的账户决定。

| 模式 | 设置 | 谁可以调用 | 所有者 |
|---|---|---|---|
| 开放（默认） | 无 | 任何能连上该端口的人 | 全局记忆 |
| 单令牌 | `ONTYRA_AUTH__TOKEN` | 携带该令牌的请求 | 全局记忆 |
| 多用户 | `ONTYRA_AUTH__MULTI_USER=true` + `ONTYRA_AUTH__ADMIN_TOKEN` | 携带某个账户凭据的请求 | 该账户 |

令牌以 `Authorization: Bearer <token>` 发送，适用于 REST、MCP，以及（作为登录凭据）控制台。`/health` 和 API 文档保持开放。

:::caution 公开部署需要完整的边界
开放模式和单令牌模式不适合互联网多租户服务。公开部署时，请设置 `ONTYRA_SERVER__DEPLOYMENT=public` 并启用多用户认证。Ontyra 应放在反向代理或负载均衡器之后，由前置服务负责 TLS、HSTS、速率限制、请求体大小限制和可信转发头。SurrealDB 和模型适配器不应暴露到公网。如果启用了控制台，请设置 `ONTYRA_AUTH__UI_ALLOWED_ORIGINS='["https://memory.example.com"]'`。公开模式会自动为 cookie 添加 Secure 属性，并拒绝显式设置 `ONTYRA_AUTH__UI_COOKIE_SECURE=false`。
:::

本地模式是默认值。它支持回环地址、纯 HTTP，以及开放或单令牌的使用。在非回环地址上的开放监听会被拒绝，除非用 `ONTYRA_SERVER__ALLOW_OPEN_NON_LOOPBACK=true` 明确确认。这是为桥接网络中的容器准备的（其宿主机发布仍被限制在 `127.0.0.1`），而不是用于公开暴露。

## 开放与单令牌模式

未设置令牌时，任何能连接端口的人都会被信任，因此服务必须只监听回环地址。调用方仍可通过 `X-Ontyra-Actor`、`X-Ontyra-Actor-Kind` 和 `X-Ontyra-Harness` 声明身份，Ontyra 会据此应用审核策略，例如允许 `human` 自动提交、让 `agent` 等待审核。这些标签没有经过认证。

要求令牌（至少 32 个字符）：

```bash
export ONTYRA_AUTH__TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

把相同的值作为 `ONTYRA_TOKEN` 交给客户端（Claude Code 和 Codex 插件直接读取它；DSH 插件读取 `tokenRef` 所指名的凭据）。只有一个令牌，它能打开一切。它的行为者类型由 `ONTYRA_AUTH__SINGLE_USER_KIND=human|agent`（默认 `human`）固定；请求头无法提升它。

## 多用户模式

每位用户都有一个**账户**和一份私有记忆。账户可以创建多个**凭据**，分别供控制台、Codex、Claude Code、DSH 配置档或脚本使用。每个凭据都有独立的令牌、名称和类型：

- `human`：这个人。可以审核提案；如果你的策略允许人类，则立即提交。
- `agent`：代表此人行动的智能体。它的写入遵循针对智能体的策略（默认被搁置，等这个人批准）。

一个账户可以看到和做什么：

- 它读取**自己的记忆和全局记忆**，绝不读取别人的。
- 它可以自由写入自己的记忆（策略允许的话）。写入**全局记忆**（以及更改共享的本体和资源）需要 `global_write` 权限。要把记忆放在那里，在提案上发送 `"space": "global"`；没有权限时会被拒绝。
- 提案由同一账户的人类凭据审核；全局提案由具有 `global_write` 的人类审核。智能体凭据从不审核。
- `admin` 管理账户和凭据（并重新训练重排器）。只有管理员的人类凭据才能使用它。

使用内置 `admin` 账户的初始令牌启动 Ontyra：

```bash
export ONTYRA_AUTH__MULTI_USER=true
export ONTYRA_AUTH__ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run ontyra serve
```

然后通过 API 创建账户和凭据（`ADMIN` 即该令牌）。新凭据的令牌在响应中**只出现一次**；存储的只有哈希。

```bash
A="Authorization: Bearer $ADMIN"
curl -s -X POST localhost:8787/v1/admin/users -H "$A" -H 'content-type: application/json' \
  -d '{"name": "alice", "permissions": []}'
curl -s -X POST localhost:8787/v1/admin/users/alice/credentials -H "$A" \
  -H 'content-type: application/json' -d '{"name": "me", "kind": "human"}'
curl -s -X POST localhost:8787/v1/admin/users/alice/credentials -H "$A" \
  -H 'content-type: application/json' -d '{"name": "codex", "kind": "agent", "harness": "codex"}'
```

其他管理调用：`GET /v1/admin/users`、`PATCH /v1/admin/users/{name}`（权限、`disabled`）、`GET /v1/admin/credentials`、`POST /v1/admin/credentials/{id}/revoke`。被撤销的凭据或被禁用的账户会立即被拒绝。更改管理员令牌的办法是更改该设置。

把每个客户端各自的凭据令牌作为 `ONTYRA_TOKEN` 交给它：控制台用人类凭据，插件用智能体凭据。

在多用户模式下，`X-Ontyra-*` 请求头会被忽略。调用方的名字是 `account.credential`（例如 `alice.codex`）。

## 控制台

只要要求任何令牌，`/ui/login` 就会请你输入令牌并开始一个 cookie 会话（HttpOnly、same-site strict，`ONTYRA_AUTH__UI_SESSION_HOURS`，默认 12；重启会让所有人退出登录）。只有人类凭据（或单令牌）可以登录。每个会改变状态的表单都需要一个被允许的 `Origin` 或 `Referer`，多用户会话在每次请求时都会重新检查凭据撤销、账户禁用和权限。公开的 HTTPS 部署必须启用 secure cookie，并按上文所示配置准确的外部来源。

Ontyra 不会抓取请求中提供的锚点 URI。唯一的对外 HTTP 目标是由运维人员配置的嵌入、重排和决策适配器；不要把这些设置指向不受信任的服务。

## MCP 工具与权限提示

每个 MCP 工具都带有注解，Claude Code 和 Codex 这类客户端用它们来决定是否先询问：

| 工具 | 只读 | 破坏性 | 说明 |
|---|---|---|---|
| `recall`、`check`、`briefing`、`ask`、`consult`、`get`、`concepts`、`search`、`overview`、`neighbors`、`list_memories`、`pending_proposals`、`pending_ontology_changes`、`find_duplicates`、`feedback_outliers`、`pending_tasks` | 是 | 否 | `recall` 和 `briefing` 还会在会话中记下它们送达了什么 |
| `check_source` | 否 | 否 | 存储客户端重新读取所引用来源后的发现；`changed` 和 `missing` 会开启审核任务，不会更改任何记忆 |
| `remember`、`propose_ontology_change` | 否 | 否 | 记录一个提案；只有策略提交了它，记忆才会改变，并且保留历史 |
| `detect_tasks`、`claim_task`、`complete_task`、`resolve_task` | 否 | 否 | 排入并关闭维护任务；`resolve_task` 还会以你的身份提交一个 `supersede` 提案（在通常的策略下提交或等待审核） |
| `reject`、`reject_ontology_change` | 否 | 否 | 关闭一个被搁置的条目，不提交任何东西 |
| `approve`、`approve_ontology_change` | 否 | 是 | 提交一个被搁置的条目，这可能取代或撤回记忆，或更改共享的本体 |

这些工具都不会操作 Ontyra 之外的系统（`openWorldHint` 为 false），并且都有便于阅读的标题。
