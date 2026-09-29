#!/usr/bin/env python
"""Audit map/buildings.txt — every instance must sit on land of its declared state.

buildings.txt format: state_id;type;x;y;z;rotation;?
x = provinces.bmp column, z = 3072 - bmp_row. We map (x,z) -> bmp pixel ->
province id -> its owning state via history/states/*.txt.

Reports entries whose pixel lands on water or in a different state.

Usage: python tools/buildings_audit.py [mapdir]
"""
import sys, re, glob, os, csv
import numpy as np
from PIL import Image

def audit(mapdir='map', bmp_h=3072):
    im = np.array(Image.open(f'{mapdir}/provinces.bmp').convert('RGB'))
    rgb2id, ptype = {}, {}
    with open(f'{mapdir}/definition.csv', encoding='utf-8', errors='replace') as fh:
        for row in csv.reader(fh, delimiter=';'):
            if row and row[0].isdigit():
                rgb2id[(int(row[1]), int(row[2]), int(row[3]))] = int(row[0])
                ptype[int(row[0])] = row[4] if len(row) > 4 else 'land'
    prov2state = {}
    for f in glob.glob('history/states/*.txt'):
        m = re.match(r'(\d+)-', os.path.basename(f))
        if not m: continue
        sid = int(m.group(1))
        pm = re.search(r'provinces\s*=\s*\{([^}]*)\}', open(f, encoding='utf-8', errors='replace').read())
        if pm:
            for x in pm.group(1).split():
                if x.isdigit(): prov2state[int(x)] = sid
    bad = []
    for i, line in enumerate(open(f'{mapdir}/buildings.txt', encoding='utf-8'), 1):
        p = line.split(';')
        if len(p) < 6 or not p[0].strip().isdigit(): continue
        sid = int(p[0]); btype = p[1]; x = int(float(p[2])); z = int(float(p[4]))
        row = bmp_h - z
        if not (0 <= x < im.shape[1] and 0 <= row < im.shape[0]):
            bad.append((i, sid, btype, 'out-of-bounds')); continue
        px = tuple(im[row, x]); pid = rgb2id.get(px)
        if pid is None:
            bad.append((i, sid, btype, f'unknown pixel {px}')); continue
        # sea/lake provinces legitimately host naval structures
        if ptype.get(pid) in ('sea', 'lake') and btype.startswith(('naval_', 'floating_', 'coastal_', 'dockyard')):
            continue
        owner = prov2state.get(pid)
        if owner is None:
            bad.append((i, sid, btype, f'prov {pid} stateless'))
        elif owner != sid and not btype.startswith('naval_'):
            bad.append((i, sid, btype, f'pixel in state {owner}'))

if __name__ == '__main__':
    sys.exit(audit(*(sys.argv[1:] or [])))
