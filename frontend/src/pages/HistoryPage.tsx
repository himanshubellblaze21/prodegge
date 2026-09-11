import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  Card,
  CardContent,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  CircularProgress,
  Alert,
  Snackbar,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Stack,
  IconButton,
} from '@mui/material'
import { ExpandMore, CheckCircle, Cancel, Visibility, Download, Refresh, Description } from '@mui/icons-material'
import { getEvaluationsList, downloadExcelScorecard, getTranscript } from '../services/api'

export default function HistoryPage() {
  const [evaluations, setEvaluations] = useState<any[]>([])
  const [filteredEvaluations, setFilteredEvaluations] = useState<any[]>([])
  const [selectedEval, setSelectedEval] = useState<any>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [transcript, setTranscript] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false,
    message: '',
    severity: 'success'
  })

  // Load evaluations on mount
  useEffect(() => {
    loadEvaluations()
  }, [])

  // Filter evaluations when filter changes
  useEffect(() => {
    if (statusFilter === 'ALL') {
      setFilteredEvaluations(evaluations)
    } else {
      setFilteredEvaluations(evaluations.filter(e => e.status === statusFilter))
    }
  }, [statusFilter, evaluations])

  const loadEvaluations = async () => {
    setLoading(true)
    try {
      const data = await getEvaluationsList()
      setEvaluations(data)
      setFilteredEvaluations(data)
    } catch (error: any) {
      setSnackbar({
        open: true,
        message: error.message || 'Failed to load evaluations',
        severity: 'error'
      })
      setEvaluations([])
      setFilteredEvaluations([])
    } finally {
      setLoading(false)
    }
  }

  const handleViewDetails = (evaluation: any) => {
    setSelectedEval(evaluation)
    setDialogOpen(true)
  }

  const handleViewTranscript = async (evaluationId: string) => {
    try {
      const data = await getTranscript(evaluationId)
      setTranscript(data)
      setTranscriptOpen(true)
    } catch (error: any) {
      setSnackbar({
        open: true,
        message: error.message || 'Failed to load transcript',
        severity: 'error'
      })
    }
  }

  const handleDownloadExcel = async (evaluationId: string) => {
    setDownloading(true)
    try {
      await downloadExcelScorecard(evaluationId)
      setSnackbar({
        open: true,
        message: 'Excel scorecard downloaded successfully!',
        severity: 'success'
      })
    } catch (error: any) {
      setSnackbar({
        open: true,
        message: error.message || 'Failed to download Excel',
        severity: 'error'
      })
    } finally {
      setDownloading(false)
    }
  }

  const getStatusColor = (status: string) => {
    if (status === 'COMPLETED') return 'success'
    if (status === 'FAILED') return 'error'
    if (status?.includes('ING')) return 'warning'
    return 'info'
  }

  const getBandColor = (band: string) => {
    if (!band) return 'default'
    if (band.startsWith('A')) return 'success'
    if (band.startsWith('B')) return 'info'
    if (band.startsWith('C')) return 'warning'
    return 'error'
  }

  const formatDate = (dateStr: string) => {
    if (!dateStr) return 'N/A'
    try {
      const date = new Date(dateStr)
      return date.toLocaleString('en-IN', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return dateStr
    }
  }

  const getSections = (evaluation: any) => {
    const scoring = evaluation?.result_data?.scoring
    if (!scoring?.sections) return []
    
    return Object.entries(scoring.sections).map(([key, section]: [string, any]) => ({
      key,
      name: section.name,
      scored: parseFloat(section.scored_points || 0),
      max: parseFloat(section.applicable_points || 0),
      coverage: parseFloat(section.coverage_percentage || 0)
    }))
  }

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h5" gutterBottom>
            Evaluation History
          </Typography>
          <Typography variant="body2" color="text.secondary">
            View all previous audio evaluations and their scorecards
          </Typography>
        </Box>
        <IconButton onClick={loadEvaluations} disabled={loading} color="primary">
          <Refresh />
        </IconButton>
      </Box>

      {/* Filters */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Status Filter</InputLabel>
          <Select
            value={statusFilter}
            label="Status Filter"
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <MenuItem value="ALL">All</MenuItem>
            <MenuItem value="COMPLETED">Completed</MenuItem>
            <MenuItem value="FAILED">Failed</MenuItem>
            <MenuItem value="TRANSCRIBING">Transcribing</MenuItem>
            <MenuItem value="EVALUATING">Evaluating</MenuItem>
          </Select>
        </FormControl>

        <Typography variant="body2" color="text.secondary" sx={{ pt: 1 }}>
          Showing {filteredEvaluations.length} of {evaluations.length} evaluations
        </Typography>
      </Stack>

      {/* Loading State */}
      {loading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
          <CircularProgress />
        </Box>
      )}

      {/* Empty State */}
      {!loading && filteredEvaluations.length === 0 && (
        <Paper sx={{ p: 5, textAlign: 'center' }}>
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No evaluations found
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {statusFilter !== 'ALL' 
              ? 'Try changing the filter or upload a new audio file'
              : 'Upload an audio file to get started'}
          </Typography>
        </Paper>
      )}

      {/* Table */}
      {!loading && filteredEvaluations.length > 0 && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell><strong>Evaluation ID</strong></TableCell>
                <TableCell><strong>Application ID</strong></TableCell>
                <TableCell><strong>Upload Date</strong></TableCell>
                <TableCell><strong>Score</strong></TableCell>
                <TableCell><strong>Band</strong></TableCell>
                <TableCell><strong>Status</strong></TableCell>
                <TableCell><strong>Actions</strong></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredEvaluations.map((evaluation) => (
                <TableRow key={`${evaluation.evaluation_id}|${evaluation.created_at}`} hover>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace">
                      {evaluation.evaluation_id}
                    </Typography>
                  </TableCell>
                  <TableCell>{evaluation.application_id || 'N/A'}</TableCell>
                  <TableCell>{formatDate(evaluation.created_at)}</TableCell>
                  <TableCell>
                    {evaluation.total_score !== null && evaluation.total_score !== undefined ? (
                      <Typography variant="body2" fontWeight="bold" color="primary">
                        {parseFloat(evaluation.total_score).toFixed(1)}
                      </Typography>
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        N/A
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    {evaluation.grade_band ? (
                      <Chip label={evaluation.grade_band} size="small" color={getBandColor(evaluation.grade_band)} />
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        N/A
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Chip label={evaluation.status || 'UNKNOWN'} size="small" color={getStatusColor(evaluation.status)} />
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={1}>
                      <Button
                        size="small"
                        startIcon={<Visibility />}
                        onClick={() => handleViewDetails(evaluation)}
                        disabled={evaluation.status !== 'COMPLETED'}
                      >
                        View
                      </Button>
                      <Button
                        size="small"
                        startIcon={<Download />}
                        onClick={() => handleDownloadExcel(evaluation.evaluation_id)}
                        disabled={!evaluation.excel_s3_key || downloading}
                      >
                        Excel
                      </Button>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert 
          onClose={() => setSnackbar({ ...snackbar, open: false })} 
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>

      {/* Scorecard Dialog */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>
          Evaluation Scorecard - {selectedEval?.application_id || selectedEval?.evaluation_id}
        </DialogTitle>
        <DialogContent dividers>
          {selectedEval && (
            <Box>
              {/* Summary */}
              <Grid container spacing={2} sx={{ mb: 3 }}>
                <Grid item xs={4}>
                  <Card variant="outlined">
                    <CardContent sx={{ textAlign: 'center' }}>
                      <Typography variant="h4" color="primary">
                        {selectedEval.total_score ? parseFloat(selectedEval.total_score).toFixed(1) : 'N/A'}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Total Score
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={4}>
                  <Card variant="outlined">
                    <CardContent sx={{ textAlign: 'center' }}>
                      <Chip label={selectedEval.grade_band || 'N/A'} color={getBandColor(selectedEval.grade_band)} />
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                        Grade Band
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={4}>
                  <Card variant="outlined">
                    <CardContent sx={{ textAlign: 'center' }}>
                      {selectedEval.result_data?.scoring?.all_knockouts_pass ? (
                        <CheckCircle sx={{ fontSize: 40, color: '#4caf50' }} />
                      ) : (
                        <Cancel sx={{ fontSize: 40, color: '#f44336' }} />
                      )}
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                        Knock-outs {selectedEval.result_data?.scoring?.all_knockouts_pass ? 'PASS' : 'FAIL'}
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              </Grid>

              {/* Section Breakdown */}
              {getSections(selectedEval).length > 0 && (
                <>
                  <Typography variant="h6" gutterBottom>
                    Section-wise Breakdown
                  </Typography>
                  {getSections(selectedEval).map((section) => (
                    <Accordion key={section.key}>
                      <AccordionSummary expandIcon={<ExpandMore />}>
                        <Box sx={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                          <Typography sx={{ flexGrow: 1 }}>
                            Section {section.key}: {section.name}
                          </Typography>
                          <Chip
                            label={`${section.scored.toFixed(1)} / ${section.max.toFixed(1)}`}
                            size="small"
                            color={section.coverage >= 80 ? 'success' : section.coverage >= 60 ? 'warning' : 'error'}
                            sx={{ mr: 2 }}
                          />
                          <Typography variant="body2" color="text.secondary">
                            {section.coverage.toFixed(0)}%
                          </Typography>
                        </Box>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Typography variant="body2" color="text.secondary">
                          Section scored {section.scored.toFixed(1)} out of {section.max.toFixed(1)} applicable points
                          ({section.coverage.toFixed(1)}% coverage).
                        </Typography>
                      </AccordionDetails>
                    </Accordion>
                  ))}
                </>
              )}

              {/* Actions */}
              <Stack direction="row" spacing={2} sx={{ mt: 3, justifyContent: 'center' }}>
                <Button
                  variant="outlined"
                  startIcon={<Description />}
                  onClick={() => handleViewTranscript(selectedEval.evaluation_id)}
                  disabled={!selectedEval.transcript_s3_key}
                >
                  View Transcript
                </Button>
                <Button 
                  variant="contained" 
                  color="primary"
                  startIcon={<Download />}
                  onClick={() => handleDownloadExcel(selectedEval.evaluation_id)}
                  disabled={!selectedEval.excel_s3_key || downloading}
                >
                  {downloading ? 'Downloading...' : 'Download Excel'}
                </Button>
              </Stack>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Transcript Dialog */}
      <Dialog open={transcriptOpen} onClose={() => setTranscriptOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Call Transcript</DialogTitle>
        <DialogContent dividers>
          {transcript?.transcript && (
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', mb: 3 }}>
              {transcript.transcript}
            </Typography>
          )}
          {transcript?.segments && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Speaker Segments:
              </Typography>
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
    </Box>
  )
}
