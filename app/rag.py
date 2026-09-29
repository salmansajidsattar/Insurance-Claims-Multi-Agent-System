"""Search the policy wording: keyword search (BM25) + meaning search (embeddings).

Both give a ranking of the clauses. We combine the two rankings with
Reciprocal Rank Fusion:  score = 1/(60 + keyword_rank) + 1/(60 + embedding_rank)
Only the customer's own product is searched.
"""
import math
import re
from collections import Counter

from app import config, data, llm
from app.schemas import Clause

STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "by", "is", "are", "it",
             "we", "you", "your", "our", "this", "that", "with", "any", "be", "will", "not", "if",
             "at", "as", "from", "who", "which", "was", "has", "have"}
_vectors: dict[str, list[float]] = {}   # cache: clause text -> embedding


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    doc_words = [words(d) for d in docs]
    avg_len = sum(len(d) for d in doc_words) / len(doc_words)
    doc_freq = Counter(w for d in doc_words for w in set(d))
    scores = []
    for d in doc_words:
        tf, score = Counter(d), 0.0
        for w in set(words(query)):
            if w in tf:
                idf = math.log(1 + (len(docs) - doc_freq[w] + 0.5) / (doc_freq[w] + 0.5))
                score += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / avg_len))
        scores.append(score)
    return scores


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


def embedding_scores(query: str, docs: list[str]) -> list[float]:
    new = [d for d in docs if d not in _vectors]
    if new:
        _vectors.update(zip(new, llm.embed(new)))
    q = llm.embed([query])[0]
    return [cosine(q, _vectors[d]) for d in docs]


def rank(scores: list[float]) -> list[int]:
    """Turn scores into ranks (1 = best)."""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ranks = [0] * len(scores)
    for position, i in enumerate(order, start=1):
        ranks[i] = position
    return ranks


def search(query: str, product_id: str, k: int | None = None) -> list[Clause]:
    clauses = list(data.product_clauses(product_id))
    docs = [f"{c.title}. {c.text}" for c in clauses]
    keyword_rank = rank(bm25(query, docs))
    meaning_rank = rank(embedding_scores(query, docs))
    fused = [1 / (60 + keyword_rank[i]) + 1 / (60 + meaning_rank[i]) for i in range(len(docs))]
    best = sorted(range(len(docs)), key=lambda i: -fused[i])
    return [clauses[i] for i in best[: k or config.TOP_K]]


def clear_cache() -> None:
    _vectors.clear()
