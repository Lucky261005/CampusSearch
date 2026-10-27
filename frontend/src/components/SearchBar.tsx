import type { FormEvent } from 'react'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  label: string
  placeholder: string
  submitLabel: string
  busy?: boolean
  examples: string[]
  onExample: (q: string) => void
}

export function SearchBar(p: Props) {
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (p.value.trim()) p.onSubmit()
  }
  return (
    <div>
      <form role="search" onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="main-query" className="sr-only">{p.label}</label>
        <input
          id="main-query"
          type="search"
          value={p.value}
          maxLength={500}
          onChange={(e) => p.onChange(e.target.value)}
          placeholder={p.placeholder}
          autoComplete="off"
          className="h-12 flex-1 rounded-xl border border-line bg-white px-4 text-base text-ink shadow-sm placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-4 focus:ring-accent/15"
        />
        <button
          type="submit"
          disabled={!p.value.trim() || p.busy}
          className="h-12 rounded-xl bg-accent px-6 text-sm font-semibold text-white shadow-sm hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
        >
          {p.submitLabel}
        </button>
      </form>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Try</span>
        {p.examples.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => p.onExample(q)}
            className="rounded-full border border-line bg-white px-3 py-1 text-ink-soft hover:border-accent/40 hover:text-accent"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}
