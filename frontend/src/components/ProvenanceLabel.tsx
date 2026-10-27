import type { Provenance } from '../types'

const LABELS: Record<Provenance, { text: string; box: string; dot: string }> = {
  official_web: {
    text: 'Official web page',
    box: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    dot: 'bg-emerald-600',
  },
  supplied_document: {
    text: 'Supplied document · source not verified',
    box: 'border-sky-200 bg-sky-50 text-sky-900',
    dot: 'bg-sky-600',
  },
  illustrative: {
    text: 'Illustrative sample · not official',
    box: 'border-amber-200 bg-amber-50 text-amber-900',
    dot: 'bg-amber-500',
  },
}

export function ProvenanceLabel({ provenance }: { provenance: Provenance }) {
  const l = LABELS[provenance] ?? LABELS.illustrative
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${l.box}`}>
      <span aria-hidden className={`size-1.5 rounded-full ${l.dot}`} />
      {l.text}
    </span>
  )
}

/** Only http(s) URLs are links; other records carry non-web identifiers. */
export function SourceLink({ url }: { url: string }) {
  if (!/^https?:\/\//i.test(url)) {
    return (
      <span className="text-xs text-muted" title={url}>
        {url.startsWith('supplied://')
          ? 'Supplied file — no public source page'
          : 'Sample record — no public source page'}
      </span>
    )
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex max-w-full items-center gap-1 truncate text-xs font-medium text-accent hover:text-accent-strong hover:underline"
    >
      <span className="truncate">{url.replace(/^https?:\/\//, '')}</span>
      <span aria-hidden>↗</span>
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  )
}
