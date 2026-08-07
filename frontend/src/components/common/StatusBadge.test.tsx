import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('renders the mapped label for known statuses', () => {
    render(<StatusBadge status="active" />)
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('uses custom label when provided', () => {
    render(<StatusBadge status="active" label="Live" />)
    expect(screen.getByText('Live')).toBeInTheDocument()
  })

  it('falls back to raw status string for unknown statuses', () => {
    render(<StatusBadge status="custom_state" />)
    expect(screen.getByText('custom_state')).toBeInTheDocument()
  })

  it('renders waiting_for_review as Review Required (not a raw code)', () => {
    render(<StatusBadge status="waiting_for_review" />)
    expect(screen.getByText('Review Required')).toBeInTheDocument()
  })

  it('renders blocked status', () => {
    render(<StatusBadge status="blocked" />)
    expect(screen.getByText('Blocked')).toBeInTheDocument()
  })

  it('renders autonomous mode (must not say "disabled")', () => {
    render(<StatusBadge status="autonomous" />)
    const el = screen.getByText('Autonomous')
    expect(el).toBeInTheDocument()
    expect(el.textContent).not.toContain('disabled')
    expect(el.textContent).not.toContain('off')
  })

  it('provides aria-label so status is not color-only', () => {
    render(<StatusBadge status="failed" />)
    const el = screen.getByRole('generic', { name: /Status: Failed/i })
    expect(el).toBeInTheDocument()
  })

  it('is case-insensitive (ACTIVE === active)', () => {
    render(<StatusBadge status="ACTIVE" />)
    expect(screen.getByText('Active')).toBeInTheDocument()
  })
})
