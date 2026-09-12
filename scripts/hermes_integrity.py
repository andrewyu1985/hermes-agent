#!/usr/bin/env python3
"""Read-only Codex SSE structural checks for the current upstream layout.

No runtime imports, API calls, golden restores, or service operations. Unknown
future layouts require manual review, not automatic source replacement. These
checks are diagnostics, not a substitute for upstream behavioral tests.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys


def check_integrity(repo: Path) -> list[str]:
    problems: list[str] = []
    try:
        tree = ast.parse((repo / 'agent/codex_runtime.py').read_text(encoding='utf-8'))
        transport = ast.parse((repo / 'agent/transports/codex.py').read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return [f'Cannot inspect Codex sources: {exc}']

    # AST unparse ignores comments and normalizes quote/whitespace changes.
    functions = {node.name: ast.unparse(node) for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    required = {
        '_open_codex_stream': ("stream_kwargs['stream'] = True", '.responses.create('),
        'run_codex_stream': ('_consume_codex_event_stream(',),
        '_consume_codex_event_stream': ('_CodexResponseAssembler(', 'assembler.feed(event)', 'assembler.result()'),
        '_on_item_done': ('self.output_items.append(done_item)',),
        'result': ('list(self.output_items)', 'output=output', 'output_text='),
    }
    for name, markers in required.items():
        body = functions.get(name, '')
        if not all(marker in body for marker in markers):
            problems.append(f'Codex SSE structure requires review: {name}')

    source = ast.unparse(tree)
    for marker in ("'response.output_item.done': _on_item_done", "'response.completed': _on_terminal"):
        if marker not in source:
            problems.append(f'Codex SSE event dispatch missing: {marker}')
    terminal = functions.get('_on_terminal', '')
    if not terminal or "'output'" in terminal or '.output' in terminal:
        problems.append('Terminal SSE handler must not consume nullable response.output')
    # The SDK stream accumulator was the source of output=null crashes.
    if '.responses.stream(' in source:
        problems.append('SDK responses.stream accumulator reintroduced; review null-output safety')

    # Catch the historical explicit tools=None regression without requiring a
    # guard/comment at an obsolete location in conversation_loop.py.
    for node in ast.walk(transport):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is None:
            if any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                   and t.slice.value == 'tools' for t in node.targets):
                problems.append('Codex transport explicitly assigns tools=None')
        if isinstance(node, ast.keyword) and node.arg == 'tools' and isinstance(node.value, ast.Constant) and node.value.value is None:
            problems.append('Codex transport explicitly passes tools=None')
    return problems


if __name__ == '__main__':
    issues = check_integrity(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    for issue in issues:
        print(f'WARN: {issue}', file=sys.stderr)
    raise SystemExit(2 if issues else 0)
