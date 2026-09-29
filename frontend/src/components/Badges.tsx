import { Box, Chip, Stack, Tooltip, Typography, alpha } from '@mui/material'
import {
  CheckCircleRounded,
  CancelRounded,
  RemoveCircleRounded,
  ErrorRounded,
} from '@mui/icons-material'
import {
  getCallType,
  getOutcome,
  getStatus,
  scoreColor,
  scorecardDetail,
  scorecardLabel,
  scorecardVersion,
  toNumber,
} from '../lib/format'

// Which scorecard version an evaluation was scored on.
export function ScorecardBadge({ ev }: { ev: any }) {
  const version = scorecardVersion(ev)
  const legacy = version === '0'
  return (
    <Tooltip title={scorecardDetail(version)}>
      <Chip
        label={scorecardLabel(version)}
        size="small"
        variant="outlined"
        sx={{
          fontWeight: 600,
          fontVariantNumeric: 'tabular-nums',
          color: legacy ? 'text.secondary' : 'primary.main',
          borderColor: legacy ? 'divider' : alpha('#1e3a8a', 0.3),
          bgcolor: legacy ? 'transparent' : alpha('#1e3a8a', 0.04),
        }}
      />
    </Tooltip>
  )
}

export function CallTypeBadge({ callType }: { callType?: string }) {
  const meta = getCallType(callType)
  if (!meta) {
    return <Chip label={callType || 'Unknown'} size="small" variant="outlined" />
  }
  return (
    <Chip
      label={meta.short}
      size="small"
      sx={{
        bgcolor: meta.bg,
        color: meta.color,
        border: `1px solid ${alpha(meta.color, 0.22)}`,
        fontWeight: 600,
      }}
    />
  )
}

const OUTCOME_STYLE = {
  accepted: { color: '#15803d', icon: <CheckCircleRounded /> },
  not_accepted: { color: '#b91c1c', icon: <CancelRounded /> },
  not_evaluable: { color: '#b45309', icon: <RemoveCircleRounded /> },
  none: { color: '#64748b', icon: undefined },
}

export function OutcomeBadge({ ev, size = 'small' }: { ev: any; size?: 'small' | 'medium' }) {
  const status = getStatus(ev.status)
  if (status.tone !== 'done') return <StatusBadge status={ev.status} />
  const outcome = getOutcome(ev)
  const style = OUTCOME_STYLE[outcome.key]
  return (
    <Chip
      icon={style.icon}
      label={outcome.label}
      size={size}
      sx={{
        bgcolor: alpha(style.color, 0.08),
        color: style.color,
        fontWeight: 600,
        '& .MuiChip-icon': { color: style.color, fontSize: size === 'small' ? 16 : 20 },
      }}
    />
  )
}

export function StatusBadge({ status }: { status?: string }) {
  const s = getStatus(status)
  const color = { done: '#15803d', active: '#2563eb', failed: '#b91c1c', waiting: '#64748b' }[s.tone]
  return (
    <Chip
      icon={s.tone === 'failed' ? <ErrorRounded /> : undefined}
      label={s.label}
      size="small"
      sx={{
        bgcolor: alpha(color, 0.08),
        color,
        fontWeight: 600,
        '& .MuiChip-icon': { color, fontSize: 16 },
        ...(s.tone === 'active' && {
          '&::before': {
            content: '""',
            width: 6,
            height: 6,
            borderRadius: '50%',
            bgcolor: color,
            ml: 1,
            animation: 'pulse 1.4s ease-in-out infinite',
          },
          '@keyframes pulse': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.25 } },
        }),
      }}
    />
  )
}

export function ScoreBar({ score, width = 64 }: { score?: unknown; width?: number }) {
  const n = toNumber(score)
  if (n === null) {
    return (
      <Typography variant="body2" color="text.disabled">
        —
      </Typography>
    )
  }
  const color = scoreColor(n)
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography variant="body2" fontWeight={700} sx={{ color, minWidth: 26, fontVariantNumeric: 'tabular-nums' }}>
        {Math.round(n)}
      </Typography>
      <Box sx={{ width, height: 6, borderRadius: 3, bgcolor: '#e9ecf2', overflow: 'hidden' }}>
        <Box sx={{ height: '100%', width: `${Math.min(Math.max(n, 0), 100)}%`, bgcolor: color, borderRadius: 3 }} />
      </Box>
    </Stack>
  )
}
