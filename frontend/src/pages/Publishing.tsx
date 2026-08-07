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
          title="Publishing — Live integration not enabled"
          description={
            `Publishing operations require YouTube OAuth credentials and ` +
            `ACE_PUBLISHING_LIVE_ENABLED=true. ` +
            `Configure credentials and enable the safety gate, then manage ` +
            `publishing through the pipeline studio or 'ace publish' CLI.`
          }
          reason="live_disabled"
        />
      </div>
    </>
  )
}
