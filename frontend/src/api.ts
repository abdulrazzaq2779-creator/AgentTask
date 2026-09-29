import type {
  AnalyzeResponse,
  DemoIncident,
  Health,
  IncidentSummary,
} from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

export const api = {
  health: () => get<Health>('/api/health'),
  incidents: () => get<IncidentSummary[]>('/api/incidents'),
  demoIncidents: () => get<DemoIncident[]>('/api/demo-incidents'),
  analyze: (text: string, incidentId: string, mode: string) =>
    post<AnalyzeResponse>('/api/analyze', {
      text,
      incident_id: incidentId,
      mode,
    }),
}
