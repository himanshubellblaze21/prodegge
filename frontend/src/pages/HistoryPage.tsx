import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import {
  RefreshRounded,
  SearchRounded,
  CloseRounded,
  WarningAmberRounded,
  AddRounded,
  ChevronRightRounded,
} from '@mui/icons-material'
import { getEvaluationsList } from '../services/api'
import { CallTypeBadge, OutcomeBadge, ScoreBar, ScorecardBadge } from '../components/Badges'
import { DownloadMenu, DownloadTarget } from '../components/DownloadActions'
import EvaluationSummary from '../components/EvaluationSummary'
import {
  displayName,
  formatDate,
  getOutcome,
  getStatus,
  isInProgress,
  toBool,
  scorecardLabel,
  scorecardVersion,
  toNumber,
} from '../lib/format'

type TypeFilter = 'ALL' | 'BCM' | 'BM' | 'RCM'
type OutcomeFilter = 'ALL' | 'accepted' | 'not_accepted' | 'not_evaluable' | 'in_progress' | 'failed'

const OUTCOME_FILTERS: { value: OutcomeFilter; label: string }[] = [
  { value: 'ALL', label: 'All results' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'not_accepted', label: 'Not accepted' },
  { value: 'not_evaluable', label: 'Not evaluable' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'failed', label: 'Failed' },
]

function targetFor(ev: any): DownloadTarget {
  return {
    evaluationId: ev.evaluation_id,
    applicationId: ev.application_id,
    customerName: ev.customer_name,
    callType: ev.call_type,
    hasExcel: Boolean(ev.excel_s3_key),
    hasTranscript: Boolean(ev.transcript_s3_key),
  }
}

function matchesOutcome(ev: any, filter: OutcomeFilter) {
  if (filter === 'ALL') return true
  const tone = getStatus(ev.status).tone
  if (filter === 'in_progress') return tone === 'active'
  if (filter === 'failed') return tone === 'failed'
  return tone === 'done' && getOutcome(ev).key === filter
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 2.25, flex: 1, minWidth: 0 }}>
      <Typography variant="body2" color="text.secondary" noWrap>
        {label}
      </Typography>
      <Typography sx={{ fontSize: 26, fontWeight: 800, mt: 0.5, color: tone || 'text.primary', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </Typography>
    </Paper>
  )
}

function ReviewFlag({ ev }: { ev: any }) {
  if (!toBool(ev.needs_review) || getOutcome(ev).key === 'not_evaluable') return null
  const reasons: string[] = Array.isArray(ev.review_reasons) ? ev.review_reasons : []
  return (
    <Tooltip title={`Check before relying on this result${reasons.length ? ': ' + reasons.join(' · ') : ''}`}>
      <WarningAmberRounded sx={{ fontSize: 18, color: 'warning.main' }} aria-label="Needs review" />
    </Tooltip>
  )
}

export default function HistoryPage({ onNew }: { onNew?: () => void }) {
  const theme = useTheme()
  const compact = useMediaQuery(theme.breakpoints.down('md'))
  const [evaluations, setEvaluations] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('ALL')
  const [versionFilter, setVersionFilter] = useState('ALL')
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>('ALL')
  const [page, setPage] = useState(0)
  const [rowsPerPage, setRowsPerPage] = useState(25)
  const [selected, setSelected] = useState<any>(null)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      setEvaluations(await getEvaluationsList())
    } catch (e: any) {
      setError(e.message || 'Could not load evaluations')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Keep in-progress rows fresh without the user reaching for Refresh.
  const anyActive = evaluations.some((e) => isInProgress(e.status))
  useEffect(() => {
    if (!anyActive) return
    const t = setInterval(() => load(true), 20000)
    return () => clearInterval(t)
  }, [anyActive, load])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return evaluations.filter((ev) => {
      if (typeFilter !== 'ALL' && !(ev.call_type || '').startsWith(typeFilter)) return false
      if (versionFilter !== 'ALL' && scorecardVersion(ev) !== versionFilter) return false
      if (!matchesOutcome(ev, outcomeFilter)) return false
      if (!q) return true
      return [ev.application_id, ev.customer_name, ev.evaluation_id]
        .some((v) => String(v || '').toLowerCase().includes(q))
    })
  }, [evaluations, query, typeFilter, outcomeFilter, versionFilter])

  useEffect(() => setPage(0), [query, typeFilter, outcomeFilter, versionFilter])

  // Offer only the versions that actually occur, newest first.
  const versions = useMemo(
    () => Array.from(new Set(evaluations.map(scorecardVersion))).sort((a, b) => Number(b) - Number(a)),
    [evaluations]
  )

  const stats = useMemo(() => {
    const done = evaluations.filter((e) => getStatus(e.status).tone === 'done')
    const scored = done.filter((e) => getOutcome(e).key !== 'not_evaluable' && toNumber(e.total_score) !== null)
    const avg = scored.length
      ? Math.round(scored.reduce((s, e) => s + (toNumber(e.total_score) || 0), 0) / scored.length)
      : null
    return {
      total: evaluations.length,
      accepted: done.filter((e) => getOutcome(e).key === 'accepted').length,
      notAccepted: done.filter((e) => getOutcome(e).key === 'not_accepted').length,
      avg,
    }
  }, [evaluations])

  const pageRows = filtered.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
  const filtersActive = Boolean(query) || typeFilter !== 'ALL' || outcomeFilter !== 'ALL' || versionFilter !== 'ALL'
  const clearFilters = () => {
    setQuery('')
    setTypeFilter('ALL')
    setOutcomeFilter('ALL')
    setVersionFilter('ALL')
  }
  const open = (ev: any) => getStatus(ev.status).tone === 'done' && setSelected(ev)

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
        <Typography variant="h5">Evaluations</Typography>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Refresh">
            <span>
              <IconButton onClick={() => load()} disabled={loading} aria-label="Refresh">
                <RefreshRounded />
              </IconButton>
            </span>
          </Tooltip>
          {onNew && (
            <Button variant="contained" startIcon={<AddRounded />} onClick={onNew}>
              New evaluation
            </Button>
          )}
        </Stack>
      </Stack>

      {/* Summary */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 1.5, mb: 3 }}>
        {loading && !evaluations.length ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={88} />)
        ) : (
          <>
            <Stat label="Evaluations" value={stats.total} />
            <Stat label="Accepted" value={stats.accepted} tone="#15803d" />
            <Stat label="Not accepted" value={stats.notAccepted} tone="#b91c1c" />
            <Stat label="Average score" value={stats.avg ?? '—'} />
          </>
        )}
      </Box>

      {/* Filters */}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ mb: 2 }} alignItems={{ md: 'center' }}>
        <TextField
          size="small"
          placeholder="Search by application, customer or ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          sx={{ flex: 1, minWidth: 0, bgcolor: 'background.paper' }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchRounded fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: query ? (
              <InputAdornment position="end">
                <IconButton size="small" onClick={() => setQuery('')} aria-label="Clear search">
                  <CloseRounded fontSize="small" />
                </IconButton>
              </InputAdornment>
            ) : undefined,
          }}
        />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={typeFilter}
          onChange={(_, v) => v && setTypeFilter(v)}
          aria-label="Call type"
          sx={{ bgcolor: 'background.paper', '& .MuiToggleButton-root': { px: 1.75, textTransform: 'none', fontWeight: 600 } }}
        >
          <ToggleButton value="ALL">All types</ToggleButton>
          <ToggleButton value="BCM">BCM</ToggleButton>
          <ToggleButton value="BM">BM FI</ToggleButton>
          <ToggleButton value="RCM">RCM</ToggleButton>
        </ToggleButtonGroup>
        <TextField
          select
          size="small"
          value={outcomeFilter}
          onChange={(e) => setOutcomeFilter(e.target.value as OutcomeFilter)}
          sx={{ minWidth: 170, bgcolor: 'background.paper' }}
          aria-label="Result"
        >
          {OUTCOME_FILTERS.map((o) => (
            <MenuItem key={o.value} value={o.value}>
              {o.label}
            </MenuItem>
          ))}
        </TextField>
        {versions.length > 1 && (
          <TextField
            select
            size="small"
            value={versionFilter}
            onChange={(e) => setVersionFilter(e.target.value)}
            sx={{ minWidth: 150, bgcolor: 'background.paper' }}
            aria-label="Scorecard version"
          >
            <MenuItem value="ALL">All scorecards</MenuItem>
            {versions.map((v) => (
              <MenuItem key={v} value={v}>
                {v === '0' ? 'Legacy scorecard' : `Scorecard ${scorecardLabel(v)}`}
              </MenuItem>
            ))}
          </TextField>
        )}
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} action={<Button color="inherit" onClick={() => load()}>Retry</Button>}>
          {error}
        </Alert>
      )}

      {/* Loading */}
      {loading && !evaluations.length && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={52} />)}
        </Paper>
      )}

      {/* Empty */}
      {!loading && !error && filtered.length === 0 && (
        <Paper variant="outlined" sx={{ py: 8, px: 3, textAlign: 'center' }}>
          <Typography variant="subtitle1" gutterBottom>
            {filtersActive ? 'No evaluations match these filters' : 'No evaluations yet'}
          </Typography>
          {filtersActive ? (
            <Button onClick={clearFilters} sx={{ mt: 1 }}>Clear filters</Button>
          ) : (
            onNew && <Button variant="contained" onClick={onNew} sx={{ mt: 1 }}>Start the first evaluation</Button>
          )}
        </Paper>
      )}

      {/* List */}
      {filtered.length > 0 && (
        <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
          {compact ? (
            <Stack divider={<Box sx={{ borderTop: 1, borderColor: 'divider' }} />}>
              {pageRows.map((ev) => {
                const clickable = getStatus(ev.status).tone === 'done'
                return (
                  <Stack
                    key={`${ev.evaluation_id}|${ev.created_at}`}
                    direction="row"
                    spacing={1.5}
                    alignItems="center"
                    onClick={() => open(ev)}
                    sx={{ p: 2, cursor: clickable ? 'pointer' : 'default', '&:active': clickable ? { bgcolor: 'action.hover' } : undefined }}
                  >
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="body2" fontWeight={700} noWrap>{ev.application_id || ev.evaluation_id}</Typography>
                        <ReviewFlag ev={ev} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary" noWrap component="div">
                        {[displayName(ev.customer_name), formatDate(ev.created_at)].filter(Boolean).join(' · ')}
                      </Typography>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                        <CallTypeBadge callType={ev.call_type} />
                        <ScorecardBadge ev={ev} />
                        <OutcomeBadge ev={ev} />
                        {getOutcome(ev).key !== 'not_evaluable' && getStatus(ev.status).tone === 'done' && <ScoreBar score={ev.total_score} width={48} />}
                      </Stack>
                    </Box>
                    <DownloadMenu target={targetFor(ev)} />
                    {clickable && <ChevronRightRounded sx={{ color: 'text.disabled' }} />}
                  </Stack>
                )
              })}
            </Stack>
          ) : (
            <TableContainer>
              <Table size="medium">
                <TableHead>
                  <TableRow>
                    <TableCell>Application</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell>Scorecard</TableCell>
                    <TableCell>Date</TableCell>
                    <TableCell>Score</TableCell>
                    <TableCell>Result</TableCell>
                    <TableCell align="right" sx={{ width: 110 }}>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pageRows.map((ev) => {
                    const done = getStatus(ev.status).tone === 'done'
                    return (
                      <TableRow
                        key={`${ev.evaluation_id}|${ev.created_at}`}
                        hover={done}
                        onClick={() => open(ev)}
                        sx={{ cursor: done ? 'pointer' : 'default', '&:last-child td': { borderBottom: 0 } }}
                      >
                        <TableCell sx={{ maxWidth: 280 }}>
                          <Stack direction="row" spacing={1} alignItems="center">
                            <Typography variant="body2" fontWeight={600} noWrap title={ev.application_id}>
                              {ev.application_id || '—'}
                            </Typography>
                            <ReviewFlag ev={ev} />
                          </Stack>
                          <Typography variant="caption" color="text.secondary" noWrap component="div">
                            {displayName(ev.customer_name) || ev.evaluation_id}
                          </Typography>
                        </TableCell>
                        <TableCell><CallTypeBadge callType={ev.call_type} /></TableCell>
                        <TableCell><ScorecardBadge ev={ev} /></TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          <Typography variant="body2">{formatDate(ev.created_at)}</Typography>
                        </TableCell>
                        <TableCell>
                          {done && getOutcome(ev).key !== 'not_evaluable'
                            ? <ScoreBar score={ev.total_score} />
                            : <Typography variant="body2" color="text.disabled">—</Typography>}
                        </TableCell>
                        <TableCell><OutcomeBadge ev={ev} /></TableCell>
                        <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                          <Stack direction="row" spacing={0.5} justifyContent="flex-end" alignItems="center">
                            <DownloadMenu target={targetFor(ev)} />
                            <Tooltip title={done ? 'Open' : 'Not ready yet'}>
                              <span>
                                <IconButton size="small" onClick={() => open(ev)} disabled={!done} aria-label="Open">
                                  <ChevronRightRounded fontSize="small" />
                                </IconButton>
                              </span>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          <TablePagination
            component="div"
            count={filtered.length}
            page={page}
            onPageChange={(_, p) => setPage(p)}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={(e) => {
              setRowsPerPage(parseInt(e.target.value, 10))
              setPage(0)
            }}
            rowsPerPageOptions={[10, 25, 50, 100]}
            labelRowsPerPage={compact ? 'Rows' : 'Rows per page'}
            sx={{ borderTop: 1, borderColor: 'divider' }}
          />
        </Paper>
      )}

      {/* Details */}
      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        maxWidth="md"
        fullWidth
        fullScreen={compact}
      >
        {selected && (
          <>
            <DialogTitle sx={{ pr: 7 }}>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                <Typography variant="h6" component="span" noWrap>
                  {selected.application_id || selected.evaluation_id}
                </Typography>
                <CallTypeBadge callType={selected.call_type} />
                <ScorecardBadge ev={selected} />
              </Stack>
              <Typography variant="body2" color="text.secondary" component="div" noWrap>
                {[displayName(selected.customer_name), formatDate(selected.created_at)].filter(Boolean).join(' · ')}
              </Typography>
              <IconButton onClick={() => setSelected(null)} aria-label="Close" sx={{ position: 'absolute', right: 12, top: 12 }}>
                <CloseRounded />
              </IconButton>
            </DialogTitle>
            <DialogContent dividers sx={{ p: { xs: 2.5, sm: 3 } }}>
              <EvaluationSummary evaluationId={selected.evaluation_id} base={selected} />
            </DialogContent>
          </>
        )}
      </Dialog>
    </Box>
  )
}
