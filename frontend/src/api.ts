import type { AskResponse, Filters, SearchMode, SearchResponse } from './types'

// Empty default = same origin; the Vite dev/preview server proxies /api to the backend.
const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export const PAGE_SIZE = 10

export class ApiError extends Error {
  readonly status: number | null

  constructor(message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] }
    const field = first.loc?.[first.loc.length - 1]
    return `Invalid ${field ?? 'request'}: ${first.msg ?? 'validation failed'}`
  }
  return `Request failed (HTTP ${status})`
}

async function request<T>(
  path: string,
  init: RequestInit,
  isValid: (body: unknown) => boolean,
): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError('Cannot reach the CampusSearch API. Check that the backend is running.')
  }
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // handled below: non-JSON success bodies are malformed
  }
  if (!res.ok) throw new ApiError(detailMessage(body, res.status), res.status)
  if (!isValid(body)) throw new ApiError('The API returned an unexpected response.', res.status)
  return body as T
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

export function filterEntries(filters: Filters): [string, string][] {
  return Object.entries(filters).filter(([, v]) => v.trim() !== '') as [string, string][]
}

export function search(
  q: string,
  mode: SearchMode,
  filters: Filters,
  offset: number,
  signal?: AbortSignal,
  includeIllustrative = false,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q, mode, limit: String(PAGE_SIZE), offset: String(offset) })
  for (const [k, v] of filterEntries(filters)) params.set(k, v.trim())
  // the backend excludes synthetic sample records unless asked
  if (includeIllustrative) params.set('include_illustrative', 'true')
  return request<SearchResponse>(
    `/api/search?${params}`,
    { signal },
    (b) => isObj(b) && Array.isArray(b.results) && typeof b.total === 'number',
  )
}

export function ask(
  question: string,
  filters: Filters,
  signal?: AbortSignal,
  includeIllustrative = false,
): Promise<AskResponse> {
  const f: Record<string, string | number | boolean> = {}
  for (const [k, v] of filterEntries(filters)) f[k] = k === 'semester' ? Number(v) : v.trim()
  if (includeIllustrative) f.include_illustrative = true
  return request<AskResponse>(
    '/api/ask',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, filters: f }),
      signal,
    },
    (b) =>
      isObj(b) &&
      typeof b.status === 'string' &&
      Array.isArray(b.citations) &&
      Array.isArray(b.sources),
  )
}
