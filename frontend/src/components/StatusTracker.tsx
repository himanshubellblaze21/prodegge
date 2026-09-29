import { useEffect, useRef, useState } from 'react'
import { Alert, AlertTitle, Box, LinearProgress, Stack, Typography } from '@mui/material'
import { CheckRounded } from '@mui/icons-material'
import { pollEvaluationStatus } from '../services/api'
import { getStatus } from '../lib/format'
import EvaluationSummary from './EvaluationSummary'

interface StatusTrackerProps {
  evaluationId: string
  applicationId?: string
  customerName?: string
  callType?: string
}

const STEPS = [
  { keys: ['UPLOADED', 'PREPROCESSING'], label: 'Preparing audio', eta: 'under a minute' },
  { keys: ['TRANSCRIBING'], label: 'Transcribing the call', eta: 'about 3–5 minutes' },
  { keys: ['EVALUATING'], label: 'Scoring against the checklist', eta: 'about 1–2 minutes' },
  { keys: ['EXCEL_GENERATING'], label: 'Building the scorecard', eta: 'a few seconds' },
]

function stepIndex(status: string) {
  if (status === 'COMPLETED') return STEPS.length
  const i = STEPS.findIndex((s) => s.keys.includes(status))
  return i < 0 ? 0 : i
}

export default function StatusTracker({ evaluationId, applicationId, customerName, callType }: StatusTrackerProps) {
  const [status, setStatus] = useState<any>({ status: 'UPLOADED' })
  const [started] = useState(() => Date.now())
  const [now, setNow] = useState(Date.now())
  const failedStep = useRef(0)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    let stopped = false
    let polls = 0

    const poll = async () => {
      try {
        const data = await pollEvaluationStatus(evaluationId)
        if (stopped) return
        setStatus(data)
        const tone = getStatus(data.status).tone
        if (tone === 'done' || tone === 'failed') return
      } catch {
        // A dropped poll is retried on the next tick.
      }
      polls += 1
      // Every 5s for the first two minutes, then every 15s.
      timer = setTimeout(poll, polls < 24 ? 5000 : 15000)
    }
    poll()

    const clock = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      stopped = true
      clearTimeout(timer)
      clearInterval(clock)
    }
  }, [evaluationId])

  const tone = getStatus(status.status).tone
  const current = stepIndex(status.status)
  if (tone !== 'failed') failedStep.current = current

  if (tone === 'done') {
    return (
      <EvaluationSummary
        evaluationId={evaluationId}
        base={{ ...status, application_id: applicationId, customer_name: customerName, call_type: status.call_type || callType }}
      />
    )
  }

  const elapsed = Math.floor((now - started) / 1000)
  const elapsedLabel = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`

  return (
    <Box>
      {tone === 'failed' ? (
        <Alert severity="error" sx={{ mb: 3 }}>
          <AlertTitle sx={{ fontWeight: 700 }}>Processing failed</AlertTitle>
          {status.error_message || 'Something went wrong while processing this recording.'} Please upload it again.
        </Alert>
      ) : (
        <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 1 }}>
          <Typography variant="body2" color="text.secondary">
            This usually takes 5–8 minutes. You can leave this page — the result will be in Evaluations.
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums', ml: 2, flexShrink: 0 }}>
            {elapsedLabel}
          </Typography>
        </Stack>
      )}

      {tone !== 'failed' && (
        <LinearProgress
          variant="determinate"
          value={Math.max(4, (current / STEPS.length) * 100)}
          sx={{ height: 6, borderRadius: 3, mb: 3, bgcolor: '#e9ecf2' }}
        />
      )}

      <Stack spacing={0}>
        {STEPS.map((step, i) => {
          const done = i < current
          const active = i === current && tone !== 'failed'
          const failed = tone === 'failed' && i === failedStep.current
          return (
            <Stack key={step.label} direction="row" spacing={2} alignItems="flex-start">
              <Stack alignItems="center" sx={{ width: 28 }}>
                <Box
                  sx={{
                    width: 28,
                    height: 28,
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: done ? 'success.main' : failed ? 'error.main' : 'background.paper',
                    border: 2,
                    borderColor: done ? 'success.main' : active ? 'primary.main' : failed ? 'error.main' : 'divider',
                    color: done || failed ? '#fff' : active ? 'primary.main' : 'text.disabled',
                    fontSize: 13,
                    fontWeight: 700,
                    position: 'relative',
                    ...(active && {
                      '&::after': {
                        content: '""',
                        position: 'absolute',
                        inset: -2,
                        borderRadius: '50%',
                        border: 2,
                        borderColor: 'transparent',
                        borderTopColor: 'primary.main',
                        animation: 'spin 0.9s linear infinite',
                      },
                      '@keyframes spin': { to: { transform: 'rotate(360deg)' } },
                    }),
                  }}
                >
                  {done ? <CheckRounded sx={{ fontSize: 18 }} /> : failed ? '!' : i + 1}
                </Box>
                {i < STEPS.length - 1 && (
                  <Box sx={{ width: 2, height: 28, bgcolor: done ? 'success.main' : 'divider', opacity: done ? 0.5 : 1 }} />
                )}
              </Stack>
              <Box sx={{ pt: 0.4 }}>
                <Typography
                  variant="body2"
                  fontWeight={active || failed ? 700 : 500}
                  color={failed ? 'error.main' : done || active ? 'text.primary' : 'text.secondary'}
                >
                  {step.label}
                </Typography>
                {active && (
                  <Typography variant="caption" color="text.secondary">
                    {step.eta}
                  </Typography>
                )}
              </Box>
            </Stack>
          )
        })}
      </Stack>
    </Box>
  )
}
