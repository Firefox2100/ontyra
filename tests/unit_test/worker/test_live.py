"""The live head fit on synthetic hidden states (needs torch; skipped without it)."""

import pytest

torch = pytest.importorskip("torch")

from ontyra.worker.live import LiveOptions, fit  # noqa: E402


def data(n: int = 120, d: int = 16, seed: int = 0):
    g = torch.Generator().manual_seed(seed)
    hidden = torch.randn(n, d, generator=g)
    head = torch.randn(d, generator=g)
    truth = head + 0.5 * torch.randn(d, generator=g)  # what the feedback "wants"
    labels = (hidden @ truth > 0).float().tolist()
    return hidden, labels, head


def test_the_fit_is_deterministic_and_moves_the_head_towards_the_feedback() -> None:
    hidden, labels, head = data()
    weights = [1.0] * len(labels)
    a = fit(hidden, labels, weights, head, torch.zeros(()), LiveOptions(max_distance=1.0))
    b = fit(hidden, labels, weights, head, torch.zeros(()), LiveOptions(max_distance=1.0))
    assert torch.equal(a[0], b[0]) and float(a[1]) == float(b[1])
    before = ((hidden @ head > 0).float() == torch.tensor(labels)).float().mean()
    after = ((hidden @ (head + a[0]) + a[1] > 0).float() == torch.tensor(labels)).float().mean()
    assert after > before


def test_the_edit_never_exceeds_its_distance_cap() -> None:
    hidden, labels, head = data()
    for cap in (0.05, 0.25):
        dw, db, _ = fit(
            hidden,
            labels,
            [1.0] * len(labels),
            head,
            torch.zeros(()),
            LiveOptions(max_distance=cap),
        )
        size = float((dw.pow(2).sum() + db.pow(2)).sqrt())
        assert size <= cap * float(head.norm()) * (1 + 1e-4)


def test_removing_examples_is_a_replay_not_a_subtraction() -> None:
    hidden, labels, head = data()
    full = fit(hidden, labels, [1.0] * 120, head, torch.zeros(()), LiveOptions())
    fewer = fit(hidden[:90], labels[:90], [1.0] * 90, head, torch.zeros(()), LiveOptions())
    again = fit(hidden[:90], labels[:90], [1.0] * 90, head, torch.zeros(()), LiveOptions())
    assert not torch.equal(full[0], fewer[0])
    assert torch.equal(
        fewer[0], again[0]
    )  # the same examples give the same edit, whatever came before


def test_flipping_the_labels_is_bounded_too() -> None:
    hidden, labels, head = data()
    flipped = [1.0 - y for y in labels]
    dw, db, _ = fit(
        hidden, flipped, [1.0] * len(labels), head, torch.zeros(()), LiveOptions(max_distance=0.1)
    )
    assert float((dw.pow(2).sum() + db.pow(2)).sqrt()) <= 0.1 * float(head.norm()) * (1 + 1e-4)
