"""The in-process cross-encoder: scoring, fine-tuning on feedback pairs, versions, rollback.

Needs `sentence-transformers` and the generic base model (cached locally after first use);
skipped when either is unavailable (no network, extra not installed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from ontyra.adapters import CrossEncoderReranker
from ontyra.models.feedback import RerankExample, TrainOptions


@pytest.fixture
def reranker(tmp_path: Path) -> CrossEncoderReranker:
    rr = CrossEncoderReranker(model_dir=tmp_path / "models", device="cpu", batch_size=8)
    try:
        rr._load()  # pyright: ignore[reportPrivateUsage]
    except Exception as exc:
        pytest.skip(f"base cross-encoder unavailable: {exc}")
    return rr


def pairs() -> list[RerankExample]:
    topics = {
        "release": "Release tidewatch: bump the version, tag it, push the tag",
        "oven": "The oven runs hotter than its dial says",
        "tests": "Use pytest for tests in tidewatch",
        "garden": "Water the vegetable beds in the early morning",
    }
    queries = {
        "release": ["how do I cut a release", "ship a new version"],
        "oven": ["why do loaves burn", "oven temperature wrong"],
        "tests": ["write tests for the endpoint", "which test framework"],
        "garden": ["when should I water", "garden watering routine"],
    }
    out: list[RerankExample] = []
    for topic, qs in queries.items():
        for q in qs:
            for other, text in topics.items():
                out.append(
                    RerankExample(query=q, text=text, relevance=1.0 if other == topic else 0.0)
                )
    return out


async def test_it_scores_pairs_and_ranks_the_obvious_match_first(
    reranker: CrossEncoderReranker,
) -> None:
    scores = await reranker.rerank(
        "how do I cut a release", ["The oven runs hot", "Release it: bump the version and tag"]
    )
    assert len(scores) == 2 and scores[1] > scores[0]
    assert await reranker.rerank("q", []) == []


async def test_training_saves_a_version_keeps_the_base_and_reports_held_out_metrics(
    reranker: CrossEncoderReranker, tmp_path: Path
) -> None:
    result = await reranker.train(
        pairs(), TrainOptions(epochs=2, batch_size=8, validation_fraction=0.25)
    )
    assert result.version == "v1" and result.examples == 32
    assert {"auc_before", "auc_after", "best_epoch", "validation_pairs"} <= set(result.metrics)
    assert (tmp_path / "models" / "v1").is_dir()
    assert reranker.versions() == ["base", "v1"]
    # a candidate that did not beat the starting point is saved but not activated
    assert result.activated == (reranker.version == "v1")
    await reranker.activate("v1")
    assert reranker.version == "v1"
    assert len(await reranker.rerank("q", ["a", "b"])) == 2
    await reranker.activate("base")
    assert reranker.version == "base"
    # the choice persists for the next process
    again = CrossEncoderReranker(model_dir=tmp_path / "models", device="cpu")
    assert again.version == "base"


async def test_unknown_versions_are_refused(reranker: CrossEncoderReranker) -> None:
    with pytest.raises(ValueError, match="unknown reranker version"):
        await reranker.activate("v99")
