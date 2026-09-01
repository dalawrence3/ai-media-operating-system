/**
 * Playwright globalSetup — runs once before the full E2E suite.
 *
 * Phase 18E. This does two things, in this order:
 *
 *   1. VERIFIES ISOLATION. Asks the backend which database and runtime mode it
 *      is actually using, and refuses to run a single test unless it reports
 *      test mode against a test database. Before this phase the suite could
 *      silently adopt the live backend on :8000; a config that *intends*
 *      isolation is not the same as a backend that *has* it, and the only way
 *      to know is to ask the running process.
 *
 *   2. SEEDS. The E2E database is disposable, so seeding it automatically is
 *      safe — unlike the old behaviour, which deliberately refused to seed
 *      because it might have been pointed at the operational database.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { request } from '@playwright/test'
import { E2E_BACKEND_URL } from '../playwright.config'

const DEV_SLUG = 'dev-studio'
const REPO_ROOT = path.resolve(__dirname, '..', '..')

function fail(title: string, lines: string[]): never {
  console.error('\n')
  console.error(`  ✗  ${title}`)
  for (const line of lines) console.error(`  ${line}`)
  console.error('\n')
  throw new Error(title)
}

async function assertIsolated(): Promise<void> {
  const ctx = await request.newContext({
    baseURL: E2E_BACKEND_URL,
    extraHTTPHeaders: { 'X-Dev-Actor': 'dev:studio-user' },
  })
  try {
    const res = await ctx.get('/api/meta')
    if (!res.ok()) {
      fail('E2E BACKEND UNREACHABLE', [
        `GET ${E2E_BACKEND_URL}/api/meta returned ${res.status()}.`,
      ])
    }
    const meta = await res.json()
    const runtime = meta?.runtime ?? {}

    if (runtime.test_mode !== 'e2e') {
      fail('E2E ISOLATION CHECK FAILED', [
        `The backend at ${E2E_BACKEND_URL} reports test_mode=${JSON.stringify(runtime.test_mode)},`,
        `expected "e2e".`,
        '',
        'This backend is NOT an isolated test runtime. Refusing to run tests',
        'against it — this is the exact condition under which the E2E suite',
        'previously mutated live publishing authorization.',
      ])
    }
    if (runtime.operational_db !== false) {
      fail('E2E ISOLATION CHECK FAILED', [
        `The backend at ${E2E_BACKEND_URL} is using the OPERATIONAL database.`,
        `db=${runtime.db_name}`,
        '',
        'Refusing to run tests against live Media OS state.',
      ])
    }

    console.log(
      `  ✓  E2E isolation verified: mode=${runtime.test_mode} db=${runtime.db_name}`,
    )
  } finally {
    await ctx.dispose()
  }
}

async function ensureSeeded(): Promise<void> {
  const ctx = await request.newContext({
    baseURL: E2E_BACKEND_URL,
    extraHTTPHeaders: { 'X-Dev-Actor': 'dev:studio-user' },
  })
  let seeded = false
  try {
    const res = await ctx.get('/api/v1/workspaces')
    if (res.ok()) {
      const workspaces: Array<{ slug: string }> = await res.json()
      seeded = workspaces.some(w => w.slug === DEV_SLUG)
    }
  } finally {
    await ctx.dispose()
  }

  if (seeded) {
    console.log(`  ✓  E2E seed present: workspace '${DEV_SLUG}'.`)
    return
  }

  // Safe to seed unattended: isolation was verified above, so this can only
  // ever write to the disposable E2E database.
  console.log('  …  Seeding the isolated E2E database.')
  try {
    execFileSync('.venv/bin/python', ['scripts/seed-dev.py'], {
      cwd: REPO_ROOT,
      stdio: 'inherit',
      env: {
        ...process.env,
        ACE_TEST_MODE: 'e2e',
        ACE_ENV: 'development',
        ACE_DB_PATH: path.join(REPO_ROOT, '.e2e-data', 'e2e-test.db'),
        ACE_ARTIFACTS_PATH: path.join(REPO_ROOT, '.e2e-data', 'artifacts'),
      },
    })
  } catch (err) {
    fail('E2E SEED FAILED', [
      String(err),
      '',
      'Run it manually to see the error:',
      '',
      '      make seed-e2e',
    ])
  }
  console.log('  ✓  E2E database seeded.')
}

export default async function globalSetup() {
  await assertIsolated()
  await ensureSeeded()
}
