_Hand-written after the v2 run on corpus fingerprint `ecdcc06fd3e76346` (48 documents: 33 official,
3 supplied, 12 illustrative; 585 chunks). If the fingerprint at the top of this report differs, re-read
the tables before trusting this section._

**How v2 differs from v1.** v1 (`RESULTS.md`, fingerprint `5a3eef0bde227d7c`) tested 13 documents, 12 of
them short synthetic records. v2 asks 26 new questions (21 judged, plus 4 unanswerable and 1 conflicting
multi-document case) about the official pages and PDFs ingested in Phase 6A. The two benchmarks have
different questions and corpora, so their scores are **not directly comparable**. v1 is kept unchanged as
a historical baseline, and the runner refuses to overwrite it.

| Benchmark | Corpus | Keyword nDCG@10 | Semantic nDCG@10 | Hybrid nDCG@10 |
|---|---|---:|---:|---:|
| v1 | 13 docs (12 illustrative) | 0.807 | 0.936 | 0.892 |
| v2 | 48 docs (33 official, 3 supplied, 12 illustrative) | 0.770 | 0.916 | 0.885 |

**Which mode did best here?** Semantic, on all three metrics. Hybrid was close behind, and keyword was
lowest. As in v1, almost all of the gap comes from the 7 paraphrase (`semantic`) questions: keyword MRR
0.46, semantic 0.89, hybrid 0.71. On keyword-style, filtered and multi-document questions, the three modes
are within a few points of each other. One author-written set of 21 judged questions is too small to rank
the modes in general.

**Where semantic beats keyword.**
- *V2-S03* "where can I see a doctor…": keyword RR 0.00, semantic 1.00 (the health centre page).
- *V2-S05* "leave my degree part way through": keyword 0.20, semantic 1.00 (the entry and exit guidelines).
- *V2-S07* "help from the state government for students living in Karnataka": keyword 0.33, semantic 1.00
  (the SSP notice).

**Where hybrid helps and hurts.** Hybrid matched or beat semantic on the `hybrid` and `multi-document`
types. On paraphrases, equal-weight fusion pulled in poor keyword rankings: *V2-S03* went from 1.00 to
0.25 and *V2-S05* from 1.00 to 0.50. This is the same pattern as v1. Keyword search wins on exact
deadlines: for *V2-K02* (MBA last date), keyword ranks the MBA page first, while semantic and hybrid put
the academic calendar above it.

**Failures and likely causes.**
1. **Illustrative records pollute real questions.** For *V2-U01* (hostel fee) the top result in every mode
   is the synthetic "[ILL] Hostel accommodation guidelines" record. For *V2-K01* keyword search ranks the
   illustrative calendar above the real one. Now that official data exists, keeping the seed records in
   the default corpus does more harm than good. Removing them, or excluding them by default, is
   recommended for Phase 6B.
2. **Hybrid evidence is the wrong chunk of long documents.** The RAG checks found the right *document*
   but not the chunk containing the fact for 3 of 9 answerable questions:
   - *V2-K01* (Deepavali, academic calendar, 35 chunks);
   - *V2-K06* (PM-Vidyalaxmi documents);
   - *V2-S02* (attendance rule, student handbook, 298 chunks).

   Hybrid fuses rankings per document and picks each document's best chunk by semantic similarity
   alone. A keyword-matching chunk inside a long document is therefore never offered as evidence. Ask AI
   would show the right source but an unhelpful passage. This is the most important retrieval defect
   left, and it should be fixed in Phase 6B (for example, chunk-level keyword scores, or several top
   chunks per document).
3. **The similarity gate is weaker on the bigger corpus.** In v1, off-topic questions scored 0.03 to
   0.21. In v2, "What is the capital of Australia?" reaches 0.279 against the student handbook, above the
   0.25 gate. None of the four unanswerable questions were stopped before the LLM; they relied on the
   model's refusal and the number check, both mocked here. The threshold was **not** retuned to make this
   pass.
4. *V2-S06* ("how large is the campus near Mysore Road") fails in all modes (RR 0.20). The
   "How to reach Kengeri" page, which mentions Mysore Road but not the campus size, outranks the campus
   page that states 78.5 acres.

**Corpus limits.**
- Five official regulation PDFs (code of conduct, substance abuse, IT, ragging) and the official
  Scholarship Policy are scans with no text layer. Five of the eight supplied scholarship posters are
  images. All are absent because OCR is not supported (see `backend/data/SOURCES.md`).
- The handbook contains its own copy of the academic calendar, and some dates differ from the revised
  calendar PDF (for example, convocation). *V2-M01* has no reference answer for this reason.
- Some PDF text is extracted without spaces between words (e.g. "inaccordance withthe" in the entry and
  exit guidelines). This hurts keyword matching for those documents.

**What would make the conclusions stronger.** OCR for the scanned regulations; questions collected from
real students rather than written by the author; relevance judged by someone who has not seen the
retrieval output; and at least 100 judged questions.
