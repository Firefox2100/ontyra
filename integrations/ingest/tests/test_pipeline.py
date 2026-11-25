from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeDaemon

from ontyra_ingest.config import Settings
from ontyra_ingest.daemon import Daemon
from ontyra_ingest.llm import Llm
from ontyra_ingest.pipeline import Pipeline

DOC = """# Widgets

## Limits

The widget must not be used above 80 degrees Celsius. It may be stored at any temperature.

## Data

We release the WidgetBench corpus, a collection of 12,000 annotated widget photographs.
"""


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    path = tmp_path / "widgets.md"
    path.write_text(DOC)
    return path


def answer(calls: list[str]) -> Callable[[str, str], Any]:
    def respond(system: str, user: str) -> Any:
        calls.append(user)
        if '"claims"' in system:
            if "80 degrees" in user:
                return {
                    "claims": [
                        {
                            "subject": "Widget",
                            "predicate": "must not be used above",
                            "object": "80 degrees Celsius",
                            "text": "A widget must not be used above 80 degrees Celsius.",
                            "quote": "must not be used above 80 degrees Celsius",
                        },
                        {
                            "subject": "Widget",
                            "predicate": "is",
                            "object": "waterproof",
                            "text": "A widget is waterproof.",
                            "quote": "The widget is completely waterproof",
                        },
                    ]
                }
            return {"claims": []}
        if "WidgetBench" in user:
            return {
                "datasets": [
                    {
                        "name": "WidgetBench",
                        "description": "12,000 annotated widget photographs.",
                        "url": "",
                        "usefulness": 5,
                        "quote": "the WidgetBench corpus, a collection of 12,000 annotated widget photographs",
                    },
                    {
                        "name": "Passing",
                        "description": "",
                        "usefulness": 1,
                        "quote": "WidgetBench corpus",
                    },
                ]
            }
        return {"datasets": []}

    return respond


async def test_ingest_grounds_submits_and_resumes(
    settings: Settings,
    daemon: Daemon,
    fake_daemon: FakeDaemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:
    calls: list[str] = []
    pipeline = Pipeline(settings, make_llm(answer(calls)), daemon)
    report = await pipeline.ingest(doc)

    assert report.errors == []
    # the invented quote is refused after one rework round; the weak dataset is dropped
    assert (report.grounded, report.rejected) == (2, 1)
    assert report.committed == 2
    ops = [op for p in fake_daemon.proposals for op in p["ops"]]
    texts = {op["fields"]["text"] for op in ops}
    assert texts == {
        "A widget must not be used above 80 degrees Celsius.",
        "WidgetBench is a dataset: 12,000 annotated widget photographs.",
    }
    claim = next(op for op in ops if "80 degrees" in op["fields"]["text"])
    source = claim["evidence"][0]["source"]
    assert source["uri"] == doc.resolve().as_uri()
    assert source["quote"] == "must not be used above 80 degrees Celsius"
    assert source["locator"] == "Widgets > Limits"
    assert source["content_hash"].startswith("sha256:")
    # an unknown subject falls back to the document's concept; a dataset gets its own
    assert claim["fields"]["subject"] == "concept:widgets"
    dataset = next(op for op in ops if "WidgetBench" in op["fields"]["text"])
    assert dataset["fields"]["subject"] == "concept:widgetbench"
    assert "concept:dataset" in fake_daemon.concepts.values()

    first_calls, first_proposals = len(calls), len(fake_daemon.proposals)
    again = await Pipeline(settings, make_llm(answer(calls)), daemon).ingest(doc)
    assert len(calls) == first_calls  # nothing asked of the model again
    assert len(fake_daemon.proposals) == first_proposals  # nothing sent again
    assert again.skipped == 2 and again.committed == 0


async def test_dry_run_writes_nothing_to_the_daemon(
    settings: Settings,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    fake_daemon: FakeDaemon,
    doc: Path,
) -> None:
    report = await Pipeline(settings, make_llm(answer([])), None).ingest(doc, dry_run=True)
    assert report.grounded == 2 and report.committed == 0
    assert fake_daemon.proposals == []


async def test_a_refused_statement_does_not_sink_the_others(
    settings: Settings,
    daemon: Daemon,
    fake_daemon: FakeDaemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:
    fake_daemon.reject_containing = "WidgetBench"
    report = await Pipeline(settings, make_llm(answer([])), daemon).ingest(doc)
    assert (report.committed, report.refused) == (1, 1)
    assert any("bad_source" in e for e in report.errors)


async def test_held_proposals_are_counted_as_held(
    settings: Settings,
    daemon: Daemon,
    fake_daemon: FakeDaemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:
    fake_daemon.decision = 202
    report = await Pipeline(settings, make_llm(answer([])), daemon).ingest(doc)
    assert (report.held, report.committed) == (2, 0)


async def test_a_changed_file_flags_its_earlier_source(
    settings: Settings,
    daemon: Daemon,
    fake_daemon: FakeDaemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:
    pipeline = Pipeline(settings, make_llm(answer([])), daemon)
    await pipeline.ingest(doc)
    assert fake_daemon.checks == []
    doc.write_text(DOC + "\nA closing remark with nothing in it.\n")
    report = await pipeline.ingest(doc)
    assert [c["state"] for c in fake_daemon.checks] == ["changed"]
    assert report.changed_tasks == 2


async def test_create_mode_defines_new_subjects(
    settings: Settings,
    daemon: Daemon,
    fake_daemon: FakeDaemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:
    created = settings.model_copy(update={"concepts": "create"})
    await Pipeline(created, make_llm(answer([])), daemon).ingest(doc)
    assert "concept:widget" in fake_daemon.concepts.values()
    claim = next(
        op for p in fake_daemon.proposals for op in p["ops"] if "80 degrees" in op["fields"]["text"]
    )
    assert claim["fields"]["subject"] == "concept:widget"


async def test_a_dead_model_is_reported_and_retried_next_time(
    settings: Settings,
    daemon: Daemon,
    make_llm: Callable[[Callable[[str, str], Any]], Llm],
    doc: Path,
) -> None:

    from ontyra_ingest.llm import LlmError

    class Dead(Llm):
        async def json(self, system: str, user: str) -> Any:
            raise LlmError("down")

    live = make_llm(answer([]))
    dead = Dead(settings, live._client)  # pyright: ignore[reportPrivateUsage]
    report = await Pipeline(settings, dead, daemon).ingest(doc)
    assert report.errors and report.committed == 0
    ok = await Pipeline(settings, live, daemon).ingest(doc)
    assert ok.committed == 2
