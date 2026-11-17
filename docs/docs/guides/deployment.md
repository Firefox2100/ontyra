---
title: Deploying with Docker
---

The repository ships a `Dockerfile` and a `compose.yaml` that run SurrealDB and the daemon
together, with state in a bind-mounted `./data` directory.

## Run it

```bash
git clone https://github.com/Firefox2100/ontyra.git ontyra && cd ontyra
cp .env.example .env            # set ONTYRA_DB_PASSWORD
docker build --build-arg VERSION=0.1.0 -t ontyra:local .
docker compose up -d
curl -s localhost:8787/health
```

To keep the deployment outside the checkout, copy `compose.yaml` and `.env` to a directory of
your choice, leave `image: ontyra:local` and run `docker compose up -d` there. After pulling a
new version, rebuild the image and run `docker compose up -d` again; the data stays in `./data`.

## What the compose file sets up

- SurrealDB 4 on `127.0.0.1:18787`, daemon on `127.0.0.1:8787` (`ONTYRA_PORT`,
  `ONTYRA_DB_PORT` in `.env`). Both use host networking and listen on loopback only, because
  there is no authentication yet. Do not publish the port to a network you do not trust.
- The MCP endpoint (`/mcp/`, lite tool profile) and the operator console (`/ui/`).
- Agents' writes wait for review; a human's own writes commit at once
  ([Review and policy](./review.md)).
- The `fusion` recall engine, with embeddings from an OpenAI-compatible server.

## Pointing it at your embedding model

The compose file expects an embedding model at `ONTYRA_EMBED_URL` (default
`http://127.0.0.1:8081/v1`, model `ONTYRA_EMBED_MODEL` = `qwen3-embedding-8b`, dimension
`ONTYRA_EMBED_DIM` = 4096). Without one, embeddings fail quietly and recall falls back to text
search only. Set the three variables in `.env` to your own server and model, and set
`ONTYRA_ABSTAIN_COSINE` for it (see [Embeddings](./embeddings.md)). If you change the model
after storing memories, re-embed: wipe and start over (pre-release).

## Backups

Everything is in `./data/surrealdb`. Stop the stack (`docker compose stop`), copy the directory,
start it again.

## Without Docker

[Quick start](../quick-start.md) runs the daemon straight from a checkout with an embedded
database; for a separate SurrealDB point `ONTYRA_DB__URL` at it (`ws://host:port/rpc`, with `ONTYRA_DB__USERNAME` and `ONTYRA_DB__PASSWORD`). The daemon applies
the schema when it starts.
