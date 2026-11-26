---
title: 使用 Docker 部署
---

仓库提供 `Dockerfile` 和 `compose.yaml`，用于同时运行 SurrealDB 与 Ontyra 服务。持久数据通过 bind mount 保存在 `./data` 目录中。

## 运行

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
cp .env.example .env            # 设置 ONTYRA_DB_PASSWORD
docker build --build-arg VERSION=0.1.0 -t ontyra:local .
docker compose up -d
curl -s localhost:8787/health
```

镜像本身默认监听回环地址。若使用桥接网络，请显式设置 `ONTYRA_SERVER__HOST=0.0.0.0`，并把宿主机端口发布在 `127.0.0.1` 上。在不启用认证的情况下运行时，还要设置 `ONTYRA_SERVER__ALLOW_OPEN_NON_LOOPBACK=true`。面向互联网的反向代理部署，请使用 `ONTYRA_SERVER__DEPLOYMENT=public` 以及[认证与账户](./authentication.md)中描述的多用户设置。

Ontyra 会拒绝超过 `ONTYRA_SERVER__MAX_BODY_BYTES` 的请求体并返回 `413`。默认上限为 2 MiB，设为 `0` 可关闭该限制。请求模型还会限制文本长度和单个提案中的操作数量，上限为 100。反向代理仍应设置请求体大小、请求速率和连接数限制，这些属于边缘层的安全职责。

若要把部署放在检出目录之外，把 `compose.yaml` 和 `.env` 复制到你选择的目录，保持 `image: ontyra:local`，然后在那里运行 `docker compose up -d`。拉取新版本之后，重新构建镜像并再次运行 `docker compose up -d`；数据保留在 `./data` 中。

## compose 默认配置

- SurrealDB 4 位于 `127.0.0.1:18787`，Ontyra 位于 `127.0.0.1:8787`，端口可通过 `.env` 中的 `ONTYRA_PORT` 和 `ONTYRA_DB_PORT` 修改。两者都使用宿主机网络并只监听回环地址。除非设置 `ONTYRA_AUTH__TOKEN` 或启用多用户模式，否则认证处于关闭状态。不要把端口暴露到不受信任的网络。
- MCP 端点（`/mcp/`，lite 工具档）和运维控制台（`/ui/`）。
- 智能体的写入等待审核；人自己的写入立即提交（[审核与策略](./review.md)）。
- 使用 `fusion` 记忆调取引擎，并从兼容 OpenAI 接口的服务器获取嵌入向量。

## 指向你自己的嵌入模型

compose 默认通过 `ONTYRA_EMBED_URL` 连接嵌入模型，默认地址为 `http://127.0.0.1:8081/v1`，模型为 `qwen3-embedding-8b`，向量维度 `ONTYRA_EMBED_DIM` 为 4096。如果服务不可用，嵌入步骤会失败，记忆调取会回退到纯文本检索。请在 `.env` 中将这些变量改为自己的服务和模型，并设置对应的 `ONTYRA_ABSTAIN_COSINE`，详见[嵌入](./embeddings.md)。保存记忆后再更换模型，需要重新生成全部嵌入。在当前发布前阶段，可以清空数据后重新开始。

## 备份

服务运行期间，可以把数据库结构和全部记录导出到一个 SurrealQL 文件：

```bash
ontyra db export                       # ./backups/ontyra-YYYYmmdd-HHMMSS.surql，仅你本人可读
ontyra db export /srv/backups --keep 14    # 导出到某个目录，保留最新的 14 份
ontyra db import backups/ontyra-....surql --yes   # 导入到一个空数据库（否则拒绝）
```

导入和导出命令读取与 Ontyra 服务相同的 `ONTYRA_DB__*` 设置，并且需要独立运行的 SurrealDB，不能使用内嵌数据库。每次进行可能改变数据库结构的升级前，请先导出备份。

如需**定时导出**，先运行 `mkdir -p backups` 创建 `./backups`。否则 Docker 可能创建一个属于 root 的目录，导致 Ontyra 无法写入。设置 `ONTYRA_BACKUP__DIR` 后，服务会按 `ONTYRA_BACKUP__EVERY_HOURS` 的间隔导出，默认每 24 小时一次，并保留最近 `ONTYRA_BACKUP__KEEP` 份，默认 14 份。重启后会根据最新备份的时间继续计时，不会立即重复导出。compose 会挂载 `./backups`，并以 `ONTYRA_UID` 和 `ONTYRA_GID` 指定的用户运行服务。请在 `.env` 中将它们设为自己的用户 id 和组 id，确保目录可写且文件归你所有。备份包含全部记忆，应像数据库本身一样妥善保护。也可以先运行 `docker compose stop` 停止服务，再复制 `./data/surrealdb`。

## 不使用 Docker

[快速开始](../quick-start.md)介绍如何从检出的代码运行 Ontyra，并使用内嵌数据库。若要改用独立的 SurrealDB，请将 `ONTYRA_DB__URL` 设为 `ws://host:port/rpc`，同时配置 `ONTYRA_DB__USERNAME` 和 `ONTYRA_DB__PASSWORD`。Ontyra 会在启动时应用数据库结构。
