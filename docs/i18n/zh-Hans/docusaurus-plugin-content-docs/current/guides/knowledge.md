---
title: 来自来源的知识
---

记忆通常来自你告诉智能体的内容，例如指示、偏好和决策。知识则来自论文、手册、网页或数据文件等外部资料。Ontyra 保存结论及其**来源引用**，但不保存或读取来源文件本身，也不要求来源始终可访问。

```json
{"op": "add", "kind": "fact",
 "fields": {"text": "Gene X is relevant to the Y pathway, which regulates Z", "about": ["gene_x"]},
 "evidence": [{"source": {"uri": "file:///papers/gene-x.pdf", "locator": "section 3, p. 4",
                          "quote": "X acts upstream of Y", "retrieved_at": "2026-11-23T10:00:00Z"}}]}
```

只有 `uri` 是必填项，可以是 `file:///path`、`https://...`、`doi:...` 或 `arxiv:...`。Ontyra 会规范化 URI，但会保留网页查询参数。还可以提供 `locator`（内容在来源中的位置）、简短的逐字 `quote`，以及可选的 `content_hash`。即使来源后来被修改或删除，引文仍会随记忆保存；内容哈希则可用于检查来源是否发生变化。每条证据只能引用会话原话或外部来源，不能同时使用两种形式；一条记忆可以有多条证据。

## 系统如何使用来源知识

系统返回结论时会附上来源位置，例如 `(source: file:///papers/gene-x.pdf section 3, p. 4)`。需要核实时，由智能体或其他客户端读取来源；Ontyra 服务不会主动抓取内容。

## 检查来源

智能体或工具重新读取来源后，可以通过 `POST /v1/sources/check` 或 MCP `check_source` 报告结果：`ok`、`changed` 或 `missing`。`changed` 和 `missing` 会为每条仍在使用该来源的记忆创建审核任务，供你选择撤回、修订、保留或推迟处理。任务中会显示当时保存的引文。在问题解决前，调取结果也会提示 `its source was reported changed`。Ontyra 不会定期访问这些地址，因此失效链接只有在客户端检查后才会被发现。`GET /v1/sources?uri=` 可列出引用指定来源的记忆及最近一次检查结果。

## 审定：自动提交不等于已审核

由人写下或批准的记忆是**已审定（vetted）**。由策略在没有人查看的情况下为智能体提交的，则是**自动（auto）**：召回会使用它，并将其标为 `[unvetted]`。设置 `ONTYRA_RECALL__MIN_VETTING=vetted` 可使召回完全排除未审定的记忆。人在 `GET /v1/vetting/pending` 中逐条处理，并用 `POST /v1/vetting/{id}/vet` 确认每一条。控制台有一个 **Knowledge** 页（`/ui/knowledge`）完成同样的事：列出等待审定的内容及其来源，列出被报告为已变更或缺失的来源，并可查找某个来源以记录一次检查。

你可以通过策略直接提交带有逐字来源引文、因而可供核查的事实，同时让其他内容继续等待审核：

```bash
ONTYRA_POLICY__RULES='[
  {"actor_kinds":["human"],"decision":"auto_commit"},
  {"actor_kinds":["agent"],"kinds":["fact"],"sourced":true,"decision":"auto_commit"}
]'
```

当每个操作都引用了带 `quote` 的来源时，`sourced: true` 匹配（`false`：一个都不能有）。内置的默认值仍然会搁置智能体的所有写入，等待审核。

## 来源知识的调取规则

- 来源知识使用独立分区和预算。除指令、流程、教训和任务外，带来源引用的记忆会显示在 **Knowledge** 下，并受 `ONTYRA_RECALL__KNOWLEDGE_BUDGET_TOKENS` 限制，默认 600。它不会占用主记忆预算，也不会被主预算中的内容挤出。设为 `0` 时两者共用预算。
- 来源层级。`ONTYRA_RECALL__SOURCE_TIERS` 是你对来源出处的判断，是一个形如 `{"match": "<uri prefix>", "tier": "<name>", "weight": 1.2}` 的 JSON 列表。第一个匹配所引用来源的前缀决定层级，一条记忆取其最好的来源，权重乘到它的分数上。小于 1 的权重可以把一条记忆挤出召回（例如与论文相矛盾的论坛帖子）；大于 1 的权重只会让它排名更高，不会提高其他记忆需要越过的门槛。智能体不能自称某个层级：它来自 URI 和你的配置。
- 互证。引用多个不同来源的记忆，每多一个来源得到 `ONTYRA_RECALL__CORROBORATION_BOOST`（默认 0.05）的加成，最多计三个，并且召回会说明“N independent sources”。
- 时效。当某个来源最近一次读取（`retrieved_at`）早于 `ONTYRA_RECALL__SOURCE_STALE_DAYS`（默认 365，`0` 关闭）时，召回会加上一条提醒。

## 批量导入

大量读取论文应由独立客户端完成。客户端通过标准提案 API 写入事实，与其他调用方没有区别。Ontyra 服务本身不包含导入流程，只保存提交的内容及其来源。本仓库提供了一个这样的客户端：[`ontyra-ingest`](./ingestion.md)。
