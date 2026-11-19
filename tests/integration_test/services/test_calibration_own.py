"""The label-free check on a store: read-only, and it reports where the threshold sits."""

from __future__ import annotations

from ontyra.app import Application
from ontyra.calibration import own
from ontyra.models.recall import RecallSettings


async def test_the_own_store_check_reads_only_and_reports(make_app, remember) -> None:
    app: Application = await make_app(recall=RecallSettings(engine="fusion"))
    topics = [
        "tidewatch releases are tagged on the main branch after the changelog is updated",
        "the oven in the flat runs fifteen degrees hotter than the dial says",
        "invoices from the print shop are paid on the first of the month",
        "use pytest fixtures from conftest instead of setup methods in every test file",
        "the sourdough starter is fed every morning with equal weights of flour and water",
        "backups of the home server run at three in the morning to the external drive",
        "the garden hose is stored in the shed because frost splits the fittings in winter",
        "meeting notes for the quarterly review go into the shared planning folder",
        "the bicycle chain needs oil after every ride in the rain to avoid rust",
        "never commit credentials to the repository; keep them in the password manager",
    ]
    for text in topics:
        await remember("fact", text)
    await app.indexer.backfill()
    before = await app.recall.store.select("recall_trace", limit=10)
    report = await own.probe_app(app, 10)
    assert report["memories"] == 10 and report["probes"] == 10
    assert report["own_memory_first"] >= 0.8  # its own first words find a memory (hash embedder)
    assert report["notes"] and "abstain_cosine" in own.render(report)
    assert await app.recall.store.select("recall_trace", limit=10) == before  # nothing recorded
