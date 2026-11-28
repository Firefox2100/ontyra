"""Retrieval over ingested knowledge: for each question about a document, does `consult` return
the entries that hold the answer? Runs against a daemon that has the document ingested, so it
needs `BENCH_KNOWLEDGE_URL` (and `BENCH_KNOWLEDGE_TOKEN`); no model is called. Absent-fact
questions are not scored here (they need an answering model)."""

from __future__ import annotations

import os

import httpx
import pytest

from evaluation_test.framework.knowledge import first_rank
from evaluation_test.framework.models import load_knowledge_cases

URL = os.environ.get("BENCH_KNOWLEDGE_URL", "")
LIMITS = (1, 3, 8)


@pytest.mark.skipif(
    not URL, reason="set BENCH_KNOWLEDGE_URL to a daemon with the datasheet ingested"
)
async def test_consult_finds_the_answer(settings, recorder, capsys) -> None:
    token = os.environ.get("BENCH_KNOWLEDGE_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    cases = [c for c in load_knowledge_cases() if not c.absent]
    cases = [c for c in cases if not settings.only or settings.only in c.id]
    rows: list[dict] = []
    async with httpx.AsyncClient(base_url=URL, headers=headers, timeout=60) as client:
        for case in cases:
            r = await client.post("/v1/consult", json={"query": case.q, "limit": max(LIMITS)})
            r.raise_for_status()
            hits = r.json()["hits"]
            texts = [
                h["text"] + " " + " ".join(s.get("quote") or "" for s in h["sources"]) for h in hits
            ]
            rank = first_rank(case, texts)
            rows.append(
                {
                    "variant": "consult",
                    "id": case.id,
                    "tags": [case.kind],
                    "split": case.split,
                    "passed": rank is not None and rank <= 8,
                    "rank": rank,
                    "hits": [h["text"] for h in hits],
                }
            )
    path = recorder("knowledge", rows)
    with capsys.disabled():
        line = ", ".join(
            f"hit@{k} {sum(1 for r in rows if r['rank'] and r['rank'] <= k)}/{len(rows)}"
            for k in LIMITS
        )
        print(f"\nknowledge consult: {line}  -> {path}")
