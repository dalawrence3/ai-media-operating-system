/* Phase 17B.1 — Channel page.

   Productized single-channel view using data already available through current
   APIs. Shows channel identity, connected platform account with connection
   health, analytics permission state, and strategy profile.

   Multi-channel support: when useCurrentChannel gains a channel switcher, the
   full channel-list layout below remains reachable; for now the single-channel
   product view is the primary path. */

import { useState, useEffect } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { MetricCard } from '@/components/common/MetricCard'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { EmptyState } from '@/components/common/EmptyState'
import { TechnicalDetails } from '@/components/common/TechnicalDetails'
import { LocalTime } from '@/components/common/LocalTime'
import { Modal } from '@/components/common/Modal'
import { useCurrentChannel } from '@/hooks/useCurrentChannel'
import {
  useChannels,
  useChannelAccounts,
  useChannelAutomationPolicy,
  useChannelPublishingAuthorization,
  useUpdatePublishingAuthorization,
  useChannelReadiness,
  useChannelStrategy,
  useCreateStrategyVersion,
  useCreateChannel,
  useCreatePlatformAccount,
  useAccountConnectionStatus,
  useStartYouTubeOAuth,
  useDisconnectYouTubeAccount,
  useUpdateAutomationPolicy,
  useVerifyYouTubeConnection,
  useUpgradeYouTubeUploadScope,
  useUpgradeYouTubeAnalyticsScope,
  useUpgradeYouTubeReleaseScope,
} from '@/hooks/useChannel'
import type { CadenceType, CPAccount, StrategyConfig, YouTubeVerificationResult } from '@/api/types'
import { formatPercent, humanizeStatus } from '@/lib/format'
import {
  MATURITY_LEVELS,
  creativeDimensionLabel,
  defaultBootstrapStrategyConfig,
  maturityLabel,
  regimeLabel,
  validateStrategyConfig,
  weightSplitLabel,
} from '@/lib/strategyPolicy'

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

function YouTubeAccountCard({
  workspaceId,
  channelId,
  account,
}: {
  workspaceId: string
  channelId: string
  account: CPAccount
}) {
  const conn = useAccountConnectionStatus(workspaceId, channelId, account.id)
  const startOAuth = useStartYouTubeOAuth(workspaceId, channelId, account.id)
  const disconnect = useDisconnectYouTubeAccount(workspaceId, channelId, account.id)
  const verify = useVerifyYouTubeConnection(workspaceId, channelId, account.id)
  const upgradeUpload = useUpgradeYouTubeUploadScope(workspaceId, channelId, account.id)
  const upgradeAnalytics = useUpgradeYouTubeAnalyticsScope(workspaceId, channelId, account.id)
  const upgradeRelease = useUpgradeYouTubeReleaseScope(workspaceId, channelId, account.id)

  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [verificationResult, setVerificationResult] = useState<YouTubeVerificationResult | null>(null)

  const status = conn.data
  const isConnected = status?.connected ?? false
  const uploadGranted = status?.upload_scope_granted ?? false
  const analyticsGranted = status?.analytics_scope_granted ?? false
  // Never inferred from uploadGranted — they are different scopes.
  const releaseGranted = status?.release_scope_granted ?? false

  function handleVerify() {
    setVerificationResult(null)
    verify.mutate(undefined, {
      onSuccess: (result) => setVerificationResult(result),
    })
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-3)', marginBottom: 'var(--sp-3)' }}>
        <div>
          <p style={{ fontWeight: 600, margin: 0 }}>{account.display_name}</p>
          <p className="text-xs text-muted" style={{ margin: '2px 0 0' }}>YouTube</p>
        </div>
        <StatusBadge status={isConnected ? 'connected' : account.status} />
      </div>

      {conn.isLoading ? (
        <p className="text-sm text-muted">Checking connection…</p>
      ) : !isConnected ? (
        <div>
          <p className="text-sm text-secondary" style={{ margin: '0 0 var(--sp-3)' }}>
            {account.status === 'credential_invalid'
              ? 'YouTube credentials have expired. Reconnect to restore access.'
              : 'Connect this account to YouTube to enable publishing and analytics.'}
          </p>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => startOAuth.mutate()}
            disabled={startOAuth.isPending}
          >
            {startOAuth.isPending ? 'Redirecting…' : account.status === 'credential_invalid' ? 'Reconnect' : 'Connect to YouTube'}
          </button>
        </div>
      ) : (
        <>
          {/* Permission cards.

              Named by what the account can actually do, not by which Google
              scope backs it. Upload and public release are separate cards
              because they are separate scopes: youtube.upload authorizes
              creating a video, and only youtube.force-ssl authorizes changing
              one's privacy status. A single "Publishing" label spanning both
              would claim a capability the credential may not have. */}
          <div className="metric-grid" style={{ marginBottom: 'var(--sp-3)' }}>
            <div className="card" style={{ padding: 'var(--sp-3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-2)' }}>
                <span className="text-sm" style={{ fontWeight: 600 }}>Upload videos</span>
                <StatusBadge status={uploadGranted ? 'active' : 'inactive'} label={uploadGranted ? 'Granted' : 'Not granted'} />
              </div>
              {!uploadGranted && (
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ marginTop: 'var(--sp-2)', width: '100%' }}
                  onClick={() => upgradeUpload.mutate()}
                  disabled={upgradeUpload.isPending}
                >
                  {upgradeUpload.isPending ? 'Redirecting…' : 'Enable upload permission'}
                </button>
              )}
            </div>
            <div className="card" style={{ padding: 'var(--sp-3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-2)' }}>
                <span className="text-sm" style={{ fontWeight: 600 }}>Read analytics</span>
                <StatusBadge status={analyticsGranted ? 'active' : 'inactive'} label={analyticsGranted ? 'Granted' : 'Not granted'} />
              </div>
              {!analyticsGranted && (
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ marginTop: 'var(--sp-2)', width: '100%' }}
                  onClick={() => upgradeAnalytics.mutate()}
                  disabled={upgradeAnalytics.isPending}
                >
                  {upgradeAnalytics.isPending ? 'Redirecting…' : 'Enable analytics permission'}
                </button>
              )}
            </div>
            <div className="card" style={{ padding: 'var(--sp-3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-2)' }}>
                <span className="text-sm" style={{ fontWeight: 600 }}>Make videos public</span>
                <StatusBadge status={releaseGranted ? 'active' : 'inactive'} label={releaseGranted ? 'Granted' : 'Not granted'} />
              </div>
              {!releaseGranted && (
                <>
                  <p className="text-xs text-muted" style={{ margin: 'var(--sp-2) 0 0' }}>
                    Uploading a video and making it public are separate YouTube permissions. This
                    account can upload but cannot yet change a video's visibility.
                  </p>
                  <button
                    className="btn btn-secondary btn-sm"
                    style={{ marginTop: 'var(--sp-2)', width: '100%' }}
                    onClick={() => upgradeRelease.mutate()}
                    disabled={upgradeRelease.isPending}
                  >
                    {upgradeRelease.isPending ? 'Redirecting…' : 'Enable public release permission'}
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Verification status */}
          {verificationResult?.verified && (
            <p className="text-sm" style={{ color: 'var(--status-healthy)', margin: '0 0 var(--sp-2)' }}>
              Verified — {verificationResult.channel_title ?? 'connected'}
            </p>
          )}
          {verificationResult && !verificationResult.verified && (
            <p className="text-sm" style={{ color: 'var(--status-error)', margin: '0 0 var(--sp-2)' }}>
              {verificationResult.failure_reason ?? 'Verification failed'}
            </p>
          )}

          {/* Actions */}
          {!confirmDisconnect ? (
            <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
              <button className="btn btn-ghost btn-sm" onClick={handleVerify} disabled={verify.isPending}>
                {verify.isPending ? 'Verifying…' : 'Verify connection'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDisconnect(true)}>
                Disconnect
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
              <button
                className="btn btn-sm"
                style={{ background: 'var(--status-error)', color: '#fff', border: 'none' }}
                onClick={() => { disconnect.mutate(); setConfirmDisconnect(false) }}
                disabled={disconnect.isPending}
              >
                {disconnect.isPending ? 'Disconnecting…' : 'Confirm disconnect'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDisconnect(false)}>
                Cancel
              </button>
            </div>
          )}
        </>
      )}

      <TechnicalDetails summary="Account details">
        <div className="detail-meta-list">
          <div className="detail-meta-row">
            <span className="detail-meta-label">Channel ID</span>
            <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>{account.external_account_id}</span>
          </div>
          <div className="detail-meta-row">
            <span className="detail-meta-label">Account ID</span>
            <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>{account.id}</span>
          </div>
          {status?.provider_channel_id && (
            <div className="detail-meta-row">
              <span className="detail-meta-label">Provider channel</span>
              <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>{status.provider_channel_id}</span>
            </div>
          )}
          {status?.granted_scopes && status.granted_scopes.length > 0 && (
            <div className="detail-meta-row">
              <span className="detail-meta-label">Scopes</span>
              <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>{status.granted_scopes.join(', ')}</span>
            </div>
          )}
        </div>
      </TechnicalDetails>
    </div>
  )
}

const MATURITY_OPTIONS = MATURITY_LEVELS

function StrategyEditModal({ workspaceId, channelId, current, open, onClose }: {
  workspaceId: string
  channelId: string
  current: StrategyConfig | null
  open: boolean
  onClose: () => void
}) {
  const base = current ?? defaultBootstrapStrategyConfig()
  const [targetCount, setTargetCount] = useState(base.bootstrap.target_publication_count ?? 18)
  const [marketWeightPct, setMarketWeightPct] = useState(Math.round(base.bootstrap.market_intelligence_weight * 100))
  const [maxClusterSharePct, setMaxClusterSharePct] = useState(Math.round(base.diversity.max_cluster_share * 100))
  const [maxConsecutive, setMaxConsecutive] = useState(base.diversity.max_consecutive_same_cluster)
  const [maturityThreshold, setMaturityThreshold] = useState(base.transition.maturity_threshold)
  const [steadyExplorationPct, setSteadyExplorationPct] = useState(Math.round(base.steady_state.exploration_share * 100))
  const [apiError, setApiError] = useState<string | null>(null)

  const createVersion = useCreateStrategyVersion(workspaceId, channelId)

  function reset() {
    setTargetCount(base.bootstrap.target_publication_count ?? 18)
    setMarketWeightPct(Math.round(base.bootstrap.market_intelligence_weight * 100))
    setMaxClusterSharePct(Math.round(base.diversity.max_cluster_share * 100))
    setMaxConsecutive(base.diversity.max_consecutive_same_cluster)
    setMaturityThreshold(base.transition.maturity_threshold)
    setSteadyExplorationPct(Math.round(base.steady_state.exploration_share * 100))
    setApiError(null)
  }

  function handleClose() {
    reset()
    onClose()
  }

  const nextConfig: StrategyConfig = {
    ...base,
    bootstrap: {
      ...base.bootstrap,
      target_publication_count: targetCount,
      market_intelligence_weight: marketWeightPct / 100,
      channel_evidence_weight: 1 - marketWeightPct / 100,
    },
    steady_state: {
      ...base.steady_state,
      exploration_share: steadyExplorationPct / 100,
    },
    transition: {
      ...base.transition,
      maturity_threshold: maturityThreshold,
    },
    diversity: {
      max_cluster_share: maxClusterSharePct / 100,
      max_consecutive_same_cluster: maxConsecutive,
    },
  }
  const errors = validateStrategyConfig(nextConfig)
  const isValid = errors.length === 0

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await createVersion.mutateAsync(nextConfig)
      handleClose()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  return (
    <Modal
      open={open}
      title={current ? 'Edit Strategy' : 'Set Up Strategy'}
      onClose={handleClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={createVersion.isPending}>Cancel</button>
          <button
            type="submit"
            form="strategy-edit-form"
            className="btn btn-primary"
            disabled={!isValid || createVersion.isPending}
          >{createVersion.isPending ? 'Saving…' : 'Save as new version'}</button>
        </>
      }
    >
      <form id="strategy-edit-form" onSubmit={handleSubmit}>
        <p className="text-sm text-secondary mb-4">
          Saving creates a new version — the current one is preserved in history, never overwritten. Topics are never
          set here; they are always sourced dynamically from live market intelligence.
        </p>

        <div className="form-group">
          <label htmlFor="strategy-target-count" className="form-label">Exploration publication target</label>
          <input
            id="strategy-target-count"
            className="form-input"
            type="number"
            min={1}
            max={100}
            value={targetCount}
            onChange={e => setTargetCount(parseInt(e.target.value, 10) || 0)}
          />
          <p className="form-hint">How many publications the bootstrap exploration phase aims to cover before leaning on the channel's own evidence.</p>
        </div>

        <div className="form-group">
          <label htmlFor="strategy-market-weight" className="form-label">Market vs. channel-evidence weighting (bootstrap)</label>
          <input
            id="strategy-market-weight"
            type="range"
            min={0}
            max={100}
            step={5}
            value={marketWeightPct}
            onChange={e => setMarketWeightPct(parseInt(e.target.value, 10))}
            style={{ width: '100%' }}
          />
          <p className="form-hint">{marketWeightPct}% market intelligence / {100 - marketWeightPct}% channel evidence, while in bootstrap.</p>
        </div>

        <div className="form-group">
          <label className="form-label">Minimum diversity requirements</label>
          <div style={{ display: 'flex', gap: 'var(--sp-3)' }}>
            <div style={{ flex: 1 }}>
              <label htmlFor="strategy-max-cluster-share" className="form-hint">Max share from one cluster</label>
              <input
                id="strategy-max-cluster-share"
                className="form-input"
                type="number"
                min={5}
                max={100}
                value={maxClusterSharePct}
                onChange={e => setMaxClusterSharePct(parseInt(e.target.value, 10) || 0)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label htmlFor="strategy-max-consecutive" className="form-hint">Max consecutive, same cluster</label>
              <input
                id="strategy-max-consecutive"
                className="form-input"
                type="number"
                min={1}
                max={10}
                value={maxConsecutive}
                onChange={e => setMaxConsecutive(parseInt(e.target.value, 10) || 0)}
              />
            </div>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="strategy-maturity-threshold" className="form-label">Transition maturity threshold</label>
          <select
            id="strategy-maturity-threshold"
            className="form-input"
            value={maturityThreshold}
            onChange={e => setMaturityThreshold(e.target.value)}
          >
            {MATURITY_OPTIONS.map(level => (
              <option key={level} value={level}>{maturityLabel(level)}</option>
            ))}
          </select>
          <p className="form-hint">
            The channel moves from bootstrap to steady state once its evidence for {base.transition.trigger_metric.replace(/_/g, ' ')} reaches this maturity level.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="strategy-steady-exploration" className="form-label">Steady-state exploration share</label>
          <input
            id="strategy-steady-exploration"
            className="form-input"
            type="number"
            min={0}
            max={100}
            value={steadyExplorationPct}
            onChange={e => setSteadyExplorationPct(parseInt(e.target.value, 10) || 0)}
          />
          <p className="form-hint">% of the portfolio still reserved for exploration once the channel has matured — retaining some exploration even after strong winners emerge.</p>
        </div>

        {errors.length > 0 && (
          <div role="alert" className="form-error">{errors.join(' · ')}</div>
        )}
        {apiError && (
          <div role="alert" className="form-error">{apiError}</div>
        )}
      </form>
    </Modal>
  )
}

const CADENCE_LABELS: Record<CadenceType, string> = {
  every_12h: 'Every 12 hours',
  daily: 'Daily',
  every_n_days: 'Every N days',
  weekly: 'Weekly',
  custom_cron: 'Custom schedule',
}

function slotStateLabel(state: string): string {
  switch (state) {
    case 'filled': return 'Filled — ready for review'
    case 'reserved': return 'Reserved — selecting next candidate'
    case 'cancelled': return 'Cancelled'
    case 'expired': return 'Expired'
    default: return state
  }
}

function productionStageLabel(status: import('@/api/types').ProductionStatus | null): string {
  switch (status) {
    case 'queued': return 'Queued for production'
    case 'producing': return 'In production'
    case 'ready': return 'Ready for publishing'
    case 'failed': return 'Production failed'
    default: return 'Not started'
  }
}

function productionStageBadgeClass(status: import('@/api/types').ProductionStatus | null): string {
  switch (status) {
    case 'ready': return 'badge-healthy'
    case 'producing': return 'badge-info'
    case 'queued': return 'badge-neutral'
    case 'failed': return 'badge-error'
    default: return 'badge-neutral'
  }
}

type DeadlineStatus = 'comfortably_ahead' | 'approaching' | 'late' | 'missed'

function computeDeadlineStatus(scheduledForUtc: string): DeadlineStatus {
  const deadline = new Date(scheduledForUtc).getTime()
  if (Number.isNaN(deadline)) return 'comfortably_ahead'
  const remainingMs = deadline - Date.now()
  if (remainingMs < 0) return 'missed'
  if (remainingMs < 2 * 60 * 60 * 1000) return 'late'
  if (remainingMs < 12 * 60 * 60 * 1000) return 'approaching'
  return 'comfortably_ahead'
}

function deadlineStatusLabel(status: DeadlineStatus): string {
  switch (status) {
    case 'comfortably_ahead': return 'Comfortably ahead of slot'
    case 'approaching': return 'Approaching slot deadline'
    case 'late': return 'Running late for slot'
    case 'missed': return 'Missed reserved slot'
  }
}

function deadlineStatusBadgeClass(status: DeadlineStatus): string {
  switch (status) {
    case 'comfortably_ahead': return 'badge-healthy'
    case 'approaching': return 'badge-warn'
    case 'late': return 'badge-error'
    case 'missed': return 'badge-error'
  }
}

function renderReadinessLabel(status: import('@/api/types').ProductionStatus | null): string {
  if (status === 'ready') return 'Rendered and validated'
  if (status === 'producing') return 'Not yet rendered — production in progress'
  if (status === 'failed') return 'Not rendered — production failed'
  return 'Not yet started'
}

function preflightStatusLabel(status: import('@/api/types').ProductionStatus | null): string {
  if (status === 'ready') return 'Passed — cleared for its publishing slot'
  if (status === 'failed') return 'Not yet passed'
  return 'Pending'
}

function AutomationPolicyEditModal({ workspaceId, channelId, current, open, onClose }: {
  workspaceId: string
  channelId: string
  current: import('@/api/types').AutonomyPolicy | null
  open: boolean
  onClose: () => void
}) {
  const [enabled, setEnabled] = useState(current?.decision_automation_enabled ?? false)
  const [productionEnabled, setProductionEnabled] = useState(current?.production_automation_enabled ?? false)
  const [cadence, setCadence] = useState<CadenceType>(current?.cadence_type ?? 'daily')
  const [intervalDays, setIntervalDays] = useState(current?.cadence_interval_days ?? 3)
  const [timezone, setTimezone] = useState(current?.timezone ?? '')
  const [preferredHour, setPreferredHour] = useState(current?.preferred_local_hour ?? 9)
  const [queueTarget, setQueueTarget] = useState(current?.queue_target ?? 1)
  const [apiError, setApiError] = useState<string | null>(null)

  const update = useUpdateAutomationPolicy(workspaceId, channelId)

  function reset() {
    setEnabled(current?.decision_automation_enabled ?? false)
    setProductionEnabled(current?.production_automation_enabled ?? false)
    setCadence(current?.cadence_type ?? 'daily')
    setIntervalDays(current?.cadence_interval_days ?? 3)
    setTimezone(current?.timezone ?? '')
    setPreferredHour(current?.preferred_local_hour ?? 9)
    setQueueTarget(current?.queue_target ?? 1)
    setApiError(null)
  }

  function handleClose() {
    reset()
    onClose()
  }

  const timezoneMissing = enabled && timezone.trim() === ''
  const isValid = !timezoneMissing

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await update.mutateAsync({
        decision_automation_enabled: enabled,
        production_automation_enabled: productionEnabled,
        cadence_type: cadence,
        cadence_interval_days: cadence === 'every_n_days' ? intervalDays : null,
        timezone: timezone.trim() || undefined,
        preferred_local_hour: preferredHour,
        queue_target: queueTarget,
      })
      onClose()
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to save automation policy')
    }
  }

  return (
    <Modal
      open={open}
      title="Automation & Publishing Policy"
      onClose={handleClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={update.isPending}>Cancel</button>
          <button
            type="submit"
            form="automation-policy-edit-form"
            className="btn btn-primary"
            disabled={!isValid || update.isPending}
          >{update.isPending ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      <form id="automation-policy-edit-form" onSubmit={handleSubmit}>
        <p className="text-sm text-secondary mb-4">
          This controls DECISION automation only — what the channel plans and queues next.
          It never authorizes public publishing, which stays a separate, still-disabled control.
        </p>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
            Decision automation enabled
          </label>
          <p className="form-hint">When enabled, the channel autonomously plans and queues its next candidate on the cadence below. It never uploads or publishes anything.</p>
        </div>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
            <input type="checkbox" checked={productionEnabled} onChange={e => setProductionEnabled(e.target.checked)} />
            Production automation enabled
          </label>
          <p className="form-hint">
            When enabled, a filled slot is carried through script, narration, captions, visuals, and rendering
            automatically, spending configured AI/TTS/visual resources. It stops at a fully validated, ready-to-publish
            video — it can never upload or make anything public on its own.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="automation-cadence" className="form-label">Publishing cadence</label>
          <select
            id="automation-cadence"
            className="form-input"
            value={cadence}
            onChange={e => setCadence(e.target.value as CadenceType)}
          >
            {(Object.keys(CADENCE_LABELS) as CadenceType[])
              .filter(c => c !== 'custom_cron')
              .map(c => <option key={c} value={c}>{CADENCE_LABELS[c]}</option>)}
          </select>
        </div>

        {cadence === 'every_n_days' && (
          <div className="form-group">
            <label htmlFor="automation-interval-days" className="form-label">Interval (days)</label>
            <input
              id="automation-interval-days"
              className="form-input"
              type="number"
              min={2}
              max={30}
              value={intervalDays}
              onChange={e => setIntervalDays(parseInt(e.target.value, 10) || 2)}
            />
          </div>
        )}

        <div className="form-group">
          <label htmlFor="automation-timezone" className="form-label">Timezone</label>
          <input
            id="automation-timezone"
            className="form-input"
            type="text"
            placeholder="e.g. America/New_York"
            value={timezone}
            onChange={e => setTimezone(e.target.value)}
          />
          <p className="form-hint">
            An IANA timezone name. Required before decision automation can be enabled — publishing slots are
            reserved in this timezone, not silently assumed as UTC or the operator's own machine timezone.
          </p>
          {timezoneMissing && (
            <div role="alert" className="form-error">A timezone is required to enable decision automation.</div>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="automation-preferred-hour" className="form-label">Preferred local hour</label>
          <input
            id="automation-preferred-hour"
            className="form-input"
            type="number"
            min={0}
            max={23}
            value={preferredHour}
            onChange={e => setPreferredHour(parseInt(e.target.value, 10) || 0)}
          />
          <p className="form-hint">The local hour (0–23) each publishing slot targets.</p>
        </div>

        <div className="form-group">
          <label htmlFor="automation-queue-target" className="form-label">Queue target</label>
          <select
            id="automation-queue-target"
            className="form-input"
            value={queueTarget}
            onChange={e => setQueueTarget(parseInt(e.target.value, 10))}
          >
            <option value={1}>1 — conservative (recommended)</option>
            <option value={2}>2 — one filled, one in progress</option>
          </select>
          <p className="form-hint">How many upcoming slots the system keeps filled at once. Not a production backlog — the system keeps learning between publications.</p>
        </div>

        {apiError && <div role="alert" className="form-error">{apiError}</div>}
      </form>
    </Modal>
  )
}

const PUBLISHING_BLOCK_LABELS: Record<string, string> = {
  global_publishing_gate_off: 'System-wide publishing is switched off',
  global_release_gate_off: 'System-wide public release is switched off',
  channel_not_authorized: 'This channel is not authorized to publish',
  rate_limit_reached: 'Daily publication limit already reached',
  account_unhealthy: 'The YouTube account needs attention',
  no_account: 'No YouTube account is connected',
  release_scope_missing:
    'The YouTube account cannot yet make videos public — grant "Make videos public" above',
}

/** The three-state automation model's third state, given the weight it deserves.

    Deliberately not a checkbox in the automation-policy form: authorizing a
    channel to publish publicly without per-video review is a different kind of
    decision from setting a cadence, and the UI should say so. */
function PublishingAuthorizationCard({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const auth = useChannelPublishingAuthorization(workspaceId, channelId)
  const update = useUpdatePublishingAuthorization(workspaceId, channelId)
  const [showConfirm, setShowConfirm] = useState(false)

  const decision = auth.data?.decision
  const record = auth.data?.authorization ?? null
  const authorized = record?.authorized ?? false

  async function handleRevoke() {
    await update.mutateAsync({ authorized: false, reason: 'Revoked from the channel page' })
  }

  return (
    <>
      <div
        className="card"
        style={{
          marginTop: 'var(--sp-3)',
          borderColor: authorized ? 'var(--status-warn)' : 'var(--border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 'var(--sp-3)' }}>
          <div>
            <p style={{ fontWeight: 600, margin: 0 }}>Public publishing authorization</p>
            <p className="text-sm text-secondary" style={{ margin: '2px 0 0' }}>
              Separate from both decision and production automation above. While this is off, the channel keeps
              producing finished videos and simply holds them — nothing is ever uploaded or made public.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flexShrink: 0 }}>
            <span className={`badge ${authorized ? 'badge-warn' : 'badge-neutral'}`}>
              {authorized ? 'Authorized' : 'Not authorized'}
            </span>
            {authorized ? (
              <button className="btn btn-secondary btn-sm" onClick={handleRevoke} disabled={update.isPending}>
                {update.isPending ? 'Turning off…' : 'Turn off'}
              </button>
            ) : (
              <button className="btn btn-secondary btn-sm" onClick={() => setShowConfirm(true)}>
                Authorize…
              </button>
            )}
          </div>
        </div>

        {auth.isLoading || !decision ? null : (
          <div style={{ marginTop: 'var(--sp-3)' }}>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Can publish right now</span>
              <span className="detail-meta-value">
                <span className={`badge ${decision.allowed ? 'badge-healthy' : 'badge-neutral'}`}>
                  {decision.allowed ? 'Yes' : 'No'}
                </span>
              </span>
            </div>
            {!decision.allowed && decision.blocked_by.length > 0 && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Waiting on</span>
                <span className="detail-meta-value">
                  {decision.blocked_by.map(r => PUBLISHING_BLOCK_LABELS[r] ?? r).join(' · ')}
                </span>
              </div>
            )}
            <div className="detail-meta-row">
              <span className="detail-meta-label">System-wide publishing switch</span>
              <span className="detail-meta-value">
                {decision.global_publishing_enabled && decision.global_release_enabled
                  ? 'On'
                  : 'Off — nothing publishes on any channel'}
              </span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Published in the last 24 hours</span>
              <span className="detail-meta-value">
                {decision.publications_last_24h} of {decision.max_publications_per_24h} allowed
              </span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">YouTube account</span>
              <span className="detail-meta-value">
                {decision.account_status ? humanizeStatus(decision.account_status) : 'Not connected'}
              </span>
            </div>
            {record && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Late-publication grace window</span>
                <span className="detail-meta-value">
                  {record.missed_slot_grace_minutes} minutes — a slot missed by more than this waits for
                  rescheduling rather than publishing late
                </span>
              </div>
            )}
            {record?.authorized && record.authorized_at && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Authorized</span>
                <span className="detail-meta-value">
                  by {record.authorized_by} · <LocalTime value={record.authorized_at} variant="relative" />
                </span>
              </div>
            )}
            {!record?.authorized && record?.revoked_at && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Last turned off</span>
                <span className="detail-meta-value">
                  by {record.revoked_by} · <LocalTime value={record.revoked_at} variant="relative" />
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      <PublishingAuthorizationConfirmModal
        open={showConfirm}
        onClose={() => setShowConfirm(false)}
        pending={update.isPending}
        currentLimit={record?.max_publications_per_24h ?? 1}
        onConfirm={async (limit) => {
          await update.mutateAsync({
            authorized: true,
            confirm: true,
            max_publications_per_24h: limit,
          })
          setShowConfirm(false)
        }}
      />
    </>
  )
}

/** Deliberate confirmation, not a casual toggle. States plainly what changes,
    and requires the operator to type the channel-agnostic confirmation word so
    the action cannot be completed by a stray click. */
function PublishingAuthorizationConfirmModal({
  open, onClose, onConfirm, pending, currentLimit,
}: {
  open: boolean
  onClose: () => void
  onConfirm: (limit: number) => Promise<void>
  pending: boolean
  currentLimit: number
}) {
  const [typed, setTyped] = useState('')
  const [limit, setLimit] = useState(currentLimit)
  const [apiError, setApiError] = useState<string | null>(null)

  const confirmed = typed.trim().toUpperCase() === 'AUTHORIZE'

  function handleClose() {
    setTyped('')
    setLimit(currentLimit)
    setApiError(null)
    onClose()
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await onConfirm(limit)
      setTyped('')
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to authorize publishing')
    }
  }

  return (
    <Modal
      open={open}
      title="Authorize public publishing"
      onClose={handleClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={pending}>
            Cancel
          </button>
          <button
            type="submit"
            form="publishing-authorization-form"
            className="btn btn-primary"
            disabled={!confirmed || pending}
          >
            {pending ? 'Authorizing…' : 'Authorize publishing'}
          </button>
        </>
      }
    >
      <form id="publishing-authorization-form" onSubmit={handleSubmit}>
        <div className="card" style={{ borderColor: 'var(--status-warn)', marginBottom: 'var(--sp-4)' }}>
          <p style={{ margin: 0, fontWeight: 600 }}>This lets the channel publish videos to the public without asking first.</p>
          <p className="text-sm text-secondary" style={{ margin: 'var(--sp-2) 0 0' }}>
            Once this is on, a finished video will be uploaded and made public at its scheduled time with no further
            review of that specific video. You can turn it off again at any moment, and doing so stops anything that
            has not already gone public.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="publishing-limit" className="form-label">Most videos it may publish per day</label>
          <select
            id="publishing-limit"
            className="form-input"
            value={limit}
            onChange={e => setLimit(parseInt(e.target.value, 10))}
          >
            <option value={1}>1 — recommended</option>
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
          <p className="form-hint">
            A hard safety ceiling, independent of the publishing cadence. The channel refuses to publish beyond it
            even if something upstream asks it to.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="publishing-confirm-text" className="form-label">
            Type AUTHORIZE to confirm
          </label>
          <input
            id="publishing-confirm-text"
            className="form-input"
            type="text"
            value={typed}
            onChange={e => setTyped(e.target.value)}
            autoComplete="off"
          />
        </div>

        {apiError && <div role="alert" className="form-error">{apiError}</div>}
      </form>
    </Modal>
  )
}

function AutomationPolicySection({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const automation = useChannelAutomationPolicy(workspaceId, channelId)
  const [showEdit, setShowEdit] = useState(false)

  const policy = automation.data?.policy ?? null
  const slots = automation.data?.active_slots ?? []
  const nextSlot = slots.find(s => s.state === 'reserved' || s.state === 'filled')
  const filledCount = slots.filter(s => s.state === 'filled').length

  return (
    <section className="section">
      <SectionHeader
        title="Automation & Publishing Policy"
        description="What the channel is trying to learn, what it intends to produce next, and when — decision automation only."
        actions={
          <button className="btn btn-secondary btn-sm" onClick={() => setShowEdit(true)}>
            {policy ? 'Edit automation policy' : 'Set up automation'}
          </button>
        }
      />
      {automation.isLoading ? <LoadingState /> : !policy ? (
        <EmptyState
          icon="🤖"
          title="Decision automation is not configured for this channel"
          description="Set it up to let the channel autonomously plan and queue its next candidate on a cadence — public publishing stays separately disabled either way."
        />
      ) : (
        <>
          <div className="metric-grid">
            <MetricCard
              label="Decision automation"
              value={policy.decision_automation_enabled ? 'Enabled' : 'Paused'}
              sub={policy.decision_automation_enabled ? 'planning and queueing autonomously' : 'not currently running'}
            />
            <MetricCard
              label="Production automation"
              value={policy.production_automation_enabled ? 'Enabled' : 'Paused'}
              sub={policy.production_automation_enabled ? 'generating and rendering queued slots' : 'queued slots wait for an operator'}
            />
            <MetricCard
              label="Publishing cadence"
              value={CADENCE_LABELS[policy.cadence_type]}
              sub={policy.timezone ?? 'timezone not set'}
            />
            <MetricCard
              label="Queue depth"
              value={`${filledCount} / ${policy.queue_target}`}
              sub="filled / target"
            />
          </div>

          <div className="card" style={{ marginTop: 'var(--sp-4)' }}>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Next planned slot</span>
              <span className="detail-meta-value">
                {nextSlot ? <LocalTime value={nextSlot.scheduled_for_utc} variant="relative" /> : 'None reserved yet'}
              </span>
            </div>
            {nextSlot && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Slot status</span>
                <span className="detail-meta-value">{slotStateLabel(nextSlot.state)}</span>
              </div>
            )}
            <div className="detail-meta-row">
              <span className="detail-meta-label">Queued candidate</span>
              <span className="detail-meta-value">
                {nextSlot?.opportunity_id != null
                  ? `Opportunity #${nextSlot.opportunity_id}`
                  : 'Not yet selected'}
              </span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Last autonomous decision</span>
              <span className="detail-meta-value">
                {policy.last_decision_at
                  ? <>{humanizeStatus(policy.last_decision_outcome ?? '')} · <LocalTime value={policy.last_decision_at} variant="relative" /></>
                  : 'No decision cycle has run yet'}
              </span>
            </div>
          </div>

          {nextSlot && (nextSlot.production_status || nextSlot.state === 'filled') && (
            <div className="card" style={{ marginTop: 'var(--sp-3)' }}>
              <div className="detail-meta-row">
                <span className="detail-meta-label">Production stage</span>
                <span className="detail-meta-value">
                  <span className={`badge ${productionStageBadgeClass(nextSlot.production_status)}`}>
                    {productionStageLabel(nextSlot.production_status)}
                  </span>
                </span>
              </div>
              {nextSlot.experiment_id && (
                <div className="detail-meta-row">
                  <span className="detail-meta-label">Experiment</span>
                  <span className="detail-meta-value"><code>{nextSlot.experiment_id}</code></span>
                </div>
              )}
              {nextSlot.production_pipeline_id && (
                <div className="detail-meta-row">
                  <span className="detail-meta-label">Production pipeline</span>
                  <span className="detail-meta-value">
                    <code>{nextSlot.production_pipeline_id}</code> — find it on the Pipelines page for full stage-by-stage detail
                  </span>
                </div>
              )}
              <div className="detail-meta-row">
                <span className="detail-meta-label">Reserved publishing slot</span>
                <span className="detail-meta-value"><LocalTime value={nextSlot.scheduled_for_utc} variant="relative" /></span>
              </div>
              <div className="detail-meta-row">
                <span className="detail-meta-label">Deadline status</span>
                <span className="detail-meta-value">
                  {(() => {
                    const ds = computeDeadlineStatus(nextSlot.scheduled_for_utc)
                    return (
                      <span className={`badge ${deadlineStatusBadgeClass(ds)}`}>{deadlineStatusLabel(ds)}</span>
                    )
                  })()}
                </span>
              </div>
              <div className="detail-meta-row">
                <span className="detail-meta-label">Render readiness</span>
                <span className="detail-meta-value">{renderReadinessLabel(nextSlot.production_status)}</span>
              </div>
              <div className="detail-meta-row">
                <span className="detail-meta-label">Preflight status</span>
                <span className="detail-meta-value">{preflightStatusLabel(nextSlot.production_status)}</span>
              </div>
              <div className="detail-meta-row">
                <span className="detail-meta-label">Last production failure</span>
                <span className="detail-meta-value">
                  {nextSlot.production_failed_at ? (
                    <>
                      {nextSlot.production_failed_stage ? humanizeStatus(nextSlot.production_failed_stage) : 'Unknown stage'}
                      {' · '}
                      <LocalTime value={nextSlot.production_failed_at} variant="relative" />
                      {nextSlot.production_error ? ` — ${nextSlot.production_error}` : ''}
                    </>
                  ) : 'No production failures'}
                </span>
              </div>
            </div>
          )}

          <PublishingAuthorizationCard workspaceId={workspaceId} channelId={channelId} />
        </>
      )}
      <AutomationPolicyEditModal
        workspaceId={workspaceId}
        channelId={channelId}
        current={policy}
        open={showEdit}
        onClose={() => setShowEdit(false)}
      />
    </section>
  )
}

const READINESS_STATUS_COLOR: Record<import('@/api/types').ReadinessStatus, string> = {
  ready: 'var(--status-healthy)',
  degraded: 'var(--status-warn)',
  blocked: 'var(--status-error)',
}

const READINESS_STATUS_LABEL: Record<import('@/api/types').ReadinessStatus, string> = {
  ready: 'Ready',
  degraded: 'Degraded',
  blocked: 'Blocked',
}

function ReadinessDot({ status }: { status: import('@/api/types').ReadinessStatus }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
        marginRight: 'var(--sp-2)',
        background: READINESS_STATUS_COLOR[status] ?? 'var(--status-neutral)',
      }}
    />
  )
}

function ReadinessSection({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const readiness = useChannelReadiness(workspaceId, channelId)
  const view = readiness.data

  return (
    <section className="section">
      <SectionHeader
        title="Autonomy readiness"
        description="What this channel can actually do right now, by area. Decision, production, analytics and provider readiness are tracked separately from publishing authorization — a fully working pipeline never implies permission to publish."
      />
      {readiness.isLoading || !view ? <LoadingState /> : (
        <>
          <div className="metric-grid">
            <MetricCard
              label="Decision automation"
              value={view.ready_for_decision_automation ? 'Ready' : 'Not ready'}
              sub="pipeline has real input to work with"
            />
            <MetricCard
              label="Public publishing"
              value={view.authorized_for_public_publishing ? 'Authorized' : 'Not authorized'}
              sub={view.authorized_for_public_publishing
                ? 'gates on, channel granted, account and scopes healthy'
                : 'needs both global gates, a channel grant, and a healthy account'}
            />
            <MetricCard
              label="Overall"
              value={READINESS_STATUS_LABEL[view.overall_status] ?? 'Unknown'}
              sub="worst status across every area"
            />
          </div>
          {(view.categories ?? []).map(category => (
            <div key={category.key} className="card" style={{ marginTop: 'var(--sp-4)' }}>
              <div className="detail-meta-row">
                <span className="detail-meta-label">
                  <ReadinessDot status={category.status} />
                  <strong>{category.label}</strong>
                </span>
                <span className="detail-meta-value">
                  {READINESS_STATUS_LABEL[category.status] ?? category.status}
                </span>
              </div>
              {view.checks.filter(check => check.category === category.key).map(check => (
                <div key={check.key} className="detail-meta-row">
                  <span className="detail-meta-label" style={{ paddingLeft: 'var(--sp-4)' }}>
                    <ReadinessDot status={check.status} />
                    {check.label}
                  </span>
                  <span className="detail-meta-value">{check.detail}</span>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </section>
  )
}

function StrategySection({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const strategy = useChannelStrategy(workspaceId, channelId)
  const [showEdit, setShowEdit] = useState(false)

  const response = strategy.data
  const profile = response?.profile ?? null
  const effective = response?.effective ?? null
  const config: StrategyConfig | null = profile ? JSON.parse(profile.config_json) : null

  return (
    <section className="section">
      <SectionHeader
        title="Strategy"
        description="How this channel decides what to explore next — weighted between live market intelligence and its own performance evidence."
        actions={
          <button className="btn btn-secondary btn-sm" onClick={() => setShowEdit(true)}>
            {profile ? 'Edit strategy' : 'Set up strategy'}
          </button>
        }
      />
      {strategy.isLoading ? <LoadingState /> :
       !profile || !config || !effective ? (
        <EmptyState
          icon="🧭"
          title="No strategy profile assigned to this channel yet"
          description="Set one up to define how this channel balances exploring new market opportunities against relying on its own performance evidence."
        />
      ) : (
        <>
          <div className="metric-grid">
            <MetricCard
              label="Mode"
              value={regimeLabel(effective.effective_regime)}
              sub={effective.effective_regime === 'bootstrap' ? 'still exploring broadly' : 'leaning on channel evidence'}
            />
            <MetricCard
              label="Exploration progress"
              value={`${effective.publication_count} / ${config.bootstrap.target_publication_count}`}
              sub="publications observed"
            />
            <MetricCard
              label="Evidence maturity"
              value={maturityLabel(effective.current_maturity)}
              sub={`for ${effective.trigger_metric.replace(/_/g, ' ')}`}
            />
            <MetricCard
              label="Current weighting"
              value={weightSplitLabel(effective.market_intelligence_weight, effective.channel_evidence_weight)}
            />
          </div>

          <div className="card" style={{ marginTop: 'var(--sp-4)' }}>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Diversity rule</span>
              <span className="detail-meta-value">
                Max {formatPercent(config.diversity.max_cluster_share * 100, 0)} of the portfolio from one cluster, at most {config.diversity.max_consecutive_same_cluster} in a row
              </span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Transitions to steady state at</span>
              <span className="detail-meta-value">{maturityLabel(config.transition.maturity_threshold)} evidence</span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Steady-state exploration share</span>
              <span className="detail-meta-value">{formatPercent(config.steady_state.exploration_share * 100, 0)} — retained even after winners emerge</span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Content constraints</span>
              <span className="detail-meta-value">Managed by the channel's content profile, not this strategy</span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Version</span>
              <span className="detail-meta-value">v{profile.version} · <LocalTime value={profile.created_at} variant="relative" /></span>
            </div>
          </div>

          <div style={{ marginTop: 'var(--sp-3)' }}>
            <p className="detail-meta-label" style={{ marginBottom: 'var(--sp-2)' }}>Creative dimensions explored</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sp-2)' }}>
              {config.creative_dimensions.map(dim => (
                <span key={dim} className="tag">{creativeDimensionLabel(dim)}</span>
              ))}
            </div>
          </div>

          <TechnicalDetails summary={`Strategy configuration (v${profile.version})`} data={config} />
        </>
      )}
      <StrategyEditModal
        workspaceId={workspaceId}
        channelId={channelId}
        current={config}
        open={showEdit}
        onClose={() => setShowEdit(false)}
      />
    </section>
  )
}

export function Channels() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const wid = workspaceId ?? ''

  const [showCreate, setShowCreate] = useState(false)
  const [showAddAccount, setShowAddAccount] = useState(false)
  const [oauthBanner, setOauthBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const { channel, primaryAccount, isLoading: currentLoading } = useCurrentChannel(wid)
  const { data: channels } = useChannels(wid)
  const accounts = useChannelAccounts(wid, channel?.id ?? '')

  // Handle OAuth callback result embedded in query params
  useEffect(() => {
    const oauthSuccess = searchParams.get('oauth_success')
    const oauthError = searchParams.get('oauth_error')

    if (oauthSuccess === 'true') {
      setOauthBanner({ type: 'success', message: 'YouTube account connected successfully.' })
      const next = new URLSearchParams(searchParams)
      next.delete('oauth_success'); next.delete('account_id')
      setSearchParams(next, { replace: true })
    } else if (oauthError) {
      const messages: Record<string, string> = {
        state_expired: 'OAuth session expired. Please try again.',
        exchange_failed: 'Google code exchange failed. Please try again.',
        no_youtube_channel: 'No YouTube channel found for this Google account.',
        channel_mismatch: 'This Google account is linked to a different YouTube channel.',
        access_denied: 'Access was denied. Please authorize the app in Google.',
        missing_params: 'OAuth callback was incomplete. Please try again.',
        not_configured: 'OAuth is not configured on the server.',
        internal_error: 'An unexpected error occurred. Please try again.',
      }
      setOauthBanner({
        type: 'error',
        message: messages[oauthError] ?? `OAuth error: ${oauthError}`,
      })
      const next = new URLSearchParams(searchParams)
      next.delete('oauth_error')
      setSearchParams(next, { replace: true })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="📡" title="No workspace selected" />
    </div>
  )

  if (currentLoading) return <LoadingState message="Loading channel…" />

  if (!channel) {
    return (
      <>
        <PageHeader
          title="Channel"
          subtitle="No channel configured yet"
          actions={
            <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
              New channel
            </button>
          }
        />
        <div className="page-body">
          <EmptyState
            icon="📡"
            title="No channel yet"
            description="Create a channel to start publishing content."
            action={
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                New channel
              </button>
            }
          />
          <CreateChannelModal workspaceId={wid} open={showCreate} onClose={() => setShowCreate(false)} />
        </div>
      </>
    )
  }

  const channelName = primaryAccount?.display_name || channel.name
  const youtubeAccounts = (accounts.data ?? []).filter(a => a.platform_key === 'youtube')
  const otherAccounts = (accounts.data ?? []).filter(a => a.platform_key !== 'youtube')

  return (
    <>
      <PageHeader
        title={channelName}
        subtitle={
          primaryAccount
            ? `YouTube · ${humanizeStatus(primaryAccount.status)}`
            : channel.description ?? 'Channel'
        }
        actions={<StatusBadge status={channel.status} />}
      />

      {oauthBanner && (
        <div
          role="alert"
          className={`attention-item attention-${oauthBanner.type === 'success' ? 'info' : 'error'}`}
          style={{ margin: '0 var(--sp-8) var(--sp-3)', borderRadius: 'var(--radius-md)', justifyContent: 'space-between' }}
        >
          <span>{oauthBanner.message}</span>
          <button
            aria-label="Dismiss"
            onClick={() => setOauthBanner(null)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '0 4px', color: 'inherit' }}
          >✕</button>
        </div>
      )}

      <div className="page-body">
        {/* Overview */}
        <section className="section">
          <div className="metric-grid">
            <MetricCard label="Platform" value="YouTube" sub={primaryAccount ? primaryAccount.display_name : 'Not connected'} />
            <MetricCard label="Connection" value={humanizeStatus(primaryAccount?.status ?? 'disconnected')} />
            <MetricCard label="Accounts" value={String(accounts.data?.length ?? 0)} sub="platform accounts" />
          </div>
        </section>

        {/* YouTube Accounts */}
        <section className="section">
          <SectionHeader
            title="YouTube accounts"
            actions={
              <button className="btn btn-ghost btn-sm" onClick={() => setShowAddAccount(true)}>
                Add account
              </button>
            }
          />
          {youtubeAccounts.length === 0 ? (
            <EmptyState
              icon="🔌"
              title="No YouTube account connected"
              description="Register a YouTube account to enable publishing and analytics."
              action={
                <button className="btn btn-secondary" onClick={() => setShowAddAccount(true)}>
                  Add account
                </button>
              }
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
              {youtubeAccounts.map(a => (
                <YouTubeAccountCard
                  key={a.id}
                  workspaceId={wid}
                  channelId={channel.id}
                  account={a}
                />
              ))}
            </div>
          )}
        </section>

        {/* Other platform accounts */}
        {otherAccounts.length > 0 && (
          <section className="section">
            <SectionHeader title="Other accounts" />
            <div className="card">
              {otherAccounts.map(a => (
                <div key={a.id} className="detail-meta-row">
                  <span className="detail-meta-label">{a.platform_key}</span>
                  <span className="detail-meta-value">{a.display_name} · <StatusBadge status={a.status} /></span>
                </div>
              ))}
            </div>
          </section>
        )}

        <StrategySection workspaceId={wid} channelId={channel.id} />

        <AutomationPolicySection workspaceId={wid} channelId={channel.id} />

        <ReadinessSection workspaceId={wid} channelId={channel.id} />

        {/* Technical Details — channel identifiers */}
        <TechnicalDetails summary="Channel identifiers">
          <div className="detail-meta-list">
            <div className="detail-meta-row">
              <span className="detail-meta-label">Channel name</span>
              <span className="detail-meta-value">{channel.name}</span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Slug</span>
              <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>/{channel.slug}</span>
            </div>
            <div className="detail-meta-row">
              <span className="detail-meta-label">Channel ID</span>
              <span className="detail-meta-value" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--font-size-xs)' }}>{channel.id}</span>
            </div>
            {(channels?.length ?? 0) > 1 && (
              <div className="detail-meta-row">
                <span className="detail-meta-label">Total channels</span>
                <span className="detail-meta-value">{channels!.length} in this workspace</span>
              </div>
            )}
          </div>
        </TechnicalDetails>

        <AddPlatformAccountModal
          workspaceId={wid}
          channelId={channel.id}
          open={showAddAccount}
          onClose={() => setShowAddAccount(false)}
        />
        <CreateChannelModal workspaceId={wid} open={showCreate} onClose={() => setShowCreate(false)} />
      </div>
    </>
  )
}
