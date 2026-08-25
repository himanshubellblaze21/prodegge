import { useEffect, useState } from 'react'
import {
  Box,
  Paper,
  Typography,
  LinearProgress,
  Stack,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
} from '@mui/material'
import {
  CheckCircle,
  Error,
  PlayArrow,
  Description,
  TableChart,
  Download,
  Close,
} from '@mui/icons-material'
import { pollEvaluationStatus, getTranscript, downloadExcelScorecard } from '../services/api'

interface StatusTrackerProps {
  evaluationId: string
  onComplete?: () => void
}

interface Status {
  status: string
  total_score?: number
  grade_band?: string
  error_message?: string
  transcript_s3_key?: string
  excel_s3_key?: string
}

const statusSteps = [
  { key: 'UPLOADED', label: 'Uploaded', time: '0s' },
  { key: 'PREPROCESSING', label: 'Preprocessing', time: '10-20s' },
  { key: 'TRANSCRIBING', label: 'Transcribing', time: '3-5 min' },
  { key: 'EVALUATING', label: 'AI Evaluation', time: '1-2 min' },
  { key: 'EXCEL_GENERATING', label: 'Generating Excel', time: '10s' },
  { key: 'COMPLETED', label: 'Completed', time: '' },
]

export default function StatusTracker({ evaluationId, onComplete }: StatusTrackerProps) {
  const [status, setStatus] = useState<Status>({ status: 'UPLOADED' })
  const [polling, setPolling] = useState(true)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [transcript, setTranscript] = useState<any>(null)

  useEffect(() => {
    let interval: NodeJS.Timeout
    let pollInterval = 5000 // Start with 5 seconds
    let pollCount = 0

    const poll = async () => {
      try {
        const data = await pollEvaluationStatus(evaluationId)
        setStatus(data)

        if (data.status === 'COMPLETED' || data.status === 'FAILED') {
          setPolling(false)
          onComplete?.()
        }

        pollCount++
        // Exponential backoff: 5s for 2min, then 10s for 5min, then 30s
        if (pollCount > 24) pollInterval = 30000 // After 2 minutes (24 * 5s)
        else if (pollCount > 60) pollInterval = 30000 // After 5 minutes
      } catch (error) {
        console.error('Polling error:', error)
      }
    }

    if (polling) {
      poll() // Initial poll
      interval = setInterval(poll, pollInterval)
    }

    return () => clearInterval(interval)
  }, [evaluationId, polling, onComplete])

  const currentStepIndex = statusSteps.findIndex((s) => s.key === status.status)

  const handleViewTranscript = async () => {
    try {
      const data = await getTranscript(evaluationId)
      setTranscript(data)
      setTranscriptOpen(true)
    } catch (error) {
      console.error('Failed to load transcript:', error)
    }
  }

  const handleDownloadExcel = async () => {
    try {
      await downloadExcelScorecard(evaluationId)
    } catch (error) {
      console.error('Failed to download Excel:', error)
    }
  }

  return (
    <Paper elevation={3} sx={{ p: 4, mt: 3 }}>
      <Typography variant="h6" gutterBottom fontWeight="600">
        Processing Status
      </Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Evaluation ID: {evaluationId}
      </Typography>

      {/* Status Steps */}
      <Box sx={{ mt: 3 }}>
        {statusSteps.map((step, index) => (
          <Stack
            key={step.key}
            direction="row"
            spacing={2}
            alignItems="center"
            sx={{ mb: 2 }}
          >
            {/* Icon */}
            {index < currentStepIndex || status.status === 'COMPLETED' ? (
              <CheckCircle sx={{ color: 'success.main', fontSize: 32 }} />
            ) : index === currentStepIndex && status.status !== 'FAILED' ? (
              <PlayArrow sx={{ color: 'primary.main', fontSize: 32 }} />
            ) : status.status === 'FAILED' && index === currentStepIndex ? (
              <Error sx={{ color: 'error.main', fontSize: 32 }} />
            ) : (
              <Box
                sx={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  border: 2,
                  borderColor: 'divider',
                }}
              />
            )}

            {/* Label */}
            <Box sx={{ flex: 1 }}>
              <Typography variant="body1" fontWeight={index === currentStepIndex ? 600 : 400}>
                {step.label}
              </Typography>
              {index === currentStepIndex && step.time && (
                <Typography variant="caption" color="text.secondary">
                  Est. {step.time} remaining
                </Typography>
              )}
            </Box>

            {/* Status Chip */}
            {index === currentStepIndex && status.status !== 'FAILED' && (
              <Chip label="In Progress" color="primary" size="small" />
            )}
            {index < currentStepIndex && (
              <Chip label="Done" color="success" size="small" variant="outlined" />
            )}
          </Stack>
        ))}
      </Box>

      {/* Progress Bar */}
      {status.status !== 'COMPLETED' && status.status !== 'FAILED' && (
        <LinearProgress sx={{ mt: 3, height: 6, borderRadius: 3 }} />
      )}

      {/* Error Message */}
      {status.status === 'FAILED' && (
        <Paper
          elevation={0}
          sx={{ p: 2, mt: 3, bgcolor: 'error.light', color: 'error.contrastText' }}
        >
          <Typography variant="body2" fontWeight="600">
            ❌ Processing Failed
          </Typography>
          <Typography variant="body2">{status.error_message || 'Unknown error'}</Typography>
        </Paper>
      )}

      {/* Completion Actions */}
      {status.status === 'COMPLETED' && (
        <Box sx={{ mt: 4 }}>
          <Paper elevation={0} sx={{ p: 3, bgcolor: 'success.light', mb: 3 }}>
            <Typography variant="h6" fontWeight="600" color="success.dark" gutterBottom>
              ✓ Evaluation Completed!
            </Typography>
            {status.total_score !== undefined && (
              <Typography variant="body1" color="success.dark">
                Score: {status.total_score}/100 • Grade: {status.grade_band}
              </Typography>
            )}
          </Paper>

          <Stack direction="row" spacing={2}>
            <Button
              variant="outlined"
              startIcon={<Description />}
              onClick={handleViewTranscript}
              disabled={!status.transcript_s3_key}
            >
              View Transcript
            </Button>
            <Button
              variant="contained"
              startIcon={<Download />}
              onClick={handleDownloadExcel}
              disabled={!status.excel_s3_key}
            >
              Download Excel
            </Button>
          </Stack>
        </Box>
      )}

      {/* Transcript Dialog */}
      <Dialog open={transcriptOpen} onClose={() => setTranscriptOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>
          Call Transcript
          <IconButton
            onClick={() => setTranscriptOpen(false)}
            sx={{ position: 'absolute', right: 8, top: 8 }}
          >
            <Close />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {transcript?.transcript && (
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
              {transcript.transcript}
            </Typography>
          )}
          {transcript?.segments && (
            <Box>
              {transcript.segments.map((seg: any, idx: number) => (
                <Box key={idx} sx={{ mb: 2, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
                  <Typography variant="caption" color="primary" fontWeight="600">
                    [{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s] {seg.speaker || 'Speaker'}
                  </Typography>
                  <Typography variant="body2" sx={{ mt: 1 }}>
                    {seg.text}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTranscriptOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}
