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
  return (
    <fieldset>
      <legend className="sr-only">Retrieval mode</legend>
      <div className="inline-flex w-full rounded-lg border border-line bg-white p-1 sm:w-auto">
        {MODES.map((m) => (
          <label
            key={m.value}
            title={m.hint}
            className={`flex-1 cursor-pointer rounded-md px-3 py-1.5 text-center text-sm font-medium transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent sm:flex-none ${
              value === m.value ? 'bg-ink text-white' : 'text-muted hover:text-ink'
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
