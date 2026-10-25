#!/usr/bin/env bash
# Report-only operational guard. It never blocks a hook, changes source,
# repairs state, or operates the gateway service.
set -u
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! python3 "$repo/scripts/hermes_integrity.py" "$repo"; then
  printf 'WARN: Hermes integrity requires manual review; no source files changed and no service operated.\n' >&2
fi
exit 0
