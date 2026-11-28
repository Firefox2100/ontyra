---
title: 批量导入
---

`ontyra-ingest` 是一个独立的命令行客户端，位于 `integrations/ingest/`，与 Ontyra 服务分开发布。它读取文档，让语言模型提取其中的陈述，再用文档原文逐条核对。通过核对的内容会作为带来源引用的事实提交给 Ontyra。服务端无需了解这个客户端，只会看到一个普通智能体正在提交带来源的变更，因此[策略](./knowledge.md)、审定和审核仍按正常流程执行。

它建立的是**知识索引，不是文档索引**。除了每条事实引用的原文片段，Ontyra 不会保存来源内容。全文搜索仍应由文档检索系统负责。

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

每项设置都有对应的命令行选项，可通过 `--help` 查看。命令可以安全地重复运行：模型对各文本块的回答和已经接受的提案都保存在 `~/.local/state/ontyra-ingest/` 中。中断后可以继续，已经完成的内容不会再次处理。未通过引文核对的陈述会写入其中的 `rejected/` 目录，不会发送给 Ontyra。

## 一份文档会经历什么

1. 用所选的解析器**解析**成带标题和页码的块。
2. **识别**：来源 URI 是文件本身，或者用 `--identity basic` 时，取自其首页中找到的 DOI 或 arXiv id。
3. 沿着章节**切块**（`--chunk-chars`），每个块都保留它的标题链。
4. 按块和配置档**抽取**。模型必须给出一段从该块中复制出来的引文。
5. **原文核对**：引文必须逐字出现在该文本块中，空白差异除外。未通过的陈述会让模型修正一次，再次失败就丢弃。最终保存的引文始终来自文档原文。
6. **提交提案**：每条陈述对应一个 `add` 操作，证据中包含来源的 uri、locator、quote、title 和 content hash，最后再写入概览。提交前会先解析主体对应的概念，详见下文。

当一个以前导入过其 URI 的文件，哈希值不同了，客户端会把旧来源报告为 `changed`（`/v1/sources/check`），于是引用旧读取内容的事实会得到审核任务。

## 参考资料，以及指向它的指针

数据手册中的数百条内容属于参考资料，不应像用户偏好或规则那样主动出现。因此，带来源引用的事实使用 `scope: reference`。它们不会进入自动调取、简报或检查结果，也不会占用这些内容的预算。需要时使用 `consult` 查询：

```bash
curl -X POST $ONTYRA_URL/v1/consult -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"query": "light sleep current", "topic": "esp32-s3-wroom-1"}'
```

（即 MCP 服务器和插件的 `consult` 工具）。命中的结果带有其来源、locator 和引文。

客户端还会为每份文档保存一条简短的**概览**记忆，作用域为 `topical`，并通过 `about` 关联文档主题。主题是一个概念，可以由模型根据标题和开头生成，也可以通过 `--topic` 和 `--alias` 指定。概览说明文档涵盖哪些内容，并提醒智能体可以按需查询。只有当前工作与该主题有关时，概览才会出现：

- 消息提到了这个主题（或别名）：“what current does the ESP32-S3-WROOM-1 draw in sleep”；
- 工作发生在一个登记为使用它的地方：

```bash
curl -X POST $ONTYRA_URL/v1/resources -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"uri": "file://myhost/home/me/firmware", "instance_of": ["esp32-s3-wroom-1"]}'
```

在其他情境中，概览和文档条目都不会出现。使用 `--scope contextual` 后，条目可以根据文本相似度被自动调取，但这通常不适合文档资料。

## 选项

| 选择 | 可选值 |
|---|---|
| 解析器（`--parser`） | `builtin`（txt、md、html、带文字层的 PDF；不需要服务）、`mineru`（通过 MinerU 4.1 及以上版本的 API 做版面感知的 PDF 解析，`--mineru-url`；用 `ONTYRA_INGEST_MINERU_TIER` 选择 flash、basic、standard 或 advanced）、`auto`（配置了 MinerU 时，PDF 用 MinerU）。更多解析器通过 `ontyra_ingest.parsers` 入口点组注册。 |
| 配置档（`-p`） | `claims`（自成一体的陈述）、`datasets`（文档所引入或使用的数据集，按可复用性打 0-5 分，`--min-usefulness`） |
| 身份识别（`--identity`） | `file`、`basic`（取自文本中的 DOI / arXiv）；更多通过 `ontyra_ingest.identity` 组注册 |
| 概念（`--concepts`） | `existing`（复用本体中已有的概念，否则用文档自己的概念）或 `create`（定义新的主体）。数据集总是得到它自己的概念，位于 `dataset` 之下。 |
| 模型 | `--model`、`--llm-url`：任何兼容 OpenAI 的聊天端点 |

客户端需要 `global_write` 才能定义概念。在默认策略下，智能体带来源的 `fact` 会立即提交（`vetting=auto`）；其他的都在审核队列中等待。
