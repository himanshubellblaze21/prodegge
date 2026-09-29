// Display helpers shared by the upload flow, the evaluations list and the
// detail views, so a call type, status or outcome reads the same everywhere.

export interface CallTypeMeta {
  value: string
  label: string
  short: string
  hint: string
  color: string
  bg: string
}

export const CALL_TYPES: CallTypeMeta[] = [
  {
    value: 'BCM_PHYSICAL_PD',
    label: 'BCM Physical PD',
    short: 'BCM PD',
    hint: 'BCM self-recording after the field visit',
    color: '#1d4ed8',
    bg: '#eff6ff',
  },
  {
    value: 'BM_AUDIO_FI',
    label: 'BM Audio FI',
    short: 'BM FI',
    hint: 'BM phone check with the customer',
    color: '#047857',
    bg: '#ecfdf5',
  },
  {
    value: 'RCM_AUDIO_PD',
    label: 'RCM Audio PD',
    short: 'RCM PD',
    hint: 'RCM phone discussion with the customer',
    color: '#b45309',
    bg: '#fffbeb',
  },
]

// Older records were written with these call-type names.
const LEGACY_CALL_TYPES: Record<string, string> = {
  RCM_TELE_PD: 'RCM_AUDIO_PD',
  BCM: 'BCM_PHYSICAL_PD',
}

export function getCallType(value?: string): CallTypeMeta | undefined {
  if (!value) return undefined
  const key = LEGACY_CALL_TYPES[value] || value
  return CALL_TYPES.find((t) => t.value === key)
}

// ─── Processing status ──────────────────────────────────────────────────────

export type StatusTone = 'done' | 'active' | 'failed' | 'waiting'

export function getStatus(status?: string): { label: string; tone: StatusTone } {
  const s = (status || '').toUpperCase()
  if (s === 'COMPLETED') return { label: 'Completed', tone: 'done' }
  if (s.includes('FAIL')) return { label: 'Failed', tone: 'failed' }
  if (s === 'UPLOAD_PENDING' || s === 'PENDING_UPLOAD') return { label: 'Upload not finished', tone: 'waiting' }
  const active: Record<string, string> = {
    UPLOADED: 'Queued',
    PREPROCESSING: 'Preparing audio',
    TRANSCRIBING: 'Transcribing',
    EVALUATING: 'Scoring',
    EXCEL_GENERATING: 'Building scorecard',
  }
  return { label: active[s] || 'In progress', tone: 'active' }
}

export function isInProgress(status?: string) {
  return getStatus(status).tone === 'active'
}

// ─── Outcome (verdict) ──────────────────────────────────────────────────────

export type OutcomeKey = 'accepted' | 'not_accepted' | 'not_evaluable' | 'none'

export function getOutcome(ev: { grade_band?: string; verdict?: string; status?: string }): {
  key: OutcomeKey
  label: string
} {
  const text = `${ev.grade_band || ''} ${ev.verdict || ''}`.toUpperCase()
  if (ev.status && ev.status !== 'COMPLETED') return { key: 'none', label: '—' }
  if (text.includes('NOT EVALUABLE')) return { key: 'not_evaluable', label: 'Not evaluable' }
  if (text.includes('NOT ACCEPTED')) return { key: 'not_accepted', label: 'Not accepted' }
  if (text.includes('ACCEPTED')) return { key: 'accepted', label: 'Accepted' }
  return { key: 'none', label: ev.grade_band || '—' }
}

// The verdict is "NOT ACCEPTED — MUST items not fully covered: B2, D2"; the
// part after the dash is the reason worth showing on its own.
export function verdictReason(verdict?: string) {
  if (!verdict) return ''
  const idx = verdict.indexOf('—')
  return idx >= 0 ? verdict.slice(idx + 1).trim() : ''
}

export function scoreColor(score: number) {
  if (score >= 90) return '#15803d'
  if (score >= 75) return '#2563eb'
  if (score >= 60) return '#d97706'
  return '#dc2626'
}

// ─── Scorecard version ──────────────────────────────────────────────────────
// Every evaluation records the scorecard version it was scored on
// (scorecards/registry.json in the backend). Records from before versioning
// carry none and were scored on the client's V2.0 scorecards.

const CLIENT_SCORECARD: Record<string, string> = {
  '0': 'V2.0 Simple',
  '1': 'V2.1 Simple',
}

export function scorecardVersion(ev: { scorecard_version?: unknown }): string {
  const v = ev.scorecard_version
  return v === undefined || v === null || v === '' ? '0' : String(v)
}

export function scorecardLabel(version: string) {
  return version === '0' ? 'Legacy' : `v${version}`
}

export function scoredOn(version: string) {
  const client = CLIENT_SCORECARD[version]
  const name = version === '0' ? 'the legacy scorecard' : `scorecard ${scorecardLabel(version)}`
  return `Scored on ${name}${client ? ` (client ${client})` : ''}`
}

export function scorecardDetail(version: string) {
  const client = CLIENT_SCORECARD[version]
  const name = version === '0' ? 'Legacy scorecard' : `Scorecard ${scorecardLabel(version)}`
  return client ? `${name} (client ${client})` : name
}

// ─── Values ─────────────────────────────────────────────────────────────────

export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = parseFloat(String(value))
  return Number.isFinite(n) ? n : null
}

// DynamoDB flags arrive as booleans on new records and as strings on old ones.
export function toBool(value: unknown) {
  return value === true || String(value).toLowerCase() === 'true'
}

export function toList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  return []
}

export function formatDate(value?: string, withTime = true) {
  if (!value) return '—'
  // Timestamps are stored in UTC without a zone suffix.
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  })
}

export function formatTimestamp(seconds?: number) {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) return '--:--'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function displayName(value?: string) {
  return value && value !== 'Unknown' ? value : ''
}
