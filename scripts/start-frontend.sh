#!/usr/bin/env bash
# Start the Vite frontend dev server.
# Loaded by 'make dev' — do not run directly in production.

set -euo pipefail

cd "$(dirname "$0")/../frontend"
exec npm run dev
