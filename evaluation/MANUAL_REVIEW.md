# Manual answer review

Automated checks (`RESULTS.md`) only test citation integrity and the rejection rules. Whether an answer is
**correct** and **supported by its cited evidence** has to be judged by a person reading the answer and the
cited passages.

## What is mocked and what is real

| Evidence | Source of answers | Where |
|---|---|---|
| Automated RAG checks in `RESULTS*.md` and the backend tests | **Mocked** model replies (fixed JSON) | `evaluation/run.py`, `backend/tests/` |
| Provider error handling (401, 429, retired model, timeouts, malformed replies) | **Mocked** HTTP responses | `backend/tests/test_provider.py` |
| Phase 7 real-provider check | **Real Groq** answers through `/api/ask` | `python -m evaluation.groq_ask_check` → `evaluation/private/groq_ask_log.json` |

**Status: six real Groq scenarios run and reviewed (Phase 7).** The backend was started in a terminal holding
the key; the check script and reviewer never saw it. The full answers and the exact passages the model
received are in `evaluation/private/groq_ask_log.json`, which is gitignored and not committed. The v1 table
at the end is still unreviewed.

## How to judge

- **Correct**: `yes` / `partial` / `no` / `n/a (refused)`. Compare with `reference_answer` in `queries.json`
  where present. For illustrative sources, "correct" means faithful to the sample record, not true of the
  real university.
- **Supported**: `yes` if every claim is stated in a cited passage; `partial` if some claims are not; `no` otherwise.
- **Refusal appropriate**: for an `insufficient_evidence` status, was refusing right (`yes`), or was the
  answer actually in the sources (`no`, a missed answer)?
- Record the reviewer and date. Do not mix these judgments into the automated check counts.

## Phase 7 real-provider check (`evaluation/groq_ask_check.py`)

Reviewer: Claude (assistant), reading each answer against the passages sent to the model. This is an
author-side review and should be confirmed by a human. Date: 2026-10-28. Model (`llm_model`):
`openai/gpt-oss-120b`. `llm_called` was true for all seven requests. Corpus fingerprint: `5b6786bd30c3a3d3`.

| ID | Scenario | Expected | Status | Correct | Supported | Refusal appropriate | Notes |
|---|---|---|---|---|---|---|---|
| G1 | single source | MBA deadline from the MBA admission page | answered | yes | yes | n/a | "25 January 2027", citing the official MBA page; the quote matches the page verbatim. |
| G2 | multi source | portals + NSP registration steps, several documents | answered | yes | yes (minor paraphrase) | n/a | Portals come from the handbook (official): "NSP & State Scholarship Portals (SSP)". The OTR and Aadhaar steps come from the NSP notice, a **supplied, unverified** document. "then provide the required … documents" summarises the notice's "Documents Required" list; the word "then" implies an order the notice does not state. Led to the unverified-source warning added in Phase 7. |
| G3 | unanswerable | refuse: no hostel fee in the corpus | insufficient_evidence | n/a | n/a | yes | No amount invented. |
| G4 | unsupported date | refuse or state the limitation: "by the due date", no date given | insufficient_evidence | n/a | n/a | yes | No deadline invented. |
| G5 | illustrative excluded | no illustrative source retrieved or cited | insufficient_evidence | n/a | n/a | yes | 0 illustrative sources retrieved. |
| G5b | illustrative opt-in | any answer from samples says they are not official | fallback (HTTP 429) on the first run; `insufficient_evidence` on one retry | n/a | n/a | yes | The first request hit Groq's rate limit. The app showed the safe "rate limit or quota" message, did not retry, and presented no answer. A single manual retry minutes later: the model refused even with two illustrative records in its context, which state no fee amount. |

Summary: correct 2/2 answered, fully supported 2/2 (G2 with a minor paraphrase), appropriate refusals 4/4.
**Limits:** seven requests on one model, reviewed by the system's author. This is a smoke test of real
behaviour, not an accuracy measurement. G2's answer depends on a supplied document whose official source is
unverified.

## v1 benchmark questions (Phase 5 template)

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
