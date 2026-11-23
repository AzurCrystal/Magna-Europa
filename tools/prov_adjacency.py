#!/usr/bin/env python
"""Rebuild province adjacency from map/provinces.bmp + map/adjacencies.csv.

Outputs JSON: {prov_id: [[neighbor_id, edge_type], ...]}
edge_type: 'land' (4-neighbor pixel contact) or 'strait' (adjacencies.csv).

A CSV row is a strait when its Type is 'sea', its Through province is set, or
its adjacency_rule_name gates a sea/canal/strait crossing. Rows typed
'impassable' are never walkable, and rule-only rows whose rule allows army
movement in every relation block are unrestricted land borders (e.g.
*_BORDER), not crossings.

Usage: python tools/prov_adjacency.py [mapdir] out.json
out.json is written relative to the repo root.
Requires: numpy, Pillow.
"""
import sys, os, re, json, csv
from _modroot import cd_mod, repo_root
cd_mod()
import numpy as np
from PIL import Image

def sea_rules(mapdir):
    """Names of adjacency rules that gate a sea/canal/strait crossing.

    Rules whose relation blocks all allow army movement are unrestricted land
    borders, not crossings.
    """
    try:
        with open(f'{mapdir}/adjacency_rules.txt', encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except FileNotFoundError:
        return set()
    rules, pos = set(), 0
    while True:
        start = text.find('adjacency_rule', pos)
        if start < 0:
            break
        end = text.find('{', start)
        if end < 0:
            break
        depth, pos = 0, end
        while pos < len(text):
            if text[pos] == '{': depth += 1
            elif text[pos] == '}':
                depth -= 1
                if depth == 0: break
            pos += 1
        block = text[end + 1:pos]
        name = re.search(r'\bname\s*=\s*"([^"]+)"', block)
        armies = re.findall(r'\barmy\s*=\s*(\w+)', block)
        if name and any(a.lower() == 'no' for a in armies):
            rules.add(name.group(1))
        pos += 1
    return rules

def build(mapdir='map'):
    im = np.array(Image.open(f'{mapdir}/provinces.bmp').convert('RGB'))
    h, w = im.shape[:2]
    rgb2id, ptype = {}, {}
    with open(f'{mapdir}/definition.csv', encoding='utf-8', errors='replace') as fh:
        for row in csv.reader(fh, delimiter=';'):
            if row and row[0].isdigit():
                pid = int(row[0])
                rgb2id[(int(row[1]), int(row[2]), int(row[3]))] = pid
                ptype[pid] = row[4] if len(row) > 4 else 'land'
    key = im[:,:,0].astype(np.int64)*65536 + im[:,:,1].astype(np.int64)*256 + im[:,:,2]
    uniq, inv = np.unique(key, return_inverse=True)
    k2id = {r*65536+g*256+b: i for (r,g,b),i in rgb2id.items()}
    uid = np.array([k2id.get(int(u), -1) for u in uniq])
    pid = uid[inv.reshape(h, w)]
    pairs = set()
    for axis in (0, 1):
        d = np.diff(pid, axis=axis)
        nz = d != 0
        if axis == 0: aa, bb = pid[:-1,:][nz], pid[1:,:][nz]
        else:         aa, bb = pid[:,:-1][nz], pid[:,1:][nz]
        for x, y in zip(aa.tolist(), bb.tolist()):
            if x != y and x > 0 and y > 0:
                pairs.add((min(x,y), max(x,y)))
    adj = {}
    for x, y in pairs:
        et = 'land'
        adj.setdefault(x, []).append([y, et])
        adj.setdefault(y, []).append([x, et])
    cross_rules = sea_rules(mapdir)
    try:
        with open(f'{mapdir}/adjacencies.csv', encoding='utf-8', errors='replace') as fh:
            for row in csv.reader(fh, delimiter=';'):
                if len(row) < 4 or not row[0].isdigit() or not row[1].isdigit():
                    continue
                row_type = row[2].strip().lower()
                if row_type == 'impassable':
                    continue
                rule = row[8].strip() if len(row) > 8 else ''
                is_strait = (row_type == 'sea'
                             or row[3].strip() not in ('', '-1')
                             or rule in cross_rules)
                if is_strait:
                    a, b = int(row[0]), int(row[1])
                    adj.setdefault(a, []).append([b, 'strait'])
                    adj.setdefault(b, []).append([a, 'strait'])
    except FileNotFoundError:
        pass
    return adj, ptype

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Rebuild province adjacency JSON.')
    ap.add_argument('mapdir', nargs='?', default='map',
                    help='map directory, resolved under the mod root (default: map)')
    ap.add_argument('out', help='output JSON path, relative to the repo root')
    args = ap.parse_args(argv)
    out = args.out if os.path.isabs(args.out) else os.path.join(repo_root(), args.out)
    adj, _ = build(args.mapdir)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(adj, fh, sort_keys=True)
    land = sum(1 for nbrs in adj.values() for _, et in nbrs if et == 'land') // 2
    strait = sum(1 for nbrs in adj.values() for _, et in nbrs if et == 'strait') // 2
    print(f'provinces: {len(adj)} | edges: {land + strait} (land {land}, strait {strait})')
    print(f'wrote {out}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
