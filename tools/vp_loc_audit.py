#!/usr/bin/env python
"""Audit victory-points localization vs mod state files.

For every province tagged as VP in history/states/*.txt:
  - must have a VICTORY_POINTS_<id> key in some loc file (else shows raw key)
  - stale-file vs regional-file collisions (last-loaded wins; victory_points_l_english.yml
    sorts last and would shadow regional names)

Usage: python tools/vp_loc_audit.py
"""
import re, glob, os, sys

def audit(locdir='localisation/english'):
    vp2state, mod_vps = {}, set()
    for f in glob.glob('history/states/*.txt'):
        m = re.match(r'(\d+)-(.+)\.txt', os.path.basename(f))
        if not m: continue
        body = open(f, encoding='utf-8', errors='replace').read()
        # findall: states can declare multiple victory_points blocks
        for vm in re.finditer(r'victory_points\s*=\s*\{([^}]*)\}', body):
            nums = [int(x) for x in vm.group(1).split() if x.isdigit()]
            for i in range(0, len(nums)-1, 2):
                mod_vps.add(nums[i]); vp2state[nums[i]] = m.group(2)
    loc = {}
    for f in sorted(glob.glob(f'{locdir}/*.yml')):
        base = os.path.basename(f)
        for line in open(f, encoding='utf-8', errors='replace'):
            for m in re.finditer(r'\bVICTORY_POINTS_(\d+)(?::\d+)?:?\s*"([^"]*)"', line):
                loc.setdefault(int(m.group(1)), []).append((m.group(2), base))
    missing = [p for p in sorted(mod_vps) if p not in loc]
    collisions = [(p, v) for p, v in loc.items() if len({n for n,_ in v}) > 1 and p in mod_vps]
    print(f'mod VPs: {len(mod_vps)} | loc\'d: {len(set(loc) & mod_vps)} | missing: {len(missing)} | name-collisions: {len(collisions)}')
    for p in missing[:30]: print(f'  missing loc: {p} (state {vp2state.get(p)})')
    for p, v in collisions[:30]: print(f'  collision: {p} -> {v}')
    return 1 if (missing or collisions) else 0

if __name__ == '__main__':
    sys.exit(audit(*(sys.argv[1:] or [])))
