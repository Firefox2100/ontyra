"""The decision benchmark's data is well formed and covers what it claims to (offline)."""

from __future__ import annotations

from collections import Counter

from benchmark_test.framework.dialogue import load_dialogues
from decision_test.framework.gate import LABELS, dialogue_items, load_items
from ontyra.calibration.duplicates import load_pairs


def test_pairs_are_unique_and_cover_every_label_and_source() -> None:
    pairs = load_pairs()
    assert len({p.id for p in pairs}) == len(pairs)
    assert len({frozenset((p.a, p.b)) for p in pairs}) == len(pairs)
    assert all(p.a != p.b for p in pairs)
    labels = Counter(p.label for p in pairs)
    assert labels["dup"] >= 40 and labels["update"] >= 10 and labels["distinct"] >= 40
    assert {p.source for p in pairs} == {"paraphrase", "light-edit", "natural"}


def test_gate_items_are_balanced_and_span_the_realms() -> None:
    items = load_items()
    assert len({i.id for i in items}) == len(items)
    counts = Counter(i.label for i in items)
    assert set(counts) == set(LABELS)
    assert min(counts.values()) >= 12
    assert {"dev", "office", "research", "daily", "hobby"} <= {i.realm for i in items}
    for item in items:
        assert item.say.strip()
        if item.label == "refers_back":
            assert item.history, f"{item.id}: refers back to nothing"
        assert len(item.history) <= 2


def test_dialogue_turns_become_gate_items() -> None:
    dialogues = load_dialogues()
    items = dialogue_items(dialogues)
    flagged = sum(
        t.needs_history for d in dialogues for t in d.turns if t.say and t.mode == "recall"
    )
    assert sum(i.label == "refers_back" for i in items) == flagged > 0
    first = next(i for i in items if i.id.endswith("#1"))
    assert first.history == []
