#!/usr/bin/env python
"""Rebuild the vanilla<->Magna Europa state/region/VP truth tables into docs/states/.

  python tools/build_state_map.py                 # repo root derived from this file
  ME_MOD=<repo> HOI4_DIR=<game dir> python tools/build_state_map.py
"""
import re, glob, os, json, csv
from collections import defaultdict

MOD = os.environ.get("ME_MOD") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = os.environ.get("HOI4_DIR") or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
OUT = os.path.join(MOD, "docs", "states")

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
                # e.g. `states = { 119 127 } # was vanilla 195` — multi-id sets; grab all ints on line
                ns = [int(x) for x in re.findall(r"\d+", lhs)]
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
