---
title: 来自来源的知识
---

记忆大多是你告诉智能体的内容：指示、偏好、决策。知识则是*来源*所说的内容：一篇论文、一份手册、一个网页、一个数据文件。Ontyra 存储结论，以及指向其出处的**引用**。它不存储、不读取、也不要求来源本身。

```json
{"op": "add", "kind": "fact",
 "fields": {"text": "Gene X is relevant to the Y pathway, which regulates Z", "about": ["gene_x"]},
 "evidence": [{"source": {"uri": "file:///papers/gene-x.pdf", "locator": "section 3, p. 4",
                          "quote": "X acts upstream of Y", "retrieved_at": "2026-11-23T10:00:00Z"}}]}
```

只有 `uri` 是必需的（`file:///path`、`https://...`、`doi:...`、`arxiv:...`；会被规范化，网页保留其查询串）。再加上 `locator`（在其中的位置）、一段简短的逐字 `quote`，如果有的话还有 `content_hash`：即使来源之后被编辑或删除，引文仍随记忆保留，而哈希让之后的检查能够判断它是否变过。证据要么是说过的话（会话引文），要么是来源，二者不能同时有；一条记忆可以有多条证据。

## 召回如何处理它

召回返回结论，并附上指向来源的指针：`(source: file:///papers/gene-x.pdf section 3, p. 4)`。要验证时，由智能体自己去读来源。守护进程中没有任何东西会去抓取它。

## 检查来源

当智能体或工具重新读取一个来源时，它会报告所见（`POST /v1/sources/check`，MCP `check_source`）：`ok`、`changed` 或 `missing`。`changed` 和 `missing` 会为每条引用它的现存记忆开启一个审核任务（撤回、修订、保留、推迟；任务会显示当时引用的引文），并且召回会加上一条提醒（`its source was reported changed`），直到问题被解决。守护进程从不轮询，因此在有人去看之前，失效的链接不会带来任何代价。`GET /v1/sources?uri=` 列出引用某个来源的记忆，以及它最近的报告。

## 审定：自动提交不等于已审核

由人写下或批准的记忆是**已审定（vetted）**。由策略在没有人查看的情况下为智能体提交的，则是**自动（auto）**：召回会使用它，并将其标为 `[unvetted]`。设置 `ONTYRA_RECALL__MIN_VETTING=vetted` 可使召回完全排除未审定的记忆。人在 `GET /v1/vetting/pending` 中逐条处理，并用 `POST /v1/vetting/{id}/vet` 确认每一条。控制台有一个 **Knowledge** 页（`/ui/knowledge`）完成同样的事：列出等待审定的内容及其来源，列出被报告为已变更或缺失的来源，并可查找某个来源以记录一次检查。

这正是让智能体抽取的知识切实可用的原因：一条策略规则可以在智能体的事实引用了带逐字引文的来源时（因此可以被审计）直接提交，并搁置其余的：

```bash
ONTYRA_POLICY__RULES='[
  {"actor_kinds":["human"],"decision":"auto_commit"},
  {"actor_kinds":["agent"],"kinds":["fact"],"sourced":true,"decision":"auto_commit"}
]'
```

当每个操作都引用了带 `quote` 的来源时，`sourced: true` 匹配（`false`：一个都不能有）。内置的默认值仍然会搁置智能体的所有写入，等待审核。

## 召回如何对待被引用的知识

- 有自己的分区和预算。引用了来源的记忆（指令、流程、教训或任务除外）会出现在 **Knowledge** 之下，在主预算之外，另以 `ONTYRA_RECALL__KNOWLEDGE_BUDGET_TOKENS`（默认 600）为限打包，因此它既不会挤掉指令，也不会被挤掉。`0` 表示它与主预算共用。
- 来源层级。`ONTYRA_RECALL__SOURCE_TIERS` 是你对来源出处的判断，是一个形如 `{"match": "<uri prefix>", "tier": "<name>", "weight": 1.2}` 的 JSON 列表。第一个匹配所引用来源的前缀决定层级，一条记忆取其最好的来源，权重乘到它的分数上。小于 1 的权重可以把一条记忆挤出召回（例如与论文相矛盾的论坛帖子）；大于 1 的权重只会让它排名更高，不会提高其他记忆需要越过的门槛。智能体不能自称某个层级：它来自 URI 和你的配置。
- 互证。引用多个不同来源的记忆，每多一个来源得到 `ONTYRA_RECALL__CORROBORATION_BOOST`（默认 0.05）的加成，最多计三个，并且召回会说明“N independent sources”。
- 时效。当某个来源最近一次读取（`retrieved_at`）早于 `ONTYRA_RECALL__SOURCE_STALE_DAYS`（默认 365，`0` 关闭）时，召回会加上一条提醒。

## 批量导入

阅读成千上万篇论文，是一个独立客户端的工作，它通过与任何参与者相同的提案 API 写入事实。守护进程没有导入流水线；它存储被提议的内容及其来源。本单体仓库带有一个这样的客户端：[`ontyra-ingest`](./ingestion.md)。
