#!/usr/bin/env python
"""Audit map/buildings.txt placement against the state/province map.

buildings.txt format: state_id;type;x;y;z;rotation;province
x = provinces.bmp column, z = (bmp_h - 1) - bmp_row.

States are keyed by their internal `id=` and their province lists are read
by the canonical loader shared with render_state_overlay.py (the filename is
ignored: history/states/1956-Kemaliye.txt declares id=1959).

The coordinate mapping is `row = bmp_h - 1 - int(z)`: under it every free
land-only row of vanilla's own buildings.txt (54,327) and of ME's (125,331)
lands on land owned by its declared state, while the former `row = bmp_h - z`
mapping produced 254 "water" + 329 cross-state ME rows. x is sampled at
`int(x)` (rounding x instead puts one of those rows on water); truncating z
before the flip and truncating after it agree on every sampled row, so no
other rounding policy is applied.

Placement rules:
* the 7th field is per type. floating_harbor anchors a land province of the
  declared state (the model itself sits in water, so its XYZ is not a land
  claim); a sea/lake anchor is a warning, not an accepted class.
  naval_base / naval_base_spawn / naval_supply_hub / naval_headquarters carry
  a sea access province (0 allowed, as vanilla hub/HQ leave it) and their XYZ
  still names the land province they stand on, so they are pixel-checked and
  a non-sea anchor is a warning.
* placements without a 7th field are sampled as before: a land-only type
  over sea/lake is an error, and a pixel in another state is a warning.
  Water-capable types keep their water exemption (naval_* models are
  land-placed and are no longer exempt).
* a sampled row on the first or last raster row (row 0 / row bmp_h-1) gets a
  boundary warning: the coordinate is an endpoint of the mapping above, but
  native Nudge rendering of that row is still unverified, so it is reported
  instead of being counted as an error or silently accepted. No map data is
  moved on this basis.

Exit status is 1 when findings exist (errors or warnings), 0 otherwise.

Usage: python tools/buildings_audit.py [mapdir]
"""
import sys, csv
import numpy as np
from PIL import Image

from _modroot import cd_mod
from render_state_overlay import load_state_provinces
cd_mod()  # run against mods/<name>/ payload root (ME_MOD to override)

# 7th-field semantics proven on the shipped data: floating_harbor anchors a
# LAND province (vanilla 2334/2334 land; ME 2573 land + 2 lake); the four
# naval_* types carry a SEA access province (vanilla naval_base_spawn
# 2362/2362 sea, vanilla hub/HQ leave it 0; ME sea 2563/2575/2575/2575)
# while their model stands on land of the declared state (100% of vanilla+ME
# rows), so the sea field must not exempt their XYZ.
FLOATING_ANCHOR = {'floating_harbor'}
SEA_ACCESS = {'naval_base', 'naval_base_spawn', 'naval_supply_hub',
              'naval_headquarters'}
ANCHORED = FLOATING_ANCHOR | SEA_ACCESS
# Water-capable types stay exempt; naval_* models are land-placed (see above).
WATER_OK = ('floating_', 'coastal_', 'dockyard')


def audit(mapdir='map', bmp_h=3072):
    im = np.array(Image.open(f'{mapdir}/provinces.bmp').convert('RGB'))
    rgb2id, ptype = {}, {}
    with open(f'{mapdir}/definition.csv', encoding='utf-8', errors='replace') as fh:
        for row in csv.reader(fh, delimiter=';'):
            if row and row[0].isdigit():
                rgb2id[(int(row[1]), int(row[2]), int(row[3]))] = int(row[0])
                ptype[int(row[0])] = row[4] if len(row) > 4 else 'land'
    prov2state = {}
    for sid, provs in load_state_provinces('history/states').items():
        for p in provs:
            prov2state[p] = sid

    def water(pid):
        return ptype.get(pid) in ('sea', 'lake')

    errors, warnings = [], []
    for i, line in enumerate(open(f'{mapdir}/buildings.txt', encoding='utf-8'), 1):
        p = line.split(';')
        if len(p) < 6 or not p[0].strip().isdigit():
            continue
        sid = int(p[0]); btype = p[1]
        prov = int(p[6]) if len(p) > 6 and p[6].strip().isdigit() else 0
        if prov and btype in ANCHORED:
            owner = prov2state.get(prov)
            if prov not in ptype:
                errors.append((i, sid, btype, f'anchor prov {prov} unknown'))
                continue
            if btype in FLOATING_ANCHOR:
                if water(prov):
                    warnings.append((i, sid, btype,
                                     f'anchor prov {prov} {ptype.get(prov)} '
                                     '(floating_harbor expects land)'))
                elif owner is None:
                    warnings.append((i, sid, btype, f'anchor prov {prov} unassigned land'))
                elif owner != sid:
                    warnings.append((i, sid, btype, f'anchor prov {prov} in state {owner}'))
                continue  # harbour model sits in water; XYZ is not a land claim
            if ptype.get(prov) != 'sea':
                warnings.append((i, sid, btype,
                                 f'anchor prov {prov} {ptype.get(prov)} '
                                 f'({btype} expects sea access)'))
            # fall through: the model's XYZ still names its land province
        xf = float(p[2]); zf = float(p[4])
        if not (0 <= xf < im.shape[1] and 0 <= zf <= bmp_h - 1):
            errors.append((i, sid, btype, f'out-of-bounds x={p[2]} z={p[4]}'))
            continue
        x = int(xf); row = bmp_h - 1 - int(zf)
        if row in (0, bmp_h - 1):
            warnings.append((i, sid, btype,
                             f'boundary row {row} (native Nudge pending)'))
        px = tuple(im[row, x]); pid = rgb2id.get(px)
        if pid is None:
            errors.append((i, sid, btype, f'unknown pixel {px}'))
            continue
        if water(pid) and btype.startswith(WATER_OK):
            continue
        owner = prov2state.get(pid)
        if owner is None:
            errors.append((i, sid, btype,
                           f'{"water" if water(pid) else "no state"} prov {pid}'))
        elif owner != sid:
            warnings.append((i, sid, btype, f'pixel in state {owner} (prov {pid})'))
    print(f'buildings bad: {len(errors) + len(warnings)} '
          f'({len(errors)} errors, {len(warnings)} warnings)')
    for label, rows in (('error', errors), ('warning', warnings)):
        if rows:
            print(f'{label}s:')
        for b in rows:
            print(' ', *b)
    return 1 if errors or warnings else 0


if __name__ == '__main__':
    sys.exit(audit(*(sys.argv[1:] or [])))
