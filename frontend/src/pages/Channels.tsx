/* M14.3 — Channel / Brand Workspace */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { StatTile } from '@/components/common/StatTile'
import { UnavailableState } from '@/components/common/UnavailableState'
import { Modal } from '@/components/common/Modal'
import { useChannels, useChannel, useChannelAccounts, useChannelStrategy, useCreateChannel, useCreatePlatformAccount } from '@/hooks/useChannel'

function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}

function CreateChannelModal({ workspaceId, open, onClose }: {
  workspaceId: string
  open: boolean
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [description, setDescription] = useState('')
  const [apiError, setApiError] = useState<string | null>(null)

  const createChannel = useCreateChannel(workspaceId)

  function handleNameChange(v: string) {
    setName(v)
    if (!slugTouched) setSlug(slugify(v))
  }

  function handleSlugChange(v: string) {
    setSlugTouched(true)
    setSlug(v)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await createChannel.mutateAsync({
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || undefined,
      })
      setName(''); setSlug(''); setSlugTouched(false); setDescription('')
      onClose()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  function handleClose() {
    setName(''); setSlug(''); setSlugTouched(false); setDescription(''); setApiError(null)
    onClose()
  }

  const isValid = name.trim().length > 0 && slug.trim().length > 0

  return (
    <Modal
      open={open}
      title="New Channel"
      onClose={handleClose}
      footer={
        <>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleClose}
            disabled={createChannel.isPending}
          >Cancel</button>
          <button
            type="submit"
            form="create-channel-form"
            className="btn btn-primary"
            disabled={!isValid || createChannel.isPending}
          >{createChannel.isPending ? 'Creating…' : 'Create Channel'}</button>
        </>
      }
    >
      <form id="create-channel-form" onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="channel-name" className="form-label">Channel name <span aria-hidden="true">*</span></label>
          <input
            id="channel-name"
            className="form-input"
            type="text"
            value={name}
            onChange={e => handleNameChange(e.target.value)}
            required
            autoFocus
            aria-required="true"
            placeholder="My Brand Channel"
          />
        </div>
        <div className="form-group">
          <label htmlFor="channel-slug" className="form-label">Slug <span aria-hidden="true">*</span></label>
          <input
            id="channel-slug"
            className="form-input"
            type="text"
            value={slug}
            onChange={e => handleSlugChange(e.target.value)}
            required
            aria-required="true"
            placeholder="my-brand-channel"
            pattern="[a-z0-9][a-z0-9-]*"
            aria-describedby="slug-hint"
          />
          <p id="slug-hint" className="form-hint">Lowercase letters, numbers, and hyphens only.</p>
        </div>
        <div className="form-group">
          <label htmlFor="channel-description" className="form-label">Description</label>
          <input
            id="channel-description"
            className="form-input"
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Optional description"
          />
        </div>
        {apiError && (
          <div role="alert" className="form-error">{apiError}</div>
        )}
      </form>
    </Modal>
  )
}

const SUPPORTED_PLATFORMS = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'instagram', label: 'Instagram' },
  { id: 'tiktok', label: 'TikTok' },
] as const

function AddPlatformAccountModal({ workspaceId, channelId, open, onClose }: {
  workspaceId: string
  channelId: string
  open: boolean
  onClose: () => void
}) {
  const [platformId, setPlatformId] = useState<string>(SUPPORTED_PLATFORMS[0].id)
  const [externalId, setExternalId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [apiError, setApiError] = useState<string | null>(null)

  const create = useCreatePlatformAccount(workspaceId, channelId)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await create.mutateAsync({
        platform_id: platformId,
        external_account_id: externalId.trim(),
        display_name: displayName.trim(),
      })
      setExternalId(''); setDisplayName(''); setApiError(null)
      onClose()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  function handleClose() {
    setPlatformId(SUPPORTED_PLATFORMS[0].id); setExternalId('')
    setDisplayName(''); setApiError(null)
    onClose()
  }

  const isValid = externalId.trim().length > 0 && displayName.trim().length > 0

  return (
    <Modal
      open={open}
      title="Register Platform Account"
      onClose={handleClose}
      footer={
        <>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleClose}
            disabled={create.isPending}
          >Cancel</button>
          <button
            type="submit"
            form="add-platform-account-form"
            className="btn btn-primary"
            disabled={!isValid || create.isPending}
          >{create.isPending ? 'Registering…' : 'Register Account'}</button>
        </>
      }
    >
      <form id="add-platform-account-form" onSubmit={handleSubmit}>
        <div className="diagnostic-finding diagnostic-finding-info mb-4">
          <span>
            This registers account metadata only. No credentials are stored here.
            Status will show <strong>Disconnected</strong> until OAuth is configured separately.
          </span>
        </div>
        <div className="form-group">
          <label htmlFor="pa-platform" className="form-label">Platform <span aria-hidden="true">*</span></label>
          <select
            id="pa-platform"
            className="field-select"
            value={platformId}
            onChange={e => setPlatformId(e.target.value)}
          >
            {SUPPORTED_PLATFORMS.map(p => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="pa-external-id" className="form-label">
            External account ID <span aria-hidden="true">*</span>
          </label>
          <input
            id="pa-external-id"
            className="form-input"
            type="text"
            value={externalId}
            onChange={e => setExternalId(e.target.value)}
            required
            aria-required="true"
            autoFocus
            placeholder="UCxxxxxxxxxxxxxxxx"
            aria-describedby="pa-external-id-hint"
          />
          <p id="pa-external-id-hint" className="form-hint">
            YouTube channel ID, Instagram handle, or TikTok username.
          </p>
        </div>
        <div className="form-group">
          <label htmlFor="pa-display-name" className="form-label">
            Display name <span aria-hidden="true">*</span>
          </label>
          <input
            id="pa-display-name"
            className="form-input"
            type="text"
            value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            required
            aria-required="true"
            placeholder="My YouTube Channel"
          />
        </div>
        {apiError && (
          <div role="alert" className="form-error">{apiError}</div>
        )}
      </form>
    </Modal>
  )
}

function ChannelDetail({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const channel   = useChannel(workspaceId, channelId)
  const accounts  = useChannelAccounts(workspaceId, channelId)
  const strategy  = useChannelStrategy(workspaceId, channelId)
  const [showAddAccount, setShowAddAccount] = useState(false)

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
          <button
            className="btn btn-secondary"
            onClick={() => setShowAddAccount(true)}
            aria-label="Add platform account"
          >+ Add Platform Account</button>
        </div>

        <AddPlatformAccountModal
          workspaceId={workspaceId}
          channelId={channelId}
          open={showAddAccount}
          onClose={() => setShowAddAccount(false)}
        />

        {accounts.isLoading ? <LoadingState /> :
         !accounts.data?.length ? (
           <EmptyState
             icon="🔌"
             title="No platform accounts registered"
             description="Register a platform account to identify where content will be published. Credentials are configured separately."
             action={
               <button className="btn btn-secondary" onClick={() => setShowAddAccount(true)}>
                 + Add Platform Account
               </button>
             }
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
                     <td className="font-mono text-xs text-muted">
                       {a.credential_profile_id
                         ? <span className="badge badge-healthy">Credential linked</span>
                         : <span className="badge badge-warn">No credential</span>}
                     </td>
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
  const [showCreate, setShowCreate] = useState(false)

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
        <button
          className="btn btn-primary"
          onClick={() => setShowCreate(true)}
          aria-label="Create new channel"
        >+ New Channel</button>
      </div>

      <CreateChannelModal
        workspaceId={wid}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />

      <div className="page-body">
        {!channels?.length ? (
          <EmptyState
            icon="📡"
            title="No channels yet"
            description="Create your first channel to start building your content operation."
            action={
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                + New Channel
              </button>
            }
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
