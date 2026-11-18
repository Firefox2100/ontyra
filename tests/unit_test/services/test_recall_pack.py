from ontyra.models import RecordId
from ontyra.models.recall import RecallItem
from ontyra.services.recall_pack import build_pack, estimate_tokens, section_of


def item(kind: str, text: str, n: int = 1, **kw: object) -> RecallItem:
    return RecallItem(
        memory=RecordId("memory", f"m{n}"),
        revision=RecordId(f"{kind}_rev", f"r{n}"),
        kind=kind,  # type: ignore[arg-type]
        text=text,
        score=1.0 / n,
        relevance=1.0,
        **kw,  # type: ignore[arg-type]
    )


def test_sections_by_kind_and_strength() -> None:
    assert section_of(item("directive", "x", strength="must")) == "must_follow"
    assert section_of(item("directive", "x", strength="prefer")) == "applicable"
    assert section_of(item("fact", "x")) == "context"
    assert section_of(item("procedure", "x")) == "how_to"
    assert section_of(item("lesson", "x")) == "how_to"
    assert section_of(item("task", "x")) == "open_tasks"


def test_priority_order_and_rendering() -> None:
    pack = build_pack(
        "recall",
        [
            item("fact", "the sky is blue", 1),
            item("directive", "never run rm -rf /", 2, strength="must", polarity="avoid"),
        ],
        budget_tokens=500,
        profile="rich",
    )
    assert not pack.silent
    assert [i.text for i in pack.must_follow] == ["never run rm -rf /"]
    assert pack.rendered.index("Must follow") < pack.rendered.index("Context")
    assert pack.tokens == estimate_tokens(pack.rendered)


def test_overflow_is_counted_not_dropped_silently() -> None:
    items = [item("fact", f"fact number {n} " + "word " * 30, n) for n in range(1, 11)]
    pack = build_pack("recall", items, budget_tokens=120, profile="compact")
    shown = len(pack.context)
    assert 0 < shown < 10
    assert pack.more[0].section == "context" and pack.more[0].count == 10 - shown
    assert pack.tokens <= 120 + 40


def test_must_directives_are_packed_before_anything_else() -> None:
    items = [item("fact", "word " * 80, 1)] + [
        item("directive", f"must rule {n}", n + 1, strength="must") for n in range(1, 4)
    ]
    pack = build_pack("briefing", items, budget_tokens=80, profile="compact")
    assert len(pack.must_follow) == 3
    assert not pack.context and pack.more[0].section == "context"


def test_the_first_item_is_always_delivered() -> None:
    pack = build_pack("recall", [item("fact", "x " * 500)], budget_tokens=10, profile="compact")
    assert len(pack.context) == 1


def test_empty_is_silent() -> None:
    pack = build_pack("recall", [], budget_tokens=500, profile="rich")
    assert pack.silent and pack.rendered == "" and pack.tokens == 0


def test_caveats_render_in_both_profiles() -> None:
    it = item("fact", "serial ports are /dev/tty*", caveats=["only if linux"])
    assert "only if linux" in build_pack("recall", [it], budget_tokens=99, profile="rich").rendered
    assert (
        "only if linux" in build_pack("recall", [it], budget_tokens=99, profile="compact").rendered
    )


def test_compact_renders_the_rule_of_a_directive_and_rich_the_full_text() -> None:
    it = item(
        "directive",
        "Never run git commit in this repo. The owner commits by hand.",
        strength="must",
        polarity="avoid",
        detail={"rule": "Do not run git commit in this repo"},
    )
    compact = build_pack("recall", [it], budget_tokens=99, profile="compact").rendered
    rich = build_pack("recall", [it], budget_tokens=99, profile="rich").rendered
    assert "- Do not run git commit in this repo [memory:m1]" in compact
    assert "owner commits by hand" not in compact
    assert "owner commits by hand" in rich


def test_compact_keeps_the_text_when_a_directive_has_no_rule() -> None:
    it = item("directive", "Prefer small commits", strength="prefer", detail={"rule": "  "})
    assert (
        "Prefer small commits"
        in build_pack("recall", [it], budget_tokens=99, profile="compact").rendered
    )
