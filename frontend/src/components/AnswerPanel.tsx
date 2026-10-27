import type { AskResponse, SourcePassage } from '../types'
import { ProvenanceLabel, SourceLink } from './ProvenanceLabel'

/** Plain-text answer with [n] markers turned into links; nothing is parsed as HTML. */
function AnswerText({ text, cited }: { text: string; cited: Set<number> }) {
  const parts = text.split(/(\[\d+\])/g)
  return (
    <p className="whitespace-pre-line text-[15px] leading-relaxed text-ink">
      {parts.map((part, i) => {
        const n = /^\[(\d+)\]$/.exec(part)?.[1]
        if (n && cited.has(Number(n))) {
          return (
            <a
              key={i}
              href={`#source-${n}`}
              aria-label={`Source ${n}`}
              className="mx-0.5 rounded bg-accent-soft px-1 align-super text-[11px] font-semibold text-accent-strong no-underline hover:bg-accent hover:text-white"
            >
              {n}
            </a>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </p>
  )
}

export function CitationList({ sources, cited }: { sources: SourcePassage[]; cited: Set<number> }) {
  return (
    <ol className="space-y-3">
      {sources.map((s) => {
        const quote = s.quote ?? null
        return (
          <li
            key={s.n}
            id={`source-${s.n}`}
            className={`scroll-mt-20 rounded-xl border bg-white p-4 target:ring-2 target:ring-accent ${
              cited.has(s.n) ? 'border-accent/40' : 'border-line'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="grid size-6 place-items-center rounded-md bg-ink text-xs font-semibold text-white">
                {s.n}
              </span>
              <ProvenanceLabel provenance={s.provenance} />
              {cited.has(s.n) && <span className="text-xs font-medium text-accent">Cited</span>}
              {s.page != null && <span className="text-xs text-muted">Page {s.page}</span>}
            </div>
            <h4 className="mt-2 font-serif text-base text-ink">{s.title}</h4>
            {quote && (
              <blockquote className="mt-2 border-l-2 border-accent pl-3 text-sm italic text-ink">
                “{quote}”
              </blockquote>
            )}
            <p className="mt-2 line-clamp-4 text-sm leading-relaxed text-ink-soft">{s.excerpt}</p>
            <p className="mt-2 text-xs text-muted">{s.provenance_label}</p>
            <div className="mt-2"><SourceLink url={s.source_url} /></div>
          </li>
        )
      })}
    </ol>
  )
}

const STATUS = {
  answered: { title: 'Answer', tone: 'border-line bg-white' },
  insufficient_evidence: { title: 'Not enough evidence', tone: 'border-slate-300 bg-slate-50' },
  fallback: { title: 'AI answer unavailable', tone: 'border-amber-200 bg-amber-50' },
} as const

export function AnswerPanel({ data }: { data: AskResponse }) {
  // quotes live on citations; merge them onto the matching source cards
  const quoteBy = new Map(data.citations.map((c) => [c.n, c.quote]))
  const sources = data.sources.map((s) => ({ ...s, quote: quoteBy.get(s.n) ?? null }))
  const cited = new Set(data.citations.map((c) => c.n))
  const citesIllustrative = data.citations.some((c) => c.provenance === 'illustrative')
  const citesSupplied = data.citations.some((c) => c.provenance === 'supplied_document')
  const s = STATUS[data.status]

  return (
    <div className="space-y-6">
      <section aria-live="polite" className={`rounded-xl border p-5 ${s.tone}`}>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">{s.title}</h2>
        {data.status === 'answered' && data.answer ? (
          <div className="mt-2">
            <AnswerText text={data.answer} cited={cited} />
            {citesIllustrative && (
              <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                This answer cites illustrative sample records. It is not official CHRIST University
                information.
              </p>
            )}
            {citesSupplied && (
              <p className="mt-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                This answer cites a supplied document whose official source has not been verified.
              </p>
            )}
            <p className="mt-3 text-xs text-muted">
              Each citation's quote was checked to appear in the retrieved source. That does not
              guarantee the answer is correct — confirm important details with the university.
            </p>
          </div>
        ) : (
          <p className="mt-2 text-[15px] leading-relaxed text-ink">{data.message}</p>
        )}
        {data.status === 'fallback' && sources.length > 0 && (
          <p className="mt-2 text-sm text-ink-soft">The most relevant retrieved passages are shown below.</p>
        )}
        <p className="mt-3 text-xs text-muted">
          Retrieval: {data.retrieval.mode ?? 'unknown'}
          {data.retrieval.semantic_available === false && ' (semantic model unavailable)'}
          {typeof data.retrieval.retrieved === 'number' && ` · ${data.retrieval.retrieved} passages`}
        </p>
      </section>

      {sources.length > 0 && (
        <section aria-labelledby="sources-heading">
          <h3 id="sources-heading" className="mb-3 text-sm font-semibold text-ink">
            {data.status === 'answered' ? 'Sources' : 'Retrieved passages'}
          </h3>
          <CitationList sources={sources} cited={cited} />
        </section>
      )}
    </div>
  )
}
