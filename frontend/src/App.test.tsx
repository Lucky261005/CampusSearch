import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from './App'
import type { AskResponse, SearchHit, SearchResponse } from './types'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    id: 'd1',
    chunk_id: null,
    title: '[Illustrative] Hostel accommodation guidelines',
    source_url: 'illustrative://campussearch/hostel',
    source_type: 'json',
    provenance: 'illustrative',
    campus: 'Kengeri',
    category: 'campus-life',
    programme: null,
    semester: null,
    publication_date: '2026-04-02',
    score: 3.92,
    snippet: 'Hostel accommodation is allotted on application.',
    ...over,
  }
}

function searchBody(over: Partial<SearchResponse> = {}): SearchResponse {
  return { query: 'hostel', mode: 'keyword', total: 1, limit: 10, offset: 0, results: [hit()], ...over }
}

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

const lastUrl = () =>
  new URL(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0] as string, 'http://x')

function typeAndSearch(text: string) {
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search query' }), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
}

describe('search', () => {
  test('submits the query and renders a result card with metadata and provenance', async () => {
    fetchMock.mockReturnValueOnce(json(searchBody()))
    render(<App />)
    typeAndSearch('hostel')

    expect(await screen.findByText('[Illustrative] Hostel accommodation guidelines')).toBeInTheDocument()
    const url = lastUrl()
    expect(url.pathname).toBe('/api/search')
    expect(Object.fromEntries(url.searchParams)).toEqual({ q: 'hostel', mode: 'keyword', limit: '10', offset: '0' })
    expect(screen.getByText(/1/, { selector: 'span.font-semibold' })).toBeInTheDocument()
    expect(screen.getByText('Illustrative sample · not official')).toBeInTheDocument()
    expect(screen.getByText('Campus: Kengeri')).toBeInTheDocument()
    expect(screen.getByText('2026-04-02')).toBeInTheDocument()
    expect(screen.getByText(/BM25 3\.92/)).toBeInTheDocument()
    // synthetic identifiers are never rendered as links
    expect(screen.getByText('Sample record — no public source page')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /illustrative:/ })).toBeNull()
  })

  test('official sources link out safely in a new tab', async () => {
    fetchMock.mockReturnValueOnce(
      json(searchBody({ results: [hit({ provenance: 'official_web', source_type: 'html', source_url: 'https://christuniversity.in/', publication_date: null })] })),
    )
    render(<App />)
    typeAndSearch('christ')
    const link = await screen.findByRole('link', { name: /christuniversity\.in/ })
    expect(link).toHaveAttribute('href', 'https://christuniversity.in/')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('Official web page')).toBeInTheDocument()
    expect(document.querySelector('time')).toBeNull() // no invented dates
  })

  test('changing mode re-runs the current search with that mode', async () => {
    fetchMock.mockReturnValueOnce(json(searchBody()))
    render(<App />)
    typeAndSearch('hostel')
    await screen.findByText(/BM25 \d/)

    fetchMock.mockReturnValueOnce(json(searchBody({ mode: 'hybrid', results: [hit({ chunk_id: 'd1:0', score: 0.0328 })] })))
    fireEvent.click(screen.getByRole('radio', { name: 'Hybrid' }))
    expect(await screen.findByText(/RRF 0\.0328/)).toBeInTheDocument()
    expect(lastUrl().searchParams.get('mode')).toBe('hybrid')
  })

  test('filters are sent on submit, kept during pagination, and reset', async () => {
    fetchMock.mockReturnValueOnce(json(searchBody({ total: 25 })))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Campus'), { target: { value: 'Kengeri' } })
    fireEvent.change(screen.getByLabelText('Semester'), { target: { value: '5' } })
    typeAndSearch('exam')
    await screen.findByText('Page 1 of 3')
    expect(lastUrl().searchParams.get('campus')).toBe('Kengeri')
    expect(lastUrl().searchParams.get('semester')).toBe('5')

    fetchMock.mockReturnValueOnce(json(searchBody({ total: 25, offset: 10 })))
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    await screen.findByText('Page 2 of 3')
    expect(Object.fromEntries(lastUrl().searchParams)).toMatchObject({ offset: '10', campus: 'Kengeri', semester: '5', q: 'exam' })

    // editing a filter does not silently change what the visible results mean
    fireEvent.change(screen.getByLabelText('Campus'), { target: { value: 'Central' } })
    expect(screen.getByText(/Filters changed/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    fetchMock.mockReturnValueOnce(json(searchBody()))
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(lastUrl().searchParams.has('campus')).toBe(false)
    expect(lastUrl().searchParams.get('offset')).toBe('0')
  })

  test('empty results show a designed empty state', async () => {
    fetchMock.mockReturnValueOnce(json(searchBody({ total: 0, results: [] })))
    render(<App />)
    typeAndSearch('zzzz')
    expect(await screen.findByText('No matching documents')).toBeInTheDocument()
  })

  test('backend error detail is shown and retry re-sends the request', async () => {
    fetchMock.mockReturnValueOnce(json({ detail: 'semantic search unavailable: embedding model not loaded' }, 503))
    render(<App />)
    fireEvent.click(screen.getByRole('radio', { name: 'Semantic' }))
    typeAndSearch('hostel')
    expect(await screen.findByRole('alert')).toHaveTextContent('embedding model not loaded')

    fetchMock.mockReturnValueOnce(json(searchBody({ mode: 'semantic' })))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/Similarity/)).toBeInTheDocument()
  })

  test('validation errors and network failures are explained', async () => {
    fetchMock.mockReturnValueOnce(json({ detail: [{ loc: ['query', 'q'], msg: 'String should have at most 500 characters' }] }, 422))
    render(<App />)
    typeAndSearch('x')
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid q: String should have at most 500 characters')

    fetchMock.mockReturnValueOnce(Promise.reject(new TypeError('Failed to fetch')))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the CampusSearch API')
  })

  test('malformed success responses are rejected', async () => {
    fetchMock.mockReturnValueOnce(json({ unexpected: true }))
    render(<App />)
    typeAndSearch('hostel')
    expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response')
  })

  test('shows loading, and a newer search wins over a slower older one', async () => {
    let resolveFirst!: (r: Response) => void
    fetchMock.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          resolveFirst = resolve
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
    render(<App />)
    typeAndSearch('first')
    expect(screen.getByRole('status')).toHaveTextContent('Searching')

    fetchMock.mockReturnValueOnce(json(searchBody({ query: 'second', results: [hit({ title: 'Second result' })] })))
    typeAndSearch('second')
    expect(await screen.findByText('Second result')).toBeInTheDocument()
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true)

    await act(async () => resolveFirst(await json(searchBody({ results: [hit({ title: 'Stale result' })] }))))
    expect(screen.queryByText('Stale result')).toBeNull()
  })

  test('an inverted date range is flagged and not sent', () => {
    render(<App />)
    fireEvent.change(screen.getByLabelText('Published from'), { target: { value: '2026-12-01' } })
    fireEvent.change(screen.getByLabelText('Published to'), { target: { value: '2026-01-01' } })
    expect(screen.getByRole('alert')).toHaveTextContent('on or before')
    typeAndSearch('hostel')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

// --- Ask AI ---------------------------------------------------------------------------

const source = (n: number, over: Partial<AskResponse['sources'][number]> = {}) => ({
  n,
  document_id: `d${n}`,
  chunk_id: `d${n}:0`,
  title: `Source title ${n}`,
  source_url: n === 1 ? 'illustrative://campussearch/exam-registration' : 'https://christuniversity.in/',
  provenance: n === 1 ? ('illustrative' as const) : ('official_web' as const),
  provenance_label: n === 1 ? 'Illustrative sample record, not official university information' : 'Official university web page',
  page: null,
  excerpt: `Excerpt for source ${n}. Students register through the portal.`,
  quote: null,
  score: 0.03,
  ...over,
})

function askBody(over: Partial<AskResponse> = {}): AskResponse {
  return {
    question: 'How do I register?',
    status: 'answered',
    answer: 'Register through the student portal [1]. <b>bold</b>',
    message: 'Answer generated from the cited sources.',
    citations: [{ ...source(1), quote: 'Students register through the portal' }],
    sources: [source(1), source(2)],
    retrieval: { mode: 'hybrid', semantic_available: true, retrieved: 2 },
    ...over,
  }
}

function openAskAndSubmit(question = 'How do I register?') {
  fireEvent.click(screen.getByRole('tab', { name: 'Ask AI' }))
  fireEvent.change(screen.getByRole('searchbox', { name: 'Question' }), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('ask', () => {
  test('posts the question with filters and renders a cited answer safely', async () => {
    fetchMock.mockReturnValueOnce(json(askBody()))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Semester'), { target: { value: '5' } })
    openAskAndSubmit()

    expect(await screen.findByText(/Register through the student portal/)).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ask')
    expect(JSON.parse(init.body as string)).toEqual({ question: 'How do I register?', filters: { semester: 5 } })

    // generated text is rendered as text, never as HTML
    expect(screen.getByText(/<b>bold<\/b>/)).toBeInTheDocument()
    expect(document.querySelector('b')).toBeNull()

    const marker = screen.getByRole('link', { name: 'Source 1' })
    expect(marker).toHaveAttribute('href', '#source-1')
    const card = document.getElementById('source-1')!
    expect(within(card).getByText('Source title 1')).toBeInTheDocument()
    expect(within(card).getByText('Cited')).toBeInTheDocument()
    expect(within(card).getByText('“Students register through the portal”')).toBeInTheDocument()
    expect(within(card).getByText('Illustrative sample · not official')).toBeInTheDocument()
    expect(within(document.getElementById('source-2')!).getByText('Official web page')).toBeInTheDocument()

    expect(screen.getByText(/cites illustrative sample records/i)).toBeInTheDocument()
    expect(screen.getByText(/does not guarantee the answer is correct/)).toBeInTheDocument()
  })

  test('citation markers that do not match a returned citation are not linked', async () => {
    fetchMock.mockReturnValueOnce(json(askBody({ answer: 'See [1] and [7].' })))
    render(<App />)
    openAskAndSubmit()
    await screen.findByRole('link', { name: 'Source 1' })
    expect(screen.queryByRole('link', { name: 'Source 7' })).toBeNull()
  })

  test('insufficient evidence is shown as such, with no answer text', async () => {
    fetchMock.mockReturnValueOnce(
      json(askBody({ status: 'insufficient_evidence', answer: null, citations: [], message: 'The retrieved sources do not establish an answer to this question.' })),
    )
    render(<App />)
    openAskAndSubmit('What is the exact hostel fee in rupees?')
    expect(await screen.findByText('Not enough evidence')).toBeInTheDocument()
    expect(screen.getByText('The retrieved sources do not establish an answer to this question.')).toBeInTheDocument()
    expect(screen.getByText('Retrieved passages')).toBeInTheDocument()
    expect(screen.queryByText('Cited')).toBeNull()
  })

  test('LLM fallback explains why and shows retrieved passages', async () => {
    fetchMock.mockReturnValueOnce(
      json(askBody({ status: 'fallback', answer: null, citations: [], message: 'answer generation is not configured (GROQ_API_KEY unset)' })),
    )
    render(<App />)
    openAskAndSubmit()
    expect(await screen.findByText('AI answer unavailable')).toBeInTheDocument()
    expect(screen.getByText(/GROQ_API_KEY unset/)).toBeInTheDocument()
    expect(screen.getByText(/most relevant retrieved passages/)).toBeInTheDocument()
    expect(document.getElementById('source-2')).not.toBeNull()
  })

  test('ask button is disabled while a request is in flight; errors can be retried', async () => {
    fetchMock.mockReturnValueOnce(json({ detail: 'question must not be blank' }, 422))
    render(<App />)
    openAskAndSubmit()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('generating an answer')
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()

    fetchMock.mockReturnValueOnce(json(askBody()))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/Register through the student portal/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
