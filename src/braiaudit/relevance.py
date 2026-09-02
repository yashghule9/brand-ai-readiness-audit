"""Small, dependency-free lexical relevance scoring.

query-guided-discovery needs to rank pages/links against a natural-language
query. Rather than pull in a heavy ML stack for a hackathon-scale reference
implementation, this module implements bag-of-words TF cosine similarity —
good enough to separate "clearly about pricing" from "clearly about
careers", which is the level of judgment the ontology actually needs.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    [
        "a", "an", "the", "and", "or", "but", "if", "is", "are", "was", "were",
        "be", "been", "being", "to", "of", "in", "on", "for", "with", "as",
        "by", "at", "from", "this", "that", "these", "those", "it", "its",
        "it's", "i", "you", "your", "we", "our", "do", "does", "did", "have",
        "has", "had", "not", "no", "yes", "what", "how", "why", "who", "where",
        "when", "which", "can", "could", "should", "would", "will",
    ]
)


def _stem(word: str) -> str:
    """Deliberately crude suffix-stripping — enough to match 'cost' with
    'costs' and 'pricing' with 'priced' without pulling in a real stemmer
    dependency for a hackathon-scale relevance heuristic."""
    for suffix, min_len in (("ing", 6), ("edly", 7), ("ed", 5), ("ies", 5), ("es", 4), ("s", 4)):
        if word.endswith(suffix) and len(word) >= min_len:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    return [
        _stem(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1
    ]


def _vector(tokens: list[str]) -> Counter:
    return Counter(tokens)


def cosine_similarity(text_a: str, text_b: str) -> float:
    """Cosine similarity over raw term-frequency vectors, in [0, 1]."""
    vec_a, vec_b = _vector(tokenize(text_a)), _vector(tokenize(text_b))
    if not vec_a or not vec_b:
        return 0.0
    shared = set(vec_a) & set(vec_b)
    dot = sum(vec_a[t] * vec_b[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def answer_completeness(query: str, text: str) -> float:
    """Heuristic proxy for "does this text plausibly answer this query":
    cosine similarity between the query's tokens and the page text,
    boosted slightly for direct query-token coverage."""
    if not text.strip():
        return 0.0
    similarity = cosine_similarity(query, text)
    query_tokens = set(tokenize(query))
    text_tokens = set(tokenize(text))
    coverage = len(query_tokens & text_tokens) / len(query_tokens) if query_tokens else 0.0
    score = 0.6 * similarity + 0.4 * coverage
    return round(min(score, 1.0), 4)


def rank_candidates(query: str, candidates: dict[str, str]) -> list[tuple[str, float]]:
    """Rank {url: text} candidates by relevance to `query`, descending."""
    scored = [(url, round(cosine_similarity(query, text), 4)) for url, text in candidates.items()]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)
