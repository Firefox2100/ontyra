---
title: 嵌入、重排与决策
---

## 嵌入

Ontyra 默认使用内置的 `hash` 嵌入器，不需要模型或网络，效果接近词法检索，适合初次试用。若要按语义查找记忆，请通过 LangChain 连接嵌入模型。LocalAI、llama.cpp、vLLM 和 OpenAI API 等兼容 OpenAI 接口的服务都可以使用：

```bash
uv sync --extra openai          # 安装 langchain-openai
export ONTYRA_EMBEDDING__PROVIDER=langchain
export ONTYRA_EMBEDDING__CLASS_PATH=langchain_openai.OpenAIEmbeddings
export ONTYRA_EMBEDDING__KWARGS='{"model":"qwen3-embedding-8b","base_url":"http://127.0.0.1:8081/v1","api_key":"none","check_embedding_ctx_length":false}'
export ONTYRA_EMBEDDING__DIMENSION=4096        # 模型的输出维度
```

改变维度会改变向量索引。在这个发布前阶段，应用的办法是清空并重新嵌入（`uv run ontyra db wipe --yes`，然后重启）。

有些模型希望在查询前面加一段指令。`ONTYRA_EMBEDDING__QUERY_PREFIX` 用来设置它（对 Qwen3-Embedding：`Instruct: Given a task or message from a developer, retrieve the stored notes that apply\nQuery:`）。

## 调取引擎与静默阈值

基准测试中，`ONTYRA_RECALL__ENGINE=fusion` 的效果更好，但代码默认仍使用 `legacy`。当最佳匹配的相似度也不足时，`fusion` 不会返回结果。这个行为由 `ONTYRA_RECALL__ABSTAIN_COSINE` 控制，具体数值取决于嵌入模型；qwen3-embedding-8b 的校准值为 0.452。如果系统经常没有结果或返回无关内容，请调整该值。

## 重排器（可选）

将 `ONTYRA_RERANK__PROVIDER` 设为 `http` 并配置 `ONTYRA_RERANK__URL`，即可调用 LocalAI 或 Jina 等服务提供的 `{url}/rerank` API。这类托管服务不能由 Ontyra 重新训练。`worker` 使用同一套 API，但由 Ontyra 自带的[重排器 worker](#the-reranker-worker) 提供，并支持根据反馈重新训练。如果重排器繁忙、超时或不可用，本次请求会回退到 Ontyra 的原始排序。

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

决策模型会读取排名靠前的候选和当前情境，并用概率表示各候选是否重要。设置 `ONTYRA_DECISION__PROVIDER=systemone` 和 `ONTYRA_DECISION__URL` 后，Ontyra 会调用 `POST {url}/systemone`，例如部署在 LocalAI 或 llama.cpp 后的 [Kev-9B](https://huggingface.co/tt-hous/kev-9b)。此功能需要 `fusion` 引擎。决策模型只是附加信号；连接失败时，系统仍会返回由确定性流程计算的结果。

### 跳过纯寒暄消息

设置 `ONTYRA_RECALL__FILLER_GATE=true` 后，会向同一个模型询问一次：一条短消息（最多 `ONTYRA_RECALL__FILLER_MAX_WORDS`，即 8 个词）是不是纯粹的寒暄（“thanks”“ok”“lol”）。当它的概率达到 `ONTYRA_RECALL__FILLER_THRESHOLD`（0.7）时，就跳过查找，召回保持沉默。向模型提问时不带任何对话历史，失败或拒绝都表示“不是寒暄”，寒暄之后带着真实请求的消息会照常查找。一个单独的“ok”或“yes”，如果是在回答智能体自己的问题，也会被读作寒暄（模型看不到对话）：通常没错，因为本来就没什么可查的，但在有风险的操作之前给出的许可，就不会再与某条规则匹配；智能体在这类操作之前自己做的 `check` 是安全网。在决策基准上（kev-9b，82 条消息，包括“先说谢谢再提请求”这类容易混淆的情况），0.7 跳过了 78% 的寒暄，且没有跳过任何真实请求。它默认关闭；好处是每次寒暄省掉一次查找，代价是每条短消息多一次 0.2 秒的模型调用。
