---
title: 审核与策略
---

## 策略

每个提案都要经过**策略**，策略由环境变量配置：

- `ONTYRA_POLICY__DEFAULT`：`review`（默认）、`auto_commit` 或 `reject`。
- `ONTYRA_POLICY__RULES`：一个 JSON 规则列表，先匹配者胜出。一条规则仅当提案中的每一个操作都符合它时才适用。

在自己机器上单人使用：`ONTYRA_POLICY__DEFAULT=auto_commit`。

智能体加一位人类所有者（Docker 部署所用的方式）：搁置智能体的写入，直接提交你自己的。

```bash
ONTYRA_POLICY__DEFAULT=review
ONTYRA_POLICY__RULES='[{"actor_kinds":["human"],"decision":"auto_commit"}]'
```

谁算人类，由 `X-Ontyra-Actor-Kind` 请求头决定，除非你启用了账户，否则它只是一个标签：在[多用户模式](./authentication.md)下，它来自凭据。没有账户时，任何能连上该端口的人都可以自称人类，所以请让守护进程只监听回环地址。

## 运维控制台

设置 `ONTYRA_UI__ENABLED=true` 后，打开 `http://127.0.0.1:8787/ui/`：

- 总览：等待中的提案和本体变更的数量，以及各种类的活跃记忆数。
- 审核：每个被搁置的提案及其操作、发现和原始 JSON，以及批准和拒绝按钮。本体变更同理。
- 记忆：按种类和状态浏览；打开一条可看到它的字段、修订历史、链接和证据。
- 概念：查找一个概念及其别名和关系。
- 召回试验场：用一条查询、若干位置和信号，试用一次召回或简报，并查看每条记忆为什么被返回。

在开放模式下，控制台以名为 `ONTYRA_UI__ACTOR`（默认 `owner`）的人类身份行事。当需要令牌时，`/ui/login` 把人类凭据换成内存中的 cookie 会话。表单提交必须来自允许的来源。开放部署和单令牌部署请保持在回环地址上；公开的控制台需要多用户模式、边缘处的 HTTPS，以及在 `ONTYRA_AUTH__UI_ALLOWED_ORIGINS` 中填写准确的外部来源。

## 修改等待中的内容

被搁置等待审核的提案并没有被冻结：

- 修改（amend）：它的提交者（或人类审核者）用一份更正过的列表替换它的操作，并说明原因。这会产生一个新的**版本**；旧版本保留在提案的 `history` 中，批准时要指明审核者所看到的版本，因此对过时版本的批准会被拒绝。
- 撤回（withdraw）：提交者或审核者把它收回（终局）。
- 请求修改（request changes）：审核者附上评论（以及可选的、针对单个操作的评论）把它退回。在被修改之前，它会离开待批准队列（`changes_requested`）；提交者在列出自己待处理的提案时，会看到这些评论。
- 逐个操作的决定：审核者可以只批准一部分操作（它们所需要的操作会被一并包含，其余的带着理由被拒绝），或拒绝一部分（连同依赖它们的操作），其余的则作为新版本继续等待。当一个操作指名了另一个操作的 `ref` 时，它们互相依赖，例如对同一提案中新增的记忆做 `supersede`。
- 等待中的提案对新的写入可见：当智能体提出的内容与一个等待中的提案近乎重复，或更改了另一个等待中的提案也要更改的记忆时，结果会带上一条 `pending_similar` 或 `pending_conflict` 发现，指明那个提案（“请改为修改它”）。等待中的提案从不影响召回。

```bash
curl -s -X POST localhost:8787/v1/proposals/ID/amend -H 'Content-Type: application/json' \
  -d '{"ops": [...], "why": "named the repo", "expect_version": 1}'
curl -s -X POST localhost:8787/v1/proposals/ID/request-changes -H 'Content-Type: application/json' \
  -H 'X-Ontyra-Actor-Kind: human' -d '{"comment": "too vague", "op_comments": {"0": "which repo?"}}'
curl -s -X POST localhost:8787/v1/proposals/ID/approve -H 'Content-Type: application/json' \
  -H 'X-Ontyra-Actor-Kind: human' -d '{"ops": [0, 2], "reason": "the second is a style opinion"}'
```

通过 MCP，智能体有 `pending_proposals` 和 `amend`（两个档都有）以及 `withdraw`（full 档）；审核工具（`approve`、`reject`、`request_changes`）在 full 档，并且需要人类凭据。在控制台里，一个等待中的提案带有逐个操作的复选框（批准或拒绝所选的；它们所需要的、或需要它们的操作会被一并包含）、带有各版本之间差异对比的版本历史、一个请求修改的表单，并且在审核队列上有一个**批量编辑**，可以把相同的作用域、过期时间、锚点或概念，给若干提案的 add 和 revise 操作各生成一个新版本。等待中的本体变更也可以用同样的方式被修改、撤回或退回（见[本体](./ontology.md)）。

## 通过 API

```bash
curl -s localhost:8787/v1/proposals                      # 待处理的提案
curl -s -X POST localhost:8787/v1/proposals/ID/approve \
  -H 'X-Ontyra-Actor: me' -H 'X-Ontyra-Actor-Kind: human'
curl -s localhost:8787/v1/ontology/changes               # 待处理的本体变更
```

`ID` 是 `proposal:` 之后的部分。批准时会针对当前数据重新校验，所以已经过时的提案会被拒绝，而不是被应用。
