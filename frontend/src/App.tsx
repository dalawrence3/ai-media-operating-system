import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import '@/design/tokens.css'
import '@/design/base.css'
import '@/design/components.css'

import { AuthProvider, useAuth } from '@/auth/AuthContext'
import { AppShell } from '@/components/layout/AppShell'
import { WorkspaceSelect } from '@/pages/WorkspaceSelect'
import { LoginPage } from '@/pages/LoginPage'
import { Dashboard } from '@/pages/Dashboard'
import { Channels } from '@/pages/Channels'
import { Pipelines } from '@/pages/Pipelines'
import { Reviews } from '@/pages/Reviews'
import { Exceptions } from '@/pages/Exceptions'
import { Operations } from '@/pages/Operations'
import { Analytics } from '@/pages/Analytics'
import { Learning } from '@/pages/Learning'
import { Experiments } from '@/pages/Experiments'
import { Workflows } from '@/pages/Workflows'
import { Health } from '@/pages/Health'
import { Audit } from '@/pages/Audit'
import { Settings } from '@/pages/Settings'
import { Publishing } from '@/pages/Publishing'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
})

// ── Protected route guard ────────────────────────────────────────────────────

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary, #9ca3af)',
          fontSize: '0.875rem',
        }}
      >
        Loading…
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// ── Workspace routes ─────────────────────────────────────────────────────────

function WorkspaceRoutes() {
  return (
    <AppShell>
      <Routes>
        <Route path="dashboard"   element={<Dashboard />} />
        <Route path="channels"    element={<Channels />} />
        <Route path="pipelines"   element={<Pipelines />} />
        <Route path="reviews"     element={<Reviews />} />
        <Route path="exceptions"  element={<Exceptions />} />
        <Route path="operations"  element={<Operations />} />
        <Route path="analytics"   element={<Analytics />} />
        <Route path="learning"    element={<Learning />} />
        <Route path="experiments" element={<Experiments />} />
        <Route path="workflows"   element={<Workflows />} />
        <Route path="publishing"  element={<Publishing />} />
        <Route path="health"      element={<Health />} />
        <Route path="audit"       element={<Audit />} />
        <Route path="settings"    element={<Settings />} />
        <Route index element={<Navigate to="dashboard" replace />} />
      </Routes>
    </AppShell>
  )
}

// ── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <RequireAuth>
                  <WorkspaceSelect />
                </RequireAuth>
              }
            />
            <Route
              path="/workspaces/:workspaceId/*"
              element={
                <RequireAuth>
                  <WorkspaceRoutes />
                </RequireAuth>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
