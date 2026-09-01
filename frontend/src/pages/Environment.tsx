/* Phase 16 — Environment Readiness
   Reads the /environment endpoint and displays operator prerequisites.
   No actions are taken here — it is a pure status view. */

import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { UnavailableState } from '@/components/common/UnavailableState'

function useEnvironmentReadiness(workspaceId: string) {
  return useQuery({
    queryKey: ['environment', workspaceId],
    queryFn: () => api.getEnvironmentReadiness(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 30_000,
  })
}

type Check = { ok: boolean; detail: string }

function CheckRow({ name, check }: { name: string; check: Check }) {
  return (
    <tr>
      <td className="font-mono text-sm">{name}</td>
      <td>
        <span
          className="tag"
          style={{
            background: check.ok ? 'var(--status-ok-bg)' : 'var(--status-error-bg)',
            color: check.ok ? 'var(--status-ok)' : 'var(--status-error)',
          }}
        >
          {check.ok ? 'ok' : 'missing'}
        </span>
      </td>
      <td className="text-sm text-secondary">{check.detail}</td>
    </tr>
  )
}

function GateBadge({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div
      className="card"
      style={{
        borderLeft: `3px solid ${ready ? 'var(--status-ok)' : 'var(--status-warn)'}`,
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
      }}
    >
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: ready ? 'var(--status-ok)' : 'var(--status-warn)',
          flexShrink: 0,
        }}
      />
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-secondary">{ready ? 'Prerequisites met' : 'Prerequisites not met'}</div>
      </div>
    </div>
  )
}

export function Environment() {
  const { workspaceId } = useParams<{ workspaceId: string }>()

  if (!workspaceId) {
    return (
      <div className="page-body">
        <UnavailableState title="No workspace selected" reason="no_data" />
      </div>
    )
  }

  return <EnvironmentContent workspaceId={workspaceId} />
}

function EnvironmentContent({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading, error } = useEnvironmentReadiness(workspaceId)

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Environment</h1>
          <p className="page-subtitle">Operator prerequisites and pilot readiness — local state only, no live calls</p>
        </div>
      </div>

      <div className="page-body">
        {isLoading && (
          <div className="section">
            <div className="card">
              <p className="text-sm text-secondary">Checking environment…</p>
            </div>
          </div>
        )}

        {error && (
          <div className="section">
            <UnavailableState
              title="Could not load environment status"
              description={(error as Error).message}
              reason="error"
            />
          </div>
        )}

        {data && (
          <>
            <div className="section">
              <div className="section-header">
                <h2 className="section-title">Pilot Gates</h2>
              </div>
              <div className="flex flex-col gap-3">
                <GateBadge label="Analytics Ready" ready={!!data.analytics_ready} />
                <GateBadge label="Pilot Ready" ready={!!data.pilot_ready} />
              </div>
            </div>

            <div className="section">
              <div className="section-header">
                <h2 className="section-title">Prerequisite Checks</h2>
              </div>
              <div className="card">
                <div className="table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Check</th>
                        <th>Status</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries((data.checks ?? {}) as Record<string, Check>).map(
                        ([name, check]) => (
                          <CheckRow key={name} name={name} check={check} />
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div className="section">
              <div className="section-header">
                <h2 className="section-title">Configuration Prerequisites</h2>
              </div>
              <div className="card">
                <div className="flex flex-col gap-3">
                  {[
                    { key: 'ACE_ENV=development', desc: 'Enable dev-auth so the frontend can authenticate without a JWT.' },
                    { key: 'ACE_YOUTUBE_API_KEY', desc: 'Required for market scans and analytics ingest (YouTube Data API v3).' },
                    { key: 'YOUTUBE_CLIENT_SECRETS_PATH', desc: 'Path to client_secrets.json for OAuth flow.' },
                    { key: 'ACE_ANTHROPIC_API_KEY', desc: 'Required when ACE_AI_PROVIDER=anthropic. Not needed in fake mode.' },
                    { key: 'ACE_AI_PROVIDER=fake', desc: 'Use fake provider for local testing without spending quota.' },
                  ].map(item => (
                    <div key={item.key} className="diagnostic-finding diagnostic-finding-info">
                      <div>
                        <code className="font-mono text-sm">{item.key}</code>
                        <p className="mt-1 text-sm">{item.desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="section">
              <div className="section-header">
                <h2 className="section-title">Approval Boundaries</h2>
              </div>
              <div className="card">
                <p className="text-sm text-secondary mb-3">
                  The following actions require explicit operator approval before execution.
                  The system will not proceed automatically past these boundaries.
                </p>
                <div className="flex flex-col gap-2">
                  {[
                    'Market scan — requires explicit operator trigger via CLI (ace market scan)',
                    'Opportunity creation — operator reviews and approves opportunities',
                    'Experiment planning — operator approves experiment hypothesis before brief generation',
                    'Content generation — operator approves brief before narration/render pipeline',
                    'Publishing — operator approves render before upload',
                    'Public release — requires ACE_RELEASE_PUBLIC_ENABLED=true AND ACE_PUBLISHING_LIVE_ENABLED=true',
                  ].map((boundary) => (
                    <div key={boundary} className="diagnostic-finding">
                      <p className="text-sm">{boundary}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
