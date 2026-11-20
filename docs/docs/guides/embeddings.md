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
Jina); `cross_encoder` runs a model in-process (`uv sync --extra rerank-local`, CPU unless
`ONTYRA_RERANK__DEVICE` is set). The `cross_encoder` reranker can be retrained from feedback;
hosted ones cannot.

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
