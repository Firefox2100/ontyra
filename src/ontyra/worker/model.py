"""The scoring model: weights, conversion to a one-logit head, batched scoring, offload.

Two architectures, one result (a logit per query/document pair; sigmoid of it is the relevance):

* `sequence`: a sequence-classification model with one label (cross-encoders, bge-reranker,
  the community Qwen3 conversions) used as it is;
* `qwen3`: a Qwen3-style reranker, a causal LM that answers "yes" or "no" after a prompt.
  Its answer is softmax over two logits, which equals sigmoid(h . (w_yes - w_no)) with `h` the
  last hidden state, so the LM head is replaced by that single vector: the same scores for a
  fraction of the memory, and a head that can be trained on its own.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ontyra.config import WorkerSettings
from ontyra.worker.probe import Hardware
from ontyra.worker.versions import Manifest

QWEN_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
QWEN_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
HEAD_FILE = "head.safetensors"
LIVE_FILE = "live.safetensors"  # a version's live head edit (a delta from its own head)


@dataclass(frozen=True)
class Scores:
    logits: list[float]
    tokens: int  # tokens the model read, for usage reporting


class Scorer(Protocol):
    """What the engine needs from a model. One thread calls it at a time."""

    @property
    def state(self) -> str: ...  # free | loaded | offloaded

    def load(self) -> None: ...

    def score(self, pairs: Sequence[tuple[str, str]]) -> Scores: ...

    def offload(self) -> None: ...

    def release(self) -> None: ...


def template(cfg: WorkerSettings) -> dict[str, str]:
    """The prompt settings a Qwen3-style version is scored with (kept in its manifest)."""
    return {
        "instruction": cfg.instruction,
        "prefix": cfg.prompt_prefix if cfg.prompt_prefix is not None else QWEN_PREFIX,
        "suffix": cfg.prompt_suffix if cfg.prompt_suffix is not None else QWEN_SUFFIX,
        "yes": cfg.yes_token,
        "no": cfg.no_token,
    }


def detect_architecture(config: Any, requested: str) -> str:
    """`qwen3` for a causal LM, `sequence` for a sequence classifier (or what was requested)."""
    if requested != "auto":
        return requested
    names = list(getattr(config, "architectures", None) or [])
    if any(n.endswith("ForSequenceClassification") for n in names):
        return "sequence"
    if any(n.endswith("ForCausalLM") for n in names):
        return "qwen3"
    raise ValueError(f"cannot tell how to score with architectures {names}: set ARCHITECTURE")


def render(tpl: dict[str, str], query: str, document: str) -> str:
    body = f"<Instruct>: {tpl['instruction']}\n<Query>: {query}\n<Document>: {document}"
    return tpl["prefix"] + body + tpl["suffix"]


def is_out_of_memory(error: BaseException) -> bool:
    return "out of memory" in str(error).lower() or type(error).__name__ == "OutOfMemoryError"


log = logging.getLogger(__name__)


class MemoryShortage(Exception):
    """The device has no room for the weights right now (another process holds the memory)."""


class TorchScorer:
    """A version's weights on the chosen device; offloaded to host memory or dropped when idle."""

    def __init__(
        self, cfg: WorkerSettings, hw: Hardware, dtype: str, manifest: Manifest, versions: Path
    ) -> None:
        self.cfg = cfg
        self.hw = hw
        self.dtype = dtype
        self.manifest = manifest
        self.versions = versions
        self._torch: Any = importlib.import_module("torch")
        self._tok: Any = None
        self._model: Any = None  # the backbone (qwen3) or the whole classifier (sequence)
        self._head: Any = None  # qwen3: (hidden,) float32 vector and scalar bias
        self._bias: Any = None
        self._snap_head: Any = None  # the version's own head (cpu), without the live edit
        self._snap_bias: Any = None
        self._dw: Any = None  # the live edit (cpu): added to the snapshot head
        self._db: Any = None
        self._state = "free"
        self._batch = max(1, cfg.batch_size)

    @property
    def state(self) -> str:
        return self._state

    @property
    def batch(self) -> int:
        return self._batch

    def load(self) -> None:
        """Read the weights (from disk or the hub; never `trust_remote_code`) onto the device."""
        if self._state == "loaded":
            return
        if self._state == "offloaded":
            self._place()
            self._state = "loaded"
            return
        torch = self._torch
        tf: Any = importlib.import_module("transformers")
        dtype = getattr(torch, self.dtype)
        base = self.manifest.base
        kwargs: dict[str, Any] = {"dtype": dtype, "attn_implementation": self.cfg.attention}
        if self.manifest.architecture == "sequence":
            self._tok = tf.AutoTokenizer.from_pretrained(base)
            model = tf.AutoModelForSequenceClassification.from_pretrained(base, **kwargs)
            if int(model.config.num_labels) != 1:
                raise ValueError(
                    f"{base} has {model.config.num_labels} labels: a reranker needs exactly one"
                )
            self._model = model
        else:
            self._tok = tf.AutoTokenizer.from_pretrained(base, padding_side="left")
            lm = tf.AutoModelForCausalLM.from_pretrained(base, **kwargs)
            tpl = self.manifest.template or template(self.cfg)
            yes = self._tok.convert_tokens_to_ids(tpl["yes"])
            no = self._tok.convert_tokens_to_ids(tpl["no"])
            unknown = self._tok.unk_token_id
            if yes is None or no is None or yes == no or unknown in (yes, no):
                raise ValueError(
                    f"{base}: tokens {tpl['yes']!r}/{tpl['no']!r} are not in its vocabulary"
                )
            rows = lm.get_output_embeddings().weight  # tied to the embeddings in small models
            head = (rows[yes] - rows[no]).detach().float().clone()
            self._head, self._bias = head, torch.zeros((), dtype=torch.float32)
            self._model = lm.base_model
            del lm
            self._apply_head_file()
            self._apply_adapter()
            self._snap_head, self._snap_bias = self._head.cpu().clone(), self._bias.cpu().clone()
            self._load_live()
        if self._tok.pad_token is None:
            self._tok.pad_token = self._tok.eos_token
        self._model.eval()
        self._state = "offloaded"
        self._place()
        self._state = "loaded"

    def _place(self) -> None:
        """Move the weights onto the device. When the card is full for a moment the move stops
        half way: put everything back on the host, wait, and try again; after that, say so. The
        weights stay on the host (`offloaded`), so the next request starts the load afresh."""
        for attempt in range(1 + max(0, self.cfg.load_retries)):
            try:
                self._to(self.hw.device)
                return
            except RuntimeError as error:
                if not is_out_of_memory(error):
                    raise
                log.warning(
                    "no device memory to load the model (attempt %d of %d): %s",
                    attempt + 1,
                    1 + max(0, self.cfg.load_retries),
                    self._device_note(),
                )
                self._to("cpu")
                self._empty_cache()
                if attempt < self.cfg.load_retries:
                    time.sleep(self.cfg.load_retry_seconds)
        raise MemoryShortage(
            f"not enough free device memory to load the model ({self._device_note()})"
        )

    def _device_note(self) -> str:
        """How much of the device is free, for the log."""
        backend = getattr(self._torch, self.hw.kind, None)
        try:
            free, total = backend.mem_get_info()  # type: ignore[union-attr]
        except Exception:
            return "free memory unknown"
        return f"{free / 2**20:.0f} MiB free of {total / 2**20:.0f} MiB"

    def _apply_head_file(self) -> None:
        if not self.manifest.head:
            return
        safetensors: Any = importlib.import_module("safetensors.torch")
        tensors = safetensors.load_file(
            str(self.versions / self.manifest.version / self.manifest.head)
        )
        self._head = tensors["weight"].float().clone()
        self._bias = tensors["bias"].float().reshape(())

    def _apply_adapter(self) -> None:
        """Merge a version's LoRA adapter into the backbone (serving needs no extra layers)."""
        if not self.manifest.adapter:
            return
        peft: Any = importlib.import_module("peft")
        path = self.versions / self.manifest.version / self.manifest.adapter
        self._model = peft.PeftModel.from_pretrained(self._model, str(path)).merge_and_unload()

    def _load_live(self) -> None:
        path = self.versions / self.manifest.version / LIVE_FILE
        self._dw = self._db = None
        if path.exists():
            safetensors: Any = importlib.import_module("safetensors.torch")
            tensors = safetensors.load_file(str(path))
            self._dw, self._db = tensors["weight"].float(), tensors["bias"].float().reshape(())

    def _sync_head(self, device: str) -> None:
        """The served head: the version's own head plus its live edit, on `device`."""
        if self._snap_head is None:
            return
        head, bias = self._snap_head, self._snap_bias
        if self._dw is not None:
            head, bias = head + self._dw, bias + self._db
        self._head, self._bias = head.to(device), bias.to(device)

    def _to(self, device: str) -> None:
        self._model.to(device)
        self._sync_head(device)

    def snapshot(self) -> tuple[Any, Any]:
        """The version's own head and bias on the device (what a live edit starts from)."""
        return self._snap_head.to(self.hw.device), self._snap_bias.to(self.hw.device)

    def live_distance(self) -> float:
        """How far the live edit moved the head: norm of the change relative to the head's norm."""
        if self._dw is None or self._snap_head is None:
            return 0.0
        return float(self._dw.norm() / self._snap_head.norm().clamp_min(1e-12))

    def set_live(self, dw: Any, db: Any) -> None:
        """Serve the snapshot head plus (dw, db) from now on, and keep it for the next load."""
        safetensors: Any = importlib.import_module("safetensors.torch")
        path = self.versions / self.manifest.version / LIVE_FILE
        if dw is None:
            self._dw = self._db = None
            path.unlink(missing_ok=True)
        else:
            self._dw, self._db = dw.detach().cpu().float(), db.detach().cpu().float().reshape(())
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            safetensors.save_file(
                {"weight": self._dw.contiguous(), "bias": self._db.reshape(1)}, str(tmp)
            )
            tmp.replace(path)
        if self._state != "free":
            self._sync_head(self.hw.device if self._state == "loaded" else "cpu")

    def clear_live_in_memory(self) -> None:
        """Serve the snapshot head only, without touching the saved edit (retraining starts from
        the base; the edit is dropped when the new version is activated)."""
        self._dw = self._db = None
        if self._state != "free":
            self._sync_head(self.hw.device if self._state == "loaded" else "cpu")

    def restore_head(self) -> None:
        self._sync_head(self.hw.device if self._state == "loaded" else "cpu")

    def hidden(self, pairs: Sequence[tuple[str, str]]) -> Any:
        """The last hidden state of each pair, on the CPU in float32: all the one-logit head
        sees, so a head can be refitted without running the backbone again."""
        torch = self._torch
        self.load()
        rows: list[Any] = []
        i = 0
        while i < len(pairs):
            chunk = pairs[i : i + self._batch]
            try:
                enc = self._qwen_inputs(chunk).to(self.hw.device)
                with torch.inference_mode():
                    rows.append(self._model(**enc).last_hidden_state[:, -1, :].float().cpu())
            except RuntimeError as error:
                if not is_out_of_memory(error) or self._batch == 1:
                    raise
                self._batch = max(1, self._batch // 2)
                self._empty_cache()
                continue
            i += len(chunk)
        return torch.cat(rows) if rows else torch.zeros((0, self._snap_head.shape[0]))

    def offload(self) -> None:
        if self._state != "loaded":
            return
        self._to("cpu")
        self._empty_cache()
        self._state = "offloaded"

    def release(self) -> None:
        self._model = self._head = self._bias = self._tok = None
        self._snap_head = self._snap_bias = self._dw = self._db = None
        self._empty_cache()
        self._state = "free"

    def _empty_cache(self) -> None:
        import gc

        gc.collect()
        kind = self.hw.kind
        backend = getattr(self._torch, kind, None)
        if kind != "cpu" and backend is not None and hasattr(backend, "empty_cache"):
            backend.empty_cache()

    def score(self, pairs: Sequence[tuple[str, str]]) -> Scores:
        self.load()
        logits: list[float] = []
        tokens = 0
        i = 0
        while i < len(pairs):
            chunk = pairs[i : i + self._batch]
            try:
                got, used = self._forward(chunk)
            except RuntimeError as error:
                if not is_out_of_memory(error) or self._batch == 1:
                    raise
                self._batch = max(1, self._batch // 2)  # smaller batches from now on
                self._empty_cache()
                continue
            logits += got
            tokens += used
            i += len(chunk)
        return Scores(logits, tokens)

    def _qwen_inputs(self, pairs: Sequence[tuple[str, str]]) -> Any:
        """Prompt ids with the body cut to fit: the answer slot at the end is never truncated."""
        tpl = self.manifest.template or template(self.cfg)
        head = self._tok(tpl["prefix"], add_special_tokens=False)["input_ids"]
        tail = self._tok(tpl["suffix"], add_special_tokens=False)["input_ids"]
        room = max(16, self.cfg.max_length - len(head) - len(tail))
        bodies = self._tok(
            [render({**tpl, "prefix": "", "suffix": ""}, q, d) for q, d in pairs],
            add_special_tokens=False,
            truncation=True,
            max_length=room,
        )["input_ids"]
        return self._tok.pad(
            {"input_ids": [head + ids + tail for ids in bodies]},
            padding=True,
            return_tensors="pt",
        )

    def _forward(self, pairs: Sequence[tuple[str, str]]) -> tuple[list[float], int]:
        torch = self._torch
        device = self.hw.device
        with torch.inference_mode():
            if self.manifest.architecture == "sequence":
                enc = self._tok(
                    [q for q, _ in pairs],
                    [d for _, d in pairs],
                    padding=True,
                    truncation=True,
                    max_length=self.cfg.max_length,
                    return_tensors="pt",
                ).to(device)
                out = self._model(**enc).logits[:, 0].float()
            else:
                enc = self._qwen_inputs(pairs).to(device)
                hidden = self._model(**enc).last_hidden_state[:, -1, :].float()
                out = hidden @ self._head + self._bias  # left padding: the last slot is the answer
            return out.cpu().tolist(), int(enc["attention_mask"].sum().item())
