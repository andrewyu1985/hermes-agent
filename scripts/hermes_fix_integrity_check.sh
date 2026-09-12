#!/usr/bin/env bash
# Read-only operational guard. Hooks warn but never block checkout/merge,
# restore golden sources, or restart services. Unknown layouts need review.
set -u
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! python3 "$repo/scripts/hermes_integrity.py" "$repo"; then
  printf 'WARN: Hermes integrity requires manual review; no source files changed and no service restarted.\n' >&2
fi
exit 0
