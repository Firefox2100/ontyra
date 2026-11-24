from datetime import UTC, datetime, timedelta

from ontyra.models.recall import RecallItem, SourceTier
from ontyra.models.sources import SourceRef
from ontyra.services.knowledge import corroboration, read_age_days, source_factor, tier_of
from ontyra.services.recall_pack import build_pack, section_of

TIERS = [
    SourceTier(match="https://arxiv.org/", tier="preprint", weight=0.9),
    SourceTier(match="doi:10.", tier="journal", weight=1.2),
]


def ref(uri: str, **kw: object) -> SourceRef:
    return SourceRef(uri=uri, quote="q", **kw)  # type: ignore[arg-type]


def item(text: str, sources: list[SourceRef], kind: str = "fact") -> RecallItem:
    return RecallItem.model_validate(
        {
            "memory": f"memory:{abs(hash(text))}",
            "revision": f"fact_rev:{abs(hash(text))}",
            "kind": kind,
            "text": text,
            "score": 1.0,
            "relevance": 1.0,
            "sources": [s.model_dump() for s in sources],
        }
    )


def test_first_matching_tier_and_neutral_without_one() -> None:
    assert tier_of("https://arxiv.org/abs/1", TIERS) == TIERS[0]
    assert tier_of("file://h/x.md", TIERS) is None
    assert source_factor([], TIERS, 0.05) == (1.0, None)
    assert source_factor([ref("file://h/x.md")], TIERS, 0.05) == (1.0, None)


def test_the_best_source_sets_the_weight_and_extra_sources_corroborate_up_to_a_cap() -> None:
    one = source_factor([ref("https://arxiv.org/abs/1")], TIERS, 0.1)
    assert one == (0.9, "preprint")
    both = source_factor([ref("https://arxiv.org/abs/1"), ref("doi:10.1/x")], TIERS, 0.1)
    assert both[1] == "journal" and abs(both[0] - 1.2 * 1.1) < 1e-9
    many = [ref(f"file://h/{i}.md") for i in range(9)]
    assert abs(source_factor(many, [], 0.1)[0] - 1.3) < 1e-9  # three extra count, no more
    assert corroboration([ref("file://h/a"), ref("file://h/a")]) == 1


def test_age_of_the_latest_reading() -> None:
    now = datetime(2026, 11, 23, tzinfo=UTC)
    assert read_age_days([ref("file://h/a")], now) is None
    old, recent = now - timedelta(days=400), now - timedelta(days=10)
    sources = [ref("file://h/a", retrieved_at=old), ref("file://h/b", retrieved_at=recent)]
    assert read_age_days(sources, now) == 10


def test_a_cited_memory_goes_to_the_knowledge_section_and_directives_stay_put() -> None:
    cited = item("Gene X acts on Y", [ref("file://h/a")])
    assert section_of(cited) == "knowledge"
    assert section_of(item("plain", [])) == "context"
    assert section_of(item("a procedure", [ref("file://h/a")], kind="procedure")) == "how_to"


def test_knowledge_has_its_own_budget() -> None:
    facts = [item(f"claim {i} " + "word " * 40, [ref(f"file://h/{i}")]) for i in range(6)]
    plain = [item("plain " + "word " * 40, [])]
    shared = build_pack("recall", [*plain, *facts], budget_tokens=120, profile="compact")
    assert len(shared.knowledge) == 0 or len(shared.context) + len(shared.knowledge) <= 3
    own = build_pack(
        "recall",
        [*plain, *facts],
        budget_tokens=120,
        profile="compact",
        knowledge_budget_tokens=300,
    )
    assert len(own.context) == 1 and len(own.knowledge) >= 2
    assert "Knowledge:" in own.rendered
    assert any(m.section == "knowledge" for m in own.more)
