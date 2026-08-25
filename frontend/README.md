# Prodigee Audio PD Scoring - Simplified Frontend

## Overview
Simple 2-tab interface for uploading audio files and viewing evaluation results.

## Features

### Tab 1: Upload Audio
- Input Application ID and Customer Name
- Select audio file (MP3, WAV, M4A, OGG)
- Upload and automatically process
- Shows upload progress

### Tab 2: History
- Table showing all past evaluations
- View button to see detailed scorecard
- Popup dialog with:
  - Total Score
  - Grade Band (A/B/C/D)
  - Knock-out gates status
  - Section-wise breakdown (10 sections A-J)
  - Download Excel scorecard button

## Running the App

```bash
cd frontend
npm install
npm run dev
```

Open: http://localhost:3000

## Simple Structure

```
frontend/
├── src/
│   ├── pages/
│   │   ├── UploadPage.tsx    # Upload form
│   │   └── HistoryPage.tsx   # History table + scorecard viewer
│   ├── services/
│   │   └── api.ts            # Backend API calls
│   ├── App.tsx               # Main app with 2 tabs
│   └── main.tsx              # Entry point
└── package.json
```

## Next Steps

1. Connect to backend API (replace mock data)
2. Add actual file upload to S3
3. Fetch real evaluation data from DynamoDB
4. Add Excel download functionality

## No Authentication
Authentication is removed for simplicity. Add back later if needed.
