---
title: 维护任务
---

有些维护工作需要人工或智能体判断，例如两条记忆是否表达同一件事，或较新的版本是否已经取代旧版本。Ontyra 会通过确定性规则找出这些情况（参阅[查找重复](./duplicates.md)），再把它们加入**任务**队列。你或宿主智能体可以认领任务，通过普通提案解决问题，并用经过校验的结果完成任务。Ontyra 不会自行合并记忆。

## 流程

1. **检测**：调用 `POST /v1/tasks/detect` 或 MCP `detect_tasks`。每项发现会根据相关修订去重，因此重复运行不会反复创建任务。被标记为 `distinct` 的记忆也不会再次询问。如果相关记忆后来被修改或撤回，原任务会以过时状态关闭。Ontyra 也会定期自动检测：启动 `ONTYRA_TASKS__DETECT_FIRST_AFTER_MINUTES` 分钟后首次运行，默认 10 分钟；此后按 `ONTYRA_TASKS__DETECT_EVERY_HOURS` 的间隔运行，默认 24 小时，设为 `0` 可关闭。检测会分别处理全局记忆和各用户的私有记忆。它只创建任务，并可让决策模型辅助判断重复候选；在任务得到处理前，不会修改记忆。
2. **查看**：`GET /v1/tasks`（MCP `pending_tasks`；可按 `kind` 和 `status` 过滤）。每个任务有一张**卡片**：说明、涉及的记忆，以及可选的决定。
3. **认领**：`POST /v1/tasks/{id}/claim`，避免两个智能体做同一份工作（`/release` 把它还回去）。
4. **解决**：提交一个普通提案。例如，可以通过 `remember` 对重复项执行 `supersede`，并将原因设为 `merged`。提案仍需经过常规校验和审核策略，因此智能体发起的合并也要等你批准。也可以调用 `POST /v1/tasks/{id}/resolve`、MCP `resolve_task`，或点击控制台中的 **Merge**。Ontyra 会以你的身份提交任务卡片中描述的提案，并在成功后完成任务。提案等待审核期间，任务会保持*已认领*；如果提案被拒绝，下次检测会把任务重新放回队列。
5. **完成**：`POST /v1/tasks/{id}/complete`，附上一个决定：

| 决定 | 含义 | 需要 |
|---|---|---|
| `merge` | 重复项已被整合（重复簇） | 完成此事的提案 |
| `supersede` | 较新的记忆取代了较旧的（更新） | 完成此事的提案 |
| `retract` | 这条记忆不再成立（被报告或即将过期） | 完成此事的提案 |
| `revise` | 这条记忆已被纠正或补充 | 完成此事的提案 |
| `distinct` | 两条记忆在重要方面不同；不会再被提出 | 无 |
| `keep` | 这条记忆依然成立；对该修订不会再被提出 | 无 |
| `defer` | 先搁置，以后再说 | 无 |

卡片没有提供的决定、缺失的提案、或不是你的、或已被拒绝的提案，都会被拒绝。

## 种类

- `consolidate`：一簇重复项。
- `review_contradiction`：同一个陈述但值变了（更新）。
- `review_feedback`：有人报告为 `outdated` 或 `wrong` 的记忆（`POST /v1/feedback`，或 MCP `feedback` 工具）。每个修订一个任务，汇集所有报告；报告本身从不改变记忆，由人或智能体决定（`retract`、`revise` 或 `keep`）。
- `expire_check`：`expires_at` 已过、或落在 `ONTYRA_TASKS__EXPIRY_WARN_DAYS`（14）天之内的现存记忆，由 `detect` 找出。召回本来就会忽略已过期的记忆；任务询问是延长它（`revise`）、撤回它，还是任其失效（`keep`）。
- `review_stale_dependent`：由一条被撤回的记忆派生出来的记忆（由撤回操作本身创建）。

`review_proposal` 和 `review_ontology_change` 任务也会被创建；这些要在控制台里或通过它们自己的端点，用批准和拒绝来决定。它们只列给能够作出决定的人，并且不能在这里认领。

## 控制台

`/ui/tasks` 列出待办任务，并把涉及的记忆并排显示。**Look for new work** 运行检测；**Merge into the oldest**（或 **The newer replaces the older**，或 **Retract it**）以你的身份解决任务；**They differ** 或 **It stands** 记录决定；**Later** 搁置它。要修订一条记忆，请从任务中打开它，然后用该提案完成任务。

## 谁能看到什么

任务属于它们所涉及记忆的所有者。你能看到自己的和全局的，并按与记忆相同的规则修改它们：自己的，或具有 `global_write` 权限时的全局任务。扫描从不混用所有者。

认领任务时会先读取状态再写入，因此当前实现假定只有一个 Ontyra 服务实例。本体管理也有相同限制。
