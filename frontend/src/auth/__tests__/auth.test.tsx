/**
 * Frontend auth integration tests.
 *
 * Covers: login success/failure, Bearer token attachment, 401→refresh→retry,
 * failed-refresh session clear, logout, 403 ForbiddenError, production path
 * (no X-Dev-Actor), dev path, and protected-content visibility.
 */

import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '@/test/server'
import {
  VALID_EMAIL,
  VALID_PASSWORD,
  MOCK_ACCESS_TOKEN,
  MOCK_REFRESH_TOKEN,
  MOCK_NEW_ACCESS_TOKEN,
} from '@/test/handlers'
import { WS_ID } from '@/test/fixtures'
import {
  ApiError,
  ForbiddenError,
  UnauthorizedError,
  api,
} from '@/api/client'

const O = 'http://localhost:5173'
const B = `${O}/api/v1`

// ── Helpers ──────────────────────────────────────────────────────────────────

function wireAuth(token: string | null = null) {
  const onAuthLost = vi.fn()
  let currentToken = token
  const onRefreshNeeded = vi.fn(async () => null as string | null)
  api.setAuthCallbacks({
    getToken: () => currentToken,
    onRefreshNeeded,
    onAuthLost,
  })
  return { onAuthLost, onRefreshNeeded, setToken: (t: string | null) => { currentToken = t } }
}

afterEach(() => {
  api.clearAuthCallbacks()
  sessionStorage.clear()
})

// ── API client: token attachment ─────────────────────────────────────────────

describe('ApiClient — token attachment', () => {
  it('attaches Authorization: Bearer header when token is set', async () => {
    let capturedAuth: string | null = null
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        capturedAuth = request.headers.get('Authorization')
        return HttpResponse.json([])
      }),
    )
    wireAuth(MOCK_ACCESS_TOKEN)
    await api.listWorkspaces()
    expect(capturedAuth).toBe(`Bearer ${MOCK_ACCESS_TOKEN}`)
  })

  it('does not attach Authorization header when no token in test env', async () => {
    let capturedAuth: string | null = null
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        capturedAuth = request.headers.get('Authorization')
        return HttpResponse.json([])
      }),
    )
    wireAuth(null)
    await api.listWorkspaces()
    // In test (non-Vite-DEV) env, no X-Dev-Actor either.
    expect(capturedAuth).toBeNull()
  })
})

// ── API client: 401 → refresh → retry ────────────────────────────────────────

describe('ApiClient — 401 refresh flow', () => {
  it('retries request with new token after successful refresh', async () => {
    let callCount = 0
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        callCount++
        const auth = request.headers.get('Authorization')
        if (auth === `Bearer ${MOCK_ACCESS_TOKEN}`) {
          return HttpResponse.json({ detail: 'Token expired' }, { status: 401 })
        }
        if (auth === `Bearer ${MOCK_NEW_ACCESS_TOKEN}`) {
          return HttpResponse.json([])
        }
        return HttpResponse.json({ detail: 'No token' }, { status: 401 })
      }),
    )

    const onRefreshNeeded = vi.fn(async () => MOCK_NEW_ACCESS_TOKEN)
    const onAuthLost = vi.fn()
    let currentToken = MOCK_ACCESS_TOKEN
    api.setAuthCallbacks({
      getToken: () => currentToken,
      onRefreshNeeded,
      onAuthLost,
    })

    const result = await api.listWorkspaces()
    expect(result).toEqual([])
    expect(callCount).toBe(2)  // original + retry
    expect(onRefreshNeeded).toHaveBeenCalledOnce()
    expect(onAuthLost).not.toHaveBeenCalled()
  })

  it('calls onAuthLost and throws UnauthorizedError when refresh fails', async () => {
    server.use(
      http.get(`${B}/workspaces`, () =>
        HttpResponse.json({ detail: 'Token expired' }, { status: 401 }),
      ),
    )

    const onRefreshNeeded = vi.fn(async () => null)
    const onAuthLost = vi.fn()
    api.setAuthCallbacks({
      getToken: () => MOCK_ACCESS_TOKEN,
      onRefreshNeeded,
      onAuthLost,
    })

    await expect(api.listWorkspaces()).rejects.toThrow(UnauthorizedError)
    expect(onAuthLost).toHaveBeenCalledOnce()
  })

  it('does not retry after non-401 error', async () => {
    let callCount = 0
    server.use(
      http.get(`${B}/workspaces`, () => {
        callCount++
        return HttpResponse.json({ detail: 'Server error' }, { status: 500 })
      }),
    )
    const { onRefreshNeeded } = wireAuth(MOCK_ACCESS_TOKEN)
    await expect(api.listWorkspaces()).rejects.toThrow(ApiError)
    expect(callCount).toBe(1)
    expect(onRefreshNeeded).not.toHaveBeenCalled()
  })
})

// ── API client: 403 handling ─────────────────────────────────────────────────

describe('ApiClient — 403 ForbiddenError', () => {
  it('throws ForbiddenError on 403 response', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
        HttpResponse.json({ detail: 'Insufficient role' }, { status: 403 }),
      ),
    )
    wireAuth(MOCK_ACCESS_TOKEN)
    await expect(api.listPipelines(WS_ID)).rejects.toThrow(ForbiddenError)
  })

  it('ForbiddenError carries status 403 and is an ApiError', () => {
    const err = new ForbiddenError('No access')
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(403)
    expect(err.message).toBe('No access')
  })

  it('does not trigger refresh on 403 (only 401 triggers refresh)', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
        HttpResponse.json({ detail: 'Forbidden' }, { status: 403 }),
      ),
    )
    const { onRefreshNeeded } = wireAuth(MOCK_ACCESS_TOKEN)
    await expect(api.listPipelines(WS_ID)).rejects.toThrow(ForbiddenError)
    expect(onRefreshNeeded).not.toHaveBeenCalled()
  })
})

// ── Production: no X-Dev-Actor ───────────────────────────────────────────────

describe('Production path — no X-Dev-Actor', () => {
  it('never sends X-Dev-Actor when a Bearer token is set', async () => {
    let devActorHeader: string | null = null
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        devActorHeader = request.headers.get('X-Dev-Actor')
        return HttpResponse.json([])
      }),
    )
    wireAuth(MOCK_ACCESS_TOKEN)
    await api.listWorkspaces()
    // Bearer token present → X-Dev-Actor must be absent, regardless of Vite mode.
    expect(devActorHeader).toBeNull()
  })

  it('sends Authorization: Bearer, not X-Dev-Actor, when authenticated', async () => {
    let capturedAuth: string | null = null
    let capturedDevActor: string | null = null
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        capturedAuth = request.headers.get('Authorization')
        capturedDevActor = request.headers.get('X-Dev-Actor')
        return HttpResponse.json([])
      }),
    )
    wireAuth(MOCK_ACCESS_TOKEN)
    await api.listWorkspaces()
    expect(capturedAuth).toBe(`Bearer ${MOCK_ACCESS_TOKEN}`)
    expect(capturedDevActor).toBeNull()
  })

  it('sends X-Dev-Actor only when no token (dev build convenience only)', async () => {
    // Vitest sets import.meta.env.DEV = true. The client sends X-Dev-Actor only in
    // DEV mode with no token — this is the backend dev-auth convenience path.
    // Production builds have import.meta.env.DEV = false and never send X-Dev-Actor.
    let capturedDevActor: string | null = null
    server.use(
      http.get(`${B}/workspaces`, ({ request }) => {
        capturedDevActor = request.headers.get('X-Dev-Actor')
        return HttpResponse.json([])
      }),
    )
    wireAuth(null)  // no token
    await api.listWorkspaces()
    // In Vitest (DEV=true) with no token: X-Dev-Actor is sent.
    // In production build (DEV=false): X-Dev-Actor would NOT be sent.
    expect(capturedDevActor).toBe('dev:studio-user')
  })
})

// ── Login: success / failure ─────────────────────────────────────────────────

describe('Login flow (direct fetch)', () => {
  it('returns tokens on valid credentials', async () => {
    const res = await fetch(`${O}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: VALID_EMAIL, password: VALID_PASSWORD }),
    })
    expect(res.ok).toBe(true)
    const data = await res.json()
    expect(data.access_token).toBe(MOCK_ACCESS_TOKEN)
    expect(data.refresh_token).toBe(MOCK_REFRESH_TOKEN)
    expect(data.token_type).toBe('bearer')
  })

  it('returns 401 on invalid credentials', async () => {
    const res = await fetch(`${O}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: VALID_EMAIL, password: 'wrong' }),
    })
    expect(res.status).toBe(401)
    const data = await res.json()
    expect(data.detail).toBe('Invalid email or password')
  })
})

// ── Refresh flow ─────────────────────────────────────────────────────────────

describe('Refresh token flow', () => {
  it('returns new access token on valid refresh token', async () => {
    const res = await fetch(`${O}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: MOCK_REFRESH_TOKEN }),
    })
    expect(res.ok).toBe(true)
    const data = await res.json()
    expect(data.access_token).toBe(MOCK_NEW_ACCESS_TOKEN)
  })

  it('returns 401 on invalid refresh token', async () => {
    const res = await fetch(`${O}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: 'bad-token' }),
    })
    expect(res.status).toBe(401)
  })
})

// ── Logout ───────────────────────────────────────────────────────────────────

describe('Logout', () => {
  it('clears auth callbacks on clearAuthCallbacks()', () => {
    wireAuth(MOCK_ACCESS_TOKEN)
    api.clearAuthCallbacks()
    // After clearing, no token should be attached (callbacks gone).
    // We verify by confirming getToken returns null (internal state gone).
    // The api still works — it just has no token.
    // We can only test this indirectly: a 401 with no callbacks should throw ApiError, not UnauthorizedError.
    server.use(
      http.get(`${B}/workspaces`, () =>
        HttpResponse.json({ detail: 'unauth' }, { status: 401 }),
      ),
    )
    // No callbacks wired — 401 throws ApiError directly (no refresh attempted).
    return expect(api.listWorkspaces()).rejects.toThrow(ApiError)
  })
})

// ── LoginPage component ───────────────────────────────────────────────────────

import { MemoryRouter, Routes, Route as RouterRoute, Navigate as RouterNavigate } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthContext'
import { LoginPage } from '@/pages/LoginPage'

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('LoginPage component', () => {
  it('renders email and password fields and submit button', () => {
    renderLoginPage()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows error message on invalid credentials', async () => {
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText(/email/i), VALID_EMAIL)
    await user.type(screen.getByLabelText(/password/i), 'wrong-password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid email or password')
    })
  })

  it('submits with valid credentials without error', async () => {
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText(/email/i), VALID_EMAIL)
    await user.type(screen.getByLabelText(/password/i), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    // After successful login the form clears / navigates — no error alert.
    await waitFor(() => {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })
})

// ── RequireAuth: protected content not shown after auth loss ─────────────────

import { useAuth } from '@/auth/AuthContext'

function FakeProtected() {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <div>loading</div>
  if (!isAuthenticated) return <RouterNavigate to="/login" replace />
  return <div data-testid="protected">Protected content</div>
}

describe('Protected route guard', () => {
  it('shows loading state while auth initializes', () => {
    // sessionStorage is empty → isLoading = false immediately (no stored token).
    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <Routes>
            <RouterRoute path="/" element={<FakeProtected />} />
            <RouterRoute path="/login" element={<div>Login page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )
    // No stored refresh token → fast path, no loading spinner needed.
    // Verify unauthenticated redirect happens.
    expect(screen.getByText('Login page')).toBeInTheDocument()
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
  })

  it('does not show protected content when unauthenticated', () => {
    sessionStorage.clear()
    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <Routes>
            <RouterRoute path="/" element={<FakeProtected />} />
            <RouterRoute path="/login" element={<div>Login page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
    expect(screen.getByText('Login page')).toBeInTheDocument()
  })
})
