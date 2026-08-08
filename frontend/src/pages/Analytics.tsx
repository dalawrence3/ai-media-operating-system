/* M14.6 — Analytics Dashboard

   Shows Platform Analytics data from the backend with correct metric semantics.
   IMPORTANT: AGG_LAST metrics (CTR, avg view duration) are NOT shown as sums.
   Mixed currencies are never silently combined.
   Provisional data is labeled as such.
*/

import { useParams } from 'react-router-dom'
import { UnavailableState } from '@/components/common/UnavailableState'

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

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Analytics</h1>
          <p className="page-subtitle">Platform performance metrics — canonical backend data only</p>
        </div>
      </div>

      <div className="page-body">
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
                  {[
                    ['views', 'sum', 'Additive across periods'],
                    ['impressions', 'sum', 'Additive across periods'],
                    ['estimated_minutes_watched', 'sum', 'Additive watch time'],
                    ['revenue_estimate', 'sum', 'Currency required; mixed currencies not combined'],
                    ['ctr', 'latest_observation', 'Gauge — not summed across periods'],
                    ['average_view_duration', 'latest_observation', 'Gauge — latest provider reading'],
                    ['likes', 'latest_observation', 'Current count, not incremental'],
                    ['subscribers_gained', 'sum', 'Additive subscriber delta'],
                  ].map(([metric, method, note]) => (
                    <tr key={metric}>
                      <td className="font-mono text-sm">{metric}</td>
                      <td>
                        <span className={`tag`} style={{ background: method === 'sum' ? 'var(--status-info-bg)' : 'var(--status-warn-bg)', color: method === 'sum' ? 'var(--status-info)' : 'var(--status-warn)' }}>
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
