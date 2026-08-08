/* Architecture boundary — all fetch calls must go through the centralized client.
   This test reads the source files and verifies no stray raw fetch() calls exist
   in pages or components outside of the client implementation itself. */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// process.cwd() = frontend/ (where npm test is run from)
const FRONTEND_SRC = join(process.cwd(), 'src')

function collectFiles(dir: string, ext: string[]): string[] {
  const results: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      results.push(...collectFiles(full, ext))
    } else if (ext.some(e => entry.endsWith(e))) {
      results.push(full)
    }
  }
  return results
}

const SRC = FRONTEND_SRC
const EXCLUDED = [
  '/api/client.ts',   // the centralized client is allowed to use fetch()
  '/auth/',           // AuthContext bootstraps the API client, so uses fetch() directly
  '/test/',           // test infrastructure and test helper files
]

function isExcluded(path: string): boolean {
  return EXCLUDED.some(ex => path.includes(ex))
}

describe('Architecture boundary', () => {
  it('no page or component uses raw fetch() outside the API client', () => {
    const files = collectFiles(SRC, ['.ts', '.tsx']).filter(f => !isExcluded(f))
    const violations: string[] = []

    for (const file of files) {
      const content = readFileSync(file, 'utf-8')
      // Match standalone fetch( calls (not inside comments)
      const lines = content.split('\n')
      lines.forEach((line: string, i: number) => {
        const stripped = line.replace(/\/\/.*/, '')
        if (/\bfetch\s*\(/.test(stripped)) {
          violations.push(`${file}:${i + 1}: ${line.trim()}`)
        }
      })
    }

    if (violations.length > 0) {
      throw new Error(
        `Raw fetch() calls found outside the API client:\n${violations.join('\n')}`,
      )
    }
    expect(violations).toHaveLength(0)
  })

  it('no page or component imports from backend modules', () => {
    const files = collectFiles(SRC, ['.ts', '.tsx']).filter(f => !isExcluded(f))
    const forbiddenPatterns = [
      /from ['"].*database/,
      /from ['"].*sqlite3/,
      /from ['"].*repositories/,
      /from ['"].*engine/,
      /import sqlite3/,
    ]
    const violations: string[] = []

    for (const file of files) {
      const content = readFileSync(file, 'utf-8')
      for (const pattern of forbiddenPatterns) {
        if (pattern.test(content)) {
          violations.push(`${file}: matches ${pattern}`)
        }
      }
    }

    if (violations.length > 0) {
      throw new Error(`Frontend imports backend internals:\n${violations.join('\n')}`)
    }
    expect(violations).toHaveLength(0)
  })

  it('no page or component contains hardcoded secrets or tokens', () => {
    const files = collectFiles(SRC, ['.ts', '.tsx']).filter(f => !isExcluded(f))
    const secretPatterns = [
      /sk-[a-zA-Z0-9]{20,}/,           // OpenAI-style keys
      /Bearer [a-zA-Z0-9+/=]{20,}/,     // JWT/bearer tokens
      /refresh_token\s*=\s*['"][^'"]{10}/,
      /client_secret\s*=\s*['"][^'"]{8}/,
      /password\s*=\s*['"][^'"]{4}/,
    ]
    const violations: string[] = []

    for (const file of files) {
      const content = readFileSync(file, 'utf-8')
      for (const pattern of secretPatterns) {
        if (pattern.test(content)) {
          violations.push(`${file}: possible secret matches ${pattern}`)
        }
      }
    }

    expect(violations).toHaveLength(0)
  })

  it('dev auth actor header is not "production" or a real bearer token', () => {
    const clientPath = join(FRONTEND_SRC, 'api', 'client.ts')
    const content = readFileSync(clientPath, 'utf-8')
    // Must use the dev: prefix actor, not a real JWT or prod value
    expect(content).toContain("'dev:studio-user'")
    expect(content).not.toMatch(/Authorization:\s*Bearer [a-zA-Z0-9+/=]{20,}/)
  })
})
