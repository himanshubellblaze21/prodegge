import axios from 'axios'
import { formatTimestamp } from '../lib/format'

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT || ''

// Create axios instance
const apiClient = axios.create({
  baseURL: API_ENDPOINT,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Mock auth token for development (remove in production)
// apiClient.interceptors.request.use(async (config) => {
//   // Authentication disabled for development
//   return config
// })

// Upload recording
export async function uploadRecording(
  file: File,
  metadata: {
    applicationId: string
    customerName: string
    callType: string
  },
  onProgress?: (progress: number) => void
) {
  try {
    // Step 1: Get pre-signed URL (or cached result)
    const presignedResponse = await apiClient.post('/upload/presigned', {
      filename: file.name,
      application_id: metadata.applicationId,
      customer_name: metadata.customerName,
      call_type: metadata.callType,
    })

    const { evaluation_id, cached, upload_url } = presignedResponse.data

    // If cached, return immediately - no need to upload
    if (cached) {
      console.log('Using cached evaluation results:', evaluation_id)
      return { evaluation_id, cached: true }
    }

    // Step 2: Upload file directly to S3
    // Note: Do NOT set Content-Type header — the presigned URL is signed
    // with only 'host' in SignedHeaders. Adding extra headers causes
    // SignatureDoesNotMatch errors from S3.
    await axios.put(upload_url, file, {
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total) {
          const progress = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          )
          onProgress?.(progress)
        }
      },
    })

    // Step 3: Trigger transcription
    await apiClient.post('/upload/complete', {
      evaluation_id
    })

    return { evaluation_id, cached: false }
  } catch (error: any) {
    console.error('Upload error:', error)
    throw new Error(error.response?.data?.error || 'Upload failed')
  }
}

// Get evaluation detail
export async function getEvaluationDetail(evaluationId: string) {
  try {
    const response = await apiClient.get(`/evaluations/${evaluationId}`)
    return response.data
  } catch (error: any) {
    console.error('Get evaluation error:', error)
    throw new Error(error.response?.data?.error || 'Failed to get evaluation')
  }
}

// Get evaluations list
export async function getEvaluationsList(filters?: {
  callType?: string
  status?: string
}) {
  try {
    const params = new URLSearchParams()
    if (filters?.callType && filters.callType !== 'ALL') {
      params.append('call_type', filters.callType)
    }
    if (filters?.status && filters.status !== 'ALL') {
      params.append('status', filters.status)
    }

    const response = await apiClient.get(`/evaluations?${params.toString()}`)
    return response.data.evaluations || []
  } catch (error: any) {
    console.error('Get evaluations list error:', error)
    throw new Error(
      error.response?.data?.error || 'Failed to get evaluations list'
    )
  }
}

// Get dashboard stats
export async function getDashboardStats() {
  try {
    const response = await apiClient.get('/dashboard/stats')
    return response.data
  } catch (error: any) {
    console.error('Get dashboard stats error:', error)
    // Return mock data for development
    return {
      total_evaluations: 0,
      completed: 0,
      in_progress: 0,
      failed: 0,
      avg_score: 0,
      score_distribution: [
        { band: 'A (90-100)', count: 0 },
        { band: 'B (75-89)', count: 0 },
        { band: 'C (60-74)', count: 0 },
        { band: 'D (<60)', count: 0 },
      ],
      recent_evaluations: [],
    }
  }
}

// Submit reviewer feedback
export async function submitReviewerFeedback(
  evaluationId: string,
  feedback: {
    decision: 'ACCEPTED' | 'OVERRIDDEN'
    comments: string
    overrides?: any
  }
) {
  try {
    const response = await apiClient.post(`/evaluations/${evaluationId}/review`, feedback)
    return response.data
  } catch (error: any) {
    console.error('Submit feedback error:', error)
    throw new Error(error.response?.data?.error || 'Failed to submit feedback')
  }
}

// Get Excel download URL
export async function getExcelDownloadUrl(evaluationId: string) {
  try {
    const response = await apiClient.get(`/evaluations/${evaluationId}/excel`)
    return response.data
  } catch (error: any) {
    console.error('Get Excel download URL error:', error)
    throw new Error(error.response?.data?.error || 'Failed to get Excel download URL')
  }
}

// Start a browser download from a presigned S3 URL. The objects are served
// with Content-Disposition: attachment, so this never navigates away.
function triggerDownload(url: string, filename?: string) {
  const link = document.createElement('a')
  link.href = url
  if (filename) link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

// Download Excel scorecard
export async function downloadExcelScorecard(evaluationId: string) {
  const { download_url, filename } = await getExcelDownloadUrl(evaluationId)
  triggerDownload(download_url, filename)
  return { success: true }
}

// Download the scorecard as PDF (both sheets). The first request for an
// evaluation renders it, which takes a few seconds; later ones are cached.
export async function downloadPdfScorecard(evaluationId: string) {
  try {
    const response = await apiClient.get(`/evaluations/${evaluationId}/pdf`)
    const { download_url, filename } = response.data
    triggerDownload(download_url, filename)
    return { success: true }
  } catch (error: any) {
    console.error('Download PDF error:', error)
    throw new Error(error.response?.data?.error || 'Failed to download PDF')
  }
}

// Download the transcript as a UTF-8 text file, one line per segment.
export async function downloadTranscript(
  evaluationId: string,
  meta: { applicationId?: string; customerName?: string; callTypeLabel?: string } = {}
) {
  const data = await getTranscript(evaluationId)
  const text = buildTranscriptText(data, { evaluationId, ...meta })
  // BOM so Notepad and Excel open the Hindi text as UTF-8.
  const blob = new Blob(['\uFEFF' + text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const safeId = (meta.applicationId || evaluationId).replace(/[^A-Za-z0-9_.-]+/g, '_')
  triggerDownload(url, `Transcript_${safeId}.txt`)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return { success: true }
}

export function speakerName(speakers: Record<string, string> | undefined, id?: string) {
  if (!id) return 'Speaker'
  if (speakers?.[id]) return speakers[id]
  const n = parseInt(id.replace(/\D/g, ''), 10)
  return Number.isNaN(n) ? id : `Speaker ${n + 1}`
}

function buildTranscriptText(
  data: any,
  meta: { evaluationId: string; applicationId?: string; customerName?: string; callTypeLabel?: string }
) {
  const header = [
    'CALL TRANSCRIPT',
    meta.applicationId && `Application ID: ${meta.applicationId}`,
    meta.customerName && meta.customerName !== 'Unknown' && `Customer: ${meta.customerName}`,
    meta.callTypeLabel && `Call type: ${meta.callTypeLabel}`,
    `Evaluation ID: ${meta.evaluationId}`,
    data?.language && `Language: ${data.language}`,
  ].filter(Boolean)

  const segments: any[] = data?.segments || []
  const body = segments.length
    ? segments.map((seg) =>
        `[${formatTimestamp(seg.start)}] ${speakerName(data.speakers, seg.speaker)}: ${(seg.text || '').trim()}`)
    : [String(data?.transcript || '')]

  return [...header, '', ...body, ''].join(String.fromCharCode(13, 10))
}

// Get transcript
export async function getTranscript(evaluationId: string) {
  try {
    const response = await apiClient.get(`/evaluations/${evaluationId}/transcript`)
    return response.data
  } catch (error: any) {
    console.error('Get transcript error:', error)
    throw new Error(error.response?.data?.error || 'Failed to get transcript')
  }
}

// Poll evaluation status
export async function pollEvaluationStatus(evaluationId: string) {
  try {
    // A poll that never answers would otherwise freeze the progress screen:
    // time it out so the tracker's next poll takes over.
    const response = await apiClient.get(`/evaluations/${evaluationId}/status`, { timeout: 20000 })
    return response.data
  } catch (error: any) {
    console.error('Poll status error:', error)
    throw new Error(error.response?.data?.error || 'Failed to get status')
  }
}
