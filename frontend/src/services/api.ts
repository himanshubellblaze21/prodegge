import axios from 'axios'

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

// Download Excel scorecard
export async function downloadExcelScorecard(evaluationId: string) {
  try {
    const { download_url, filename } = await getExcelDownloadUrl(evaluationId)
    
    // Open in new tab or trigger download
    const link = document.createElement('a')
    link.href = download_url
    link.download = filename
    link.target = '_blank'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    
    return { success: true }
  } catch (error: any) {
    console.error('Download Excel error:', error)
    throw new Error(error.response?.data?.error || 'Failed to download Excel')
  }
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
    const response = await apiClient.get(`/evaluations/${evaluationId}/status`)
    return response.data
  } catch (error: any) {
    console.error('Poll status error:', error)
    throw new Error(error.response?.data?.error || 'Failed to get status')
  }
}
