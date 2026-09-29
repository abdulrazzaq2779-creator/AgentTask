import { useState } from 'react'
import type { IncidentSummary } from '../types'
import { ChainVisual, Empty, IdChip, SectionTitle } from './ui'

type Props = {
  incidents: IncidentSummary[]
  onRefresh: () => void
}

export default function BankTab({ incidents, onRefresh }: Props) {
  const [selected, setSelected] = useState<string | null>(
    incidents.length ? incidents[0].incident_id : null,
  )
  const active = incidents.find((i) => i.incident_id === selected) ?? null

  if (!incidents.length) {
    return (
      <Empty
        icon="🗄"
        message="Memory bank is empty. The API seeds the 10 bundled incidents on startup — restart uvicorn if you see this."
      />
    )
  }

  return (
    <div className="space-y-6">
      <SectionTitle icon="🗄" hint={`${incidents.length} incidents retained in memory`}>
        Memory bank
      </SectionTitle>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {incidents.map((inc) => (
          <button
            key={inc.incident_id}
            onClick={() => setSelected(inc.incident_id)}
            className={`card card-hover p-5 text-left ${
              selected === inc.incident_id ? 'ring-1 ring-signal-blue/50' : ''
            }`}
          >
            <div className="mb-2 flex items-center justify-between">
              <IdChip id={inc.incident_id} />
              {inc.severity && (
                <span className="chip bg-signal-red/10 text-signal-red">{inc.severity}</span>
              )}
            </div>
            <p className="font-mono text-sm text-signal-blue">{inc.service || 'unknown'}</p>
            <p className="mt-1 line-clamp-2 text-sm text-slate-300">{inc.symptom}</p>
            <div className="mt-3 flex gap-3 text-xs text-slate-500">
              <span>✅ {inc.successful_fixes.length} fixes worked</span>
              <span>⛔ {inc.failed_fixes.length} failed</span>
              <span>🧬 {inc.failure_chain.length} steps</span>
            </div>
          </button>
        ))}
      </div>

      {active && (
        <section className="card fade-up p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold text-white">
                {active.incident_id} · {active.service}
              </h3>
              <p className="text-sm text-slate-400">{active.symptom}</p>
            </div>
            <button onClick={onRefresh} className="btn-ghost">
              ↻ Refresh
            </button>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Failure chain
              </p>
              <ChainVisual steps={active.failure_chain} />
            </div>
            <div className="space-y-4">
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Root cause
                </p>
                <p className="text-sm text-slate-200">{active.root_cause || '—'}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-signal-green/20 bg-signal-green/5 p-3">
                  <p className="mb-1.5 text-xs font-semibold text-signal-green">✅ Worked</p>
                  <ul className="space-y-1 text-sm text-slate-300">
                    {active.successful_fixes.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                    {!active.successful_fixes.length && (
                      <li className="text-slate-500">none recorded</li>
                    )}
                  </ul>
                </div>
                <div className="rounded-lg border border-signal-red/20 bg-signal-red/5 p-3">
                  <p className="mb-1.5 text-xs font-semibold text-signal-red">⛔ Failed</p>
                  <ul className="space-y-1 text-sm text-slate-300">
                    {active.failed_fixes.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                    {!active.failed_fixes.length && (
                      <li className="text-slate-500">none recorded</li>
                    )}
                  </ul>
                </div>
              </div>
              {active.preventive_action && (
                <div className="rounded-lg border border-signal-blue/20 bg-signal-blue/5 p-3">
                  <p className="mb-1 text-xs font-semibold text-signal-blue">🛡 Prevention</p>
                  <p className="text-sm text-slate-300">{active.preventive_action}</p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
