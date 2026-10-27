import type { SearchHit, SearchMode } from '../types'
import { ProvenanceLabel, SourceLink } from './ProvenanceLabel'

const SCORE_NAME: Record<SearchMode, string> = {
  keyword: 'BM25',
  semantic: 'Similarity',
  hybrid: 'RRF',
}

export function Meta({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-ink-soft">{children}</span>
  )
}

export function SearchResultCard({ hit, mode, rank }: { hit: SearchHit; mode: SearchMode; rank: number }) {
  return (
    <article className="lift group rounded-2xl border border-line bg-white p-5 hover:border-accent/40">
      <div className="flex flex-wrap items-center gap-2">
        <ProvenanceLabel provenance={hit.provenance} />
        <span className="text-xs uppercase tracking-wide text-muted">{hit.source_type}</span>
        <span className="ml-auto font-mono text-xs text-muted" title={`${SCORE_NAME[mode]} score`}>
          #{rank} · {SCORE_NAME[mode]} {hit.score.toFixed(mode === 'hybrid' ? 4 : 2)}
        </span>
      </div>
      <h3 className="mt-2 font-serif text-lg leading-snug text-ink">{hit.title}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">{hit.snippet}</p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {hit.campus && <Meta>Campus: {hit.campus}</Meta>}
        {hit.category && <Meta>{hit.category}</Meta>}
        {hit.programme && <Meta>{hit.programme}</Meta>}
        {hit.semester != null && <Meta>Semester {hit.semester}</Meta>}
        {hit.publication_date && (
          <Meta>
            Published <time dateTime={hit.publication_date}>{hit.publication_date}</time>
          </Meta>
        )}
      </div>
      <div className="mt-3 border-t border-line pt-3">
        <SourceLink url={hit.source_url} />
      </div>
    </article>
  )
}
