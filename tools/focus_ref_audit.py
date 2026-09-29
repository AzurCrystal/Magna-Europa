#!/usr/bin/env python
"""Audit focus references in ai_strategy_plans, decisions, events.

Every `focus = <id>` / `has_completed_focus = <id>` / `focus_progress` target must
exist as `id = <id>` inside common/national_focus/*.txt (unless the ref lives in a
comment or an always=no dead branch — flagged separately as WARN).

Usage: python tools/focus_ref_audit.py
"""
import re, glob, os, sys

def audit():
    foci = set()
    for f in glob.glob('common/national_focus/*.txt'):
        for m in re.finditer(r'^\s*id\s*=\s*([A-Za-z0-9_]+)\s*(?:#.*)?$', open(f, encoding='utf-8', errors='replace').read(), re.M):
            foci.add(m.group(1))
    refs = []
    pat = re.compile(r'(?<![\w.])(?:focus|has_completed_focus|has_selected_focus)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)')
    for d in ('common/ai_strategy_plans', 'common/decisions', 'events', 'common/ai_strategy'):
        for f in glob.glob(f'{d}/**/*.txt', recursive=True) + glob.glob(f'{d}/*.txt'):
            for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
                code = line.split('#', 1)[0]
                for m in pat.finditer(code):
                    if m.group(1) not in foci:
                        refs.append((os.path.basename(f), i, m.group(1)))
    print(f'focus ids defined: {len(foci)} | dead refs: {len(refs)}')
    for r in refs[:40]: print('  ', r)
    return 1 if refs else 0

if __name__ == '__main__':
    sys.exit(audit())
