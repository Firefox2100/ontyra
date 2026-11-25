# ontyra-ingest

Bulk-ingestion client for Ontyra. Reads documents, extracts statements with a language model,
checks each against the document's own words, and proposes the survivors to the daemon as sourced
facts over REST. It never imports `ontyra`; the only coupling is the API.

```bash
uv sync
uv run ontyra-ingest run papers/ --dry-run
uv run ontyra-ingest run papers/ -p claims -p datasets --parser auto --mineru-url http://host:10010
```

Configuration is environment (`ONTYRA_URL`, `ONTYRA_TOKEN`, `ONTYRA_INGEST_*`) and options; see
`uv run ontyra-ingest run --help` and the documentation guide *Bulk ingestion*.

Extending: parsers register in the `ontyra_ingest.parsers` entry-point group (name → factory taking
`Settings`, returning an object with `name`, `suffixes` and `async parse(path) -> ParsedDocument`),
identity finders in `ontyra_ingest.identity` (a function `ParsedDocument -> {"doi": …}`). A profile
is a prompt in `prompts/` plus a reader in `profiles.py`.

Develop: `uv run pytest`, `uv run ruff check .`, `uv run pyright`.
