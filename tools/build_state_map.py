#!/usr/bin/env python
"""Rebuild the vanilla<->Magna Europa state/region/VP truth tables and raw mapping evidence into docs/states/.

  python tools/build_state_map.py                 # mod root via ME_MOD or auto-discovery under mods/*/
  ME_MOD=<repo> HOI4_DIR=<game dir> python tools/build_state_map.py
"""
import re, glob, os, json, csv
from collections import defaultdict

from _modroot import mod_root, repo_root

MOD = os.environ.get("ME_MOD") or mod_root()
V = os.environ.get("HOI4_DIR") or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
OUT = os.path.join(repo_root(), "docs", "states")
EVIDENCE_COLUMNS = [
    "me_id", "me_name", "owner", "me_region", "ancestor", "ancestor_name",
    "basis", "status", "manual_override", "pixel_best", "pixel_share",
    "pixel_runner", "pixel_runner_share", "pixel_margin", "comment_best",
    "comment_votes", "comment_runner", "comment_runner_votes", "comment_margin",
    "name_norm", "name_exact_me_ids", "name_collision_count", "vp_best", "vp_votes",
    "vp_runner", "vp_runner_votes", "vp_margin", "me_core_tags", "ancestor_core_tags",
    "me_vp_count", "ancestor_vp_count",
]

def _rank_votes(votes):
    """Return deterministic top-two (candidate, vote count) pairs."""
    return sorted(((int(candidate), int(count)) for candidate, count in votes.items()),
                  key=lambda item: (-item[1], item[0]))[:2]

def _ratio(value):
    return "" if value in (None, "") else f"{value:.6f}"


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
        if m:
            value = m.group(2).replace(r"\t", "").replace(r"\n", "").strip()
            out[m.group(1)] = value
    return out

v_state_loc, me_state_loc = {}, {}
v_vp_loc, me_vp_loc = {}, {}
v_reg_loc, me_reg_loc = {}, {}
for f in sorted(glob.glob(os.path.join(V,"localisation/english/*.yml"))):
    for k,v in parse_loc(f).items():
        if k.startswith("STATE_") and k not in v_state_loc: v_state_loc[k]=v
        if k.startswith("VICTORY_POINTS_") and k not in v_vp_loc: v_vp_loc[k]=v
        if k.startswith("STRATEGICREGION_") and k not in v_reg_loc: v_reg_loc[k]=v
for f in sorted(glob.glob(os.path.join(MOD,"localisation/english/*.yml"))):
    for k,v in parse_loc(f).items():
        if k.startswith("STATE_") and k not in me_state_loc: me_state_loc[k]=v
        if k.startswith("VICTORY_POINTS_") and k not in me_vp_loc: me_vp_loc[k]=v
        if k.startswith("STRATEGICREGION_") and k not in me_reg_loc: me_reg_loc[k]=v

def parse_states(root, loc):
    out = {}
    for f in sorted(glob.glob(os.path.join(root,"history/states/*.txt"))):
        sid = int(os.path.basename(f).split("-")[0])
        src = open(f, encoding="utf-8-sig", errors="replace").read()
        hist = grab(src, "history")
        vps = []
        for m in re.finditer(r"victory_points\s*=\s*\{([^}]*)\}", hist):
            vals = ints(m.group(1))
            for i in range(0, len(vals) - 1, 2):
                if vals[i] > 0 and vals[i + 1] > 0:
                    vps.append((vals[i], vals[i + 1]))
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
    for f in sorted(glob.glob(os.path.join(root,"map/strategicregions/*.txt"))):
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
    for rid,rd in sorted(regions.items()):
        c = len(pset & set(rd["provs"]))
        if c > best[1]: best = (rid,c)
    return best[0]

# ---------- state-context migration comments ----------
# Accept state-valued keys, numeric state scopes, and members of `states` lists;
# identifiers in strategic-region/province namespaces are not state evidence.
comment_votes = {}          # vanilla_id -> {me_id: votes}
reverse_comment_votes = {}  # me_id -> {vanilla_id: votes}
comment_edge_votes = {}     # (vanilla_id, me_id) -> votes; every ID in the
                            # was-vanilla set, not just the first. Evidence
                            # only — canonical tables keep the first-ID rule.
prov_pairs_all = {}         # state-context pairs rejected by two-end ID filter
# Canonical adjudication sees only the FIRST numeric ID after `was vanilla`
# (_pair_re), exactly as before. `_edge_re` instead captures the whole ID set
# (`42/51/978`, `45,764`, `{291 1011 676}`): the run ends at the first char
# that cannot be part of an ID list, so trailing prose, (names), and later
# province/SR numbering never leak in.
_pair_re = re.compile(r"was vanilla\s+(\d+)", re.IGNORECASE)
_edge_re = re.compile(r"was vanilla\b\s*[{]?\s*(\d[\d\s,/{}+]*)", re.IGNORECASE)
_no_state_re = re.compile(
    r"\boff[\s-]*map\b|\bno\s+(?:direct\s+)?equivalent\b|"
    r"\bnot\s+on\s+(?:the\s+)?(?:mod\s+)?map\b|"
    r"\b(?:strategic[_\s-]*regions?|SR)\b", re.IGNORECASE)
_state_keys = {
    "state", "states", "controls_state", "owns_state", "add_state_claim",
    "remove_state_claim", "add_state_core", "remove_state_core",
    "set_state_owner", "transfer_state", "transfer_state_to",
}
_non_state_blocks = {
    "region", "regions", "strategic_region", "strategic_regions",
    "province", "provinces", "victory_points",
}
_block_re = re.compile(r'"(?:\\.|[^"\\])*"|(\w+)\s*=\s*\{|([{}])')
for pat in ["common/**/*.txt","events/*.txt","history/**/*.txt"]:
    for f in sorted(glob.glob(os.path.join(MOD, pat), recursive=True)):
        blocks = []
        for line in open(f, encoding="utf-8-sig", errors="replace"):
            if line.lstrip().startswith('#'): continue
            code, _, comment = line.partition('#')
            in_states = bool(blocks and blocks[-1] == "states")
            in_non_state = any(key in _non_state_blocks for key in blocks)
            # Track every active line, including lines without migration notes.
            # Quoted strings cannot open/close a list or change its namespace.
            for bm in _block_re.finditer(code):
                if bm.group(1): blocks.append(bm.group(1))
                elif bm.group(2) == "{": blocks.append(None)
                elif bm.group(2) == "}" and blocks: blocks.pop()
            cm = _pair_re.search(comment)
            em = _edge_re.search(comment)
            if in_non_state or _no_state_re.search(comment) or not (cm or em): continue
            new_ids = []
            scalar = re.fullmatch(r"\s*(\w+)\s*=\s*(\d+)\s*", code)
            scope = re.match(r"\s*(\d+)\s*=\s*\{", code)
            state_list = re.fullmatch(r"\s*states\s*=\s*\{([\d\s]*)\}?\s*", code)
            if scalar and scalar.group(1) in _state_keys:
                new_ids = [int(scalar.group(2))]
            elif scope:
                new_ids = [int(scope.group(1))]
            elif state_list:
                # Includes `states = { 119 127 } # was vanilla 195`.
                new_ids = ints(state_list.group(1))
            elif in_states and re.fullmatch(r"\s*\d+(?:\s+\d+)*\s*\}?\s*", code):
                new_ids = ints(code)
            if em:
                # Full-set evidence: every vanilla ID in the tail x every ME
                # target. Pairs whose ends are not both real states fall
                # through to the same province-level reject bucket.
                for eid in ints(em.group(1)):
                    for new_id in new_ids:
                        if eid in v_states and new_id in me_states:
                            key = (eid, new_id)
                            comment_edge_votes[key] = comment_edge_votes.get(key, 0) + 1
                        else:
                            prov_pairs_all.setdefault(eid, set()).add(new_id)
            if not cm: continue
            old_id = int(cm.group(1))
            for new_id in new_ids:
                if new_id in me_states and old_id in v_states:
                    votes = comment_votes.setdefault(old_id, {})
                    votes[new_id] = votes.get(new_id, 0) + 1
                    votes = reverse_comment_votes.setdefault(new_id, {})
                    votes[old_id] = votes.get(old_id, 0) + 1
                else:
                    prov_pairs_all.setdefault(old_id, set()).add(new_id)

# Forward preferences are proposals only; reverse votes adjudicate ancestry.
# All vote ties choose the smaller numeric ID, never filesystem order.
v2me = {vid: max(votes, key=lambda mid: (votes[mid], -mid))
        for vid, votes in sorted(comment_votes.items())}
src = {k:"comment" for k in v2me}
os.makedirs(OUT, exist_ok=True)
json.dump({str(k):sorted(v,key=str) for k,v in sorted(prov_pairs_all.items())}, open(os.path.join(OUT,"province_pairs.json"),"w"), indent=0)
# Complete multi-ID comment edges (LF-only, sorted by vanilla then ME id).
with open(os.path.join(OUT,"mapping_comment_edges.tsv"),"w",newline="",encoding="utf-8") as f:
    w = csv.writer(f, delimiter="\t", lineterminator="\n")
    w.writerow(("vanilla_id","me_id","comment_votes"))
    for (vid, mid), votes in sorted(comment_edge_votes.items()):
        w.writerow((vid, mid, votes))
print("comment pairs: vanilla",len(v2me),"| province-level rejects",sum(len(v) for v in prov_pairs_all.values()))
print("comment edges:",len(comment_edge_votes),"pairs,",sum(comment_edge_votes.values()),"votes")

# source 2: exact names, then a longer ME prefix/suffix extension of a vanilla name.
import unicodedata
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]","",s)
me_by_norm = defaultdict(list)
for ms,d in sorted(me_states.items()): me_by_norm[norm(d["name"])].append(ms)
vanilla_by_norm = defaultdict(list)
for vs, d in sorted(v_states.items()):
    key = norm(d["name"])
    if key:
        vanilla_by_norm[key].append(vs)
def name_cands(vn, vowner):
    # returns me_ids matching name; prefer same owner
    exact = me_by_norm.get(vn, [])
    if exact: return exact, "name"
    cands = []
    # Fuzzy extensions require exactly one ME candidate. Never match a shorter
    # ME name inside a longer vanilla name (Arsk/Krasnoyarsk, Kut/Yakutsk).
    for mn, ids in me_by_norm.items():
        if len(mn) > len(vn) >= 4 and (mn.startswith(vn) or mn.endswith(vn)):
            cands += ids
    if len(cands) != 1: return [], "name~"
    return cands, "name~"
for vs,d in sorted(v_states.items()):
    if vs in v2me: continue
    vn = norm(d["name"])
    cands, basis = name_cands(vn, d["owner"])
    if not cands: continue
    same_owner = [c for c in cands if me_states[c]["owner"] == d["owner"]]
    pool = same_owner or cands
    v2me[vs] = sorted(pool)[0]; src[vs] = basis

# source 3: same-id + same-name (untouched states)
for vs,d in sorted(v_states.items()):
    if vs in v2me: continue
    if vs in me_states and norm(me_states[vs]["name"]) == norm(d["name"]):
        v2me[vs] = vs; src[vs]="same-id"

# Hard geographic overrides: these claims precede all comment/name/geo evidence.
# MANUAL supplies vanilla-side member groups; MANUAL_ME2V resolves individual
# ME claims (and takes precedence if a member appears in both tables).
MANUAL = {
    # vanilla_id: [me_ids] — each member must claim this ancestor with manual basis
    77:  [1579, 818],         # Dobrogea/S.Dobrudja → Durostor, Bazargic (Bulgarian-ceded 1913 shards)
    971: [767, 793],          # Northern Dobruja → Tulcea + Constanța (v971's naval-base shard)
}
# ME-side hard claims, including corrections to existing inferred ancestors.
MANUAL_ME2V = {
    # Audited 2026-10-21. Sources: MV=MappingVerifier, EE=EastEuropeAudit,
    # LC=LowConfidenceAudit, AA=AfricaAsiaAudit. IDs are ME -> vanilla 1.19.
    # Explicit review resolutions are noted where the audits disagreed.
    25: 144,    # Nordland: verifier-confirmed ID repair [MV]
    40: 100,    # Norðurland eystra: Iceland; retained warp-edge correction
    44: 100,    # Vestfirðir: Iceland; retained Westfjords correction
    45: 918,    # Västerbotten: verifier-confirmed ID repair [MV]
    48: 100,    # Austurland: Iceland, not v101 Greenland [MV]
    49: 100,    # Norðurland vestra: Iceland, not a region-key ancestor [MV]
    50: 150,    # Kainuu/Kajaani belongs to Kuopio VP state
    51: 100,    # Vesturland: Iceland; retained off-hull correction
    58: 16,     # Hauts-de-Seine: Ile de France; region-key collision [MV]
    59: 216,    # Suoyarvi: Olonets; region-key collision [MV]
    60: 464,    # Prince Edward Island: retained Nova Scotia island claim
    83: 920,    # Oppland: Opplandene migration split; state audit + natural coverage 70.8%
    90: 337,    # Færø: verifier-confirmed Faroe ID, not short-name containment [MV]
    98: 145,    # Åland: verifier-confirmed ID repair [MV]
    106: 921,   # Buskerud: NOR migration trigger assigns it to Telemark [state audit]
    122: 86,    # Poznań: off-map/comment namespace collision [MV]
    126: 133,   # Glasgow: Lanark; region-key collision [MV]
    128: 142,   # Rogaland: Vestlandet; region-key collision [MV]
    131: 921,   # Vestfold: NOR migration trigger assigns it to Telemark [state audit]
    151: 209,   # Pskov: verifier-confirmed ID repair [MV]
    158: 120,   # Caithness: retained Scottish Highlands claim
    164: 910,   # Bornholm: verifier-confirmed ID repair [MV]
    168: 139,   # Kalmar: Småland; verifier-confirmed ID repair [MV]
    184: 915,   # Halland: verifier-confirmed redraw claim [MV]
    187: 120,   # Orkney: retained Scottish Highlands island claim
    190: 139,   # Kronoberg: Småland; verifier-confirmed ID repair [MV]
    193: 136,   # Angus/Dundee VP belongs to Aberdeenshire [VP]
    198: 253,   # Vladimir: Kovrov/Kineshma cluster, not Voronezh [LC]
    200: 249,   # Kazan: strategic-region-list namespace collision [MV]
    203: 138,   # Blekinge: Skåne; verifier-confirmed ID repair [MV]
    209: 256,   # Cheboksary: Chuvashia; verifier-confirmed ID repair [MV]
    211: 120,   # Kinross: retained Scottish Highlands claim
    212: 138,   # Malmöhus: Skåne; verifier-confirmed ID repair [MV]
    218: 242,   # Vyazma: Smolensk cluster [LC]
    236: 784,   # Święciany: Wilno/1936 Poland; migration evidence [LC]
    240: 911,   # Odense: explicit review resolves stale v37/v315 references [MV]
    247: 911,   # Svendborg: verifier-confirmed Fyn ID [MV]
    253: 119,   # Antrim: Northern Ireland; verifier-confirmed ID repair [MV]
    261: 250,   # Ulyanovsk: Ulyanovsky, not commented-out Ohio [MV]
    274: 11,    # Alytaus: Kaunas/Dzūkija cluster [EE]
    280: 85,    # Danzig: verifier-confirmed ID repair [MV]
    293: 132,   # Lancashire: not the off-map Yemen comment [MV]
    300: 95,    # Grodno: Lida/Wołkowysk cluster [EE]
    307: 95,    # Słonim: Nowogródek VP/history evidence [EE]
    320: 113,   # Baile Átha Cliath: Leinster, not off-map French India [MV]
    325: 129,   # Derbyshire: East Midlands [LC]
    330: 224,   # Lokot: vanilla VP 6386 belongs to Bryansk [LC]
    364: 36,    # Overijssel: Friesland, not off-map South Carolina [MV]
    366: 239,   # Saratov: not off-map Florida [MV]
    371: 135,   # Luimneach: Munster, not off-map Louisiana [MV]
    372: 125,   # Cambridgeshire: East Anglia, not off-map Arkansas [MV]
    384: 194,   # Zhitkovichi: Mozyr; user-confirmed in 2026-10-22 visual re-check
    393: 60,    # Hildesheim: explicit verifier repair, not stale v59/v393 [MV]
    394: 135,   # Corcaigh: Munster, not off-map Wisconsin [MV]
    395: 135,   # Port Láirge: Munster, not off-map Illinois [MV]
    419: 122,   # Monmouthshire: Wales; user-confirmed in 2026-10-22 visual re-check
    430: 407,   # Uralsk: explicit 1.19 ID repair, not stale v408/v430 [MV]
    435: 126,   # Brent: Greater London; v44 is Central Albania [MV]
    446: 127,  # Kent: Sussex, not Kentung/Tashkent name containment [MV]
    500: 980,  # Luxembourg Belge: BEL/WLN state, VP Marche-en-Famenne; pixel/natural geography place it in vanilla 980 Ardennes, not 8 Luxembourg City [pixel 980/1.000, 39 natural pairs]
    512: 8,    # Lëtzebuerg: vanilla 8 Luxembourg City (VP Stad Lëtzebuerg); ME500's `was vanilla 8` notes are a stale old-remapper artifact, not this state [pixel 8/1.000]
    538: 28,   # Verdun: Alsace-Lorraine, not off-map Congo [MV]
    547: 28,    # Moselle: verifier-confirmed Alsace-Lorraine claim [MV]
    574: 71,    # Hron: Eastern Slovakia; user-confirmed in 2026-10-22 visual re-check
    592: 78,    # Hotin: Bessarabian county, not Bucovina's two provinces [LC]
    608: 154,   # Mátészalka: Transtisza, not off-map Beiping [MV]
    614: 197,   # Nikolayev=Mykolaiv, not Nikolayevsk [EE/MV]
    621: 975,   # Eisenstadt VP belongs to Burgenland [VP]
    629: 80,    # Câmpulung Moldovenesc: core BUC resolves verifier uncertainty [EE]
    632: 236,   # Astrakhan: verifier-confirmed ID repair [MV]
    694: 151,   # Bernese Oberland: verifier-confirmed Swiss ID repair [MV]
    721: 109,   # Osijek: Croatia, not off-map East Timor [MV]
    725: 23,    # Charente-Inférieure: Poitou, not off-map Nauru [MV]
    768: 46,    # Dâmbovița: core ROM/WLC/MUN [EE]
    769: 137,   # Kerch: Crimea, not off-map Burundi [MV]
    788: 46,    # Olt: core WLC/MUN, not Upper Volta [EE]
    796: 108,   # Niš: Morava, not Solomon Islands name containment [MV]
    800: 81,    # Romanați: core WLC/OLT [EE]
    804: 46,    # Teleorman: core WLC/MUN, not Egyptian Alexandria VP [EE]
    829: 851,   # Var: not Bolivar name containment [MV]
    839: 157,   # Perugia: Abruzzo; user-confirmed in 2026-10-22 visual re-check
    857: 174,   # León: not Sierra Leone name containment [AA/MV]
    883: 176,   # Logroño: Rioja/Upper Ebro, Burgos cluster [AA]
    888: 802,   # Kaçanik: Kosovo cluster, not off-map Lusambo [EE]
    889: 229,   # Zaqatala: Azerbaijan/Transcaucasia cluster [EE]
    901: 170,   # Soria: retain existing edge claim; replacement not proven [AA leave]
    940: 793,   # Guadalajara: exact state/VP evidence, not North Darfur [AA]
    973: 354,   # Erzurum: vanilla VP 11853 belongs to Trabzon [AA]
    987: 170,   # Cáceres: Extremadura/Badajoz cluster [AA]
    992: 93,    # Kowel: Wołyn, not Province of Aden [EE]
    1012: 353,  # Tunceli: vanilla Tunceli/Xarpêt VP evidence [AA]
    1017: 343,  # Kütahya: vanilla VP 3856 belongs to Afyon [AA]
    1030: 93,   # Łuck: Wołyn/Kowel cluster [EE]
    1039: 800,  # Van: vanilla VP 6935, not Vancouver Island [AA]
    1047: 353,  # Elâzığ: Tunceli; user/visual/geographic review confirmed 2026-10-22
    1091: 350,  # Mardin: Diyarbakır region, not Syrian Deir-az-Zur [AA]
    1108: 458,  # Jendouba: northwest Tunisia [AA]
    1112: 460,  # Bône=Annaba: vanilla VP 7081 belongs to Constantine [LC/AA]
    1116: 458,  # Le Kef: northwest Tunisia [AA]
    1120: 459,  # Alger: Algiers coast, not Algerian Desert [AA]
    1126: 460,  # Souk-Ahras: east Algeria/Constantine-Annaba cluster [AA]
    1127: 458,  # Zaghouan: Tunis hinterland [LC/AA]
    1140: 458,  # Siliana: central-west Tunisia [AA]
    1152: 458,  # Kairouan: central Tunisia/Sousse-Sfax cluster [AA]
    1153: 94,   # Brześć: vanilla Brest-Litovsk VP 3392 [LC]
    1165: 458,  # Kasserine: northern side of Gabès/Gafsa boundary [LC/AA]
    1178: 814,  # Seinų: Sūduva; candidate-independent visual/geographic review confirmed 2026-10-22
    1180: 458,  # Sidi Bou Zid: Kairouan/Kasserine/Sfax cluster [AA]
    1189: 290,  # Rif: Spanish Africa/Xauen-Kert evidence resolves short-name error [AA]
    1201: 461,  # Taza: Casablanca/Fès-Oujda cluster, not Pastaza [AA]
    1224: 665,  # Médénine: southern Tunisia/Gabès [LC/AA]
    1226: 291,  # Kut: Baghdad, not Yakutsk name containment [AA]
    1231: 665,  # Tataouine: southern Tunisia, not Dodecanese [LC/AA]
    1236: 448,  # Nuqat al Khams: coastal Tripoli; explicit review beats LC v661 [AA]
    1240: 451,  # Quba: Derna/Green Mountain coast [AA]
    1245: 448,  # Zawiya: Tripoli coast/El Agelat VP [AA]
    1249: 451,  # Marj: Benghazi-Derna coast [AA]
    1255: 661,  # Bani Walid: reviewed inland Tripolitania, not LC v449 [AA]
    1257: 661,  # Ghadames: western Tripolitania, not Cyrenaica/desert heuristic [AA]
    1261: 661,  # Mizda: inland Nafusa/Tripolitania [LC/AA]
    1263: 857,  # Al-Hudūd Aš-Šamāliyya: Arar/Rafha; MV's 1264 was a typo [AA]
    1295: 514,  # Illizi: Sahara impassable/FRA, not Libyan Desert [AA]
    1299: 446,  # 'Ataqah: Suez governorate [AA]
    1308: 273,  # Jufra: Sahara impassable/ITA [AA]
    1318: 663,  # Jaghbub: Cyrenaica/Al Wahat interior [AA]
    1321: 273,  # Ghat: southwest Fezzan/Libyan Desert [AA]
    1327: 679,  # Al-Madīnah: Medina VP, not conflicting Asir-Makkah comment [AA]
    1333: 273,  # Kufra: Sahara impassable/ITA [AA]
    1334: 456,  # Aswan: vanilla VP 11999, not stale Eastern Desert comment [AA]
    1345: 190,  # Talsi: core CUR/Kurzeme, adjacent to Aizpute [EE]
    1362: 159,  # Como: Lombardy, not Comoro Islands name containment [MV]
    1459: 3,    # Uri: Swiss Plateau, not short-name cross-continent claims [MV]
    1463: 109,  # Jastrebarsko: verifier-confirmed Croatia claim [MV]
    1470: 45,   # Šid: Vojvodina/Srem, not Sidamo [EE]
    1512: 764,  # Južni Banat: verifier-confirmed West Banat claim [MV]
    1513: 45,   # Sjeverni Banat: verifier-confirmed Vojvodina claim [MV]
    1514: 45,   # Novi Sad: verifier-confirmed Vojvodina claim [MV]
    1589: 46,   # Vlașca: Muntenia/Bucharest, not Izmir VP collision [LC]
    1596: 79,   # Dorohoi: core MOL without BSR [EE]
    1597: 80,   # Suceava: core BUC, not Punta Porá [EE]
    1602: 78,   # Cahul: core MOL/BSR and Bessarabia cluster [EE]
    1611: 5,    # Allenstein: East Prussia/core PRE/WRM [EE]
    1625: 63,   # Köslin: verifier-confirmed Hinterpommern claim [MV]
    1675: 65,   # Plauen: Sachsen; user-confirmed in 2026-10-22 visual re-check
    1824: 178,  # Santa Cruz de Tenerife: Canarias, not Argentine Santa Cruz [MV]
    1867: 460,  # Guelma: Constantine/Annaba cluster [AA]
    1869: 458,  # Béja: Tunisia, not Portugal's same-named Beja [AA]
    1870: 458,  # Manubah: Tunis suburb [AA]
    1874: 661,  # Nalut: Nafusa/Tripolitania interior [LC/AA]
    1875: 661,  # Yafran: Nafusa interior [AA]
    1876: 661,  # Gharyan: Nafusa interior/Garian VP [AA]
    1877: 448,  # Sabratha wa Sorman: Tripoli coast [AA]
    1878: 448,  # Murqub: Khoms coast; VP "Homs" is not Syrian Homs [AA]
    1879: 449,  # Misrata: Gulf of Sirte coast [AA]
    1881: 273,  # Quatrun: Sahara impassable/ITA [AA]
    1882: 663,  # Al Wahat: Cyrenaica interior [LC/AA]
    1889: 907,  # Al Qahirah/Al-Qahirah: Cairo capital VP 15800 + Helwan VP 21158; vanilla 907 Cairo (VP 7011 Cairo), not 446 Suez (Port Said VP 12049); pixel 907/1.000 [2026-11-04 root-cause fix]
    1894: 552,  # Bahariya: reviewed impassable desert; overrides LC Cairo leave [AA]
    1937: 339,  # Soma: Manisa/Izmir region, not Somaliland [AA]
    1956: 344,  # Gaziantep: vanilla VP 9893 belongs to Malatya [AA]
    1998: 103,  # Pag: explicit Dalmatian-island resolution, not MV v163 [EE]
    2039: 198,  # Bar: Vinnytsia/Zhmerynka cluster [EE]
    2065: 203,  # Uman': Cherkasy/Zvenigorodka cluster [EE]
    2082: 218,  # Taganrog: vanilla VP 9430 belongs to Rostov [LC]
    2083: 218,  # Shakhty: Rostov/lower Don [LC]
    2164: 207,  # Dubrovno: Vitebsk/2168 migration evidence [LC]
    2191: 243,  # Desnogorsk: Roslavl/2190 migration evidence [LC]
    2255: 354,  # Kelkit: Gümüşhane VP belongs to Trabzon [AA]
    2272: 231,  # Svaneti: Georgia/Racha-Lechkhumi cluster [LC]
    2292: 229,  # Baku: Azerbaijan, not Tamaulipas comment [EE]
    2299: 658,  # Dubai: verifier-confirmed Abu Dhabi/UAE claim [MV]
    2310: 227,  # Chestyakovo=Torez: Donetsk, not Mikhaylovka [LC]
    2372: 805,  # Pogradec: Korçë/Ersekë cluster [EE]
    2376: 205,  # Zherelevo: Kaluga/Kuybyshevsky district [LC]
    2379: 397,  # Ukhta: natural/visual border fit follows Syktyvkar, not Pechora [visual review]
    2396: 273,  # Bhralrm Al-Alzym: Sahara impassable/ITA [AA]
    2409: 234,  # Aleksandrovka: local natural/visual overlay places the state inside Krasnodar [visual review]
    2417: 217,  # Kamyshin: natural/visual border fit follows Stalingrad, not Mikhaylovka [visual review]
    2426: 220,  # Sudzha: Kursk/Belgorod migration cluster [LC]
    2428: 220,  # Rylsk: Kursk/Shchigry cluster [EE]
    2439: 205,  # Kirov in Kaluga: same-name collision; natural coverage 90.8% [state audit]
    2460: 262,  # Leshukonskoe: natural/visual topology fits Pechora after Ukhta->Syktyvkar review [visual review]
    2462: 825,  # Novaya Zemlya: retained Nenets/arctic island edge claim
    2501: 234,  # Kanevskaya: Krasnodar; user/visual/geographic review confirmed 2026-10-22
    2502: 234,  # Tikhoretsk: Kuban core/region/VP; natural coverage 92.5% [state audit]
    2503: 232,  # Kochubei: Dagestan/Terekli-Mekteb, not MV v234 [EE]
    2517: 236,  # Kharabali: core AKN/Astrakhan [EE]
    2537: 448,  # Zouara=Zuwara: geographic coast, despite ME owner TUN [AA]
    2552: 332,  # American Border: retained Labrador edge claim
    2646: 249,  # Arsk: Kazan, not Krasnoyarsk [EE/MV]
    2657: 399,  # Igra: Udmurtia/Glazov cluster, not Tigray [EE]
    2658: 399,  # Uva: Udmurtia, not Tannu Tuva [EE]
    2680: 76,   # Vișeu: North Transylvania; user/visual/geographic review confirmed 2026-10-22
    2711: 666,  # Gällivare: vanilla 666 owns the Gällivare VP; natural coverage 91.3% [state audit]
    2723: 218,  # Bataysk: core DON/Rostov urban area [EE]
    2733: 218,  # Novocherkassk: core DON/Rostov urban area [EE]
    2734: 220,  # Kurchatov: Lgovskiy/Kursk VP, not Kazakh Kurchatov [EE]
    2741: 245,  # Kamensk-Shakhtinsky: core DON/Millerovo cluster [EE]
    2759: 584,  # Kizyl-Arvat=Serdar: Ashkhabad cluster [LC]
    2776: 452,  # Qattara: explicit Matrouh geographic resolution, not AA v552 [LC]
    2777: 273,  # Gialo: Sahara impassable/ITA [AA]
    2781: 120,  # Outer Hebrides: retained Scottish Highlands island claim
    2821: 104,  # Orašje: core BOS/Bosnian Posavina [EE]
    2827: 163,  # Nin: Zadar/Zara-Preko-Biograd cluster [EE]
    2857: 973,  # Baja: Bács-Kiskun, not Baja California [MV]
    2863: 234,  # Krasnoye: core KUB/Kuban, not Balakovo [EE]
    2870: 237,  # Artezian: core KAL/Kalmykia [EE]
    2871: 273,  # Tajarhi: southern Libya/Libyan Desert [AA]
    2881: 109,  # Vinkovci: core CRO/Slavonia, not Vojvodina [EE]
    2885: 915,  # Bohuslän: verifier-confirmed ID repair [MV]
    2889: 253,  # Zavolzhsk: Ivanovo/Kineshma cluster; explicit review decision [EE]
    2895: 103,  # Rab: explicit Dalmatian-island resolution, not MV v163 [EE/AA]
    2916: 103,  # Vis: Hvar/Korčula/Dalmatian islands [EE]
    2942: 218,  # Bagaevskaya: core DON/lower Rostov cluster [EE]
    # Second-pass residuals: exact-name, country/region, and stale-list collisions.
    54: 917,    # Jämtland, not Norrbotten
    73: 149,    # Etelä-Pohjanmaa/Seinäjoki belongs to Vaasa [VP]
    110: 919,   # Värmland, not Gävleborg
    150: 913,   # Östergötland, not Gävleborg
    194: 522,   # Perth VP belongs to Western Australia; retain geographic VP evidence
    214: 121,   # Clackmannan/Fife-Central Scotland cluster
    246: 912,   # Tønder/Southern Jutland, not Rzhev's Kalinin
    272: 119,   # Fermanagh/Northern Ireland
    412: 260,   # Voronezh, not Poltava
    530: 978,   # Buchen/Baden, not North Sudetenland
    724: 847,   # Valais/Western Swiss Alps, not Equatorial Africa
    730: 84,    # Sibiu/Transylvania, not Sibi in British India
    743: 853,   # Ljubljana, not generic North Slovenia
    750: 32,    # Mont-Blanc/French Alpes, not Piedmont
    842: 32,    # Montgenèvre/French Alpes, not Piedmont
    846: 108,   # Pirot/Morava, not Sofia
    867: 90,    # Kielce, not Kraków
    1020: 229,  # Cəbrayıl/Azerbaijan, not Iranian East Azerbaijan
    1110: 93,   # Dubno/Wołyn, not Zaporozhe
    1157: 88,   # Kraków, not Stanisławów
    1282: 662,  # Sirte, not El Agheila
    1335: 699,  # Rio de Oro, not generic Spanish Africa
    1456: 3,    # Thurgau/Swiss Plateau, not Baden
    1457: 3,    # Schaffhausen/Swiss Plateau, not Baden
    1534: 103,  # Lagosta/Lastovo, Dalmatia, not Lagos
    1565: 48,   # Bulgarian Montana, not US Montana
    1672: 65,   # Freiberg/Saxony, not North Sudetenland
    1804: 4,    # Gänserndorf/Lower Austria, not Magnitogorsk
    1811: 4,    # Amstetten/Lower Austria, not Kolyma
    1971: 108,  # Bosilegrad/Morava, not Sofia
    1972: 108,  # Dimitrovgrad/Caribrod/Morava, not Sofia
    2139: 194,  # Mozyr, not Polesie stale member collision
    2159: 206,  # Borisov/Minsk, not Wilejka
    2160: 206,  # Mogilyov/Minsk, not Wilejka
    2163: 207,  # Orsha/Vitebsk, not Wilejka
    2166: 207,  # Polotsk/Vitebsk, not Wilejka
    2516: 402,  # Akhtubinsk, not Astrakhan
    2762: 823,  # Nukus/Karakalpakstan, not Shandong
    1945: 343,   # Bolvadin is in Afyon province, not Ankara [LC geography]
    # Third-pass VP/state-localisation corrections; VP evidence outranks stale comments.
    15: 99,     # Århus/Jutland
    66: 38,     # Västernorrland/Sundsvall, not Norrbotten
    141: 141,   # Stockholm/Södermanland, not Gävleborg
    1429: 74,   # Ostrava/Sudeten Silesia
    1464: 109,  # Prigorje/Zagreb-Croatia
    1631: 63,   # Stettin/Hinterpommern
    1745: 55,   # Wiesbaden/Hessen
    1751: 51,   # Köln/Rhineland
    1771: 50,   # Stuttgart/Württemberg
    1795: 152,  # Salzburg/Upper Austria
    196: 809,   # Jelgava/Zemgale, not Latgale
    223: 99,    # Vejle/Jutland
    2389: 582,  # Beloretsk/Magnitogorsk VP cluster
    2933: 764,  # Pančevo/West Banat
    391: 56,    # Münster/Weser-Ems VP, not Westfalen
    399: 36,    # Utrecht/Friesland VP
    431: 51,    # Düsseldorf/Rhineland
    469: 51,    # Bonn/Rhineland
    487: 34,    # Namur/Wallonia
    521: 55,    # Mainz/Hessen
    556: 978,   # Karlsruhe/Baden
    566: 28,    # Bas-Rhin/Alsace-Lorraine
    584: 18,    # Aube/Troyes/Champagne
    606: 28,    # Haut-Rhin/Alsace-Lorraine
    626: 27,    # Côte-d'Or/Bourgogne
    700: 973,   # Szeged/Bács-Kiskun VP
    747: 20,    # Lyon/Rhône VP
    880: 166,   # Zaragoza/Western Aragón VP
    939: 166,   # Teruel/Western Aragón VP
    1025: 420,  # Ardabil/Gilan VP
    1028: 232,  # Kumykia/Dagestan
    1040: 1000, # Eastern Azerbaijan/Tabriz VP
    1146: 420,  # Alborz/Karaj VP
    1171: 417,  # Markazi/Arak VP
    1283: 411,  # Kohgīlūye/Yasuj VP
    1323: 699,  # Saguia el Hamra/Laayoune VP
    1342: 29,   # Lille/Douai VP belongs to Nord-Pas-de-Calais
    1433: 972,  # Znojmo/South Sudetenland VP
    2355: 419,  # Bukan/Mahabad West Azerbaijan VP
    2209: 291,  # Kerbala/Bahgdad VP
    242: 11,    # Kauno/Kaunas VP belongs to Kaunas
    383: 36,    # Gelderland/Arnhem VP belongs to Friesland
    507: 74,    # Opava/Sudeten Silesia
    536: 75,    # Brno/Moravia
    893: 290,   # Ceuta/Spanish Africa
    1073: 47,   # Kórinthos/Attica VP
    1160: 290,  # Tétouan/Spanish Africa
    1198: 96,   # Mołodeczno/Wilejka
    1749: 51,   # Essen-Duisburg/Rhineland
}
# ---------- ME → vanilla: one adjudicated ancestor per ME state ----------
me2v = {}
me_basis = {}
for vid, mids in sorted(MANUAL.items()):
    if vid not in v_states: continue
    for mid in mids:
        if mid in me_states:
            me2v[mid] = vid; me_basis[mid] = "manual"
for mid, vid in sorted(MANUAL_ME2V.items()):
    if mid in me_states and vid in v_states:
        me2v[mid] = vid; me_basis[mid] = "manual"
for ms, votes in sorted(reverse_comment_votes.items()):
    if ms in me2v: continue
    vs = max(votes, key=lambda vid: (votes[vid], -vid))
    me2v[ms] = vs
    me_basis[ms] = "comment" if v2me.get(vs) == ms else "comment(member)"
# Exact-name/same-ID proposals cannot overwrite manual or comment winners.
for vs, ms in sorted(v2me.items()):
    if src[vs] == "name~" or ms in me2v: continue
    me2v[ms] = vs; me_basis[ms] = src[vs]
# name-singleton ancestry: unclaimed ME state whose norm-name equals a vanilla state
# → subdivision descendant of it. Exact names precede fuzzy name proposals.
for vs,d in sorted(v_states.items()):
    vn = norm(d["name"])
    if not vn: continue
    for mid in me_by_norm.get(vn, []):
        if mid not in me2v:
            me2v[mid] = vs; me_basis[mid] = "name-singleton"
for vs, ms in sorted(v2me.items()):
    if src[vs] == "name~" and ms not in me2v:
        me2v[ms] = vs; me_basis[ms] = "name~"

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
#   5. me2v claims ONLY where no higher basis exists; members derive from me2v.
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

# control points: vanilla VP province -> same-named ME VP province.
# Hardened name matching (cross-state VP pollution guard):
#   * A name is usable only when it is UNAMBIGUOUS on both maps — exactly one
#     named VP on the vanilla side and exactly one on the ME side. A last-wins
#     name->prov dict silently pairs a vanilla VP with an unrelated ME name-twin.
#   * The ME VP's host state must not contradict the preliminary me2v ancestor
#     resolved above (manual/comment/name chain): if the ME state already has an
#     adjudicated ancestor different from the vanilla VP's state, the VP was
#     relocated across state lines (e.g. Perth, Sverdlovsk) and is rejected.
#   * Every emitted (v_prov, me_prov) pair is unique — no last-wins overwrite.
v_vp_named = {}                 # norm-name -> (v_prov, v_state); ambiguous names -> None
for vs, d in v_states.items():
    for pid, pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm:
            key = norm(nm)
            if key: v_vp_named[key] = (pid, vs) if key not in v_vp_named else None
me_vp_named = {}                # norm-name -> (me_prov, me_state); ambiguous names -> None
for sid, d in me_states.items():
    for pid, pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm:
            key = norm(nm)
            if key: me_vp_named[key] = (pid, sid) if key not in me_vp_named else None
_anchors = []
_anchor_seen = set()
for vn, vhit in v_vp_named.items():
    if vhit is None: continue            # duplicate/ambiguous vanilla name
    mhit = me_vp_named.get(vn)
    if mhit is None: continue            # missing or duplicate/ambiguous ME name
    (v_prov, vs), (m_prov, ms) = vhit, mhit
    if ms in me2v and me2v[ms] != vs:
        continue                         # relocated VP: preliminary ancestry disagrees
    if (v_prov, m_prov) in _anchor_seen: continue
    _anchor_seen.add((v_prov, m_prov))
    if v_prov in v_cent and m_prov in m_cent:
        _anchors.append((v_prov, m_prov))
# Keep the exact control set used by the ancestry census reproducible. The
# overlay verifier consumes this manifest instead of reconstructing name
# collisions from the already adjudicated VP table.
with open(os.path.join(OUT, "registration_anchors.tsv"), "w", newline="", encoding="utf-8") as _af:
    _aw = csv.writer(_af, delimiter="\t")
    _aw.writerow(("v_prov", "v_state", "me_prov", "me_state"))
    for _vp, _mp in _anchors:
        _aw.writerow((_vp, v_p2s[_vp], _mp, m_p2s[_mp]))

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
    vid, share = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
    pixel_ancestor[ms] = (vid, share/tot)
print("pixel anchors:", len(_anchors), "| me states attributed:", len(pixel_ancestor))

# vp-geo ancestry: ME mod-new state whose named VPs match vanilla VP names →
# modal vanilla state of those VPs. (Ajaccio→v1 Corsica, Linz→v4 Lower Austria)
v_vp_to_state = {}
for vs,d in sorted(v_states.items()):
    for pid,pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm: v_vp_to_state.setdefault(norm(nm), vs)
# Keep a non-mutating named-VP vote pass for evidence. Unlike the adjudication
# loop below, this intentionally includes ME states already claimed by stronger
# evidence. Ambiguous vanilla VP names are not usable evidence.
_vp_name_states = defaultdict(set)
for vs, d in sorted(v_states.items()):
    for pid, pts in d["vps"]:
        nm = v_vp_loc.get(f"VICTORY_POINTS_{pid}")
        key = norm(nm) if nm else ""
        if key:
            _vp_name_states[key].add(vs)
_vp_unique_to_state = {
    key: next(iter(states)) for key, states in _vp_name_states.items()
    if len(states) == 1
}
vp_geo_votes = {}
for ms, d in sorted(me_states.items()):
    votes = defaultdict(int)
    for pid, pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        key = norm(nm) if nm else ""
        if key in _vp_unique_to_state:
            votes[_vp_unique_to_state[key]] += 1
    if votes:
        vp_geo_votes[ms] = dict(votes)

for ms,d in me_states.items():
    if ms in me2v or not d["vps"]: continue
    votes = defaultdict(int)
    for pid,pts in d["vps"]:
        nm = me_vp_loc.get(f"VICTORY_POINTS_{pid}")
        if nm and norm(nm) in v_vp_to_state:
            votes[v_vp_to_state[norm(nm)]] += 1
    if votes:
        best = max(votes, key=lambda vid: (votes[vid], -vid))
        me2v[ms] = best; me_basis[ms] = "vp-geo"
# pixel-geo claims: last-mile fallback — spatial majority ancestor for any ME
# state still unclaimed after comment/manual/name/vp-geo chains.
for ms,(vid,share) in pixel_ancestor.items():
    if ms in me2v: continue
    me2v[ms] = vid; me_basis[ms] = "pixel-geo" if share >= 0.5 else "pixel-geo-low"
# ---------- status ----------
# Member sets and VP descendant gates use only the adjudicated reverse map.
members_by_v = defaultdict(list)
for mid, vid in sorted(me2v.items()):
    members_by_v[vid].append(mid)
_basis_rank = {
    "manual": 0, "comment": 1, "comment(member)": 1, "name": 2,
    "same-id": 2, "name-singleton": 3, "name~": 4, "vp-geo": 5,
    "pixel-geo": 6, "pixel-geo-low": 7,
}
rows_v2m = []
for vs in sorted(v_states):
    d = v_states[vs]
    members = members_by_v.get(vs, [])
    # Prefer stronger evidence, then comment votes/spatial share, then lowest ID.
    ms = min(members, key=lambda mid: (
        _basis_rank[me_basis[mid]], -comment_votes.get(vs, {}).get(mid, 0),
        -(pixel_ancestor[mid][1] if mid in pixel_ancestor and pixel_ancestor[mid][0] == vs else 0),
        mid)) if members else None
    status = "unmapped"
    if len(members) == 1: status = "1:1"
    elif members: status = "1:many"
    rows_v2m.append(dict(
        vanilla_id=vs, vanilla_name=d["name"], owner=d["owner"],
        v_prov_count=len(d["provinces"]), region=state_region(v_states, v_regions, vs),
        me_primary=ms or "", me_members="|".join(map(str,members)),
        me_names="|".join(me_states[i]["name"] for i in members),
        basis=me_basis[ms] if ms is not None else "unmapped", status=status,
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
        basis=me_basis.get(ms,"unmapped"), status="mapped" if vs else "mod-new",
    ))

os.makedirs(OUT, exist_ok=True)
# Evidence is deliberately assembled after final adjudication. It reads the raw
# vote tables above, but never feeds them back into canonical me2v/v2m decisions.
mapping_evidence_rows = []
for ms in sorted(me_states):
    d = me_states[ms]
    ancestor = me2v.get(ms)
    basis = me_basis.get(ms, "unmapped")

    pixel_ranked = _rank_votes(_px_votes.get(ms, {}))
    pixel_total = sum(_px_votes.get(ms, {}).values())
    if pixel_ranked and pixel_total:
        pixel_best, pixel_best_votes = pixel_ranked[0]
        pixel_share = pixel_best_votes / pixel_total
        if len(pixel_ranked) > 1:
            pixel_runner, pixel_runner_votes = pixel_ranked[1]
            pixel_runner_share = pixel_runner_votes / pixel_total
        else:
            pixel_runner = pixel_runner_votes = pixel_runner_share = ""
        pixel_margin = pixel_share - (pixel_runner_share or 0)
    else:
        pixel_best = pixel_share = pixel_runner = pixel_runner_share = pixel_margin = ""

    comment_ranked = _rank_votes(reverse_comment_votes.get(ms, {}))
    if comment_ranked:
        comment_best, comment_best_votes = comment_ranked[0]
        if len(comment_ranked) > 1:
            comment_runner, comment_runner_votes = comment_ranked[1]
        else:
            comment_runner = comment_runner_votes = ""
        comment_margin = comment_best_votes - (comment_runner_votes or 0)
    else:
        comment_best = comment_best_votes = comment_runner = comment_runner_votes = comment_margin = ""

    name_key = norm(d["name"])
    exact_names = vanilla_by_norm.get(name_key, []) if name_key else []

    vp_ranked = _rank_votes(vp_geo_votes.get(ms, {}))
    if vp_ranked:
        vp_best, vp_best_votes = vp_ranked[0]
        if len(vp_ranked) > 1:
            vp_runner, vp_runner_votes = vp_ranked[1]
        else:
            vp_runner = vp_runner_votes = ""
        vp_margin = vp_best_votes - (vp_runner_votes or 0)
    else:
        vp_best = vp_best_votes = vp_runner = vp_runner_votes = vp_margin = ""

    me_region = state_region(me_states, me_regions, ms)
    ancestor_data = v_states.get(ancestor)
    mapping_evidence_rows.append(dict(
        me_id=ms,
        me_name=d["name"],
        owner=d["owner"] or "",
        me_region=me_region if me_region is not None else "",
        ancestor=ancestor if ancestor is not None else "",
        ancestor_name=ancestor_data["name"] if ancestor_data else "",
        basis=basis,
        status="mapped" if ancestor is not None else "mod-new",
        manual_override=str(basis == "manual").lower(),
        pixel_best=pixel_best,
        pixel_share=_ratio(pixel_share),
        pixel_runner=pixel_runner,
        pixel_runner_share=_ratio(pixel_runner_share if pixel_runner_share != "" else None),
        pixel_margin=_ratio(pixel_margin if pixel_margin != "" else None),
        comment_best=comment_best,
        comment_votes=comment_best_votes,
        comment_runner=comment_runner,
        comment_runner_votes=comment_runner_votes,
        comment_margin=comment_margin,
        name_norm=name_key,
        name_exact_me_ids="|".join(map(str, exact_names)),
        name_collision_count=len(exact_names),
        vp_best=vp_best,
        vp_votes=vp_best_votes,
        vp_runner=vp_runner,
        vp_runner_votes=vp_runner_votes,
        vp_margin=vp_margin,
        me_core_tags="|".join(d["cores"]),
        ancestor_core_tags="|".join(ancestor_data["cores"]) if ancestor_data else "",
        me_vp_count=len(d["vps"]),
        ancestor_vp_count=len(ancestor_data["vps"]) if ancestor_data else "",
    ))
with open(os.path.join(OUT, "mapping_evidence.tsv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=EVIDENCE_COLUMNS, delimiter="\t")
    w.writeheader()
    w.writerows(mapping_evidence_rows)
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
    modal = max(cnt, key=lambda rid: (cnt[rid], -rid)) if cnt else None
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
    # Descendants are exactly the states whose adjudicated ancestor is this row.
    desc = set(members_by_v.get(vs, []))
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
print("written: docs/states/{v2m_states,m2v_states,regions,vps,mapping_evidence,mapping_comment_edges}.tsv + province_pairs.json + registration_anchors.tsv")
