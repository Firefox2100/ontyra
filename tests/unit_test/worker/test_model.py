from types import SimpleNamespace

import pytest

from ontyra.config import WorkerSettings
from ontyra.worker.model import QWEN_PREFIX, QWEN_SUFFIX, detect_architecture, render, template


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
