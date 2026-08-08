/* M14.3 — Channel / Brand Workspace */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { StatTile } from '@/components/common/StatTile'
import { UnavailableState } from '@/components/common/UnavailableState'
import { useChannels, useChannel, useChannelAccounts, useChannelStrategy } from '@/hooks/useChannel'

function ChannelDetail({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const channel   = useChannel(workspaceId, channelId)
  const accounts  = useChannelAccounts(workspaceId, channelId)
  const strategy  = useChannelStrategy(workspaceId, channelId)

  if (channel.isLoading) return <LoadingState message="Loading channel…" />
  if (channel.error) return <ErrorState error={channel.error} retry={channel.refetch} />

  const ch = channel.data!
  const strat = strategy.data
  const stratAvailable = strat && !('status' in strat && strat.status === 'unavailable')

  return (
    <div>
      {/* Channel header */}
      <div className="card mb-6">
        <div className="card-header">
          <div>
            <h2 className="card-title">{ch.name}</h2>
            <p className="card-subtitle">/{ch.slug}</p>
          </div>
          <div className="flex gap-2">
            <StatusBadge status={ch.status} />
            {ch.paused && <StatusBadge status="paused" />}
          </div>
        </div>
        <div className="stat-grid mt-4">
          <StatTile label="Automation" value={ch.automation_level} />
          <StatTile label="Accounts" value={ch.account_count} sub="Platform accounts" />
          <StatTile label="Active Pipelines" value={ch.active_pipeline_count} />
        </div>
      </div>

      {/* Platform accounts */}
      <section className="section">
        <div className="section-header">
          <h3 className="section-title">Platform Accounts</h3>
        </div>
        {accounts.isLoading ? <LoadingState /> :
         !accounts.data?.length ? (
           <UnavailableState
             title="No platform accounts connected"
             description="Connect a platform account to enable publishing on this channel."
             reason="provider_setup_required"
           />
         ) : (
           <div className="table-wrapper">
             <table className="data-table">
               <thead>
                 <tr>
                   <th>Platform</th>
                   <th>Display Name</th>
                   <th>External ID</th>
                   <th>Status</th>
                   <th>Credential</th>
                 </tr>
               </thead>
               <tbody>
                 {accounts.data.map(a => (
                   <tr key={a.id}>
                     <td><span className="tag">{a.platform_key}</span></td>
                     <td className="font-600">{a.display_name}</td>
                     <td className="font-mono text-xs text-muted">{a.external_account_id}</td>
                     <td><StatusBadge status={a.status} /></td>
                     <td className="font-mono text-xs text-muted">{a.credential_profile_id?.slice(0,12) ?? 'No credential'}</td>
                   </tr>
                 ))}
               </tbody>
             </table>
           </div>
         )}
      </section>

      {/* Strategy */}
      <section className="section">
        <div className="section-header">
          <h3 className="section-title">Strategy Profile</h3>
        </div>
        {strategy.isLoading ? <LoadingState /> :
         !stratAvailable ? (
           <UnavailableState
             title="No strategy profile assigned"
             description="Assign a strategy profile to define what this channel is trying to achieve."
             reason="no_data"
           />
         ) : (
           <div className="card">
             <p className="card-title">{(strat as { name?: string }).name ?? 'Strategy'}</p>
             <pre className="font-mono text-xs text-secondary mt-4" style={{ whiteSpace: 'pre-wrap' }}>
               {JSON.stringify(strat, null, 2)}
             </pre>
           </div>
         )}
      </section>
    </div>
  )
}

export function Channels() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [selected, setSelected] = useState<string | null>(null)

  const { data: channels, isLoading, error, refetch } = useChannels(wid)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="📡" title="No workspace selected" />
    </div>
  )

  if (isLoading) return <LoadingState message="Loading channels…" />
  if (error) return <ErrorState error={error} retry={refetch} />

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Channels</h1>
          <p className="page-subtitle">Brand / channel workspace management</p>
        </div>
      </div>

      <div className="page-body">
        {!channels?.length ? (
          <EmptyState
            icon="📡"
            title="No channels yet"
            description="Create a channel to start building your content operation."
          />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: selected ? '280px 1fr' : '1fr', gap: 'var(--sp-6)' }}>
            {/* Channel list */}
            <div>
              {channels.map(ch => (
                <button
                  key={ch.id}
                  onClick={() => setSelected(ch.id === selected ? null : ch.id)}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    padding: 'var(--sp-4)',
                    background: ch.id === selected ? 'var(--accent-subtle)' : 'var(--surface-card)',
                    border: `1px solid ${ch.id === selected ? 'var(--accent)' : 'var(--border)'}`,
                    borderRadius: 'var(--radius-lg)',
                    marginBottom: 'var(--sp-3)',
                    cursor: 'pointer',
                    transition: 'all var(--transition-fast)',
                  }}
                  aria-pressed={ch.id === selected}
                  aria-label={`Select channel ${ch.name}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-600" style={{ fontSize: 'var(--font-size-md)' }}>{ch.name}</span>
                    <StatusBadge status={ch.status} />
                  </div>
                  <p className="text-xs text-muted mt-2">/{ch.slug}</p>
                  <div className="flex gap-3 mt-3">
                    <span className="text-xs text-secondary">{ch.description ?? 'Channel'}</span>
                  </div>
                </button>
              ))}
            </div>

            {/* Channel detail */}
            {selected && (
              <div>
                <ChannelDetail workspaceId={wid} channelId={selected} />
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
