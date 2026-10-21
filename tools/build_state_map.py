#!/usr/bin/env python
"""Rebuild the vanilla<->Magna Europa state/region/VP truth tables into docs/states/.

  python tools/build_state_map.py                 # mod root via ME_MOD or auto-discovery under mods/*/
  ME_MOD=<repo> HOI4_DIR=<game dir> python tools/build_state_map.py
"""
import re, glob, os, json, csv
from collections import defaultdict

from _modroot import mod_root, repo_root

MOD = os.environ.get("ME_MOD") or mod_root()
V = os.environ.get("HOI4_DIR") or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
OUT = os.path.join(repo_root(), "docs", "states")

def strip_comments(s): return re.sub(r"#[^\n]*","",s)
def grab(src, key):
    # brace-depth extractor: `key = { ... }` (nested-safe)
    m = re.search(r"\b" + re.escape(key) + r"\s*=\s*\{", src)
    if not m: return ""
    depth, i, start = 0, m.end()-1, m.end()
    for i in range(m.end()-1, len(src)):
        c = src[i]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return src[start:i]
    return src[start:]
def ints(s): return [int(x) for x in re.findall(r"\d+", s)]
def parse_loc(path):
    out = {}
    for line in open(path, encoding="utf-8-sig", errors="replace"):
        m = re.match(r'\s*(\w+)(?::\d+)?\s*[=:]?\s*"([^"]*)"', line)
        if m: out[m.group(1)] = m.group(2)
    return out

v_state_loc, me_state_loc = {}, {}
v_vp_loc, me_vp_loc = {}, {}
v_reg_loc, me_reg_loc = {}, {}
for f in glob.glob(os.path.join(V,"localisation/english/*.yml")):
    for k,v in parse_loc(f).items():
        if k.startswith("STATE_") and k not in v_state_loc: v_state_loc[k]=v
        if k.startswith("VICTORY_POINTS_") and k not in v_vp_loc: v_vp_loc[k]=v
        if k.startswith("STRATEGICREGION_") and k not in v_reg_loc: v_reg_loc[k]=v
for f in glob.glob(os.path.join(MOD,"localisation/english/*.yml")):
    for k,v in parse_loc(f).items():
        if k.startswith("STATE_") and k not in me_state_loc: me_state_loc[k]=v
        if k.startswith("VICTORY_POINTS_") and k not in me_vp_loc: me_vp_loc[k]=v
        if k.startswith("STRATEGICREGION_") and k not in me_reg_loc: me_reg_loc[k]=v

def parse_states(root, loc):
    out = {}
    for f in glob.glob(os.path.join(root,"history/states/*.txt")):
        sid = int(os.path.basename(f).split("-")[0])
        src = open(f, encoding="utf-8-sig", errors="replace").read()
        hist = grab(src, "history")
        vps = []
        for m in re.finditer(r"victory_points\s*=\s*\{([^}]*)\}", hist):
            vals = ints(m.group(1))
            for i in range(0,len(vals),2):
                vps.append((vals[i], vals[i+1] if i+1<len(vals) else 0))
        owner_m = re.search(r"\bowner\s*=\s*([A-Z]{3,4})", hist)
        out[sid] = dict(
            file=os.path.basename(f),
            name=loc.get(f"STATE_{sid}") or os.path.basename(f).split("-",1)[1][:-4],
            vps=vps,
            owner=owner_m.group(1) if owner_m else None,
            cores=re.findall(r"add_core_of\s*=\s*([A-Z]{3,4})", hist),
            provinces=ints(grab(src,"provinces")),
            cat=(re.search(r"state_category\s*=\s*(\w+)",src) or [None,None])[1],
            manpower=(re.search(r"manpower\s*=\s*(\d+)",src) or [None,None])[1],
        )
    return out

v_states = parse_states(V, v_state_loc)
me_states = parse_states(MOD, me_state_loc)

def parse_regions(root, loc):
    reg = {}
    for f in glob.glob(os.path.join(root,"map/strategicregions/*.txt")):
        rid = int(os.path.basename(f).split("-")[0])
        src = open(f, encoding="utf-8-sig", errors="replace").read()
        reg[rid] = dict(
            file=os.path.basename(f),
            name=loc.get(f"STRATEGICREGION_{rid}") or os.path.basename(f).split("-",1)[1][:-4],
            provs=ints(grab(src,"provinces")))
    return reg
v_regions = parse_regions(V, v_reg_loc); me_regions = parse_regions(MOD, me_reg_loc)

# state -> primary region (province count majority)
def state_region(states, regions, sid):
    pset = set(states[sid]["provinces"]); best=(None,0)
    for rid,rd in regions.items():
        c = len(pset & set(rd["provs"]))
        if c > best[1]: best = (rid,c)
    return best[0]

# ---------- vanilla → ME primary map ----------
# source 1: migration comments — 3 line shapes:
#   `X = Y # was vanilla N`  /  `X # was vanilla N`  /  `X = { ... # was vanilla N` (block key)
comment_votes = {}   # vanilla_id -> {me_id: votes}
prov_pairs_all = {}  # rejected by two-end state filter (province-level ids)
_pair_re = re.compile(r"was vanilla\s+(\d+)")
for pat in ["common/**/*.txt","events/*.txt","history/**/*.txt"]:
    for f in glob.glob(os.path.join(MOD, pat), recursive=True):
        for line in open(f, encoding="utf-8-sig", errors="replace"):
            cm = _pair_re.search(line)
            if not cm: continue
            old_id = int(cm.group(1))
            lhs = line[:cm.start()]
            new_id = None
            m1 = re.match(r"\s*\w+\s*=\s*(\d+)\s*#", lhs)          # `X = N #`
            m2 = re.match(r"\s*(\d+)\s*#", lhs)                    # `N #`
            m3 = re.search(r"(\d+)\s*=\s*\{[^}]*#?\s*$", lhs)      # `N = { ... #`
            m4 = re.match(r"\s*(\d+)\s*=\s*\{?", lhs)              # `N = {` block opener
            for mm in (m1,m2,m3,m4):
                if mm: new_id = int(mm.group(1)); break
            if new_id is None:
                # e.g. `states = { 119 127 } # was vanilla 195` — multi-id sets; grab all ints
                # left of the note (`# 1.19-migration:` would otherwise leak `1` and `19` as ids,
                # and a fully commented-out line would leak every id on it)
                ns = [int(x) for x in re.findall(r"\d+", lhs.split("#",1)[0])]
                if len(ns) >= 1 and lhs.strip().startswith(("states","controls_state","add_state_claim","set_state_owner")):
                    for n in ns:
                        if n in me_states and old_id in v_states:
                            comment_votes.setdefault(old_id,{}).setdefault(n,0); comment_votes[old_id][n]+=1
                    continue
                prov_pairs_all.setdefault(old_id,set()).add(tuple(ns) if ns else "?"  )
                continue
            if new_id in me_states and old_id in v_states:
                comment_votes.setdefault(old_id,{}).setdefault(new_id,0); comment_votes[old_id][new_id]+=1
            else:
                prov_pairs_all.setdefault(old_id,set()).add(new_id)

v2me = {k: max(v,key=v.get) for k,v in comment_votes.items()}
src = {k:"comment" for k in v2me}
os.makedirs(OUT, exist_ok=True)
json.dump({str(k):sorted(v,key=str) for k,v in prov_pairs_all.items()}, open(os.path.join(OUT,"province_pairs.json"),"w"), indent=0)
print("comment pairs: vanilla",len(v2me),"| province-level rejects",sum(len(v) for v in prov_pairs_all.values()))

# source 2: name match — exact norm, then containment ("Leningrad" ⊂ "Leningrad Area")
import unicodedata
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]","",s)
me_by_norm = defaultdict(list)
for ms,d in me_states.items(): me_by_norm[norm(d["name"])].append(ms)
def name_cands(vn, vowner):
    # returns me_ids matching name; prefer same owner
    exact = me_by_norm.get(vn, [])
    if exact: return exact, "name"
    cands = []
    # containment is fuzzy → only accept when exactly ONE me candidate survives
    # (prevents "Isfahan" claiming both a real Isfahan shard and an unrelated Hormozgan shard)
    for mn, ids in me_by_norm.items():
        if not mn or not vn: continue
        if (len(vn) >= 4 and (vn in mn or mn in vn)):
            cands += ids
    if len(cands) != 1: return [], "name~"
    return cands, "name~"
for vs,d in v_states.items():
    if vs in v2me: continue
    vn = norm(d["name"])
    cands, basis = name_cands(vn, d["owner"])
    if not cands: continue
    same_owner = [c for c in cands if me_states[c]["owner"] == d["owner"]]
    pool = same_owner or cands
    v2me[vs] = sorted(pool)[0]; src[vs] = basis

# source 3: same-id + same-name (untouched states)
for vs,d in v_states.items():
    if vs in v2me: continue
    if vs in me_states and norm(me_states[vs]["name"]) == norm(d["name"]):
        v2me[vs] = vs; src[vs]="same-id"

# source 4: manual overrides — hand-maintained pairs never reachable by comments/names.
# Keep synchronized with repo facts (AGENTS.md "ME remap facts"); add entries when a
# split member is only discoverable by human/domain knowledge.
MANUAL = {
    # vanilla_id: [me_ids] — extend members; primary stays whatever votes already chose
    77:  [1579, 818],         # Dobrogea/S.Dobrudja → Durostor, Bazargic (Bulgarian-ceded 1913 shards)
    971: [767, 793],          # Northern Dobruja → Tulcea + Constanța (v971's naval-base shard)
}
# ME-side claim where v2m itself has no row for this me_id
MANUAL_ME2V = {
    435:  44,   # Brent ⊂ London (v44 Middlesex members {1521,1523}; Brent is the remaining London shard)
    51:  100,   # Vesturland (West Iceland shard; RBF extrapolation nearest=Labrador is wrong)
    44:  100,   # Vestfirðir (Westfjords; same Iceland cluster)
    187: 120,   # Orkney (Scottish cluster)
    158: 120,   # Caithness (Scottish cluster)
    211: 120,   # Kinross (Scottish cluster)
    2781:120,   # Outer Hebrides (Scottish cluster)
    2552:332,   # American Border (Labrador edge shard)
    40:  100,   # Norðurland eystra (Iceland shard; warp outlier)
    48:  101,   # Austurland (Iceland shard; nearest vanilla land is Greenland)
    60:  464,   # Prince Edward Island (Nova Scotia shard; island off-hull)
    901: 170,   # Soria (Extremadura; ME Iberia redraw, centroid outside hull)
    2462:825,   # Novaya Zemlya (Nenets; arctic island off-hull)
}
for vid, mids in MANUAL.items():
    for mid in mids:
        if mid in me_states:
            comment_votes.setdefault(vid, {}).setdefault(mid, 0)
            v2me.setdefault(vid, mid); src.setdefault(vid, "manual")
for mid, vid in MANUAL_ME2V.items():
    if mid in me_states and vid in v_states:
        comment_votes.setdefault(vid, {}).setdefault(mid, 0)

# ---------- ME → vanilla map: every member claims the vanilla ancestor ----------
me2v = {}
me_basis = {}
for vs,ms in v2me.items():
    me2v[ms] = vs; me_basis[ms] = src[vs]
for vs, members in comment_votes.items():
    if vs not in v_states: continue
    manual_pair = vs in MANUAL or vs in MANUAL_ME2V.values()
    for ms in members:
        if ms in me2v: continue
        me2v[ms] = vs; me_basis[ms] = "manual" if manual_pair else "comment(member)"
# name-singleton ancestry: unclaimed ME state whose norm-name equals a vanilla state
# → subdivision descendant of it. Lowest precedence.
for vs,d in v_states.items():
    vn = norm(d["name"])
    if not vn: continue
    for mid in me_by_norm.get(vn, []):
        if mid not in me2v:
            me2v[mid] = vs; me_basis[mid] = "name-singleton"

# ---------- pixel-geo attribution: spatial descendant census ----------
# `# was vanilla N` comments only tag the STATE-level descendants the porter
# remembered to mark. Heavily subdivided regions (Ukraine: 10 vanilla → ~80 ME
# states) leave most shards unclaimed → v2m.me_members was ancestry-only and
# under-covers. Fix: reverse-warp every ME province centroid into vanilla pixel
# space, look up which vanilla state that pixel belongs to, majority-vote.
#
# Pipeline:
#   1. province pixel centroids from both provinces.bmp + definition.csv
#   2. control points = vps.tsv-style pairs (vanilla VP prov -> ME VP prov by name)
#   3. Delaunay piecewise-affine warp  me_px -> v_px  (handles differing map
#      size 5632x2048 vs 4096x3072 AND differing projection)
#   4. ME prov centroid -> vanilla pixel -> province -> state; weight vote by
#      province pixel area
#   5. me2v claims ONLY where no higher basis exists; member lists union.
# Confidence: me2v basis "pixel-geo" (>=50% vote) / "pixel-geo-low" (<50%).

def _prov_centroids(bmp_path, def_path):
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(bmp_path), dtype=np.int32)
    H, W, _ = im.shape
    flat = ((im[...,0]<<16)|(im[...,1]<<8)|im[...,2]).ravel()
    order = np.argsort(flat, kind='stable')
    sf = flat[order]
    uniq, start = np.unique(sf, return_index=True)
    counts = np.diff(np.append(start, len(sf)))
    xs = np.tile(np.arange(W, dtype=np.int64), H)
    ys = np.repeat(np.arange(H, dtype=np.int64), W)
    sx = np.add.reduceat(xs[order], start)/counts
    sy = np.add.reduceat(ys[order], start)/counts
    pid = {}
    for line in open(def_path, encoding='utf-8', errors='replace'):
        p = line.split(';')
        if len(p) >= 4:
            try: pid[(int(p[1])<<16)|(int(p[2])<<8)|int(p[3])] = int(p[0])
            except ValueError: pass
    out = {}
    for i in range(len(uniq)):
        c = int(uniq[i])
        if c in pid: out[pid[c]] = (float(sx[i]), float(sy[i]), int(counts[i]))
    return out, pid

v_cent, v_c2p = _prov_centroids(os.path.join(V,"map/provinces.bmp"), os.path.join(V,"map/definition.csv"))
m_cent, _ = _prov_centroids(os.path.join(MOD,"map/provinces.bmp"), os.path.join(MOD,"map/definition.csv"))

# prov -> state maps (both sides) for the vote lookup
v_p2s, m_p2s = {}, {}
for sid, d in v_states.items():
    for p in d["provinces"]: v_p2s[p] = sid
for sid, d in me_states.items():
    for p in d["provinces"]: m_p2s[p] = sid

# control points: vanilla VP province -> same-named ME VP province
_anchors = []
me_vp_prov_by_name = {}
for sid, d in me_states.items():
    for pid, pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm: me_vp_prov_by_name[norm(nm)] = pid
for vs, d in v_states.items():
    for pid, pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        mp = me_vp_prov_by_name.get(norm(nm)) if nm else None
        if mp and pid in v_cent and mp in m_cent:
            _anchors.append((pid, mp))

import numpy as _np
from scipy.interpolate import Rbf as _Rbf
from scipy.spatial import Delaunay as _Delaunay, cKDTree as _cKDTree
from scipy.interpolate import LinearNDInterpolator as _LNI
from PIL import Image as _Image
_v_pts = _np.array([v_cent[v][:2] for v,_ in _anchors])
_m_pts = _np.array([m_cent[m][:2] for _,m in _anchors])
# Hybrid warp: Delaunay piecewise-affine inside the anchor hull (exact at anchors,
# correct wherever VPs are dense), RBF thin-plate for extrapolation beyond it
# (islands / map edges). Smoothed RBF is not trusted inside the hull — its
# ill-conditioned solve gave zero-residual anchors but folded space elsewhere.
_tri = _Delaunay(_m_pts)
_warp = _LNI(_tri, _v_pts)
_rbf_x = _Rbf(_m_pts[:,0], _m_pts[:,1], _v_pts[:,0], function='thin_plate', smooth=1.0)
_rbf_y = _Rbf(_m_pts[:,0], _m_pts[:,1], _v_pts[:,1], function='thin_plate', smooth=1.0)
_vbmp = _np.asarray(_Image.open(os.path.join(V,"map/provinces.bmp")), dtype=_np.int32)
_VH,_VW,_ = _vbmp.shape
_land_ids = _np.array([p for p in v_cent if p in v_p2s])
_land_xy = _np.array([v_cent[p][:2] for p in _land_ids])
_kdt = _cKDTree(_land_xy)
_RINGS = [(dx,dy) for r in range(0,13) for dx in range(-r,r+1) for dy in ([-r,r] if abs(dx)<r else range(-r,r+1))]
_px_votes = {}
for mp,(x,y,n) in m_cent.items():
    ms = m_p2s.get(mp)
    if not ms: continue
    inside = _tri.find_simplex([(x,y)])[0] >= 0
    if inside:
        v_xy = _warp([(x,y)])[0]; vx, vy = float(v_xy[0]), float(v_xy[1])
    else:
        vx, vy = float(_rbf_x(x,y)), float(_rbf_y(x,y))
    vid = None
    for dx,dy in _RINGS:
        x2,y2 = int(round(vx))+dx, int(round(vy))+dy
        if 0<=x2<_VW and 0<=y2<_VH:
            c = int((_vbmp[y2,x2,0]<<16)|(_vbmp[y2,x2,1]<<8)|_vbmp[y2,x2,2])
            vid = v_p2s.get(v_c2p.get(c))
            if vid: break
    if not vid:
        # nearest LAND province centroid, only if reasonably close — beyond
        # ~50px the warp has sailed into ocean/edge and nearest-land is fiction
        dist, idx = _kdt.query([vx,vy])
        if dist <= 50: vid = v_p2s[_land_ids[idx]]
        else: continue
    _px_votes.setdefault(ms, {}).setdefault(vid, 0)
    _px_votes[ms][vid] += n
pixel_ancestor = {}          # me_id -> (v_id, share)
for ms, votes in _px_votes.items():
    tot = sum(votes.values())
    vid, share = max(votes.items(), key=lambda kv: kv[1])
    pixel_ancestor[ms] = (vid, share/tot)
print("pixel anchors:", len(_anchors), "| me states attributed:", len(pixel_ancestor))

# vp-geo ancestry: ME mod-new state whose named VPs match vanilla VP names →
# modal vanilla state of those VPs. (Ajaccio→v1 Corsica, Linz→v4 Lower Austria)
v_vp_to_state = {}
for vs,d in v_states.items():
    for pid,pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm: v_vp_to_state.setdefault(norm(nm), vs)
for ms,d in me_states.items():
    if ms in me2v or not d["vps"]: continue
    votes = defaultdict(int)
    for pid,pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm and norm(nm) in v_vp_to_state:
            votes[v_vp_to_state[norm(nm)]] += 1
    if votes:
        best = max(votes, key=votes.get)
        me2v[ms] = best; me_basis[ms] = "vp-geo"
# pixel-geo claims: last-mile fallback — spatial majority ancestor for any ME
# state still unclaimed after comment/manual/name/vp-geo chains.
for ms,(vid,share) in pixel_ancestor.items():
    if ms in me2v: continue
    me2v[ms] = vid; me_basis[ms] = "pixel-geo" if share >= 0.5 else "pixel-geo-low"
# ---------- status ----------
# vanilla -> [me ids] all candidates (comment map can have many)
comment_all = {k: set(v.keys()) for k,v in comment_votes.items()}

rows_v2m = []
for vs in sorted(v_states):
    d = v_states[vs]
    ms = v2me.get(vs)
    members = sorted(comment_all.get(vs,{ms})) if (vs in comment_all or ms) else []
    # include ME-side descendants that claimed this vanilla ancestor without a comment
    # (name-singleton/manual ME→V), so v2m member lists stay symmetric
    extra = [mid for mid,vid in me2v.items() if vid == vs and mid not in members]
    members_all = members + sorted(set(extra))
    if not ms and members_all:
        # pixel-geo fallback primary: majority-voted spatial descendant when no
        # claimed candidate exists (status stays 1:many/1:1 by member count)
        px = [(pixel_ancestor[mid][1], mid) for mid in members_all
              if mid in pixel_ancestor and pixel_ancestor[mid][0] == vs]
        if px: ms = max(px)[1]
        elif vs not in src: src[vs] = "pixel-geo"
    status = "unmapped"
    if len(members_all) == 1: status = "1:1"
    elif members_all: status = "1:many"
    rows_v2m.append(dict(
        vanilla_id=vs, vanilla_name=d["name"], owner=d["owner"],
        v_prov_count=len(d["provinces"]), region=state_region(v_states, v_regions, vs),
        me_primary=ms or "", me_members="|".join(map(str,members_all)),
        me_names="|".join(me_states[i]["name"] if i in me_states else f"?!{i}" for i in members_all),
        basis=src.get(vs,""), status=status,
    ))

rows_m2v = []
for ms in sorted(me_states):
    d = me_states[ms]
    vs = me2v.get(ms)
    rows_m2v.append(dict(
        me_id=ms, me_name=d["name"], owner=d["owner"],
        prov_count=len(d["provinces"]), region=state_region(me_states, me_regions, ms),
        cat=d["cat"], manpower=d["manpower"],
        v_ancestor=(vs or ""), v_name=(v_states[vs]["name"] if vs in v_states else ""),
        basis=me_basis.get(ms,""), status="mapped" if vs else "mod-new",
    ))

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT,"v2m_states.tsv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_v2m[0].keys()), delimiter="\t")
    w.writeheader(); w.writerows(rows_v2m)
with open(os.path.join(OUT,"m2v_states.tsv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_m2v[0].keys()), delimiter="\t")
    w.writeheader(); w.writerows(rows_m2v)

# region table: primary pair = modal vanilla region of member states' ancestors
reg_state_members = defaultdict(list)
for ms,d in me_states.items():
    reg_state_members[state_region(me_states, me_regions, ms)].append(ms)
v_reg_by_norm = {norm(d["name"]): (rid,d) for rid,d in v_regions.items()}
reg_rows = []
for rid,d in sorted(me_regions.items()):
    cnt = defaultdict(int)
    for ms in reg_state_members.get(rid,[]):
        va = me2v.get(ms)
        if va in v_states:
            vr = state_region(v_states, v_regions, va)
            if vr is not None: cnt[vr] += 1
    modal = max(cnt, key=cnt.get) if cnt else None
    match = v_reg_by_norm.get(norm(d["name"]))
    vrid = modal if modal is not None else (match[0] if match else "")
    vname = v_regions[modal]["name"] if modal else (match[1]["name"] if match else "")
    reg_rows.append(dict(me_region=rid, me_name=d["name"], v_region=vrid, v_name=vname,
                         status="member-modal" if modal else ("name" if match else "mod-new")))
with open(os.path.join(OUT,"regions.tsv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(reg_rows[0].keys()), delimiter="\t")
    w.writeheader(); w.writerows(reg_rows)

# vp table: vanilla vp -> ME vp by normalized name
def _lev1(a, b):
    # True iff a,b differ by ≤1 char AND the differing position is a boundary
    # (insertion/deletion/change at either end). Catches Roma/Rome, Vienne/Vienna,
    # Gent/Ghent, Genova/Genoa, Lisboa/Lisbon — rejects Yap/Gap, Macau/Bacău, Dobo/Doboj.
    if a == b: return True
    if min(len(a),len(b)) < 4: return False               # too short → any 1-edit is coincidence-prone
    if a[1:] == b[1:] or a[:-1] == b[:-1]: return True    # boundary insertion/deletion
    if len(a) == len(b) and a[1:-1] == b[1:-1] and (a[0] != b[0]) + (a[-1] != b[-1]) <= 1:
        return True                                       # boundary substitution
    return False
me_vp_by_name = defaultdict(list)
for sid,d in me_states.items():
    for pid,pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm: me_vp_by_name[norm(nm)].append((sid,pid,pts,nm))
vp_rows=[]
print("v vp loc:", len(v_vp_loc), "me vp loc:", len(me_vp_loc), "v_states with vps:", sum(1 for d in v_states.values() if d["vps"]))
for vs,d in sorted(v_states.items()):
    # descendant states of this vanilla state (comment members + name-singleton/manual claims)
    desc = set(comment_all.get(vs, set())) | {mid for mid,vid in me2v.items() if vid == vs}
    for pid,pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        cands = me_vp_by_name.get(norm(nm), [])
        status = "name" if cands else "unmapped"
        if not cands and nm:
            # boundary-edit fallback (Rome↔Roma, Ghent↔Gent); must be unique AND
            # provably inside this state's descendant set — speculative match needs proof
            near = [(k,v) for k,v in me_vp_by_name.items()
                    if _lev1(k, norm(nm)) and v and v[0][0] in desc]
            if len(near) == 1:
                cands = near[0][1]; status = "near"
        # geo-consistency gate: candidate must sit in a descendant state of this vanilla state
        if cands:
            anc = me2v.get(cands[0][0])
            # flag only when the candidate is provably elsewhere: its ancestor is a
            # different vanilla state, or this state has a known member set and it's not in it
            if (anc is not None and anc != vs) or (desc and cands[0][0] not in desc):
                status = "cross-state"
        vp_rows.append(dict(v_state=vs, v_state_name=d["name"], v_prov=pid, v_pts=pts, vp_name=nm,
                            me_state=cands[0][0] if cands else "", me_prov=cands[0][1] if cands else "",
                            me_pts=cands[0][2] if cands else "", me_name=cands[0][3] if cands else "",
                            status=status))
with open(os.path.join(OUT,"vps.tsv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(vp_rows[0].keys()), delimiter="\t")
    w.writeheader(); w.writerows(vp_rows)

from collections import Counter
print("v2m status:", Counter(r["status"] for r in rows_v2m))
print("m2v status:", Counter(r["status"] for r in rows_m2v))
print("region status:", Counter(r["status"] for r in reg_rows))
print("vp status:", Counter(r["status"] for r in vp_rows))
print("written: docs/states/{v2m_states,m2v_states,regions,vps}.tsv + province_pairs.json")
