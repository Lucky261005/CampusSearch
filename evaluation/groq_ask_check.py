"""Real-provider check of /api/ask against a running backend (Phase 7).

The backend process must have GROQ_API_KEY in its own environment; this script never reads,
sends or prints a key. It only calls the local CampusSearch API.

    backend/.venv/Scripts/python -m evaluation.groq_ask_check --live [--base http://127.0.0.1:8000]

`--live` is required: each scenario can make one real provider request (at most six per run), and the
project rule is that live requests are made only when explicitly intended. The run stops at the first
rate-limit or usage-limit response instead of continuing.

Writes evaluation/private/groq_ask_log.json (gitignored: it contains generated answers for
manual review). Automated checks here cover citation integrity and provenance only; whether an
answer is factually supported must be judged by reading it against the cited passages
(see evaluation/MANUAL_REVIEW.md).
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "private" / "groq_ask_log.json"
LIMIT_HIT = re.compile(r"rate limit|quota|usage limit|spending limit|resume in about")

# (id, scenario, question, filters, expectation)
SCENARIOS = [
    ("G1", "single source", "When is the last date to apply for the MBA selection process?", {},
     "answer from the MBA admission page (25 January 2027)"),
    ("G2", "multi source", "Which government scholarship portals does the university help students "
     "with, and how do students register on the National Scholarship Portal?", {},
     "scholarship cell page plus the SSP/NSP notice steps"),
    ("G3", "unanswerable", "How much is the hostel fee at the Kengeri campus?", {},
     "refusal: no hostel fee exists in the corpus"),
    ("G4", "unsupported date", "What is the exact date by which the semester tuition fee must be paid?", {},
     "refusal or explicit limitation: the handbook says 'by the due date' but gives no date"),
    ("G5", "illustrative excluded", "What is the hostel fee?", {},
     "no illustrative source retrieved or cited by default"),
    ("G5b", "illustrative opt-in", "What is the hostel fee?", {"include_illustrative": True},
     "illustrative sources allowed but labelled; any answer from them must say they are not official"),
]


def check(resp: dict) -> dict:
    """Automated integrity checks; not a judgment of factual correctness."""
    sources = {s["n"]: s for s in resp["sources"]}
    cited_ok = all(c["n"] in sources and c["chunk_id"] == sources[c["n"]]["chunk_id"]
                   and c["source_url"] == sources[c["n"]]["source_url"] for c in resp["citations"])
    quotes_ok = all(c.get("quote") and " ".join(c["quote"].split()).casefold()
                    in " ".join(c["excerpt"].split()).casefold() for c in resp["citations"])
    markers = {int(m) for m in re.findall(r"\[(\d+)\]", resp.get("answer") or "")}
    return {
        "citations_resolve_to_retrieved_sources": cited_ok,
        "quotes_found_in_cited_excerpts": quotes_ok,
        "answer_markers_all_cited": markers <= {c["n"] for c in resp["citations"]},
        "illustrative_sources": sum(s["provenance"] == "illustrative" for s in resp["sources"]),
        "illustrative_citations": sum(c["provenance"] == "illustrative" for c in resp["citations"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--live", action="store_true",
                    help=f"confirm: send up to {len(SCENARIOS)} real provider requests via the backend")
    args = ap.parse_args()
    if not args.live:
        raise SystemExit(f"Not run. This check can make up to {len(SCENARIOS)} real Groq requests through the "
                         "backend. Re-run with --live only when you intend to use your free allowance.")
    rows = []
    with httpx.Client(base_url=args.base, timeout=60) as client:
        client.get("/health").raise_for_status()
        for sid, scenario, question, filters, expectation in SCENARIOS:
            t = time.perf_counter()
            r = client.post("/api/ask", json={"question": question, "filters": filters})
            ms = round((time.perf_counter() - t) * 1000)
            row = {"id": sid, "scenario": scenario, "question": question, "filters": filters,
                   "expectation": expectation, "http_status": r.status_code, "latency_ms": ms}
            if r.status_code == 200:
                body = r.json()
                row |= {
                    "status": body["status"], "message": body["message"], "answer": body["answer"],
                    "llm_called": body["retrieval"].get("llm_called"),
                    "llm_model": body["retrieval"].get("llm_model"),
                    "retrieval_mode": body["retrieval"].get("mode"),
                    "citations": [{k: c[k] for k in ("n", "chunk_id", "title", "source_url", "provenance",
                                                     "page", "quote")} for c in body["citations"]],
                    "sources": [{k: s[k] for k in ("n", "chunk_id", "title", "provenance", "page")}
                                | {"excerpt": s["excerpt"]} for s in body["sources"]],  # what the model saw
                    "automated_checks": check(body),
                }
            rows.append(row)
            print(f"{sid:4s} {scenario:22s} HTTP {r.status_code} {row.get('status', '?'):22s} "
                  f"llm_called={row.get('llm_called')} {ms} ms")
            if LIMIT_HIT.search(row.get("message") or ""):
                print("Provider limit reached: stopping; remaining scenarios were not sent.")
                break
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "base": args.base, "results": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path(__file__).parents[1]).as_posix()} (gitignored)")


if __name__ == "__main__":
    main()
