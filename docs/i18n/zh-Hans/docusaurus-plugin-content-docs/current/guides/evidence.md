---
title: 证据与会话记录
---

一条记忆可以引用会话记录（episode），也可以引用外部来源。本页介绍会话记录，也就是按原文保存且不会被修改的单条消息。文档、网页和其他外部资料的处理方式见[来自来源的知识](./knowledge.md)。

Claude Code、Codex 和 DSH 插件会替你把每条提示和回复记录到 `POST /v1/episodes`（`ONTYRA_LOG_EPISODES=0`，或 DSH 中的 `episodes` 设置，可以关闭它）。

## 引用说过的话

写入记忆的智能体用**引文（quote）**引用其背后的原话。它不需要 id：

```json
{"op": "add", "kind": "directive", "fields": {"...": "..."},
 "evidence": [{"quote": "from now on, always use pytest"}]}
```

Ontyra 会从新到旧搜索调用方最近的会话记录，并在匹配时忽略大小写和空白差异。用户消息的优先级高于智能体回复。找到后，提案会保存对应会话记录的引用，因此即使几天后审核，仍能定位到同一条消息。如果找不到原文，系统会返回 `grounding_failed` 并拒绝提案，防止记忆引用从未出现过的内容。也可以显式提供 `episode` id，但引文必须确实出现在对应记录中。

## 会话记录保留多久

智能体客户端负责保存完整对话历史；Ontyra 中的会话记录只用于核对引文。没有被任何记忆引用的记录会在 `ONTYRA_EPISODES__RETENTION_DAYS` 天后删除，默认 14 天，设为 `0` 表示永久保留。清理在写入会话记录时触发，执行间隔不会短于 `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES`。只要相关记忆仍然存在，被引用的会话记录就会保留。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `ONTYRA_EPISODES__MATCH_WINDOW_HOURS` | `72` | 引文向前查找多久 |
| `ONTYRA_EPISODES__MATCH_LIMIT` | `500` | 检查的最近会话记录条数 |
| `ONTYRA_EPISODES__RETENTION_DAYS` | `14` | 未被引用的会话记录在多少天后删除 |
| `ONTYRA_EPISODES__PRUNE_INTERVAL_MINUTES` | `60` | 两次清理之间的最短间隔 |
