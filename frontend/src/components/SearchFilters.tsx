import { useState } from 'react'
import type { Filters } from '../types'

interface Props {
  value: Filters
  onChange: (f: Filters) => void
  onReset: () => void
  pending: boolean // draft differs from what the visible results used
  applyLabel: string
  includeIllustrative: boolean
  onIncludeIllustrative: (on: boolean) => void
}

const input =
  'h-9 w-full rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15'

export function SearchFilters({
  value,
  onChange,
  onReset,
  pending,
  applyLabel,
  includeIllustrative,
  onIncludeIllustrative,
}: Props) {
  const set = (k: keyof Filters) => (e: { target: { value: string } }) =>
    onChange({ ...value, [k]: e.target.value })
  const [open, setOpen] = useState(false) // small screens only; always shown from lg up
  const active = Object.values(value).filter((v) => v.trim()).length
  const badRange = value.date_from && value.date_to && value.date_from > value.date_to

  const text = (k: 'campus' | 'category' | 'programme', label: string, eg: string) => (
    <div>
      <label htmlFor={`f-${k}`} className="mb-1 block text-xs font-medium text-muted">{label}</label>
      <input id={`f-${k}`} value={value[k]} onChange={set(k)} placeholder={eg} className={input} />
    </div>
  )

  return (
    <section aria-labelledby="filters-heading" className="rounded-xl border border-line bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 id="filters-heading" className="text-sm font-semibold text-ink">
          Filters{active > 0 && <span className="ml-1.5 text-muted">({active})</span>}
        </h2>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onReset}
            disabled={active === 0}
            className="text-xs font-medium text-accent hover:underline disabled:cursor-not-allowed disabled:text-slate-400 disabled:no-underline"
          >
            Reset filters
          </button>
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-controls="filter-fields"
            className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-slate-50 lg:hidden"
          >
            {open ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      <div id="filter-fields" className={`mt-3 ${open ? 'block' : 'hidden'} lg:block`}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
        {text('campus', 'Campus', 'e.g. Kengeri')}
        {text('category', 'Category', 'e.g. examinations')}
        {text('programme', 'Programme', 'e.g. MBA')}
        <div>
          <label htmlFor="f-semester" className="mb-1 block text-xs font-medium text-muted">Semester</label>
          <select id="f-semester" value={value.semester} onChange={set('semester')} className={input}>
            <option value="">Any</option>
            {Array.from({ length: 12 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>Semester {i + 1}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="f-from" className="mb-1 block text-xs font-medium text-muted">Published from</label>
          <input id="f-from" type="date" value={value.date_from} onChange={set('date_from')} className={input} />
        </div>
        <div>
          <label htmlFor="f-to" className="mb-1 block text-xs font-medium text-muted">Published to</label>
          <input id="f-to" type="date" value={value.date_to} onChange={set('date_to')} className={input} />
        </div>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Text filters match exactly (case-insensitive). Date filters exclude undated records.
      </p>
      <label className="mt-3 flex items-start gap-2 border-t border-line pt-3 text-xs text-ink-soft">
        <input
          type="checkbox"
          checked={includeIllustrative}
          onChange={(e) => onIncludeIllustrative(e.target.checked)}
          className="mt-0.5 size-3.5 accent-amber-600"
        />
        <span>
          Include illustrative sample records
          <span className="block text-muted">Synthetic demo data, not official information.</span>
        </span>
      </label>
      </div>
      {badRange && <p role="alert" className="mt-2 text-xs font-medium text-red-700">“From” must be on or before “to”.</p>}
      {pending && !badRange && (
        <p className="mt-2 text-xs font-medium text-accent">Filters changed — press {applyLabel} to apply.</p>
      )}
    </section>
  )
}
