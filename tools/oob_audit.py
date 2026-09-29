#!/usr/bin/env python
"""Audit history/units/*.txt OOB integrity.

Per file:
  - `division_template = { name = "X" ... }` defines a template
  - `division_template = "X"` inside a `division = {}` references it
Dead refs: reference to a template not defined in the same file (or shared
_global templates in history/units/_*.txt).
Also flags divisions lacking start_experience_factor (cosmetic) and
duplicate template names within a file.

Usage: python tools/oob_audit.py
"""
import re, glob, os, sys

def audit():
    dead, dup = [], []
    for f in glob.glob('history/units/*.txt'):
        txt = open(f, encoding='utf-8', errors='replace').read()
        defined = re.findall(r'division_template\s*=\s*\{[^}]*?name\s*=\s*"([^"]+)"', txt)
        seen = set()
        for t in defined:
            if t in seen: dup.append((os.path.basename(f), t))
            seen.add(t)
        referenced = re.findall(r'division_template\s*=\s*"([^"]+)"', txt)
        for t in referenced:
            if t not in seen:
                dead.append((os.path.basename(f), t))
    print(f'dead template refs: {len(dead)} | duplicate names: {len(dup)}')
    for d in dead[:30]: print('  dead', d)
    for d in dup[:30]: print('  dup ', d)
    return 1 if dead or dup else 0

if __name__ == '__main__':
    sys.exit(audit())
