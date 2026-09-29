import { useState } from 'react'
import { api } from '../api'
import type { AnalyzeResponse, Health } from '../types'
import type { PickerEntry } from '../App'
import {
  ChainVisual,
  ConfidenceGauge,
  Empty,
  IdChip,
  SectionTitle,
  Spinner,
} from './ui'

type Props = {
  pickerEntries: PickerEntry[]
  health: Health | null
  onAnalyzed: () => void
}

export default function AnalyzeTab({ pickerEntries, health, onAnalyzed }: Props) {
  const [text, setText] = useState('')
  const [incidentId, setIncidentId] = useState('INC-NEW')
  const [mode, setMode] = useState<'both' | 'stateless' | 'memory'>('both')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<AnalyzeResponse | null>(null)

  const loadEntry = (key: string) => {
    const entry = pickerEntries.find((e) => e.key === key)
    if (!entry) return
    if (entry.kind === 'live') {
      setText(entry.text)
      setIncidentId(entry.incident_id)
    } else {
      const [id] = entry.title.split(' · ')
      setIncidentId(entry.incident_id || id.replace('HISTORY', '').trim())
      if (!text) setText('')
    }
  }

  const run = async () => {
    if (!text.trim()) {
      setError('Paste some incident text first (or pick a LIVE demo scenario).')
      return
    }
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const res = await api.analyze(text, incidentId, mode)
      setResult(res)
      onAnalyzed()
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'Analysis failed — the LLM may be rate-limited, try again shortly.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* ---------------- input panel */}
      <section className="card fade-up p-6">
        <SectionTitle icon="🚨" hint="pick a scenario or paste your own alert">
          New incident
        </SectionTitle>

        <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
          <div className="space-y-3">
            <select
              onChange={(e) => loadEntry(e.target.value)}
              defaultValue=""
              className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-sm text-slate-200 outline-none focus:border-signal-blue/50"
            >
              <option value="" disabled>
                ⚡ Load a scenario… ({pickerEntries.length} available)
              </option>
              {pickerEntries.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.title}
                </option>
              ))}
            </select>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                'e.g.\n"On-call: checkout requests timing out since 14:00 UTC.\nPayment provider showing elevated latency.\nWorker utilization pegged at 100%."'
              }
              rows={7}
              className="w-full resize-y rounded-lg border border-ink-700 bg-ink-850 px-4 py-3 font-mono text-sm text-slate-200 placeholder:text-slate-600 outline-none focus:border-signal-blue/50"
            />
          </div>

          <div className="flex flex-col gap-3">
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Incident ID
            </label>
            <input
              value={incidentId}
              onChange={(e) => setIncidentId(e.target.value)}
              className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 font-mono text-sm text-slate-200 outline-none focus:border-signal-blue/50"
            />
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Mode
            </label>
            <div className="flex flex-col gap-1.5">
              {(
                [
                  ['both', '⚖️  Stateless vs Memory'],
                  ['stateless', '👻 Stateless only'],
                  ['memory', '🧠 Memory only'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  className={`rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    mode === value
                      ? 'bg-signal-blue/15 text-white ring-1 ring-signal-blue/40'
                      : 'text-slate-400 hover:bg-ink-850 hover:text-slate-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <button onClick={run} disabled={busy} className="btn-primary mt-2 w-full">
              {busy ? 'Analyzing…' : '🔎 Analyze incident'}
            </button>
            {!health?.llm_configured && (
              <p className="text-xs text-signal-red">
                No LLM key — set LLM_API_KEY in .env and restart the API.
              </p>
            )}
          </div>
        </div>
        {error && (
          <p className="mt-3 rounded-lg border border-signal-red/30 bg-signal-red/10 px-3 py-2 text-sm text-signal-red">
            {error}
          </p>
        )}
      </section>

      {busy && <Spinner label="Extracting fingerprint → recalling memory → judging matches → writing recommendations…" />}

      {/* ---------------- results */}
      {result && !busy && (
        <>
          <FingerprintCard result={result} />
          {result.matches.length > 0 && <MatchesSection result={result} />}
          <FixEvidenceSection result={result} />
          <RecommendationsSection result={result} />
        </>
      )}

      {!result && !busy && (
        <Empty
          icon="🛰"
          message="Pick a LIVE scenario and hit Analyze — the engine will extract the causal fingerprint, match it against your incident memory, and compare what the LLM says with and without history."
        />
      )}
    </div>
  )
}

function FingerprintCard({ result }: { result: AnalyzeResponse }) {
  const fp = result.fingerprint
  return (
    <section className="card fade-up p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <SectionTitle icon="🧬" hint={`extracted from raw incident text`}>
          Fingerprint {fp.incident_id && <IdChip id={fp.incident_id} />}
        </SectionTitle>
        {fp.severity && (
          <span className="chip bg-signal-red/15 text-signal-red ring-1 ring-signal-red/30">
            {fp.severity}
          </span>
        )}
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-2 text-sm">
          {[
            ['Service', fp.service],
            ['Symptom', fp.symptom],
            ['Dependency', fp.dependency],
            ['Trigger', fp.triggering_change],
            ['Deployment', fp.recent_deployment],
            ['Impact', fp.customer_impact],
          ].map(([k, v]) => (
            <div key={k as string} className="flex gap-2">
              <span className="w-24 shrink-0 text-slate-500">{k}</span>
              <span className="text-slate-200">{v || '—'}</span>
            </div>
          ))}
          <div className="flex gap-2 border-t border-ink-800 pt-2">
            <span className="w-24 shrink-0 text-slate-500">Root cause</span>
            <span className="font-medium text-slate-100">
              {fp.root_cause || (
                <em className="text-signal-amber">unknown — live incident, not yet confirmed</em>
              )}
            </span>
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Failure chain · the matching signal
          </p>
          <ChainVisual steps={fp.failure_chain} highlight />
        </div>
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Extraction confidence
          </p>
          {Object.entries(result.field_confidence).map(([field, v]) => (
            <ConfidenceGauge key={field} value={v} label={field.replace('_', ' ')} />
          ))}
          {result.extraction_notes.length > 0 && (
            <details className="mt-2 rounded-lg border border-ink-700/60 bg-ink-850/50 p-3 text-xs text-slate-400">
              <summary className="cursor-pointer font-semibold text-slate-300">
                {result.extraction_notes.length} extraction notes
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-4">
                {result.extraction_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </section>
  )
}

function MatchesSection({ result }: { result: AnalyzeResponse }) {
  return (
    <section className="fade-up">
      <SectionTitle icon="📊" hint="scored by causal mechanism, not error text">
        Pattern matches ({result.matches.length})
      </SectionTitle>
      {result.cold_start && result.matching_reason && (
        <p className="mb-3 rounded-lg border border-signal-amber/30 bg-signal-amber/10 px-3 py-2 text-sm text-signal-amber">
          {result.matching_reason}
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {result.matches.map((m) => (
          <div key={m.incident_id} className="card card-hover p-5">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <IdChip id={m.incident_id} />
                {m.match_type === 'partial_match' && (
                  <span className="chip ml-2 bg-signal-amber/15 text-signal-amber ring-1 ring-signal-amber/30">
                    near-miss
                  </span>
                )}
              </div>
              <span className="font-mono text-lg font-bold text-white">
                {m.total.toFixed(2)}
              </span>
            </div>
            <p className="mb-3 line-clamp-2 text-sm text-slate-300">{m.title}</p>
            <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-ink-800">
              <div
                className={`h-full rounded-full ${
                  m.match_type === 'match' ? 'bg-signal-green' : 'bg-signal-amber'
                }`}
                style={{ width: `${Math.round(m.total * 100)}%` }}
              />
            </div>
            <div className="grid grid-cols-5 gap-1 text-center text-[10px] text-slate-500">
              {Object.entries(m.components).map(([k, v]) => (
                <div key={k} className="rounded bg-ink-850/70 px-1 py-1.5">
                  <div className="font-mono font-semibold text-slate-300">{v.toFixed(2)}</div>
                  <div className="mt-0.5 truncate">{k.split('_')[0]}</div>
                </div>
              ))}
            </div>
            {m.judge_rationale && (
              <p className="mt-3 text-xs italic text-slate-500">“{m.judge_rationale}”</p>
            )}
            {m.match_type === 'partial_match' && m.difference_note && (
              <p className="mt-2 text-xs text-signal-amber">⚠ {m.difference_note}</p>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function FixEvidenceSection({ result }: { result: AnalyzeResponse }) {
  const fx = result.fix_evidence
  if (!fx.proven.length && !fx.failed.length && !fx.conflicting.length) return null
  return (
    <section className="card fade-up p-6">
      <SectionTitle icon="🛠" hint="aggregated from matched incidents">
        Fix evidence
      </SectionTitle>
      <div className="grid gap-6 md:grid-cols-3">
        <div>
          <p className="mb-2 text-sm font-semibold text-signal-green">✅ Worked before</p>
          <ul className="space-y-2">
            {fx.proven.map((f) => (
              <li key={f.action} className="rounded-lg bg-ink-850/60 p-3 text-sm">
                {f.action}
                <span className="ml-2 text-xs text-slate-500">
                  {f.incidents.map((id) => (
                    <span key={id} className="chip-id mr-1">
                      {id}
                    </span>
                  ))}
                </span>
              </li>
            ))}
            {!fx.proven.length && <li className="text-sm text-slate-500">none in matches</li>}
          </ul>
        </div>
        <div>
          <p className="mb-2 text-sm font-semibold text-signal-red">⛔ Failed before</p>
          <ul className="space-y-2">
            {fx.failed.map((f) => (
              <li key={f.action} className="rounded-lg bg-ink-850/60 p-3 text-sm">
                <span className="line-through decoration-signal-red/60">{f.action}</span>
                <span className="ml-2 text-xs text-slate-500">{f.incidents.join(', ')}</span>
              </li>
            ))}
            {!fx.failed.length && <li className="text-sm text-slate-500">none in matches</li>}
          </ul>
        </div>
        <div>
          <p className="mb-2 text-sm font-semibold text-signal-amber">⚔ Conflicting</p>
          <ul className="space-y-2">
            {fx.conflicting.map((f) => (
              <li key={f.action} className="rounded-lg bg-ink-850/60 p-3 text-sm">
                {f.action}
                <div className="mt-1 text-xs">
                  <span className="text-signal-green">worked: {f.worked_in.join(', ')}</span>
                  {' · '}
                  <span className="text-signal-red">failed: {f.failed_in.join(', ')}</span>
                </div>
              </li>
            ))}
            {!fx.conflicting.length && (
              <li className="text-sm text-slate-500">no conflicting evidence</li>
            )}
          </ul>
        </div>
      </div>
    </section>
  )
}

function RecommendationsSection({ result }: { result: AnalyzeResponse }) {
  const s = result.recommendations.stateless
  const m = result.recommendations.memory
  const both = s !== null && m !== null
  const delta = both ? (m as NonNullable<typeof m>).confidence - (s as NonNullable<typeof s>).confidence : null
  return (
    <section className="fade-up">
      <SectionTitle icon="💡" hint="same LLM, same incident — the only difference is memory">
        Recommendations
      </SectionTitle>
      <div className={`grid gap-6 ${both ? 'xl:grid-cols-2' : ''}`}>
        {s && (
          <RecCard
            kind="stateless"
            rec={s}
            tone="border-signal-amber/25"
            header="👻 Before memory"
          />
        )}
        {m && (
          <RecCard
            kind="memory"
            rec={m}
            tone="border-signal-blue/25"
            header="🧠 After memory"
          />
        )}
      </div>
      {delta !== null && s && m && (
        <div className="card mt-4 grid gap-4 p-5 md:grid-cols-3">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">Evidence added</p>
            <p className="text-2xl font-bold text-white">
              {m.matched_incidents.length}
              <span className="ml-2 text-sm font-normal text-slate-400">cited incidents</span>
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">Fixes to avoid</p>
            <p className="text-2xl font-bold text-white">
              {m.fixes_to_avoid.length}
              <span className="ml-2 text-sm font-normal text-slate-400">
                with historical evidence
              </span>
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">Confidence shift</p>
            <p
              className={`text-2xl font-bold ${
                delta > 0 ? 'text-signal-green' : delta < 0 ? 'text-signal-amber' : 'text-slate-300'
              }`}
            >
              {s.confidence.toFixed(2)} → {m.confidence.toFixed(2)}
              <span className="ml-2 text-sm font-normal">
                ({delta >= 0 ? '+' : ''}
                {delta.toFixed(2)})
              </span>
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {delta > 0
                ? 'memory strengthens the diagnosis'
                : delta < 0
                  ? 'no strong match — memory honesty lowers confidence'
                  : 'no change'}
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

function RecCard({
  kind,
  rec,
  tone,
  header,
}: {
  kind: 'stateless' | 'memory'
  rec: AnalyzeResponse['recommendations']['memory']
  tone: string
  header: string
}) {
  if (!rec) return null
  return (
    <div className={`card border ${tone} p-6`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">{header}</p>
          <h3 className="mt-1 text-lg font-bold text-white">{rec.likely_pattern.name}</h3>
        </div>
        <div className="w-32">
          <ConfidenceGauge value={rec.confidence} />
        </div>
      </div>
      <p className="mb-4 text-sm text-slate-400">{rec.likely_pattern.explanation}</p>

      <ol className="space-y-3">
        {rec.recommended_next_steps.map((s, i) => (
          <li key={i} className="flex gap-3">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-ink-800 font-mono text-xs font-bold text-signal-blue">
              {i + 1}
            </span>
            <div>
              <p className="text-sm text-slate-200">
                {s.step}
                {s.supporting_incident_ids.length > 0 ? (
                  s.supporting_incident_ids.map((id) => (
                    <span key={id} className="chip-id ml-1.5">
                      {id}
                    </span>
                  ))
                ) : (
                  <span className="chip ml-1.5 bg-ink-800 text-slate-500">general reasoning</span>
                )}
              </p>
              {s.rationale && (
                <p className="mt-0.5 text-xs text-slate-500">{s.rationale}</p>
              )}
            </div>
          </li>
        ))}
      </ol>

      {rec.fixes_to_avoid.length > 0 && (
        <div className="mt-4 rounded-lg border border-signal-red/25 bg-signal-red/5 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-signal-red">
            Fixes to avoid
          </p>
          <ul className="space-y-1.5">
            {rec.fixes_to_avoid.map((f) => (
              <li key={f.action} className="text-sm text-slate-300">
                <span className="line-through decoration-signal-red/60">{f.action}</span>
                <span className="text-slate-500"> — {f.why}</span>
                {f.incident_ids.map((id) => (
                  <span key={id} className="chip-id ml-1.5">
                    {id}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}

      {rec.matched_incidents.length > 0 && (
        <details className="mt-4 rounded-lg border border-ink-700/60 bg-ink-850/50 p-3 text-sm">
          <summary className="cursor-pointer font-semibold text-slate-300">
            {rec.matched_incidents.length} matched incidents
          </summary>
          <ul className="mt-2 space-y-2">
            {rec.matched_incidents.map((mi) => (
              <li key={mi.incident_id}>
                <IdChip id={mi.incident_id} />
                <span className="ml-2 font-mono text-xs text-signal-blue">
                  {mi.similarity.toFixed(2)}
                </span>
                <span className="ml-2 text-slate-300">{mi.title}</span>
                <p className="mt-0.5 text-xs text-slate-500">{mi.why_matched}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {rec.alternative_hypotheses.length > 0 && (
          <details className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-3 text-xs text-slate-400">
            <summary className="cursor-pointer font-semibold text-slate-300">
              🤔 {rec.alternative_hypotheses.length} alternative hypotheses
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {rec.alternative_hypotheses.map((h, i) => (
                <li key={i}>{h}</li>
              ))}
            </ul>
          </details>
        )}
        {rec.data_gaps.length > 0 && (
          <details className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-3 text-xs text-slate-400">
            <summary className="cursor-pointer font-semibold text-slate-300">
              🕳 {rec.data_gaps.length} data gaps
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {rec.data_gaps.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          </details>
        )}
      </div>

      <p className="mt-4 border-t border-ink-800 pt-3 text-xs italic text-slate-500">
        {rec.safety_note}
      </p>
      <p className="sr-only">{kind}</p>
    </div>
  )
}
