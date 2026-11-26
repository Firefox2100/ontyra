---
title: 使用 Docker 部署
---

仓库附带一个 `Dockerfile` 和一个 `compose.yaml`，把 SurrealDB 和守护进程一起运行，状态保存在以 bind mount 挂载的 `./data` 目录中。

## 运行

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
cp .env.example .env            # 设置 ONTYRA_DB_PASSWORD
docker build --build-arg VERSION=0.1.0 -t ontyra:local .
docker compose up -d
curl -s localhost:8787/health
```

镜像本身默认监听回环地址。若使用桥接网络，请显式设置 `ONTYRA_SERVER__HOST=0.0.0.0`，并把宿主机端口发布在 `127.0.0.1` 上。在不启用认证的情况下运行时，还要设置 `ONTYRA_SERVER__ALLOW_OPEN_NON_LOOPBACK=true`。面向互联网的反向代理部署，请使用 `ONTYRA_SERVER__DEPLOYMENT=public` 以及[认证与账户](./authentication.md)中描述的多用户设置。

守护进程会拒绝超过 `ONTYRA_SERVER__MAX_BODY_BYTES`（默认 2 MiB，`0` 表示关闭）的请求体，并返回 `413`；请求模型也限制了文本长度和一个提案中的操作数（100）。反向代理仍应设置它自己的请求体、速率和连接数限制：那是边缘层的职责。

若要把部署放在检出目录之外，把 `compose.yaml` 和 `.env` 复制到你选择的目录，保持 `image: ontyra:local`，然后在那里运行 `docker compose up -d`。拉取新版本之后，重新构建镜像并再次运行 `docker compose up -d`；数据保留在 `./data` 中。

## compose 文件配置了什么

- SurrealDB 4 位于 `127.0.0.1:18787`，守护进程位于 `127.0.0.1:8787`（`.env` 中的 `ONTYRA_PORT`、`ONTYRA_DB_PORT`）。两者都使用宿主机网络，且只监听回环地址，因为除非你设置 `ONTYRA_AUTH__TOKEN` 或开启多用户模式（[认证](./authentication.md)），否则认证是关闭的。不要把端口发布到你不信任的网络。
- MCP 端点（`/mcp/`，lite 工具档）和运维控制台（`/ui/`）。
- 智能体的写入等待审核；人自己的写入立即提交（[审核与策略](./review.md)）。
- `fusion` 召回引擎，嵌入来自一个兼容 OpenAI 的服务器。

## 指向你自己的嵌入模型

compose 文件期望在 `ONTYRA_EMBED_URL`（默认 `http://127.0.0.1:8081/v1`，模型 `ONTYRA_EMBED_MODEL` = `qwen3-embedding-8b`，维度 `ONTYRA_EMBED_DIM` = 4096）有一个嵌入模型。没有的话，嵌入会悄悄失败，召回回退为只做文本检索。请在 `.env` 中把这三个变量设为你自己的服务器和模型，并为它设置 `ONTYRA_ABSTAIN_COSINE`（见[嵌入](./embeddings.md)）。如果在已存入记忆之后更换模型，需要重新嵌入：清空后重新开始（发布前阶段）。

## 备份

在堆栈运行时，把数据库导出为一个 SurrealQL 文件（模式和每一条记录）：

```bash
ontyra db export                       # ./backups/ontyra-YYYYmmdd-HHMMSS.surql，仅你本人可读
ontyra db export /srv/backups --keep 14    # 导出到某个目录，保留最新的 14 份
ontyra db import backups/ontyra-....surql --yes   # 导入到一个空数据库（否则拒绝）
```

它读取与守护进程相同的 `ONTYRA_DB__*` 设置，并且需要一个 SurrealDB 服务器（不是内嵌数据库）。在每次会改变模式的升级之前，先做一次导出。

**定时导出**（先用 `mkdir -p backups` 创建 `./backups`，否则 Docker 会创建一个属于 root 的目录，守护进程无法写入）：设置 `ONTYRA_BACKUP__DIR`，守护进程会每隔 `ONTYRA_BACKUP__EVERY_HOURS`（24）自行导出，并保留最新的 `ONTYRA_BACKUP__KEEP`（14）份。它会等待距离最新文件的间隔剩余的时间，所以重启不会重复导出。compose 文件为此挂载了 `./backups`，并以 `ONTYRA_UID`/`ONTYRA_GID` 运行守护进程（在 `.env` 中把它们设为你自己的用户，使该目录可写、文件归你所有；这些文件仅你本人可读）。这些文件包含你的全部记忆：要像保护数据库一样保护好它们。在堆栈停止（`docker compose stop`）的情况下复制 `./data/surrealdb` 也可以。

## 不使用 Docker

[快速开始](../quick-start.md)直接从检出的代码运行守护进程，使用内嵌数据库；若要使用独立的 SurrealDB，把 `ONTYRA_DB__URL` 指向它（`ws://host:port/rpc`，并配上 `ONTYRA_DB__USERNAME` 和 `ONTYRA_DB__PASSWORD`）。守护进程在启动时应用模式。
