export interface Fingerprint {
  incident_id: string
  service: string
  environment: string
  region: string
  symptom: string
  error_type: string
  triggering_change: string
  recent_deployment: string
  infrastructure_change: string
  dependency: string
  affected_component: string
  failure_chain: string[]
  customer_impact: string
  severity: string
  root_cause: string
  attempted_fixes: { action: string; outcome: string; notes: string }[]
  successful_fixes: string[]
  failed_fixes: string[]
  resolution_time_minutes: number | null
  preventive_action: string
  final_outcome: string
}

export interface MatchResult {
  incident_id: string
  total: number
  components: Record<string, number>
  match_type: 'match' | 'partial_match'
  judge_rationale: string
  difference_note: string
  title: string
  service: string
  dependency: string
  failure_chain: string[]
  successful_fixes: string[]
  failed_fixes: string[]
  severity: string
}

export interface FixEvidence {
  proven: { action: string; incidents: string[]; count: number; rank_score: number }[]
  failed: { action: string; incidents: string[] }[]
  conflicting: { action: string; worked_in: string[]; failed_in: string[] }[]
}

export interface Recommendation {
  likely_pattern: { name: string; explanation: string }
  recommended_next_steps: { step: string; rationale: string; supporting_incident_ids: string[] }[]
  fixes_to_avoid: { action: string; why: string; incident_ids: string[] }[]
  prevention_actions: string[]
  matched_incidents: {
    incident_id: string
    title: string
    similarity: number
    why_matched: string
    differences: string
  }[]
  alternative_hypotheses: string[]
  confidence: number
  confidence_rationale: string
  data_gaps: string[]
  safety_note: string
}

export interface AnalyzeResponse {
  incident_id: string
  fingerprint: Fingerprint
  extraction_notes: string[]
  field_confidence: Record<string, number>
  matches: MatchResult[]
  cold_start: boolean
  matching_reason: string
  fix_evidence: FixEvidence
  recommendations: {
    stateless: Recommendation | null
    memory: Recommendation | null
  }
}

export interface IncidentSummary {
  incident_id: string
  service: string
  symptom: string
  dependency: string
  severity: string
  root_cause: string
  failure_chain: string[]
  successful_fixes: string[]
  failed_fixes: string[]
  preventive_action: string
  created_at: string
  resolved: boolean
}

export interface DemoIncident {
  incident_id: string
  demo_number: number
  title: string
  text: string
  expected_pattern: string
}

export interface Health {
  status: string
  llm_configured: boolean
  llm_model: string
  memory_mode: string
  incident_count: number
}
