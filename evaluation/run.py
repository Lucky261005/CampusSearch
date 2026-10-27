"""Deterministic CampusSearch evaluation: retrieval metrics, RAG verification checks, latency.

Run from the repository root (Postgres up, corpus ingested and reindexed):

    backend/.venv/Scripts/python -m evaluation.run                  # Windows, v1 baseline
    backend/.venv/Scripts/python -m evaluation.run --benchmark v2   # official corpus
    backend/.venv/bin/python -m evaluation.run                # Linux/macOS

Options:
    --repeats N         timed passes per mode after one warm-up pass (default 5)
    --groq              also run real Groq answers if GROQ_API_KEY is set (off by default). Makes one
                        provider request per question (up to 26); stops at the first 429 or account limit.
                        Use only deliberately, within a free allowance.
    --price-in/--price-out USD per million tokens, from Groq's published pricing, to estimate cost

Writes evaluation/results/results.json and evaluation/RESULTS.md.
The embedding model is loaded from the local cache only (no downloads). If it is unavailable,
semantic/hybrid results are reported as unavailable; nothing is substituted.
"""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never download weights during evaluation
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app import db, rag  # noqa: E402
from app.embeddings import MODEL_NAME, get_embedder  # noqa: E402
from app.retrieval import (Filters, answer_passages, hybrid, keyword,  # noqa: E402
                           load_chunk_index, semantic)
from app.search import SearchIndex  # noqa: E402
from evaluation.metrics import mean, ndcg_at_k, percentile, recall_at_k, reciprocal_rank  # noqa: E402
from evaluation.report import render  # noqa: E402

EVAL = ROOT / "evaluation"
MODES = ("keyword", "semantic", "hybrid")
TOP_K_ASK = 5  # matches AskRequest.top_k default in backend/app/main.py


class DatasetError(Exception):
    pass


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


# Each benchmark version keeps its own dataset, results and report; v1 is the Phase 5 baseline.
# `frozen` runs are historical baselines: never rerun or overwritten (the code they were
# measured with has since changed). `include_illustrative` is the corpus policy the run used.
BENCHMARKS = {
    "v1": {"queries": EVAL / "queries.json", "json": EVAL / "results" / "results.json",
           "md": EVAL / "RESULTS.md", "analysis": EVAL / "analysis.md",
           "include_illustrative": True, "frozen": True},
    "v2": {"queries": EVAL / "v2" / "queries.json", "json": EVAL / "results" / "v2" / "results.json",
           "md": EVAL / "RESULTS_v2.md", "analysis": EVAL / "v2" / "analysis.md",
           "include_illustrative": True, "frozen": True},
    # Phase 6B: same v2 questions; user-facing corpus policy and passage-level answer evidence
    "v2-6b": {"queries": EVAL / "v2" / "queries.json", "json": EVAL / "results" / "v2_6b" / "results.json",
              "md": EVAL / "RESULTS_v2_6b.md", "analysis": EVAL / "v2_6b" / "analysis.md",
              "include_illustrative": False, "frozen": False},
}


def load_dataset(path: Path = BENCHMARKS["v1"]["queries"]) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_dataset(data: dict, docs: dict) -> None:
    """Judgments must match the stored corpus exactly, or the metrics are meaningless."""
    for doc_id, meta in data["documents"].items():
        d = docs.get(doc_id)
        if d is None or d.source_url != meta["source_url"] or d.provenance != meta["provenance"]:
            raise DatasetError(f"document {doc_id} ({meta['name']}) missing or changed in the database")
    for q in data["queries"]:
        if "supporting_quote" in q and norm(q["supporting_quote"]) not in norm(docs[q["supporting_doc"]].content):
            raise DatasetError(f"{q['id']}: supporting quote not found in {q['supporting_doc']}")


def snapshot(conn) -> dict:
    docs = conn.execute("SELECT id, content_hash, provenance FROM documents ORDER BY id").fetchall()
    chunks = conn.execute("SELECT id, content_hash, model FROM chunks ORDER BY id").fetchall()
    h = hashlib.sha256(json.dumps([docs, chunks], default=str).encode()).hexdigest()[:16]
    return {
        "documents": len(docs),
        "illustrative_documents": sum(d["provenance"] == "illustrative" for d in docs),
        "official_documents": sum(d["provenance"] == "official_web" for d in docs),
        "supplied_documents": sum(d["provenance"] == "supplied_document" for d in docs),
        "chunks": len(chunks),
        "chunk_models": sorted({c["model"] for c in chunks}),
        "fingerprint": h,
    }


def environment(conn) -> dict:
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "postgres": conn.execute("SELECT version() AS v").fetchone()["v"].split(",")[0],
        "pgvector": conn.execute(
            "SELECT extversion AS v FROM pg_extension WHERE extname = 'vector'").fetchone()["v"],
        "embedding_model": MODEL_NAME,
        "vector_search": "exact cosine scan (no ANN index)",
    }
    try:
        import sentence_transformers
        import torch

        env |= {"torch": torch.__version__, "torch_threads": torch.get_num_threads(),
                "sentence_transformers": sentence_transformers.__version__,
                "device": "cuda" if torch.cuda.is_available() else "cpu"}
    except ImportError:
        env["torch"] = "not installed"
    return env


def retrieve(mode, conn, embedder, index, q):
    filters = Filters(**q.get("filters", {}))
    if mode == "keyword":
        return keyword(index, q["query"], filters)
    if mode == "semantic":
        return semantic(conn, embedder, q["query"], filters)
    return hybrid(conn, embedder, index, q["query"], filters)


def evaluate_retrieval(modes, conn, embedder, index, data) -> dict:
    names = {k: v["name"] for k, v in data["documents"].items()}
    tag = {"illustrative": "ILL", "supplied_document": "SUP", "official_web": "OFF"}
    for doc_id, d in index.docs.items():  # unjudged documents: readable, provenance-tagged
        names.setdefault(doc_id, f"[{tag[d.provenance]}] {d.title.removeprefix('[Illustrative] ')[:32]}")
    out = {}
    for mode in modes:
        rows = []
        for q in data["queries"]:
            passages = retrieve(mode, conn, embedder, index, q)
            ranked = [p.doc.id for p in passages]
            g = q["relevant"]
            rows.append({
                "id": q["id"], "type": q["type"], "query": q["query"],
                "recall@5": recall_at_k(ranked, g, 5),
                "ndcg@10": ndcg_at_k(ranked, g, 10),
                "rr@10": reciprocal_rank(ranked, g, 10),
                "top3": [names.get(d, d) for d in ranked[:3]],
                "returned": len(ranked),
                "top_similarity": max((p.similarity for p in passages if p.similarity is not None),
                                      default=None),
            })
        judged = [r for r in rows if r["recall@5"] is not None]
        by_type = {}
        for t in sorted({r["type"] for r in judged}):
            sub = [r for r in judged if r["type"] == t]
            by_type[t] = {"n": len(sub), "recall@5": mean([r["recall@5"] for r in sub]),
                          "ndcg@10": mean([r["ndcg@10"] for r in sub]),
                          "mrr": mean([r["rr@10"] for r in sub])}
        out[mode] = {
            "judged_queries": len(judged),
            "unjudged_queries": [r["id"] for r in rows if r["recall@5"] is None],
            "recall@5": mean([r["recall@5"] for r in judged]),
            "ndcg@10": mean([r["ndcg@10"] for r in judged]),
            "mrr": mean([r["rr@10"] for r in judged]),
            "by_type": by_type,
            "per_query": rows,
        }
    return out


def benchmark(modes, conn, embedder, index, data, repeats: int) -> dict:
    out = {}
    for mode in modes:
        for q in data["queries"]:  # warm-up pass, discarded
            retrieve(mode, conn, embedder, index, q)
        samples = []
        for _ in range(repeats):
            for q in data["queries"]:
                t = time.perf_counter()
                retrieve(mode, conn, embedder, index, q)
                samples.append((time.perf_counter() - t) * 1000)
        out[mode] = {"samples": len(samples), "distinct_queries": len(data["queries"]),
                     "p50_ms": percentile(samples, 50), "p95_ms": percentile(samples, 95),
                     "mean_ms": sum(samples) / len(samples), "max_ms": max(samples)}
    return out


# --- RAG verification checks (deterministic mocked model) ------------------------------------

def mock(reply: dict):
    calls = []

    def llm(messages):
        calls.append(messages)
        return json.dumps(reply)

    llm.calls = calls
    return llm


def citation_problems(res, conn) -> list[str]:
    """Every citation must resolve to a stored chunk/document with identical fields."""
    problems = []
    for c in res.citations:
        if c.chunk_id:
            row = conn.execute(
                "SELECT c.content, c.document_id, d.source_url, d.title, d.provenance "
                "FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = %s",
                (c.chunk_id,)).fetchone()
            expected_excerpt = row["content"][: rag.MAX_PASSAGE_CHARS] if row else None
        else:  # keyword-only retrieval cites documents, not chunks
            row = conn.execute("SELECT id AS document_id, source_url, title, provenance, content "
                               "FROM documents WHERE id = %s", (c.document_id,)).fetchone()
            expected_excerpt = None
        if row is None:
            problems.append(f"citation {c.n}: chunk/document not found")
            continue
        for field in ("document_id", "source_url", "title", "provenance"):
            if getattr(c, field) != row[field]:
                problems.append(f"citation {c.n}: {field} differs from stored record")
        if expected_excerpt is not None and c.excerpt != expected_excerpt:
            problems.append(f"citation {c.n}: excerpt differs from stored chunk")
        if c.quote and norm(c.quote) not in norm(c.excerpt):
            problems.append(f"citation {c.n}: quote not in excerpt")
        if c.provenance_label != rag.PROVENANCE_LABELS[c.provenance]:
            problems.append(f"citation {c.n}: provenance label wrong")
    return problems


def evaluate_rag(conn, embedder, index, chunk_index, data) -> dict:
    mode = "hybrid" if embedder else "keyword"
    rows = []
    for q in data["queries"]:
        filters = Filters(**q.get("filters", {}))
        # exactly what /api/ask sends to the model
        passages, _ = answer_passages(conn if embedder else None, embedder, index, chunk_index,
                                      q["query"], filters, TOP_K_ASK)
        info = {"mode": mode}
        row = {"id": q["id"], "type": q["type"], "checks": {}}

        if q.get("answerable") is False:
            honest = mock({"answer": "", "citations": [], "insufficient_evidence": True})
            res = rag.answer(q["query"], passages, honest, info)
            row["checks"]["honest_model_insufficient"] = res.status == "insufficient_evidence"
            row["gated_before_llm"] = not honest.calls
            # an over-confident model that invents a figure while quoting real text
            quote = passages[0].text[:60].strip() if passages else "none"
            liar = mock({"answer": "It costs 85,000 rupees [1].",
                         "citations": [{"source": 1, "quote": quote}],
                         "insufficient_evidence": False})
            res = rag.answer(q["query"], passages, liar, info)
            row["checks"]["fabricated_figure_rejected"] = res.status == "insufficient_evidence"
            rows.append(row)
            continue

        if "supporting_quote" not in q:
            continue
        n = next((i for i, p in enumerate(passages, start=1)
                  if p.doc.id == q["supporting_doc"] and norm(q["supporting_quote"]) in norm(p.text)),
                 None)
        row["evidence_retrieved"] = n is not None
        if n is None:  # retrieval failed to surface the supporting chunk; nothing to cite
            rows.append(row)
            continue
        cite = {"source": n, "quote": q["supporting_quote"]}
        faithful = rag.answer(q["query"], passages,
                              mock({"answer": f"{q['reference_answer']} [{n}]", "citations": [cite],
                                    "insufficient_evidence": False}), info)
        problems = citation_problems(faithful, conn)
        row["checks"] |= {
            "faithful_answer_accepted": faithful.status == "answered",
            "citations_resolve_to_stored_records": faithful.status == "answered" and not problems,
            "illustrative_source_labelled": all(
                ("not official" in c.provenance_label) == (c.provenance == "illustrative")
                for c in faithful.citations)
            and (any(c.provenance == "illustrative" for c in faithful.citations)
                 <= ("illustrative" in faithful.message.lower())),
        }
        row["problems"] = problems
        bad = {
            "fabricated_citation_id_rejected": {"answer": f"{q['reference_answer']} [{len(passages) + 3}]",
                                                "citations": [{"source": len(passages) + 3,
                                                               "quote": q["supporting_quote"]}]},
            "unsupported_marker_rejected": {"answer": f"{q['reference_answer']} [{n}] [9]",
                                            "citations": [cite]},
            "unsupported_number_rejected": {"answer": f"{q['reference_answer']} The fee is 45,000 rupees [{n}].",
                                            "citations": [cite]},
            "misquoted_evidence_rejected": {"answer": f"{q['reference_answer']} [{n}]",
                                            "citations": [{"source": n, "quote": "This sentence does not appear in the source."}]},
        }
        for name, reply in bad.items():
            res = rag.answer(q["query"], passages, mock({**reply, "insufficient_evidence": False}), info)
            row["checks"][name] = res.status == "insufficient_evidence" and not res.citations
        rows.append(row)

    totals: dict[str, list[int]] = {}
    for r in rows:
        for name, ok in r["checks"].items():
            t = totals.setdefault(name, [0, 0])
            t[0] += bool(ok)
            t[1] += 1
    return {"retrieval_mode": mode, "top_k": TOP_K_ASK, "checks": totals, "per_query": rows}


# --- optional real provider run ---------------------------------------------------------------

def evaluate_groq(conn, embedder, index, data, price_in, price_out) -> dict:
    import httpx

    if not os.environ.get("GROQ_API_KEY"):
        return {"status": "unavailable", "reason": "GROQ_API_KEY not set"}
    usage_log = []

    class LimitReached(Exception):
        pass

    def llm(messages):  # same request settings as rag.groq_chat, but keeps token usage
        payload = {"model": rag.GROQ_MODEL, "messages": messages, "temperature": 0,
                   "max_tokens": rag.GROQ_MAX_TOKENS, "response_format": {"type": "json_object"}}
        if rag.GROQ_MODEL.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"
        try:  # same local quota as the app (app/usage.py)
            rid = rag.usage_limiter().reserve()
        except rag.LocalLimitReached:
            raise LimitReached from None
        try:
            res = httpx.post(rag.GROQ_URL, timeout=rag.GROQ_TIMEOUT_S, json=payload,
                             headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
        except httpx.ConnectError:
            rag.usage_limiter().release(rid)
            raise
        except httpx.HTTPError as e:
            rag.usage_limiter().settle(rid, type(e).__name__)
            raise
        rag.usage_limiter().settle(rid, f"http_{res.status_code}")
        if res.status_code == 429 or rag._error_code(res) == "blocked_api_access":
            raise LimitReached  # free-allowance protection: stop the whole run
        res.raise_for_status()
        body = res.json()
        usage_log.append(body.get("usage") or {})
        return body["choices"][0]["message"]["content"]

    rows, stopped = [], None
    for q in data["queries"]:
        passages, _ = answer_passages(conn, embedder, index, load_chunk_index(conn), q["query"],
                                      Filters(**q.get("filters", {})), TOP_K_ASK)
        before = len(usage_log)
        t = time.perf_counter()
        try:
            res = rag.answer(q["query"], passages, llm, {"mode": "hybrid"})
            status, problems = res.status, citation_problems(res, conn)
        except LimitReached:
            stopped = f"provider or local limit reached at {q['id']}; remaining questions were not sent"
            break
        except Exception as e:  # provider errors are recorded, never raised with details
            status, problems = f"error: {type(e).__name__}", []
        rows.append({"id": q["id"], "status": status, "latency_ms": (time.perf_counter() - t) * 1000,
                     "llm_called": len(usage_log) > before, "citation_problems": problems,
                     "answer": res.answer if status == "answered" else None})
    tokens_in = sum(u.get("prompt_tokens", 0) for u in usage_log)
    tokens_out = sum(u.get("completion_tokens", 0) for u in usage_log)
    called = [r["latency_ms"] for r in rows if r["llm_called"]]
    cost = None
    if price_in is not None and price_out is not None and usage_log:
        cost = (tokens_in * price_in + tokens_out * price_out) / 1e6 / len(usage_log)
    return {"status": "ran", "stopped": stopped, "model": rag.GROQ_MODEL, "llm_calls": len(usage_log),
            "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
            "p50_ms": percentile(called, 50) if called else None,
            "p95_ms": percentile(called, 95) if called else None,
            "est_cost_per_llm_call_usd": cost, "pricing_usd_per_m": [price_in, price_out],
            "per_query": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", choices=sorted(BENCHMARKS), default="v1")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace saved results even if they were measured on a different corpus")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--groq", action="store_true")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    args = ap.parse_args()

    paths = BENCHMARKS[args.benchmark]
    data = load_dataset(paths["queries"])
    for q in data["queries"]:  # apply the run's corpus policy to every retrieval call
        q["filters"] = {**q.get("filters", {}), "include_illustrative": paths["include_illustrative"]}
    with db.connect() as conn:
        docs = {d.id: d for d in db.all_documents(conn)}
        validate_dataset(data, docs)
        index = SearchIndex(list(docs.values()))
        snap = snapshot(conn)
        if paths["frozen"]:
            sys.exit(f"refusing to run: {args.benchmark} is a frozen historical baseline. Add a new "
                     "entry to BENCHMARKS for a new measurement.")
        if paths["json"].exists() and not args.overwrite:
            saved = json.loads(paths["json"].read_text(encoding="utf-8"))["corpus"]["fingerprint"]
            sys.exit(f"refusing to overwrite saved {args.benchmark} results (corpus {saved}; database "
                     f"now {snap['fingerprint']}). Pass --overwrite to replace them deliberately.")

        t = time.perf_counter()
        embedder, error = get_embedder()
        load_s = time.perf_counter() - t
        modes = MODES if embedder else ("keyword",)
        if embedder and MODEL_NAME not in snap["chunk_models"]:
            raise DatasetError("chunks were not embedded with the current model; run python -m app.reindex")

        results = {
            "benchmark": args.benchmark,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "corpus": snap,
            "environment": environment(conn),
            "queries": {"total": len(data["queries"]),
                        "by_type": {t: sum(q["type"] == t for q in data["queries"])
                                    for t in sorted({q["type"] for q in data["queries"]})}},
            "semantic": {"available": embedder is not None, "reason": error,
                         "model_load_s": round(load_s, 3) if embedder else None},
            "retrieval": evaluate_retrieval(modes, conn, embedder, index, data),
            "latency": {"repeats": args.repeats, "warmup_passes": 1,
                        "measured": "in-process retrieval call (query embedding + SQL / BM25 + snippets); excludes HTTP",
                        "modes": benchmark(modes, conn, embedder, index, data, args.repeats)},
            "rag_checks": evaluate_rag(conn, embedder, index, load_chunk_index(conn), data),
            "corpus_policy": {"include_illustrative": paths["include_illustrative"]},
            "groq": (evaluate_groq(conn, embedder, index, data, args.price_in, args.price_out)
                     if args.groq else {"status": "not run", "reason": "pass --groq with GROQ_API_KEY set"}),
        }

    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    paths["json"].write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    paths["md"].write_text(render(results, data, paths["analysis"]), encoding="utf-8")
    for mode, m in results["retrieval"].items():
        lat = results["latency"]["modes"][mode]
        print(f"{mode:9s} R@5={m['recall@5']:.3f} nDCG@10={m['ndcg@10']:.3f} MRR={m['mrr']:.3f} "
              f"p50={lat['p50_ms']:.1f}ms p95={lat['p95_ms']:.1f}ms")
    print("RAG checks:", {k: f"{a}/{b}" for k, (a, b) in results["rag_checks"]["checks"].items()})
    print(f"wrote {paths['md'].relative_to(ROOT).as_posix()} and "
          f"{paths['json'].relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
