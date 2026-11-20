"""Ранжирование релевантности для Reader.

По умолчанию — скоринг по перекрытию токенов (без внешних моделей). Для production
доступен порт :class:`Embedder` (эмбеддинги/pgvector/Sentence-Transformers) и косинусный
скорер поверх него. Подстрочное сравнение целого текста заменено на токенное ранжирование.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text)) if len(t) >= 2]


class RelevanceScorer(Protocol):
    """Порт скоринга релевантности текста запросу (больше — релевантнее; 0 — нерелевантно)."""

    def score(self, query: str, text: str) -> float: ...


class TokenOverlapScorer:
    """Скоринг по числу совпавших термов запроса (с токенным подсовпадением) + частота.

    Терм запроса считается совпавшим, если какой-либо токен текста содержит его или
    содержится в нём (устойчиво к простым морфологическим вариациям и префиксам).
    """

    def score(self, query: str, text: str) -> float:
        q_terms = set(tokenize(query))
        if not q_terms:
            return 0.0
        t_tokens = tokenize(text)
        if not t_tokens:
            return 0.0
        matched = 0
        occurrences = 0
        for term in q_terms:
            hits = sum(1 for tok in t_tokens if term in tok or tok in term)
            if hits:
                matched += 1
                occurrences += hits
        if matched == 0:
            return 0.0
        # основной вес — доля совпавших термов запроса; частота — слабый тай-брейк
        return matched + 0.001 * occurrences


class Embedder(Protocol):
    """Порт эмбеддингов для production-ранжирования (pgvector/Sentence-Transformers)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


class HashingEmbedder:
    """Детерминированный эмбеддер без внешних моделей (hashing bag-of-tokens + L2).

    Для production-поиска на pgvector без тяжёлых зависимостей (torch). Похожие тексты
    дают близкие векторы; замена на Sentence-Transformers — за тем же портом `Embedder`.
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _vec(self, text: str) -> list[float]:
        acc = [0.0] * self._dim
        for tok in tokenize(text):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest())
            acc[h % self._dim] += 1.0
        norm = math.sqrt(sum(x * x for x in acc))
        if norm == 0.0:
            acc[0] = 1.0
            return acc
        return [x / norm for x in acc]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class EmbeddingScorer:
    """Косинусный скорер поверх :class:`Embedder` (production-вариант)."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def score(self, query: str, text: str) -> float:
        qv, tv = self._embedder.embed([query, text])
        sim = cosine(qv, tv)
        return max(0.0, sim)
