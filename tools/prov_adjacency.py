#!/usr/bin/env python
"""Rebuild province adjacency from map/provinces.bmp + map/adjacencies.csv.

Outputs JSON: {prov_id: [[neighbor_id, edge_type], ...]}
edge_type: 'land' (4-neighbor pixel contact) or 'strait' (adjacencies.csv).

Usage: python tools/prov_adjacency.py [mapdir] [out.json]
Requires: numpy, Pillow.
"""
import sys, json, csv
import numpy as np
from PIL import Image

def build(mapdir='map', out='_ctx/prov_adj_fresh.json'):
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
    try:
        for row in csv.reader(open(f'{mapdir}/adjacencies.csv', encoding='utf-8', errors='replace'), delimiter=';'):
            if len(row) < 4 or not row[0].isdigit() or not row[1].isdigit():
                continue
            # strait = either Type='sea' (legacy) or a Through province (col 3)
            is_strait = row[2].strip().lower() == 'sea' or (row[3].strip() not in ('', '-1'))
            if is_strait:
                a, b = int(row[0]), int(row[1])
                adj.setdefault(a, []).append([b, 'strait'])
                adj.setdefault(b, []).append([a, 'strait'])
    except FileNotFoundError:
        pass
    return adj, ptype

if __name__ == '__main__':
    build(*(sys.argv[1:] or []))
