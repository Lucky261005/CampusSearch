"""Ranking metrics over document IDs.

All functions take `ranked`, the system's ordered list of document IDs, and `grades`, a
mapping doc_id -> relevance grade (> 0 = relevant). Documents absent from `grades` count as 0.

recall_at_k(ranked, grades, k)
    |relevant ∩ top-k| / |relevant|.
ndcg_at_k(ranked, grades, k)
    DCG@k  = sum over positions i = 1..k of (2^grade_i - 1) / log2(i + 1)
    IDCG@k = DCG@k of the ideal ordering of *all* judged relevant documents,
             so unretrieved relevant documents lower the score (no credit for them).
    nDCG@k = DCG@k / IDCG@k.
reciprocal_rank(ranked, grades, k)
    1 / rank of the first relevant document within the top k, 0 if none.
    MRR is the mean of this over queries.

Queries with no relevant documents make all three undefined (division by zero / nothing to
find). They return None and must be excluded from means and reported separately.
Duplicate IDs in `ranked` are counted once, at their first position.
"""

import math
from collections.abc import Mapping, Sequence


def _dedupe(ranked: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    return [d for d in ranked if not (d in seen or seen.add(d))]


def _relevant(grades: Mapping[str, float]) -> set[str]:
    return {d for d, g in grades.items() if g > 0}


def recall_at_k(ranked: Sequence[str], grades: Mapping[str, float], k: int) -> float | None:
    rel = _relevant(grades)
    if not rel:
        return None
    return len(rel & set(_dedupe(ranked)[:k])) / len(rel)


def dcg(gains: Sequence[float]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked: Sequence[str], grades: Mapping[str, float], k: int) -> float | None:
    if not _relevant(grades):
        return None
    actual = dcg([grades.get(d, 0) for d in _dedupe(ranked)[:k]])
    ideal = dcg(sorted((g for g in grades.values() if g > 0), reverse=True)[:k])
    return actual / ideal


def reciprocal_rank(ranked: Sequence[str], grades: Mapping[str, float], k: int) -> float | None:
    rel = _relevant(grades)
    if not rel:
        return None
    for i, d in enumerate(_dedupe(ranked)[:k], start=1):
        if d in rel:
            return 1 / i
    return 0.0


def mean(values: Sequence[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def percentile(samples: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (no interpolation); p in (0, 100]."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]
