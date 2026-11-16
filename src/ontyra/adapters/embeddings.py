"""Embedder implementations."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from ontyra.ports.adapters import Embedder


class HashEmbedder:
    """Dependency-free feature-hashing embedder (words, word pairs, character trigrams).

    No model and no network: a deterministic baseline for tests, offline use and as the
    reference point benchmarks must beat. It captures lexical overlap (including inflection
    and typos through trigrams), not meaning.
    """

    def __init__(self, dimension: int = 256) -> None:
        if dimension < 16:
            raise ValueError("dimension too small")
        self._dim = dimension

    @property
    def name(self) -> str:
        return f"hash-{self._dim}"

    @property
    def dimension(self) -> int:
        return self._dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        words = re.findall(r"\w+", text.casefold())
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in pairwise(words)]
        for w in words:
            padded = f"^{w}$"
            feats += [(f"c:{padded[i : i + 3]}", 0.3) for i in range(len(padded) - 2)]
        return feats

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for feat, weight in self._features(text):
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % self._dim] += weight if (h >> 63) & 1 else -weight
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class LangChainEmbedder:
    """Wraps any LangChain `Embeddings` object (OpenAI-compatible APIs, Ollama, local HF…)."""

    def __init__(
        self,
        embeddings: Any,
        *,
        name: str,
        dimension: int,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._emb = embeddings
        self._name = name
        self._dim = dimension
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix

    @property
    def name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = await self._emb.aembed_documents(
            [self._document_prefix + t for t in texts]
        )
        return self._checked(vectors)

    async def embed_query(self, text: str) -> list[float]:
        return self._checked([await self._emb.aembed_query(self._query_prefix + text)])[0]

    def _checked(self, vectors: list[list[float]]) -> list[list[float]]:
        for v in vectors:
            if len(v) != self._dim:
                raise ValueError(
                    f"embedding model returned {len(v)} dimensions, configured {self._dim}"
                )
        return vectors


_: type[Embedder] = HashEmbedder  # structural check
