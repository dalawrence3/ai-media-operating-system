import { type ReactNode, useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router-dom'
import { useWorkspaces, useHealth, useReviewQueue, useExceptionQueue } from '@/hooks/useWorkspace'
import './AppShell.css'

const NAV = [
  {
    section: 'Operations',
    items: [
      { to: 'dashboard',   icon: '⬛', label: 'Dashboard' },
      { to: 'pipelines',   icon: '▶', label: 'Pipelines' },
      { to: 'reviews',     icon: '✅', label: 'Reviews', badge: 'reviews' },
      { to: 'exceptions',  icon: '⚠', label: 'Exceptions', badge: 'exceptions' },
      { to: 'operations',  icon: '⚙', label: 'Operations' },
    ],
  },
  {
    section: 'Content',
    items: [
      { to: 'channels',    icon: '📡', label: 'Channels' },
      { to: 'publishing',  icon: '📤', label: 'Publishing' },
    ],
  },
  {
    section: 'Intelligence',
    items: [
      { to: 'analytics',   icon: '📊', label: 'Analytics' },
      { to: 'learning',    icon: '🎓', label: 'Learning' },
      { to: 'experiments', icon: '🧪', label: 'Experiments' },
    ],
  },
  {
    section: 'System',
    items: [
      { to: 'workflows',   icon: '🔄', label: 'Workflows' },
      { to: 'health',      icon: '❤', label: 'Health' },
      { to: 'audit',       icon: '📋', label: 'Audit' },
      { to: 'settings',    icon: '🔧', label: 'Settings' },
    ],
  },
]

interface Props {
  children: ReactNode
}

export function AppShell({ children }: Props) {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const [_theme, setTheme] = useState<'light' | 'dark'>(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
  )

  const { data: workspaces } = useWorkspaces()
  const { data: health } = useHealth(workspaceId ?? '')
  const { data: reviews } = useReviewQueue(workspaceId ?? '')
  const { data: exceptions } = useExceptionQueue(workspaceId ?? '')

  const reviewCount = reviews?.length ?? 0
  const exceptionCount = exceptions?.length ?? 0

  const healthStatus = health?.overall_status ?? 'unknown'
  const healthDotCls =
    healthStatus === 'healthy'   ? 'health-dot health-dot-healthy' :
    healthStatus === 'degraded'  ? 'health-dot health-dot-warn'    :
    healthStatus === 'unhealthy' ? 'health-dot health-dot-error'   :
    'health-dot'

  function onWorkspaceChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value
    if (id) void navigate(`/workspaces/${id}/dashboard`)
  }

  function toggleTheme() {
    setTheme(t => {
      const next = t === 'light' ? 'dark' : 'light'
      document.documentElement.setAttribute('data-theme', next)
      return next
    })
  }

  const badges: Record<string, number> = {
    reviews: reviewCount,
    exceptions: exceptionCount,
  }

  return (
    <div className="app-shell">
      {/* ── Dev auth banner ── */}
      <div className="dev-auth-banner" role="banner" aria-label="Development mode indicator">
        ⚠ DEV MODE — Not production authentication
      </div>

      <nav className="sidebar" aria-label="Primary navigation">
        {/* Logo */}
        <div className="sidebar-logo">
          <span className="sidebar-logo-text">AI Media OS</span>
          <span className="sidebar-logo-sub">Studio Dashboard</span>
        </div>

        {/* Workspace selector */}
        {workspaces && workspaces.length > 0 && (
          <div className="sidebar-workspace">
            <p className="sidebar-workspace-label">Workspace</p>
            <select
              className="sidebar-workspace-select"
              value={workspaceId ?? ''}
              onChange={onWorkspaceChange}
              aria-label="Select workspace"
            >
              <option value="">— select —</option>
              {workspaces.map(w => (
                <option key={w.id} value={w.id}>{w.name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Navigation */}
        <div className="sidebar-nav">
          {NAV.map(section => (
            <div key={section.section} className="sidebar-nav-section">
              <p className="sidebar-nav-section-label">{section.section}</p>
              {section.items.map(item => {
                const to = workspaceId
                  ? `/workspaces/${workspaceId}/${item.to}`
                  : `/${item.to}`
                const count = item.badge ? (badges[item.badge] ?? 0) : 0
                return (
                  <NavLink
                    key={item.to}
                    to={to}
                    className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                    aria-label={item.label}
                  >
                    <span className="nav-link-icon" aria-hidden="true">{item.icon}</span>
                    <span>{item.label}</span>
                    {count > 0 && (
                      <span
                        className={`nav-badge${item.badge === 'exceptions' ? ' nav-badge-warn' : ''}`}
                        aria-label={`${count} ${item.label.toLowerCase()} pending`}
                      >
                        {count > 99 ? '99+' : count}
                      </span>
                    )}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </div>

        {/* Footer health indicator */}
        <div className="sidebar-footer">
          <div className="sidebar-health-indicator">
            <span className={healthDotCls} aria-hidden="true" />
            <span>System {healthStatus}</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={toggleTheme}
              style={{ marginLeft: 'auto', color: 'rgba(255,255,255,.4)' }}
              aria-label="Toggle color theme"
            >
              ◑
            </button>
          </div>
        </div>
      </nav>

      <main className="main-content" id="main-content">
        {children}
      </main>
    </div>
  )
}
