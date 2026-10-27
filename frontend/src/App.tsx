import { useEffect, useState } from 'react'
import { ask, filterEntries, PAGE_SIZE, search } from './api'
import { AnswerPanel } from './components/AnswerPanel'
import { Header, type Tab } from './components/Header'
import { Pagination } from './components/Pagination'
import { SearchBar } from './components/SearchBar'
import { SearchFilters } from './components/SearchFilters'
import { MODE_HINT, SearchModeSelector } from './components/SearchModeSelector'
import { Meta, SearchResultCard } from './components/SearchResultCard'
import { EmptyState, ErrorState, LoadingState } from './components/States'
import { EMPTY_FILTERS, type AskResponse, type Filters, type SearchMode, type SearchResponse } from './types'

type Async<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: string }

interface SearchRequest {
  q: string
  mode: SearchMode
  filters: Filters
  offset: number
}

interface AskRequest {
  question: string
  filters: Filters
}

const SEARCH_EXAMPLES = ['academic calendar', 'hostel accommodation', 'exam registration', 'scholarships']
const ASK_EXAMPLES = [
  'How do I register for end-semester exams?',
  'What is the exact hostel fee in rupees?',
]

const sameFilters = (a: Filters, b: Filters) => JSON.stringify(a) === JSON.stringify(b)
const message = (e: unknown) => (e instanceof Error ? e.message : 'Unexpected error')

/** Runs `run` whenever `req` changes; aborts the previous request so stale responses never land. */
function useRequest<R, T>(req: R | null, run: (r: R, signal: AbortSignal) => Promise<T>, attempt: number) {
  const [state, setState] = useState<Async<T>>({ status: 'idle' })
  useEffect(() => {
    if (req === null) return
    const ac = new AbortController()
    setState({ status: 'loading' })
    run(req, ac.signal).then(
      (data) => setState({ status: 'success', data }),
      (e) => {
        if (!ac.signal.aborted) setState({ status: 'error', error: message(e) })
      },
    )
    return () => ac.abort()
    // `run` is a stable module-level function
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req, attempt])
  return state
}

const runSearch = (r: SearchRequest, s: AbortSignal) => search(r.q, r.mode, r.filters, r.offset, s)
const runAsk = (r: AskRequest, s: AbortSignal) => ask(r.question, r.filters, s)

export default function App() {
  const [tab, setTab] = useState<Tab>('search')
  const [query, setQuery] = useState('')
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<SearchMode>('keyword')
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)

  const [searchReq, setSearchReq] = useState<SearchRequest | null>(null)
  const [askReq, setAskReq] = useState<AskRequest | null>(null)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const [askAttempt, setAskAttempt] = useState(0)
  const searchState = useRequest<SearchRequest, SearchResponse>(searchReq, runSearch, searchAttempt)
  const askState = useRequest<AskRequest, AskResponse>(askReq, runAsk, askAttempt)

  const badRange = Boolean(filters.date_from && filters.date_to && filters.date_from > filters.date_to)
  const activeReq = tab === 'search' ? searchReq : askReq
  const pending = activeReq !== null && !sameFilters(filters, activeReq.filters)

  const submitSearch = (q = query) => {
    if (badRange) return
    setQuery(q)
    setSearchReq({ q: q.trim(), mode, filters, offset: 0 })
  }
  const submitAsk = (q = question) => {
    if (badRange) return
    setQuestion(q)
    setAskReq({ question: q.trim(), filters })
  }
  const changeMode = (m: SearchMode) => {
    setMode(m)
    if (searchReq) setSearchReq({ ...searchReq, mode: m, offset: 0 })
  }
  const resetFilters = () => {
    setFilters(EMPTY_FILTERS)
    if (tab === 'search' && searchReq) setSearchReq({ ...searchReq, filters: EMPTY_FILTERS, offset: 0 })
    if (tab === 'ask' && askReq) setAskReq({ ...askReq, filters: EMPTY_FILTERS })
  }

  return (
    <div className="min-h-screen">
      <Header tab={tab} onTab={setTab} />

      <section className="border-b border-line bg-white">
        <div className="mx-auto max-w-6xl px-4 pb-8 pt-10 sm:px-6 sm:pt-14">
          <h1 className="max-w-2xl font-serif text-3xl leading-tight tracking-tight text-ink sm:text-4xl">
            {tab === 'search'
              ? 'Search university information, by keyword or by meaning.'
              : 'Ask a question. Get an answer grounded in cited sources.'}
          </h1>
          <p className="mt-3 max-w-2xl text-base text-muted">
            {tab === 'search'
              ? 'CampusSearch indexes CHRIST (Deemed to be University) information with a custom BM25 engine, vector search and hybrid rank fusion.'
              : 'Answers are generated only from retrieved passages. When the sources do not establish an answer, CampusSearch says so instead of guessing.'}
          </p>
          <div className="mt-6 max-w-3xl">
            {tab === 'search' ? (
              <SearchBar
                key="search"
                value={query}
                onChange={setQuery}
                onSubmit={() => submitSearch()}
                label="Search query"
                placeholder="Search programmes, admissions, examinations…"
                submitLabel="Search"
                examples={SEARCH_EXAMPLES}
                onExample={submitSearch}
              />
            ) : (
              <SearchBar
                key="ask"
                value={question}
                onChange={setQuestion}
                onSubmit={() => submitAsk()}
                label="Question"
                placeholder="Ask about admissions, exams, hostels…"
                submitLabel="Ask"
                busy={askState.status === 'loading'}
                examples={ASK_EXAMPLES}
                onExample={submitAsk}
              />
            )}
          </div>
          {tab === 'search' && (
            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
              <SearchModeSelector value={mode} onChange={changeMode} />
              <span className="text-xs text-muted">{MODE_HINT[mode]}</span>
            </div>
          )}
        </div>
      </section>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-8 sm:px-6 lg:grid-cols-[260px_1fr]">
        <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <SearchFilters
            value={filters}
            onChange={setFilters}
            onReset={resetFilters}
            pending={pending}
            applyLabel={tab === 'search' ? 'Search' : 'Ask'}
          />
          <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-900">
            Most records in this demo corpus are <strong>illustrative samples</strong>, not official
            university information. Each result is labelled with its provenance.
          </p>
        </aside>

        <div className="min-w-0">
          {tab === 'search' ? (
            <SearchResults
              req={searchReq}
              state={searchState}
              onRetry={() => setSearchAttempt((n) => n + 1)}
              onPage={(offset) => searchReq && setSearchReq({ ...searchReq, offset })}
            />
          ) : askState.status === 'idle' ? (
            <EmptyState title="Ask a question to get started">
              Try an example above. The second one asks for a figure the corpus does not contain,
              to show how unsupported questions are handled.
            </EmptyState>
          ) : askState.status === 'loading' ? (
            <LoadingState label="Retrieving sources and generating an answer" />
          ) : askState.status === 'error' ? (
            <ErrorState message={askState.error} onRetry={() => setAskAttempt((n) => n + 1)} />
          ) : (
            <AnswerPanel data={askState.data} />
          )}
        </div>
      </main>
    </div>
  )
}

function SearchResults({
  req,
  state,
  onRetry,
  onPage,
}: {
  req: SearchRequest | null
  state: Async<SearchResponse>
  onRetry: () => void
  onPage: (offset: number) => void
}) {
  if (!req || state.status === 'idle') {
    return (
      <EmptyState title="Start with a search">
        Use Keyword for exact terms, Semantic for questions phrased in your own words, or Hybrid
        to combine both.
      </EmptyState>
    )
  }
  if (state.status === 'loading') return <LoadingState label="Searching" />
  if (state.status === 'error') return <ErrorState message={state.error} onRetry={onRetry} />

  const { data } = state
  const applied = filterEntries(req.filters)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
        <p aria-live="polite" className="text-sm text-ink">
          <span className="font-semibold">{data.total}</span> {data.total === 1 ? 'result' : 'results'} for{' '}
          <span className="font-semibold">“{data.query}”</span>
          <span className="text-muted"> · {data.mode} search</span>
        </p>
        {applied.map(([k, v]) => (
          <Meta key={k}>
            {k.replace('_', ' ')}: {v}
          </Meta>
        ))}
      </div>
      {data.results.length === 0 ? (
        <EmptyState title="No matching documents">
          Try different words, switch to Semantic or Hybrid mode, or remove some filters.
        </EmptyState>
      ) : (
        <>
          <ol className="space-y-3">
            {data.results.map((hit, i) => (
              <li key={`${hit.id}:${hit.chunk_id ?? ''}`}>
                <SearchResultCard hit={hit} mode={data.mode} rank={data.offset + i + 1} />
              </li>
            ))}
          </ol>
          <Pagination offset={data.offset} limit={data.limit || PAGE_SIZE} total={data.total} onPage={onPage} />
        </>
      )}
    </div>
  )
}
