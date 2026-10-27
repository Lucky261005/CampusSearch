export function Pagination({
  offset,
  limit,
  total,
  onPage,
}: {
  offset: number
  limit: number
  total: number
  onPage: (offset: number) => void
}) {
  if (total <= limit) return null
  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  const btn =
    'rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink hover:border-slate-300 disabled:cursor-not-allowed disabled:opacity-40'
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between pt-2">
      <button type="button" className={btn} disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>
        ← Previous
      </button>
      <span className="text-sm text-muted">Page {page} of {pages}</span>
      <button type="button" className={btn} disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>
        Next →
      </button>
    </nav>
  )
}
