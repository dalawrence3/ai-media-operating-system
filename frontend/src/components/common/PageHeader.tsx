import type { ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: string
  /** Right-aligned controls: status chips, selectors, primary actions. */
  actions?: ReactNode
}

/** Standard page header. Every product page uses this so title typography,
    spacing, and action placement stay identical across the app. */
export function PageHeader({ title, subtitle, actions }: Props) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </div>
  )
}
