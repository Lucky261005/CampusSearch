// Mirrors backend/app/models.py and the /api/search, /api/ask signatures in backend/app/main.py.

export type SearchMode = 'keyword' | 'semantic' | 'hybrid'
export type Provenance = 'official_web' | 'supplied_document' | 'illustrative'
export type SourceType = 'json' | 'text' | 'html' | 'pdf'

export interface Filters {
  campus: string
  category: string
  programme: string
  semester: string // '' or '1'..'12'
  date_from: string // '' or YYYY-MM-DD
  date_to: string
}

export const EMPTY_FILTERS: Filters = {
  campus: '',
  category: '',
  programme: '',
  semester: '',
  date_from: '',
  date_to: '',
}

export interface SearchHit {
  id: string
  chunk_id: string | null
  title: string
  source_url: string
  source_type: SourceType
  provenance: Provenance
  campus: string | null
  category: string | null
  programme: string | null
  semester: number | null
  publication_date: string | null
  score: number
  snippet: string
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  total: number
  limit: number
  offset: number
  results: SearchHit[]
}

export interface Citation {
  n: number
  document_id: string
  chunk_id: string | null
  title: string
  source_url: string
  provenance: Provenance
  provenance_label: string
  page: number | null
  excerpt: string
  quote: string | null
}

export interface SourcePassage extends Citation {
  score: number
}

export type AskStatus = 'answered' | 'insufficient_evidence' | 'fallback'

export interface AskResponse {
  question: string
  status: AskStatus
  answer: string | null
  message: string
  citations: Citation[]
  sources: SourcePassage[]
  retrieval: { mode?: string; semantic_available?: boolean; retrieved?: number }
}
