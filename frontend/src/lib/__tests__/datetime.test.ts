import { describe, it, expect } from 'vitest'
import {
  parseUtc,
  formatDate,
  formatDateTime,
  formatRelative,
  formatFull,
} from '../datetime'

describe('parseUtc', () => {
  it('returns null for empty/nullish input', () => {
    expect(parseUtc(null)).toBeNull()
    expect(parseUtc(undefined)).toBeNull()
    expect(parseUtc('')).toBeNull()
    expect(parseUtc('   ')).toBeNull()
  })

  it('returns null for an unparseable string', () => {
    expect(parseUtc('not-a-date')).toBeNull()
  })

  it('treats a naive backend timestamp as UTC, not local time', () => {
    // The backend emits naive strings like "2026-08-25T19:24:04" meaning UTC.
    // Without the Z suffix the engine would read it as local time and shift it.
    const naive = parseUtc('2026-08-25T19:24:04')
    const explicit = parseUtc('2026-08-25T19:24:04Z')
    expect(naive).not.toBeNull()
    expect(naive!.getTime()).toBe(explicit!.getTime())
  })

  it('honours an explicit +00:00 offset', () => {
    const withOffset = parseUtc('2026-08-25T19:24:04.670069+00:00')
    const withZ = parseUtc('2026-08-25T19:24:04.670069Z')
    expect(withOffset!.getTime()).toBe(withZ!.getTime())
  })

  it('honours a non-UTC offset rather than forcing UTC', () => {
    const plusTwo = parseUtc('2026-08-25T21:24:04+02:00')
    const utc = parseUtc('2026-08-25T19:24:04Z')
    expect(plusTwo!.getTime()).toBe(utc!.getTime())
  })
})

describe('formatters return an em dash for missing values', () => {
  it.each([formatDate, formatDateTime, formatRelative])('%#', fn => {
    expect(fn(null)).toBe('—')
    expect(fn(undefined)).toBe('—')
    expect(fn('')).toBe('—')
  })

  it('formatFull uses a word, not a dash', () => {
    expect(formatFull(null)).toBe('Unknown')
  })
})

describe('formatDate / formatDateTime', () => {
  it('renders a real date without leaking the ISO string', () => {
    const out = formatDate('2026-08-25T19:24:04Z')
    expect(out).not.toBe('—')
    expect(out).not.toContain('T')
    expect(out).toContain('2026')
  })

  it('datetime includes a time component that the date form omits', () => {
    const dt = formatDateTime('2026-08-25T19:24:04Z')
    const d = formatDate('2026-08-25T19:24:04Z')
    expect(dt.length).toBeGreaterThan(d.length)
  })
})

describe('formatRelative', () => {
  const now = new Date('2026-08-25T12:00:00Z')

  it('reports sub-minute differences as "just now"', () => {
    expect(formatRelative('2026-08-25T11:59:40Z', now)).toBe('just now')
  })

  it('reports hours in the past', () => {
    expect(formatRelative('2026-08-25T09:00:00Z', now)).toMatch(/3 hours ago/)
  })

  it('reports days in the future', () => {
    expect(formatRelative('2026-08-27T12:00:00Z', now)).toMatch(/in 2 days/)
  })

  it('reports months for larger gaps', () => {
    expect(formatRelative('2026-05-25T12:00:00Z', now)).toMatch(/month/)
  })
})
