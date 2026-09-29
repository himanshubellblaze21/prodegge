import { MouseEvent, useState } from 'react'
import {
  Button,
  CircularProgress,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
} from '@mui/material'
import {
  DownloadRounded,
  TableChartOutlined,
  PictureAsPdfOutlined,
  SubjectRounded,
} from '@mui/icons-material'
import { downloadExcelScorecard, downloadPdfScorecard, downloadTranscript } from '../services/api'
import { getCallType } from '../lib/format'
import { useNotify } from './Notifier'

export interface DownloadTarget {
  evaluationId: string
  applicationId?: string
  customerName?: string
  callType?: string
  hasExcel: boolean
  hasTranscript: boolean
}

type Kind = 'excel' | 'pdf' | 'transcript'

const OPTIONS: { kind: Kind; label: string; detail: string; icon: JSX.Element }[] = [
  { kind: 'pdf', label: 'Scorecard (PDF)', detail: 'Both sheets, ready to print or share', icon: <PictureAsPdfOutlined /> },
  { kind: 'excel', label: 'Scorecard (Excel)', detail: 'Editable spreadsheet', icon: <TableChartOutlined /> },
  { kind: 'transcript', label: 'Transcript (TXT)', detail: 'Full call text with timestamps', icon: <SubjectRounded /> },
]

function useDownloads(target: DownloadTarget) {
  const notify = useNotify()
  const [busy, setBusy] = useState<Kind | null>(null)

  const available = (kind: Kind) => (kind === 'transcript' ? target.hasTranscript : target.hasExcel)

  const run = async (kind: Kind) => {
    setBusy(kind)
    try {
      if (kind === 'excel') await downloadExcelScorecard(target.evaluationId)
      if (kind === 'pdf') await downloadPdfScorecard(target.evaluationId)
      if (kind === 'transcript') {
        await downloadTranscript(target.evaluationId, {
          applicationId: target.applicationId,
          customerName: target.customerName,
          callTypeLabel: getCallType(target.callType)?.label,
        })
      }
      notify('Download started')
    } catch (e: any) {
      notify(e?.message || 'Download failed. Please try again.', 'error')
    } finally {
      setBusy(null)
    }
  }

  return { busy, available, run }
}

// Icon button + menu — used in table rows where space is tight.
export function DownloadMenu({ target }: { target: DownloadTarget }) {
  const { busy, available, run } = useDownloads(target)
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const anyAvailable = OPTIONS.some((o) => available(o.kind))

  const open = (e: MouseEvent<HTMLElement>) => {
    e.stopPropagation()
    setAnchor(e.currentTarget)
  }

  return (
    <>
      <Tooltip title={anyAvailable ? 'Download' : 'Nothing to download yet'}>
        <span>
          <IconButton size="small" onClick={open} disabled={!anyAvailable || busy !== null} aria-label="Download">
            {busy ? <CircularProgress size={18} /> : <DownloadRounded fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
      >
        {OPTIONS.map((o) => (
          <MenuItem
            key={o.kind}
            disabled={!available(o.kind)}
            onClick={() => {
              setAnchor(null)
              run(o.kind)
            }}
          >
            <ListItemIcon>{o.icon}</ListItemIcon>
            <ListItemText
              primary={o.label}
              secondary={o.detail}
              primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }}
              secondaryTypographyProps={{ fontSize: 12 }}
            />
          </MenuItem>
        ))}
      </Menu>
    </>
  )
}

// Full buttons — used on the result panel and in the details dialog.
export function DownloadButtons({ target }: { target: DownloadTarget }) {
  const { busy, available, run } = useDownloads(target)
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.25} useFlexGap flexWrap="wrap">
      {OPTIONS.map((o, i) => (
        <Button
          key={o.kind}
          variant={i === 0 ? 'contained' : 'outlined'}
          startIcon={busy === o.kind ? <CircularProgress size={16} color="inherit" /> : o.icon}
          onClick={() => run(o.kind)}
          disabled={!available(o.kind) || busy !== null}
        >
          {busy === o.kind && o.kind === 'pdf' ? 'Preparing PDF…' : o.label}
        </Button>
      ))}
    </Stack>
  )
}
