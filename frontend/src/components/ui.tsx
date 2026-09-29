import type { ReactNode } from 'react'

export function Logo() {
  return (
    <div className="relative grid h-12 w-12 place-items-center rounded-xl bg-gradient-to-br from-signal-blue/25 to-signal-violet/25 ring-1 ring-signal-blue/40">
      <span className="text-2xl">🔁</span>
      <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full bg-signal-green shadow-[0_0_8px_rgba(61,220,151,0.8)] animate-pulse-slow" />
    </div>
  )
}

const TONES: Record<string, string> = {
  blue: 'bg-signal-blue/10 text-signal-blue ring-signal-blue/30',
  green: 'bg-signal-green/10 text-signal-green ring-signal-green/30',
  red: 'bg-signal-red/10 text-signal-red ring-signal-red/30',
  amber: 'bg-signal-amber/10 text-signal-amber ring-signal-amber/30',
  violet: 'bg-signal-violet/10 text-signal-violet ring-signal-violet/30',
}

export function StatChip({
  label,
  value,
  tone = 'blue',
}: {
  label: string
  value: string
  tone?: keyof typeof TONES | string
}) {
  return (
    <div className={`chip px-3 py-1.5 ring-1 ${TONES[tone] ?? TONES.blue}`}>
      <span className="opacity-60">{label}</span>
      <span className="font-semibold">{value}</span>
    </div>
  )
}

export function IdChip({ id }: { id: string }) {
  return <span className="chip-id">{id}</span>
}

export function ConfidenceGauge({
  value,
  label = 'confidence',
}: {
  value: number
  label?: string
}) {
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100)
  const tone = pct >= 70 ? 'bg-signal-green' : pct >= 45 ? 'bg-signal-amber' : 'bg-signal-red'
  const glow =
    pct >= 70
      ? 'shadow-[0_0_10px_rgba(61,220,151,0.5)]'
      : pct >= 45
        ? 'shadow-[0_0_10px_rgba(255,180,84,0.5)]'
        : 'shadow-[0_0_10px_rgba(255,92,92,0.5)]'
  return (
    <div className="w-full">
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="font-mono font-semibold text-slate-200">{(value).toFixed(2)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-ink-800">
        <div
          className={`h-full rounded-full ${tone} ${glow} transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export function ChainVisual({
  steps,
  highlight = false,
}: {
  steps: string[]
  highlight?: boolean
}) {
  if (!steps?.length) {
    return <p className="text-sm italic text-slate-500">No failure chain extracted.</p>
  }
  return (
    <div className="space-y-0.5">
      {steps.map((step, i) => (
        <div key={i}>
          {i > 0 && (
            <div
              className={`my-0.5 text-center text-xs font-bold ${
                highlight ? 'text-signal-red' : 'text-slate-600'
              }`}
            >
              ↓
            </div>
          )}
          <div
            className={`rounded-lg border px-3 py-2 text-sm ${
              highlight
                ? 'border-signal-red/25 bg-signal-red/5 text-slate-100'
                : 'border-ink-700/50 bg-ink-850/60 text-slate-300'
            }`}
          >
            {step}
          </div>
        </div>
      ))}
    </div>
  )
}

export function SectionTitle({
  icon,
  children,
  hint,
}: {
  icon: string
  children: ReactNode
  hint?: string
}) {
  return (
    <div className="mb-4 flex items-baseline gap-2">
      <h2 className="text-lg font-bold text-white">
        {icon} {children}
      </h2>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </div>
  )
}

export function Empty({ icon, message }: { icon: string; message: string }) {
  return (
    <div className="grid place-items-center rounded-xl border border-dashed border-ink-700 py-10 text-center">
      <div className="text-3xl">{icon}</div>
      <p className="mt-2 max-w-md text-sm text-slate-400">{message}</p>
    </div>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="card p-6">
      <div className="mb-3 flex items-center gap-3">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-signal-blue/30 border-t-signal-blue" />
        <span className="font-medium text-slate-200">{label}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-ink-800">
        <div className="scan-line h-full w-1/2" />
      </div>
    </div>
  )
}
