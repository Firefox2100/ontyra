---
title: 嵌入、重排与决策
---

## 嵌入

默认情况下 Ontyra 使用内置的 `hash` 嵌入器：不需要模型，也不需要网络，大致相当于词法检索。试用系统足够了。若要真正的语义召回，请通过 LangChain 指向一个嵌入模型；任何兼容 OpenAI 的服务器都可以（LocalAI、llama.cpp、vLLM、OpenAI API）：

```bash
uv sync --extra openai          # 安装 langchain-openai
export ONTYRA_EMBEDDING__PROVIDER=langchain
export ONTYRA_EMBEDDING__CLASS_PATH=langchain_openai.OpenAIEmbeddings
export ONTYRA_EMBEDDING__KWARGS='{"model":"qwen3-embedding-8b","base_url":"http://127.0.0.1:8081/v1","api_key":"none","check_embedding_ctx_length":false}'
export ONTYRA_EMBEDDING__DIMENSION=4096        # 模型的输出维度
```

改变维度会改变向量索引。在这个发布前阶段，应用的办法是清空并重新嵌入（`uv run ontyra db wipe --yes`，然后重启）。

有些模型希望在查询前面加一段指令。`ONTYRA_EMBEDDING__QUERY_PREFIX` 用来设置它（对 Qwen3-Embedding：`Instruct: Given a task or message from a developer, retrieve the stored notes that apply\nQuery:`）。

## 召回引擎与弃权

`ONTYRA_RECALL__ENGINE=fusion` 是基准测试所青睐的引擎（代码中的默认值是 `legacy`）。当即使最佳匹配也不够相似时，它保持沉默：由 `ONTYRA_RECALL__ABSTAIN_COSINE` 控制，它取决于嵌入模型（0.452 是针对 qwen3-embedding-8b 校准的）。如果召回什么也不返回，或者返回噪声，请调整它。

## 重排器（可选）

`ONTYRA_RERANK__PROVIDER=http` 配合 `ONTYRA_RERANK__URL`，使用 `{url}/rerank` API（LocalAI、Jina）；它是托管的，不能重新训练。`worker` 是由我们自己的[重排器 worker](#the-reranker-worker) 提供的同一个 API，它可以根据反馈重新训练。如果重排器繁忙、缓慢（`ONTYRA_RERANK__TIMEOUT`）或宕机，召回就对该请求使用它自己的原始排序。

### 重排器 worker {/* #the-reranker-worker */}

`ontyra worker` 是一个独立的进程，它独占 GPU，并提供同样的标准重排 API（`POST /rerank` 或 `/v1/rerank`：`query`、`documents`、`top_n`、`return_documents`；Jina、Cohere 和 LocalAI 的客户端无需修改即可使用），所以守护进程或其他任何东西都能像使用托管的重排器一样访问它：

```bash
uv sync --extra worker            # 在为你的 GPU 安装好 torch 构建之后
ONTYRA_WORKER__MODEL=Qwen/Qwen3-Reranker-0.6B ontyra worker
# 守护进程：
ONTYRA_RERANK__PROVIDER=worker  ONTYRA_RERANK__URL=http://127.0.0.1:8788/v1   # 或 http，仅作为服务使用
```

`ONTYRA_WORKER__MODEL` 是一个 Hugging Face id，或一个存放 safetensors 权重的本地目录。Qwen3 风格的重排器会被精确地转换为单 logit 头（得分相同，占用内存更少）；只有一个标签的序列分类模型（bge-reranker、MiniLM 和其他交叉编码器）则按原样使用。设备和数据类型默认为 `auto`（在支持的卡上用 bf16，较旧的卡用 fp16，CPU 上用 fp32），可在 Linux 和 Windows 上运行，只需要 PyTorch 和 transformers。请先安装支持你那块卡的 PyTorch 构建：默认的 wheel 不覆盖较旧的 GPU（V100 需要 cu126 构建）；worker 在启动时会检查这一点并给出提示。内存由 worker 管理：同时到达的请求共用一次前向计算，队列已满时回答 `503`，空闲的权重会移到主机内存（`OFFLOAD_AFTER_SECONDS`），之后被丢弃（`FREE_AFTER_SECONDS`），在下一个请求到来时重新加载。`GET /status` 报告设备、这台机器能做什么（做不到时还说明原因），以及计数器。设置 `ONTYRA_WORKER__TOKEN` 可要求 bearer 令牌。重新训练见[从反馈中学习](./feedback-learning.md#retraining-the-reranker)。模型的各个版本保存在 `ONTYRA_WORKER__MODEL_DIR` 之下；更换所配置的模型，会从该模型自己的基线开始。

## 决策模型（可选）

决策模型读取排名靠前的候选和当前情境，以概率的形式返回哪些重要。`ONTYRA_DECISION__PROVIDER=systemone` 配合 `ONTYRA_DECISION__URL`，会调用服务器的 `POST {url}/systemone`（例如部署在 LocalAI 或 llama.cpp 服务器之后的 [Kev-9B](https://huggingface.co/tt-hous/kev-9b)）。需要 `fusion` 引擎。它只是参考性的：如果无法连接，召回会返回确定性的结果。

### 对寒暄跳过召回

设置 `ONTYRA_RECALL__FILLER_GATE=true` 后，会向同一个模型询问一次：一条短消息（最多 `ONTYRA_RECALL__FILLER_MAX_WORDS`，即 8 个词）是不是纯粹的寒暄（“thanks”“ok”“lol”）。当它的概率达到 `ONTYRA_RECALL__FILLER_THRESHOLD`（0.7）时，就跳过查找，召回保持沉默。向模型提问时不带任何对话历史，失败或拒绝都表示“不是寒暄”，寒暄之后带着真实请求的消息会照常查找。一个单独的“ok”或“yes”，如果是在回答智能体自己的问题，也会被读作寒暄（模型看不到对话）：通常没错，因为本来就没什么可查的，但在有风险的操作之前给出的许可，就不会再与某条规则匹配；智能体在这类操作之前自己做的 `check` 是安全网。在决策基准上（kev-9b，82 条消息，包括“先说谢谢再提请求”这类容易混淆的情况），0.7 跳过了 78% 的寒暄，且没有跳过任何真实请求。它默认关闭；好处是每次寒暄省掉一次查找，代价是每条短消息多一次 0.2 秒的模型调用。
