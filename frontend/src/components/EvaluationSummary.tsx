import { useEffect, useState } from 'react'
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Divider,
  LinearProgress,
  Skeleton,
  Stack,
  Typography,
  alpha,
} from '@mui/material'
import { SubjectRounded, FlagRounded } from '@mui/icons-material'
import { getEvaluationDetail } from '../services/api'
import {
  getOutcome,
  scoreColor,
  scoredOn,
  scorecardVersion,
  toBool,
  toList,
  toNumber,
  verdictReason,
} from '../lib/format'
import { OutcomeBadge } from './Badges'
import { DownloadButtons, DownloadTarget } from './DownloadActions'
import TranscriptDialog from './TranscriptDialog'

function ScoreRing({ score }: { score: number | null }) {
  const size = 104
  const stroke = 9
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const value = score === null ? 0 : Math.min(Math.max(score, 0), 100)
  const color = score === null ? '#94a3b8' : scoreColor(score)
  return (
    <Box sx={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} role="img" aria-label={score === null ? 'No score' : `Score ${Math.round(value)} out of 100`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e9ecf2" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value / 100)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dashoffset 600ms ease' }}
        />
      </svg>
      <Stack alignItems="center" justifyContent="center" sx={{ position: 'absolute', inset: 0 }}>
        <Typography sx={{ fontSize: 30, fontWeight: 800, lineHeight: 1, color, fontVariantNumeric: 'tabular-nums' }}>
          {score === null ? '—' : Math.round(value)}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          of 100
        </Typography>
      </Stack>
    </Box>
  )
}

function SectionHeading({ children }: { children: string }) {
  return (
    <Typography variant="overline" color="text.secondary" sx={{ fontWeight: 700, letterSpacing: '0.06em' }}>
      {children}
    </Typography>
  )
}

export default function EvaluationSummary({ evaluationId, base }: { evaluationId: string; base?: any }) {
  const [detail, setDetail] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [transcriptOpen, setTranscriptOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getEvaluationDetail(evaluationId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [evaluationId])

  const ev = { ...(base || {}), ...(detail || {}) }
  const result = detail?.result_data || {}
  const scoring = result.scoring || {}
  const outcome = getOutcome(ev)
  const notEvaluable = outcome.key === 'not_evaluable'
  const score = notEvaluable ? null : toNumber(ev.total_score)
  const reason = verdictReason(ev.verdict)
  const needsReview = toBool(ev.needs_review ?? result.needs_review)
  const reviewReasons = toList(ev.review_reasons ?? result.review_reasons)
  const sections = Object.entries(scoring.sections || {}) as [string, any][]
  const gaps: any[] = Array.isArray(scoring.top3_gaps) ? scoring.top3_gaps : []
  const redFlags: any[] = Array.isArray(result.red_flags) ? result.red_flags : []

  const target: DownloadTarget = {
    evaluationId,
    applicationId: ev.application_id,
    customerName: ev.customer_name,
    callType: ev.call_type,
    hasExcel: Boolean(ev.excel_s3_key),
    hasTranscript: Boolean(ev.transcript_s3_key),
  }

  return (
    <Stack spacing={3}>
      {/* Headline */}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={3} alignItems={{ xs: 'flex-start', sm: 'center' }}>
        <ScoreRing score={score} />
        <Box sx={{ minWidth: 0 }}>
          <OutcomeBadge ev={ev} size="medium" />
          {reason && (
            <Typography variant="body1" sx={{ mt: 1.25, fontWeight: 500 }}>
              {reason}
            </Typography>
          )}
          {!reason && outcome.key === 'accepted' && (
            <Typography variant="body1" sx={{ mt: 1.25, fontWeight: 500 }}>
              All MUST items were covered.
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            {scoredOn(scorecardVersion(ev))}
          </Typography>
        </Box>
      </Stack>

      {needsReview && !notEvaluable && (
        <Alert severity="warning">
          <AlertTitle sx={{ fontWeight: 700 }}>Check this result before relying on it</AlertTitle>
          {reviewReasons.length ? reviewReasons.map((r, i) => <div key={i}>{r}</div>) : 'The evaluator flagged this run as uncertain.'}
        </Alert>
      )}

      {/* Actions */}
      <Stack spacing={1.25}>
        <DownloadButtons target={target} />
        <Box>
          <Button
            startIcon={<SubjectRounded />}
            onClick={() => setTranscriptOpen(true)}
            disabled={!target.hasTranscript}
            sx={{ ml: -1 }}
          >
            View transcript
          </Button>
        </Box>
      </Stack>

      {loading && (
        <Stack spacing={1}>
          <Skeleton variant="rounded" height={18} width={140} />
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={28} />)}
        </Stack>
      )}

      {/* Section breakdown */}
      {!loading && sections.length > 0 && !notEvaluable && (
        <Box>
          <Divider sx={{ mb: 2 }} />
          <SectionHeading>Score by section</SectionHeading>
          <Stack spacing={1.25} sx={{ mt: 1 }}>
            {sections.map(([key, sec]) => {
              const max = toNumber(sec.applicable ?? sec.max) || 0
              const got = toNumber(sec.scored) || 0
              const pct = max ? (got / max) * 100 : 0
              return (
                <Box
                  key={key}
                  sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 64px', sm: 'minmax(0, 260px) 1fr 64px' }, gap: { xs: 0.5, sm: 2 }, alignItems: 'center' }}
                >
                  <Typography variant="body2" noWrap title={sec.name}>
                    <Box component="span" sx={{ fontWeight: 700, mr: 1 }}>{key}</Box>
                    {sec.name}
                  </Typography>
                  <LinearProgress
                    variant="determinate"
                    value={pct}
                    sx={{
                      height: 8,
                      borderRadius: 4,
                      bgcolor: '#e9ecf2',
                      gridColumn: { xs: '1 / -1', sm: 'auto' },
                      gridRow: { xs: 2, sm: 'auto' },
                      '& .MuiLinearProgress-bar': { bgcolor: scoreColor(pct), borderRadius: 4 },
                    }}
                  />
                  <Typography variant="body2" color="text.secondary" textAlign="right" sx={{ fontVariantNumeric: 'tabular-nums', gridRow: { xs: 1, sm: 'auto' }, gridColumn: { xs: 2, sm: 'auto' } }}>
                    {Number.isInteger(got) ? got : got.toFixed(1)} / {max}
                  </Typography>
                </Box>
              )
            })}
          </Stack>
        </Box>
      )}

      {/* Biggest gaps */}
      {!loading && gaps.length > 0 && !notEvaluable && (
        <Box>
          <SectionHeading>Biggest gaps</SectionHeading>
          <Stack spacing={1} sx={{ mt: 1 }}>
            {gaps.map((g, i) => (
              <Stack key={g.id || i} direction="row" spacing={1.5} alignItems="flex-start"
                sx={{ p: 1.5, borderRadius: 2, border: 1, borderColor: 'divider' }}>
                <Box sx={{ px: 1, py: 0.25, borderRadius: 1, bgcolor: alpha('#b91c1c', 0.08), color: '#b91c1c', fontWeight: 700, fontSize: 13, flexShrink: 0 }}>
                  {g.id}
                </Box>
                <Typography variant="body2" sx={{ flex: 1 }}>{g.text}</Typography>
                {toNumber(g.pts_lost) !== null && (
                  <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                    −{g.pts_lost} pts
                  </Typography>
                )}
              </Stack>
            ))}
          </Stack>
        </Box>
      )}

      {/* Red flags */}
      {!loading && redFlags.length > 0 && (
        <Box>
          <SectionHeading>Red flags</SectionHeading>
          <Stack spacing={1} sx={{ mt: 1 }}>
            {redFlags.map((f, i) => (
              <Stack key={i} direction="row" spacing={1.5} alignItems="flex-start"
                sx={{ p: 1.5, borderRadius: 2, bgcolor: alpha('#b91c1c', 0.04), border: 1, borderColor: alpha('#b91c1c', 0.18) }}>
                <FlagRounded sx={{ color: '#b91c1c', fontSize: 20, mt: 0.25 }} />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={700}>
                    {[f.code, f.title].filter(Boolean).join(' · ')}
                    {f.timestamp && (
                      <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                        at {f.timestamp}
                      </Typography>
                    )}
                  </Typography>
                  {f.detail && <Typography variant="body2" color="text.secondary">{f.detail}</Typography>}
                  {f.evidence && (
                    <Typography variant="body2" sx={{ mt: 0.5, fontStyle: 'italic' }}>“{f.evidence}”</Typography>
                  )}
                </Box>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}

      <TranscriptDialog open={transcriptOpen} onClose={() => setTranscriptOpen(false)} target={target} />
    </Stack>
  )
}
