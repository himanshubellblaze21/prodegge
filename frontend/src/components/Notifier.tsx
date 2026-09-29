import { createContext, ReactNode, useCallback, useContext, useState } from 'react'
import { Alert, Snackbar } from '@mui/material'

type Severity = 'success' | 'error' | 'info'
type Notify = (message: string, severity?: Severity) => void

const NotifyContext = createContext<Notify>(() => {})

// One toast for the whole app, so every download and error reports the same way.
export function NotifierProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ open: boolean; message: string; severity: Severity }>({
    open: false,
    message: '',
    severity: 'success',
  })

  const notify = useCallback<Notify>((message, severity = 'success') => {
    setState({ open: true, message, severity })
  }, [])

  const close = () => setState((s) => ({ ...s, open: false }))

  return (
    <NotifyContext.Provider value={notify}>
      {children}
      <Snackbar
        open={state.open}
        autoHideDuration={state.severity === 'error' ? 7000 : 4000}
        onClose={(_, reason) => reason !== 'clickaway' && close()}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert onClose={close} severity={state.severity} variant="filled" sx={{ minWidth: 280 }}>
          {state.message}
        </Alert>
      </Snackbar>
    </NotifyContext.Provider>
  )
}

export function useNotify() {
  return useContext(NotifyContext)
}
