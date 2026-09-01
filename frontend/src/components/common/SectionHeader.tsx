import type { ReactNode } from 'react'

interface Props {
  title: string
  /** One-line explanation shown under the title. Use to make a section
      understandable without backend knowledge. */
  description?: string
  actions?: ReactNode
}

/** Section heading used inside pages. Renders an <h2> so the document
    outline stays correct beneath the page's single <h1>. */
export function SectionHeader({ title, description, actions }: Props) {
  return (
    <div className="section-header">
      <div>
        <h2 className="section-title">{title}</h2>
        {description && <p className="section-description">{description}</p>}
      </div>
      {actions && <div className="section-header-actions">{actions}</div>}
    </div>
  )
}
