import { type ReactNode, useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router-dom'
import { useWorkspaces, useHealth, useReviewQueue, useExceptionQueue } from '@/hooks/useWorkspace'
import './AppShell.css'

/* Primary navigation — the five things an operator does day to day.
   Infrastructure lives under Advanced so it never competes with these. */
const PRIMARY_NAV = [
  { to: 'dashboard', icon: '◫', label: 'Dashboard' },
  { to: 'content',   icon: '🎬', label: 'Content' },
  { to: 'analytics', icon: '📊', label: 'Analytics' },
  { to: 'learn',     icon: '💡', label: 'Learn' },
  { to: 'channel',   icon: '📡', label: 'Channel' },
]

/* Advanced / system surfaces. Still fully reachable, deliberately secondary. */
const ADVANCED_NAV = [
  { to: 'pipelines',   icon: '▶', label: 'Pipelines' },
  { to: 'reviews',     icon: '✅', label: 'Reviews', badge: 'reviews' },
  { to: 'exceptions',  icon: '⚠', label: 'Exceptions', badge: 'exceptions' },
  { to: 'operations',  icon: '⚙', label: 'Operations' },
  { to: 'workflows',   icon: '🔄', label: 'Workflows' },
  { to: 'experiments', icon: '🧪', label: 'Experiments' },
  { to: 'environment', icon: '🔬', label: 'Environment' },
  { to: 'health',      icon: '❤', label: 'Health' },
  { to: 'audit',       icon: '📋', label: 'Audit' },
  { to: 'settings',    icon: '🔧', label: 'Settings' },
]

interface NavItemDef {
  to: string
  icon: string
  label: string
  badge?: string
}

/** A single sidebar link, shared by the primary and advanced groups. */
function NavItem({
  item,
  workspaceId,
  badges,
}: {
  item: NavItemDef
  workspaceId: string | undefined
  badges: Record<string, number>
}) {
  const to = workspaceId ? `/workspaces/${workspaceId}/${item.to}` : `/${item.to}`
  const count = item.badge ? (badges[item.badge] ?? 0) : 0

  return (
    <NavLink
      to={to}
      className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
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
}

interface Props {
  children: ReactNode
}

export function AppShell({ children }: Props) {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const [_theme, setTheme] = useState<'light' | 'dark'>(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
  )
  /* Advanced group stays collapsed by default so infrastructure does not
     compete with the five primary destinations. */
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const { data: workspaces } = useWorkspaces()
  const { data: health } = useHealth(workspaceId ?? '')
  const { data: reviews } = useReviewQueue(workspaceId ?? '')
  const { data: exceptions } = useExceptionQueue(workspaceId ?? '')

  const reviewCount = reviews?.length ?? 0
  const exceptionCount = exceptions?.length ?? 0

  /* Global status chip. The backend's overall_status vocabulary
     (ok / warn / degraded) is mapped to product wording; the dot is never the
     only signal — the label always carries the same meaning in words. */
  const healthStatus = health?.overall_status ?? 'unknown'
  const healthDotCls =
    healthStatus === 'ok' || healthStatus === 'healthy' ? 'health-dot health-dot-healthy' :
    healthStatus === 'warn'                             ? 'health-dot health-dot-warn'    :
    healthStatus === 'degraded' || healthStatus === 'unhealthy'
      ? 'health-dot health-dot-error'
      : 'health-dot'
  const healthLabel =
    healthStatus === 'ok' || healthStatus === 'healthy' ? 'All systems healthy' :
    healthStatus === 'warn'                             ? 'Needs attention'     :
    healthStatus === 'degraded' || healthStatus === 'unhealthy' ? 'Degraded'    :
    'Status unknown'

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

  /* Rolled up onto the collapsed Advanced toggle so moving these out of the
     primary nav never hides work that needs attention. */
  const advancedBadgeTotal = ADVANCED_NAV.reduce(
    (sum, item) => sum + (item.badge ? (badges[item.badge] ?? 0) : 0),
    0,
  )

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
          <div className="sidebar-nav-section">
            {PRIMARY_NAV.map(item => (
              <NavItem key={item.to} item={item} workspaceId={workspaceId} badges={badges} />
            ))}
          </div>

          <div className="sidebar-nav-section">
            <button
              className="sidebar-advanced-toggle"
              onClick={() => setAdvancedOpen(o => !o)}
              aria-expanded={advancedOpen}
              aria-controls="advanced-nav-items"
            >
              <span className="sidebar-nav-section-label">Advanced</span>
              <span className="sidebar-advanced-caret" aria-hidden="true">
                {advancedOpen ? '▾' : '▸'}
              </span>
              {!advancedOpen && advancedBadgeTotal > 0 && (
                <span
                  className="nav-badge nav-badge-warn"
                  aria-label={`${advancedBadgeTotal} items need attention under Advanced`}
                >
                  {advancedBadgeTotal > 99 ? '99+' : advancedBadgeTotal}
                </span>
              )}
            </button>
            <div id="advanced-nav-items" hidden={!advancedOpen}>
              {ADVANCED_NAV.map(item => (
                <NavItem key={item.to} item={item} workspaceId={workspaceId} badges={badges} />
              ))}
            </div>
          </div>
        </div>

        {/* Footer: global system status + theme toggle */}
        <div className="sidebar-footer">
          <div className="sidebar-health-indicator">
            <NavLink
              to={workspaceId ? `/workspaces/${workspaceId}/health` : '/health'}
              className="sidebar-health-link"
              aria-label={`System status: ${healthLabel}. Open system diagnostics.`}
            >
              <span className={healthDotCls} aria-hidden="true" />
              <span>{healthLabel}</span>
            </NavLink>
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
