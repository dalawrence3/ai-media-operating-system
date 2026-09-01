import { describe, it, expect } from 'vitest'
import {
  formatNumber,
  formatCompact,
  formatPercent,
  formatDuration,
  formatDurationMs,
  formatWatchTime,
  humanizeKey,
  humanizeStatus,
} from '../format'

describe('missing values', () => {
  it.each([
    formatNumber,
    formatCompact,
    formatPercent,
    formatDuration,
    formatDurationMs,
    formatWatchTime,
  ])('%# renders an em dash rather than NaN or 0', fn => {
    expect(fn(null)).toBe('—')
    expect(fn(undefined)).toBe('—')
    expect(fn(NaN)).toBe('—')
  })

  it('distinguishes a real zero from a missing value', () => {
    expect(formatNumber(0)).toBe('0')
    expect(formatCompact(0)).toBe('0')
  })
})

describe('formatNumber', () => {
  it('groups thousands', () => {
    expect(formatNumber(10380)).toMatch(/10.380/)
  })
  it('honours the fraction-digit limit', () => {
    expect(formatNumber(3.14159, 2)).toMatch(/3.14/)
  })
})

describe('formatCompact', () => {
  it('leaves values below 1000 uncompacted', () => {
    expect(formatCompact(474)).toBe('474')
    expect(formatCompact(999)).toBe('999')
  })
  it('compacts thousands', () => {
    expect(formatCompact(10380)).toMatch(/10.4K/i)
  })
  it('compacts millions', () => {
    expect(formatCompact(1_250_000)).toMatch(/1.3M/i)
  })
})

describe('formatPercent', () => {
  it('appends a percent sign to an already-scaled value', () => {
    // Backend stores average_view_percentage as 95.57, already a percentage.
    expect(formatPercent(95.57)).toBe('95.6%')
  })
  it('handles zero', () => {
    expect(formatPercent(0)).toBe('0%')
  })
})

describe('formatDuration', () => {
  it('formats sub-hour durations as m:ss', () => {
    expect(formatDuration(56)).toBe('0:56')
    expect(formatDuration(90)).toBe('1:30')
  })
  it('formats hour-plus durations as h:mm:ss', () => {
    expect(formatDuration(3661)).toBe('1:01:01')
  })
  it('clamps negatives to zero', () => {
    expect(formatDuration(-5)).toBe('0:00')
  })
})

describe('formatDurationMs', () => {
  it('converts milliseconds before formatting', () => {
    expect(formatDurationMs(56_000)).toBe('0:56')
  })
})

describe('formatWatchTime', () => {
  it('uses seconds below a minute', () => {
    expect(formatWatchTime(45)).toBe('45s')
  })
  it('uses minutes below an hour', () => {
    expect(formatWatchTime(300)).toBe('5m')
  })
  it('uses hours and minutes above an hour', () => {
    // 10380s is the live watch-time value for publication 1.
    expect(formatWatchTime(10380)).toBe('2h 53m')
  })
  it('omits the minute part on a whole hour', () => {
    expect(formatWatchTime(7200)).toBe('2h')
  })
})

describe('humanizeKey / humanizeStatus', () => {
  it('turns a snake_case metric name into a readable label', () => {
    expect(humanizeKey('average_view_percentage')).toBe('Average view percentage')
  })
  it('handles hyphens', () => {
    expect(humanizeKey('credential-invalid')).toBe('Credential invalid')
  })
  it('returns empty string for empty input', () => {
    expect(humanizeKey('')).toBe('')
  })
  it('humanizeStatus falls back to Unknown', () => {
    expect(humanizeStatus(null)).toBe('Unknown')
    expect(humanizeStatus('credential_invalid')).toBe('Credential invalid')
  })
})
