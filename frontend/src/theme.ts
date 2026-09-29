import { createTheme, alpha } from '@mui/material/styles'

// ─── Brand tokens ───────────────────────────────────────────────────────────
// Deep navy primary, close to the scorecard's own header colour, so the app
// and the Excel/PDF it produces read as one product.
const BRAND = {
  primary: '#1e3a8a',
  primaryLight: '#3b5bb5',
  primaryDark: '#172c69',
  success: '#15803d',
  warning: '#d97706',
  error: '#dc2626',
  bg: '#f6f7f9',
  paper: '#ffffff',
  ink: '#0f172a',
  muted: '#5b6474',
}

const border = alpha(BRAND.ink, 0.1)

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: BRAND.primary,
      light: BRAND.primaryLight,
      dark: BRAND.primaryDark,
      contrastText: '#ffffff',
    },
    secondary: { main: BRAND.primaryLight },
    success: { main: BRAND.success },
    warning: { main: BRAND.warning },
    error: { main: BRAND.error },
    background: { default: BRAND.bg, paper: BRAND.paper },
    divider: border,
    text: { primary: BRAND.ink, secondary: BRAND.muted },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: ['Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'].join(','),
    h5: { fontWeight: 700, fontSize: '1.5rem', letterSpacing: '-0.015em' },
    h6: { fontWeight: 700, fontSize: '1.125rem', letterSpacing: '-0.01em' },
    subtitle1: { fontWeight: 700, fontSize: '1rem' },
    subtitle2: { fontWeight: 700 },
    body2: { fontSize: '0.875rem' },
    button: { fontWeight: 600, textTransform: 'none', letterSpacing: 0 },
  },
  shadows: Object.assign([], new Array(25).fill('none'), {
    1: '0 1px 2px rgba(15,23,42,0.05)',
    2: '0 2px 6px rgba(15,23,42,0.06)',
    3: '0 4px 12px rgba(15,23,42,0.08)',
    4: '0 8px 24px rgba(15,23,42,0.10)',
    8: '0 10px 30px rgba(15,23,42,0.12)',
    24: '0 24px 60px rgba(15,23,42,0.18)',
  }) as any,
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { fontFeatureSettings: '"cv02","cv03","cv04","cv11"', WebkitFontSmoothing: 'antialiased' },
        '::-webkit-scrollbar': { width: 10, height: 10 },
        '::-webkit-scrollbar-thumb': {
          backgroundColor: alpha(BRAND.ink, 0.18),
          borderRadius: 10,
          border: '2px solid transparent',
          backgroundClip: 'padding-box',
        },
        '::-webkit-scrollbar-track': { backgroundColor: 'transparent' },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 8, paddingInline: 16, minHeight: 38 },
        sizeLarge: { minHeight: 46, fontSize: '0.95rem' },
        outlined: { borderColor: alpha(BRAND.ink, 0.18), '&:hover': { borderColor: alpha(BRAND.ink, 0.32) } },
      },
    },
    MuiIconButton: {
      styleOverrides: { root: { borderRadius: 8 } },
    },
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: 'none' },
        rounded: { borderRadius: 12 },
        outlined: { borderColor: border },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600, borderRadius: 6 },
        sizeSmall: { height: 24, fontSize: '0.75rem' },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: border },
        head: {
          fontWeight: 600,
          fontSize: '0.75rem',
          color: BRAND.muted,
          backgroundColor: '#f9fafb',
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          paddingTop: 10,
          paddingBottom: 10,
        },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: { '&.MuiTableRow-hover:hover': { backgroundColor: alpha(BRAND.primary, 0.03) } },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          '& .MuiOutlinedInput-notchedOutline': { borderColor: alpha(BRAND.ink, 0.18) },
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: 'none', fontWeight: 600, fontSize: '0.9rem', minWidth: 0 },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: { height: 3, borderRadius: '3px 3px 0 0' },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          borderColor: alpha(BRAND.ink, 0.18),
          color: BRAND.muted,
          '&.Mui-selected': {
            color: BRAND.primary,
            backgroundColor: alpha(BRAND.primary, 0.08),
            '&:hover': { backgroundColor: alpha(BRAND.primary, 0.12) },
          },
        },
      },
    },
    MuiDialog: {
      styleOverrides: { paper: { borderRadius: 14 }, paperFullScreen: { borderRadius: 0 } },
    },
    MuiDialogTitle: {
      styleOverrides: { root: { padding: '20px 24px 16px' } },
    },
    MuiAlert: {
      styleOverrides: { root: { borderRadius: 10 } },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: { fontSize: '0.75rem', backgroundColor: '#1e293b', padding: '6px 10px' },
      },
    },
    MuiMenuItem: {
      styleOverrides: { root: { borderRadius: 6, marginInline: 4 } },
    },
  },
})

export default theme
