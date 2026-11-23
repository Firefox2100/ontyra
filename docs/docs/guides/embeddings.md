---
title: Embeddings, reranking and decisions
---

## Embeddings

By default Ontyra uses a built-in `hash` embedder: no model and no network, roughly lexical. It is
fine to try the system. For real semantic recall, point it at an embedding model through
LangChain; any OpenAI-compatible server works (LocalAI, llama.cpp, vLLM, the OpenAI API):

```bash
uv sync --extra openai          # installs langchain-openai
export ONTYRA_EMBEDDING__PROVIDER=langchain
export ONTYRA_EMBEDDING__CLASS_PATH=langchain_openai.OpenAIEmbeddings
export ONTYRA_EMBEDDING__KWARGS='{"model":"qwen3-embedding-8b","base_url":"http://127.0.0.1:8081/v1","api_key":"none","check_embedding_ctx_length":false}'
export ONTYRA_EMBEDDING__DIMENSION=4096        # the model's output size
```

Changing the dimension changes the vector index. In this pre-release the way to apply it is to
wipe and re-embed (`uv run ontyra db wipe --yes`, then restart).

Some models want an instruction in front of the query. `ONTYRA_EMBEDDING__QUERY_PREFIX` sets it
(for Qwen3-Embedding: `Instruct: Given a task or message from a developer, retrieve the stored notes that apply\nQuery:`).

## Recall engine and abstaining

`ONTYRA_RECALL__ENGINE=fusion` is the engine the benchmarks favoured (`legacy` is the default in
code). It stays silent when even the best match is not similar enough:
`ONTYRA_RECALL__ABSTAIN_COSINE`, which depends on the embedding model (0.452 was calibrated for
qwen3-embedding-8b). If recall returns nothing, or returns noise, tune it.

## Reranker (optional)

`ONTYRA_RERANK__PROVIDER=http` with `ONTYRA_RERANK__URL` uses a `{url}/rerank` API (LocalAI,
Jina); it is hosted and cannot be retrained. `worker` is the same API served by our own
[reranker worker](#the-reranker-worker), which can be retrained from feedback. If the reranker is
busy, slow (`ONTYRA_RERANK__TIMEOUT`) or down, recall uses its plain ranking for that request.

### The reranker worker

`ontyra worker` is a separate process that owns the GPU and serves the same standard rerank API
(`POST /rerank` or `/v1/rerank`: `query`, `documents`, `top_n`, `return_documents`; Jina, Cohere
and LocalAI clients work unchanged), so the daemon, or anything else, reaches it like a hosted
reranker:

```bash
uv sync --extra worker            # after installing the torch build for your GPU
ONTYRA_WORKER__MODEL=Qwen/Qwen3-Reranker-0.6B ontyra worker
# daemon:
ONTYRA_RERANK__PROVIDER=worker  ONTYRA_RERANK__URL=http://127.0.0.1:8788/v1   # or http, to only serve
```

`ONTYRA_WORKER__MODEL` is a Hugging Face id or a local directory of safetensors weights. A
Qwen3-style reranker is converted exactly to a one-logit head (same scores, less memory); a
sequence-classification model with one label (bge-reranker, MiniLM and other cross-encoders) is
used as it is. Device and dtype default to `auto` (bf16 on cards that have it, fp16 on older ones,
fp32 on CPU), work on Linux and Windows, and need only PyTorch and transformers. Install the
PyTorch build that supports your card first: the default wheels do not cover older GPUs (a V100
needs the cu126 build); the worker checks this at start and says so. Memory is managed by the
worker: requests that arrive together share a forward pass, a full queue answers `503`, and idle
weights move to host memory (`OFFLOAD_AFTER_SECONDS`) and are then dropped
(`FREE_AFTER_SECONDS`), reloading on the next request. `GET /status` reports the device, what the
machine can do (and why not, when it cannot) and counters. Set `ONTYRA_WORKER__TOKEN` to require a
bearer token. Retraining is described in [Learning from feedback](./feedback-learning.md#retraining). Versions of the model live under `ONTYRA_WORKER__MODEL_DIR`; changing the configured
model starts from that model's own baseline.

## Decision model (optional)

A decision model reads the top candidates and the situation, and returns which matter, as
probabilities. `ONTYRA_DECISION__PROVIDER=systemone` with `ONTYRA_DECISION__URL` calls a server's
`POST {url}/systemone` (for example [Kev-9B](https://huggingface.co/tt-hous/kev-9b) behind
LocalAI or llama.cpp's server). Requires the `fusion` engine. It is advisory: if it is
unreachable, recall returns the deterministic result.

### Skipping recall for pleasantries

With `ONTYRA_RECALL__FILLER_GATE=true` the same model is asked, once, whether a short message
(at most `ONTYRA_RECALL__FILLER_MAX_WORDS`, 8) is a pure pleasantry ("thanks", "ok", "lol"). When
its probability reaches `ONTYRA_RECALL__FILLER_THRESHOLD` (0.7) the lookup is skipped and recall
stays silent. The model is asked without any conversation history, a failure or a refusal means "not
filler", and a message with a real request after a pleasantry is looked up as usual. A bare "ok" or "yes" that answers the agent's own question reads as filler too (the model sees no conversation): usually right, since there is nothing to look up, but a go-ahead before a risky action is then not matched against a rule; the agent's own `check` before such actions is the safety net. On the
decision benchmark (kev-9b, 82 messages including thanks-then-request near misses) 0.7 skipped 78%
of the pleasantries and no real request. It is off by default; the saving is one lookup per
pleasantry, at the price of one 0.2 s model call per short message.
