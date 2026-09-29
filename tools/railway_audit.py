#!/usr/bin/env python
"""Audit map/railways.txt + map/supply_nodes.txt against fresh province adjacency.

Checks:
  - every consecutive pair in each railway chain is land-adjacent (or strait)
  - no sea/lake province inside any chain (crashes supply mapmode)
  - every supply node is a land province
  - rail-graph connected components (fragmented net -> straight-line supply rendering)

Usage: python tools/railway_audit.py [mapdir]
"""
import sys, json, csv
from collections import deque
from prov_adjacency import build

def audit(mapdir='map'):
    adj_list, ptype = build(mapdir, '_ctx/_adj_tmp.json')
    adj = {int(k): {n for n,_ in v} for k, v in adj_list.items()}
    rail_adj, rail_provs = {}, set()
    breaks, sea_hops = [], []
    for i, line in enumerate(open(f'{mapdir}/railways.txt', encoding='utf-8'), 1):
        p = line.split()
        if len(p) > 3 and p[0].isdigit():
            ch = [int(x) for x in p[2:] if x.isdigit()]
            rail_provs.update(ch)
            for a, b in zip(ch, ch[1:]):
                rail_adj.setdefault(a, set()).add(b)
                rail_adj.setdefault(b, set()).add(a)
                if b not in adj.get(a, set()):
                    breaks.append((i, a, b))
                for v in (a, b):
                    if ptype.get(v) != 'land':
                        sea_hops.append((i, v, ptype.get(v)))
    nodes, node_bad = [], []
    for i, line in enumerate(open(f'{mapdir}/supply_nodes.txt', encoding='utf-8'), 1):
        p = line.split()
        if len(p) >= 2 and p[0].isdigit():
            pid = int(p[1]); nodes.append(pid)
            if ptype.get(pid) != 'land': node_bad.append((i, pid, ptype.get(pid)))
    # components
    seen, comps = set(), []
    unseen = set(rail_provs)
    while unseen:
        s = unseen.pop(); comp = {s}; q = deque([s])
        while q:
            c = q.popleft()
            for n in rail_adj.get(c, ()):
                if n not in comp: comp.add(n); q.append(n)
        unseen -= comp; comps.append(comp)
    comps.sort(key=len, reverse=True)
    print(f'rail provs: {len(rail_provs)} | components: {[len(c) for c in comps[:8]]}')
    print(f'chain breaks: {len(breaks)} | sea/lake in chain: {len(sea_hops)} | bad nodes: {len(node_bad)}')
    for b in breaks[:20]: print('  break', b)
    for s in sea_hops[:20]: print('  sea', s)
    for n in node_bad[:10]: print('  node', n)
    return 1 if (breaks or sea_hops or node_bad) else 0

if __name__ == '__main__':
    sys.exit(audit(sys.argv[1] if len(sys.argv) > 1 else 'map'))
