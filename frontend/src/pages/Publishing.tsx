/* Publishing — intentional state: provider setup required */

import { useParams } from 'react-router-dom'
import { UnavailableState } from '@/components/common/UnavailableState'
import { EmptyState } from '@/components/common/EmptyState'

export function Publishing() {
  const { workspaceId } = useParams<{ workspaceId: string }>()

  if (!workspaceId) return (
    <div className="page-body">
      <EmptyState icon="📤" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Publishing</h1>
          <p className="page-subtitle">Publishing plans, jobs, and publication records</p>
        </div>
      </div>
      <div className="page-body">
        <UnavailableState
          title="Publishing — Live integration not configured"
          description={
            `Publishing records appear here after a pipeline produces an approved render ` +
            `and a platform account has credentials configured. ` +
            `Prerequisites: (1) Channel exists, (2) platform account registered with OAuth credential, ` +
            `(3) pipeline completes render stage, (4) render passes human review, ` +
            `(5) ACE_PUBLISHING_LIVE_ENABLED=true. ` +
            `Manage publishing via the pipeline studio or 'ace publish' CLI.`
          }
          reason="live_disabled"
        />
      </div>
    </>
  )
}
