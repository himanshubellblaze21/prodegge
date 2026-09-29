import { useRef, useState, DragEvent, ReactNode } from 'react'
import {
  Alert,
  Box,
  Button,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
  alpha,
} from '@mui/material'
import {
  CheckRounded,
  CloudUploadOutlined,
  CloseRounded,
  GraphicEqRounded,
  AddRounded,
} from '@mui/icons-material'
import { uploadRecording } from '../services/api'
import StatusTracker from '../components/StatusTracker'
import { CallTypeBadge } from '../components/Badges'
import { CALL_TYPES, formatFileSize, getCallType } from '../lib/format'

const ACCEPTED = /\.(mp3|wav|m4a|ogg|flac|mp4|aac|opus)$/i
const MAX_BYTES = 500 * 1024 * 1024

function StepCard({
  n,
  title,
  done,
  disabled,
  children,
}: {
  n: number
  title: string
  done?: boolean
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <Paper
      variant="outlined"
      sx={{
        p: { xs: 2.5, sm: 3 },
        opacity: disabled ? 0.55 : 1,
        pointerEvents: disabled ? 'none' : 'auto',
        transition: 'opacity 200ms',
      }}
      aria-disabled={disabled}
    >
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
        <Box
          sx={{
            width: 26,
            height: 26,
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 13,
            fontWeight: 700,
            bgcolor: done ? 'success.main' : 'primary.main',
            color: '#fff',
            flexShrink: 0,
          }}
        >
          {done ? <CheckRounded sx={{ fontSize: 17 }} /> : n}
        </Box>
        <Typography variant="subtitle1">{title}</Typography>
      </Stack>
      {children}
    </Paper>
  )
}

export default function UploadPage({ onViewAll }: { onViewAll?: () => void }) {
  // No auto-detection: the recording is scored against exactly the checklist
  // chosen here, so the choice is required and comes first.
  const [callType, setCallType] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [applicationId, setApplicationId] = useState('')
  const [customerName, setCustomerName] = useState('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [submitted, setSubmitted] = useState<{ id: string; cached: boolean } | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [touched, setTouched] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const selectedType = getCallType(callType)
  const idMissing = !applicationId.trim()
  const ready = Boolean(callType && file && !idMissing)

  const pickFile = (f: File | null | undefined) => {
    if (!f) return
    if (!ACCEPTED.test(f.name)) {
      setError('That file is not a supported audio format. Use MP3, WAV, M4A, OGG, FLAC, AAC or MP4.')
      return
    }
    if (f.size > MAX_BYTES) {
      setError('That file is larger than 500 MB.')
      return
    }
    setFile(f)
    setError('')
    // Suggest an application ID from the file name; the user can overwrite it.
    if (!applicationId) {
      const base = f.name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9-_]/g, '-')
      setApplicationId(`APP-${base.substring(0, 20).toUpperCase()}`)
    }
  }

  const handleDrag = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(e.type === 'dragenter' || e.type === 'dragover')
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)
    pickFile(e.dataTransfer.files?.[0])
  }

  const handleSubmit = async () => {
    setTouched(true)
    if (!ready || !file) return
    setUploading(true)
    setProgress(0)
    setError('')
    try {
      const result = await uploadRecording(
        file,
        { applicationId: applicationId.trim(), customerName: customerName.trim() || 'Unknown', callType },
        (p) => setProgress(p)
      )
      setSubmitted({ id: result.evaluation_id, cached: Boolean(result.cached) })
    } catch (e: any) {
      setError(e.message || 'Upload failed. Please check your connection and try again.')
    } finally {
      setUploading(false)
    }
  }

  const handleReset = () => {
    setFile(null)
    setSubmitted(null)
    setError('')
    setProgress(0)
    setApplicationId('')
    setCustomerName('')
    setCallType('')
    setTouched(false)
  }

  // ── After upload: progress, then the result ────────────────────────────
  if (submitted) {
    return (
      <Box sx={{ maxWidth: 820, mx: 'auto' }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }} spacing={2} sx={{ mb: 3 }}>
          <Box sx={{ minWidth: 0 }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="h5" noWrap>{applicationId.trim()}</Typography>
              <CallTypeBadge callType={callType} />
            </Stack>
            <Typography variant="body2" color="text.secondary" noWrap>
              {[customerName.trim(), file?.name].filter(Boolean).join(' · ')}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            {onViewAll && <Button onClick={onViewAll}>All evaluations</Button>}
            <Button variant="outlined" startIcon={<AddRounded />} onClick={handleReset}>
              New evaluation
            </Button>
          </Stack>
        </Stack>

        {submitted.cached && (
          <Alert severity="info" sx={{ mb: 2 }}>
            This recording was evaluated before, so the earlier result is shown.
          </Alert>
        )}

        <Paper variant="outlined" sx={{ p: { xs: 2.5, sm: 4 } }}>
          <StatusTracker
            evaluationId={submitted.id}
            applicationId={applicationId.trim()}
            customerName={customerName.trim()}
            callType={callType}
          />
        </Paper>
      </Box>
    )
  }

  // ── The form ───────────────────────────────────────────────────────────
  return (
    <Box sx={{ maxWidth: 820, mx: 'auto' }}>
      <Typography variant="h5" sx={{ mb: 3 }}>
        New evaluation
      </Typography>

      <Stack spacing={2}>
        {/* 1 — Call type */}
        <StepCard n={1} title="Choose the call type" done={Boolean(callType)}>
          <Box
            role="radiogroup"
            aria-label="Call type"
            sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' }, gap: 1.5 }}
          >
            {CALL_TYPES.map((ct) => {
              const selected = callType === ct.value
              return (
                <Box
                  key={ct.value}
                  role="radio"
                  aria-checked={selected}
                  tabIndex={0}
                  onClick={() => setCallType(ct.value)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setCallType(ct.value))}
                  sx={{
                    position: 'relative',
                    p: 2,
                    borderRadius: 2,
                    border: 2,
                    borderColor: selected ? ct.color : 'divider',
                    bgcolor: selected ? ct.bg : 'background.paper',
                    cursor: 'pointer',
                    transition: 'border-color 150ms, background-color 150ms',
                    outline: 'none',
                    '&:hover': { borderColor: selected ? ct.color : alpha(ct.color, 0.45) },
                    '&:focus-visible': { boxShadow: `0 0 0 3px ${alpha(ct.color, 0.3)}` },
                  }}
                >
                  <Typography variant="body1" fontWeight={700} sx={{ color: selected ? ct.color : 'text.primary', pr: 3 }}>
                    {ct.label}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {ct.hint}
                  </Typography>
                  {selected && (
                    <Box sx={{ position: 'absolute', top: 12, right: 12, width: 20, height: 20, borderRadius: '50%', bgcolor: ct.color, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <CheckRounded sx={{ fontSize: 14 }} />
                    </Box>
                  )}
                </Box>
              )
            })}
          </Box>
          {touched && !callType && (
            <Typography variant="body2" color="error" sx={{ mt: 1.5 }}>
              Choose the call type.
            </Typography>
          )}
        </StepCard>

        {/* 2 — Recording */}
        <StepCard n={2} title="Add the recording" done={Boolean(file)} disabled={!callType}>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.aac,.opus"
            onChange={(e) => {
              pickFile(e.target.files?.[0])
              e.target.value = ''
            }}
          />
          {!file ? (
            <Box
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), fileInputRef.current?.click())}
              sx={{
                border: 2,
                borderStyle: 'dashed',
                borderColor: dragActive ? 'primary.main' : 'divider',
                bgcolor: dragActive ? alpha('#1e3a8a', 0.04) : 'background.default',
                borderRadius: 2,
                py: { xs: 4, sm: 5 },
                px: 2,
                textAlign: 'center',
                cursor: 'pointer',
                transition: 'all 150ms',
                outline: 'none',
                '&:hover, &:focus-visible': { borderColor: 'primary.main', bgcolor: alpha('#1e3a8a', 0.03) },
              }}
            >
              <CloudUploadOutlined sx={{ fontSize: 40, color: 'primary.main', mb: 1 }} />
              <Typography variant="body1" fontWeight={600}>
                Drop the audio file here, or <Box component="span" sx={{ color: 'primary.main', textDecoration: 'underline' }}>browse</Box>
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                MP3, WAV, M4A, OGG, FLAC, AAC or MP4 · up to 500 MB
              </Typography>
            </Box>
          ) : (
            <Stack
              direction="row"
              spacing={2}
              alignItems="center"
              sx={{ p: 2, borderRadius: 2, border: 1, borderColor: 'divider', bgcolor: 'background.default' }}
            >
              <Box sx={{ width: 40, height: 40, borderRadius: 2, bgcolor: alpha('#1e3a8a', 0.08), color: 'primary.main', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <GraphicEqRounded />
              </Box>
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2" fontWeight={600} noWrap title={file.name}>
                  {file.name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {formatFileSize(file.size)}
                </Typography>
              </Box>
              {!uploading && (
                <>
                  <Button size="small" onClick={() => fileInputRef.current?.click()}>
                    Replace
                  </Button>
                  <Tooltip title="Remove">
                    <IconButton size="small" onClick={() => setFile(null)} aria-label="Remove file">
                      <CloseRounded fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </>
              )}
            </Stack>
          )}
          {touched && callType && !file && (
            <Typography variant="body2" color="error" sx={{ mt: 1.5 }}>
              Add the recording.
            </Typography>
          )}
        </StepCard>

        {/* 3 — Details */}
        <StepCard n={3} title="Confirm the details" done={Boolean(file && !idMissing)} disabled={!file}>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField
              label="Application ID"
              value={applicationId}
              onChange={(e) => setApplicationId(e.target.value)}
              placeholder="e.g. APP-12345"
              fullWidth
              required
              error={touched && idMissing}
              helperText={touched && idMissing ? 'Enter the application ID' : ' '}
            />
            <TextField
              label="Customer name (optional)"
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              placeholder="e.g. Ramesh Kumar"
              fullWidth
              helperText=" "
            />
          </Stack>
        </StepCard>
      </Stack>

      {error && (
        <Alert severity="error" onClose={() => setError('')} sx={{ mt: 2 }}>
          {error}
        </Alert>
      )}

      {/* Submit */}
      <Paper variant="outlined" sx={{ mt: 2, p: 2.5 }}>
        {uploading ? (
          <Box>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 1 }}>
              <Typography variant="body2" fontWeight={600}>
                Uploading…
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {progress}%
              </Typography>
            </Stack>
            <LinearProgress variant="determinate" value={progress} sx={{ height: 8, borderRadius: 4 }} />
          </Box>
        ) : (
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'center' }} justifyContent="space-between">
            <Typography variant="body2" color="text.secondary">
              {ready
                ? `Will be scored against the ${selectedType?.label} checklist.`
                : !callType
                  ? 'Start by choosing the call type.'
                  : !file
                    ? 'Next, add the recording.'
                    : 'Enter the application ID to continue.'}
            </Typography>
            <Button
              variant="contained"
              size="large"
              onClick={handleSubmit}
              disabled={uploading}
              sx={{ px: 4, flexShrink: 0, ...(!ready && { opacity: 0.6 }) }}
            >
              Start evaluation
            </Button>
          </Stack>
        )}
      </Paper>
    </Box>
  )
}
