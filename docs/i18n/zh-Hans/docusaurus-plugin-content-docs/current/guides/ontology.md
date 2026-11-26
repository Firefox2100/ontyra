---
title: 本体
---

本体是记忆所谈论的词汇：**概念**（`python`、`ubuntu`、`ci pipeline`），带有别名和有类型的关系（`is_a`、`part_of`、`same_as`、`alternative_to`、`depends_on`、`disjoint_with` 等）。记忆指向概念 id（`about`、限定条件）；召回会沿着关系扩展一次提及，所以一条关于 `ubuntu` 的记忆可以回答关于 `linux` 的问题。这里没有任何建议：建议存在于记忆里。

## 修改本体

智能体**提议**变更（`propose_ontology_change`、`POST /v1/ontology/changes`）；人自己的变更立即生效，智能体的则等待审核。每次变更都作为一个原子批次应用。

| 变更 | 作用 |
|---|---|
| `define` | 一个新概念（id 为其标签的 slug，永不重用） |
| `relate` / `unrelate` | 添加一条关系 / 终止一条关系（这条边作为历史保留） |
| `edit` | 更清晰的标签、更紧凑的定义、其他别名；旧标签成为别名 |
| `reparent` | 以原子方式把一个更宽泛的概念换成另一个，并做环检测 |
| `merge` | `source` 成为 `into` 的别名，并被弃用 |
| `split` | 一个概念其实是好几个：创建新概念（或指定已有的概念） |
| `deprecate` | 弃用一个概念，可带或不带后继，并给出理由 |
| `restore` | 把概念恢复到较早的版本（回退也是这样实现的） |

**编辑不得改变概念的含义。** 如果含义变了，请定义一个新概念并弃用旧的。守护进程无法检查这一点；由审核者来判断，与旧定义几乎没有共同之处的重写定义，会得到一行建议。

**先预览**：`POST /v1/ontology/changes/preview`（MCP `preview_ontology_change`，full 档；控制台始终先预览）会列出一次变更将会做什么：涉及的概念、有多少条现存记忆与它们相关，以及建议。不会写入任何东西。

## 版本、墓碑，以及哪些仍然可见

- 每次编辑都会写入概念的一个新的不可变**版本**。版本不会出现在查找、召回、搜索或列表中；只有调查类工具会显示它们：`GET /v1/concepts/ID/history`、`GET /v1/ontology/changes?pending=false&concept=ID`（MCP `concept_history`、`ontology_history`，full 档），以及控制台的概念页。
- **被弃用的概念从不删除**：它作为墓碑保留，带有 `replaced_by`（一个后继）或 `split_into`（多个后继）。id 不会重用。
- *使用*已弃用的概念会受到引导：查找和列表会隐藏它（被拆分的概念由它的后继代表）；点名它的新记忆会被转移到后继并带有警告，如果它已被拆分（请选择一个后继）或没有后继，则会被拒绝。
- *表示*现有数据不受影响：记忆保留它们写入时使用的 id，仍然能被找到（召回会把被替换或拆分的概念扩展到它的后继）。派生的搜索索引由定时维护整理；拆分还会排入可选的细化任务（`review_concept_drift`，上限由 `ONTYRA_TASKS__REFINE_CAP` 决定），询问一条记忆真正关于哪个后继。修订从不被改写。

## 回退

`POST /v1/ontology/changes/ID/revert`（人，全局写权限；MCP `revert_ontology_change`）会把该变更存储的撤销操作作为一次新变更应用。如果之后又有变更涉及相同的概念，则会被拒绝：请先回退那些变更。一次回退本身也可以被回退。

## 健康检查

`GET /v1/ontology/changes/check`（MCP `check_ontology`，控制台 **Concepts**，再点 **Health check**）是一份只读报告：`is_a`/`part_of` 中的环、指向不存在概念的链接、替换环、被两个概念使用的名称、现存记忆仍在使用的已弃用概念、未使用的概念。每一项发现都会指出最可能造成它的那次变更。不会自动修复任何东西。

## 审核等待中的变更

一项等待中的本体变更可以被修改（产生新版本）、由其提议者撤回，或由审核者附上评论退回（`changes_requested`）；批准时可以指明审核者所看到的版本。定义了类似概念、或涉及同一个概念的第二个提案，会得到 `pending_similar` 或 `pending_conflict` 建议。见[审核与策略](./review.md)。

## 在控制台中

**Concepts** 列出概念（勾选 *show deprecated* 可看到墓碑），并链接到概念页，页内有它的关系、记忆、版本以及涉及它的变更。编辑表单在应用之前总会显示预览。**Change history** 列出已应用的变更，并带有回退按钮；**Review** 页显示每项等待中的变更将会做什么。
