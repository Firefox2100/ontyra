---
title: 批量导入
---

`ontyra-ingest` 是一个独立的命令行客户端（位于 `integrations/ingest/`，与守护进程分开发布）。它读取文档，请语言模型给出文档所作的陈述，用文档自己的原话核对每一条陈述，并把留下来的作为引用其来源的事实提议给守护进程。守护进程并不知道它的存在：它看到的是一个普通的智能体在写入带来源的提案，所以[策略](./knowledge.md)、审定和审核照常适用。

它索引的是**知识，而不是文档**：除了每条事实所引用的那段引文，来源的任何内容都不会被存储。全文搜索仍然是文档检索系统的工作。

## 安装与运行

```bash
cd integrations/ingest && uv sync
export ONTYRA_URL=http://127.0.0.1:8787 ONTYRA_TOKEN=...      # 与插件使用相同的名字
export ONTYRA_INGEST_LLM_URL=http://127.0.0.1:8081/v1          # 任何兼容 OpenAI 的端点
export ONTYRA_INGEST_LLM_MODEL=qwen3.6-35b-a3b-uncensored-1m

uv run ontyra-ingest run papers/ --dry-run        # 抽取并核对，不写入任何东西
uv run ontyra-ingest run papers/ -p claims -p datasets
uv run ontyra-ingest parse paper.pdf              # 文件如何被读取并切分成块
uv run ontyra-ingest plugins                      # 解析器、身份识别器、配置档（profile）
```

每一项设置也都是一个选项（`--help`）。运行可以放心重复：模型对每个块的回答，以及已被接受的提案，都保存在 `~/.local/state/ontyra-ingest/` 中，所以中断的运行可以续上，完成的运行不花任何代价。未通过引文核对的陈述记录在那里的 `rejected/` 中，绝不会被发送。

## 一份文档会经历什么

1. 用所选的解析器**解析**成带标题和页码的块。
2. **识别**：来源 URI 是文件本身，或者用 `--identity basic` 时，取自其首页中找到的 DOI 或 arXiv id。
3. 沿着章节**切块**（`--chunk-chars`），每个块都保留它的标题链。
4. 按块和配置档**抽取**。模型必须给出一段从该块中复制出来的引文。
5. **落地核对**：引文必须逐字出现在该块中（空白除外）。未通过的陈述有一轮返工，之后就被丢弃。存储的引文是文档自己的文字。
6. **提议**：每条陈述一个 `add` 操作，证据 = 来源（uri、locator、quote、title、content hash），随后是概览。主体的概念会先被解析（见下文）。

当一个以前导入过其 URI 的文件，哈希值不同了，客户端会把旧来源报告为 `changed`（`/v1/sources/check`），于是引用旧读取内容的事实会得到审核任务。

## 参考资料，以及指向它的指针

数据手册里的数百条条目是文档，不是记忆。因此，引用了来源的事实是**参考资料**（`scope: reference`）：召回、简报和检查从不送达它，所以它不会挤掉你告诉智能体的内容。它按需查阅：

```bash
curl -X POST $ONTYRA_URL/v1/consult -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"query": "light sleep current", "topic": "esp32-s3-wroom-1"}'
```

（即 MCP 服务器和插件的 `consult` 工具）。命中的结果带有其来源、locator 和引文。

对每份文档，客户端还会写入一条简短的**概览**记忆，其作用域为 `topical`，并且 `about` 该文档的*主题*（一个概念，由模型根据标题和开头命名，或用 `--topic` 和 `--alias` 指定；别名是消息可以用来称呼它的名字）。它说明文档涵盖什么，以及其中的条目可以去查阅。只有当工作与该主题相关时才会送达：

- 消息提到了这个主题（或别名）：“what current does the ESP32-S3-WROOM-1 draw in sleep”；
- 工作发生在一个登记为使用它的地方：

```bash
curl -X POST $ONTYRA_URL/v1/resources -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"uri": "file://myhost/home/me/firmware", "instance_of": ["esp32-s3-wroom-1"]}'
```

在其他地方，概览和条目都不会出现。`--scope contextual` 会让这些条目重新可以按其文本被召回，这对文档来说通常不是你想要的。

## 选项

| 选择 | 可选值 |
|---|---|
| 解析器（`--parser`） | `builtin`（txt、md、html、带文字层的 PDF；不需要服务）、`mineru`（通过 MinerU 的 API 做版面感知的 PDF 解析，`--mineru-url`）、`auto`（配置了 MinerU 时，PDF 用 MinerU）。更多解析器通过 `ontyra_ingest.parsers` 入口点组注册。 |
| 配置档（`-p`） | `claims`（自成一体的陈述）、`datasets`（文档所引入或使用的数据集，按可复用性打 0-5 分，`--min-usefulness`） |
| 身份识别（`--identity`） | `file`、`basic`（取自文本中的 DOI / arXiv）；更多通过 `ontyra_ingest.identity` 组注册 |
| 概念（`--concepts`） | `existing`（复用本体中已有的概念，否则用文档自己的概念）或 `create`（定义新的主体）。数据集总是得到它自己的概念，位于 `dataset` 之下。 |
| 模型 | `--model`、`--llm-url`：任何兼容 OpenAI 的聊天端点 |

客户端需要 `global_write` 才能定义概念。在默认策略下，智能体带来源的 `fact` 会立即提交（`vetting=auto`）；其他的都在审核队列中等待。
