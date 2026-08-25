import { useState } from 'react'
import {
  Container,
  AppBar,
  Toolbar,
  Typography,
  Box,
  Tabs,
  Tab,
} from '@mui/material'
import UploadPage from './pages/UploadPage'
import HistoryPage from './pages/HistoryPage'

function App() {
  const [currentTab, setCurrentTab] = useState(0)

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: '#f5f5f5' }}>
      {/* Header */}
      <AppBar position="static">
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            Prodigee Finance - Audio PD Scoring
          </Typography>
        </Toolbar>
      </AppBar>

      {/* Tabs */}
      <Container maxWidth="lg" sx={{ mt: 3 }}>
        <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 3 }}>
          <Tabs value={currentTab} onChange={(e, val) => setCurrentTab(val)}>
            <Tab label="Upload Audio" />
            <Tab label="History" />
          </Tabs>
        </Box>

        {/* Content */}
        {currentTab === 0 && <UploadPage />}
        {currentTab === 1 && <HistoryPage />}
      </Container>
    </Box>
  )
}

export default App
