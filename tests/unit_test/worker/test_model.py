from types import SimpleNamespace
from typing import Any

import pytest

from ontyra.config import WorkerSettings
from ontyra.worker.model import (
    QWEN_PREFIX,
    QWEN_SUFFIX,
    MemoryShortage,
    TorchScorer,
    detect_architecture,
    render,
    template,
)


def test_architecture_is_read_from_the_config() -> None:
    causal = SimpleNamespace(architectures=["Qwen3ForCausalLM"])
    seq = SimpleNamespace(architectures=["BertForSequenceClassification"])
    assert detect_architecture(causal, "auto") == "qwen3"
    assert detect_architecture(seq, "auto") == "sequence"
    assert detect_architecture(causal, "sequence") == "sequence"  # the operator decides
    with pytest.raises(ValueError, match="ARCHITECTURE"):
        detect_architecture(SimpleNamespace(architectures=["BertModel"]), "auto")


def test_the_prompt_template_is_configurable() -> None:
    default = template(WorkerSettings())
    assert default["prefix"] == QWEN_PREFIX and default["suffix"] == QWEN_SUFFIX
    custom = template(
        WorkerSettings(instruction="Find it", prompt_prefix="<a>", prompt_suffix="<b>")
    )
    assert render(custom, "q", "d") == "<a><Instruct>: Find it\n<Query>: q\n<Document>: d<b>"


def _scorer(fail: int, *, retries: int = 2, error: Exception | None = None) -> Any:
    """A TorchScorer whose first `fail` moves onto the device run out of memory."""
    s: Any = object.__new__(TorchScorer)
    s.cfg = WorkerSettings(load_retries=retries, load_retry_seconds=0)
    s.hw = SimpleNamespace(device="cuda:0", kind="cpu")
    s._torch = SimpleNamespace()
    s._state = "offloaded"
    s.moves = []
    left = [fail]

    def to(device: str) -> None:
        s.moves.append(device)
        if device != "cpu" and left[0] > 0:
            left[0] -= 1
            raise error or RuntimeError("CUDA out of memory. Tried to allocate 48.00 MiB")

    s._to = to
    return s


def test_a_full_device_is_retried_with_the_weights_put_back_on_the_host() -> None:
    s = _scorer(fail=2)
    s.load()
    assert s.state == "loaded"
    assert s.moves == ["cuda:0", "cpu", "cuda:0", "cpu", "cuda:0"]


def test_still_no_room_leaves_the_weights_on_the_host_and_says_so() -> None:
    s = _scorer(fail=99)
    with pytest.raises(MemoryShortage, match="free device memory"):
        s.load()
    assert s.state == "offloaded"
    assert s.moves[-1] == "cpu"  # nothing is left half on the device
    assert s.moves.count("cuda:0") == 3


def test_a_different_failure_is_not_retried() -> None:
    s = _scorer(fail=1, error=RuntimeError("something else"))
    with pytest.raises(RuntimeError, match="something else"):
        s.load()
    assert s.moves == ["cuda:0"]
