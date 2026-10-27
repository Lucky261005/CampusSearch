import type { SearchMode } from '../types'

const MODES: { value: SearchMode; label: string; hint: string }[] = [
  { value: 'keyword', label: 'Keyword', hint: 'Exact terms, BM25 ranking' },
  { value: 'semantic', label: 'Semantic', hint: 'Meaning, vector similarity' },
  { value: 'hybrid', label: 'Hybrid', hint: 'Both, fused with RRF' },
]

export function SearchModeSelector({
  value,
  onChange,
}: {
  value: SearchMode
  onChange: (m: SearchMode) => void
}) {
  const index = MODES.findIndex((m) => m.value === value)
  return (
    <fieldset>
      <legend className="sr-only">Retrieval mode</legend>
      <div className="relative grid w-full grid-cols-3 rounded-lg border border-line bg-white p-1 sm:w-80">
        {/* one indicator slides between equal columns (state indication) */}
        <span
          aria-hidden
          className="slide absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/3)] rounded-md bg-ink"
          style={{ transform: `translateX(${index * 100}%)` }}
        />
        {MODES.map((m) => (
          <label
            key={m.value}
            title={m.hint}
            className={`relative cursor-pointer rounded-md px-3 py-1.5 text-center text-sm font-medium transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent ${
              value === m.value ? 'text-white' : 'text-muted hover:text-ink'
            }`}
          >
            <input
              type="radio"
              name="mode"
              value={m.value}
              checked={value === m.value}
              onChange={() => onChange(m.value)}
              className="sr-only"
            />
            {m.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

export const MODE_HINT = Object.fromEntries(MODES.map((m) => [m.value, m.hint])) as Record<
  SearchMode,
  string
>
