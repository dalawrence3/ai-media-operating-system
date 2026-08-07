import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatTile } from './StatTile'

describe('StatTile', () => {
  it('renders label and value', () => {
    render(<StatTile label="Dead Letters" value={3} />)
    expect(screen.getByText('Dead Letters')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('renders sub-label when provided', () => {
    render(<StatTile label="Monthly Spend" value="$12.34" sub="Current period" />)
    expect(screen.getByText('Current period')).toBeInTheDocument()
  })

  it('does not render sub when omitted', () => {
    const { container } = render(<StatTile label="L" value="V" />)
    expect(container.querySelectorAll('.stat-tile-sub')).toHaveLength(0)
  })

  it('renders string values', () => {
    render(<StatTile label="Spend" value="$0.00" />)
    expect(screen.getByText('$0.00')).toBeInTheDocument()
  })
})
