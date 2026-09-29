#!/usr/bin/env python
"""Audit map/strategicregions/ vs state/province ownership.

Every strategicregion file lists `provinces = {...}` (SR id in filename).
Every province must appear in exactly ONE SR; every state must be fully inside
one SR (state's provinces ⊆ one SR's list — vanilla invariant).

Usage: python tools/sr_audit.py
"""
import re, glob, os, sys
from collections import Counter

def audit():
    sr_prov = {}   # pid -> sr_id
    sr_multi = []  # provs in >1 SR
    for f in glob.glob('map/strategicregions/*.txt'):
        m = re.match(r'(\d+)-', os.path.basename(f))
        if not m: continue
        srid = int(m.group(1))
        pm = re.search(r'provinces\s*=\s*\{([^}]*)\}', open(f, encoding='utf-8', errors='replace').read())
        if not pm: continue
        for x in pm.group(1).split():
            if x.isdigit():
                pid = int(x)
                if pid in sr_prov: sr_multi.append((pid, sr_prov[pid], srid))
                sr_prov[pid] = srid
    # states -> their SR coverage
    state_gap = []
    for f in glob.glob('history/states/*.txt'):
        m = re.match(r'(\d+)-(.+)\.txt', os.path.basename(f))
        if not m: continue
        sid = int(m.group(1))
        body = open(f, encoding='utf-8', errors='replace').read()
        pm = re.search(r'provinces\s*=\s*\{([^}]*)\}', body)
        if not pm: continue
        pids = [int(x) for x in pm.group(1).split() if x.isdigit()]
        srs = {sr_prov.get(p) for p in pids}
        srs.discard(None)
        missing = [p for p in pids if p not in sr_prov]
        if len(srs) > 1:
            state_gap.append((sid, m.group(2), f'spans SRs {sorted(srs)}'))
        if missing:
            state_gap.append((sid, m.group(2), f'{len(missing)} provs not in any SR'))
    print(f'SR coverage: {len(sr_prov)} provs | multi-SR provs: {len(sr_multi)} | state anomalies: {len(state_gap)}')
    for x in sr_multi[:10]: print('  multi', x)
    for x in state_gap[:20]: print('  state', x)
    return 1 if (sr_multi or state_gap) else 0

if __name__ == '__main__':
    sys.exit(audit())
