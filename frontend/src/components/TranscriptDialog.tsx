import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { CloseRounded, DownloadRounded, SearchRounded } from '@mui/icons-material'
import { downloadTranscript, getTranscript, speakerName } from '../services/api'
import { formatTimestamp, getCallType } from '../lib/format'
import { useNotify } from './Notifier'
import type { DownloadTarget } from './DownloadActions'

const SPEAKER_COLORS = ['#1d4ed8', '#047857', '#7c3aed', '#b45309']

export default function TranscriptDialog({
  open,
  onClose,
  target,
}: {
  open: boolean
  onClose: () => void
  target: DownloadTarget
}) {
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))
  const notify = useNotify()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setData(null)
    setError('')
    setQuery('')
    getTranscript(target.evaluationId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message || 'Could not load the transcript'))
    return () => {
      cancelled = true
    }
  }, [open, target.evaluationId])

  const segments: any[] = data?.segments || []
  const speakerIds = useMemo(
    () => Array.from(new Set(segments.map((s) => s.speaker).filter(Boolean))),
    [segments]
  )
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? segments.filter((s) => (s.text || '').toLowerCase().includes(q)) : segments
  }, [segments, query])

  const handleDownload = async () => {
    setDownloading(true)
    try {
      await downloadTranscript(target.evaluationId, {
        applicationId: target.applicationId,
        customerName: target.customerName,
        callTypeLabel: getCallType(target.callType)?.label,
      })
      notify('Download started')
    } catch (e: any) {
      notify(e?.message || 'Download failed', 'error')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth fullScreen={fullScreen}
      PaperProps={{ sx: { height: fullScreen ? '100%' : '85vh' } }}>
      <DialogTitle sx={{ pr: 7 }}>
        <Typography variant="h6" component="div">Transcript</Typography>
        <Typography variant="body2" color="text.secondary" component="div">
          {target.applicationId || target.evaluationId}
        </Typography>
        <IconButton onClick={onClose} aria-label="Close" sx={{ position: 'absolute', right: 12, top: 12 }}>
          <CloseRounded />
        </IconButton>
      </DialogTitle>

      <Box sx={{ px: 3, pb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
          <TextField
            size="small"
            fullWidth
            placeholder="Search the transcript"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={!segments.length}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRounded fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
          <Button
            variant="outlined"
            startIcon={downloading ? <CircularProgress size={16} /> : <DownloadRounded />}
            onClick={handleDownload}
            disabled={!data || downloading}
            sx={{ flexShrink: 0 }}
          >
            Download
          </Button>
        </Stack>
      </Box>

      <DialogContent sx={{ bgcolor: 'background.default', py: 2 }}>
        {!data && !error && (
          <Stack alignItems="center" justifyContent="center" sx={{ height: '100%' }}>
            <CircularProgress size={28} />
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}

        {data && !segments.length && (
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
            {data.transcript || 'This transcript is empty.'}
          </Typography>
        )}

        {data && segments.length > 0 && visible.length === 0 && (
          <Typography color="text.secondary" textAlign="center" sx={{ mt: 4 }}>
            No lines match “{query}”.
          </Typography>
        )}

        {visible.length > 0 && (
          <Box sx={{ bgcolor: 'background.paper', borderRadius: 2, border: 1, borderColor: 'divider', overflow: 'hidden' }}>
            {visible.map((seg, i) => {
              const color = SPEAKER_COLORS[Math.max(0, speakerIds.indexOf(seg.speaker)) % SPEAKER_COLORS.length]
              return (
                <Box
                  key={`${seg.start}-${i}`}
                  sx={{
                    display: 'grid',
                    gridTemplateColumns: { xs: '1fr', sm: '110px 1fr' },
                    gap: { xs: 0.25, sm: 2 },
                    px: 2,
                    py: 1.25,
                    borderTop: i ? 1 : 0,
                    borderColor: 'divider',
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Stack direction="row" spacing={1} alignItems="baseline">
                    <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums', minWidth: 38 }}>
                      {formatTimestamp(seg.start)}
                    </Typography>
                    <Typography variant="caption" fontWeight={700} sx={{ color }} noWrap>
                      {speakerName(data.speakers, seg.speaker)}
                    </Typography>
                  </Stack>
                  <Typography variant="body2" sx={{ lineHeight: 1.7 }}>
                    {seg.text}
                  </Typography>
                </Box>
              )
            })}
          </Box>
        )}
        {data && segments.length > 0 && (
          <Typography variant="caption" color="text.secondary" display="block" textAlign="center" sx={{ mt: 2 }}>
            {query ? `${visible.length} of ${segments.length} lines` : `${segments.length} lines`}
          </Typography>
        )}
      </DialogContent>
    </Dialog>
  )
}
