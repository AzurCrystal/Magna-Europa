#!/usr/bin/env python
"""Flag references to states/provinces that don't exist in this mod's map.

Scans common/ events/ for numeric ids used in state-ish contexts and checks:
  - state ids present in history/states/*.txt
  - province ids present in map/definition.csv
  - continent names valid for the mod's reduced map (australia/oceania south-america etc.)

Heuristic, not exhaustive: reports id + file:line for manual triage. Anything
inside an `always = no` branch is tagged DEAD (harmless) vs LIVE.

Usage: python tools/offmap_refs.py
"""
import re, glob, os, sys

VALID_CONTINENTS = {'europe','asia','africa','north_america','middle_east'}  # mod map coverage

def audit():
    states, provs = set(), set()
    for f in glob.glob('history/states/*.txt'):
        m = re.match(r'(\d+)-', os.path.basename(f))
        if m: states.add(int(m.group(1)))
    for line in open('map/definition.csv', encoding='utf-8', errors='replace'):
        p = line.split(';')
        if p and p[0].isdigit(): provs.add(int(p[0]))

    hits = {'state': [], 'continent': []}
    state_ctx = re.compile(r'\b(?:owns_state|controls_state|is_core_of\s*=\s*ROOT\s*\}\s*\n|add_state_core|transfer_state|set_state_category|state|start_experience_factor_for_[a-z_]+)\s*=\s*(\d+)')
    continent_ctx = re.compile(r'is_on_continent\s*=\s*([a-z_]+)')
    for d in ('common', 'events'):
        for f in glob.glob(f'{d}/**/*.txt', recursive=True):
            lines = open(f, encoding='utf-8', errors='replace').read().splitlines()
            dead = any('always = no' in l for l in lines)
            for i, line in enumerate(lines, 1):
                code = line.split('#', 1)[0]  # strip comments
                for m in state_ctx.finditer(code):
                    sid = int(m.group(1))
                    if sid not in states and sid > 0:
                        hits['state'].append((os.path.basename(f), i, sid, 'DEAD' if dead else 'LIVE'))
                for m in continent_ctx.finditer(code):
                    c = m.group(1)
                    if c not in VALID_CONTINENTS:
                        hits['continent'].append((os.path.basename(f), i, c, 'DEAD' if dead else 'LIVE'))
    for k, v in hits.items():
        live = [h for h in v if h[3] == 'LIVE']
        print(f'{k}: {len(v)} suspects ({len(live)} LIVE)')
        for h in live[:40]: print('  ', h)
    return 1 if any(h[3]=='LIVE' for v in hits.values() for h in v) else 0

if __name__ == '__main__':
    sys.exit(audit())
