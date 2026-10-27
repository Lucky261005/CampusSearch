_Hand-written after the v2-6b run on corpus fingerprint `5b6786bd30c3a3d3` (54 documents: 39 official,
3 supplied, 12 illustrative; 753 chunks). If the fingerprint at the top of this report differs, re-read the
tables before trusting this section._

**What this run is.** The same 26 v2 questions and judgments as `RESULTS_v2.md` (Phase 6A, fingerprint
`ecdcc06fd3e76346`), re-run after the Phase 6B changes:

1. Illustrative records are excluded by default (the user-facing corpus policy).
2. Answer evidence uses passage-level fusion (`answer_passages`, the same function `/api/ask` calls).
3. One PDF was re-extracted with repaired word spacing (the entry and exit guidelines).
4. Six scanned official PDFs were added through OCR.

The question set is the same, but **the corpus is not**: changes 3 and 4 alter it. This is therefore not a
perfectly controlled before-and-after comparison. The 6A results are kept unchanged and frozen.

| | Keyword R@5 / nDCG@10 / MRR | Semantic R@5 / nDCG@10 / MRR | Hybrid R@5 / nDCG@10 / MRR | Supporting passage retrieved |
|---|---|---|---|---:|
| v2 (6A) | 0.829 / 0.770 / 0.749 | 0.913 / 0.916 / 0.938 | 0.885 / 0.885 / 0.879 | 6/9 |
| v2-6b | 0.796 / 0.767 / 0.740 | 0.927 / 0.895 / 0.890 | 0.875 / 0.875 / 0.855 | **7/9** |

**Answer evidence improved; document ranking was roughly flat.**
- *V2-K01* (Deepavali, in the 35-chunk calendar) now has its supporting passage first. *V2-S03*, which
  an intermediate design lost, is kept at position 5.
- *V2-K06* and *V2-S02* are still missed:
  - In *V2-K06*, the "Annexure-6" chunk is neither the semantic nor the keyword best chunk of its notice.
  - In *V2-S02*, the attendance rule shares one word with a paraphrased question in a 298-chunk handbook.

  These two are kept as known-failure (`xfail(strict=True)`) regression tests in
  `evaluation/test_passages_v2.py` rather than being tuned away.

**Why some ranking scores went down.** The six OCR documents did not exist when the v2 judgments were
written, so every one of them counts as non-relevant. Several are plausibly relevant:
- In *V2-F02* ("scholarship application", category filter) the official **Scholarship Policy** now ranks
  first in all three modes. The question is scored as a miss (RR 1.00 → 0.50, nDCG 1.00 → 0.735).
- In *V2-H04* (counselling) and *V2-S04* (lost property), regulations on substance abuse and ragging
  enter the top three. Their relevance has not been checked.

The v2 judgments were **not** re-graded to include these documents. That would be done after seeing their
ranks, which biases the judgment. A future v3 question set should be judged on the current corpus by
someone who has not seen retrieval output.

**Effect of the corpus policy.** Removing illustrative records helped exactly where Phase 6A found them
polluting results:
- *V2-K01* keyword search no longer ranks the synthetic calendar first (RR 0.50 → 1.00).
- *V2-H01* semantic search no longer includes the synthetic admissions record.
- The hostel-fee question (*V2-U01*) now retrieves official Kengeri pages instead of the synthetic hostel
  record.

**Unanswerable questions.** All four still pass the mocked checks: an honest refusal is accepted, and an
invented figure is rejected by the number check. The similarity gate stops none of them. "Capital of
Australia" scores 0.279, still above the 0.25 gate, and the other three are on-topic (0.46–0.65). Refusal
therefore depends on the model and the number check. **Real model behaviour is untested** (no Groq key).

**Latency.** Search latency rose slightly with the larger corpus (753 chunks, up from 585). Hybrid p50 went
from 17.7 to 21.0 ms in the saved runs; values vary run to run by a few milliseconds, so treat
differences of this size as indicative only. Measured separately, the evidence
selection used by `/api/ask` costs about 10 ms more than the 6A path: p50 20.6 → 31.0 ms and p95
27.3 → 41.7 ms over 125 calls. The extra cost is a second query embedding plus one chunk-level SQL query.
That is small next to an LLM call, and reusing the query vector would remove most of it.

**Limits.**
- The questions were written by the project author after reading the corpus.
- There are 21 judged questions; one rank change moves a mean by about 0.05.
- The OCR documents are unjudged.
- No real LLM answers were evaluated.
