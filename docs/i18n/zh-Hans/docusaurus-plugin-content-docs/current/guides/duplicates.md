---
title: 查找重复
---

随着记忆逐渐增加，智能体可能会重复保存含义相同、措辞不同的规则。这会浪费调取结果的词元预算。`POST /v1/consolidation/scan`（`full` 工具档中的 MCP `find_duplicates`）可以找出这些内容。扫描是只读的，不会自动合并、取代或撤回记忆。你可以通过普通提案处理每组结果；[维护任务](./maintenance.md)会将结果加入队列并记录处理决定。

```bash
curl -s localhost:8787/v1/consolidation/scan -H 'content-type: application/json' -d '{}'
```

## 它报告什么

- `duplicates`：说同一件事的同种类记忆簇，按从旧到新排序。`keep` 是最旧的一条；用 `supersede` 或 `retract` 提案取代或撤回其余的（或把它们的措辞并入它）。
- `updates`：同一个陈述只是值变了的一对（PostgreSQL 14 与 16，300 EUR 上限与 500 EUR 上限）。这不是重复：较新的那条很可能取代较旧的，合并会丢失这个变化。两者靠其中包含的数字、版本和日期区分。
- `scanned`、`unembedded`（没有向量、被跳过的记忆）、`rejected` 和 `model_errors`（见下文）。

一次扫描查看你自己的记忆和全局记忆，绝不会把一条记忆与另一个所有者的记忆配对，并且只比较种类相同、且适用于共同位置（锚点互相包含，或都没有锚点）的记忆。

## 如何判断两条记忆是否重复

1. **候选**是嵌入索引中每条记忆的最近邻，其余弦相似度至少为 `ONTYRA_CONSOLIDATION__CANDIDATE_COSINE`（0.70）。
2. **没有决策模型时**，候选的余弦相似度达到 `ONTYRA_CONSOLIDATION__DUPLICATE_COSINE`（0.85）就算重复。
3. **有决策模型时**（[System One](../reference/configuration.md)，与召回使用相同的 `ONTYRA_DECISION__*` 设置），会按两种顺序分别询问这两条说的是不是同一件事；两次回答都达到 `ONTYRA_CONSOLIDATION__CONFIRM`（0.85）时，候选才算重复。模型无法回答的一对，按余弦规则判定，并计入 `model_errors`；被它否决的计入 `rejected`。

模型只提供判断依据，不会自动修改数据；它的回答也不会用于训练其他模型。这些余弦阈值针对 `qwen3-embedding-8b` 设置。更换嵌入器后，请重新校准。

## 效果如何

在基准世界（`tests/benchmark_test`：429 条记忆中有 47 个人为植入的重复，其中 11% 被手工改写为轻度编辑或完全转述）上，用 `qwen3-embedding-8b` 测得：

| 判定方式 | 找到的重复 | 精确率 |
|---|---|---|
| 余弦 ≥ 0.90 | 47 个中的 40 个 | 1.00 |
| 余弦 ≥ 0.85（默认） | 47 个中的 45 个 | 1.00 |
| 余弦 ≥ 0.80 | 47 个中的 47 个 | 0.86（8 个误报对：`molarity`/`molality`、`pay in 30`/`pay in 45 days`……） |
| `kev-9b` 确认 ≥ 0.70 的候选 | 47 个中的 45 个 | 1.00（它否决了 45 个候选对） |

在这组基准数据中，合适的余弦阈值与决策模型表现相近。决策模型的优势是减少对单一阈值的依赖：阈值偏低会增加误报，偏高则会漏掉重复项。上述结果只适用于这组基准数据和当前嵌入器，请在自己的环境中验证。

复现：`BENCH_PROFILES=profiles.json uv run pytest tests/benchmark_test/test_consolidation.py`（一个 profile 设定嵌入器、可选的 `decision` 模型和 `consolidation` 阈值）。
