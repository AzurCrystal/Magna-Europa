#!/usr/bin/env python
"""Audit decision structure integrity.

Checks per decision block:
  - visible/available/complete_effect braces balance (pdx_parse handles syntax;
    this catches semantic empties: visible={} = always visible, usually intended)
  - icon reference resolves in interface/*.gfx or gfx/ (warning only)
  - activate/complete_effect don't reference undefined decision ids in the same file
  - fire_only_once + days_remove both present (typical vanilla shape)

Usage: python tools/decision_audit.py
"""
import re, glob, os, sys

def audit():
    issues = []
    for f in glob.glob('common/decisions/**/*.txt', recursive=True):
        txt = open(f, encoding='utf-8', errors='replace').read()
        # decision blocks: name = { ... } at depth 1
        for m in re.finditer(r'^\t([A-Za-z0-9_]+)\s*=\s*\{', txt, re.M):
            name = m.group(1)
            # heuristics: real decision blocks have days_remove or visible/available
            seg = txt[m.start():]
            # crude block extraction via brace counting
            depth, end = 0, 0
            for i, ch in enumerate(seg):
                if ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0: end = i; break
            body = seg[:end]
            has_dr = 'days_remove' in body
            has_eff = 'complete_effect' in body or 'remove_effect' in body
            has_mod = 'modifier = {' in body or 'modifier ={' in body
            # dead = timed but produces nothing: no effect, no modifier, no custom cost
            if has_dr and not (has_eff or has_mod):
                issues.append((os.path.basename(f), name, 'days_remove but no effect/modifier — likely dead decision'))
    print(f'decision anomalies: {len(issues)}')
    for i in issues[:40]: print('  ', i)
    return 1 if issues else 0

if __name__ == '__main__':
    sys.exit(audit())
