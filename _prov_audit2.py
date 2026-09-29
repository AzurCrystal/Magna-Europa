#!/usr/bin/env python3
"""Deep audit: build_railway path contiguity + start/end state checks; fix candidates."""
import json, os, re, collections

ROOT = r'D:/Projects/Magna-Europa'
mp = json.load(open(os.path.join(ROOT, '_mp.json')))
MOD_P = set(mp['mod_provinces'])
P2S = {int(k): v for k, v in mp['p2s'].items()}
ADJ = {int(k): set(v) for k, v in json.load(open(os.path.join(ROOT, '_adj.json'))).items()}
PTYPE = {}
with open(os.path.join(ROOT, 'map', 'definition.csv'), encoding='utf-8', errors='replace') as f:
    for line in f:
        p = line.strip().split(';')
        if len(p) < 5: continue
        try: PTYPE[int(p[0])] = p[4]
        except ValueError: pass
S2P = collections.defaultdict(list)
for p, s in P2S.items(): S2P[s].append(p)

# --- extract build_railway blocks with line numbers ---
rail = []  # (file, blockstart, pathline, pathlist, startline, startval, targetline, targetval)
for d in ['common', 'events']:
    for dirpath, _, names in os.walk(os.path.join(ROOT, d)):
        for n in names:
            if not n.endswith('.txt'): continue
            fp = os.path.join(dirpath, n)
            rel = os.path.relpath(fp, ROOT)
            try: lines = open(fp, encoding='utf-8-sig', errors='replace').read().split('\n')
            except OSError: continue
            depth = 0; in_rail = -1; rail_depth = -1
            cur = dict(path=None, pathln=None, sp=None, spln=None, tp=None, tpln=None)
            for i, raw in enumerate(lines, 1):
                code = raw.split('#', 1)[0]
                m = re.search(r'build_railway\s*=\s*\{', code)
                if m and in_rail < 0:
                    in_rail = i; rail_depth = depth + code[:m.end()].count('{') - code[:m.start()].count('}')
                    cur = dict(path=None, pathln=None, sp=None, spln=None, tp=None, tpln=None)
                if in_rail > 0:
                    pm = re.search(r'path\s*=\s*\{([^}]*)\}', code)
                    if pm: cur['path'] = [int(x) for x in pm.group(1).split()]; cur['pathln'] = i
                    pm2 = re.search(r'path\s*=\s*(\d+)', code)
                    if pm2 and '{' not in code: cur['path'] = [int(pm2.group(1))]; cur['pathln'] = i
                    sm = re.search(r'start_province\s*=\s*(\d+)', code)
                    if sm: cur['sp'] = int(sm.group(1)); cur['spln'] = i
                    tm = re.search(r'target_province\s*=\s*(\d+)', code)
                    if tm: cur['tp'] = int(tm.group(1)); cur['tpln'] = i
                depth += code.count('{') - code.count('}')
                if in_rail > 0 and depth < rail_depth:
                    rail.append((rel, in_rail, cur)); in_rail = -1

print(f'{len(rail)} build_railway blocks')
bad = []
for rel, bl, c in rail:
    path = c['path'] or []
    probs = []
    for j, p in enumerate(path):
        if p not in MOD_P: probs.append(f'member[{j}]={p} NOT_IN_MOD')
        elif PTYPE.get(p) != 'land': probs.append(f'member[{j}]={p} NON_LAND({PTYPE.get(p)})')
    for j in range(len(path) - 1):
        a, b = path[j], path[j + 1]
        if a in MOD_P and b in MOD_P and b not in ADJ.get(a, set()):
            probs.append(f'NON_ADJACENT {a}->{b} (pos {j})')
    sp, tp = c['sp'], c['tp']
    if sp is not None:
        if sp not in MOD_P: probs.append(f'start_province={sp} NOT_IN_MOD')
        elif PTYPE.get(sp) != 'land': probs.append(f'start_province={sp} NON_LAND')
    if tp is not None:
        if tp not in MOD_P: probs.append(f'target_province={tp} NOT_IN_MOD')
        elif PTYPE.get(tp) != 'land': probs.append(f'target_province={tp} NON_LAND')
    if path and sp is not None and sp in P2S and path[0] in P2S:
        if P2S[path[0]] != P2S[sp]:
            probs.append(f'path[0]={path[0]} state {P2S[path[0]]} != start {sp} state {P2S[sp]}')
    if path and tp is not None and tp in P2S and path[-1] in P2S:
        if P2S[path[-1]] != P2S[tp]:
            probs.append(f'path[-1]={path[-1]} state {P2S[path[-1]]} != target {tp} state {P2S[tp]}')
    if probs:
        bad.append((rel, c['pathln'] or bl, path, c['sp'], c['tp'], probs))
for rel, ln, path, sp, tp, probs in bad:
    print(f'\n{rel}:{ln}  path={path} sp={sp} tp={tp}')
    for pr in probs: print('   ', pr)
json.dump([dict(file=r, line=l, path=p, sp=s, tp=t, problems=pr) for r, l, p, s, t, pr in bad],
          open(os.path.join(ROOT, '_prov_rail_bad.json'), 'w'), indent=1)
print(f'\n{len(bad)} blocks with problems')
