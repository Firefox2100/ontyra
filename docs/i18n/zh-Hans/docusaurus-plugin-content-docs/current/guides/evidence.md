---
title: 证据与会话记录
---

一条记忆可以引用一段对话记录（episode），也可以引用外部来源。本页讲会话记录：对话中的一条原始消息，逐字存储，永不更改。关于文档、网页和其他外部材料，见[来自来源的知识](./knowledge.md)。

Claude Code、Codex 和 DSH 插件会替你把每条提示和回复记录到 `POST /v1/episodes`（`ONTYRA_LOG_EPISODES=0`，或 DSH 中的 `episodes` 设置，可以关闭它）。

## 引用说过的话

写入记忆的智能体用**引文（quote）**引用其背后的原话。它不需要 id：

```json
{"op": "add", "kind": "directive", "fields": {"...": "..."},
 "evidence": [{"quote": "from now on, always use pytest"}]}
```

守护进程会在调用方最近的会话记录中查找这句引文，从新到旧，忽略大小写和空白。人说的话优先于回复给人的话。随后提案会存下它找到的那条会话记录，因此几天后的审核仍然指向同一条消息。哪里都找不到的引文会触发 `grounding_failed` 错误，提案被拒绝：记忆不能声称一句没人说过的话。显式给出的 `episode` id 仍然可用，此时引文必须出现在那条会话记录中。

## 会话记录保留多久

智能体宿主自己保留完整的对话记录；守护进程的日志只是用来核对引文。没有任何记忆引用的会话记录，在 `ONTYRA_EPISODES__RETENTION_DAYS`（默认 14；0 表示全部保留）之后被删除。清理最多每 `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES` 一次，在写入会话记录时触发。被引用的会话记录，只要它所支撑的记忆还在，就一直保留。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `ONTYRA_EPISODES__MATCH_WINDOW_HOURS` | `72` | 引文向前查找多久 |
| `ONTYRA_EPISODES__MATCH_LIMIT` | `500` | 检查的最近会话记录条数 |
| `ONTYRA_EPISODES__RETENTION_DAYS` | `14` | 未被引用的会话记录在多少天后删除 |
| `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES` | `60` | 两次清理之间的最短间隔 |
