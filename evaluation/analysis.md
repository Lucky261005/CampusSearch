_Hand-written after the run on corpus fingerprint `5a3eef0bde227d7c`. If the fingerprint at the top of
this report differs, re-read the tables before trusting this section._

**Which mode did best here?** Semantic had the highest nDCG@10 and MRR. Hybrid had the highest
Recall@5: it was the only mode with every judged relevant document in its top five. Keyword was
lowest on all three. Almost all of the difference comes from the 8 `semantic` paraphrase queries.
On the 7 `keyword` and 6 `hybrid`-type queries, all three modes put a relevant document first.
This is one small, author-built dataset, so it says nothing general about which mode is better.

**Where semantic beats keyword.** Paraphrases that share no terms with the source:
- *S07* "can I take textbooks home": keyword has no relevant document in its top three (RR 0.00);
  semantic ranks the library record first.
- *S03* "how many classes must I attend…": keyword RR 0.17, semantic 1.00 (the attendance record).
- *S02* "sign up for my end of term exams": keyword RR 0.50, semantic 1.00.

The keyword engine has no stemming or synonyms, so "exams" does not match "examination" and
"textbooks" does not match "books".

**Where hybrid helps and where it hurts.** Hybrid gave the best Recall@5. When one ranking misses a
document, the other usually ranks it within the top five. But reciprocal rank fusion gives both
rankings equal weight. When keyword matching ranks unrelated documents highly, fusion pulls them
above the correct semantic hit:
- *S03*: semantic 1.00 → hybrid 0.33.
- *S07*: semantic 1.00 → hybrid 0.25.

A keyword weight below 1, or fusing only when both rankings agree, would be worth testing on a larger set.

**Failures and likely causes.**
- *S01* "where can I stay while studying" fails in every mode (keyword 0.00, semantic 0.25,
  hybrid 0.20). The hostel record never uses "stay", and MiniLM ranked the placements and homepage
  chunks higher.
- The official homepage appears in many top-3 lists because its long, varied text matches many
  topics weakly.
- *U01* (World Cup) still returns the homepage in keyword mode through the word "World", which
  appears in a ranking headline. The semantic similarity (0.207) is below the 0.25 threshold, so
  RAG refuses without calling the LLM. That margin is small.

**RAG checks.** The verification rules accepted every correctly cited mocked answer and rejected
every fabricated citation ID, unsupported marker, misquote and invented number. For all 8
answerable queries with reference answers, the supporting chunk was in the top five. These
checks test the guardrails, not the language model: no real model answer was evaluated, and
citation validity is not factual correctness.

**Limits of this evaluation.**
- **Synthetic corpus.** 12 of 13 documents are synthetic samples written for this project. They
  are short and each covers one topic, which makes retrieval unrealistically easy. The single
  real page (the homepage) is long and noisy.
- **Self-built dataset.** The queries and relevance judgments were written by the system's author
  after seeing the corpus. The paraphrase queries were deliberately written without the source's
  keywords. Both choices favour semantic retrieval and inflate all scores.
- **Small sample.** 23 judged queries; changing the rank of one document moves a mean by about 0.05.

**What would make the conclusions stronger.**
- Ingest real, verified pages: the academic calendar, examination regulations, the fee structure,
  hostel and admissions pages, and programme syllabi (PDF support is needed for most of these).
- Write 100+ queries from real student questions, with graded judgments by two people and their
  agreement measured.
- Remove or isolate the illustrative records before reporting any numbers as real search quality.
