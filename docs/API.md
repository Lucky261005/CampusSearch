# API reference

The backend runs locally at `http://127.0.0.1:8000`. FastAPI also serves interactive docs at `/docs`.
There is no authentication: the API is meant for your own machine only.

## `GET /health`

Returns `{"status": "ok"}`.

## `GET /api/search`

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `q` | string, ≤ 500 chars | `""` | An empty query returns no results |
| `mode` | `keyword` \| `semantic` \| `hybrid` | `keyword` | Semantic and hybrid need the embedding model; otherwise they return 503 |
| `campus`, `category`, `programme` | string | none | Exact match, case-insensitive |
| `semester` | int 1–12 | none | |
| `date_from`, `date_to` | `YYYY-MM-DD` | none | Excludes undated documents; an inverted range returns 400 |
| `include_illustrative` | bool | `false` | Opt in to synthetic sample records |
| `limit` | int 1–50 | 10 | |
| `offset` | int ≥ 0 | 0 | |

The response is `{query, mode, total, limit, offset, results[]}`. Each result has:

- `id`, `chunk_id` (null in keyword mode), `title`, `source_url`, `source_type`, `provenance`;
- `campus`, `category`, `programme`, `semester`, `publication_date`;
- `score` (BM25, cosine similarity, or RRF, depending on the mode) and `snippet`.

Invalid parameters return **422**.

## `POST /api/ask`

```json
{ "question": "When is the last date to apply for the MBA selection process?",
  "filters": { "campus": null, "include_illustrative": false },
  "top_k": 5 }
```

- `question`: 1–500 characters, not blank.
- `top_k`: 1–8 passages.
- `filters`: takes the same fields as the search filters.

The response is `{question, status, answer, message, citations[], sources[], retrieval}`:

- **`status`** is one of:
  - `answered`: a checked answer with citations;
  - `insufficient_evidence`: the sources do not establish an answer;
  - `fallback`: AI is unavailable; `message` explains why (no key, a local or provider limit, or an
    error). `sources` still holds the retrieved passages.
- **Each citation and source** has `n`, `document_id`, `chunk_id`, `title`, `source_url`, `provenance`,
  `provenance_label`, `page`, `excerpt`, and `quote` (on citations).
- **`retrieval`** has `mode`, `semantic_available`, `retrieved`, `llm_called` (whether a request was
  actually sent) and `llm_model`.

The server-side `GROQ_API_KEY` is never returned, logged or sent to the browser.
