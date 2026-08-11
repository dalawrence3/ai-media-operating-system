/**
 * Authentication context and provider.
 *
 * Stores access token + refresh token in memory only (never localStorage for
 * the access token). Refresh token is kept in a short-lived sessionStorage key
 * so a page reload does not force re-login, but closing the tab clears it.
 *
 * Token refresh is initiated automatically when the API client receives a 401.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api } from '@/api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthUser {
  userId: number
  email: string
  actor: string
  workspaceRoles: Record<string, string>
}

export interface AuthState {
  user: AuthUser | null
  accessToken: string | null
  isAuthenticated: boolean
  isLoading: boolean
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** Attempt a silent token refresh. Resolves with the new token or null. */
  refresh: () => Promise<string | null>
}

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

const REFRESH_TOKEN_KEY = 'ace_refresh_token'

function saveRefreshToken(token: string) {
  sessionStorage.setItem(REFRESH_TOKEN_KEY, token)
}

function loadRefreshToken(): string | null {
  return sessionStorage.getItem(REFRESH_TOKEN_KEY)
}

function clearRefreshToken() {
  sessionStorage.removeItem(REFRESH_TOKEN_KEY)
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const AuthContext = createContext<AuthContextValue | null>(null)

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

// E2E dev-auth bypass: injected by Playwright fixtures before React mounts.
// Skips JWT bootstrap so workspace routes render without a real login.
// import.meta.env.DEV is substituted to `false` by Vite at production build time,
// making the entire expression dead code that tree-shaking removes.
// Setting window.__ACE_E2E_DEV_AUTH in a production build has no effect.
const isE2EDevAuth =
  import.meta.env.DEV &&
  typeof window !== 'undefined' &&
  (window as unknown as Record<string, unknown>).__ACE_E2E_DEV_AUTH === true

const E2E_DEV_USER: AuthUser = {
  userId: 0,
  email: 'dev@local',
  actor: 'dev:studio-user',
  workspaceRoles: {},
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  // Prevent concurrent refresh calls.
  const refreshingRef = useRef<Promise<string | null> | null>(null)
  // Keep a ref to access token so auth callbacks don't capture a stale closure.
  const accessTokenRef = useRef<string | null>(null)

  // Wire the API client auth callbacks.
  // refresh and onAuthLost are stable (useCallback with no deps or captured via ref).
  useEffect(() => {
    api.setAuthCallbacks({
      getToken: () => accessTokenRef.current,
      onRefreshNeeded: () => {
        // Call refresh(), which deduplicates concurrent calls via refreshingRef.
        return refreshFn.current()
      },
      onAuthLost: () => {
        setAccessToken(null)
        accessTokenRef.current = null
        setUser(null)
        clearRefreshToken()
      },
    })
    return () => api.clearAuthCallbacks()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep accessTokenRef in sync with state.
  useEffect(() => {
    accessTokenRef.current = accessToken
  }, [accessToken])

  // Stable ref to the refresh function (avoids stale closure in api callbacks).
  const refreshFn = useRef<() => Promise<string | null>>(() => Promise.resolve(null))

  // On mount, try to silently refresh using a persisted refresh token.
  useEffect(() => {
    const storedRefresh = loadRefreshToken()
    if (!storedRefresh) {
      setIsLoading(false)
      return
    }
    silentRefresh(storedRefresh).finally(() => setIsLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function silentRefresh(rawRefreshToken: string): Promise<string | null> {
    try {
      const res = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rawRefreshToken }),
      })
      if (!res.ok) {
        clearRefreshToken()
        return null
      }
      const data = await res.json()
      const newAccessToken: string = data.access_token
      setAccessToken(newAccessToken)
      accessTokenRef.current = newAccessToken
      await fetchMe(newAccessToken)
      return newAccessToken
    } catch {
      clearRefreshToken()
      return null
    }
  }

  async function fetchMe(token: string): Promise<void> {
    try {
      const res = await fetch('/api/v1/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setUser({
        userId: data.user_id,
        email: data.email,
        actor: data.actor,
        workspaceRoles: data.workspace_roles ?? {},
      })
    } catch {
      // Non-fatal — user identity just stays null.
    }
  }

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(err.detail ?? 'Login failed')
    }
    const data = await res.json()
    setAccessToken(data.access_token)
    accessTokenRef.current = data.access_token
    saveRefreshToken(data.refresh_token)
    await fetchMe(data.access_token)
  }, [])

  const refresh = useCallback(async (): Promise<string | null> => {
    // Deduplicate concurrent refresh calls.
    if (refreshingRef.current) return refreshingRef.current

    const storedRefresh = loadRefreshToken()
    if (!storedRefresh) {
      setAccessToken(null)
      accessTokenRef.current = null
      setUser(null)
      return null
    }

    const promise = silentRefresh(storedRefresh).then((token) => {
      refreshingRef.current = null
      if (!token) {
        setAccessToken(null)
        accessTokenRef.current = null
        setUser(null)
      }
      return token
    })
    refreshingRef.current = promise
    return promise
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep refreshFn.current pointing at the latest refresh.
  useEffect(() => {
    refreshFn.current = refresh
  }, [refresh])

  const logout = useCallback(async () => {
    const storedRefresh = loadRefreshToken()
    const token = accessToken

    setAccessToken(null)
    setUser(null)
    clearRefreshToken()

    if (token && storedRefresh) {
      try {
        await fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ refresh_token: storedRefresh }),
        })
      } catch {
        // Best-effort — tokens are already cleared locally.
      }
    }
  }, [accessToken])

  // E2E bypass: render immediately as authenticated with a synthetic dev user.
  // The API client still sends X-Dev-Actor (token is null → DEV fallback fires).
  if (isE2EDevAuth) {
    return (
      <AuthContext.Provider
        value={{
          user: E2E_DEV_USER,
          accessToken: null,
          isAuthenticated: true,
          isLoading: false,
          login: async () => {},
          logout: async () => {},
          refresh: async () => null,
        }}
      >
        {children}
      </AuthContext.Provider>
    )
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        accessToken,
        isAuthenticated: !!accessToken && !!user,
        isLoading,
        login,
        logout,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
