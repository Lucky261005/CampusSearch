# Manual answer review

Automated checks (`RESULTS.md`) only test citation integrity and the rejection rules. Whether an answer is
**correct** and **supported by its cited evidence** has to be judged by a person reading the answer and the
cited passages.

**Status: not yet performed.** No real LLM answers have been generated yet: run
`python -m evaluation.run --groq` with a rotated `GROQ_API_KEY`, then fill in the table from
`evaluation/results/results.json` → `groq.per_query`.

## How to judge

- **Correct**: `yes` / `partial` / `no` / `n/a (refused)`. Compare with `reference_answer` in `queries.json`
  where present. For illustrative sources, "correct" means faithful to the sample record, not true of the
  real university.
- **Supported**: `yes` if every claim is stated in a cited passage; `partial` if some claims are not; `no` otherwise.
- **Refusal appropriate**: for an `insufficient_evidence` status, was refusing right (`yes`), or was the
  answer actually in the sources (`no`, a missed answer)?
- Record the reviewer and date. Do not mix these judgments into the automated check counts.

Reviewer: ______  Date: ______  Model: ______  Corpus fingerprint: ______

| Query ID | Status | Correct | Supported | Refusal appropriate | Notes |
|---|---|---|---|---|---|
| K02 | | | | | |
| K03 | | | | | |
| K04 | | | | | |
| S02 | | | | | |
| S03 | | | | | |
| S08 | | | | | |
| H01 | | | | | |
| H05 | | | | | |
| U01 | | | | | |
| U02 | | | | | |
| U03 | | | | | |

Summary (fill in): correct __/__, fully supported __/__, appropriate refusals __/__.
