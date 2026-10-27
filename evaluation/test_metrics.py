import json
import math
from pathlib import Path

import pytest

from evaluation.metrics import mean, ndcg_at_k, percentile, recall_at_k, reciprocal_rank

G = {"a": 3, "b": 1, "c": 2}  # three relevant docs; "x", "y" are non-relevant


def test_recall_hand_calculated():
    assert recall_at_k(["x", "a", "y", "b"], G, 5) == pytest.approx(2 / 3)
    assert recall_at_k(["x", "y", "z", "q", "r", "a"], G, 5) == 0  # 'a' is at rank 6
    assert recall_at_k(["a", "b", "c"], G, 5) == 1


def test_ndcg_hand_calculated():
    # ranked a(3), x(0), c(2): DCG = 7/log2(2) + 0 + 3/log2(4) = 7 + 1.5 = 8.5
    # ideal 3,2,1:            IDCG = 7 + 3/log2(3) + 1/log2(4) = 7 + 1.892789 + 0.5
    ideal = 7 + 3 / math.log2(3) + 0.5
    assert ndcg_at_k(["a", "x", "c"], G, 10) == pytest.approx(8.5 / ideal)
    assert ndcg_at_k(["a", "c", "b"], G, 10) == pytest.approx(1.0)


def test_ndcg_gives_no_credit_for_unretrieved_relevant_docs():
    # only 'a' retrieved; the ideal still includes c and b
    assert ndcg_at_k(["a"], G, 10) == pytest.approx(7 / (7 + 3 / math.log2(3) + 0.5))
    assert ndcg_at_k([], G, 10) == 0


def test_ndcg_cutoff_applies_to_ideal_too():
    assert ndcg_at_k(["a"], G, 1) == pytest.approx(1.0)


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "y", "c"], G, 10) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a"], G, 10) == 1
    assert reciprocal_rank(["x", "y"], G, 10) == 0
    assert reciprocal_rank(["x", "a"], G, 1) == 0  # beyond cutoff


def test_duplicates_count_once():
    assert recall_at_k(["a", "a", "a", "a", "a", "b"], G, 5) == pytest.approx(2 / 3)
    assert ndcg_at_k(["a", "a"], {"a": 3}, 10) == pytest.approx(1.0)


def test_no_relevant_documents_is_undefined_not_zero():
    for f in (recall_at_k, ndcg_at_k, reciprocal_rank):
        assert f(["x"], {}, 5) is None
        assert f(["x"], {"x": 0}, 5) is None
    assert mean([None, 1.0, 0.0]) == 0.5 and mean([None]) is None


def test_percentile_nearest_rank():
    s = list(range(1, 21))  # 1..20
    assert percentile(s, 50) == 10 and percentile(s, 95) == 19 and percentile(s, 100) == 20
    assert percentile([5.0], 95) == 5.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_dataset_is_well_formed():
    data = json.loads((Path(__file__).parent / "queries.json").read_text(encoding="utf-8"))
    ids = [q["id"] for q in data["queries"]]
    assert len(ids) == len(set(ids)) and 20 <= len(ids) <= 25
    for q in data["queries"]:
        assert set(q["relevant"]) <= set(data["documents"]), q["id"]
        assert all(g in (1, 2, 3) for g in q["relevant"].values()), q["id"]
        if "supporting_doc" in q:
            assert q["relevant"].get(q["supporting_doc"]) == 3 and q["supporting_quote"], q["id"]
        if q.get("answerable") is False:
            assert "reference_answer" not in q, q["id"]
