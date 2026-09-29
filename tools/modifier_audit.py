#!/usr/bin/env python
"""Audit modifier + opinion_modifier references.

`add_modifier = { modifier = X }` → X must be a depth-1 key in
common/modifiers/ or common/dynamic_modifiers/.
`add_opinion_modifier` / `has_opinion_modifier` / `remove_opinion_modifier` →
X must be a depth-1 key in common/opinion_modifiers/.

Skipped: numeric values (factor weights like `0.15`), files inside the
definition dirs themselves.

Usage: python tools/modifier_audit.py
"""
import re, glob, os, sys

def defs(*dirs):
    out = set()
    for d in dirs:
        for f in glob.glob(f'{d}/*.txt'):
            txt = open(f, encoding='utf-8', errors='replace').read()
            for m in re.finditer(r'^\t?([A-Za-z0-9_.]+)\s*=\s*\{', txt, re.M):
                out.add(m.group(1))
    return out

def audit():
    mods = defs('common/modifiers', 'common/dynamic_modifiers')
    op = defs('common/opinion_modifiers')
    dead_mod, dead_op = [], []
    skip = ('common/modifiers','common/dynamic_modifiers','common/opinion_modifiers')
    for f in glob.glob('common/**/*.txt', recursive=True) + glob.glob('events/*.txt') + glob.glob('history/**/*.txt', recursive=True):
        rel = f.replace('\\','/')
        if any(d in rel for d in skip): continue
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in re.finditer(r'\b(?:add_modifier|swap_modifier|remove_modifier)\s*=\s*\{\s*modifier\s*=\s*([A-Za-z0-9_.]+)', code):
                v = m.group(1)
                if v not in mods and not v[0].isdigit():
                    dead_mod.append((os.path.basename(f), i, v))
            for m in re.finditer(r'\b(?:add_opinion_modifier|has_opinion_modifier|remove_opinion_modifier)\s*=\s*\{\s*modifier\s*=\s*([A-Za-z0-9_.]+)', code):
                v = m.group(1)
                if v not in op:
                    dead_op.append((os.path.basename(f), i, v))
    print(f'modifiers: {len(mods)} | opinion_modifiers: {len(op)}')
    print(f'dead modifier refs: {len(dead_mod)} | dead opinion refs: {len(dead_op)}')
    for d in dead_mod[:25]: print('  mod', d)
    for d in dead_op[:25]: print('  opi', d)
    return 1 if (dead_mod or dead_op) else 0

if __name__ == '__main__':
    sys.exit(audit())
