import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { LocalTime } from '../LocalTime'
import { TechnicalDetails } from '../TechnicalDetails'
import { MaturityBadge } from '../MaturityBadge'
import { MetricCard } from '../MetricCard'
import { PageHeader } from '../PageHeader'
import { SectionHeader } from '../SectionHeader'
import { VideoThumbnail } from '../VideoThumbnail'

describe('LocalTime', () => {
  it('renders a <time> element with a machine-readable dateTime', () => {
    render(<LocalTime value="2026-08-25T19:24:04Z" />)
    const el = screen.getByText(/2026/)
    expect(el.tagName).toBe('TIME')
    expect(el).toHaveAttribute('dateTime', '2026-08-25T19:24:04Z')
  })

  it('exposes the full timestamp via title for hover recovery', () => {
    render(<LocalTime value="2026-08-25T19:24:04Z" />)
    expect(screen.getByText(/2026/)).toHaveAttribute('title')
  })

  it('never leaks a raw ISO string into the visible text', () => {
    render(<LocalTime value="2026-08-25T19:24:04Z" />)
    expect(screen.getByText(/2026/).textContent).not.toContain('T19:24')
  })

  it('shows the fallback when the value is missing', () => {
    render(<LocalTime value={null} fallback="Never" />)
    expect(screen.getByText('Never')).toBeInTheDocument()
  })
})

describe('TechnicalDetails', () => {
  it('collapses content by default so JSON is not in the reading path', () => {
    render(<TechnicalDetails data={{ secret_shape: 'value' }} />)
    // <details> without `open` — content exists but is not exposed.
    const details = screen.getByText('Technical details').closest('details')
    expect(details).not.toHaveAttribute('open')
  })

  it('uses a native details/summary so it is keyboard accessible', async () => {
    const user = userEvent.setup()
    render(<TechnicalDetails data={{ a: 1 }} />)
    const summary = screen.getByText('Technical details')
    await user.click(summary)
    expect(summary.closest('details')).toHaveAttribute('open')
  })

  it('accepts a custom summary label', () => {
    render(<TechnicalDetails summary="Raw health payload" data={{}} />)
    expect(screen.getByText('Raw health payload')).toBeInTheDocument()
  })

  it('serializes structured data', () => {
    render(<TechnicalDetails data={{ alpha: 1 }} />)
    expect(screen.getByText(/"alpha": 1/)).toBeInTheDocument()
  })

  it('survives non-serializable data without throwing', () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular
    expect(() => render(<TechnicalDetails data={circular} />)).not.toThrow()
  })

  it('renders custom children instead of serialized data', () => {
    render(<TechnicalDetails><p>Custom body</p></TechnicalDetails>)
    expect(screen.getByText('Custom body')).toBeInTheDocument()
  })
})

describe('MaturityBadge', () => {
  it.each([
    ['insufficient', 'Not enough data'],
    ['exploratory', 'Exploratory'],
    ['directional', 'Directional'],
    ['actionable', 'Actionable'],
  ])('renders %s as plain language "%s"', (token, label) => {
    render(<MaturityBadge maturity={token} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('preserves the canonical backend token in a data attribute', () => {
    render(<MaturityBadge maturity="directional" />)
    expect(screen.getByText('Directional')).toHaveAttribute('data-maturity', 'directional')
  })

  it('treats a missing maturity as insufficient rather than implying confidence', () => {
    render(<MaturityBadge maturity={null} />)
    expect(screen.getByText('Not enough data')).toBeInTheDocument()
  })

  it('shows sample size so thin evidence is visible', () => {
    render(<MaturityBadge maturity="directional" sampleSize={2} />)
    expect(screen.getByText('based on 2 videos')).toBeInTheDocument()
  })

  it('singularizes a sample size of one', () => {
    render(<MaturityBadge maturity="exploratory" sampleSize={1} />)
    expect(screen.getByText('based on 1 video')).toBeInTheDocument()
  })

  it('omits the sample line when the count is unknown', () => {
    render(<MaturityBadge maturity="exploratory" />)
    expect(screen.queryByText(/based on/)).not.toBeInTheDocument()
  })

  it('carries meaning in the tooltip, not only in color', () => {
    render(<MaturityBadge maturity="exploratory" />)
    expect(screen.getByText('Exploratory').getAttribute('title')).toMatch(/investigate/i)
  })
})

describe('MetricCard', () => {
  it('renders label and pre-formatted value', () => {
    render(<MetricCard label="Views" value="474" />)
    expect(screen.getByText('Views')).toBeInTheDocument()
    expect(screen.getByText('474')).toBeInTheDocument()
  })

  it('renders the sub line as supporting context', () => {
    render(<MetricCard label="Views" value="474" sub="across 2 videos" />)
    expect(screen.getByText('across 2 videos')).toBeInTheDocument()
  })

  it('attention tone still carries meaning in text, not color alone', () => {
    render(<MetricCard label="Exceptions" value="3" sub="Need attention" tone="attention" />)
    expect(screen.getByText('Need attention')).toBeInTheDocument()
  })
})

describe('PageHeader / SectionHeader', () => {
  it('PageHeader renders the page h1', () => {
    render(<PageHeader title="Analytics" subtitle="Channel performance" />)
    expect(screen.getByRole('heading', { level: 1, name: 'Analytics' })).toBeInTheDocument()
    expect(screen.getByText('Channel performance')).toBeInTheDocument()
  })

  it('PageHeader renders actions', () => {
    render(<PageHeader title="Analytics" actions={<button>Refresh</button>} />)
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  })

  it('SectionHeader renders an h2 so the outline stays beneath the page h1', () => {
    render(<SectionHeader title="Recent videos" />)
    expect(screen.getByRole('heading', { level: 2, name: 'Recent videos' })).toBeInTheDocument()
  })

  it('SectionHeader description explains the section without backend terms', () => {
    render(<SectionHeader title="Retention" description="How long viewers stay." />)
    expect(screen.getByText('How long viewers stay.')).toBeInTheDocument()
  })
})

describe('VideoThumbnail', () => {
  it('loads the static YouTube poster for a public video', () => {
    render(<VideoThumbnail videoId="Pj7h0P0tp-A" title="Test video" />)
    const img = document.querySelector('img')
    expect(img).not.toBeNull()
    expect(img!.getAttribute('src')).toContain('Pj7h0P0tp-A')
  })

  it('never renders an iframe or embed (no autoplay surface)', () => {
    render(<VideoThumbnail videoId="Pj7h0P0tp-A" title="Test video" />)
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('loads the thumbnail for a private video with a lock overlay', () => {
    render(<VideoThumbnail videoId="IkWQdkURSww" title="Private one" isPrivate />)
    const img = document.querySelector('img')
    expect(img).not.toBeNull()
    expect(img!.getAttribute('src')).toContain('IkWQdkURSww')
    expect(document.querySelector('.video-thumb-private-overlay')).not.toBeNull()
  })

  it('falls back to a lock placeholder when a private video has no ID', () => {
    render(<VideoThumbnail videoId={null} title="Private one" isPrivate />)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByRole('img', { name: /private video/i })).toBeInTheDocument()
  })

  it('shows a placeholder when there is no video ID', () => {
    render(<VideoThumbnail videoId={null} title="Not uploaded" />)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByRole('img', { name: /no preview/i })).toBeInTheDocument()
  })
})
