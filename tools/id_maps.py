#!/usr/bin/env python
"""Build mod<->vanilla id maps by name matching.

Outputs to tools/out/:
  states_map.json     {mod_sid: {"name":..., "vanilla_sid": n|null, "vanilla_name":...}}
  provinces_map.json  {mod_pid: {"state":sid, "vanilla_pid": n|null}}  (VP-only)
  reverse maps included under "vanilla_to_mod" keys.

Name normalization: lowercase, strip accents/diacritics, collapse non-alnum.
Match rule: identical normalized state filename stem; VP match = identical
normalized English loc name when resolvable, else state-name anchor.

Usage:
  python tools/id_maps.py [--vanilla "C:/.../Hearts of Iron IV"]
Writes JSON only; prints a coverage summary.
"""
import re, glob, os, sys, json, unicodedata, argparse

def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '', s.lower())

def load_states(root):
    """-> {sid: {'name': str, 'vps': [pid,...]}}"""
    out = {}
    for f in glob.glob(os.path.join(root, 'history/states/*.txt')):
        m = re.match(r'(\d+)-(.+)\.txt', os.path.basename(f))
        if not m: continue
        sid, name = int(m.group(1)), m.group(2)
        body = open(f, encoding='utf-8', errors='replace').read()
        vps = []
        for vm in re.finditer(r'victory_points\s*=\s*\{([^}]*)\}', body):
            nums = [int(x) for x in vm.group(1).split() if x.isdigit()]
            vps += [nums[i] for i in range(0, len(nums)-1, 2)]
        out[sid] = {'name': name.replace('-', ' '), 'vps': vps}
    return out

def load_vp_names(locdir):
    """-> {pid: name} (first occurrence wins; tag-suffixed variants keyed separately are ignored)"""
    names = {}
    for f in sorted(glob.glob(os.path.join(locdir, '*.yml'))):
        for line in open(f, encoding='utf-8', errors='replace'):
            for m in re.finditer(r'\bVICTORY_POINTS_(\d+)(?::\d+)?:?\s*"([^"]*)"', line):
                names.setdefault(int(m.group(1)), m.group(2))
    return names

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla', default=r'C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV')
    ap.add_argument('--mod', default='.')
    ap.add_argument('--out', default='tools/out')
    a = ap.parse_args()
    m_states = load_states(a.mod)
    v_states = load_states(a.vanilla)
    m_names = load_vp_names(os.path.join(a.mod, 'localisation/english'))
    v_names = load_vp_names(os.path.join(a.vanilla, 'localisation/english'))

    # state map: primary anchor = same state id (mod keeps vanilla numbering for
    # European states); secondary = normalized name match for remapped states
    vnorm = {norm(v['name']): sid for sid, v in v_states.items()}
    smap, unmatched_m = {}, []
    for sid, s in m_states.items():
        vsid = sid if sid in v_states else vnorm.get(norm(s['name']))
        how = 'same-id' if sid in v_states else ('name' if vsid else None)
        smap[sid] = {'name': s['name'], 'vanilla_sid': vsid, 'match': how,
                     'vanilla_name': v_states.get(vsid, {}).get('name')}
        if vsid is None: unmatched_m.append((sid, s['name']))
    mnorm = {norm(v['name']): sid for sid, v in m_states.items()}
    un_v = [(sid, s['name']) for sid, s in v_states.items()
            if sid not in m_states and norm(s['name']) not in mnorm]
    # in the vanilla state with the same name; additionally match by loc name.
    v_state_vps = {sid: v['vps'] for sid, v in v_states.items()}
    pmap = {}
    for msid, s in m_states.items():
        vsid = smap[msid]['vanilla_sid']
        vvps = v_state_vps.get(vsid, [])
        vname_by_pid = {pid: v_names.get(pid) for pid in vvps}
        for mpid in s['vps']:
            cand = None
            mname = m_names.get(mpid)
            # primary: same pid present among the mapped vanilla state's VPs
            if mpid in vvps: cand = mpid
            elif mname:
                for vpid, vn in vname_by_pid.items():
                    if vn and norm(vn) == norm(mname): cand = vpid; break
            pmap[mpid] = {'state': msid, 'vanilla_pid': cand,
                          'match': 'same-id' if cand == mpid else ('name' if cand else None),
                          'mod_name': mname,
                          'vanilla_name': v_names.get(cand) if cand else None}

    os.makedirs(a.out, exist_ok=True)
    json.dump(smap, open(f'{a.out}/states_map.json', 'w'), indent=1, sort_keys=True)
    json.dump(pmap, open(f'{a.out}/provinces_map.json', 'w'), indent=1, sort_keys=True)
    matched = sum(1 for v in smap.values() if v['vanilla_sid'] is not None)
    vp_match = sum(1 for v in pmap.values() if v['vanilla_pid'] is not None)
    print(f'states: mod {len(m_states)} -> matched {matched} ({len(unmatched_m)} no vanilla twin)')
    print(f'vanilla states with no mod twin: {len(un_v)}')
    print(f'VPs: mod {len(pmap)} -> matched {vp_match}')
    print(f'-> {a.out}/states_map.json, provinces_map.json')
    return 0

if __name__ == '__main__':
    sys.exit(main())
