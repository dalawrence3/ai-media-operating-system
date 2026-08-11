import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/auth/AuthContext'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--surface-bg, #0f1117)',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 400,
          padding: '2rem',
          background: 'var(--card-bg, #1a1d27)',
          borderRadius: 8,
          border: '1px solid var(--border, #2a2d3a)',
        }}
      >
        <h1
          style={{
            margin: '0 0 1.5rem',
            fontSize: '1.25rem',
            fontWeight: 600,
            color: 'var(--text-primary, #e8eaf0)',
          }}
        >
          Sign in — AI Media OS
        </h1>

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ marginBottom: '1rem' }}>
            <label
              htmlFor="email"
              style={{
                display: 'block',
                marginBottom: '0.375rem',
                fontSize: '0.875rem',
                color: 'var(--text-secondary, #9ca3af)',
              }}
            >
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              style={{
                width: '100%',
                padding: '0.5rem 0.75rem',
                background: 'var(--input-bg, #0f1117)',
                border: '1px solid var(--border, #2a2d3a)',
                borderRadius: 4,
                color: 'var(--text-primary, #e8eaf0)',
                fontSize: '0.875rem',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div style={{ marginBottom: '1.5rem' }}>
            <label
              htmlFor="password"
              style={{
                display: 'block',
                marginBottom: '0.375rem',
                fontSize: '0.875rem',
                color: 'var(--text-secondary, #9ca3af)',
              }}
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              style={{
                width: '100%',
                padding: '0.5rem 0.75rem',
                background: 'var(--input-bg, #0f1117)',
                border: '1px solid var(--border, #2a2d3a)',
                borderRadius: 4,
                color: 'var(--text-primary, #e8eaf0)',
                fontSize: '0.875rem',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {error && (
            <p
              role="alert"
              style={{
                margin: '0 0 1rem',
                padding: '0.5rem 0.75rem',
                background: 'var(--error-bg, #3b1c1c)',
                border: '1px solid var(--error-border, #7f1d1d)',
                borderRadius: 4,
                color: 'var(--error-text, #fca5a5)',
                fontSize: '0.875rem',
              }}
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting || !email || !password}
            style={{
              width: '100%',
              padding: '0.625rem',
              background: isSubmitting ? 'var(--brand-muted, #3b4a6b)' : 'var(--brand, #4f6ef7)',
              border: 'none',
              borderRadius: 4,
              color: '#fff',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: isSubmitting ? 'not-allowed' : 'pointer',
            }}
          >
            {isSubmitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
