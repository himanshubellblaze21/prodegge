import { useState, useRef } from 'react'
import {
  Box,
  Paper,
  Typography,
  Button,
  Alert,
  LinearProgress,
  Card,
  Stack,
  Chip,
  alpha,
} from '@mui/material'
import { CloudUpload, AudioFile, CheckCircle, InsertDriveFile } from '@mui/icons-material'
import { uploadRecording } from '../services/api'
import StatusTracker from '../components/StatusTracker'

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [evaluationId, setEvaluationId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)

  const handleFileChange = (selectedFile: File | null) => {
    if (selectedFile) {
      // Validate audio file
      if (!selectedFile.name.match(/\.(mp3|wav|m4a|ogg|flac)$/i)) {
        setMessage({ type: 'error', text: 'Please select an audio file (MP3, WAV, M4A, OGG, FLAC)' })
        return
      }
      
      // Check file size (max 500 MB)
      if (selectedFile.size > 500 * 1024 * 1024) {
        setMessage({ type: 'error', text: 'File size must be less than 500 MB' })
        return
      }
      
      setFile(selectedFile)
      setMessage(null)
    }
  }

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileChange(e.dataTransfer.files[0])
    }
  }

  const handleSubmit = async () => {
    if (!file) {
      setMessage({ type: 'error', text: 'Please select an audio file' })
      return
    }

    setUploading(true)
    setProgress(0)
    setMessage(null)

    try {
      // Generate application ID based on file name and size for caching
      // Same file (name + size) = same application ID = cache hit
      const fileHash = `${file.name}-${file.size}`.replace(/[^a-zA-Z0-9-]/g, '_')
      const shortHash = btoa(fileHash).replace(/[^a-zA-Z0-9]/g, '').substring(0, 12).toUpperCase()
      
      // Call actual API
      const result = await uploadRecording(
        file,
        {
          applicationId: `APP-${shortHash}`,
          customerName: 'Customer', // Will be extracted from transcript
          callType: 'RCM_AUDIO_PD'
        },
        (progress) => {
          setProgress(progress)
        }
      )
      
      setProgress(100)
      
      // Check if results were cached
      if (result.cached) {
        setMessage({ 
          type: 'success', 
          text: `✓ Using cached results! This file was already processed. Evaluation ID: ${result.evaluation_id}` 
        })
      } else {
        setMessage({ 
          type: 'success', 
          text: `✓ Upload successful! Evaluation ID: ${result.evaluation_id}` 
        })
      }
      
      setEvaluationId(result.evaluation_id)
      setUploading(false)
      
    } catch (error: any) {
      setMessage({ type: 'error', text: error.message || 'Upload failed. Please try again.' })
      setUploading(false)
      setProgress(0)
    }
  }

  const handleReset = () => {
    setFile(null)
    setEvaluationId(null)
    setMessage(null)
    setProgress(0)
  }

  return (
    <Box sx={{ maxWidth: 800, mx: 'auto' }}>
      {/* Header */}
      <Box sx={{ textAlign: 'center', mb: 4 }}>
        <AudioFile sx={{ fontSize: 60, color: 'primary.main', mb: 2 }} />
        <Typography variant="h4" gutterBottom fontWeight="600">
          Upload Audio Recording
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Upload your PD verification call recording for automated AI evaluation
        </Typography>
      </Box>

      {/* Upload Card */}
      <Card 
        elevation={0}
        sx={{ 
          border: 2, 
          borderColor: dragActive ? 'primary.main' : 'divider',
          borderStyle: 'dashed',
          bgcolor: dragActive ? alpha('#1976d2', 0.05) : 'background.paper',
          transition: 'all 0.3s ease',
          '&:hover': {
            borderColor: 'primary.main',
            bgcolor: alpha('#1976d2', 0.02)
          }
        }}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <Box sx={{ p: 6, textAlign: 'center' }}>
          {!file ? (
            <>
              <CloudUpload sx={{ fontSize: 80, color: 'primary.main', mb: 2, opacity: 0.8 }} />
              <Typography variant="h6" gutterBottom>
                Drag & Drop Audio File
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                or click to browse
              </Typography>
              <Button
                variant="contained"
                size="large"
                startIcon={<InsertDriveFile />}
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                sx={{ px: 4, py: 1.5 }}
              >
                Select Audio File
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                hidden
                accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac"
                onChange={(e) => handleFileChange(e.target.files?.[0] || null)}
              />
              
              {/* Supported Formats */}
              <Stack direction="row" spacing={1} justifyContent="center" sx={{ mt: 3 }}>
                <Chip label="MP3" size="small" variant="outlined" />
                <Chip label="WAV" size="small" variant="outlined" />
                <Chip label="M4A" size="small" variant="outlined" />
                <Chip label="OGG" size="small" variant="outlined" />
                <Chip label="FLAC" size="small" variant="outlined" />
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                Max file size: 500 MB | Duration: 1-60 minutes
              </Typography>
            </>
          ) : (
            <>
              <CheckCircle sx={{ fontSize: 80, color: 'success.main', mb: 2 }} />
              <Typography variant="h6" gutterBottom>
                File Selected
              </Typography>
              <Paper 
                elevation={0} 
                sx={{ 
                  p: 2, 
                  bgcolor: alpha('#2e7d32', 0.08), 
                  display: 'inline-block',
                  mt: 2,
                  mb: 3
                }}
              >
                <Typography variant="body1" fontWeight="500">
                  {file.name}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {(file.size / 1024 / 1024).toFixed(2)} MB
                </Typography>
              </Paper>
              
              {!uploading && (
                <Box>
                  <Button
                    variant="text"
                    size="small"
                    onClick={() => setFile(null)}
                    sx={{ mr: 2 }}
                  >
                    Remove
                  </Button>
                  <Button
                    variant="text"
                    size="small"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    Choose Different File
                  </Button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    hidden
                    accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac"
                    onChange={(e) => handleFileChange(e.target.files?.[0] || null)}
                  />
                </Box>
              )}
            </>
          )}
        </Box>
      </Card>

      {/* Messages */}
      {message && (
        <Alert 
          severity={message.type} 
          onClose={() => setMessage(null)}
          sx={{ mt: 3 }}
        >
          {message.text}
        </Alert>
      )}

      {/* Progress */}
      {uploading && (
        <Paper elevation={0} sx={{ p: 3, mt: 3, bgcolor: alpha('#1976d2', 0.05) }}>
          <Typography variant="body2" gutterBottom fontWeight="500">
            Uploading... {progress}%
          </Typography>
          <LinearProgress 
            variant="determinate" 
            value={progress} 
            sx={{ height: 8, borderRadius: 4 }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Processing will start automatically after upload completes
          </Typography>
        </Paper>
      )}

      {/* Upload Button */}
      <Button
        variant="contained"
        size="large"
        fullWidth
        onClick={handleSubmit}
        disabled={uploading || !file}
        startIcon={<CloudUpload />}
        sx={{ 
          mt: 3, 
          py: 2,
          fontSize: '1.1rem',
          fontWeight: 600,
          boxShadow: 3,
          '&:hover': {
            boxShadow: 6
          }
        }}
      >
        {uploading ? 'Uploading...' : 'Upload & Start Processing'}
      </Button>

      {/* Info Box */}
      <Paper 
        elevation={0} 
        sx={{ 
          p: 3, 
          mt: 4, 
          bgcolor: alpha('#ed6c02', 0.05),
          border: 1,
          borderColor: alpha('#ed6c02', 0.2)
        }}
      >
        <Typography variant="subtitle2" gutterBottom fontWeight="600" color="warning.dark">
          📋 What happens after upload?
        </Typography>
        <Stack spacing={1} sx={{ mt: 2 }}>
          <Typography variant="body2" color="text.secondary">
            1️⃣ Audio transcription (Hindi/English supported) — ~3-5 min
          </Typography>
          <Typography variant="body2" color="text.secondary">
            2️⃣ AI evaluation of 37 criteria with evidence — ~1-2 min
          </Typography>
          <Typography variant="body2" color="text.secondary">
            3️⃣ Excel scorecard generation with timestamps — ~10 sec
          </Typography>
          <Typography variant="body2" color="text.secondary" fontWeight="500" sx={{ mt: 1 }}>
            ⏱️ Total time: 5-8 minutes | Real-time status shown below
          </Typography>
        </Stack>
      </Paper>

      {/* Status Tracker */}
      {evaluationId && (
        <>
          <StatusTracker evaluationId={evaluationId} />
          <Button
            variant="outlined"
            fullWidth
            onClick={handleReset}
            sx={{ mt: 3 }}
          >
            Upload Another File
          </Button>
        </>
      )}
    </Box>
  )
}
