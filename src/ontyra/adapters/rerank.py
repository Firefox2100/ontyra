"""Reranker implementations: hosted (cannot be retrained) and in-process (can be)."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import random
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ontyra.models.feedback import RerankExample, TrainOptions, TrainResult


class LangChainReranker:
    """Wraps a LangChain `BaseDocumentCompressor` that scores documents (cross-encoders,
    hosted rerank APIs). The compressor returns documents with a `relevance_score` in their
    metadata; documents it drops get score 0. Not retrainable."""

    def __init__(self, compressor: Any) -> None:
        self._compressor = compressor

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        from langchain_core.documents import Document

        docs = [Document(page_content=t, metadata={"i": i}) for i, t in enumerate(texts)]
        kept = await self._compressor.acompress_documents(docs, query)
        scores = [0.0] * len(texts)
        for doc in kept:
            scores[int(doc.metadata["i"])] = float(doc.metadata.get("relevance_score", 1.0))
        return scores


class HttpReranker:
    """A rerank API in the Jina/Cohere/LocalAI shape: `POST {url}/rerank` with a model, a query
    and documents; the answer lists `{index, relevance_score}`. Scores may be raw logits.
    Hosted, so it cannot be retrained here."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: Any = None,
    ) -> None:
        self._url = url.rstrip("/") + "/rerank"
        self._model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._transport = transport  # tests inject an httpx transport

    @property
    def name(self) -> str:
        return f"http:{self._model}"

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        import httpx

        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "query": query,
                    "documents": list(texts),
                    "top_n": len(texts),
                },
            )
            response.raise_for_status()
        scores = [0.0] * len(texts)
        for item in response.json()["results"]:
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores


def _auc(scores: Sequence[float], labels: Sequence[float]) -> float | None:
    """Chance that a relevant pair outscores a non-relevant one (None without both classes)."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y >= 0.5]
    neg = [s for s, y in zip(scores, labels, strict=True) if y < 0.5]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


class CrossEncoderReranker:
    """An in-process cross-encoder (sentence-transformers) that can be fully fine-tuned.

    The base model is generic (`cross-encoder/ms-marco-MiniLM-L6-v2` by default). Training
    updates all weights from feedback pairs, keeps the best epoch on held-out pairs, and saves
    the result as a new version under `model_dir`; it is activated only if it beats the version
    it started from on that held-out data. Versions stay on disk for rollback.
    """

    BASE = "base"

    def __init__(
        self,
        model: str = "cross-encoder/ms-marco-MiniLM-L6-v2",
        *,
        model_dir: str | Path | None = None,
        device: str | None = None,
        batch_size: int = 32,
        max_length: int = 512,
    ) -> None:
        self._base = model
        self._dir = Path(model_dir) if model_dir else None
        # CPU unless asked otherwise: a CUDA-capable torch build may lack kernels for an older GPU
        self._device = device or "cpu"
        self._batch = batch_size
        self._max_length = max_length
        self._model: Any = None
        self._version = self.BASE
        self._lock = threading.Lock()
        if self._dir and (self._dir / "active.json").exists():
            self._version = json.loads((self._dir / "active.json").read_text())["version"]

    @property
    def name(self) -> str:
        return f"cross-encoder:{self._base}"

    @property
    def version(self) -> str:
        return self._version

    def versions(self) -> list[str]:
        found = sorted(p.name for p in self._dir.glob("v*") if p.is_dir()) if self._dir else []
        return [self.BASE, *found]

    def _path(self, version: str) -> str:
        if version == self.BASE or self._dir is None:
            return self._base
        return str(self._dir / version)

    def _load(self) -> Any:
        if self._model is None:
            sentence_transformers: Any = importlib.import_module("sentence_transformers")
            CrossEncoder: Any = sentence_transformers.CrossEncoder
            self._model = CrossEncoder(
                self._path(self._version), device=self._device, max_length=self._max_length
            )
        return self._model

    def _predict(self, query: str, texts: Sequence[str]) -> list[float]:
        with self._lock:
            model = self._load()
            scores = model.predict(
                [(query, t) for t in texts], batch_size=self._batch, show_progress_bar=False
            )
        return [float(s) for s in scores]

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        return await asyncio.to_thread(self._predict, query, texts)

    async def activate(self, version: str) -> None:
        if version not in self.versions():
            raise ValueError(f"unknown reranker version {version!r}")
        with self._lock:
            self._version = version
            self._model = None
            if self._dir:
                self._dir.mkdir(parents=True, exist_ok=True)
                (self._dir / "active.json").write_text(json.dumps({"version": version}))

    async def train(self, examples: Sequence[RerankExample], options: TrainOptions) -> TrainResult:
        return await asyncio.to_thread(self._train, list(examples), options)

    # ---- training -------------------------------------------------------------------------

    def _scores(self, model: Any, pairs: list[RerankExample]) -> list[float]:
        out = model.predict(
            [(e.query, e.text) for e in pairs], batch_size=self._batch, show_progress_bar=False
        )
        return [float(s) for s in out]

    def _train(self, examples: list[RerankExample], opt: TrainOptions) -> TrainResult:
        torch: Any = importlib.import_module("torch")

        rng = random.Random(opt.seed)
        shuffled = examples[:]
        rng.shuffle(shuffled)
        cut = max(0, int(len(shuffled) * opt.validation_fraction))
        valid, train = shuffled[:cut], shuffled[cut:]
        if not train:
            raise ValueError("no training examples left after the validation split")
        with self._lock:
            ce = self._load()
            torch_model = ce.model
            device = next(torch_model.parameters()).device
            labels_val = [e.relevance for e in valid]
            before = _auc(self._scores(ce, valid), labels_val) if valid else None
            best_state = copy.deepcopy({k: v.cpu() for k, v in torch_model.state_dict().items()})
            best_auc, best_epoch = before, 0
            optimizer = torch.optim.AdamW(torch_model.parameters(), lr=opt.learning_rate)
            loss_fn = torch.nn.BCEWithLogitsLoss(reduction="none")
            history: list[float] = []
            for epoch in range(1, opt.epochs + 1):
                torch_model.train()
                order = train[:]
                rng.shuffle(order)
                total = 0.0
                for i in range(0, len(order), opt.batch_size):
                    batch = order[i : i + opt.batch_size]
                    enc = ce.tokenizer(
                        [e.query for e in batch],
                        [e.text for e in batch],
                        padding=True,
                        truncation=True,
                        max_length=self._max_length,
                        return_tensors="pt",
                    ).to(device)
                    target = torch.tensor([e.relevance for e in batch], device=device)
                    weight = torch.tensor([e.weight for e in batch], device=device)
                    logits = torch_model(**enc).logits.view(-1)
                    loss = (loss_fn(logits, target) * weight).mean()
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()  # pyright: ignore[reportUnknownMemberType]
                    total += float(loss.detach()) * len(batch)
                torch_model.eval()
                history.append(total / len(order))
                auc = _auc(self._scores(ce, valid), labels_val) if valid else None
                if auc is not None and (best_auc is None or auc > best_auc):
                    best_auc, best_epoch = auc, epoch
                    best_state = copy.deepcopy(
                        {k: v.cpu() for k, v in torch_model.state_dict().items()}
                    )
            torch_model.load_state_dict(best_state)
            torch_model.eval()
            improved = (
                best_epoch > 0 and best_auc is not None and (before is None or best_auc > before)
            )
            version = self._next_version()
            if self._dir is not None:
                ce.save(str(self._dir / version))
            if improved and opt.activate and self._dir is not None:
                self._version = version
                (self._dir / "active.json").write_text(json.dumps({"version": version}))
            else:  # restore the weights the active version had
                self._model = None
        return TrainResult(
            version=version,
            examples=len(examples),
            metrics={
                "validation_pairs": len(valid),
                "auc_before": before,
                "auc_after": best_auc,
                "best_epoch": best_epoch,
                "epochs_run": len(history),
                "train_loss": history,
            },
            activated=bool(improved and opt.activate and self._dir is not None),
            note=None
            if improved
            else "not activated: no improvement on held-out pairs (or no validation data)",
        )

    def _next_version(self) -> str:
        existing = [int(v[1:]) for v in self.versions() if v.startswith("v") and v[1:].isdigit()]
        return f"v{max(existing, default=0) + 1}"
