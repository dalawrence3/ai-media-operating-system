/* Phase 18E — visual quality surface for one produced video.

   Deliberately compact and embedded in the existing Content detail page
   rather than given a page of its own: an operator asks "why does this video
   look like that?" while looking at the video, not from a separate section of
   the app.

   The panel leads with the two numbers that decide the verdict (meaningful vs
   text-card runtime), and keeps the per-scene planned/realized breakdown
   collapsed. That breakdown is the answer to the question the summary raises —
   "the planner wanted imagery here, so what happened?" — and belongs one click
   away, not in the default view. */

import { useState } from 'react'
import type {
  PublicationVisualQuality,
  VisualQualityStatus,
  VisualSceneDiagnostic,
} from '@/api/types'

const FAMILY_LABELS: Record<string, string> = {
  motion_footage: 'Motion footage',
  photographic: 'Photography',
  illustration: 'Illustration',
  generated_diagram: 'Generated diagram',
  text_card: 'Text card',
  unresolved: 'Unresolved',
}

const STATUS_LABELS: Record<VisualQualityStatus, string> = {
  pass: 'Pass',
  pass_with_warnings: 'Pass with warnings',
  blocked: 'Blocked',
}

const STATUS_COLORS: Record<VisualQualityStatus, string> = {
  pass: 'var(--status-success, #16a34a)',
  pass_with_warnings: 'var(--status-warning, #d97706)',
  blocked: 'var(--status-error, #dc2626)',
}

function familyLabel(family: string): string {
  return FAMILY_LABELS[family] ?? family
}

function pct(value: number | undefined): string {
  return value === undefined ? '—' : `${Math.round(value * 100)}%`
}

function secs(ms: number | undefined): string {
  return ms === undefined ? '—' : `${(ms / 1000).toFixed(1)}s`
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card" style={{ padding: 'var(--sp-3)' }}>
      <div className="detail-meta-label" style={{ fontSize: '0.7rem' }}>{label}</div>
      <div style={{ fontSize: '1.25rem', fontWeight: 600, marginTop: '2px' }}>{value}</div>
      {hint && (
        <div className="text-secondary" style={{ fontSize: '0.7rem', marginTop: '2px' }}>{hint}</div>
      )}
    </div>
  )
}

function SceneRow({ scene }: { scene: VisualSceneDiagnostic }) {
  // A creative fallback is a decision and reads as neutral; only a provider
  // fallback is a fault, and only that one is coloured as one.
  const isFault = scene.fallback_class === 'provider' && !scene.meaningful
  return (
    <tr data-testid="visual-scene-row">
      <td style={{ whiteSpace: 'nowrap' }}>{scene.scene_index + 1}.{scene.beat_index}</td>
      <td style={{ whiteSpace: 'nowrap' }}>{secs(scene.start_ms)}</td>
      <td>{scene.visual_intent}</td>
      <td>{familyLabel(scene.planned)}</td>
      <td style={{ color: isFault ? 'var(--status-error, #dc2626)' : undefined }}>
        {familyLabel(scene.realized)}
      </td>
      <td className="text-secondary" style={{ fontSize: '0.75rem' }}>
        {scene.fallback_class === 'provider' && scene.fallback_reason}
        {scene.fallback_class === 'creative' && 'by design'}
      </td>
    </tr>
  )
}

export function VisualQualityPanel({ data }: { data: PublicationVisualQuality }) {
  const [showScenes, setShowScenes] = useState(false)

  if (!data.assessed) {
    return (
      <div className="card">
        <p className="text-sm text-secondary" style={{ margin: 0 }}>
          This video was produced before visual quality assessment existed, so its
          composition was never measured.
        </p>
      </div>
    )
  }

  const status = (data.status ?? 'pass') as VisualQualityStatus
  const findings = data.findings ?? []
  const blocking = findings.filter(f => f.severity === 'blocking')
  const warnings = findings.filter(f => f.severity === 'warning')
  const families = (data.family_distribution ?? []).filter(f => f.runtime_ms > 0)

  return (
    <div data-testid="visual-quality-panel">
      <div
        className="card"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--sp-3)',
          flexWrap: 'wrap',
          borderLeft: `3px solid ${STATUS_COLORS[status]}`,
        }}
      >
        <div>
          <span style={{ fontWeight: 600, color: STATUS_COLORS[status] }} data-testid="visual-quality-status">
            {STATUS_LABELS[status]}
          </span>
          <span className="text-secondary" style={{ fontSize: '0.8rem', marginLeft: 'var(--sp-2)' }}>
            {data.meaningful_beat_count} of {data.total_beat_count} visuals carry meaning
            {data.visual_style ? ` · ${data.visual_style} treatment` : ''}
            {data.remediated ? ' · remediated' : ''}
          </span>
        </div>
      </div>

      {(blocking.length > 0 || warnings.length > 0) && (
        <ul style={{ listStyle: 'none', padding: 0, margin: 'var(--sp-3) 0 0' }}>
          {[...blocking, ...warnings].map(finding => (
            <li
              key={finding.code}
              data-testid={`visual-finding-${finding.severity}`}
              style={{
                fontSize: '0.8rem',
                padding: 'var(--sp-2) var(--sp-3)',
                marginBottom: '4px',
                borderRadius: '4px',
                background: 'var(--bg-tertiary, var(--bg-secondary))',
                color:
                  finding.severity === 'blocking'
                    ? 'var(--status-error, #dc2626)'
                    : 'var(--text-secondary)',
              }}
            >
              {finding.message}
            </li>
          ))}
        </ul>
      )}

      <div className="metric-grid" style={{ marginTop: 'var(--sp-3)' }}>
        <Stat label="Meaningful runtime" value={pct(data.meaningful_runtime_pct)} />
        <Stat label="Text / fallback runtime" value={pct(data.text_card_runtime_pct)} />
        <Stat
          label="Visual changes"
          value={`${(data.visual_changes_per_minute ?? 0).toFixed(1)}/min`}
        />
        <Stat
          label="Distinct assets"
          value={String(data.distinct_asset_count ?? 0)}
          hint={`${pct(data.asset_reuse_ratio)} reused`}
        />
        <Stat
          label="Longest visual gap"
          value={secs(data.max_meaningful_gap_ms)}
          hint={`avg ${secs(data.avg_meaningful_gap_ms)}`}
        />
        <Stat
          label="Retrieval fallbacks"
          value={String(data.provider_fallback_beats ?? 0)}
          hint={`${data.creative_fallback_beats ?? 0} by design`}
        />
      </div>

      {families.length > 0 && (
        <div className="card" style={{ marginTop: 'var(--sp-3)' }}>
          <div className="detail-meta-label" style={{ marginBottom: 'var(--sp-2)' }}>
            Visual families
          </div>
          {families.map(family => (
            <div key={family.family} className="detail-meta-row">
              <span className="detail-meta-label">{familyLabel(family.family)}</span>
              <span className="detail-meta-value">
                {pct(family.runtime_pct)} · {family.beat_count} visual
                {family.beat_count === 1 ? '' : 's'}
              </span>
            </div>
          ))}
          <div className="detail-meta-row">
            <span className="detail-meta-label">Opening visual</span>
            <span className="detail-meta-value">
              {data.opening_meaningful_visual ? 'Meaningful' : 'Text only'}
            </span>
          </div>
        </div>
      )}

      {(data.scene_diagnostics?.length ?? 0) > 0 && (
        <div style={{ marginTop: 'var(--sp-3)' }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setShowScenes(v => !v)}
            data-testid="toggle-visual-scenes"
          >
            {showScenes ? 'Hide' : 'Show'} per-scene breakdown ({data.scene_diagnostics!.length})
          </button>
          {showScenes && (
            <div style={{ overflowX: 'auto', marginTop: 'var(--sp-2)' }}>
              <table className="table" style={{ fontSize: '0.8rem', width: '100%' }}>
                <thead>
                  <tr>
                    <th>Scene</th>
                    <th>At</th>
                    <th>Intent</th>
                    <th>Planned</th>
                    <th>Realized</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.scene_diagnostics!.map(scene => (
                    <SceneRow key={scene.beat_index} scene={scene} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
