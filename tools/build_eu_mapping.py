#!/usr/bin/env python
"""Build the definitive 4-layer European Vanilla <-> Magna Europa mapping tables,
legacy error health audit, and upgraded offline visual review page.

Outputs written under docs/states/eu_mapping/ (deterministic UTF-8, LF):
  Layer 1 — Province Crosswalk:
    prov_m2v.tsv               ME land province -> primary Vanilla state/prov + weights
    prov_v2m.tsv               Vanilla state -> ordered ME provinces + coverage
  Layer 2 — Weighted Many-to-Many State Overlap Matrix:
    state_overlap.tsv          All meaningful (vanilla_id, me_id) overlap edges
  Layer 3 — Strict Disjoint Partition & Minimal Aligned Blocks:
    state_partition_m2v.tsv    Every ME state -> unique disjoint Vanilla parent (or excluded)
    state_partition_v2m.tsv    Every European Vanilla state -> ordered ME members + primary
    state_aligned_blocks.tsv   Minimal union-to-union aligned bipartite blocks
  Layer 4 — Single-Target City / Capital / Building Identity:
    state_primary_city.tsv     Vanilla state -> single primary ME state for cities/factories
  Audits, Summaries & Review UI:
    legacy_audit.tsv           Full diff vs legacy docs/states/m2v_states.tsv
    review_queue.tsv           Compact human worklist (genuine straddlers / edge cases only)
    registration_report.json   Stage 1/2/3 geometric registration & independent benchmarks
    mapping_summary.json       Complete summary statistics & input hashes
    review/index.html          Self-contained offline visual review page (0.5px aligned!)

Usage:
  python tools/build_eu_mapping.py
  python tools/build_eu_mapping.py --vanilla "C:/path/to/Hearts of Iron IV" --no-review
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import mod_root, repo_root  # noqa: E402
from eu_map_registration import (  # noqa: E402
    DEFAULT_VANILLA,
    ME_OWNER_TO_VANILLA,
    VANILLA_EU_BBOX,
    EuropeanRegistration,
    run_registration,
)


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _fmt_ratio(v) -> str:
    return "" if v is None else f"{float(v):.6f}"


def _write_tsv(path: Path, columns: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _state_adjacency(state_raster: np.ndarray, valid_mask: np.ndarray) -> dict[int, set[int]]:
    """Build 4-neighbor state adjacency graph from a state ID raster."""
    sr = np.where(valid_mask, state_raster, 0)
    adj: dict[int, set[int]] = defaultdict(set)
    for a, b in [(sr[:-1, :], sr[1:, :]), (sr[:, :-1], sr[:, 1:])]:
        diff = (a > 0) & (b > 0) & (a != b)
        if not diff.any():
            continue
        pa = a[diff]
        pb = b[diff]
        lo = np.minimum(pa, pb).astype(np.int64)
        hi = np.maximum(pa, pb).astype(np.int64)
        uniq = np.unique(lo * 100000 + hi)
        for k in uniq:
            u, v = int(k // 100000), int(k % 100000)
            adj[u].add(v)
            adj[v].add(u)
    return adj


def _graph_dist_capped(adj: dict[int, set[int]], start: int, targets: set[int], max_hops: int = 4) -> int:
    if start in targets:
        return 0
    seen = {start}
    q = deque([(start, 0)])
    while q:
        u, d = q.popleft()
        if d >= max_hops:
            continue
        for nb in adj.get(u, ()):
            if nb in targets:
                return d + 1
            if nb not in seen:
                seen.add(nb)
                q.append((nb, d + 1))
    return max_hops + 1


def _build_vanilla_land_trees(van: dict):
    """Build fast boundary KD-trees for Vanilla land (global + per 1936 owner)."""
    sr = van["state_raster"]
    lm = van["land_mask"] & (sr > 0)
    # Boundary pixels of vanilla land (enough for nearest-land lookup for water-landing ME states)
    # Use full Europe box land pixels (every 1px for small islands, dense everywhere)
    vx0, vy0, vx1, vy1 = VANILLA_EU_BBOX
    # Expand box slightly to catch edge states (Madeira/Canaries/Jan Mayen/Oman)
    x0 = max(0, vx0 - 120)
    y0 = max(0, vy0)
    x1 = min(van["width"], vx1 + 120)
    y1 = min(van["height"], vy1 + 120)

    sub_lm = lm[y0:y1, x0:x1]
    sub_sr = sr[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub_lm)
    pts_global = np.column_stack([xs + x0, ys + y0]).astype(np.float64)
    sids_global = sub_sr[ys, xs]
    pr_global = van["prov_raster"][ys + y0, xs + x0]
    tree_global = cKDTree(pts_global)

    # Per-owner trees
    v_sid_own = {sid: st["owner36"] for sid, st in van["states"].items()}
    by_own = defaultdict(list)
    for idx, sid in enumerate(sids_global):
        own = v_sid_own.get(int(sid), "")
        if own:
            by_own[own].append(idx)

    owner_trees = {}
    for own, idxs in by_own.items():
        arr = np.asarray(idxs, dtype=np.int64)
        owner_trees[own] = (cKDTree(pts_global[arr]), sids_global[arr], pr_global[arr])

    # Per-state centroid/boundary distance lookup for legacy audit
    by_state = defaultdict(list)
    for idx, sid in enumerate(sids_global):
        by_state[int(sid)].append(idx)
    state_trees = {}
    for sid, idxs in by_state.items():
        arr = np.asarray(idxs, dtype=np.int64)
        # Subsample huge states slightly for instant distance queries
        step = max(1, len(arr) // 500)
        state_trees[sid] = cKDTree(pts_global[arr[::step]])

    return (tree_global, sids_global, pr_global), owner_trees, state_trees


def _load_legacy_m2v(docs_dir: Path) -> dict[int, dict]:
    p = docs_dir / "m2v_states.tsv"
    if not p.is_file():
        return {}
    out = {}
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            try:
                mid = int(r["me_id"])
            except (KeyError, ValueError):
                continue
            vid = int(r["v_ancestor"]) if r.get("v_ancestor", "").isdigit() else None
            out[mid] = {
                "v_id": vid,
                "v_name": r.get("v_name", ""),
                "basis": r.get("basis", ""),
                "status": r.get("status", ""),
            }
    return out


_STOP_TOKENS = {
    "north", "south", "east", "west", "northern", "southern", "eastern", "western",
    "upper", "lower", "central", "greater", "lesser", "inner", "outer", "middle",
    "new", "old", "desert", "island", "islands", "region", "area", "state", "province",
    "republic", "kingdom", "empire", "realm", "free", "duchy", "grand", "principality",
    "union", "unitary", "peoples", "democratic", "socialist", "federation", "swiss", "alps",
    "french", "german", "british", "italian", "russian", "polish", "spanish", "turkish",
    "algerian", "libyan", "scottish", "english", "welsh", "irish", "austrian", "hungarian",
    "czech", "slovak", "serbian", "croatian", "greek", "bulgarian", "romanian", "finnish",
    "swedish", "norwegian", "danish", "dutch", "belgian", "portuguese", "arabian", "syrian",
    "iraqi", "persian", "egyptian", "moroccan", "tunisian", "caucasus", "plateau", "coast",
    "valley", "basin", "plain", "plains", "hills", "mountains", "highlands", "lowlands",
    "midlands", "border", "marches", "territory", "land", "lands", "city", "district",
    "county", "oblast", "krai", "voivodeship", "canton", "department", "governorate",
}

_EXONYMS = {
    "styria": "steiermark",
    "apulia": "puglia",
    "samogitia": "zemaitija",
    "khorasanejonubi": "southkhorasan",
    "abruzzi": "abruzzo",
    "almadinah": "madinah",
    "almadina": "madinah",
    "velikiynovgorod": "novgorod",
    "kabardinobalkar": "kabardinobalkaria",
    "tobruch": "tobruk",
}

# Human-adjudicated straddler overrides (approved 2026-11-22 via eu_mapping_adjudication.md).
# Guarded at build time to require same normalized 1936 owner so no cross-owner drift can enter.
ADJUDICATED_STRADDLER_OVERRIDES: dict[int, tuple[int, str]] = {
    1632: (62, "Randow west of Oder; Stettin #1631 already in v63 Hinterpommern (1945 Oder-Neisse border)"),
    856: (231, "Samegrelo/Zugdidi is historic Georgia proper (GEO core, no ABK core), not Abkhazia"),
    2853: (32, "Vallee de la Romanche is Dauphine (Isere), not Italian-claimed Savoy"),
    2212: (675, "Al Bussayyah belongs to Samawah/Al-Muthanna desert (v675), not Basrah"),
    1264: (451, "Butnan contains Tobruch(5) & Bardia(1), matching Vanilla v451 Derna VP Tobruk(5)"),
    1289: (449, "Ajdabiya contains El-Agheila(1), giving v449 El Agheila its direct partition member"),
    1669: (66, "Brieg & Ohlau belong to Regierungsbezirk Breslau (Lower Silesia v66)"),
}

# Human-adjudicated straddler / offshore-island confirmations of modal candidate A (52 states).
ADJUDICATED_CONFIRMED_A: set[int] = {
    29, 36, 59, 143, 194, 325, 408, 445, 467, 544, 574, 607, 625, 780, 799,
    949, 1018, 1033, 1080, 1084, 1175, 1211, 1261, 1277, 1281, 1299, 1312,
    1315, 1316, 1435, 1458, 1487, 1534, 1876, 1882, 2158, 2173, 2193, 2255,
    2322, 2323, 2324, 2376, 2417, 2446, 2464, 2511, 2588, 2618, 2626, 2697,
    2909,
}


def _tokens(text: str) -> set[str]:
    """Extract distinctive geographic name tokens (exonym-normalized, stop-word-filtered)."""
    raw = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    out = set()
    for t in re.split(r"[\s\-_/,'’`]+", raw):
        t = re.sub(r"[^a-z0-9]", "", t)
        if len(t) >= 4 and t not in _STOP_TOKENS:
            out.add(_EXONYMS.get(t, t))
    full = _norm(text)
    if full in _EXONYMS:
        out.add(_EXONYMS[full])
    if len(full) >= 4 and full not in _STOP_TOKENS:
        out.add(full)
    return out


def _load_tag_names(mod_dir: str, vanilla_root: str) -> dict[str, str]:
    """Load English names for 3-letter country/regional core tags (ME first, Vanilla fallback)."""
    tag_loc: dict[str, str] = {}
    roots = [
        (Path(mod_dir) / "localisation", True),
        (Path(vanilla_root) / "localisation" / "english", False),
    ]
    pat = re.compile(r'^\s*([A-Z0-9]{3})(?:_neutrality|_democratic|_fascism|_communism)?:\d*\s+"([^"]+)"', re.M)
    for root, is_me in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*l_english.yml")):
            low = p.name.lower()
            if "countries" not in low and "releasable" not in low:
                continue
            txt = p.read_text(encoding="utf-8-sig", errors="replace")
            for m in pat.finditer(txt):
                tag, nm = m.group(1), m.group(2)
                key_raw = m.group(0).split(":")[0].strip()
                if is_me or tag not in tag_loc:
                    if tag not in tag_loc or "_" not in key_raw:
                        tag_loc[tag] = nm
    return tag_loc


_BORDER_SCRIPT_RE = re.compile(
    r"\b(transfer_state|set_state_owner|add_state_core|add_state_claim|remove_state_core|set_demilitarized_zone)\s*=\s*(\d+)\b"
    r"|\b(\d+)\s*=\s*\{[^{}]{0,260}?\b(transfer_state_to|add_core_of|add_claim_by|remove_core_of|set_state_owner|set_demilitarized_zone)\b",
    re.S,
)


def _scan_vanilla_border_scripts(vanilla_root: str) -> dict[int, set[str]]:
    """Scan Vanilla 1.19 focuses/decisions/events for territorial border-change effects per state ID."""
    vr = Path(vanilla_root)
    out: dict[int, set[str]] = defaultdict(set)
    for sub in ("common/national_focus", "common/decisions", "events"):
        d = vr / sub
        if not d.is_dir():
            continue
        short_sub = sub.split("/")[-1]
        for f in sorted(d.rglob("*.txt")):
            if f.stem == "resource_prospecting":
                continue
            txt = re.sub(r"#[^\n]*", "", f.read_text(encoding="utf-8-sig", errors="replace"))
            rel = f"{short_sub}/{f.stem}"
            for m in _BORDER_SCRIPT_RE.finditer(txt):
                sid = int(m.group(2) or m.group(3))
                if 1 <= sid <= 1200:
                    out[sid].add(rel)
    return out


def _build_river_layer(mod_dir: str, eu_land_me: np.ndarray, mx0: int, my0: int, scale: float, rw: int, rh: int) -> Image.Image:
    """Area-max downsample ME rivers.bmp onto display grid so thin 1px rivers stay crisp."""
    rgba = np.zeros((rh, rw, 4), dtype=np.uint8)
    rp = Path(mod_dir) / "map" / "rivers.bmp"
    if not rp.is_file():
        return Image.fromarray(rgba, mode="RGBA")
    rarr = np.asarray(Image.open(rp))
    if rarr.shape != eu_land_me.shape:
        return Image.fromarray(rgba, mode="RGBA")
    rmask = (rarr < 254) & eu_land_me
    ry, rx = np.nonzero(rmask)
    if not len(rx):
        return Image.fromarray(rgba, mode="RGBA")
    vals = rarr[ry, rx]
    dx = np.floor((rx - mx0) * scale).astype(np.int32)
    dy = np.floor((ry - my0) * scale).astype(np.int32)
    valid = (dx >= 0) & (dy >= 0) & (dx < rw) & (dy < rh)
    dx, dy, vals = dx[valid], dy[valid], vals[valid]
    # Tributaries (<6) slightly softer, main rivers (>=6) crisper cyan-blue
    minor = vals < 6
    major = ~minor
    if minor.any():
        rgba[dy[minor], dx[minor]] = (84, 195, 245, 130)
    if major.any():
        rgba[dy[major], dx[major]] = (56, 189, 248, 210)
    return Image.fromarray(rgba, mode="RGBA")


def _vanilla_pt_to_display(
    reg: EuropeanRegistration,
    vx: float,
    vy: float,
    vs: int,
    van_grid: np.ndarray,
    van_geom: dict,
    mx0: int,
    my0: int,
    scale: float,
    rw: int,
    rh: int,
    inv_mats: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> tuple[int, int] | None:
    """Invert registration cleanly to map a Vanilla VP (vx, vy) onto the exact ME display grid."""
    inv_main, inv_uk, inv_ice = inv_mats
    cands = []
    # 1. Mainland inverse (with 4 fixed-point iterations inverting the smooth residual field)
    vx0, vy0 = vx, vy
    if reg.residual_field is not None:
        for _ in range(4):
            dx, dy = reg.residual_field.eval_xy(np.array([vx0]), np.array([vy0]))
            vx0 = vx - float(dx[0])
            vy0 = vy - float(dy[0])
    for invM, vx_in, vy_in in ((inv_main, vx0, vy0), (inv_uk, vx, vy), (inv_ice, vx, vy)):
        mx = invM[0, 0] * vx_in + invM[0, 1] * vy_in + invM[0, 2]
        my = invM[1, 0] * vx_in + invM[1, 1] * vy_in + invM[1, 2]
        gx = int(round((mx - mx0) * scale))
        gy = int(round((my - my0) * scale))
        if 0 <= gx < rw and 0 <= gy < rh:
            hit = (int(van_grid[gy, gx]) == vs)
            g = van_geom.get(vs)
            dist = math.hypot(gx - g[0], gy - g[1]) if g else 9999.0
            cands.append((0 if hit else 1, dist, gx, gy))
    if not cands:
        return None
    cands.sort()
    return cands[0][2], cands[0][3]


# ----------------------------------------------------------- Review Page UI

def _encode_png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _encode_id_png(id_arr: np.ndarray) -> Image.Image:
    """Encode 24-bit integer IDs into an RGB PNG (R=hi, G=mid, B=lo)."""
    arr = np.maximum(id_arr, 0).astype(np.int32)
    rgb = np.zeros((*arr.shape, 3), dtype=np.uint8)
    rgb[..., 0] = (arr >> 16) & 0xFF
    rgb[..., 1] = (arr >> 8) & 0xFF
    rgb[..., 2] = arr & 0xFF
    return Image.fromarray(rgb, mode="RGB")


def _boundary_rgba(id_arr: np.ndarray, color_rgba: tuple[int, int, int, int]) -> Image.Image:
    b = np.zeros(id_arr.shape, dtype=bool)
    b[1:, :] |= (id_arr[1:, :] != id_arr[:-1, :])
    b[:-1, :] |= (id_arr[1:, :] != id_arr[:-1, :])
    b[:, 1:] |= (id_arr[:, 1:] != id_arr[:, :-1])
    b[:, :-1] |= (id_arr[:, 1:] != id_arr[:, :-1])
    b &= (id_arr > 0)
    rgba = np.zeros((*id_arr.shape, 4), dtype=np.uint8)
    rgba[b] = color_rgba
    return Image.fromarray(rgba, mode="RGBA")


def _graph_color_states(adj: dict[int, set[int]], sids: list[int], n_colors: int = 8) -> dict[int, int]:
    """DSATUR greedy graph coloring so adjacent Vanilla states never share a map color."""
    sid_set = set(sids)
    nbrs = {s: (adj.get(s, set()) & sid_set) for s in sids}
    colors: dict[int, int] = {}
    used_by_nbr: dict[int, set[int]] = {s: set() for s in sids}
    usage = [0] * n_colors
    uncolored = set(sids)
    # Deterministic order: highest saturation, then highest degree, then lowest id
    while uncolored:
        u = max(uncolored, key=lambda s: (len(used_by_nbr[s]), len(nbrs[s]), -s))
        uncolored.remove(u)
        forbidden = used_by_nbr[u]
        # Pick least-used valid color across all 8 palette tones for rich, balanced atlas colors
        avail = [c for c in range(n_colors) if c not in forbidden]
        chosen = min(avail, key=lambda c: (usage[c], c)) if avail else 0
        colors[u] = chosen
        usage[chosen] += 1
        for nb in nbrs[u]:
            if nb in uncolored:
                used_by_nbr[nb].add(chosen)
    return colors


def _grid_centroids_and_boxes(grid: np.ndarray) -> dict[int, tuple[int, int, int, int, int, int]]:
    """Fast vectorized (cx, cy, x0, y0, x1, y1) per positive ID in a 2D int32 grid."""
    ys, xs = np.nonzero(grid > 0)
    if not len(xs):
        return {}
    ids = grid[ys, xs]
    mx = int(ids.max())
    cnt = np.bincount(ids, minlength=mx + 1)
    sx = np.bincount(ids, weights=xs, minlength=mx + 1)
    sy = np.bincount(ids, weights=ys, minlength=mx + 1)
    # Bounding boxes via sorted unique slices
    order = np.argsort(ids, kind="mergesort")
    s_ids = ids[order]
    s_xs = xs[order]
    s_ys = ys[order]
    uniq, starts = np.unique(s_ids, return_index=True)
    ends = np.append(starts[1:], len(s_ids))
    out = {}
    for uid, a, b in zip(uniq, starts, ends):
        u = int(uid)
        out[u] = (
            int(round(sx[u] / cnt[u])),
            int(round(sy[u] / cnt[u])),
            int(s_xs[a:b].min()),
            int(s_ys[a:b].min()),
            int(s_xs[a:b].max()),
            int(s_ys[a:b].max()),
        )
    return out


def build_review_page(
    reg: EuropeanRegistration,
    out_dir: Path,
    m2v_rows: list[dict],
    v2m_rows: list[dict],
    overlap_rows: list[dict],
    legacy_rows: list[dict],
    summary: dict,
    max_width: int = 1400,
    max_height: int = 1050,
    mod_dir: str | None = None,
    me_aux: dict | None = None,
    tag_names: dict | None = None,
):
    """Render an intuitive offline cartographic review page with 4 clean visual modes:
    Swipe Atlas, Parent-Color Check, Spotlight A/B/C/D Review (with rivers, VPs, cores,
    and Vanilla territorial script diff), and Synced Side-by-Side."""
    me = reg.me
    van = reg.van
    me_aux = me_aux or {}
    tag_names = tag_names or {}
    mod_dir = mod_dir or mod_root()
    excl_sids = set(me["excluded_states"])
    excl_mask = np.isin(me["state_raster"], list(excl_sids)) if excl_sids else np.zeros_like(me["land_mask"])
    eu_land_me = me["land_mask"] & (me["state_raster"] > 0) & (~excl_mask)

    # Crop tightly around European ME land (plus 2% margin)
    ys_e, xs_e = np.nonzero(eu_land_me)
    mx0 = max(0, int(xs_e.min()) - 24)
    mx1 = min(me["width"], int(xs_e.max()) + 24)
    my0 = max(0, int(ys_e.min()) - 24)
    my1 = min(me["height"], int(ys_e.max()) + 24)
    cw, ch = mx1 - mx0, my1 - my0
    scale = min(max_width / cw, max_height / ch, 1.0)
    rw = max(1, int(round(cw * scale)))
    rh = max(1, int(round(ch * scale)))

    # Sample native ME state raster & warped Vanilla state raster on the exact same display grid!
    gx = mx0 + (np.arange(rw, dtype=np.float64) + 0.5) / scale
    gy = my0 + (np.arange(rh, dtype=np.float64) + 0.5) / scale
    gxx, gyy = np.meshgrid(gx, gy)
    ix_m = np.clip(np.rint(gxx).astype(np.int32), 0, me["width"] - 1)
    iy_m = np.clip(np.rint(gyy).astype(np.int32), 0, me["height"] - 1)

    me_grid = np.where(eu_land_me[iy_m, ix_m], me["state_raster"][iy_m, ix_m], 0).astype(np.int32)

    from scipy.ndimage import binary_dilation
    uk_dil = binary_dilation(reg.uk_mask_me, iterations=28)
    ice_dil = binary_dilation(reg.ice_mask_me, iterations=28)

    flat_x = gxx.ravel()
    flat_y = gyy.ravel()
    fix_m = ix_m.ravel()
    fiy_m = iy_m.ravel()

    vx = reg.A_main[0, 0] * flat_x + reg.A_main[0, 1] * flat_y + reg.A_main[0, 2]
    vy = reg.A_main[1, 0] * flat_x + reg.A_main[1, 1] * flat_y + reg.A_main[1, 2]

    m_uk = uk_dil[fiy_m, fix_m]
    if m_uk.any():
        vx[m_uk] = reg.A_uk[0, 0] * flat_x[m_uk] + reg.A_uk[0, 1] * flat_y[m_uk] + reg.A_uk[0, 2]
        vy[m_uk] = reg.A_uk[1, 0] * flat_x[m_uk] + reg.A_uk[1, 1] * flat_y[m_uk] + reg.A_uk[1, 2]

    m_ice = ice_dil[fiy_m, fix_m] & (~m_uk)
    if m_ice.any():
        vx[m_ice] = reg.A_ice[0, 0] * flat_x[m_ice] + reg.A_ice[0, 1] * flat_y[m_ice] + reg.A_ice[0, 2]
        vy[m_ice] = reg.A_ice[1, 0] * flat_x[m_ice] + reg.A_ice[1, 1] * flat_y[m_ice] + reg.A_ice[1, 2]

    if reg.residual_field is not None:
        m_main = (~m_uk) & (~m_ice)
        dx, dy = reg.residual_field.eval_xy(vx[m_main], vy[m_main])
        vx[m_main] += dx
        vy[m_main] += dy

    ivx = np.rint(vx).astype(np.int32)
    ivy = np.rint(vy).astype(np.int32)
    in_v = (ivx >= 0) & (ivy >= 0) & (ivx < van["width"]) & (ivy < van["height"])
    van_grid_flat = np.zeros(len(flat_x), dtype=np.int32)
    van_grid_flat[in_v] = np.where(
        van["land_mask"][ivy[in_v], ivx[in_v]],
        van["state_raster"][ivy[in_v], ivx[in_v]],
        0,
    )
    van_grid = van_grid_flat.reshape(rh, rw)

    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    me_hit_img = _encode_id_png(me_grid)
    van_hit_img = _encode_id_png(van_grid)
    river_img = _build_river_layer(mod_dir, eu_land_me, mx0, my0, scale, rw, rh)
    me_hit_img.save(review_dir / "me_hit.png")
    van_hit_img.save(review_dir / "van_hit.png")

    # Graph-color the Vanilla states on display grid so adjacent Vanilla states NEVER share a color!
    van_disp_adj = _state_adjacency(van_grid, van_grid > 0)
    v_ids_list = [int(r["vanilla_id"]) for r in v2m_rows]
    v_color = _graph_color_states(van_disp_adj, v_ids_list, n_colors=8)

    # Display-grid centroids & bboxes for O(1) zoom, on-map labels, and legacy-error arrows!
    me_geom = _grid_centroids_and_boxes(me_grid)
    van_geom = _grid_centroids_and_boxes(van_grid)

    # Inverse 3x3 affine matrices for projecting Vanilla VP cities onto the display grid
    def _inv_aff(A23: np.ndarray) -> np.ndarray:
        M = np.vstack([A23, [0.0, 0.0, 1.0]])
        return np.linalg.inv(M)[:2, :]

    inv_mats = (_inv_aff(reg.A_main), _inv_aff(reg.A_uk), _inv_aff(reg.A_ice))

    # Compact JS payload — sort overlaps cleanly by (same_owner desc, share desc, vid asc)
    ov_by_me = defaultdict(list)
    for r in overlap_rows:
        is_same = int(r["norm_owner_match"] or 0)
        same_sh = round(float(r["me_same_owner_share"] or 0.0), 3) if is_same else 0.0
        all_sh = round(float(r["me_share"] or 0.0), 3)
        ov_by_me[int(r["me_id"])].append([
            int(r["vanilla_id"]),
            same_sh if is_same else all_sh,
            all_sh,
            r["edge_role"],
            is_same,
        ])
    for mid in ov_by_me:
        ov_by_me[mid].sort(key=lambda x: (-x[4], -x[1], -x[2], x[0]))

    me_meta = {}
    for r in m2v_rows:
        if r["status"] != "mapped":
            continue
        mid = int(r["me_id"])
        g = me_geom.get(mid)
        aux = me_aux.get(mid, {})
        # Project ME VP city points onto display grid
        disp_vps = []
        for vname, pts, lvs, mx_p, my_p in aux.get("vps", [])[:5]:
            if mx_p or my_p:
                gx_v = int(round((mx_p - mx0) * scale))
                gy_v = int(round((my_p - my0) * scale))
                disp_vps.append([gx_v, gy_v, vname, pts, lvs])
        me_meta[str(mid)] = {
            "id": mid,
            "n": r["me_name"],
            "o": r["me_owner"],
            "no": r["norm_owner"],
            "v": int(r["v_id"]),
            "vn": r["v_name"],
            "sh": round(float(r["same_owner_share"] or 0.0), 3),
            "r2": int(r["runner_v_id"]) if r["runner_v_id"] else 0,
            "r2sh": round(float(r["runner_share"] or 0.0), 3),
            "t": r["confidence_tier"],
            "res": r["resolution"],
            "rev": int(r["needs_review"]),
            "rr": r["review_reason"],
            "blk": int(r["block_id"]) if r["block_id"] else 0,
            "leg": int(r["legacy_v_id"]) if r["legacy_v_id"] else 0,
            "ld": r["legacy_diff"],
            "ov": ov_by_me.get(mid, [])[:5],
            "cx": g[0] if g else 0,
            "cy": g[1] if g else 0,
            "bb": [g[2], g[3], g[4], g[5]] if g else None,
            "vps": disp_vps,
            "cs": aux.get("cores", []),
            "imp": aux.get("impact", "low"),
            "dc": aux.get("diff_cnt", 0),
            "da": aux.get("diff_a", []),
            "db": aux.get("diff_b", []),
            "adv": aux.get("advisory", ""),
        }

    v_meta = {}
    for r in v2m_rows:
        vid = int(r["vanilla_id"])
        g = van_geom.get(vid)
        v_vps = []
        if vid in van["states"]:
            for vp_id, pts in sorted(van["states"][vid]["vps"], key=lambda x: (-x[1], x[0]))[:6]:
                nm = van["vp_loc"].get(f"VICTORY_POINTS_{vp_id}", "")
                if nm and vp_id in van["prov_centroids"]:
                    vx_p, vy_p, _ = van["prov_centroids"][vp_id]
                    pt_d = _vanilla_pt_to_display(reg, vx_p, vy_p, vid, van_grid, van_geom, mx0, my0, scale, rw, rh, inv_mats)
                    if pt_d:
                        v_vps.append([pt_d[0], pt_d[1], nm, int(pts)])
        v_meta[str(vid)] = {
            "id": vid,
            "n": r["vanilla_name"],
            "o": r["vanilla_owner"],
            "p": int(r["primary_me_id"]) if r["primary_me_id"] else 0,
            "pn": r["primary_me_name"],
            "ms": [int(x) for x in r["me_members"].split("|") if x],
            "rc": round(float(r["reverse_coverage"] or 0.0), 3),
            "blk": int(r["block_id"]) if r["block_id"] else 0,
            "ci": v_color.get(vid, vid % 8),
            "cx": g[0] if g else 0,
            "cy": g[1] if g else 0,
            "bb": [g[2], g[3], g[4], g[5]] if g else None,
            "vps": v_vps,
        }

    leg_errs = [
        {
            "me": int(r["me_id"]),
            "mn": r["me_name"],
            "o": r["me_owner"],
            "nv": int(r["new_v_id"]),
            "nvn": r["new_v_name"],
            "nsh": round(float(r["new_share"] or 0.0), 3),
            "lv": int(r["legacy_v_id"]) if r["legacy_v_id"] else 0,
            "lvn": r["legacy_v_name"],
            "lsh": round(float(r["legacy_share"] or 0.0), 3),
            "ldist": round(float(r["dist_to_legacy_px"] or 0.0), 1) if r["dist_to_legacy_px"] else 0,
            "lb": r["legacy_basis"],
            "dc": r["diff_class"],
        }
        for r in legacy_rows
        if r["diff_class"] in ("legacy_distant_error", "legacy_wrong_owner", "city_vs_area_split", "straddler_difference")
    ]

    # Compact country tag -> English name map for owners present on the map
    used_owners = {r["me_owner"] for r in m2v_rows if r["status"] == "mapped"} | {r["norm_owner"] for r in m2v_rows if r["status"] == "mapped"}
    owner_names = {t: tag_names[t] for t in sorted(used_owners) if t in tag_names}

    payload = {
        "w": rw,
        "h": rh,
        "me_hit": _encode_png_b64(me_hit_img),
        "van_hit": _encode_png_b64(van_hit_img),
        "rivers": _encode_png_b64(river_img),
        "me": me_meta,
        "van": v_meta,
        "legacy": leg_errs,
        "owners": owner_names,
        "summary": summary,
    }

    html = _REVIEW_HTML_TEMPLATE.replace("__PAYLOAD_JSON__", json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    (review_dir / "index.html").write_text(html, encoding="utf-8")
    return review_dir / "index.html"


_REVIEW_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Magna Europa ↔ Vanilla 1.19 — 直观地图对比与复核台</title>
<style>
  :root {
    --bg: #0f1620; --panel: #151e2b; --card: #1c2736; --border: #2a394d;
    --text: #e8eef5; --muted: #8fa3bc; --accent: #38bdf8;
    --ok: #22c55e; --warn: #f59e0b; --err: #ef4444;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; user-select: none; }
  header { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 14px; background: var(--panel); border-bottom: 1px solid var(--border); }
  header h1 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: 0.2px; }
  .pills { display: flex; flex-wrap: wrap; gap: 7px; margin-left: auto; }
  .pill { background: var(--card); border: 1px solid var(--border); padding: 2px 9px; border-radius: 99px; font-size: 11.5px; color: var(--muted); }
  .pill b { color: var(--text); }
  .pill.ok b { color: var(--ok); }
  .pill.warn b { color: var(--warn); }
  .pill.err b { color: var(--err); }
  .layout { display: grid; grid-template-columns: 1fr 430px; height: calc(100vh - 44px); }
  .map-col { display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid var(--border); }
  .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; padding: 7px 12px; background: var(--panel); border-bottom: 1px solid var(--border); }
  .seg { display: inline-flex; background: var(--bg); border: 1px solid var(--border); border-radius: 7px; padding: 2px; gap: 2px; }
  .seg button { background: transparent; border: none; color: var(--muted); padding: 4px 10px; border-radius: 5px; cursor: pointer; font-size: 12px; font-weight: 500; transition: all 0.12s; }
  .seg button:hover { color: var(--text); }
  .seg button.active { background: #235383; color: #fff; font-weight: 600; }
  .chk { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); font-size: 12px; cursor: pointer; }
  .chk input:checked + span { color: var(--text); }
  .btn { background: var(--card); color: var(--text); border: 1px solid var(--border); padding: 4px 9px; border-radius: 6px; cursor: pointer; font-size: 12px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn.primary { background: #1d4ed8; border-color: #3b82f6; color: #fff; font-weight: 600; }
  .btn.primary:hover { background: #2563eb; }
  .btn.ok { background: #15803d; border-color: #22c55e; color: #fff; font-weight: 600; }
  .btn.warn { background: #b45309; border-color: #f59e0b; color: #fff; font-weight: 600; }
  .btn.em { background: #166552; border-color: #34d399; color: #fff; font-weight: 600; }
  .btn.vio { background: #5b3f8c; border-color: #a78bfa; color: #fff; font-weight: 600; }
  .btn.xs { padding: 2px 7px; font-size: 11px; border-radius: 4px; }
  .stage { position: relative; flex: 1; overflow: hidden; background: #0c1824; }
  .stage.dual { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; background: var(--border); }
  .pane { position: relative; width: 100%; height: 100%; overflow: hidden; background: #0c1824; cursor: grab; }
  .pane:active { cursor: grabbing; }
  .pane canvas { position: absolute; left: 0; top: 0; }
  .pane-tag { position: absolute; top: 10px; left: 12px; background: rgba(15,22,32,0.86); border: 1px solid var(--border); padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; pointer-events: none; z-index: 5; }
  .pane-tag.right { left: auto; right: 12px; }
  .curtain { position: absolute; top: 0; bottom: 0; width: 3px; background: #fff; box-shadow: 0 0 12px rgba(0,0,0,0.85); cursor: ew-resize; z-index: 10; display: none; }
  .curtain-knob { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 38px; height: 38px; border-radius: 50%; background: #fff; color: #0f1620; font-weight: 800; font-size: 14px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 12px rgba(0,0,0,0.65); border: 2px solid #235383; }
  .hud { position: absolute; left: 12px; bottom: 12px; background: rgba(15,22,32,0.92); border: 1px solid var(--border); border-radius: 8px; padding: 7px 12px; font-size: 12px; pointer-events: none; z-index: 8; max-width: 68%; }
  .spot-bar { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); background: rgba(15,22,32,0.96); border: 1px solid #3b82f6; border-radius: 10px; padding: 7px 12px; display: none; flex-wrap: wrap; justify-content: center; align-items: center; gap: 7px; z-index: 12; box-shadow: 0 8px 28px rgba(0,0,0,0.65); max-width: 96%; }
  .spot-bar.show { display: flex; }
  .legend { position: absolute; right: 12px; top: 10px; background: rgba(15,22,32,0.88); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-size: 11.5px; color: var(--muted); z-index: 8; pointer-events: none; }
  .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
  .side { display: flex; flex-direction: column; overflow: hidden; background: var(--panel); }
  .tabs { display: flex; border-bottom: 1px solid var(--border); }
  .tab { flex: 1; padding: 8px 4px; text-align: center; cursor: pointer; color: var(--muted); border-bottom: 2px solid transparent; font-size: 12px; }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; background: var(--card); }
  .search-bar { display: flex; gap: 6px; padding: 7px 8px; border-bottom: 1px solid var(--border); }
  .search-bar input, .search-bar select { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font-size: 12px; }
  .search-bar input { flex: 1; user-select: text; }
  .filter-bar { display: flex; gap: 6px; padding: 6px 8px; border-bottom: 1px solid var(--border); background: #121a26; align-items: center; flex-wrap: wrap; }
  .chip { background: var(--bg); color: var(--muted); border: 1px solid var(--border); border-radius: 99px; padding: 2px 9px; font-size: 11px; cursor: pointer; }
  .chip.active { background: #235383; border-color: #38bdf8; color: #fff; font-weight: 600; }
  .detail { padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--card); min-height: 170px; max-height: 360px; overflow-y: auto; user-select: text; }
  .detail h3 { margin: 0 0 6px; font-size: 13px; display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
  .adv-box { margin: 5px 0 7px; padding: 6px 9px; border-radius: 6px; font-size: 12px; line-height: 1.4; border-left: 3px solid var(--accent); background: rgba(56,189,248,0.1); color: #e2e8f0; }
  .adv-box.low { border-left-color: var(--ok); background: rgba(34,197,94,0.12); }
  .adv-box.high { border-left-color: var(--warn); background: rgba(245,158,11,0.12); }
  .kv { display: grid; grid-template-columns: 74px 1fr; gap: 4px 8px; font-size: 12px; align-items: baseline; }
  .kv .k { color: var(--muted); }
  .cand-pill { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11.5px; margin: 1px 3px 1px 0; border: 1px solid transparent; }
  .cA { background: rgba(44,115,176,0.35); border-color: #7dd3fc; color: #e0f2fe; }
  .cB { background: rgba(184,114,42,0.35); border-color: #fdba74; color: #ffedd5; }
  .cC { background: rgba(41,122,99,0.35); border-color: #6ee7b7; color: #d1fae5; }
  .cD { background: rgba(110,84,153,0.35); border-color: #c4b5fd; color: #ede9fe; }
  .cO { background: rgba(100,116,139,0.22); border-color: #475569; color: #cbd5e1; }
  .sc-tag { display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 11px; margin: 1px 3px 1px 0; background: rgba(15,23,42,0.75); border: 1px solid #334155; color: #cbd5e1; font-family: ui-monospace, Consolas, monospace; }
  .sc-tag.a { border-color: #38bdf8; color: #bae6fd; }
  .sc-tag.b { border-color: #f59e0b; color: #fde68a; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .b-ok { background: rgba(34,197,94,0.18); color: #4ade80; }
  .b-warn { background: rgba(245,158,11,0.2); color: #fbbf24; }
  .b-err { background: rgba(239,68,68,0.22); color: #f87171; }
  .b-info { background: rgba(56,189,248,0.2); color: #7dd3fc; }
  .list { flex: 1; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { position: sticky; top: 0; background: var(--panel); text-align: left; padding: 6px 7px; color: var(--muted); border-bottom: 1px solid var(--border); font-weight: 600; z-index: 2; }
  td { padding: 5px 7px; border-bottom: 1px solid #1f2b3a; cursor: pointer; }
  tr:hover td { background: #1e2d40; }
  tr.sel td { background: #1e3a5f; }
  .adj-bar { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; align-items: center; }
  .adj-bar input { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 3px 6px; font-size: 12px; width: 78px; user-select: text; }
  .adj-bar input.note { flex: 1; width: auto; min-width: 90px; }
</style>
</head>
<body>
<header>
  <h1>Magna Europa ↔ Vanilla 1.19 直观地图对比台</h1>
  <div class="pills" id="pills"></div>
</header>
<div class="layout">
  <div class="map-col">
    <div class="toolbar">
      <div class="seg" id="modeSeg">
        <button data-mode="swipe" class="active" title="左右拖动卷帘：左边原版大州，右边同色 ME 细分拼图">卷帘拼图</button>
        <button data-mode="atlas" title="全图 ME 按归属原版父州着色，错标州会瞬间变色跳出来">同色体检</button>
        <button data-mode="spotlight" title="压暗周边地图，点亮全部候选 A(蓝)/B(橙)/C(绿)/D(紫)，叠加河流与城市VP">聚光灯 A/B/C/D</button>
        <button data-mode="dual" title="左屏原版、右屏 ME 像素同步缩放与十字准星联动">同步双屏</button>
      </div>
      <label class="chk" title="在 ME 拼图上叠加淡淡的原版大州白线"><input type="checkbox" id="chkVanBorder" checked><span>原版州界</span></label>
      <label class="chk" title="叠加 ME 真实河网 (rivers.bmp)，一眼识别以河流为界的州"><input type="checkbox" id="chkRivers" checked><span>河流</span></label>
      <label class="chk" title="在地图上标出目标州内 ME 城市(●黄点)与原版周边 VP 城市(◆菱形)"><input type="checkbox" id="chkCities" checked><span>城市/VP</span></label>
      <label class="chk" title="对比旧表映射到地图上有多乱"><input type="checkbox" id="chkLegacyColor"><span>旧表着色</span></label>
      <label class="chk" title="显示州名标签"><input type="checkbox" id="chkLabels" checked><span>州名</span></label>
      <button class="btn primary" id="btnStartReview">▶ 逐条复核</button>
      <button class="btn" id="btnReset">全景</button>
      <button class="btn" id="btnExport">导出裁决</button>
    </div>
    <div class="stage" id="stage">
      <div class="pane" id="pane1">
        <canvas id="cv1"></canvas>
        <div class="pane-tag" id="tag1">◀ 原版 1.19 大州</div>
        <div class="pane-tag right" id="tag1r">ME 同色细分州 ▶</div>
        <div class="curtain" id="curtain"><div class="curtain-knob">⇄</div></div>
      </div>
      <div class="pane" id="pane2" style="display:none">
        <canvas id="cv2"></canvas>
        <div class="pane-tag">Magna Europa 细分州（按原版父州同色）</div>
      </div>
      <div class="hud" id="hud">滚轮缩放 · 拖拽平移 · 拖动中间白色把手左右对比 · 按住 <b>Space</b> 瞬切原版/ME</div>
      <div class="legend" id="legend"></div>
      <div class="spot-bar" id="spotBar"></div>
    </div>
  </div>
  <div class="side">
    <div class="tabs">
      <div class="tab active" data-tab="review">待复核 (<span id="cntRev">0</span>)</div>
      <div class="tab" data-tab="legacy">旧表错标 (<span id="cntLeg">0</span>)</div>
      <div class="tab" data-tab="van">原版州 (464)</div>
      <div class="tab" data-tab="all">全部 ME</div>
    </div>
    <div class="search-bar">
      <input id="q" placeholder="搜索 ID / 地名 / 国家 / Core (如 GER, MLP)...">
      <select id="ownerSel"><option value="">全部国家</option></select>
    </div>
    <div class="filter-bar" id="filterBar">
      <button class="chip active" data-f="all">全部</button>
      <button class="chip" data-f="high">🔴 仅高国策分歧 (<span id="cntHigh">0</span>)</button>
      <button class="chip" data-f="low">🟢 无分歧 (<span id="cntLow">0</span>)</button>
      <button class="btn xs ok" id="btnBulkLow" style="margin-left:auto" title="原版领土/核心/宣称国策从未拆开 A 与 B，可一键批量确认按面积选 A">✓ 批量确认🟢无分歧</button>
    </div>
    <div class="detail" id="detail">
      <b>辅助判断 5 大工具：</b><br>
      1. <b>A/B/C/D 全着色</b>：聚光灯把 3-4 个重叠候选全部染色，告别黑洞。<br>
      2. <b>河流 + 城市 VP</b>：地图直接标出河网、ME 主城 <b>●</b> 与原版大城 <b>◆</b>。<br>
      3. <b>🔴/🟢 国策分歧</b>：标出哪些原版领土/核心国策会拆开 A 与 B；<b>🟢无分歧</b>可直接批量确认！<br>
      4. <b>ME 区域 Core + 维基一键查</b>：直接翻译区域核心名，一键搜真实地理。
    </div>
    <div class="list">
      <table>
        <thead id="thead"></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
const DATA = __PAYLOAD_JSON__;
const W = DATA.w, H = DATA.h;
const STORE_KEY = "me_eu_mapping_v2_decisions";
let decisions = {};
try { decisions = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch {}

// 8 harmonious low-saturation cartographic atlas pastel colors (zero eye fatigue!)
const PALETTE = [
  [124, 169, 130], // sage green
  [196, 164, 118], // warm sand
  [129, 161, 193], // slate blue
  [186, 134, 146], // dusty rose
  [163, 142, 184], // muted lavender
  [136, 178, 175], // seafoam teal
  [191, 145, 115], // soft terracotta
  [168, 176, 122], // olive parchment
];
const SEA_RGB = [17, 30, 44];
// 4 distinct Spotlight candidate colors (A=Sky Blue, B=Warm Amber, C=Soft Emerald, D=Muted Violet)
const SPOT_SLOTS = [
  { key: "A", fill: [44, 115, 176], border: [125, 211, 252], bg: "rgba(14,74,122,0.92)", bd: "#7dd3fc", btn: "ok", pill: "cA" },
  { key: "B", fill: [184, 114, 42], border: [253, 186, 116], bg: "rgba(146,72,14,0.92)", bd: "#fdba74", btn: "warn", pill: "cB" },
  { key: "C", fill: [41, 122, 99],  border: [110, 231, 183], bg: "rgba(20,94,74,0.92)",  bd: "#6ee7b7", btn: "em", pill: "cC" },
  { key: "D", fill: [110, 84, 153], border: [196, 181, 253], bg: "rgba(76,53,115,0.92)", bd: "#c4b5fd", btn: "vio", pill: "cD" },
];

let meIds = null, vanIds = null;
const offVan = document.createElement("canvas"); offVan.width = W; offVan.height = H;
const offMeNew = document.createElement("canvas"); offMeNew.width = W; offMeNew.height = H;
const offMeLeg = document.createElement("canvas"); offMeLeg.width = W; offMeLeg.height = H;
const offDim = document.createElement("canvas"); offDim.width = W; offDim.height = H;
const offVanBorder = document.createElement("canvas"); offVanBorder.width = W; offVanBorder.height = H;
const offSpot = document.createElement("canvas"); offSpot.width = W; offSpot.height = H;
const offRivers = document.createElement("canvas"); offRivers.width = W; offRivers.height = H;

let reviewList = [];
let state = {
  mode: "swipe",        // swipe | atlas | spotlight | dual
  tab: "review",
  revFilter: "all",     // all | high | low
  showVanBorder: true,
  showRivers: true,
  showCities: true,
  useLegacyColor: false,
  showLabels: true,
  spaceBlink: false,    // hold Space to flash pure Vanilla
  splitRatio: 0.5,      // swipe curtain x fraction [0..1]
  selMe: 0,
  selVan: 0,
  hovMe: 0,
  hovVan: 0,
  hovX: -1,
  hovY: -1,
  reviewIdx: -1,
  zoom: 1,
  tx: 0,
  ty: 0,
  dragging: false,
  dragCurtain: false,
  moved: false,
  dx0: 0,
  dy0: 0,
};

const stage = document.getElementById("stage");
const pane1 = document.getElementById("pane1");
const pane2 = document.getElementById("pane2");
const cv1 = document.getElementById("cv1"), ctx1 = cv1.getContext("2d");
const cv2 = document.getElementById("cv2"), ctx2 = cv2.getContext("2d");
const curtain = document.getElementById("curtain");

function loadImg(src) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; });
}
function decodeIds(im) {
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  const g = c.getContext("2d"); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, W, H).data;
  const out = new Int32Array(W * H);
  for (let i = 0, j = 0; i < out.length; i++, j += 4) out[i] = (d[j] << 16) | (d[j+1] << 8) | d[j+2];
  return out;
}

function getEffectiveParent(mid) {
  const dec = decisions[mid];
  if (dec && dec.status === "override" && dec.override) return dec.override;
  return DATA.me[mid]?.v || 0;
}

function colorForVan(vid) {
  if (!vid) return [80, 88, 98];
  const ci = DATA.van[vid] ? DATA.van[vid].ci : (vid % PALETTE.length);
  return PALETTE[ci % PALETTE.length];
}

function buildAtlasLayers() {
  // Build owner lookup arrays for crisp 1936 country borders
  const maxV = Math.max(2000, ...Object.keys(DATA.van).map(Number)) + 2;
  const maxM = Math.max(3200, ...Object.keys(DATA.me).map(Number)) + 2;
  const vOwn = new Int16Array(maxV), mOwn = new Int16Array(maxM);
  const ownMap = {};
  let ownNext = 1;
  const oid = s => { if (!s) return 0; if (!ownMap[s]) ownMap[s] = ownNext++; return ownMap[s]; };
  for (const k in DATA.van) vOwn[+k] = oid(DATA.van[k].o);
  for (const k in DATA.me) mOwn[+k] = oid(DATA.me[k].no);

  const imgVan = new ImageData(W, H), dv = imgVan.data;
  const imgMeN = new ImageData(W, H), dn = imgMeN.data;
  const imgMeL = new ImageData(W, H), dl = imgMeL.data;
  const imgDim = new ImageData(W, H), dd = imgDim.data;
  const imgVB = new ImageData(W, H), dvb = imgVB.data;

  // Lookup ME -> color index for fast pixel loop
  const mColNew = new Int8Array(maxM).fill(-1);
  const mColLeg = new Int8Array(maxM).fill(-1);
  const vColIdx = new Int8Array(maxV).fill(-1);
  for (const k in DATA.van) vColIdx[+k] = DATA.van[k].ci % PALETTE.length;
  for (const k in DATA.me) {
    const mid = +k;
    const pv = getEffectiveParent(mid);
    mColNew[mid] = pv && vColIdx[pv] >= 0 ? vColIdx[pv] : (pv ? pv % PALETTE.length : -1);
    const lv = DATA.me[k].leg || 0;
    mColLeg[mid] = lv && vColIdx[lv] >= 0 ? vColIdx[lv] : (lv ? lv % PALETTE.length : -1);
  }

  for (let y = 0; y < H; y++) {
    const row = y * W;
    for (let x = 0; x < W; x++) {
      const i = row + x, p = i * 4;
      const m = meIds[i], v = vanIds[i];

      // Vanilla neighbors
      const vR = x + 1 < W ? vanIds[i + 1] : 0;
      const vD = y + 1 < H ? vanIds[i + W] : 0;
      const vL = x > 0 ? vanIds[i - 1] : 0;
      const vU = y > 0 ? vanIds[i - W] : 0;
      const vStateB = v > 0 && (v !== vR || v !== vD);
      const vCountryB = v > 0 && ((vR > 0 && vOwn[v] !== vOwn[vR]) || (vD > 0 && vOwn[v] !== vOwn[vD]));
      const vCoastB = v > 0 && (vR === 0 || vD === 0 || vL === 0 || vU === 0);

      // ME neighbors
      const mR = x + 1 < W ? meIds[i + 1] : 0;
      const mD = y + 1 < H ? meIds[i + W] : 0;
      const mL = x > 0 ? meIds[i - 1] : 0;
      const mU = y > 0 ? meIds[i - W] : 0;
      const mStateB = m > 0 && (m !== mR || m !== mD);
      const mCountryB = m > 0 && ((mR > 0 && mOwn[m] !== mOwn[mR]) || (mD > 0 && mOwn[m] !== mOwn[mD]));
      const mCoastB = m > 0 && (mR === 0 || mD === 0 || mL === 0 || mU === 0);

      // 1. Vanilla Atlas layer (clean pastel fill, dark country borders, subtle state borders)
      if (v > 0) {
        const c = PALETTE[vColIdx[v] >= 0 ? vColIdx[v] : 0];
        let k = 1.0;
        if (vCountryB || vCoastB) k = 0.25;
        else if (vStateB) k = 0.62;
        dv[p] = c[0] * k; dv[p+1] = c[1] * k; dv[p+2] = c[2] * k; dv[p+3] = 255;
      } else {
        dv[p] = SEA_RGB[0]; dv[p+1] = SEA_RGB[1]; dv[p+2] = SEA_RGB[2]; dv[p+3] = 255;
      }

      // 2. ME Atlas (new parent color) & Legacy Atlas (old parent color)
      if (m > 0) {
        const cn = mColNew[m] >= 0 ? PALETTE[mColNew[m]] : [110, 118, 128];
        const cl = mColLeg[m] >= 0 ? PALETTE[mColLeg[m]] : [110, 118, 128];
        // Subtle hairline for ME internal borders, crisp dark for country/coast!
        let k = 1.0;
        if (mCountryB || mCoastB) k = 0.26;
        else if (mStateB) k = 0.84;
        dn[p] = cn[0] * k; dn[p+1] = cn[1] * k; dn[p+2] = cn[2] * k; dn[p+3] = 255;
        dl[p] = cl[0] * k; dl[p+1] = cl[1] * k; dl[p+2] = cl[2] * k; dl[p+3] = 255;
      } else {
        dn[p] = SEA_RGB[0]; dn[p+1] = SEA_RGB[1]; dn[p+2] = SEA_RGB[2]; dn[p+3] = 255;
        dl[p] = SEA_RGB[0]; dl[p+1] = SEA_RGB[1]; dl[p+2] = SEA_RGB[2]; dl[p+3] = 255;
      }

      // 3. Quiet dimmed base for Spotlight mode (calm paper-dark slate + clear country borders)
      if (m > 0 || v > 0) {
        if (mCountryB || vCountryB) { dd[p]=95; dd[p+1]=115; dd[p+2]=138; dd[p+3]=255; }
        else if (mCoastB || vCoastB) { dd[p]=65; dd[p+1]=82; dd[p+2]=102; dd[p+3]=255; }
        else if (mStateB) { dd[p]=40; dd[p+1]=50; dd[p+2]=63; dd[p+3]=255; }
        else { dd[p]=31; dd[p+1]=40; dd[p+2]=52; dd[p+3]=255; }
      } else {
        dd[p]=13; dd[p+1]=22; dd[p+2]=33; dd[p+3]=255;
      }

      // 4. Subtle clean white/charcoal Vanilla border overlay (only Vanilla state borders, NOT dashed clutter!)
      if (vCountryB) { dvb[p]=15; dvb[p+1]=20; dvb[p+2]=28; dvb[p+3]=220; }
      else if (vStateB) { dvb[p]=255; dvb[p+1]=255; dvb[p+2]=255; dvb[p+3]=145; }
    }
  }
  offVan.getContext("2d").putImageData(imgVan, 0, 0);
  offMeNew.getContext("2d").putImageData(imgMeN, 0, 0);
  offMeLeg.getContext("2d").putImageData(imgMeL, 0, 0);
  offDim.getContext("2d").putImageData(imgDim, 0, 0);
  offVanBorder.getContext("2d").putImageData(imgVB, 0, 0);
}

function getSpotlightCandidates(m) {
  if (!m) return [];
  const vA = getEffectiveParent(m.id);
  const ovMap = {};
  (m.ov || []).forEach(x => { ovMap[x[0]] = x; });
  const cands = [];
  const used = new Set();
  if (vA) {
    const shA = ovMap[vA] ? ovMap[vA][1] : m.sh;
    cands.push({ vid: vA, share: shA, slot: SPOT_SLOTS[0], isLeg: false });
    used.add(vA);
  }
  if (state.tab === "legacy" && m.leg && !used.has(m.leg)) {
    const shL = ovMap[m.leg] ? ovMap[m.leg][1] : 0;
    cands.push({ vid: m.leg, share: shL, slot: SPOT_SLOTS[1], isLeg: true });
    used.add(m.leg);
    return cands;
  }
  // Same-owner candidates in descending share order (up to A, B, C, D)
  const sameOv = (m.ov || []).filter(x => x[4] === 1 && !used.has(x[0]));
  for (const x of sameOv) {
    if (cands.length >= 4) break;
    if (cands.length >= 2 && x[1] < 0.08) continue;
    cands.push({ vid: x[0], share: x[1], slot: SPOT_SLOTS[cands.length], isLeg: false });
    used.add(x[0]);
  }
  if (cands.length === 1 && m.r2 && !used.has(m.r2)) {
    cands.push({ vid: m.r2, share: m.r2sh, slot: SPOT_SLOTS[1], isLeg: false });
    used.add(m.r2);
  } else if (cands.length === 1 && m.leg && !used.has(m.leg)) {
    cands.push({ vid: m.leg, share: 0, slot: SPOT_SLOTS[1], isLeg: true });
    used.add(m.leg);
  }
  return cands;
}

function buildSpotlightCanvas() {
  const g = offSpot.getContext("2d");
  g.drawImage(offDim, 0, 0);
  const m = state.selMe ? DATA.me[state.selMe] : null;
  const vA = m ? getEffectiveParent(m.id) : state.selVan;
  const cands = m ? getSpotlightCandidates(m) : (vA ? [{ vid: vA, share: 1, slot: SPOT_SLOTS[0], isLeg: false }] : []);
  if (!cands.length && !m) return;

  // Map vanilla_id -> candidate index (0..3)
  const candByV = new Map();
  cands.forEach((c, idx) => candByV.set(c.vid, idx));

  const img = g.getImageData(0, 0, W, H);
  const d = img.data;
  const famA = new Set(vA && DATA.van[vA] ? DATA.van[vA].ms : []);
  const targetMe = m ? m.id : 0;

  for (let y = 1; y < H - 1; y++) {
    const row = y * W;
    for (let x = 1; x < W - 1; x++) {
      const i = row + x, p = i * 4;
      const mi = meIds[i], vi = vanIds[i];
      const ci = vi > 0 && candByV.has(vi) ? candByV.get(vi) : -1;
      const isTarget = mi > 0 && mi === targetMe;
      const inFamA = !targetMe && mi > 0 && famA.has(mi);

      if (ci >= 0) {
        const c = cands[ci];
        if (c.isLeg) { d[p]=165; d[p+1]=54; d[p+2]=64; }
        else { d[p]=c.slot.fill[0]; d[p+1]=c.slot.fill[1]; d[p+2]=c.slot.fill[2]; }
      }
      if (inFamA) {
        d[p] = Math.min(255, d[p] + 24); d[p+1] = Math.min(255, d[p+1] + 54); d[p+2] = Math.min(255, d[p+2] + 30);
        if (meIds[i-1] !== mi || meIds[i+1] !== mi || meIds[i-W] !== mi || meIds[i+W] !== mi) {
          d[p]=190; d[p+1]=240; d[p+2]=220;
        }
      }
      if (isTarget && !state.spaceBlink) {
        // Luminous soft highlight inside target ME state so ALL underlying A/B/C/D colors show through!
        d[p] = Math.min(255, d[p] + 72); d[p+1] = Math.min(255, d[p+1] + 82); d[p+2] = Math.min(255, d[p+2] + 88);
      }
      if (ci >= 0) {
        const c = cands[ci];
        if (vanIds[i-1] !== vi || vanIds[i+1] !== vi || vanIds[i-W] !== vi || vanIds[i+W] !== vi) {
          if (c.isLeg) { d[p]=248; d[p+1]=113; d[p+2]=113; }
          else { d[p]=c.slot.border[0]; d[p+1]=c.slot.border[1]; d[p+2]=c.slot.border[2]; }
        }
      }
      if (isTarget && !state.spaceBlink && (meIds[i-1] !== targetMe || meIds[i+1] !== targetMe || meIds[i-W] !== targetMe || meIds[i+W] !== targetMe)) {
        d[p]=255; d[p+1]=255; d[p+2]=255;
      }
    }
  }
  g.putImageData(img, 0, 0);
}

function drawLabel(ctx, text, mx, my, style = "me", customColors = null) {
  const sx = mx * state.zoom + state.tx;
  const sy = my * state.zoom + state.ty;
  if (sx < -80 || sy < -30 || sx > ctx.canvas.width + 80 || sy > ctx.canvas.height + 30) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  if (style === "cand" || style === "vanA" || style === "vanLeg") {
    ctx.font = "700 12.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    const bg = customColors ? customColors.bg : (style === "vanLeg" ? "rgba(145,28,36,0.92)" : "rgba(14,74,122,0.92)");
    const bd = customColors ? customColors.bd : (style === "vanLeg" ? "#fca5a5" : "#7dd3fc");
    const tw = ctx.measureText(text).width + 13;
    ctx.fillStyle = bg; ctx.strokeStyle = bd; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.roundRect(sx - tw/2, sy - 10.5, tw, 21, 5); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.fillText(text, sx, sy + 0.5);
  } else if (style === "target") {
    ctx.font = "700 12.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    const tw = ctx.measureText(text).width + 12;
    ctx.fillStyle = "rgba(15,23,42,0.94)"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.roundRect(sx - tw/2, sy - 10, tw, 20, 4); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.fillText(text, sx, sy + 0.5);
  } else if (style === "van") {
    ctx.font = "600 11.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(10,16,24,0.85)"; ctx.strokeText(text, sx, sy);
    ctx.fillStyle = "#f1f5f9"; ctx.fillText(text, sx, sy);
  } else {
    ctx.font = "500 10.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.lineWidth = 2.8; ctx.strokeStyle = "rgba(10,16,24,0.85)"; ctx.strokeText(text, sx, sy);
    ctx.fillStyle = "#e2e8f0"; ctx.fillText(text, sx, sy);
  }
  ctx.restore();
}

function drawCityPin(ctx, mx, my, text, kind = "van") {
  const sx = mx * state.zoom + state.tx;
  const sy = my * state.zoom + state.ty;
  if (sx < -40 || sy < -20 || sx > ctx.canvas.width + 40 || sy > ctx.canvas.height + 20) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (kind === "me") {
    // Bright gold circle for target ME VP city
    ctx.beginPath(); ctx.arc(sx, sy, 4.8, 0, Math.PI * 2);
    ctx.fillStyle = "#facc15"; ctx.fill();
    ctx.lineWidth = 1.8; ctx.strokeStyle = "#0f172a"; ctx.stroke();
    ctx.font = "700 11.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.lineWidth = 3.2; ctx.strokeStyle = "rgba(10,16,24,0.92)";
    ctx.strokeText(text, sx + 7, sy);
    ctx.fillStyle = "#fef08a"; ctx.fillText(text, sx + 7, sy);
  } else {
    // Crisp white diamond for Vanilla VP city
    ctx.beginPath();
    ctx.moveTo(sx, sy - 4.5); ctx.lineTo(sx + 4.5, sy);
    ctx.lineTo(sx, sy + 4.5); ctx.lineTo(sx - 4.5, sy);
    ctx.closePath();
    ctx.fillStyle = "#f8fafc"; ctx.fill();
    ctx.lineWidth = 1.5; ctx.strokeStyle = "#0f172a"; ctx.stroke();
    ctx.font = "600 10.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(10,16,24,0.88)";
    ctx.strokeText(text, sx + 7, sy);
    ctx.fillStyle = "#e0f2fe"; ctx.fillText(text, sx + 7, sy);
  }
  ctx.restore();
}

function drawArrow(ctx, x1, y1, x2, y2, color) {
  const sx1 = x1 * state.zoom + state.tx, sy1 = y1 * state.zoom + state.ty;
  const sx2 = x2 * state.zoom + state.tx, sy2 = y2 * state.zoom + state.ty;
  const ang = Math.atan2(sy2 - sy1, sx2 - sx1);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2.2;
  ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(sx1, sy1); ctx.lineTo(sx2, sy2); ctx.stroke();
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(sx2, sy2);
  ctx.lineTo(sx2 - 11 * Math.cos(ang - 0.35), sy2 - 11 * Math.sin(ang - 0.35));
  ctx.lineTo(sx2 - 11 * Math.cos(ang + 0.35), sy2 - 11 * Math.sin(ang + 0.35));
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

function drawSelectionOutline(ctx, targetId, idArr, color, widthPx) {
  const meta = idArr === meIds ? DATA.me[targetId] : DATA.van[targetId];
  if (!meta || !meta.bb) return;
  const [x0, y0, x1, y1] = meta.bb;
  ctx.save();
  ctx.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
  ctx.fillStyle = color;
  for (let y = Math.max(1, y0); y <= Math.min(H - 2, y1); y++) {
    const row = y * W;
    for (let x = Math.max(1, x0); x <= Math.min(W - 2, x1); x++) {
      const i = row + x;
      if (idArr[i] === targetId && (idArr[i-1] !== targetId || idArr[i+1] !== targetId || idArr[i-W] !== targetId || idArr[i+W] !== targetId)) {
        ctx.fillRect(x - 0.3, y - 0.3, widthPx, widthPx);
      }
    }
  }
  ctx.restore();
}

function drawScene() {
  const isDual = state.mode === "dual";
  stage.classList.toggle("dual", isDual);
  pane2.style.display = isDual ? "block" : "none";
  curtain.style.display = state.mode === "swipe" ? "block" : "none";
  document.getElementById("tag1").style.display = (state.mode === "swipe" || isDual) ? "block" : "none";
  document.getElementById("tag1r").style.display = state.mode === "swipe" ? "block" : "none";

  cv1.width = pane1.clientWidth; cv1.height = pane1.clientHeight;
  ctx1.setTransform(1, 0, 0, 1, 0, 0);
  ctx1.fillStyle = "#0c1824"; ctx1.fillRect(0, 0, cv1.width, cv1.height);
  ctx1.imageSmoothingEnabled = state.zoom < 1.8;

  const meLayer = state.useLegacyColor ? offMeLeg : offMeNew;
  const activeV = state.selVan || (state.selMe ? getEffectiveParent(state.selMe) : 0);

  if (state.mode === "swipe") {
    const splitX = Math.round(cv1.width * state.splitRatio);
    curtain.style.left = splitX + "px";
    document.getElementById("tag1").textContent = "◀ 原版 1.19 大州";
    document.getElementById("tag1r").textContent = (state.useLegacyColor ? "ME 旧表着色 ▶" : "ME 新映射同色拼图 ▶");
    ctx1.save();
    ctx1.beginPath(); ctx1.rect(0, 0, splitX, cv1.height); ctx1.clip();
    ctx1.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx1.drawImage(offVan, 0, 0);
    if (state.showRivers) ctx1.drawImage(offRivers, 0, 0);
    ctx1.restore();

    ctx1.save();
    ctx1.beginPath(); ctx1.rect(splitX, 0, cv1.width - splitX, cv1.height); ctx1.clip();
    ctx1.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx1.drawImage(state.spaceBlink ? offVan : meLayer, 0, 0);
    if (state.showRivers) ctx1.drawImage(offRivers, 0, 0);
    if (state.showVanBorder && !state.spaceBlink) ctx1.drawImage(offVanBorder, 0, 0);
    ctx1.restore();
  } else if (state.mode === "atlas") {
    ctx1.save();
    ctx1.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx1.drawImage(state.spaceBlink ? offVan : meLayer, 0, 0);
    if (state.showRivers) ctx1.drawImage(offRivers, 0, 0);
    if (state.showVanBorder && !state.spaceBlink) ctx1.drawImage(offVanBorder, 0, 0);
    ctx1.restore();
  } else if (state.mode === "spotlight") {
    ctx1.save();
    ctx1.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx1.drawImage(offSpot, 0, 0);
    if (state.showRivers) ctx1.drawImage(offRivers, 0, 0);
    ctx1.restore();
  } else if (isDual) {
    document.getElementById("tag1").textContent = "原版 1.19 大州（像素同步）";
    ctx1.save();
    ctx1.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx1.drawImage(offVan, 0, 0);
    if (state.showRivers) ctx1.drawImage(offRivers, 0, 0);
    ctx1.restore();

    cv2.width = pane2.clientWidth; cv2.height = pane2.clientHeight;
    ctx2.setTransform(1, 0, 0, 1, 0, 0);
    ctx2.fillStyle = "#0c1824"; ctx2.fillRect(0, 0, cv2.width, cv2.height);
    ctx2.imageSmoothingEnabled = state.zoom < 1.8;
    ctx2.save();
    ctx2.setTransform(state.zoom, 0, 0, state.zoom, state.tx, state.ty);
    ctx2.drawImage(meLayer, 0, 0);
    if (state.showRivers) ctx2.drawImage(offRivers, 0, 0);
    if (state.showVanBorder) ctx2.drawImage(offVanBorder, 0, 0);
    ctx2.restore();
  }

  // Selection outlines & crosshairs
  if (state.mode !== "spotlight") {
    if (activeV) {
      drawSelectionOutline(ctx1, activeV, vanIds, "#ffffff", 1.5);
      if (isDual) drawSelectionOutline(ctx2, activeV, vanIds, "#ffffff", 1.5);
    }
    if (state.selMe) {
      drawSelectionOutline(isDual ? ctx2 : ctx1, state.selMe, meIds, "#facc15", 1.7);
    }
    if (isDual && state.hovX >= 0) {
      const cx = state.hovX * state.zoom + state.tx, cy = state.hovY * state.zoom + state.ty;
      for (const g of [ctx1, ctx2]) {
        g.save(); g.setTransform(1,0,0,1,0,0); g.strokeStyle = "rgba(255,255,255,0.75)"; g.lineWidth = 1;
        g.beginPath(); g.moveTo(cx - 10, cy); g.lineTo(cx + 10, cy); g.moveTo(cx, cy - 10); g.lineTo(cx, cy + 10); g.stroke();
        g.restore();
      }
    }
  }

  // City / VP pins!
  if (state.showCities) {
    if (state.mode === "spotlight") {
      const m = state.selMe ? DATA.me[state.selMe] : null;
      const cands = m ? getSpotlightCandidates(m) : (activeV ? [{ vid: activeV }] : []);
      // Draw Vanilla VP diamonds in every lit candidate A/B/C/D
      cands.forEach(c => {
        const va = DATA.van[c.vid];
        if (!va || !va.vps) return;
        va.vps.forEach(vp => drawCityPin(ctx1, vp[0], vp[1], `${vp[2]} (${vp[3]})`, "van"));
      });
      // Draw Target ME VP gold circles on top
      if (m && m.vps) {
        m.vps.forEach(vp => drawCityPin(ctx1, vp[0], vp[1], `${vp[2]} (${vp[3]})`, "me"));
      }
    } else if (activeV && DATA.van[activeV]) {
      const va = DATA.van[activeV];
      (va.vps || []).forEach(vp => drawCityPin(ctx1, vp[0], vp[1], `${vp[2]}`, "van"));
      if (state.selMe && DATA.me[state.selMe]?.vps) {
        const tCtx = isDual ? ctx2 : ctx1;
        DATA.me[state.selMe].vps.forEach(vp => drawCityPin(tCtx, vp[0], vp[1], `${vp[2]} (${vp[3]})`, "me"));
      }
    }
  }

  // On-map readable state labels!
  if (state.showLabels) {
    if (state.mode === "spotlight") {
      const m = state.selMe ? DATA.me[state.selMe] : null;
      const cands = m ? getSpotlightCandidates(m) : (activeV ? [{ vid: activeV, share: 1, slot: SPOT_SLOTS[0], isLeg: false }] : []);
      cands.forEach((c, idx) => {
        const va = DATA.van[c.vid];
        if (!va || !va.cx) return;
        if (m && c.isLeg && Math.hypot(m.cx - va.cx, m.cy - va.cy) > 15) {
          drawArrow(ctx1, m.cx, m.cy, va.cx, va.cy, "#f87171");
        }
        const pct = m && c.share > 0 ? ` (${(c.share*100).toFixed(0)}%)` : "";
        const lbl = c.isLeg ? `旧表: v${va.id} ${va.n}` : `${c.slot.key}: v${va.id} ${va.n}${pct}`;
        const col = c.isLeg ? { bg: "rgba(145,28,36,0.92)", bd: "#fca5a5" } : { bg: c.slot.bg, bd: c.slot.bd };
        drawLabel(ctx1, lbl, va.cx, va.cy - (idx === 0 ? 12 : 0), "cand", col);
      });
      if (m) {
        drawLabel(ctx1, `ME #${m.id} ${m.n}`, m.cx, m.cy - 10, "target");
      } else if (activeV && DATA.van[activeV]) {
        const va = DATA.van[activeV];
        va.ms.forEach(mid => {
          const mi = DATA.me[mid];
          if (mi && mi.cx) drawLabel(ctx1, (mid === va.p ? "★ " : "") + `#${mid} ${mi.n}`, mi.cx, mi.cy, mid === va.p ? "target" : "me");
        });
      }
    } else {
      if (activeV && DATA.van[activeV]) {
        const va = DATA.van[activeV];
        drawLabel(ctx1, `v${va.id} ${va.n}`, va.cx, va.cy, "vanA");
        const targetCtx = isDual ? ctx2 : ctx1;
        if (state.zoom >= 1.4 || va.ms.length <= 12) {
          va.ms.forEach(mid => {
            const mi = DATA.me[mid];
            if (mi && mi.cx) drawLabel(targetCtx, (mid === va.p ? "★ " : "") + mi.n, mi.cx, mi.cy, mid === state.selMe ? "target" : "me");
          });
        }
      } else if (state.zoom >= 1.8) {
        for (const k in DATA.van) {
          const va = DATA.van[k];
          if (va.cx) drawLabel(ctx1, va.n, va.cx, va.cy, "van");
        }
      }
    }
  }
  updateLegendAndSpotBar();
}

function updateLegendAndSpotBar() {
  const leg = document.getElementById("legend");
  const bar = document.getElementById("spotBar");
  if (state.mode === "swipe") {
    leg.innerHTML = `同色即同一原版州 · 拖动 <b>⇄</b> 对比裂变 · 按住 <b>Space</b> 瞬切原版`;
  } else if (state.mode === "atlas") {
    leg.innerHTML = `${state.useLegacyColor ? "<b style='color:#f87171'>旧表着色中</b>：错色斑点即旧表错标" : "<b>新映射同色拼图</b>：同色块应贴合原版白线"} · <b>Space</b> 瞬切原版`;
  } else if (state.mode === "dual") {
    leg.innerHTML = `左右同步缩放 · 十字准星对齐 · <b>★</b> 主城目标`;
  } else {
    leg.innerHTML = `<span class="sw" style="background:#38bdf8"></span>A &nbsp;<span class="sw" style="background:#f59e0b"></span>B &nbsp;<span class="sw" style="background:#34d399"></span>C &nbsp;<span class="sw" style="background:#a78bfa"></span>D &nbsp;· <b style="color:#facc15">●</b> ME主城 &nbsp;<b style="color:#f8fafc">◆</b> 原版VP &nbsp;· <span style="color:#38bdf8">〰</span> 河流`;
  }

  const m = state.selMe ? DATA.me[state.selMe] : null;
  if (state.mode === "spotlight" && m) {
    const cands = getSpotlightCandidates(m);
    const posStr = state.reviewIdx >= 0 ? `第 ${state.reviewIdx + 1}/${reviewList.length} · ` : "";
    const impBadge = m.imp === "high"
      ? `<span class="badge b-err">🔴 国策分歧(${m.dc})</span>`
      : (m.imp === "island" ? `<span class="badge b-info">🏝️ 岛屿</span>` : `<span class="badge b-ok">🟢 无国策分歧</span>`);
    const btns = cands.map((c, idx) => {
      const vn = DATA.van[c.vid]?.n || "?";
      const pct = c.share > 0 ? ` ${(c.share*100).toFixed(0)}%` : "";
      const prefix = idx === 0 ? `[1] 确认 ${c.slot.key}` : `[${idx+1}] 改归 ${c.slot.key}`;
      return `<button class="btn ${c.slot.btn}" onclick="quickChoose(${m.id}, ${c.vid})">${prefix}: v${c.vid} ${vn}${pct}</button>`;
    }).join("");
    bar.innerHTML = `
      <span style="font-size:12px;color:var(--muted)">${posStr}<b style="color:#fff">#${m.id} ${m.n}</b> ${impBadge}</span>
      ${btns}
      <button class="btn" onclick="stepReview(-1)">←</button>
      <button class="btn" onclick="stepReview(1)">→</button>
    `;
    bar.classList.add("show");
  } else {
    bar.classList.remove("show");
  }
}

function resetView() {
  const w = pane1.clientWidth, h = pane1.clientHeight;
  const s = Math.min(w / W, h / H);
  state.zoom = s; state.tx = (w - W * s) / 2; state.ty = (h - H * s) / 2;
  drawScene();
}

function zoomToBBox(bb, pad = 75, maxZ = 4.8) {
  if (!bb) return;
  const w = pane1.clientWidth, h = pane1.clientHeight;
  const bw = Math.max(110, (bb[2] - bb[0]) + pad * 2);
  const bh = Math.max(110, (bb[3] - bb[1]) + pad * 2);
  const s = Math.min(w / bw, h / bh, maxZ);
  state.zoom = s;
  state.tx = w / 2 - ((bb[0] + bb[2]) / 2) * s;
  state.ty = h / 2 - ((bb[1] + bb[3]) / 2) * s;
  drawScene();
}

function unionBBox(list) {
  let x0 = W, y0 = H, x1 = 0, y1 = 0, ok = false;
  for (const bb of list) {
    if (!bb) continue;
    if (bb[0] < x0) x0 = bb[0]; if (bb[1] < y0) y0 = bb[1];
    if (bb[2] > x1) x1 = bb[2]; if (bb[3] > y1) y1 = bb[3];
    ok = true;
  }
  return ok ? [x0, y0, x1, y1] : null;
}

function focusSelection() {
  const m = state.selMe ? DATA.me[state.selMe] : null;
  const vA = m ? getEffectiveParent(m.id) : state.selVan;
  const boxes = [];
  if (m && m.bb) boxes.push(m.bb);
  if (vA && DATA.van[vA]?.bb) boxes.push(DATA.van[vA].bb);
  if (m && state.mode === "spotlight") {
    const cands = getSpotlightCandidates(m);
    for (let i = 1; i < cands.length; i++) {
      const vb = DATA.van[cands[i].vid];
      if (vb?.bb && Math.hypot(m.cx - vb.cx, m.cy - vb.cy) < 180) boxes.push(vb.bb);
    }
  }
  if (state.mode === "spotlight") buildSpotlightCanvas();
  zoomToBBox(unionBBox(boxes), 65, 5.2);
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#modeSeg button").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  if (mode === "spotlight") buildSpotlightCanvas();
  drawScene();
}

function refreshReviewList() {
  const impRank = { high: 0, island: 1, low: 2 };
  const hasOpenRev = Object.values(DATA.me).some(m => m.rev);
  reviewList = Object.values(DATA.me)
    .filter(m => {
      if (hasOpenRev) {
        return m.rev && (state.revFilter === "all" || (state.revFilter === "high" ? m.imp !== "low" : m.imp === "low"));
      }
      if (m.t !== "adjudicated") return false;
      if (state.revFilter === "high") return m.res === "adjudicated_override";
      if (state.revFilter === "low") return m.res === "adjudicated_confirmed";
      return true;
    })
    .sort((a, b) => {
      if (!hasOpenRev) {
        const ra = a.res === "adjudicated_override" ? 0 : 1;
        const rb = b.res === "adjudicated_override" ? 0 : 1;
        if (ra !== rb) return ra - rb;
      }
      return (impRank[a.imp] - impRank[b.imp]) || (b.dc - a.dc) || (a.sh - b.sh) || (a.id - b.id);
    })
    .map(m => m.id);
}

window.quickChoose = function(mid, vid) {
  const origV = DATA.me[mid]?.v || 0;
  decisions[mid] = { status: vid === origV ? "accept" : "override", override: vid === origV ? 0 : vid, note: "spotlight", ts: new Date().toISOString() };
  try { localStorage.setItem(STORE_KEY, JSON.stringify(decisions)); } catch {}
  buildAtlasLayers();
  if (state.reviewIdx >= 0) stepReview(1);
  else { updateDetail(); renderTable(); if (state.mode === "spotlight") buildSpotlightCanvas(); drawScene(); }
};

window.stepReview = function(delta) {
  if (!reviewList.length) return;
  if (state.reviewIdx < 0) state.reviewIdx = 0;
  else state.reviewIdx = (state.reviewIdx + delta + reviewList.length) % reviewList.length;
  const mid = reviewList[state.reviewIdx];
  state.selMe = mid; state.selVan = getEffectiveParent(mid);
  setMode("spotlight");
  updateDetail(); renderTable(); focusSelection();
};

function updateDetail() {
  const el = document.getElementById("detail");
  const m = state.selMe ? DATA.me[state.selMe] : null;
  const v = state.selVan ? DATA.van[state.selVan] : (m ? DATA.van[getEffectiveParent(m.id)] : null);
  if (!m && !v) return;
  let html = "";
  if (m) {
    const dec = decisions[m.id] || {};
    const effV = getEffectiveParent(m.id);
    const cands = getSpotlightCandidates(m);
    const candMap = {};
    cands.forEach(c => { candMap[c.vid] = c.slot; });

    const cls = m.rev ? "b-warn" : "b-ok";
    const impBadge = m.imp === "high"
      ? `<span class="badge b-err">🔴 国策分歧(${m.dc})</span>`
      : (m.imp === "island" ? `<span class="badge b-info">🏝️ 离岸岛屿</span>` : `<span class="badge b-ok">🟢 领土国策无分歧</span>`);
    const ownEn = DATA.owners?.[m.no] || DATA.owners?.[m.o] || m.o;
    const wikiUrl = `https://en.wikipedia.org/w/index.php?search=${encodeURIComponent(m.n + " " + ownEn)}`;

    // 1. Area candidates with A/B/C/D color pills
    const ovHtml = (m.ov || []).map(x => {
      const sl = candMap[x[0]];
      const pCls = sl ? sl.pill : "cO";
      const kStr = sl ? `${sl.key}: ` : "";
      return `<span class="cand-pill ${pCls}">${kStr}<b>v${x[0]}</b> ${DATA.van[x[0]]?.n||""} ${(x[1]*100).toFixed(0)}%</span>`;
    }).join("") || "—";

    // 2. ME VP city landings
    const vpHtml = (m.vps && m.vps.length)
      ? m.vps.map(vp => {
          const lv = vp[4];
          const lvn = lv ? `v${lv} ${DATA.van[lv]?.n||""}` : "水域";
          const matchCls = lv === effV ? "b-ok" : "b-warn";
          return `<span style="margin-right:6px"><b style="color:#fef08a">● ${vp[2]}</b>(${vp[3]}) → <span class="badge ${matchCls}">${lvn}</span></span>`;
        }).join("<br>")
      : `<span style="color:var(--muted)">无 VP 城市</span>`;

    // 3. ME sub-regional cores
    const coreHtml = (m.cs && m.cs.length)
      ? m.cs.map(c => `<span class="sc-tag">${c[0]}: ${c[1]}</span>`).join("")
      : `<span style="color:var(--muted)">无独立区域 Core</span>`;

    // 4. Vanilla border-change script diff
    let scHtml = `<span style="color:#4ade80">原版 1.19 领土/核心/宣称国策从未把 A 与 B 拆开，按面积确认即可</span>`;
    if (m.imp === "island") {
      scHtml = (m.da && m.da.length)
        ? `A 涉及: ` + m.da.slice(0, 5).map(x => `<span class="sc-tag a">${x}</span>`).join("")
        : `<span style="color:var(--muted)">无特殊领土事件</span>`;
    } else if (m.dc > 0) {
      const pA = (m.da && m.da.length) ? `<div>仅 <b>A</b>: ${m.da.slice(0, 5).map(x => `<span class="sc-tag a">${x}</span>`).join("")}</div>` : "";
      const pB = (m.db && m.db.length) ? `<div>仅 <b>B</b>: ${m.db.slice(0, 5).map(x => `<span class="sc-tag b">${x}</span>`).join("")}</div>` : "";
      scHtml = pA + pB;
    }

    // 5. Legacy agreement
    const legSame = m.leg && m.leg === effV;
    const legHtml = m.leg
      ? `旧表 <b>v${m.leg} ${DATA.van[m.leg]?.n||""}</b> ${legSame ? `<span class="badge b-ok">一致 ✓</span>` : `<span class="badge b-warn">${m.ld || "分歧"}</span>`}`
      : "无旧表记录";

    html += `<h3>ME #${m.id} ${m.n} (${m.o}${m.o!==m.no?"→"+m.no:""}) <span class="badge ${cls}">${m.t}</span> ${impBadge}
      <a class="btn xs" href="${wikiUrl}" target="_blank" rel="noopener" style="margin-left:auto" title="在维基百科搜索该地的真实历史行政归属">🔍 维基地理</a>
    </h3>
    ${m.adv ? `<div class="adv-box ${m.imp === "low" ? "low" : (m.imp === "high" ? "high" : "")}">${m.adv}</div>` : ""}
    <div class="kv">
      <div class="k">面积分布</div><div>${ovHtml}</div>
      <div class="k">主城落点</div><div>${vpHtml}</div>
      <div class="k">区域 Core</div><div>${coreHtml}</div>
      <div class="k">国策分歧</div><div>${scHtml}</div>
      <div class="k">旧表对比</div><div>${legHtml} · <span style="color:var(--muted)">${m.res}</span></div>
    </div>
    <div class="adj-bar">
      <button class="btn ok" onclick="setDec(${m.id},'accept')">✓ 确认 v${m.v}</button>
      <input id="ovrV" placeholder="改原版ID" value="${dec.override||""}">
      <input id="ovrN" class="note" placeholder="备注" value="${dec.note||""}">
      <button class="btn" onclick="setDec(${m.id},'override')">覆写</button>
      ${dec.status?`<span class="badge b-info">${dec.status}${dec.override?"→v"+dec.override:""}</span>`:""}
    </div>`;
  } else if (v) {
    const vpList = (v.vps || []).map(vp => `◆ ${vp[2]}(${vp[3]})`).join(" · ") || "无";
    html += `<h3>原版 v${v.id} ${v.n} (${v.o})</h3>
    <div class="kv">
      <div class="k">ME 子州数</div><div><b>${v.ms.length}</b> 个 (反向覆盖 ${(v.rc*100).toFixed(1)}%)</div>
      <div class="k">主城/经济目标</div><div>★ ME #${v.p} ${v.pn}</div>
      <div class="k">原版 VP 城</div><div>${vpList}</div>
      <div class="k">子州清单</div><div>${v.ms.map(x=>`#${x} ${DATA.me[x]?.n||""}`).slice(0,18).join(", ")}${v.ms.length>18?"...":""}</div>
    </div>`;
  }
  el.innerHTML = html;
}

window.setDec = function(mid, status) {
  const ovr = document.getElementById("ovrV")?.value.trim();
  const note = document.getElementById("ovrN")?.value.trim();
  decisions[mid] = { status, override: status === "override" ? (parseInt(ovr)||0) : 0, note, ts: new Date().toISOString() };
  try { localStorage.setItem(STORE_KEY, JSON.stringify(decisions)); } catch {}
  buildAtlasLayers();
  if (state.mode === "spotlight") buildSpotlightCanvas();
  updateDetail(); renderTable(); drawScene();
};

function renderTable() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const own = document.getElementById("ownerSel").value;
  const th = document.getElementById("thead"), tb = document.getElementById("tbody");
  document.getElementById("filterBar").style.display = state.tab === "review" ? "flex" : "none";

  if (state.tab === "review" || state.tab === "all") {
    const hasOpenRev = Object.values(DATA.me).some(m => m.rev);
    th.innerHTML = state.tab === "review"
      ? "<tr><th>ME 州</th><th>国</th><th>A / B 候选</th><th>分歧</th><th>状态</th></tr>"
      : "<tr><th>ME 州</th><th>国</th><th>A 主父州</th><th>B 次选</th><th>状态</th></tr>";
    const impRank = { high: 0, island: 1, low: 2 };
    let arr = Object.values(DATA.me).filter(m => state.tab === "all" || (hasOpenRev ? m.rev : m.t === "adjudicated"));
    if (state.tab === "review" && state.revFilter !== "all") {
      if (hasOpenRev) arr = arr.filter(m => state.revFilter === "high" ? m.imp !== "low" : m.imp === "low");
      else arr = arr.filter(m => state.revFilter === "high" ? m.res === "adjudicated_override" : m.res === "adjudicated_confirmed");
    }
    const rows = arr.filter(m => {
      if (own && m.o !== own && m.no !== own) return false;
      if (!q) return true;
      const csStr = (m.cs || []).map(c => c[0] + " " + c[1]).join(" ");
      return `${m.id} ${m.n} ${m.o} ${m.v} ${m.vn} ${m.rr} ${csStr}`.toLowerCase().includes(q);
    });
    if (state.tab === "review") {
      rows.sort((a,b) => {
        if (!hasOpenRev) {
          const ra = a.res === "adjudicated_override" ? 0 : 1;
          const rb = b.res === "adjudicated_override" ? 0 : 1;
          if (ra !== rb) return ra - rb;
        }
        return (impRank[a.imp] - impRank[b.imp]) || (b.dc - a.dc) || (a.sh - b.sh) || (a.id - b.id);
      });
    } else {
      rows.sort((a,b) => (b.rev - a.rev) || (a.sh - b.sh) || (a.id - b.id));
    }
    tb.innerHTML = rows.slice(0, 400).map(m => {
      const dec = decisions[m.id];
      const adjTag = m.res === "adjudicated_override"
        ? `<span class="badge b-info">已改B✓</span>`
        : (m.res === "adjudicated_confirmed" ? `<span class="badge b-ok">已确A✓</span>` : null);
      const tag = dec ? `<span class="badge b-info">${dec.status}${dec.override?"→v"+dec.override:""}</span>` : (adjTag || (m.rev ? `<span class="badge b-warn">${(m.sh*100).toFixed(0)}%</span>` : `<span class="badge b-ok">${(m.sh*100).toFixed(0)}%</span>`));
      if (state.tab === "review") {
        const ab = `<b>v${m.v}</b> ${m.vn}` + (m.r2 ? `<br><span style="color:var(--muted);font-size:11px">vs v${m.r2} ${DATA.van[m.r2]?.n||""} (${(m.r2sh*100).toFixed(0)}%)</span>` : "");
        const impTag = m.imp === "high"
          ? `<span class="badge b-err">🔴${m.dc}</span>`
          : (m.imp === "island" ? `<span class="badge b-info">🏝️</span>` : `<span class="badge b-ok">🟢0</span>`);
        return `<tr class="${m.id===state.selMe?"sel":""}" onclick="pickMe(${m.id}, true)"><td>#${m.id} ${m.n}</td><td>${m.o}</td><td>${ab}</td><td>${impTag}</td><td>${tag}</td></tr>`;
      }
      const bStr = m.r2 ? `v${m.r2} (${(m.r2sh*100).toFixed(0)}%)` : "—";
      return `<tr class="${m.id===state.selMe?"sel":""}" onclick="pickMe(${m.id}, true)"><td>#${m.id} ${m.n}</td><td>${m.o}</td><td>v${m.v} ${m.vn}</td><td>${bStr}</td><td>${tag}</td></tr>`;
    }).join("");
  } else if (state.tab === "legacy") {
    th.innerHTML = "<tr><th>ME 州</th><th>旧表(红)</th><th>新表(蓝)</th><th>分类</th></tr>";
    const rows = DATA.legacy.filter(r => (!own || r.o === own) && (!q || `${r.me} ${r.mn} ${r.o} ${r.lv} ${r.lvn} ${r.nv} ${r.nvn} ${r.dc}`.toLowerCase().includes(q)));
    tb.innerHTML = rows.slice(0, 400).map(r => {
      const cls = r.dc.startsWith("legacy_") ? "b-err" : "b-warn";
      return `<tr class="${r.me===state.selMe?"sel":""}" onclick="pickMe(${r.me}, true)"><td>#${r.me} ${r.mn}</td><td>v${r.lv} (${r.ldist}px)</td><td>v${r.nv} (${(r.nsh*100).toFixed(0)}%)</td><td><span class="badge ${cls}">${r.dc.replace("legacy_","")}</span></td></tr>`;
    }).join("");
  } else {
    th.innerHTML = "<tr><th>原版州</th><th>国</th><th>子州</th><th>覆盖</th><th>★ 主城</th></tr>";
    const rows = Object.values(DATA.van).filter(v => (!own || v.o === own) && (!q || `${v.id} ${v.n} ${v.o} ${v.pn}`.toLowerCase().includes(q)));
    rows.sort((a,b) => a.id - b.id);
    tb.innerHTML = rows.slice(0, 500).map(v => `<tr class="${v.id===state.selVan?"sel":""}" onclick="pickVan(${v.id})"><td>v${v.id} ${v.n}</td><td>${v.o}</td><td>${v.ms.length}</td><td>${(v.rc*100).toFixed(0)}%</td><td>#${v.p} ${v.pn}</td></tr>`).join("");
  }
}

window.pickMe = function(mid, autoSpot = false) {
  state.selMe = mid; state.selVan = getEffectiveParent(mid);
  state.reviewIdx = reviewList.indexOf(mid);
  if (autoSpot && (state.tab === "review" || state.tab === "legacy")) setMode("spotlight");
  updateDetail(); renderTable(); focusSelection();
};
window.pickVan = function(vid) {
  state.selMe = 0; state.selVan = vid; state.reviewIdx = -1;
  updateDetail(); renderTable(); focusSelection();
};

function bindPaneEvents(pane, canvas, isRightDual) {
  canvas.onwheel = e => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.28 : 0.78;
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const nz = Math.max(0.35, Math.min(12, state.zoom * f));
    state.tx = mx - (mx - state.tx) * (nz / state.zoom);
    state.ty = my - (my - state.ty) * (nz / state.zoom);
    state.zoom = nz; drawScene();
  };
  canvas.onmousedown = e => {
    state.dragging = true; state.moved = false;
    state.dx0 = e.clientX - state.tx; state.dy0 = e.clientY - state.ty;
  };
  canvas.onmousemove = e => {
    if (state.dragCurtain) return;
    if (state.dragging) {
      state.moved = true; state.tx = e.clientX - state.dx0; state.ty = e.clientY - state.dy0; drawScene(); return;
    }
    const r = canvas.getBoundingClientRect();
    const mx = Math.floor((e.clientX - r.left - state.tx) / state.zoom);
    const my = Math.floor((e.clientY - r.top - state.ty) / state.zoom);
    if (mx < 0 || my < 0 || mx >= W || my >= H) return;
    state.hovX = mx; state.hovY = my;
    const m = meIds[my * W + mx], v = vanIds[my * W + mx];
    if (m !== state.hovMe || v !== state.hovVan || state.mode === "dual") {
      state.hovMe = m; state.hovVan = v;
      const mi = DATA.me[m], vi = DATA.van[v];
      document.getElementById("hud").innerHTML =
        (vi ? `原版: <b>v${vi.id} ${vi.n}</b> (${vi.o})` : "原版: 水域") +
        " &nbsp;↔&nbsp; " +
        (mi ? `ME: <b>#${mi.id} ${mi.n}</b> (${mi.o}) → 归属 <b>v${getEffectiveParent(mi.id)} ${DATA.van[getEffectiveParent(mi.id)]?.n||""}</b> (${(mi.sh*100).toFixed(0)}%)` : "ME: 水域/排除区");
      if (state.mode === "dual") drawScene();
    }
  };
  canvas.onclick = e => {
    if (state.moved) return;
    const r = canvas.getBoundingClientRect();
    const mx = Math.floor((e.clientX - r.left - state.tx) / state.zoom);
    const my = Math.floor((e.clientY - r.top - state.ty) / state.zoom);
    if (mx < 0 || my < 0 || mx >= W || my >= H) return;
    const m = meIds[my * W + mx], v = vanIds[my * W + mx];
    const clickedVanillaSide = (state.mode === "swipe" && (e.clientX - r.left) < cv1.width * state.splitRatio) || (state.mode === "dual" && !isRightDual);
    if (clickedVanillaSide && v && DATA.van[v]) {
      state.selMe = 0; state.selVan = v; state.reviewIdx = -1;
    } else if (m && DATA.me[m]) {
      state.selMe = m; state.selVan = getEffectiveParent(m); state.reviewIdx = reviewList.indexOf(m);
    } else if (v && DATA.van[v]) {
      state.selMe = 0; state.selVan = v; state.reviewIdx = -1;
    } else {
      state.selMe = 0; state.selVan = 0; state.reviewIdx = -1;
    }
    if (state.mode === "spotlight") buildSpotlightCanvas();
    updateDetail(); renderTable(); drawScene();
  };
}

async function init() {
  const s = DATA.summary;
  const revAll = Object.values(DATA.me).filter(m => m.rev);
  const revHigh = revAll.filter(m => m.imp !== "low").length;
  const revLow = revAll.filter(m => m.imp === "low").length;
  const adjAll = Object.values(DATA.me).filter(m => m.t === "adjudicated");
  const adjOvr = adjAll.filter(m => m.res === "adjudicated_override").length;
  const adjConf = adjAll.filter(m => m.res === "adjudicated_confirmed").length;
  const cleared = s.review_queue_count === 0;

  document.getElementById("pills").innerHTML = `
    <div class="pill ok">1936国界像素一致 <b>${(s.registration_same_owner_acc*100).toFixed(2)}%</b></div>
    <div class="pill ok">首都校验 <b>${s.registration_capitals_hit}</b></div>
    <div class="pill">欧洲 ME 州 <b>${s.eu_me_states}</b></div>
    ${cleared
      ? `<div class="pill ok">复核清零 <b>✓ 已裁决 ${adjAll.length}</b> (改B ${adjOvr})</div>`
      : `<div class="pill warn">待复核 <b>${s.review_queue_count}</b> (高分歧 ${revHigh})</div>`}
    <div class="pill err">旧表错标 <b>${s.legacy_distant_or_owner_errors}</b></div>
  `;
  if (cleared) {
    document.querySelector('.tab[data-tab="review"]').innerHTML = `已裁决 (${adjAll.length} ✓)`;
    document.querySelector('#filterBar .chip[data-f="high"]').innerHTML = `✏️ 改归B (${adjOvr})`;
    document.querySelector('#filterBar .chip[data-f="low"]').innerHTML = `✓ 确认A (${adjConf})`;
    document.getElementById("btnBulkLow").style.display = "none";
    document.getElementById("btnStartReview").textContent = `✓ 巡检已裁决(${adjAll.length})`;
  } else {
    document.getElementById("cntRev").textContent = s.review_queue_count;
    document.getElementById("cntHigh").textContent = revHigh;
    document.getElementById("cntLow").textContent = revLow;
    document.getElementById("btnStartReview").textContent = `▶ 逐条复核(${revHigh}高分歧/${s.review_queue_count})`;
  }
  document.getElementById("cntLeg").textContent = DATA.legacy.length;

  refreshReviewList();

  const owners = [...new Set(Object.values(DATA.me).map(m => m.o))].sort();
  const sel = document.getElementById("ownerSel");
  owners.forEach(o => { const opt = document.createElement("option"); opt.value = o; opt.textContent = o; sel.appendChild(opt); });

  const [mIm, vIm, rIm] = await Promise.all([loadImg(DATA.me_hit), loadImg(DATA.van_hit), loadImg(DATA.rivers)]);
  meIds = decodeIds(mIm); vanIds = decodeIds(vIm);
  offRivers.getContext("2d").drawImage(rIm, 0, 0);
  buildAtlasLayers();
  resetView();
  renderTable();

  document.querySelectorAll("#modeSeg button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  document.getElementById("chkVanBorder").onchange = e => { state.showVanBorder = e.target.checked; drawScene(); };
  document.getElementById("chkRivers").onchange = e => { state.showRivers = e.target.checked; drawScene(); };
  document.getElementById("chkCities").onchange = e => { state.showCities = e.target.checked; drawScene(); };
  document.getElementById("chkLegacyColor").onchange = e => { state.useLegacyColor = e.target.checked; drawScene(); };
  document.getElementById("chkLabels").onchange = e => { state.showLabels = e.target.checked; drawScene(); };
  document.getElementById("btnStartReview").onclick = () => {
    state.tab = "review";
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === "review"));
    refreshReviewList();
    state.reviewIdx = -1;
    stepReview(1);
  };
  document.querySelectorAll("#filterBar .chip").forEach(ch => ch.onclick = () => {
    document.querySelectorAll("#filterBar .chip").forEach(x => x.classList.remove("active"));
    ch.classList.add("active");
    state.revFilter = ch.dataset.f;
    refreshReviewList();
    state.reviewIdx = state.selMe ? reviewList.indexOf(state.selMe) : -1;
    renderTable();
    updateLegendAndSpotBar();
  });
  document.getElementById("btnBulkLow").onclick = () => {
    let n = 0;
    Object.values(DATA.me).forEach(m => {
      if (m.rev && m.imp === "low" && !decisions[m.id]) {
        decisions[m.id] = { status: "accept", override: 0, note: "bulk_low_impact", ts: new Date().toISOString() };
        n++;
      }
    });
    if (n > 0) {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(decisions)); } catch {}
      buildAtlasLayers();
      renderTable();
      updateDetail();
      drawScene();
    }
  };
  document.getElementById("btnReset").onclick = resetView;
  document.getElementById("btnExport").onclick = () => {
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), decisions }, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "eu-mapping-decisions.json"; a.click();
  };
  document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active"); state.tab = t.dataset.tab; renderTable();
  });
  document.getElementById("q").oninput = renderTable;
  document.getElementById("ownerSel").onchange = renderTable;

  // Curtain handle drag
  curtain.onmousedown = e => { e.stopPropagation(); state.dragCurtain = true; };
  window.addEventListener("mousemove", e => {
    if (!state.dragCurtain) return;
    const r = pane1.getBoundingClientRect();
    state.splitRatio = Math.max(0.02, Math.min(0.98, (e.clientX - r.left) / r.width));
    drawScene();
  });
  window.addEventListener("mouseup", () => { state.dragging = false; state.dragCurtain = false; });

  bindPaneEvents(pane1, cv1, false);
  bindPaneEvents(pane2, cv2, true);

  // Keyboard shortcuts: Space=blink Vanilla, 1/2/3/4=choose A/B/C/D in Spotlight, Left/Right=prev/next review!
  window.addEventListener("keydown", e => {
    if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (!state.spaceBlink) { state.spaceBlink = true; if (state.mode === "spotlight") buildSpotlightCanvas(); drawScene(); }
    } else if (e.key === "ArrowRight") { e.preventDefault(); stepReview(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); stepReview(-1); }
    else if (["1", "2", "3", "4"].includes(e.key) && state.mode === "spotlight" && state.selMe) {
      const m = DATA.me[state.selMe];
      const cands = getSpotlightCandidates(m);
      const idx = parseInt(e.key, 10) - 1;
      if (cands[idx]) quickChoose(state.selMe, cands[idx].vid);
    }
  });
  window.addEventListener("keyup", e => {
    if (e.code === "Space" && state.spaceBlink) {
      state.spaceBlink = false;
      if (state.mode === "spotlight") buildSpotlightCanvas();
      drawScene();
    }
  });
  window.onresize = drawScene;
}
init();
</script>
</body>
</html>
"""


# ----------------------------------------------------------- main builder

def build_all(vanilla_root: str = DEFAULT_VANILLA, mod_dir: str | None = None,
              out_dir: str | None = None, build_review: bool = True) -> dict:
    mod_dir = mod_dir or mod_root()
    docs_root = Path(repo_root()) / "docs" / "states"
    out_path = Path(out_dir) if out_dir else (docs_root / "eu_mapping")
    out_path.mkdir(parents=True, exist_ok=True)

    reg = run_registration(vanilla_root, mod_dir, apply_residual=True)
    me = reg.me
    van = reg.van
    excl_sids = set(me["excluded_states"])
    clean_core_tags = sorted(reg.clean_cores)

    # Save registration_report.json
    (out_path / "registration_report.json").write_text(
        json.dumps(reg.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    excl_mask = np.isin(me["state_raster"], list(excl_sids)) if excl_sids else np.zeros_like(me["land_mask"])
    eu_land_me = me["land_mask"] & (me["state_raster"] > 0) & (~excl_mask)

    # Map ALL European ME land pixels through Stage 1 + Stage 3
    eu_ys, eu_xs = np.nonzero(eu_land_me)
    vx_f, vy_f = reg.map_points(eu_xs, eu_ys, use_residual=True)
    ix_v = np.rint(vx_f).astype(np.int32)
    iy_v = np.rint(vy_f).astype(np.int32)
    Hv, Wv = van["height"], van["width"]
    in_v = (ix_v >= 0) & (iy_v >= 0) & (ix_v < Wv) & (iy_v < Hv)

    v_sids = np.zeros(len(eu_xs), dtype=np.int32)
    v_pids = np.zeros(len(eu_xs), dtype=np.int32)
    v_sids[in_v] = np.where(van["land_mask"][iy_v[in_v], ix_v[in_v]], van["state_raster"][iy_v[in_v], ix_v[in_v]], 0)
    v_pids[in_v] = np.where(van["land_mask"][iy_v[in_v], ix_v[in_v]], van["prov_raster"][iy_v[in_v], ix_v[in_v]], 0)

    m_sids = me["state_raster"][eu_ys, eu_xs]
    m_pids = me["prov_raster"][eu_ys, eu_xs]

    # Normalized owner lookup
    m_norm_own = {sid: ME_OWNER_TO_VANILLA.get(st["owner36"], st["owner36"]) for sid, st in me["states"].items()}
    v_own = {sid: st["owner36"] for sid, st in van["states"].items()}

    # Fast nearest-land trees for micro-island / coastal-sliver fallback
    (global_tree, global_sids, global_pids), owner_trees, state_trees = _build_vanilla_land_trees(van)
    tag_names = _load_tag_names(mod_dir, vanilla_root)
    border_scripts = _scan_vanilla_border_scripts(vanilla_root)

    # ---------------- Layer 1: Province Crosswalk
    # Count (me_prov, v_state) and (me_prov, v_prov)
    ps_k = m_pids.astype(np.int64) * 100000 + v_sids.astype(np.int64)
    ups_k, ups_c = np.unique(ps_k, return_counts=True)
    prov_tot = defaultdict(int)
    prov_v_cnts = defaultdict(lambda: defaultdict(int))
    for k, c in zip(ups_k, ups_c):
        mp, vs, c = int(k // 100000), int(k % 100000), int(c)
        prov_tot[mp] += c
        if vs > 0:
            prov_v_cnts[mp][vs] += c

    pp_k = m_pids.astype(np.int64) * 100000 + v_pids.astype(np.int64)
    upp_k, upp_c = np.unique(pp_k, return_counts=True)
    prov_vp_cnts = defaultdict(lambda: defaultdict(int))
    for k, c in zip(upp_k, upp_c):
        mp, vp, c = int(k // 100000), int(k % 100000), int(c)
        if vp > 0:
            prov_vp_cnts[mp][vp] += c

    m_p2s = {p: sid for sid, st in me["states"].items() for p in st["provinces"]}
    v_p2s = {p: sid for sid, st in van["states"].items() for p in st["provinces"]}

    prov_m2v_rows = []
    provs_by_v = defaultdict(list)
    me_prov_in_v_by_state = defaultdict(lambda: defaultdict(int))  # (ms, vs) -> # of ME provs

    for mp in sorted(prov_tot):
        ms = m_p2s.get(mp, 0)
        if not ms or ms in excl_sids:
            continue
        tot_px = prov_tot[mp]
        m_own = me["states"][ms]["owner36"]
        n_own = m_norm_own[ms]
        all_vc = prov_v_cnts[mp]
        same_vc = {vs: c for vs, c in all_vc.items() if v_own.get(vs) == n_own}

        geo_method = "direct_raster"
        nearest_d = 0.0
        if same_vc:
            best_vs, best_c = max(same_vc.items(), key=lambda x: (x[1], -x[0]))
            same_tot = sum(same_vc.values())
            same_sh = best_c / same_tot
            all_sh = best_c / tot_px
            # Pick modal vanilla prov inside best_vs
            vp_in_vs = {vp: c for vp, c in prov_vp_cnts[mp].items() if v_p2s.get(vp) == best_vs}
            best_vp = max(vp_in_vs.items(), key=lambda x: (x[1], -x[0]))[0] if vp_in_vs else 0
        else:
            # Fallback: warp province centroid and check nearest same-owner vanilla land
            cx, cy, _ = me["prov_centroids"][mp]
            vx_c, vy_c = reg.map_points(np.array([cx]), np.array([cy]), use_residual=True)
            pt_v = np.array([vx_c[0], vy_c[0]], dtype=np.float64)
            best_vs = 0
            best_vp = 0
            same_sh = 0.0
            all_sh = 0.0
            if n_own in owner_trees:
                ot, osids, opids = owner_trees[n_own]
                d, idx = ot.query(pt_v)
                if d <= 45.0:
                    best_vs = int(osids[idx])
                    best_vp = int(opids[idx])
                    nearest_d = float(d)
                    geo_method = "nearest_same_owner_land"
            if best_vs == 0 and all_vc:
                best_vs, best_c = max(all_vc.items(), key=lambda x: (x[1], -x[0]))
                all_sh = best_c / tot_px
                vp_in_vs = {vp: c for vp, c in prov_vp_cnts[mp].items() if v_p2s.get(vp) == best_vs}
                best_vp = max(vp_in_vs.items(), key=lambda x: (x[1], -x[0]))[0] if vp_in_vs else 0
                geo_method = "cross_owner_raster"
            if best_vs == 0:
                d, idx = global_tree.query(pt_v)
                best_vs = int(global_sids[idx])
                best_vp = int(global_pids[idx])
                nearest_d = float(d)
                geo_method = "nearest_any_land"

        if geo_method != "direct_raster":
            p_cls = "fallback"
        elif same_sh >= 0.95:
            p_cls = "pure_95"
        elif same_sh >= 0.80:
            p_cls = "clean_80"
        elif same_sh >= 0.60:
            p_cls = "majority"
        else:
            p_cls = "straddler"

        all_str = "|".join(
            f"{vs}:{c/tot_px:.3f}" for vs, c in sorted(all_vc.items(), key=lambda x: (-x[1], x[0]))[:4]
        )
        prov_m2v_rows.append({
            "me_prov": mp,
            "me_state": ms,
            "me_state_name": me["states"][ms]["name"],
            "me_owner": m_own,
            "norm_owner": n_own,
            "me_px": tot_px,
            "v_state": best_vs,
            "v_state_name": van["states"][best_vs]["name"] if best_vs in van["states"] else "",
            "v_prov": best_vp,
            "v_state_share": _fmt_ratio(all_sh),
            "same_owner_share": _fmt_ratio(same_sh),
            "purity_class": p_cls,
            "geo_method": geo_method,
            "nearest_dist_px": f"{nearest_d:.2f}",
            "all_v_states": all_str,
        })
        provs_by_v[best_vs].append(mp)
        me_prov_in_v_by_state[ms][best_vs] += 1

    # ---------------- Forward & Reverse State Pixel Census
    sv_k = m_sids.astype(np.int64) * 10000 + v_sids.astype(np.int64)
    usv_k, usv_c = np.unique(sv_k, return_counts=True)
    st_tot_px = defaultdict(int)
    st_v_px = defaultdict(lambda: defaultdict(int))
    for k, c in zip(usv_k, usv_c):
        ms, vs, c = int(k // 10000), int(k % 10000), int(c)
        st_tot_px[ms] += c
        if vs > 0:
            st_v_px[ms][vs] += c

    # Unique vanilla pixels covered per (v_state, me_state) for exact reverse coverage!
    hit_m = v_sids > 0
    v_lin = iy_v[hit_m].astype(np.int64) * Wv + ix_v[hit_m].astype(np.int64)
    # Unique (me_state, v_lin) pairs -> unique vanilla pixels hit by each ME state
    # Pack (me_state, v_lin): v_lin < 5632*2048 = 11.53M < 2^24
    # Use structured unique on 2 int32 columns for exactness!
    m_hit_s = m_sids[hit_m]
    v_hit_s = v_sids[hit_m]
    # Pack: (m_hit_s << 24) | v_lin fits comfortably in int64 (3000 << 24 = 5e10 << 9e18)
    packed_mv = (m_hit_s.astype(np.int64) << 24) | v_lin
    uniq_mv = np.unique(packed_mv)
    u_ms = (uniq_mv >> 24).astype(np.int32)
    u_vlin = (uniq_mv & ((1 << 24) - 1)).astype(np.int64)
    u_vs = van["state_raster"].ravel()[u_vlin]
    # Count unique vanilla pixels per (ms, vs)
    rk = u_ms.astype(np.int64) * 10000 + u_vs.astype(np.int64)
    urk, urc = np.unique(rk, return_counts=True)
    rev_unique_px = {}
    for k, c in zip(urk, urc):
        rev_unique_px[(int(k // 10000), int(k % 10000))] = int(c)

    # Also unique vanilla pixels covered by ANY ME state per vanilla state
    uniq_vlin = np.unique(v_lin)
    v_cov_sids = van["state_raster"].ravel()[uniq_vlin]
    v_cov_uniq_id, v_cov_cnt = np.unique(v_cov_sids, return_counts=True)
    v_total_covered_px = {int(s): int(c) for s, c in zip(v_cov_uniq_id, v_cov_cnt) if s > 0}

    # Native vanilla state areas
    v_native_px = {sid: info[2] for sid, info in van["state_centroids"].items()}

    # ---------------- Build Historical Signatures (using ONLY auto-validated clean_cores!)
    def _hist_sig(st: dict, is_me: bool):
        o36 = ME_OWNER_TO_VANILLA.get(st["owner36"], st["owner36"]) if is_me else st["owner36"]
        o39 = ME_OWNER_TO_VANILLA.get(st["owner39"], st["owner39"]) if is_me else st["owner39"]
        cs = tuple(c for c in clean_core_tags if c in st["cores36"])
        return (o36, o39, st["dmz36"], cs)

    v_eu_sids = {vs for vs in v_total_covered_px if v_total_covered_px[vs] >= 1}
    # Add any vanilla state referenced by a fallback province
    v_eu_sids |= set(provs_by_v.keys())
    v_eu_sids.discard(0)

    v_by_sig = defaultdict(list)
    for vs in sorted(v_eu_sids):
        if vs in van["states"]:
            v_by_sig[_hist_sig(van["states"][vs], False)].append(vs)

    # Build spatially-verified VP lookup: (norm_name, v_state) -> (vx, vy, pts)
    v_owner_state_count = Counter(v_own.get(vs, "") for vs in v_eu_sids if vs in van["states"])
    v_max_vp_pts = {
        vs: max((pts for _, pts in van["states"][vs]["vps"]), default=0)
        for vs in v_eu_sids if vs in van["states"]
    }
    v_vps_by_norm = defaultdict(list)
    for vs in sorted(v_eu_sids):
        if vs not in van["states"]:
            continue
        for vp_id, pts in van["states"][vs]["vps"]:
            nm = _norm(van["vp_loc"].get(f"VICTORY_POINTS_{vp_id}", ""))
            if nm and vp_id in van["prov_centroids"]:
                vx_p, vy_p, _ = van["prov_centroids"][vp_id]
                v_vps_by_norm[nm].append((vs, vp_id, pts, vx_p, vy_p))

    # Pre-check spatial VP matches for every European ME state (within 15 vanilla px & same normalized owner!)
    me_spatial_vp_matches = defaultdict(list)  # ms -> [(vs, vp_name, v_pts, dist_px)]
    for ms, st in sorted(me["states"].items()):
        if ms in excl_sids:
            continue
        n_own = m_norm_own[ms]
        for mp_id, m_pts in st["vps"]:
            raw_nm = me["vp_loc"].get(f"VICTORY_POINTS_{mp_id}", "")
            nm = _norm(raw_nm)
            nm = _EXONYMS.get(nm, nm)
            if not nm or nm not in v_vps_by_norm or mp_id not in me["prov_centroids"]:
                continue
            mx_p, my_p, _ = me["prov_centroids"][mp_id]
            wvx, wvy = reg.map_points(np.array([mx_p]), np.array([my_p]), use_residual=True)
            for vs, vp_id, v_pts, vx_p, vy_p in v_vps_by_norm[nm]:
                if v_own.get(vs) != n_own:
                    continue
                d = math.hypot(float(wvx[0]) - vx_p, float(wvy[0]) - vy_p)
                if d <= 15.0:
                    me_spatial_vp_matches[ms].append((vs, raw_nm, v_pts, d))

    # ---------------- Layer 3a: Strict Disjoint Partition (2,961 European ME states)
    partition_v = {}       # ms -> chosen v_id
    partition_info = {}    # ms -> dict
    adjudication_rows = []

    for ms, st in sorted(me["states"].items()):
        if ms in excl_sids:
            continue
        m_own = st["owner36"]
        n_own = m_norm_own[ms]
        tot_px = st_tot_px.get(ms, 0)
        all_vc = st_v_px.get(ms, {})
        same_vc = {vs: c for vs, c in all_vc.items() if v_own.get(vs) == n_own}
        same_tot = sum(same_vc.values())

        geo_method = "direct_raster"
        nearest_d = 0.0
        norm_owner_match = 1

        # Candidate list sorted by (-px, vid)
        if same_vc:
            ranked = sorted(same_vc.items(), key=lambda x: (-x[1], x[0]))
            denom = same_tot
        else:
            cx, cy, _ = me["state_centroids"].get(ms, (0.0, 0.0, 0))
            wvx, wvy = reg.map_points(np.array([cx]), np.array([cy]), use_residual=True)
            pt_v = np.array([wvx[0], wvy[0]], dtype=np.float64)
            ranked = []
            if n_own in owner_trees:
                ot, osids, _ = owner_trees[n_own]
                d, idx = ot.query(pt_v)
                if d <= 45.0:
                    ranked = [(int(osids[idx]), 1)]
                    denom = 1
                    nearest_d = float(d)
                    geo_method = "nearest_same_owner_land"
            if not ranked and all_vc:
                ranked = sorted(all_vc.items(), key=lambda x: (-x[1], x[0]))
                denom = sum(all_vc.values())
                geo_method = "cross_owner_raster"
                norm_owner_match = 0
            if not ranked:
                d, idx = global_tree.query(pt_v)
                ranked = [(int(global_sids[idx]), 1)]
                denom = 1
                nearest_d = float(d)
                geo_method = "nearest_any_land"
                norm_owner_match = 1 if v_own.get(ranked[0][0]) == n_own else 0

        v1, c1 = ranked[0]
        s1 = c1 / denom
        v2, c2 = ranked[1] if len(ranked) > 1 else (0, 0)
        s2 = c2 / denom if v2 else 0.0
        margin = s1 - s2

        # Historical signature check
        msig = _hist_sig(st, True)
        sig_cands = v_by_sig.get(msig, [])
        chosen_v = v1
        chosen_sh = s1
        runner_v = v2
        runner_sh = s2
        resolution = "area_modal"
        tier = "high_interior"
        sub_cores = [c for c in sorted(st["cores36"]) if c != m_own and c != n_own]
        island_confirmed = False

        if geo_method != "direct_raster":
            tier = "fallback_island"
            resolution = geo_method
            # Check if island assignment is indisputable (single-state owner or regional core name match)
            if geo_method == "nearest_same_owner_land":
                if v_owner_state_count.get(n_own, 0) <= 1:
                    island_confirmed = True
                    resolution = "island_sole_owner_state"
                elif chosen_v in van["states"]:
                    v_toks = _tokens(van["states"][chosen_v]["name"])
                    for c in sub_cores:
                        if _tokens(tag_names.get(c, "")) & v_toks:
                            island_confirmed = True
                            resolution = "island_core_confirmed"
                            break
        elif s1 >= 0.80:
            # Overwhelming geometric interior (>=80% physically inside v1):
            # geometry is authoritative; never let a slightly broader ME core override it.
            tier = "high_core_locked" if (len(sig_cands) == 1 and sig_cands[0] == v1) else "high_interior"
            resolution = "area_interior_core_verified" if tier == "high_core_locked" else "area_interior"
        elif len(sig_cands) == 1 and sig_cands[0] in same_vc and (same_vc[sig_cands[0]] / denom) >= 0.35 and s1 < 0.70:
            # Border straddler whose verified historical signature uniquely locks one overlapping Vanilla state
            chosen_v = sig_cands[0]
            chosen_sh = same_vc[chosen_v] / denom
            if chosen_v != v1:
                runner_v, runner_sh = v1, s1
            tier = "high_core_locked"
            resolution = "core_signature_lock"
        else:
            # Straddler / majority zone (< 0.80): check clean non-ambiguous VP & name signals first for s1 < 0.65
            elig = [(vs, c / denom) for vs, c in ranked if (c / denom) >= 0.20]
            elig_map = dict(elig)
            resolved_tiebreak = False

            if s1 < 0.65 and len(elig) >= 2:
                # T1: Historical signature tie-break among top-2
                hist_match_1 = v1 in sig_cands
                hist_match_2 = v2 in sig_cands if v2 else False
                if v2 and margin < 0.20 and s2 >= 0.15 and (hist_match_1 != hist_match_2):
                    if hist_match_2:
                        chosen_v, chosen_sh = v2, s2
                        runner_v, runner_sh = v1, s1
                    resolution = "straddler_hist_tiebreak"
                    tier = "medium_straddler"
                    resolved_tiebreak = True

                # T2: Spatially-verified exact Vanilla VP match among eligible (>=20%) candidates.
                # Require either tight straddler (s1 < 0.60) OR matched VP is the primary (>=5 pts) VP of that Vanilla state.
                if not resolved_tiebreak:
                    vp_hits = [
                        m for m in me_spatial_vp_matches.get(ms, [])
                        if m[0] in elig_map
                        and (s1 < 0.60 or (m[2] >= 5 and m[2] >= v_max_vp_pts.get(m[0], 0)))
                    ]
                    if vp_hits and len({m[0] for m in vp_hits}) == 1:
                        tgt = vp_hits[0][0]
                        chosen_v, chosen_sh = tgt, elig_map[tgt]
                        if chosen_v != v1:
                            runner_v, runner_sh = v1, s1
                        resolution = "straddler_vp_tiebreak"
                        tier = "medium_straddler"
                        resolved_tiebreak = True

                # T3: Exact state-name or primary-VP-name token match uniquely identifying one eligible candidate
                if not resolved_tiebreak:
                    m_toks = _tokens(st["name"])
                    for mp_id, m_pts in sorted(st["vps"], key=lambda x: -x[1])[:1]:
                        if m_pts >= 5 or len(st["vps"]) == 1:
                            m_toks |= _tokens(me["vp_loc"].get(f"VICTORY_POINTS_{mp_id}", ""))
                    name_hits = [
                        vs for vs, _ in elig
                        if vs in van["states"] and (m_toks & _tokens(van["states"][vs]["name"]))
                    ]
                    if len(name_hits) == 1:
                        tgt = name_hits[0]
                        chosen_v, chosen_sh = tgt, elig_map[tgt]
                        if chosen_v != v1:
                            runner_v, runner_sh = v1, s1
                        resolution = "straddler_name_tiebreak"
                        tier = "medium_straddler"
                        resolved_tiebreak = True

                # T4: ME sub-regional core name uniquely confirms v1 (confirm-only, NEVER overrides v1 -> v2,
                # and rejects shared Vanilla cores like SLV present on both v1 and v2!)
                if not resolved_tiebreak and s1 < 0.60:
                    v2_cores = van["states"].get(v2, {}).get("cores36", set()) if v2 else set()
                    core_hits = set()
                    for vs, _ in elig:
                        if vs not in van["states"]:
                            continue
                        v_toks = _tokens(van["states"][vs]["name"])
                        for c in sub_cores:
                            if c in v2_cores:
                                continue
                            if _tokens(tag_names.get(c, "")) & v_toks:
                                core_hits.add(vs)
                    if len(core_hits) == 1 and v1 in core_hits:
                        resolution = "straddler_core_confirmed"
                        tier = "medium_straddler"
                        resolved_tiebreak = True

            if not resolved_tiebreak:
                if s1 >= 0.60 and margin >= 0.20:
                    tier = "high_majority"
                    resolution = "area_majority"
                else:
                    resolution = "straddler_area_modal"
                    tier = "medium_straddler" if (s1 >= 0.55 and margin >= 0.15) else "low_straddler"

        # Apply approved human adjudications (guarded to require same normalized 1936 owner)
        adjudicated = False
        if ms in ADJUDICATED_STRADDLER_OVERRIDES:
            ovr_v, ovr_reason = ADJUDICATED_STRADDLER_OVERRIDES[ms]
            if v_own.get(ovr_v) == n_own:
                prev_v = chosen_v
                if ovr_v != chosen_v:
                    runner_v, runner_sh = chosen_v, chosen_sh
                chosen_v = ovr_v
                chosen_sh = same_vc.get(ovr_v, 0) / denom if same_vc else 1.0
                tier = "adjudicated"
                resolution = "adjudicated_override"
                adjudicated = True
                adjudication_rows.append({
                    "me_id": ms,
                    "me_name": st["name"],
                    "me_owner": m_own,
                    "action": "override",
                    "chosen_v_id": chosen_v,
                    "chosen_v_name": van["states"][chosen_v]["name"] if chosen_v in van["states"] else "",
                    "prev_auto_v_id": prev_v,
                    "prev_auto_v_name": van["states"][prev_v]["name"] if prev_v in van["states"] else "",
                    "chosen_share": _fmt_ratio(chosen_sh),
                    "reason": ovr_reason,
                })
        elif ms in ADJUDICATED_CONFIRMED_A:
            tier = "adjudicated"
            resolution = "adjudicated_confirmed"
            adjudicated = True
            adjudication_rows.append({
                "me_id": ms,
                "me_name": st["name"],
                "me_owner": m_own,
                "action": "confirm_a",
                "chosen_v_id": chosen_v,
                "chosen_v_name": van["states"][chosen_v]["name"] if chosen_v in van["states"] else "",
                "prev_auto_v_id": chosen_v,
                "prev_auto_v_name": van["states"][chosen_v]["name"] if chosen_v in van["states"] else "",
                "chosen_share": _fmt_ratio(chosen_sh),
                "reason": "Human-approved confirmation of candidate A (eu_mapping_adjudication.md)",
            })

        # Review flags
        rev_reasons = []
        if not adjudicated:
            if tier == "low_straddler":
                rev_reasons.append("tight_straddler")
            if geo_method != "direct_raster" and nearest_d > 5.0 and not island_confirmed:
                rev_reasons.append(f"distant_island_{nearest_d:.1f}px")
            if norm_owner_match == 0:
                rev_reasons.append("cross_owner_fallback")

        partition_v[ms] = chosen_v
        partition_info[ms] = {
            "v_id": chosen_v,
            "same_owner_share": chosen_sh,
            "all_share": all_vc.get(chosen_v, 0) / max(tot_px, 1),
            "runner_v_id": runner_v,
            "runner_share": runner_sh,
            "margin": chosen_sh - runner_sh,
            "tier": tier,
            "resolution": resolution,
            "geo_method": geo_method,
            "nearest_dist_px": nearest_d,
            "norm_owner_match": norm_owner_match,
            "rev_reasons": rev_reasons,
            "core_sig_match": 1 if chosen_v in sig_cands else 0,
        }

    # Graph-outlier sanity check (using 4-neighbor adjacency of ME states & Vanilla states)
    me_adj = _state_adjacency(me["state_raster"], eu_land_me)
    van_adj = _state_adjacency(van["state_raster"], van["land_mask"])
    graph_outliers = 0
    for ms, info in partition_info.items():
        n_own = m_norm_own[ms]
        same_nbs = [nb for nb in me_adj.get(ms, ()) if nb in partition_v and m_norm_own.get(nb) == n_own]
        if len(same_nbs) >= 2:
            nb_vs = {partition_v[nb] for nb in same_nbs}
            d_hop = _graph_dist_capped(van_adj, info["v_id"], nb_vs, max_hops=3)
            if d_hop >= 3:
                info["rev_reasons"].append(f"graph_outlier_hop{d_hop}")
                graph_outliers += 1

    # ---------------- Minimal Aligned Blocks (bipartite connected components)
    # Edges: same-owner overlap with me_same_owner_share >= 0.15 UNION chosen partition parent
    bip_v_to_m = defaultdict(set)
    bip_m_to_v = defaultdict(set)
    for ms, info in partition_info.items():
        v_p = info["v_id"]
        bip_v_to_m[v_p].add(ms)
        bip_m_to_v[ms].add(v_p)
        n_own = m_norm_own[ms]
        same_vc = {vs: c for vs, c in st_v_px.get(ms, {}).items() if v_own.get(vs) == n_own}
        st_sum = sum(same_vc.values())
        if st_sum > 0:
            for vs, c in same_vc.items():
                if (c / st_sum) >= 0.15:
                    bip_v_to_m[vs].add(ms)
                    bip_m_to_v[ms].add(vs)

    seen_m = set()
    seen_v = set()
    blocks = []
    m_block = {}
    v_block = {}
    for ms0 in sorted(partition_info):
        if ms0 in seen_m:
            continue
        comp_m = set()
        comp_v = set()
        qm = deque([ms0])
        seen_m.add(ms0)
        while qm:
            u = qm.popleft()
            comp_m.add(u)
            for v in bip_m_to_v[u]:
                if v not in seen_v:
                    seen_v.add(v)
                    comp_v.add(v)
                    for m2 in bip_v_to_m[v]:
                        if m2 not in seen_m:
                            seen_m.add(m2)
                            qm.append(m2)
        blocks.append((sorted(comp_v), sorted(comp_m)))

    blocks.sort(key=lambda x: (x[0][0] if x[0] else 99999, x[1][0]))
    block_rows = []
    for bid, (vs_list, ms_list) in enumerate(blocks, start=1):
        for v in vs_list:
            v_block[v] = bid
        for m in ms_list:
            m_block[m] = bid
        vs_set = set(vs_list)
        # Forward union purity over same-owner pixels in block
        fwd_in = sum(c for m in ms_list for vs, c in st_v_px.get(m, {}).items() if vs in vs_set)
        fwd_tot = sum(st_tot_px.get(m, 0) for m in ms_list)
        # Reverse union coverage over vanilla pixels in block
        rev_hit = sum(v_total_covered_px.get(v, 0) for v in vs_list)
        rev_tot = sum(v_native_px.get(v, 0) for v in vs_list)
        owners_blk = sorted({v_own.get(v, "") for v in vs_list if v_own.get(v)})
        if len(vs_list) == 1 and len(ms_list) == 1:
            btype = "1:1"
        elif len(vs_list) == 1:
            btype = "1:many"
        else:
            btype = "many:many"
        block_rows.append({
            "block_id": bid,
            "owners": "|".join(owners_blk),
            "block_type": btype,
            "v_count": len(vs_list),
            "me_count": len(ms_list),
            "vanilla_ids": "|".join(str(v) for v in vs_list),
            "vanilla_names": "|".join(van["states"][v]["name"] for v in vs_list if v in van["states"]),
            "me_ids": "|".join(str(m) for m in ms_list),
            "union_forward_purity": _fmt_ratio(fwd_in / max(fwd_tot, 1)),
            "union_reverse_coverage": _fmt_ratio(min(1.0, rev_hit / max(rev_tot, 1))),
        })

    # ---------------- Layer 2: Weighted State Overlap Matrix
    overlap_rows = []
    for ms in sorted(partition_info):
        info = partition_info[ms]
        tot_px = st_tot_px.get(ms, 0)
        all_vc = st_v_px.get(ms, {})
        n_own = m_norm_own[ms]
        same_tot = sum(c for vs, c in all_vc.items() if v_own.get(vs) == n_own)
        cand_vs = set(all_vc.keys()) | {info["v_id"]}
        for vs in sorted(cand_vs):
            px = all_vc.get(vs, 0)
            me_sh = px / max(tot_px, 1)
            is_same = 1 if v_own.get(vs) == n_own else 0
            me_same_sh = (px / same_tot) if (is_same and same_tot > 0) else 0.0
            v_uniq = rev_unique_px.get((ms, vs), 0)
            v_nat = v_native_px.get(vs, 0)
            v_sh = v_uniq / max(v_nat, 1)
            is_parent = 1 if vs == info["v_id"] else 0
            if not is_parent and me_sh < 0.01 and v_sh < 0.01:
                continue
            ref_sh = me_same_sh if is_same else me_sh
            if ref_sh >= 0.80:
                role = "interior"
            elif ref_sh >= 0.50:
                role = "majority"
            elif ref_sh >= 0.15:
                role = "straddler"
            elif ref_sh >= 0.05:
                role = "minor"
            else:
                role = "sliver"
            msig = _hist_sig(me["states"][ms], True)
            overlap_rows.append({
                "vanilla_id": vs,
                "vanilla_name": van["states"][vs]["name"] if vs in van["states"] else "",
                "vanilla_owner": v_own.get(vs, ""),
                "me_id": ms,
                "me_name": me["states"][ms]["name"],
                "me_owner": me["states"][ms]["owner36"],
                "norm_owner_match": is_same,
                "me_px": px,
                "me_total_px": tot_px,
                "me_share": _fmt_ratio(me_sh),
                "me_same_owner_share": _fmt_ratio(me_same_sh) if is_same else "",
                "v_unique_px": v_uniq,
                "v_native_px": v_nat,
                "v_share": _fmt_ratio(v_sh),
                "prov_count_in_v": me_prov_in_v_by_state[ms].get(vs, 0),
                "edge_role": role,
                "core_sig_match": 1 if vs in v_by_sig.get(msig, []) else 0,
                "is_partition_parent": is_parent,
                "block_id": m_block.get(ms, 0),
            })
    overlap_rows.sort(key=lambda r: (r["vanilla_id"], r["me_id"]))

    # ---------------- Layer 4: Primary City / Capital / Building Identity (1 -> 1)
    members_by_v = defaultdict(list)
    for ms, vid in sorted(partition_v.items()):
        members_by_v[vid].append(ms)

    # Shared European 1936 LIVE SOVEREIGN national capitals: v_cap_state -> (me_cap_state, tag)
    # Only live 1936 owning countries (excludes releasable/postwar tags like WGR whose vanilla
    # capital=64 Berlin differs from ME's capital=469 Bonn!)
    live_v_owners = {st["owner36"] for st in van["states"].values() if st["owner36"]}
    live_m_owners = {st["owner36"] for sid, st in me["states"].items() if sid not in excl_sids and st["owner36"]}
    live_shared_owners = live_v_owners & live_m_owners
    v_cap_to_me = {}
    for tag in sorted(live_shared_owners):
        if tag in van["capitals"] and tag in me["capitals"]:
            vc = van["capitals"][tag]
            mc = me["capitals"][tag]
            if mc not in excl_sids:
                v_cap_to_me[vc] = (mc, tag)

    # Index spatial VP matches per vanilla state
    v_spatial_vps = defaultdict(list)  # vs -> [(v_pts, -dist, ms, raw_nm, dist)]
    for ms, matches in me_spatial_vp_matches.items():
        for vs, raw_nm, v_pts, dist in matches:
            v_spatial_vps[vs].append((v_pts, -dist, ms, raw_nm, dist))

    # Index same-owner overlap states per vanilla state (for edge/narrow states with 0 partition members)
    v_overlap_same = defaultdict(list)  # vs -> [(v_uniq_px, me_px, ms)]
    for ms, info in partition_info.items():
        n_own = m_norm_own[ms]
        for vs, px in st_v_px.get(ms, {}).items():
            if v_own.get(vs) == n_own and px > 0:
                v_overlap_same[vs].append((rev_unique_px.get((ms, vs), 0), px, ms))

    primary_city_rows = []
    primary_me_of_v = {}
    all_mapped_vs = sorted(set(members_by_v.keys()) | set(provs_by_v.keys()))
    all_mapped_vs = [v for v in all_mapped_vs if v in van["states"]]

    for vs in all_mapped_vs:
        mems = members_by_v.get(vs, [])
        v_st = van["states"][vs]
        p_me = 0
        p_basis = "none"
        vp_nm = ""
        vp_d = None

        # 1. Live 1936 sovereign national capital match (must be inside partition members)
        if vs in v_cap_to_me and v_cap_to_me[vs][0] in mems:
            p_me = v_cap_to_me[vs][0]
            p_basis = f"national_capital:{v_cap_to_me[vs][1]}"
        # 2. Spatially-verified highest VP match (preferring inside partition members)
        elif v_spatial_vps.get(vs):
            cands = sorted(
                v_spatial_vps[vs],
                key=lambda x: (1 if x[2] in mems else 0, x[0], x[1]),
                reverse=True,
            )
            best_vp = cands[0]
            p_me = best_vp[2]
            p_basis = "vp_spatial_match"
            vp_nm = best_vp[3]
            vp_d = best_vp[4]
        # 3. Exact normalized state name inside partition members
        elif mems:
            vn = _norm(v_st["name"])
            exact_m = [m for m in mems if _norm(me["states"][m]["name"]) == vn]
            if exact_m:
                p_me = max(exact_m, key=lambda m: (sum(pts for _, pts in me["states"][m]["vps"]), st_v_px.get(m, {}).get(vs, 0), -m))
                p_basis = "exact_name_in_partition"
            else:
                # 4. Highest VP sum -> highest manpower -> largest overlap px
                p_me = max(
                    mems,
                    key=lambda m: (
                        sum(pts for _, pts in me["states"][m]["vps"]),
                        me["states"][m]["manpower"],
                        st_v_px.get(m, {}).get(vs, 0),
                        -m,
                    ),
                )
                p_basis = "highest_vp_or_manpower_member"
        # 5. Narrow coastal / map-edge vanilla states with 0 partition members (e.g. Derna, Sistan)
        elif v_overlap_same.get(vs):
            best_ov = max(v_overlap_same[vs], key=lambda x: (x[0], x[1], -x[2]))
            p_me = best_ov[2]
            p_basis = "edge_overlap_member"

        primary_me_of_v[vs] = p_me
        primary_city_rows.append({
            "vanilla_id": vs,
            "vanilla_name": v_st["name"],
            "vanilla_owner": v_st["owner36"],
            "primary_me_id": p_me if p_me else "",
            "primary_me_name": me["states"][p_me]["name"] if p_me in me["states"] else "",
            "primary_basis": p_basis,
            "vp_matched_name": vp_nm,
            "vp_dist_px": f"{vp_d:.2f}" if vp_d is not None else "",
            "in_partition_members": 1 if (p_me and p_me in mems) else 0,
            "me_member_count": len(mems),
        })

    # ---------------- Legacy Audit (vs docs/states/m2v_states.tsv)
    legacy_m2v = _load_legacy_m2v(docs_root)
    legacy_audit_rows = []
    legacy_diff_by_me = {}

    for ms in sorted(me["states"]):
        leg = legacy_m2v.get(ms, {})
        lv = leg.get("v_id")
        lb = leg.get("basis", "")
        if ms in excl_sids:
            legacy_diff_by_me[ms] = "excluded_offmap"
            if lv:
                legacy_audit_rows.append({
                    "me_id": ms, "me_name": me["states"][ms]["name"], "me_owner": me["states"][ms]["owner36"],
                    "new_v_id": "", "new_v_name": "", "new_share": "",
                    "legacy_v_id": lv, "legacy_v_name": leg.get("v_name", ""), "legacy_share": "",
                    "dist_to_legacy_px": "", "legacy_basis": lb, "diff_class": "excluded_offmap",
                })
            continue

        info = partition_info[ms]
        nv = info["v_id"]
        if lv is None:
            legacy_diff_by_me[ms] = "legacy_missing"
            continue
        if lv == nv:
            legacy_diff_by_me[ms] = "agree"
            continue

        tot_px = max(st_tot_px.get(ms, 0), 1)
        n_sh = info["same_owner_share"]
        l_sh = st_v_px.get(ms, {}).get(lv, 0) / tot_px

        # Compute exact distance in vanilla px from ME state warped centroid to legacy vanilla state!
        cx, cy, _ = me["state_centroids"].get(ms, (0.0, 0.0, 0))
        wvx, wvy = reg.map_points(np.array([cx]), np.array([cy]), use_residual=True)
        pt_v = np.array([wvx[0], wvy[0]], dtype=np.float64)
        if lv in state_trees:
            ldist = float(state_trees[lv].query(pt_v)[0])
        elif lv in van["state_centroids"]:
            vcx, vcy, _ = van["state_centroids"][lv]
            ldist = math.hypot(pt_v[0] - vcx, pt_v[1] - vcy)
        else:
            ldist = 9999.0

        # Check if ME state name or VP name matches legacy_v (e.g., Amasya case)
        m_nm = _norm(me["states"][ms]["name"])
        lv_nm = _norm(van["states"][lv]["name"]) if lv in van["states"] else ""
        lv_vp_nms = {_norm(van["vp_loc"].get(f"VICTORY_POINTS_{p}", "")) for p, _ in van["states"].get(lv, {}).get("vps", [])}
        m_vp_nms = {_norm(me["vp_loc"].get(f"VICTORY_POINTS_{p}", "")) for p, _ in me["states"][ms]["vps"]}
        name_or_vp_hit = bool((m_nm and m_nm == lv_nm) or (m_vp_nms & lv_vp_nms - {""}))

        n_own = m_norm_own[ms]
        l_own = v_own.get(lv, "")
        if l_own and l_own != n_own:
            dcls = "legacy_wrong_owner"
        elif l_sh < 0.01 and ldist > 3.0:
            dcls = "city_vs_area_split" if (name_or_vp_hit and ldist <= 25.0) else "legacy_distant_error"
        elif l_sh >= 0.05:
            dcls = "straddler_difference"
        else:
            dcls = "border_touch_difference"

        legacy_diff_by_me[ms] = dcls
        legacy_audit_rows.append({
            "me_id": ms,
            "me_name": me["states"][ms]["name"],
            "me_owner": me["states"][ms]["owner36"],
            "new_v_id": nv,
            "new_v_name": van["states"][nv]["name"] if nv in van["states"] else "",
            "new_share": _fmt_ratio(n_sh),
            "legacy_v_id": lv,
            "legacy_v_name": van["states"][lv]["name"] if lv in van["states"] else leg.get("v_name", ""),
            "legacy_share": _fmt_ratio(l_sh),
            "dist_to_legacy_px": f"{ldist:.1f}",
            "legacy_basis": lb,
            "diff_class": dcls,
        })

    # Sort legacy audit: worst errors first (distant errors, wrong owner, city_vs_area, straddlers)
    dcls_rank = {
        "legacy_distant_error": 0, "legacy_wrong_owner": 1, "city_vs_area_split": 2,
        "straddler_difference": 3, "border_touch_difference": 4, "excluded_offmap": 5,
    }
    legacy_audit_rows.sort(key=lambda r: (dcls_rank.get(r["diff_class"], 9), -float(r["dist_to_legacy_px"] or 0), r["me_id"]))

    # ---------------- Build Auxiliary Decision Clues (VP city landings, regional cores, Vanilla border script diffs)
    me_aux: dict[int, dict] = {}
    for ms, info in partition_info.items():
        st = me["states"][ms]
        m_own = st["owner36"]
        n_own = m_norm_own[ms]
        vid = info["v_id"]
        rv = info["runner_v_id"]

        # 1. ME VP cities & which Vanilla state each VP physically lands inside
        vps_out = []
        for mp_id, pts in sorted(st["vps"], key=lambda x: (-x[1], x[0])):
            vname = me["vp_loc"].get(f"VICTORY_POINTS_{mp_id}", f"#{mp_id}")
            if mp_id in me["prov_centroids"]:
                mx_p, my_p, _ = me["prov_centroids"][mp_id]
                wvx, wvy = reg.map_points(np.array([mx_p]), np.array([my_p]), use_residual=True)
                ivx, ivy = int(round(float(wvx[0]))), int(round(float(wvy[0])))
                lvs = int(van["state_raster"][ivy, ivx]) if (0 <= ivx < Wv and 0 <= ivy < Hv and van["land_mask"][ivy, ivx]) else 0
                vps_out.append([vname, int(pts), lvs, round(float(mx_p), 1), round(float(my_p), 1)])
            else:
                vps_out.append([vname, int(pts), 0, 0.0, 0.0])

        # 2. Sub-regional ME cores with human-readable English names
        sub_cores = [
            [c, tag_names.get(c, c)]
            for c in sorted(st["cores36"])
            if c != m_own and c != n_own
        ]

        # 3. Vanilla 1.19 border-change script divergence (A vs B)
        s_a = border_scripts.get(vid, set())
        s_b = border_scripts.get(rv, set()) if rv else set()
        diff_a = sorted(s_a - s_b) if rv else sorted(s_a)
        diff_b = sorted(s_b - s_a) if rv else []
        if info["geo_method"] != "direct_raster":
            impact = "island"
            diff_cnt = len(s_a)
        else:
            diff_cnt = len(diff_a) + len(diff_b)
            impact = "high" if diff_cnt > 0 else "low"

        # 4. Synthesized human advisory from 4 signals (area, VP landing, legacy, script diff)
        leg_v = legacy_m2v.get(ms, {}).get("v_id")
        top_vp_v = vps_out[0][2] if vps_out else 0
        if impact == "island":
            adv = "🏝️ 离岸岛屿（无直接陆地重叠）：结合海图位置与国家归属确认"
        elif impact == "low":
            if (not top_vp_v or top_vp_v == vid) and (leg_v == vid):
                adv = "🟢 原版领土国策无分歧，且面积/主城/旧表一致 → 直接确认 A"
            else:
                adv = "🟢 原版领土/核心/宣称国策从未拆开 A 与 B → 放心按面积确认 A"
        else:
            if top_vp_v and top_vp_v == vid and leg_v == vid:
                adv = "✅ 面积 + 主城落点 + 旧表三信号均指 A（有国策分歧，推荐确认 A）"
            elif top_vp_v and rv and top_vp_v == rv:
                adv = "⚖️ 面积偏 A，但主城 VP 落在 B → 看下方红标国策归属与河流拍板"
            elif top_vp_v and top_vp_v != vid:
                adv = "⚖️ 面积偏 A，但主城 VP 落在第三州 → 看地图城市标点与国策分歧"
            elif leg_v == vid:
                adv = "🟡 面积与旧表均指 A（有国策分歧，对照河流/城市确认）"
            else:
                adv = "⚖️ 边界骑墙且旧表指 B → 对照地图河流、周边城市与红标国策拍板"

        me_aux[ms] = {
            "vps": vps_out,
            "cores": sub_cores,
            "impact": impact,
            "diff_cnt": diff_cnt,
            "diff_a": diff_a[:8],
            "diff_b": diff_b[:8],
            "advisory": adv,
        }

    # ---------------- Assemble Layer 3 TSVs + Compact Review Queue
    m2v_rows = []
    review_queue_rows = []
    for ms in sorted(me["states"]):
        st = me["states"][ms]
        leg = legacy_m2v.get(ms, {})
        if ms in excl_sids:
            m2v_rows.append({
                "me_id": ms, "me_name": st["name"], "me_owner": st["owner36"], "norm_owner": "",
                "status": me["excluded_states"][ms], "v_id": "", "v_name": "", "v_owner": "",
                "same_owner_share": "", "all_share": "", "runner_v_id": "", "runner_share": "",
                "margin": "", "confidence_tier": "excluded", "resolution": me["excluded_states"][ms],
                "geo_method": "excluded", "nearest_dist_px": "", "core_sig_match": "",
                "block_id": "", "needs_review": 0, "review_reason": "",
                "legacy_v_id": leg.get("v_id") or "", "legacy_diff": "excluded_offmap",
            })
            continue
        info = partition_info[ms]
        aux = me_aux[ms]
        vid = info["v_id"]
        needs_rev = 1 if info["rev_reasons"] else 0
        row = {
            "me_id": ms,
            "me_name": st["name"],
            "me_owner": st["owner36"],
            "norm_owner": m_norm_own[ms],
            "status": "mapped",
            "v_id": vid,
            "v_name": van["states"][vid]["name"] if vid in van["states"] else "",
            "v_owner": v_own.get(vid, ""),
            "same_owner_share": _fmt_ratio(info["same_owner_share"]),
            "all_share": _fmt_ratio(info["all_share"]),
            "runner_v_id": info["runner_v_id"] if info["runner_v_id"] else "",
            "runner_share": _fmt_ratio(info["runner_share"]) if info["runner_v_id"] else "",
            "margin": _fmt_ratio(info["margin"]),
            "confidence_tier": info["tier"],
            "resolution": info["resolution"],
            "geo_method": info["geo_method"],
            "nearest_dist_px": f"{info['nearest_dist_px']:.2f}",
            "core_sig_match": info["core_sig_match"],
            "block_id": m_block.get(ms, ""),
            "needs_review": needs_rev,
            "review_reason": "|".join(info["rev_reasons"]),
            "legacy_v_id": leg.get("v_id") or "",
            "legacy_diff": legacy_diff_by_me.get(ms, ""),
        }
        m2v_rows.append(row)
        if needs_rev:
            rq_row = dict(row)
            rq_row["gameplay_impact"] = aux["impact"]
            rq_row["border_script_diff_count"] = aux["diff_cnt"]
            rq_row["only_a_scripts"] = "|".join(aux["diff_a"])
            rq_row["only_b_scripts"] = "|".join(aux["diff_b"])
            rq_row["me_vps"] = "|".join(f"{v[0]}({v[1]})->v{v[2]}" for v in aux["vps"])
            rq_row["me_sub_cores"] = "|".join(f"{c[0]}:{c[1]}" for c in aux["cores"])
            rq_row["advisory"] = aux["advisory"]
            review_queue_rows.append(rq_row)

    # Sort review queue: high gameplay impact first (largest script diff & tightest share), then islands, then low-impact
    imp_rank = {"high": 0, "island": 1, "low": 2}
    review_queue_rows.sort(
        key=lambda r: (
            imp_rank.get(r["gameplay_impact"], 9),
            -int(r["border_script_diff_count"] or 0),
            float(r["same_owner_share"] or 0.0),
            r["me_id"],
        )
    )

    # Layer 3b: state_partition_v2m.tsv
    v2m_rows = []
    for vs in all_mapped_vs:
        mems = members_by_v.get(vs, [])
        p_me = primary_me_of_v.get(vs, 0)
        # Order members: primary first, then numeric order
        ordered = ([p_me] if p_me in mems else []) + sorted(m for m in mems if m != p_me)
        interiors = [m for m in ordered if partition_info[m]["tier"] in ("high_interior", "high_core_locked")]
        straddlers = [m for m in ordered if m not in interiors]
        v_nat = v_native_px.get(vs, 0)
        v_cov = v_total_covered_px.get(vs, 0)
        has_rev = 1 if any(partition_info[m]["rev_reasons"] for m in ordered) else 0
        v2m_rows.append({
            "vanilla_id": vs,
            "vanilla_name": van["states"][vs]["name"],
            "vanilla_owner": v_own.get(vs, ""),
            "me_count": len(ordered),
            "primary_me_id": p_me if p_me else "",
            "primary_me_name": me["states"][p_me]["name"] if p_me in me["states"] else "",
            "me_members": "|".join(str(m) for m in ordered),
            "interior_members": "|".join(str(m) for m in interiors),
            "straddler_members": "|".join(str(m) for m in straddlers),
            "v_native_px": v_nat,
            "v_covered_px": v_cov,
            "reverse_coverage": _fmt_ratio(min(1.0, v_cov / max(v_nat, 1))),
            "block_id": v_block.get(vs, ""),
            "has_review_member": has_rev,
        })

    # Layer 1b: prov_v2m.tsv
    prov_v2m_rows = []
    for vs in all_mapped_vs:
        mps = sorted(provs_by_v.get(vs, []))
        v_nat = v_native_px.get(vs, 0)
        v_cov = v_total_covered_px.get(vs, 0)
        prov_v2m_rows.append({
            "vanilla_id": vs,
            "vanilla_name": van["states"][vs]["name"],
            "vanilla_owner": v_own.get(vs, ""),
            "me_prov_count": len(mps),
            "v_native_px": v_nat,
            "v_covered_px": v_cov,
            "reverse_coverage": _fmt_ratio(min(1.0, v_cov / max(v_nat, 1))),
            "me_provs_ordered": "|".join(str(p) for p in mps),
        })

    # Write all TSVs
    _write_tsv(out_path / "prov_m2v.tsv", [
        "me_prov", "me_state", "me_state_name", "me_owner", "norm_owner", "me_px",
        "v_state", "v_state_name", "v_prov", "v_state_share", "same_owner_share",
        "purity_class", "geo_method", "nearest_dist_px", "all_v_states",
    ], prov_m2v_rows)
    _write_tsv(out_path / "prov_v2m.tsv", [
        "vanilla_id", "vanilla_name", "vanilla_owner", "me_prov_count",
        "v_native_px", "v_covered_px", "reverse_coverage", "me_provs_ordered",
    ], prov_v2m_rows)
    _write_tsv(out_path / "state_overlap.tsv", [
        "vanilla_id", "vanilla_name", "vanilla_owner", "me_id", "me_name", "me_owner",
        "norm_owner_match", "me_px", "me_total_px", "me_share", "me_same_owner_share",
        "v_unique_px", "v_native_px", "v_share", "prov_count_in_v",
        "edge_role", "core_sig_match", "is_partition_parent", "block_id",
    ], overlap_rows)
    _write_tsv(out_path / "state_partition_m2v.tsv", [
        "me_id", "me_name", "me_owner", "norm_owner", "status", "v_id", "v_name", "v_owner",
        "same_owner_share", "all_share", "runner_v_id", "runner_share", "margin",
        "confidence_tier", "resolution", "geo_method", "nearest_dist_px",
        "core_sig_match", "block_id", "needs_review", "review_reason",
        "legacy_v_id", "legacy_diff",
    ], m2v_rows)
    _write_tsv(out_path / "state_partition_v2m.tsv", [
        "vanilla_id", "vanilla_name", "vanilla_owner", "me_count",
        "primary_me_id", "primary_me_name", "me_members", "interior_members",
        "straddler_members", "v_native_px", "v_covered_px", "reverse_coverage",
        "block_id", "has_review_member",
    ], v2m_rows)
    _write_tsv(out_path / "state_aligned_blocks.tsv", [
        "block_id", "owners", "block_type", "v_count", "me_count",
        "vanilla_ids", "vanilla_names", "me_ids",
        "union_forward_purity", "union_reverse_coverage",
    ], block_rows)
    _write_tsv(out_path / "state_primary_city.tsv", [
        "vanilla_id", "vanilla_name", "vanilla_owner", "primary_me_id", "primary_me_name",
        "primary_basis", "vp_matched_name", "vp_dist_px", "in_partition_members", "me_member_count",
    ], primary_city_rows)
    _write_tsv(out_path / "legacy_audit.tsv", [
        "me_id", "me_name", "me_owner", "new_v_id", "new_v_name", "new_share",
        "legacy_v_id", "legacy_v_name", "legacy_share", "dist_to_legacy_px",
        "legacy_basis", "diff_class",
    ], legacy_audit_rows)
    _write_tsv(out_path / "review_queue.tsv", [
        "me_id", "me_name", "me_owner", "norm_owner", "v_id", "v_name",
        "same_owner_share", "runner_v_id", "runner_share", "margin",
        "confidence_tier", "resolution", "geo_method", "nearest_dist_px",
        "block_id", "review_reason", "legacy_v_id", "legacy_diff",
        "gameplay_impact", "border_script_diff_count", "only_a_scripts",
        "only_b_scripts", "me_vps", "me_sub_cores", "advisory",
    ], review_queue_rows)
    adjudication_rows.sort(key=lambda r: (0 if r["action"] == "override" else 1, r["me_id"]))
    _write_tsv(out_path / "adjudications.tsv", [
        "me_id", "me_name", "me_owner", "action", "chosen_v_id", "chosen_v_name",
        "prev_auto_v_id", "prev_auto_v_name", "chosen_share", "reason",
    ], adjudication_rows)

    # Summary JSON
    s3_bench = reg.report["benchmarks"].get("stage3_diffeomorphic") or reg.report["benchmarks"]["stage1_3landmass"]
    tier_counts = Counter(r["confidence_tier"] for r in m2v_rows if r["status"] == "mapped")
    res_counts = Counter(r["resolution"] for r in m2v_rows if r["status"] == "mapped")
    prov_cls_counts = Counter(r["purity_class"] for r in prov_m2v_rows)
    leg_cls_counts = Counter(r["diff_class"] for r in legacy_audit_rows)
    blk_type_counts = Counter(r["block_type"] for r in block_rows)
    prim_basis_counts = Counter(r["primary_basis"].split(":")[0] for r in primary_city_rows)
    rq_imp_counts = Counter(r["gameplay_impact"] for r in review_queue_rows)

    summary = {
        "schema": "eu_mapping_summary/v1",
        "scope": "European / MENA main map only (NA inset, Congo/Indonesia proxies, and MAP borders excluded)",
        "total_me_states": len(me["states"]),
        "eu_me_states": len(partition_info),
        "excluded_me_states": len(excl_sids),
        "eu_vanilla_states": len(all_mapped_vs),
        "eu_me_provinces": len(prov_m2v_rows),
        "overlap_edges": len(overlap_rows),
        "aligned_blocks_total": len(block_rows),
        "aligned_blocks_by_type": dict(sorted(blk_type_counts.items())),
        "state_confidence_tiers": dict(sorted(tier_counts.items())),
        "state_resolutions": dict(sorted(res_counts.items())),
        "province_purity_classes": dict(sorted(prov_cls_counts.items())),
        "primary_city_basis": dict(sorted(prim_basis_counts.items())),
        "graph_outliers_flagged": graph_outliers,
        "adjudicated_count": len(adjudication_rows),
        "adjudicated_overrides": sum(1 for r in adjudication_rows if r["action"] == "override"),
        "review_queue_count": len(review_queue_rows),
        "review_queue_by_impact": dict(sorted(rq_imp_counts.items())),
        "legacy_diff_counts": dict(sorted(leg_cls_counts.items())),
        "legacy_distant_or_owner_errors": leg_cls_counts["legacy_distant_error"] + leg_cls_counts["legacy_wrong_owner"],
        "registration_same_owner_acc": s3_bench["same_1936_owner_px_acc"],
        "registration_capitals_hit": s3_bench["national_capitals_hit"],
        "registration_diffeomorphic_ok": bool(reg.report["stage3_residual_field"]["diffeomorphic_ok"]) if reg.report["stage3_residual_field"] else True,
        "registration_min_jacobian_det": reg.report["stage3_residual_field"]["min_jacobian_det_on_land"] if reg.report["stage3_residual_field"] else 1.0,
    }
    (out_path / "mapping_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if build_review:
        review_html = build_review_page(
            reg, out_path, m2v_rows, v2m_rows, overlap_rows, legacy_audit_rows, summary,
            mod_dir=mod_dir, me_aux=me_aux, tag_names=tag_names,
        )
        summary["review_html"] = str(review_html)

    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build definitive 4-layer European Vanilla <-> ME mapping tables.")
    parser.add_argument("--vanilla", default=DEFAULT_VANILLA)
    parser.add_argument("--mod", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-review", action="store_true")
    args = parser.parse_args(argv)

    summary = build_all(args.vanilla, args.mod, args.output_dir, build_review=not args.no_review)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
