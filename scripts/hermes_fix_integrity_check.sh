#!/usr/bin/env bash
# Local operational guard: verify Codex output=null / TypeError hotfix invariants.
# Safe for git hooks: never blocks checkout/merge; detailed watchdogs run via Hermes cron.
set -u
repo="/root/hermes-agent"
problems=0
check() {
  local file="$1" pattern="$2" desc="$3"
  if ! grep -Eq "$pattern" "$repo/$file" 2>/dev/null; then
    printf 'WARN: missing integrity marker: %s (%s)\n' "$desc" "$file" >&2
    problems=$((problems+1))
  fi
}
check "agent/codex_runtime.py" 'NoneType.*iterable|_consume_codex_event_stream' 'Codex output null guard / SSE consumer'
check "agent/codex_runtime.py" 'responses\.create|stream=True|stream_kwargs\["stream"\]' 'raw responses streaming path'
check "agent/conversation_loop.py" 'output_text|TypeError' 'conversation loop TypeError/output_text recovery'
if [ "$problems" -gt 0 ]; then
  printf 'WARN: Hermes integrity check found %s potential issue(s). Run ~/.hermes/scripts/hermes_doctor.py for details.\n' "$problems" >&2
fi
exit 0
