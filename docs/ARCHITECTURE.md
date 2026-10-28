# Architecture

CampusSearch is a local, three-part application: a React frontend, a FastAPI backend, and PostgreSQL
with the pgvector extension. Everything runs on the user's machine. The only optional external call is
to Groq, made with the user's own key.

```
Browser (React + Vite, :5173)
   │  /api/* (Vite dev proxy)
   ▼
FastAPI backend (:8000)
   ├── keyword search ── in-memory BM25 index over documents (built at startup)
   ├── semantic search ─ pgvector cosine search over chunk embeddings
   ├── hybrid search ── Reciprocal Rank Fusion of the two rankings
   └── /api/ask ─────── retrieval ─► evidence passages ─► Groq (optional) ─► citation checks
   ▼
PostgreSQL + pgvector (Docker, :5432)
   documents · chunks (384-dim embeddings) · ai_usage (local AI quota)
```

## Ingestion (`backend/app/ingest.py`, `pdf.py`, `ocr.py`)

- **Sources:** official pages and PDFs listed in `backend/data/sources.json`, fetched one request per second;
  optional local ZIPs of PDFs; optional illustrative seed records.
- **Text extraction:**
  - HTML: main-content extraction that drops navigation, footers and repeated blocks.
  - PDF: pypdf. A page switches to layout extraction only when that repairs run-together words without
    losing text. Repeated headers, footers and page numbers are removed.
  - Scanned PDFs: rejected, or recognised with the optional local OCR (RapidOCR).
- **Provenance:** every document is labelled `official_web`, `supplied_document` or `illustrative`.
- **Change handling:** the content hash detects changes. An updated document's chunks are deleted in the same
  transaction, so stale text is never searchable. Metadata-only changes keep the chunks.

## Indexing (`reindex.py`, `embeddings.py`)

- Chunks are 150-word windows with a 30-word overlap. They never cross a PDF page boundary, so citations
  keep page numbers.
- Embeddings come from `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions) on the CPU. A chunk is
  re-embedded only when its text or the model changes.

## Retrieval (`search.py`, `retrieval.py`)

- **Keyword:** a hand-written inverted index with BM25 (k1 1.5, b 0.75). The formula is documented in
  `search.py`.
- **Semantic:** pgvector exact cosine search. The best chunk per document gives the document's rank.
- **Hybrid:** Reciprocal Rank Fusion (k = 60) of the keyword and semantic document rankings.
- **Corpus policy:** illustrative records are excluded unless a request opts in. The same rule is applied
  in Python (keyword) and in SQL (semantic and hybrid).
- **Answer evidence:** each top document nominates its best semantic chunk and its best keyword chunk.
  Candidates are fused at passage level, so an exact-term passage inside a long document is not lost.

## Answers (`rag.py`, `usage.py`)

1. **Off-topic gate:** questions whose best chunk similarity is below 0.25 are refused without calling the
   model.
2. **Bounded context:** the context holds up to 6,000 characters of numbered sources. It is marked as
   untrusted data and labelled with provenance.
3. **Model reply:** Groq (`openai/gpt-oss-120b` by default) must return JSON citations with a verbatim
   quote for each source.
4. **Checks in code:** every citation must point to a passage sent in that request and quote it exactly.
   Every number in the answer must appear in a cited source. Any failure rejects the whole answer.
5. **Local quota:** an atomic Postgres counter limits AI requests to 10 per day and 3 per minute. It
   survives restarts, and no request is sent if the counter is unavailable. After an HTTP 429, nothing is
   sent until Groq's `retry-after` time has passed.

Without a key, or when any limit applies, Ask AI returns the retrieved passages with a clear reason.

## Evaluation (`evaluation/`)

- Recall@5, nDCG@10 and MRR for keyword, semantic and hybrid search, plus citation-integrity checks with a
  mocked model, and latency.
- Saved benchmarks are versioned (v1, v2, v2-6b). Historical runs are frozen and cannot be overwritten.
- See `evaluation/RESULTS_v2_6b.md` and the limitations described there.
