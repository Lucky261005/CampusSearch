export type Tab = 'search' | 'ask'

const TABS: { value: Tab; label: string }[] = [
  { value: 'search', label: 'Search' },
  { value: 'ask', label: 'Ask AI' },
]

export function Header({ tab, onTab }: { tab: Tab; onTab: (t: Tab) => void }) {
  const index = TABS.findIndex((t) => t.value === tab)
  return (
    <header className="material sticky top-0 z-20 border-b border-line/70">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="grid size-7 place-items-center rounded-lg bg-accent text-sm font-semibold text-white">
            C
          </span>
          <span className="text-[17px] font-semibold tracking-tight text-ink">CampusSearch</span>
          <span className="hidden text-xs text-muted md:inline">Student project · not an official CHRIST service</span>
        </div>
        <div role="tablist" aria-label="View" className="relative grid grid-cols-2 rounded-lg bg-black/5 p-0.5">
          <span
            aria-hidden
            className="slide absolute inset-y-0.5 left-0.5 w-[calc((100%-0.25rem)/2)] rounded-md bg-white shadow-sm"
            style={{ transform: `translateX(${index * 100}%)` }}
          />
          {TABS.map((t) => (
            <button
              key={t.value}
              id={`tab-${t.value}`}
              aria-controls="view-panel"
              type="button"
              role="tab"
              onClick={() => onTab(t.value)}
              aria-selected={tab === t.value}
              className={`press relative whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-colors sm:px-4 ${
                tab === t.value ? 'text-ink' : 'text-muted hover:text-ink'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
