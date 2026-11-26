---
title: Bulk ingestion
---

`ontyra-ingest` is a standalone command-line client (in `integrations/ingest/`, released apart from
the daemon). It reads documents, asks a language model for the statements they make, checks every
statement against the document's own words, and proposes the survivors to the daemon as facts that
cite their source. The daemon does not know it exists: it sees an ordinary agent writing sourced
proposals, so [policy](./knowledge.md), vetting and review apply as usual.

It indexes **knowledge, not documents**: nothing of the source is stored beyond the quote each fact
cites. Search over the full text remains a job for a document-search system.

## Install and run

```bash
cd integrations/ingest && uv sync
export ONTYRA_URL=http://127.0.0.1:8787 ONTYRA_TOKEN=...      # same names the plugins use
export ONTYRA_INGEST_LLM_URL=http://127.0.0.1:8081/v1          # any OpenAI-compatible endpoint
export ONTYRA_INGEST_LLM_MODEL=qwen3.6-35b-a3b-uncensored-1m

uv run ontyra-ingest run papers/ --dry-run        # extract and check, write nothing
uv run ontyra-ingest run papers/ -p claims -p datasets
uv run ontyra-ingest parse paper.pdf              # how a file is read and cut into chunks
uv run ontyra-ingest plugins                      # parsers, identity finders, profiles
```

Every setting is also an option (`--help`). Runs are safe to repeat: the model's answers per chunk
and the proposals already accepted are kept in `~/.local/state/ontyra-ingest/`, so an interrupted
run resumes and a finished one costs nothing. Statements that fail the quote check are logged in
`rejected/` there, never sent.

## What happens to a document

1. **Parse** with the chosen parser into blocks with headings and pages.
2. **Identify**: the source URI is the file, or a DOI or arXiv id found in its first page with
   `--identity basic`.
3. **Chunk** along sections (`--chunk-chars`), each chunk keeping its heading chain.
4. **Extract** per chunk and profile. The model must give a quote copied from the chunk.
5. **Ground**: the quote must occur in the chunk, word for word (whitespace aside). A statement
   that fails gets one rework round, then is dropped. The stored quote is the document's own text.
6. **Propose**: one `add` op per statement, evidence = the source (uri, locator, quote, title,
   content hash), then the overview. Concepts for subjects are resolved first (below).

When a file whose URI was ingested before has a different hash, the client reports the old source
as `changed` (`/v1/sources/check`), so the facts cited from the old reading get review tasks.

## Reference material, and a pointer to it

A datasheet's hundreds of entries are documentation, not memory. A fact that cites a source is
therefore **reference material** (`scope: reference`): recall, briefing and check never deliver it,
so it cannot crowd out what you told the agent. It is looked up on demand:

```bash
curl -X POST $ONTYRA_URL/v1/consult -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"query": "light sleep current", "topic": "esp32-s3-wroom-1"}'
```

(the `consult` tool of the MCP server and the plugins). Hits carry their source, locator and quote.

For each document the client also writes one short **overview** memory, `topical` and `about` the
document's *topic* (a concept, named by the model from the title and opening, or given with
`--topic` and `--alias`; aliases are what a message can name it by). It says what the document
covers and that its entries can be looked up. It is delivered only when the work ties to the topic:

- the message names the topic (or an alias): "what current does the ESP32-S3-WROOM-1 draw in sleep";
- the work happens in a place registered as using it:

```bash
curl -X POST $ONTYRA_URL/v1/resources -H "Authorization: Bearer $ONTYRA_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"uri": "file://myhost/home/me/firmware", "instance_of": ["esp32-s3-wroom-1"]}'
```

Elsewhere neither the overview nor the entries appear. `--scope contextual` makes the entries
recallable by their text again, which is rarely what you want for documentation.

## Choices

| Choice | Options |
|---|---|
| Parser (`--parser`) | `builtin` (txt, md, html, text-layer PDF; no service), `mineru` (layout-aware PDF through MinerU's API, `--mineru-url`), `auto` (MinerU for PDFs when configured). More register through the `ontyra_ingest.parsers` entry-point group. |
| Profiles (`-p`) | `claims` (self-contained statements), `datasets` (datasets a document introduces or uses, scored 0-5 for reuse, `--min-usefulness`) |
| Identity (`--identity`) | `file`, `basic` (DOI / arXiv from the text); more via the `ontyra_ingest.identity` group |
| Concepts (`--concepts`) | `existing` (reuse a concept the ontology already has, else the document's own concept) or `create` (define new subjects). Datasets always get a concept of their own, under `dataset`. |
| Model | `--model`, `--llm-url`: any OpenAI-compatible chat endpoint |

The client needs `global_write` to define concepts. With the default policy an agent's sourced `fact`
commits at once (`vetting=auto`); anything else waits in the review queue.
