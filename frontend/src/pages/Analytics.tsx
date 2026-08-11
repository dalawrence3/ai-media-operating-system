/* M14.6 — Analytics Dashboard

   Shows Platform Analytics data from the backend with correct metric semantics.
   IMPORTANT: AGG_LAST metrics (CTR, avg view duration) are NOT shown as sums.
   Mixed currencies are never silently combined.
   Provisional data is labeled as such.
*/

import { useParams } from 'react-router-dom'
import { UnavailableState } from '@/components/common/UnavailableState'
import { useAnalyticsAggregates } from '@/hooks/useAnalytics'
import type { AnalyticsAggregate } from '@/api/types'

// Static metric semantics reference — always visible regardless of data state.
const METRIC_SEMANTICS = [
  ['views',                    'sum',                'Additive across periods'],
  ['impressions',              'sum',                'Additive across periods'],
  ['estimated_minutes_watched','sum',                'Additive watch time'],
  ['revenue_estimate',         'sum',                'Currency required; mixed currencies not combined'],
  ['ctr',                      'latest_observation', 'Gauge — not summed across periods'],
  ['average_view_duration',    'latest_observation', 'Gauge — latest provider reading'],
  ['likes',                    'latest_observation', 'Current count, not incremental'],
  ['subscribers_gained',       'sum',                'Additive subscriber delta'],
] as const

const PERIOD_ORDER: Record<string, number> = {
  lifetime: 0,
  monthly:  1,
  weekly:   2,
  daily:    3,
}

function formatMetricValue(agg: AnalyticsAggregate): string {
  const v = agg.metric_value
  if (agg.metric_name === 'ctr') return `${(v * 100).toFixed(1)}%`
  if (agg.metric_name === 'average_view_duration') {
    const m = Math.floor(v / 60)
    const s = Math.round(v % 60)
    return `${m}m ${s}s`
  }
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return v.toFixed(v % 1 === 0 ? 0 : 2)
}

export function Analytics() {
  const { workspaceId } = useParams<{ workspaceId: string }>()

  if (!workspaceId) {
    return (
      <div className="page-body">
        <UnavailableState
          title="No workspace selected"
          description="Select a workspace to view analytics."
          reason="no_data"
        />
      </div>
    )
  }

  return <AnalyticsContent workspaceId={workspaceId} />
}

function AnalyticsContent({ workspaceId }: { workspaceId: string }) {
  const { data: aggregates, isLoading } = useAnalyticsAggregates(workspaceId)

  const hasData = !isLoading && aggregates && aggregates.length > 0

  // Group by period_type, sorted by PERIOD_ORDER, then by period_key desc within group.
  const grouped = hasData
    ? Object.entries(
        aggregates.reduce<Record<string, AnalyticsAggregate[]>>((acc, agg) => {
          if (!acc[agg.period_type]) acc[agg.period_type] = []
          acc[agg.period_type].push(agg)
          return acc
        }, {}),
      ).sort(([a], [b]) => (PERIOD_ORDER[a] ?? 9) - (PERIOD_ORDER[b] ?? 9))
    : []

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Analytics</h1>
          <p className="page-subtitle">Platform performance metrics — canonical backend data only</p>
        </div>
      </div>

      <div className="page-body">
        {isLoading ? (
          <div className="section">
            <div className="card">
              <p className="text-sm text-secondary">Loading analytics data…</p>
            </div>
          </div>
        ) : hasData ? (
          grouped.map(([periodType, rows]) => {
            const sortedRows = [...rows].sort((a, b) =>
              b.period_key.localeCompare(a.period_key) || a.metric_name.localeCompare(b.metric_name),
            )
            return (
              <div className="section" key={periodType}>
                <div className="section-header">
                  <h2 className="section-title" style={{ textTransform: 'capitalize' }}>
                    {periodType}
                  </h2>
                </div>
                <div className="card">
                  <div className="table-wrapper">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Period</th>
                          <th>Metric</th>
                          <th>Value</th>
                          <th>Method</th>
                          <th>Snapshots</th>
                          <th>Provider</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedRows.map(agg => (
                          <tr key={agg.id}>
                            <td className="font-mono text-sm">{agg.period_key}</td>
                            <td className="font-mono text-sm">{agg.metric_name}</td>
                            <td className="font-mono text-sm" style={{ fontVariantNumeric: 'tabular-nums' }}>
                              {formatMetricValue(agg)}
                              {agg.currency_code ? ` ${agg.currency_code}` : ''}
                            </td>
                            <td>
                              <span
                                className="tag"
                                style={{
                                  background: agg.calculation_method === 'sum'
                                    ? 'var(--status-info-bg)'
                                    : 'var(--status-warn-bg)',
                                  color: agg.calculation_method === 'sum'
                                    ? 'var(--status-info)'
                                    : 'var(--status-warn)',
                                }}
                              >
                                {agg.calculation_method}
                              </span>
                            </td>
                            <td className="text-sm text-secondary">{agg.snapshot_count}</td>
                            <td className="text-sm text-secondary">{agg.provider}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )
          })
        ) : (
          <div className="section">
            <UnavailableState
              title="Analytics integration — Provider setup required"
              description={
                `Platform analytics are collected from YouTube and other providers ` +
                `after publishing is configured and live credentials are set. ` +
                `Ingest analytics data using the 'ace analytics' CLI, then return here to explore.`
              }
              reason="provider_setup_required"
            />
          </div>
        )}

        <div className="section">
          <div className="section-header">
            <h2 className="section-title">Metric Semantics</h2>
          </div>
          <div className="card">
            <p className="text-sm text-secondary mb-4">
              The analytics engine stores metrics with explicit calculation methods to prevent misinterpretation:
            </p>
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Method</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {METRIC_SEMANTICS.map(([metric, method, note]) => (
                    <tr key={metric}>
                      <td className="font-mono text-sm">{metric}</td>
                      <td>
                        <span
                          className="tag"
                          style={{
                            background: method === 'sum'
                              ? 'var(--status-info-bg)'
                              : 'var(--status-warn-bg)',
                            color: method === 'sum'
                              ? 'var(--status-info)'
                              : 'var(--status-warn)',
                          }}
                        >
                          {method}
                        </span>
                      </td>
                      <td className="text-sm text-secondary">{note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
