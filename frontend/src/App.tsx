import { useState } from 'react'
import { AppBar, Box, Container, Tab, Tabs, Toolbar, Typography } from '@mui/material'
import { GraphicEqRounded } from '@mui/icons-material'
import UploadPage from './pages/UploadPage'
import HistoryPage from './pages/HistoryPage'
import { NotifierProvider } from './components/Notifier'

type View = 'new' | 'evaluations'

function App() {
  const [view, setView] = useState<View>('new')
  // "New evaluation" from the list remounts the upload flow so it starts from
  // a clean form; the tab itself just switches back to whatever was there.
  const [uploadKey, setUploadKey] = useState(0)

  const startNew = () => {
    setUploadKey((k) => k + 1)
    setView('new')
  }

  return (
    <NotifierProvider>
      <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
        <AppBar
          position="sticky"
          elevation={0}
          sx={{ bgcolor: 'background.paper', color: 'text.primary', borderBottom: 1, borderColor: 'divider' }}
        >
          <Container maxWidth="lg">
            <Toolbar disableGutters sx={{ gap: { xs: 1, sm: 3 }, minHeight: { xs: 56, sm: 64 } }}>
              <Box
                component="button"
                onClick={() => setView('new')}
                aria-label="Prodigee — home"
                sx={{ display: 'flex', alignItems: 'center', gap: 1.25, border: 0, bgcolor: 'transparent', p: 0, cursor: 'pointer', color: 'inherit' }}
              >
                <Box
                  sx={{
                    width: 32,
                    height: 32,
                    borderRadius: 2,
                    bgcolor: 'primary.main',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <GraphicEqRounded sx={{ color: '#fff', fontSize: 20 }} />
                </Box>
                <Typography sx={{ fontWeight: 800, fontSize: 17, letterSpacing: '-0.01em' }}>
                  Prodigee
                </Typography>
              </Box>

              <Box sx={{ flexGrow: 1 }} />

              <Tabs
                value={view}
                onChange={(_, v: View) => setView(v)}
                sx={{ alignSelf: 'stretch', '& .MuiTabs-flexContainer': { height: '100%' }, '& .MuiTab-root': { minHeight: '100%', px: { xs: 1.5, sm: 2 } } }}
              >
                <Tab
                  value="new"
                  label={
                    <>
                      <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>New evaluation</Box>
                      <Box component="span" sx={{ display: { xs: 'inline', sm: 'none' } }}>New</Box>
                    </>
                  }
                />
                <Tab value="evaluations" label="Evaluations" />
              </Tabs>
            </Toolbar>
          </Container>
        </AppBar>

        <Container maxWidth="lg" sx={{ py: { xs: 3, sm: 4 } }}>
          {/* Both views stay mounted so an upload in progress survives a look at the list. */}
          <Box sx={{ display: view === 'new' ? 'block' : 'none' }}>
            <UploadPage key={uploadKey} onViewAll={() => setView('evaluations')} />
          </Box>
          {view === 'evaluations' && <HistoryPage onNew={startNew} />}
        </Container>
      </Box>
    </NotifierProvider>
  )
}

export default App
