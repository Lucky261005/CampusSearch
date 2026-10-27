export type Tab = 'search' | 'ask'

export function Header({ tab, onTab }: { tab: Tab; onTab: (t: Tab) => void }) {
  const item = (t: Tab, label: string) => (
    <button
      type="button"
      role="tab"
      onClick={() => onTab(t)}
      aria-selected={tab === t}
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
        tab === t ? 'bg-accent-soft text-accent-strong' : 'text-muted hover:bg-slate-100 hover:text-ink'
      }`}
    >
      {label}
    </button>
  )
  return (
    <header className="border-b border-line bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-xl font-semibold tracking-tight text-ink">CampusSearch</span>
          <span className="hidden text-xs text-muted sm:inline">Student project · not an official CHRIST service</span>
        </div>
        <div role="tablist" aria-label="View" className="flex gap-1">
          {item('search', 'Search')}
          {item('ask', 'Ask AI')}
        </div>
      </div>
    </header>
  )
}
