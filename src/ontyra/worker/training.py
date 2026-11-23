"""Retraining a Qwen3-style reranker: LoRA on the backbone plus its one-logit head.

The backbone stays in the serving dtype and frozen; LoRA weights and the head train in float32
under autocast (the same code runs on fp16-only cards such as V100 and on bf16 cards). Examples
are split *by query* so the validation queries are unseen, the epoch with the best validation
AUC is kept, and the result is saved as a new version next to its parent: nothing is activated
here. A trusted gate set (requests with candidates) is scored before and after, so whoever
decides about promotion has numbers that a poisoned pool cannot move.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ontyra.worker.model import HEAD_FILE, TorchScorer

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj"]


@dataclass(frozen=True)
class Example:
    query: str
    text: str
    relevance: float
    weight: float = 1.0


@dataclass
class Options:
    epochs: int = 4
    learning_rate: float = 1e-4  # LoRA; the head learns 5x faster
    batch_size: int = 8
    lora_rank: int = 8
    lora_alpha: int = 16
    head_only: bool = False
    validation_fraction: float = 0.15
    seed: int = 13


def split_by_query(
    examples: Sequence[Example], fraction: float, seed: int
) -> tuple[list[Example], list[Example]]:
    """(train, validation) with every query wholly on one side; validation needs both classes."""
    if fraction <= 0:
        return list(examples), []
    train: list[Example] = []
    valid: list[Example] = []
    for e in examples:
        h = int.from_bytes(hashlib.sha1(f"{seed}:{e.query}".encode()).digest()[:4], "big") / 2**32
        (valid if h < fraction else train).append(e)
    if len({e.relevance >= 0.5 for e in valid}) < 2 or len({e.relevance >= 0.5 for e in train}) < 2:
        return list(examples), []
    return train, valid


def auc(scores: Sequence[float], labels: Sequence[float]) -> float | None:
    """Chance that a relevant example outscores a not relevant one (ties count half)."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y >= 0.5]
    neg = [s for s, y in zip(scores, labels, strict=True) if y < 0.5]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def class_weights(examples: Sequence[Example]) -> list[float]:
    """Per-example weights: the feedback weight, balanced so neither class dominates."""
    pos = sum(e.weight for e in examples if e.relevance >= 0.5) or 1.0
    neg = sum(e.weight for e in examples if e.relevance < 0.5) or 1.0
    half = (pos + neg) / 2
    return [e.weight * (half / pos if e.relevance >= 0.5 else half / neg) for e in examples]


def mrr_and_accuracy(
    requests: Sequence[dict[str, Any]],
    score: Callable[[Sequence[tuple[str, str]]], list[float]],
) -> dict[str, float]:
    """Mean reciprocal rank of the first relevant candidate and the share of relevant-over-not
    relevant pairs ordered right, over `requests` (`{query, candidates: [{text, relevant}]}`)."""
    rr: list[float] = []
    acc: list[float] = []
    for r in requests:
        cands = r["candidates"]
        logits = score([(r["query"], c["text"]) for c in cands])
        order = sorted(range(len(cands)), key=lambda i: -logits[i])
        ranks = [n + 1 for n, i in enumerate(order) if cands[i]["relevant"]]
        if not ranks:
            continue
        rr.append(1.0 / min(ranks))
        value = auc(logits, [1.0 if c["relevant"] else 0.0 for c in cands])
        if value is not None:
            acc.append(value)
    return {
        "mrr": round(sum(rr) / len(rr), 4) if rr else 0.0,
        "pairwise": round(sum(acc) / len(acc), 4) if acc else 0.0,
        "requests": float(len(rr)),
    }


def train(
    scorer: TorchScorer,
    examples: Sequence[Example],
    gate: Sequence[dict[str, Any]],
    options: Options,
    out: Path,
) -> dict[str, Any]:
    """Train on the model thread (hold the engine's lease) and save the best epoch to `out`.

    Returns the metrics. The scorer is left as it was (LoRA removed, original head); the result
    is the saved version, to be activated by whoever decides.
    """
    if scorer.manifest.architecture != "qwen3":
        raise ValueError("training is supported for Qwen3-style rerankers")
    torch: Any = importlib.import_module("torch")
    peft: Any = importlib.import_module("peft")
    safetensors: Any = importlib.import_module("safetensors.torch")
    scorer.load()
    rng = random.Random(options.seed)
    train_set, valid_set = split_by_query(examples, options.validation_fraction, options.seed)
    if not train_set:
        raise ValueError("no examples to train on")
    device = scorer.hw.device
    scorer.clear_live_in_memory()  # retraining starts from the base, without any live edit
    backbone = scorer._model  # pyright: ignore[reportPrivateUsage]
    head0, bias0 = scorer.snapshot()
    gate_before = mrr_and_accuracy(gate, lambda p: scorer.score(p).logits) if gate else None

    head = torch.nn.Parameter(head0.detach().clone().float())
    bias = torch.nn.Parameter(bias0.detach().clone().float())
    model = backbone
    params: list[Any] = [{"params": [head, bias], "lr": options.learning_rate * 5}]
    if not options.head_only:
        config = peft.LoraConfig(
            r=options.lora_rank,
            lora_alpha=options.lora_alpha,
            lora_dropout=0.05,
            target_modules=TARGETS,
        )
        model = peft.get_peft_model(backbone, config)
        params.append({"params": [p for p in model.parameters() if p.requires_grad]})
        params[-1]["lr"] = options.learning_rate
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()
        if hasattr(backbone, "gradient_checkpointing_enable"):
            backbone.gradient_checkpointing_enable()
            backbone.config.use_cache = False
    else:
        for p in backbone.parameters():
            p.requires_grad_(False)
    optimizer = torch.optim.AdamW(params, weight_decay=0.0)
    use_scaler = scorer.dtype == "float16" and scorer.hw.kind == "cuda"
    scaler = torch.amp.GradScaler(enabled=use_scaler)
    amp_dtype = getattr(torch, scorer.dtype)
    amp_on = scorer.hw.kind != "cpu" and scorer.dtype != "float32"

    def logits_of(batch: Sequence[Example], *, grad: bool) -> Any:
        enc = scorer._qwen_inputs([(e.query, e.text) for e in batch]).to(device)  # pyright: ignore[reportPrivateUsage]
        with (
            torch.set_grad_enabled(grad),
            torch.autocast(device_type=scorer.hw.kind, dtype=amp_dtype, enabled=amp_on),
        ):
            hidden = model(**enc).last_hidden_state[:, -1, :].float()
        return hidden @ head + bias

    def validation() -> float | None:
        if not valid_set:
            return None
        model.eval()
        got: list[float] = []
        for i in range(0, len(valid_set), options.batch_size * 2):
            got += logits_of(valid_set[i : i + options.batch_size * 2], grad=False).tolist()
        return auc(got, [e.relevance for e in valid_set])

    def snapshot() -> dict[str, Any]:
        return {
            "head": head.detach().cpu().clone(),
            "bias": bias.detach().cpu().clone(),
            "lora": {
                k: v.detach().cpu().clone() for k, v in model.state_dict().items() if "lora_" in k
            },
        }

    auc_before = validation()
    best_auc, best, best_epoch = auc_before, snapshot(), 0
    weights = dict(zip(map(id, train_set), class_weights(train_set), strict=True))
    losses: list[float] = []
    for epoch in range(1, options.epochs + 1):
        model.train()
        order = list(train_set)
        rng.shuffle(order)
        total, count = 0.0, 0
        for i in range(0, len(order), options.batch_size):
            batch = order[i : i + options.batch_size]
            target = torch.tensor([e.relevance for e in batch], device=device)
            w = torch.tensor([weights[id(e)] for e in batch], device=device)
            loss = (
                torch.nn.functional.binary_cross_entropy_with_logits(
                    logits_of(batch, grad=True), target, reduction="none"
                )
                * w
            ).sum() / w.sum()
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total += float(loss.item()) * len(batch)
            count += len(batch)
        losses.append(round(total / max(1, count), 4))
        got = validation()
        if got is None or best_auc is None or got > best_auc:
            best_auc, best, best_epoch = got, snapshot(), epoch

    # put the best epoch into the model, score the gate set with it, save, then restore
    with torch.no_grad():
        head.copy_(best["head"].to(device))
        bias.copy_(best["bias"].to(device))
        if not options.head_only:
            state = model.state_dict()
            for k, v in best["lora"].items():
                state[k].copy_(v.to(device))
    model.eval()
    scorer._head, scorer._bias = head.detach(), bias.detach()  # pyright: ignore[reportPrivateUsage]
    scorer._model = model  # pyright: ignore[reportPrivateUsage]
    try:
        gate_after = mrr_and_accuracy(gate, lambda p: scorer.score(p).logits) if gate else None
        out.mkdir(parents=True, exist_ok=True)
        if not options.head_only:
            model.save_pretrained(str(out / "adapter"))
        safetensors.save_file(
            {"weight": head.detach().cpu().contiguous(), "bias": bias.detach().cpu().reshape(1)},
            str(out / HEAD_FILE),
        )
    finally:
        if not options.head_only:
            if hasattr(backbone, "gradient_checkpointing_disable"):
                backbone.gradient_checkpointing_disable()
            scorer._model = model.unload()  # pyright: ignore[reportPrivateUsage]
        scorer.restore_head()
        scorer._model.eval()  # pyright: ignore[reportPrivateUsage]
    metrics: dict[str, Any] = {
        "train_examples": len(train_set),
        "validation_examples": len(valid_set),
        "auc_before": None if auc_before is None else round(auc_before, 4),
        "auc_after": None if best_auc is None else round(best_auc, 4),
        "best_epoch": best_epoch,
        "loss": losses,
        "gate_before": gate_before,
        "gate_after": gate_after,
        "head_only": options.head_only,
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


__all__ = [
    "Example",
    "Options",
    "auc",
    "class_weights",
    "mrr_and_accuracy",
    "split_by_query",
    "train",
]
