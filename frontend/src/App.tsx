import { useEffect, useState } from 'react'
import { api } from './api'
import type { DemoIncident, Health, IncidentSummary } from './types'
import AnalyzeTab from './components/AnalyzeTab'
import BankTab from './components/BankTab'
import { Logo, StatChip } from './components/ui'

export type PickerEntry = {
  key: string
  incident_id: string
  title: string
  text: string
  kind: 'live' | 'historical'
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [demos, setDemos] = useState<DemoIncident[]>([])
  const [tab, setTab] = useState<'analyze' | 'bank'>('analyze')

  const refresh = () => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.incidents().then(setIncidents).catch(() => setIncidents([]))
  }

  useEffect(() => {
    refresh()
    api.demoIncidents().then(setDemos).catch(() => setDemos([]))
  }, [])

  // Build the picker: 4 live demo scenarios + every stored historical incident.
  const pickerEntries: PickerEntry[] = [
    ...demos.map((d) => ({
      key: `demo-${d.demo_number}`,
      incident_id: d.incident_id,
      title: `LIVE · ${d.title}`,
      text: d.text,
      kind: 'live' as const,
    })),
    ...incidents.map((i) => ({
      key: `hist-${i.incident_id}`,
      incident_id: i.incident_id,
      title: `HISTORY · ${i.incident_id} · ${i.service || 'unknown service'}`,
      text: '',
      kind: 'historical' as const,
    })),
  ]

  return (
    <div className="mx-auto max-w-[1600px] px-6 pb-16">
      {/* header */}
      <header className="flex flex-wrap items-center justify-between gap-4 py-6">
        <div className="flex items-center gap-4">
          <Logo />
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">
              FailureLoop
            </h1>
            <p className="text-sm text-slate-400">
              Incident recurrence prevention · memory-powered on-call assistant
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <StatChip
            label="memory"
            value={health?.memory_mode === 'hindsight' ? 'Hindsight' : 'SQLite'}
            tone={health?.memory_mode === 'hindsight' ? 'violet' : 'blue'}
          />
          <StatChip label="incidents" value={String(incidents.length)} tone="green" />
          <StatChip
            label="llm"
            value={health?.llm_configured ? health.llm_model.split('/').pop() || 'ready' : 'not configured'}
            tone={health?.llm_configured ? 'green' : 'red'}
          />
        </div>
      </header>

      {/* tabs */}
      <nav className="mb-8 flex gap-2 rounded-xl border border-ink-700/60 bg-ink-900/60 p-1.5 backdrop-blur w-fit">
        {(
          [
            ['analyze', '🚨 Analyze incident'],
            ['bank', '🗄 Memory bank'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-lg px-5 py-2.5 text-sm font-semibold transition-all duration-200 ${
              tab === key
                ? 'bg-gradient-to-r from-signal-blue/20 to-signal-violet/20 text-white shadow-inner ring-1 ring-signal-blue/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === 'analyze' && (
        <AnalyzeTab
          pickerEntries={pickerEntries}
          health={health}
          onAnalyzed={refresh}
        />
      )}
      {tab === 'bank' && <BankTab incidents={incidents} onRefresh={refresh} />}

      <footer className="mt-16 border-t border-ink-800 pt-6 text-center text-xs text-slate-500">
        FailureLoop assists on-call engineers — it never executes production changes. Claims
        citing incident IDs come from your incident memory; everything else is general reasoning.
      </footer>
    </div>
  )
}
