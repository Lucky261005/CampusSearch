import type { Provenance } from '../types'

export function ProvenanceLabel({ provenance }: { provenance: Provenance }) {
  return provenance === 'official_web' ? (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
      <span aria-hidden className="size-1.5 rounded-full bg-emerald-600" />
      Official web page
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900">
      <span aria-hidden className="size-1.5 rounded-full bg-amber-500" />
      Illustrative sample · not official
    </span>
  )
}

/** Only http(s) URLs are links; illustrative records carry synthetic identifiers. */
export function SourceLink({ url }: { url: string }) {
  if (!/^https?:\/\//i.test(url)) {
    return (
      <span className="text-xs text-muted" title={url}>
        Sample record — no public source page
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
