#!/usr/bin/env python
"""Definitive European-scope ME -> Vanilla map registration engine.

Candidate-independent, non-circular geometric registration between Magna Europa
(4096x3072) and Vanilla HOI4 (5632x2048), strictly scoped to the European /
MENA main map (explicitly excluding the top-left North America inset, the
off-map Congo/Indonesia proxy boxes, and synthetic MAP border states).

Zero circularity: no Victory Points, state names, migration comments, or legacy
mapping tables enter any geometric transform.

Pipeline:
  1. Fast vectorized rasterization of provinces.bmp, definition.csv,
     rivers.bmp, heightmap.bmp, plus history/states (1936/1939 owners,
     undated cores, DMZ, VPs, manpower) and 1936 country capitals.
  2. Explicit European scope filter (25 off-map/inset/synthetic ME states
     excluded cleanly) and normalization of the 20 on-map ME-only 1936 owner
     tags to their true Vanilla 1.19 sovereigns.
  3. Stage 1 — Physical Landmass Decoupling:
       - Eurasia + North Africa mainland (+ non-shifted islands): fitted from
         unique normalized 1936 triple junctions (country/coast/lake) with
         Huber IRLS, refined by labeled mutual-NN boundary ICP.
       - North Atlantic / British Isles cluster (Great Britain, Ireland, Man,
         Hebrides, Orkney, Shetland, Faroes, Jan Mayen): dedicated affine
         warm-started by coarse shift search + coast/border mutual-NN ICP.
       - Iceland: dedicated affine warm-started by bbox + coast mutual-NN ICP.
  4. Stage 2 — Automatic Geometric Validation of Shared Releasable Core Tags
     & 1936-1939 Historical Boundary Snapshots:
       - Validates every shared releasable core tag by IoU under Stage 1,
         automatically accepting high-IoU tags (IoU >= 0.72) and rejecting
         repurposed/divergent tags (e.g., BRN, TNE, SAM, SIC, SIL, MOL).
  5. Stage 3 — Bounded Diffeomorphic Smooth Residual Field (Mainland):
       - Sparse bilinear lattice displacement field (grid = 24 vanilla px)
         solved with Laplacian + ridge regularization and Huber IRLS over 4
         non-circular physical/topological cues:
           (a) 1936 + verified clean-core topological triple junctions (2D)
           (b) Labeled country/coast/lake/high-IoU-core boundaries (normal)
           (c) River skeleton mutual-NN correspondences (normal)
           (d) High-confidence inland (heightmap + river) NCC patch shifts (2D)
       - Analytic Jacobian guard enforces det(I + grad D) >= 0.55 and
         |D| <= 14.0 vanilla px everywhere on land (never folds).

Dependencies: numpy, scipy, Pillow, stdlib only (no OpenCV required).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import sparse
from scipy.ndimage import (
    binary_erosion,
    distance_transform_edt,
    find_objects,
    gaussian_filter,
    label,
)
from scipy.sparse import linalg as spla
from scipy.spatial import cKDTree

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import mod_root, repo_root  # noqa: E402

DEFAULT_VANILLA = (
    os.environ.get("HOI4_DIR")
    or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
)

# 20 on-map ME-only 1936 owners -> their exact Vanilla 1.19 sovereigns.
# (MAP and COG are excluded off-map/synthetic, handled separately.)
ME_OWNER_TO_VANILLA = {
    "ADR": "FRA",  # Andorra -> France/Spain Pyrenees border
    "ALW": "SYR",  # Alawite -> Syria (SYR owns 554/677/680 in vanilla 1.19)
    "BHR": "ENG",  # Bahrain -> UK (vanilla Gulf states)
    "DNZ": "POL",  # Danzig -> Poland (v85 owned by POL in 1936)
    "EGY": "ENG",  # Egypt -> UK (v446/447/907 owned by ENG in 1936)
    "GUE": "ENG",  # Guernsey -> UK
    "ISM": "ENG",  # Isle of Man -> UK (v932 owned by ENG in 1936)
    "JDZ": "SYR",  # Jabal al-Druze -> Syria (v554)
    "JER": "ENG",  # Jersey -> UK
    "LIE": "SWI",  # Liechtenstein -> Swiss/Austrian border
    "MLT": "ENG",  # Malta -> UK (v116 owned by ENG)
    "MNC": "FRA",  # Monaco -> France (v735)
    "MOR": "FRA",  # Morocco -> France (v461/462 owned by FRA)
    "MRT": "FRA",  # Mauritania -> France
    "QAT": "ENG",  # Qatar -> UK (v765 owned by ENG)
    "SMR": "ITA",  # San Marino -> Italy
    "TNG": "SPR",  # Tangier -> Spanish Africa (v290 owned by SPR)
    "TUN": "FRA",  # Tunisia -> France (v458/665 owned by FRA)
    "UAE": "ENG",  # Trucial States/UAE -> UK (v658 owned by ENG)
    "VAT": "ITA",  # Vatican -> Italy (v2)
}

# Explicitly excluded off-map / inset / synthetic ME owners & state IDs
EXCLUDED_OWNERS = frozenset({"MAP", "COG", "USA", "CAN"})
EXCLUDED_EXPLICIT_STATE_IDS = frozenset({
    64,    # Newfoundland (ENG, top-left NA inset)
    2920,  # St. Pierre et Miquelon (FRA, top-left NA inset)
    2974,  # Congo (COG, bottom-left off-map proxy)
    2975,  # Indonesia (HOL, bottom-right off-map proxy)
    2552,  # American Border (MAP, synthetic border)
    2986,  # Map Border (MAP, synthetic border)
})

# Vanilla Europe/MENA bounding box in vanilla provinces.bmp pixel coords
VANILLA_EU_BBOX = (2360, 0, 3840, 1080)  # x0, y0, x1, y1

# Stage 3 diffeomorphic lattice hyperparameters
RESIDUAL_GRID_STEP = 24     # vanilla px per lattice cell
RESIDUAL_MAX_MAG_PX = 14.0  # hard cap on displacement magnitude
RESIDUAL_MIN_JAC = 0.55     # hard lower bound on det(I + grad D) on land


# ------------------------------------------------------------------ parsing

def _strip_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", "", text)


def _grab_block(src: str, key: str) -> str:
    m = re.search(r"\b" + re.escape(key) + r"\s*=\s*\{", src)
    if not m:
        return ""
    depth = 0
    start = m.end()
    for i in range(m.end() - 1, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i]
    return src[start:]


def _split_top_and_dated(hist: str):
    """Split a state history block into (undated_top_text, {date_tuple: text})."""
    top = []
    dated = {}
    i = 0
    n = len(hist)
    date_re = re.compile(r"\s*(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?\s*=\s*\{")
    while i < n:
        m = date_re.match(hist, i)
        if m:
            dt = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))
            j = m.end() - 1
            depth = 0
            while j < n:
                if hist[j] == "{":
                    depth += 1
                elif hist[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            dated[dt] = dated.get(dt, "") + "\n" + hist[m.end():j]
            i = j + 1
        else:
            top.append(hist[i])
            i += 1
    return "".join(top), dated


def parse_localisation(root: str):
    state_loc = {}
    vp_loc = {}
    for path in sorted(glob.glob(os.path.join(root, "localisation", "english", "*.yml"))):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                m = re.match(r'\s*((?:STATE|VICTORY_POINTS)_\d+)(?::\d+)?\s*[=:]?\s*"([^"]*)"', line)
                if not m:
                    continue
                k, v = m.group(1), m.group(2).replace(r"\t", "").replace(r"\n", "").strip()
                if k.startswith("STATE_"):
                    state_loc.setdefault(k, v)
                else:
                    vp_loc.setdefault(k, v)
    return state_loc, vp_loc


def load_states_metadata(root: str, state_loc: dict):
    """Parse history/states/*.txt with 1936 & 1939 owners, cores, DMZ, VPs."""
    states = {}
    paths = sorted(glob.glob(os.path.join(root, "history", "states", "*.txt")))
    if not paths:
        raise FileNotFoundError(f"No history/states/*.txt under {root}")
    for path in paths:
        bn = os.path.basename(path)
        m_id = re.match(r"^(\d+)\s*-", bn)
        if not m_id:
            continue
        sid = int(m_id.group(1))
        src = _strip_comments(open(path, encoding="utf-8-sig", errors="replace").read())
        provs = [int(x) for x in re.findall(r"\d+", _grab_block(src, "provinces"))]
        hist = _grab_block(src, "history")
        top_s, dated = _split_top_and_dated(hist)

        m_own = re.search(r"\bowner\s*=\s*([A-Z0-9]{3})", top_s)
        owner36 = m_own.group(1) if m_own else ""
        # Apply any dated block <= 1936.1.1
        for dt in sorted(dated):
            if dt[:3] <= (1936, 1, 1):
                mo = re.search(r"\bowner\s*=\s*([A-Z0-9]{3})", dated[dt])
                if mo:
                    owner36 = mo.group(1)
        owner39 = owner36
        for dt in sorted(dated):
            if dt[:3] <= (1939, 8, 14):
                mo = re.search(r"\bowner\s*=\s*([A-Z0-9]{3})", dated[dt])
                if mo:
                    owner39 = mo.group(1)

        cores36 = set(re.findall(r"\badd_core_of\s*=\s*([A-Z0-9]{3})", top_s))
        dmz36 = bool(re.search(r"\bset_demilitarized_zone\s*=\s*yes", top_s))
        impassable = bool(re.search(r"\bimpassable\s*=\s*yes", src))
        m_mp = re.search(r"\bmanpower\s*=\s*(\d+)", src)
        manpower = int(m_mp.group(1)) if m_mp else 0

        vps = []
        for vm in re.finditer(r"victory_points\s*=\s*\{([^}]*)\}", top_s):
            vals = [int(x) for x in re.findall(r"\d+", vm.group(1))]
            for k in range(0, len(vals) - 1, 2):
                if vals[k] > 0 and vals[k + 1] > 0:
                    vps.append((vals[k], vals[k + 1]))

        name = state_loc.get(f"STATE_{sid}") or bn.split("-", 1)[1].rsplit(".", 1)[0].strip()
        states[sid] = {
            "id": sid,
            "file": bn,
            "name": name,
            "provinces": sorted(set(provs)),
            "owner36": owner36,
            "owner39": owner39,
            "cores36": cores36,
            "dmz36": dmz36,
            "impassable": impassable,
            "manpower": manpower,
            "vps": vps,
        }
    return states


def load_capitals(root: str) -> dict[str, int]:
    """Parse 1936 country capital state IDs from history/countries/*.txt."""
    caps = {}
    for path in sorted(glob.glob(os.path.join(root, "history", "countries", "*.txt"))):
        tag = os.path.basename(path).split("-", 1)[0].strip()
        if len(tag) != 3:
            continue
        txt = _strip_comments(open(path, encoding="utf-8-sig", errors="replace").read())
        # Avoid reading capital inside a later dated block
        top_s, _ = _split_top_and_dated(txt)
        m = re.search(r"\bcapital\s*=\s*(\d+)", top_s)
        if m:
            caps[tag] = int(m.group(1))
    return caps


def load_strategic_regions(root: str) -> dict[int, set[int]]:
    regs = {}
    for path in sorted(glob.glob(os.path.join(root, "map", "strategicregions", "*.txt"))):
        bn = os.path.basename(path)
        m = re.match(r"^(\d+)\s*-", bn)
        if not m:
            continue
        rid = int(m.group(1))
        src = _strip_comments(open(path, encoding="utf-8-sig", errors="replace").read())
        regs[rid] = set(int(x) for x in re.findall(r"\d+", _grab_block(src, "provinces")))
    return regs


def load_map_side(root: str, is_me: bool = False):
    """Fast vectorized loader for one HOI4 map side."""
    map_dir = os.path.join(root, "map")
    def_path = os.path.join(map_dir, "definition.csv")
    prov_bmp = os.path.join(map_dir, "provinces.bmp")
    hm_bmp = os.path.join(map_dir, "heightmap.bmp")
    riv_bmp = os.path.join(map_dir, "rivers.bmp")

    # 1. definition.csv -> sorted color lookup arrays + type masks
    col_list = []
    pid_list = []
    land_ids, sea_ids, lake_ids = [], [], []
    with open(def_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if not row or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            if pid <= 0:
                continue
            r, g, b = int(row[1]), int(row[2]), int(row[3])
            col_list.append((r << 16) | (g << 8) | b)
            pid_list.append(pid)
            typ = row[4].strip().lower()
            if typ == "land":
                land_ids.append(pid)
            elif typ == "sea":
                sea_ids.append(pid)
            elif typ == "lake":
                lake_ids.append(pid)

    cols_arr = np.asarray(col_list, dtype=np.int32)
    pids_arr = np.asarray(pid_list, dtype=np.int32)
    order = np.argsort(cols_arr)
    cols_sorted = cols_arr[order]
    pids_sorted = pids_arr[order]

    # 2. Vectorized provinces.bmp -> int32 province raster
    im = np.asarray(Image.open(prov_bmp).convert("RGB"), dtype=np.int32)
    h, w, _ = im.shape
    packed = ((im[..., 0] << 16) | (im[..., 1] << 8) | im[..., 2]).ravel()
    idx = np.searchsorted(cols_sorted, packed)
    idx_clip = np.minimum(idx, len(cols_sorted) - 1)
    matched = cols_sorted[idx_clip] == packed
    prov_flat = np.where(matched, pids_sorted[idx_clip], 0).astype(np.int32)
    prov_raster = prov_flat.reshape(h, w)

    max_pid = max(int(pids_arr.max()), int(prov_raster.max()), 1)
    type_lut = np.zeros(max_pid + 1, dtype=np.int8)  # 1=land, 2=sea, 3=lake
    type_lut[np.asarray(land_ids, dtype=np.int32)] = 1
    type_lut[np.asarray(sea_ids, dtype=np.int32)] = 2
    type_lut[np.asarray(lake_ids, dtype=np.int32)] = 3
    type_raster = type_lut[prov_raster]
    land_mask = type_raster == 1
    sea_mask = type_raster == 2
    lake_mask = type_raster == 3

    # 3. States, localisation, capitals
    state_loc, vp_loc = parse_localisation(root)
    states = load_states_metadata(root, state_loc)
    capitals = load_capitals(root)

    p2s_lut = np.zeros(max_pid + 1, dtype=np.int32)
    for sid, st in states.items():
        for p in st["provinces"]:
            if 0 < p <= max_pid:
                p2s_lut[p] = sid
    state_raster = p2s_lut[prov_raster]

    # 4. Physical rasters (heightmap & rivers)
    heightmap = np.asarray(Image.open(hm_bmp), dtype=np.float32)
    rivers_raw = np.asarray(Image.open(riv_bmp))
    river_mask = (rivers_raw <= 11) & land_mask

    # 5. Province centroids & areas (vectorized bincount!)
    ys_all, xs_all = np.nonzero(land_mask & (prov_raster > 0))
    pf = prov_raster[ys_all, xs_all]
    p_cnt = np.bincount(pf, minlength=max_pid + 1)
    p_sx = np.bincount(pf, weights=xs_all, minlength=max_pid + 1)
    p_sy = np.bincount(pf, weights=ys_all, minlength=max_pid + 1)
    prov_centroids = {}
    for p in np.nonzero(p_cnt)[0]:
        p = int(p)
        prov_centroids[p] = (float(p_sx[p] / p_cnt[p]), float(p_sy[p] / p_cnt[p]), int(p_cnt[p]))

    # State centroids & areas
    max_sid = max(states) if states else 1
    sf = state_raster[ys_all, xs_all]
    s_cnt = np.bincount(sf, minlength=max_sid + 1)
    s_sx = np.bincount(sf, weights=xs_all, minlength=max_sid + 1)
    s_sy = np.bincount(sf, weights=ys_all, minlength=max_sid + 1)
    state_centroids = {}
    for s in np.nonzero(s_cnt)[0]:
        s = int(s)
        state_centroids[s] = (float(s_sx[s] / s_cnt[s]), float(s_sy[s] / s_cnt[s]), int(s_cnt[s]))

    # 6. Identify excluded off-map / inset / synthetic states on ME side
    excluded_states = {}
    if is_me:
        for sid, st in sorted(states.items()):
            own = st["owner36"]
            cx, cy, _ = state_centroids.get(sid, (0.0, 0.0, 0))
            if own == "MAP" or sid in (2552, 2986):
                excluded_states[sid] = "excluded_synthetic_border"
            elif own == "COG" or sid in (2974, 2975):
                excluded_states[sid] = "excluded_offmap_proxy"
            elif own in ("USA", "CAN") or sid in (64, 2920) or (cx < 500 and cy < 380):
                excluded_states[sid] = "excluded_na_inset"
            elif cx < 350 and cy > 2500 and own not in ("SPR", "POR", "MOR", "MRT", "FRA"):
                excluded_states[sid] = "excluded_offmap_box"

    return {
        "root": root,
        "is_me": is_me,
        "width": w,
        "height": h,
        "prov_raster": prov_raster,
        "state_raster": state_raster,
        "land_mask": land_mask,
        "sea_mask": sea_mask,
        "lake_mask": lake_mask,
        "heightmap": heightmap,
        "river_mask": river_mask,
        "states": states,
        "state_loc": state_loc,
        "vp_loc": vp_loc,
        "capitals": capitals,
        "prov_centroids": prov_centroids,
        "state_centroids": state_centroids,
        "excluded_states": excluded_states,
    }


# ----------------------------------------------------------- geometry helpers

def _apply_affine(A: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ A[:, :2].T + A[:, 2]


def _fit_affine_irls(src: np.ndarray, dst: np.ndarray, weights=None, iters=8, huber_delta=2.5):
    """Weighted Huber-IRLS 2x3 affine fit (pure numpy, deterministic)."""
    n = len(src)
    if n < 3:
        raise ValueError(f"Need >= 3 points for affine fit, got {n}")
    X = np.column_stack([src, np.ones(n, dtype=np.float64)])
    w = np.ones(n, dtype=np.float64) if weights is None else np.asarray(weights, dtype=np.float64).copy()
    A = np.zeros((2, 3), dtype=np.float64)
    for _ in range(iters):
        sw = np.sqrt(np.maximum(w, 1e-12))
        Xw = X * sw[:, None]
        Yw = dst * sw[:, None]
        sol, *_ = np.linalg.lstsq(Xw, Yw, rcond=None)
        A = sol.T  # (2, 3)
        resid = np.linalg.norm(X @ sol - dst, axis=1)
        # Adaptive Huber threshold anchored on MAD
        med = float(np.median(resid))
        scale = max(huber_delta, 1.4826 * med * 2.0)
        huber = np.where(resid <= scale, 1.0, scale / np.maximum(resid, 1e-9))
        base_w = np.ones(n, dtype=np.float64) if weights is None else np.asarray(weights, dtype=np.float64)
        w = base_w * huber
    return A


def _build_unified_label_raster(side: dict, tag2id: dict, remap=None, excluded_sids=None):
    """int32 raster: -2=sea, -3=lake, 0=unowned/excluded, >=1=unified owner id."""
    h, w = side["land_mask"].shape
    out = np.zeros((h, w), dtype=np.int32)
    out[side["sea_mask"]] = -2
    out[side["lake_mask"]] = -3
    excl = excluded_sids or set()
    max_sid = max(side["states"]) if side["states"] else 1
    lut = np.zeros(max_sid + 1, dtype=np.int32)
    for sid, st in side["states"].items():
        if sid in excl:
            continue
        o = st["owner36"]
        if remap:
            o = remap.get(o, o)
        lut[sid] = tag2id.get(o, 0)
    sr = side["state_raster"]
    m = side["land_mask"] & (sr > 0)
    out[m] = lut[sr[m]]
    return out


def extract_triple_junctions(u: np.ndarray, valid_mask=None, radius=2, margin=12, cluster_dist=25.0):
    """Find unique 3-label topological junctions (label_a, label_b, label_c) -> (x, y)."""
    h, w = u.shape
    shifts = [u[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    stack = np.stack(shifts, axis=0)
    stack.sort(axis=0)
    n_uniq = 1 + np.sum(stack[1:] != stack[:-1], axis=0)
    has_zero = (stack == 0).any(axis=0)
    cand = (n_uniq >= 3) & (~has_zero)
    cand[:margin, :] = cand[-margin:, :] = cand[:, :margin] = cand[:, -margin:] = False
    if valid_mask is not None:
        cand &= valid_mask[1:-1, 1:-1]
    cy, cx = np.nonzero(cand)
    cy += 1
    cx += 1
    by_triple = defaultdict(list)
    for y, x in zip(cy, cx):
        y0, y1 = max(0, y - radius), min(h, y + radius + 1)
        x0, x1 = max(0, x - radius), min(w, x + radius + 1)
        patch = u[y0:y1, x0:x1]
        vals = tuple(sorted(int(v) for v in np.unique(patch) if v != 0))
        if len(vals) == 3:
            by_triple[vals].append((float(x), float(y)))

    junctions = {}
    for triple, pts in by_triple.items():
        arr = np.asarray(pts, dtype=np.float64)
        # Spatial clustering: keep only triples that occur at a SINGLE geographic point
        clusters = []
        for p in arr:
            placed = False
            for cl in clusters:
                if math.hypot(p[0] - cl[0][0], p[1] - cl[0][1]) < cluster_dist:
                    cl.append(p)
                    placed = True
                    break
            if not placed:
                clusters.append([p])
        if len(clusters) == 1:
            c = np.mean(clusters[0], axis=0)
            junctions[triple] = (float(c[0]), float(c[1]))
    return junctions


def extract_labeled_boundaries(u: np.ndarray, valid_mask=None, grid_cell=4, margin=10):
    """Extract {(a, b): Nx2 float64 pts} for every ordered pair a < b (none == 0).

    Includes country|country borders, sea|country coasts, and lake|country shores.
    """
    h, w = u.shape
    pairs_xy = defaultdict(list)

    # Check horizontal and vertical 4-neighbor contacts
    for axis in (0, 1):
        if axis == 0:
            a = u[:-1, :]
            b = u[1:, :]
            diff = (a != b) & (a != 0) & (b != 0)
            if valid_mask is not None:
                diff &= (valid_mask[:-1, :] | valid_mask[1:, :])
            diff[:margin, :] = diff[-margin:, :] = False
            diff[:, :margin] = diff[:, -margin:] = False
            ys, xs = np.nonzero(diff)
            av = a[ys, xs]
            bv = b[ys, xs]
            py = ys.astype(np.float64) + 0.5
            px = xs.astype(np.float64)
        else:
            a = u[:, :-1]
            b = u[:, 1:]
            diff = (a != b) & (a != 0) & (b != 0)
            if valid_mask is not None:
                diff &= (valid_mask[:, :-1] | valid_mask[:, 1:])
            diff[:margin, :] = diff[-margin:, :] = False
            diff[:, :margin] = diff[:, -margin:] = False
            ys, xs = np.nonzero(diff)
            av = a[ys, xs]
            bv = b[ys, xs]
            py = ys.astype(np.float64)
            px = xs.astype(np.float64) + 0.5

        lo = np.minimum(av, bv)
        hi = np.maximum(av, bv)
        # Skip sea|lake
        keep = ~((lo == -3) & (hi == -2))
        lo, hi, px, py = lo[keep], hi[keep], px[keep], py[keep]
        keys = (lo.astype(np.int64) + 1000) * 10000 + (hi.astype(np.int64) + 1000)
        order = np.argsort(keys)
        ks = keys[order]
        pxs = px[order]
        pys = py[order]
        uk, starts = np.unique(ks, return_index=True)
        ends = np.append(starts[1:], len(ks))
        for k, s0, s1 in zip(uk, starts, ends):
            la = int(k // 10000) - 1000
            lb = int(k % 10000) - 1000
            pairs_xy[(la, lb)].append(np.column_stack([pxs[s0:s1], pys[s0:s1]]))

    out = {}
    for pair, chunks in pairs_xy.items():
        pts = np.vstack(chunks)
        if grid_cell > 1 and len(pts) > 0:
            gx = np.floor(pts[:, 0] / grid_cell).astype(np.int64)
            gy = np.floor(pts[:, 1] / grid_cell).astype(np.int64)
            gkey = gy * 100000 + gx
            order = np.argsort(gkey)
            gk = gkey[order]
            ps = pts[order]
            _, st = np.unique(gk, return_index=True)
            cnt = np.diff(np.append(st, len(gk)))
            mx = np.add.reduceat(ps[:, 0], st) / cnt
            my = np.add.reduceat(ps[:, 1], st) / cnt
            pts = np.column_stack([mx, my])
        out[pair] = pts
    return out


def _mutual_nn(src_v: np.ndarray, dst_v: np.ndarray, max_dist: float):
    """Return (src_idx, dst_idx, dists) for mutual nearest neighbors within max_dist."""
    if len(src_v) == 0 or len(dst_v) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    t_dst = cKDTree(dst_v)
    d_sd, i_sd = t_dst.query(src_v, distance_upper_bound=max_dist)
    valid = np.isfinite(d_sd) & (i_sd < len(dst_v))
    if not valid.any():
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    t_src = cKDTree(src_v)
    _, i_ds = t_src.query(dst_v, distance_upper_bound=max_dist)
    src_idx = np.nonzero(valid)[0]
    dst_idx = i_sd[src_idx]
    mut = i_ds[dst_idx] == src_idx
    return src_idx[mut], dst_idx[mut], d_sd[src_idx[mut]]


def _refine_labeled_affine(src_b: dict, dst_b: dict, A_init: np.ndarray, max_dist=12.0, iters=12):
    """Refine 2x3 affine via labeled boundary mutual-NN Huber-IRLS."""
    A = A_init.copy()
    common = sorted(set(src_b) & set(dst_b))
    for _ in range(iters):
        s_all, d_all, w_all = [], [], []
        for pair in common:
            sp = src_b[pair]
            dp = dst_b[pair]
            if len(sp) < 3 or len(dp) < 3:
                continue
            sp_v = _apply_affine(A, sp)
            si, di, dists = _mutual_nn(sp_v, dp, max_dist)
            if len(si) == 0:
                continue
            # Weight country|country borders slightly higher than coasts
            w = 1.5 if (pair[0] > 0 and pair[1] > 0) else 1.0
            s_all.append(sp[si])
            d_all.append(dp[di])
            w_all.append(np.full(len(si), w, dtype=np.float64))
        if not s_all:
            break
        S = np.vstack(s_all)
        D = np.vstack(d_all)
        W = np.concatenate(w_all)
        if len(S) < 12:
            break
        A_new = _fit_affine_irls(S, D, weights=W, iters=4, huber_delta=2.0)
        if np.linalg.det(A_new[:, :2]) <= 0:
            break
        delta = float(np.abs(A_new - A).max())
        A = A_new
        if delta < 1e-4:
            break
    return A


def _mask_coast_points(mask: np.ndarray, grid_cell=1):
    er = binary_erosion(mask, structure=np.ones((3, 3), dtype=bool), border_value=0)
    b = mask & (~er)
    ys, xs = np.nonzero(b)
    pts = np.column_stack([xs, ys]).astype(np.float64)
    if grid_cell > 1 and len(pts):
        gx = np.floor(pts[:, 0] / grid_cell).astype(np.int64)
        gy = np.floor(pts[:, 1] / grid_cell).astype(np.int64)
        gkey = gy * 100000 + gx
        order = np.argsort(gkey)
        gk = gkey[order]
        ps = pts[order]
        _, st = np.unique(gk, return_index=True)
        cnt = np.diff(np.append(st, len(gk)))
        pts = np.column_stack([np.add.reduceat(ps[:, 0], st) / cnt,
                               np.add.reduceat(ps[:, 1], st) / cnt])
    return pts


def _fit_island_cluster_affine(me_mask, van_mask, A_main, search_bbox_init=False, max_dist=18.0):
    """Fit a dedicated affine for a rigidly shifted island cluster (UK/IRE or Iceland)."""
    pm = _mask_coast_points(me_mask, grid_cell=3)
    pv = _mask_coast_points(van_mask, grid_cell=1)
    if search_bbox_init:
        mx0, my0 = pm.min(axis=0)
        mx1, my1 = pm.max(axis=0)
        vx0, vy0 = pv.min(axis=0)
        vx1, vy1 = pv.max(axis=0)
        sx = (vx1 - vx0) / max(mx1 - mx0, 1.0)
        sy = (vy1 - vy0) / max(my1 - my0, 1.0)
        A = np.array([[sx, 0.0, vx0 - sx * mx0], [0.0, sy, vy0 - sy * my0]], dtype=np.float64)
    else:
        # Coarse translation grid search around A_main using distance transform!
        vy_all, vx_all = np.nonzero(van_mask)
        vx0, vx1 = max(0, int(vx_all.min()) - 40), min(van_mask.shape[1], int(vx_all.max()) + 40)
        vy0, vy1 = max(0, int(vy_all.min()) - 40), min(van_mask.shape[0], int(vy_all.max()) + 40)
        sub_v = van_mask[vy0:vy1, vx0:vx1]
        sub_b = sub_v & (~binary_erosion(sub_v, structure=np.ones((3, 3), dtype=bool), border_value=0))
        dt = distance_transform_edt(~sub_b)
        p0 = _apply_affine(A_main, pm)
        best_cost = 1e18
        best_shift = (0.0, 0.0)
        for dy in range(-28, 12, 2):
            for dx in range(-24, 16, 2):
                ix = np.rint(p0[:, 0] + dx - vx0).astype(np.int32)
                iy = np.rint(p0[:, 1] + dy - vy0).astype(np.int32)
                ok = (ix >= 0) & (iy >= 0) & (ix < dt.shape[1]) & (iy < dt.shape[0])
                if ok.mean() < 0.7:
                    continue
                cost = float(np.median(dt[iy[ok], ix[ok]])) + 0.3 * float(np.mean(dt[iy[ok], ix[ok]]))
                if cost < best_cost:
                    best_cost = cost
                    best_shift = (float(dx), float(dy))
        A = A_main.copy()
        A[0, 2] += best_shift[0]
        A[1, 2] += best_shift[1]

    # Mutual-NN Huber-IRLS refinement
    for _ in range(25):
        pred = _apply_affine(A, pm)
        si, di, _ = _mutual_nn(pred, pv, max_dist)
        if len(si) < 12:
            break
        A_new = _fit_affine_irls(pm[si], pv[di], iters=4, huber_delta=1.5)
        if np.linalg.det(A_new[:, :2]) <= 0:
            break
        delta = float(np.abs(A_new - A).max())
        A = A_new
        if delta < 1e-4:
            break

    pred = _apply_affine(A, pm)
    t_v = cKDTree(pv)
    d, _ = t_v.query(pred)
    return A, float(np.median(d)), float(np.percentile(d, 90))


# ---------------------------------------------- Stage 3: diffeomorphic field

class SmoothResidualField:
    """Bounded bilinear lattice displacement field D(vx, vy) -> (dx, dy) in vanilla px."""

    def __init__(self, x0: int, y0: int, x1: int, y1: int, step: int, dx_grid: np.ndarray, dy_grid: np.ndarray, meta: dict):
        self.x0 = int(x0)
        self.y0 = int(y0)
        self.x1 = int(x1)
        self.y1 = int(y1)
        self.step = int(step)
        self.dx = np.asarray(dx_grid, dtype=np.float64)
        self.dy = np.asarray(dy_grid, dtype=np.float64)
        self.ny, self.nx = self.dx.shape
        self.meta = meta

    def eval_xy(self, vx: np.ndarray, vy: np.ndarray):
        """Evaluate bilinear displacement at vanilla coordinates (vx, vy)."""
        vx = np.asarray(vx, dtype=np.float64)
        vy = np.asarray(vy, dtype=np.float64)
        gx = (vx - self.x0) / self.step
        gy = (vy - self.y0) / self.step
        inside = (gx >= 0.0) & (gy >= 0.0) & (gx <= self.nx - 1.000001) & (gy <= self.ny - 1.000001)
        out_dx = np.zeros_like(vx)
        out_dy = np.zeros_like(vy)
        if not inside.any():
            return out_dx, out_dy
        gxi = gx[inside]
        gyi = gy[inside]
        ix = np.floor(gxi).astype(np.int32)
        iy = np.floor(gyi).astype(np.int32)
        fx = gxi - ix
        fy = gyi - iy
        w00 = (1.0 - fx) * (1.0 - fy)
        w10 = fx * (1.0 - fy)
        w01 = (1.0 - fx) * fy
        w11 = fx * fy
        out_dx[inside] = (
            w00 * self.dx[iy, ix]
            + w10 * self.dx[iy, ix + 1]
            + w01 * self.dx[iy + 1, ix]
            + w11 * self.dx[iy + 1, ix + 1]
        )
        out_dy[inside] = (
            w00 * self.dy[iy, ix]
            + w10 * self.dy[iy, ix + 1]
            + w01 * self.dy[iy + 1, ix]
            + w11 * self.dy[iy + 1, ix + 1]
        )
        return out_dx, out_dy

    def jacobian_det_grid(self):
        """Analytic Jacobian det(I + grad D) at cell centers of the lattice."""
        ddx_dx = (self.dx[:, 1:] - self.dx[:, :-1]) / self.step
        ddx_dy = (self.dx[1:, :] - self.dx[:-1, :]) / self.step
        ddy_dx = (self.dy[:, 1:] - self.dy[:, :-1]) / self.step
        ddy_dy = (self.dy[1:, :] - self.dy[:-1, :]) / self.step
        # Cell-center averages
        jx_x = 1.0 + 0.5 * (ddx_dx[:-1, :] + ddx_dx[1:, :])
        jx_y = 0.5 * (ddx_dy[:, :-1] + ddx_dy[:, 1:])
        jy_x = 0.5 * (ddy_dx[:-1, :] + ddy_dx[1:, :])
        jy_y = 1.0 + 0.5 * (ddy_dy[:, :-1] + ddy_dy[:, 1:])
        return jx_x * jy_y - jx_y * jy_x

    def to_dict(self):
        return {
            "bbox_xyxy": [self.x0, self.y0, self.x1, self.y1],
            "step_px": self.step,
            "shape_ny_nx": [self.ny, self.nx],
            "dx": np.round(self.dx, 4).tolist(),
            "dy": np.round(self.dy, 4).tolist(),
            "meta": self.meta,
        }


def _inland_ncc_anchors(me, van, A_main, mainland_mask_me, bbox, patch_half=24, search_pad=10, grid_step=36):
    """Measure local inland displacement via pure-scipy normalized cross-correlation
    on (heightmap + river distance), strictly away from coast/edges."""
    vx0, vy0, vx1, vy1 = bbox
    W, H = vx1 - vx0, vy1 - vy0

    # Sample ME heightmap & river onto vanilla crop under A_main (fast inverse remap!)
    A_inv = np.zeros((2, 3), dtype=np.float64)
    lin_inv = np.linalg.inv(A_main[:, :2])
    A_inv[:, :2] = lin_inv
    A_inv[:, 2] = -lin_inv @ A_main[:, 2]

    gx, gy = np.meshgrid(np.arange(vx0, vx1, dtype=np.float64), np.arange(vy0, vy1, dtype=np.float64))
    mx = np.rint(A_inv[0, 0] * gx + A_inv[0, 1] * gy + A_inv[0, 2]).astype(np.int32)
    my = np.rint(A_inv[1, 0] * gx + A_inv[1, 1] * gy + A_inv[1, 2]).astype(np.int32)
    ok = (mx >= 0) & (my >= 0) & (mx < me["width"]) & (my < me["height"])

    land_m_w = np.zeros((H, W), dtype=bool)
    hm_m_w = np.zeros((H, W), dtype=np.float32)
    riv_m_w = np.zeros((H, W), dtype=bool)
    land_m_w[ok] = mainland_mask_me[my[ok], mx[ok]]
    hm_m_w[ok] = me["heightmap"][my[ok], mx[ok]]
    riv_m_w[ok] = me["river_mask"][my[ok], mx[ok]] & mainland_mask_me[my[ok], mx[ok]]

    land_v_c = van["land_mask"][vy0:vy1, vx0:vx1]
    hm_v_c = van["heightmap"][vy0:vy1, vx0:vx1]
    riv_v_c = van["river_mask"][vy0:vy1, vx0:vx1]

    # Distance from non-land (inland check)
    inland_m = distance_transform_edt(land_m_w)
    inland_v = distance_transform_edt(land_v_c)

    # River proximity fields (smooth)
    rd_m = np.exp(-distance_transform_edt(~riv_m_w) / 2.5).astype(np.float32) * land_m_w
    rd_v = np.exp(-distance_transform_edt(~riv_v_c) / 2.5).astype(np.float32) * land_v_c
    hm_m_s = gaussian_filter(hm_m_w, sigma=1.0)
    hm_v_s = gaussian_filter(hm_v_c, sigma=1.0)

    pts_v = []
    resids = []
    scores = []
    margin = patch_half + search_pad + 2

    def _ncc_surface(tpl, srch):
        th, tw = tpl.shape
        sh, sw = srch.shape
        oh, ow = sh - th + 1, sw - tw + 1
        t0 = tpl - tpl.mean()
        t_norm = float(np.sqrt(np.sum(t0 * t0)))
        if t_norm < 1e-4:
            return None
        # Fast FFT cross-correlation or direct small loop (oh=ow=21 -> 441 ops is tiny via strides!)
        from numpy.lib.stride_tricks import sliding_window_view
        wins = sliding_window_view(srch, (th, tw))  # (oh, ow, th, tw)
        wm = wins.mean(axis=(2, 3), keepdims=True)
        w0 = wins - wm
        w_norm = np.sqrt(np.sum(w0 * w0, axis=(2, 3)))
        num = np.sum(w0 * t0[None, None, :, :], axis=(2, 3))
        return np.where(w_norm > 1e-4, num / (w_norm * t_norm + 1e-12), 0.0)

    for cy in range(margin, H - margin, grid_step):
        for cx in range(margin, W - margin, grid_step):
            if inland_m[cy, cx] < 14.0 or inland_v[cy, cx] < 14.0:
                continue
            y0, y1 = cy - patch_half, cy + patch_half
            x0, x1 = cx - patch_half, cx + patch_half
            if land_m_w[y0:y1, x0:x1].mean() < 0.85 or land_v_c[y0:y1, x0:x1].mean() < 0.85:
                continue
            sy0, sy1 = y0 - search_pad, y1 + search_pad
            sx0, sx1 = x0 - search_pad, x1 + search_pad

            c_hm = _ncc_surface(hm_m_s[y0:y1, x0:x1], hm_v_s[sy0:sy1, sx0:sx1])
            c_rv = _ncc_surface(rd_m[y0:y1, x0:x1], rd_v[sy0:sy1, sx0:sx1])
            if c_hm is None and c_rv is None:
                continue
            if c_hm is not None and c_rv is not None:
                # Weight heightmap if patch has real relief, else rely on rivers
                hm_std = float(hm_m_s[y0:y1, x0:x1].std())
                w_hm = 0.65 if hm_std > 4.0 else 0.25
                comb = w_hm * c_hm + (1.0 - w_hm) * c_rv
            elif c_hm is not None:
                comb = c_hm
            else:
                comb = c_rv

            iy, ix = np.unravel_index(int(np.argmax(comb)), comb.shape)
            peak = float(comb[iy, ix])
            # Reject border-touching peaks or flat ambiguous surfaces
            if iy == 0 or iy == comb.shape[0] - 1 or ix == 0 or ix == comb.shape[1] - 1:
                continue
            if peak < 0.62:
                continue
            # Check sharpness against ring >= 4px away
            yy, xx = np.ogrid[:comb.shape[0], :comb.shape[1]]
            far = ((yy - iy) ** 2 + (xx - ix) ** 2) >= 16
            if far.any() and (peak - float(comb[far].max())) < 0.06:
                continue
            # Sub-pixel parabolic refinement around peak
            dx_sub = 0.5 * (comb[iy, ix - 1] - comb[iy, ix + 1]) / (comb[iy, ix - 1] - 2 * comb[iy, ix] + comb[iy, ix + 1] - 1e-12)
            dy_sub = 0.5 * (comb[iy - 1, ix] - comb[iy + 1, ix]) / (comb[iy - 1, ix] - 2 * comb[iy, ix] + comb[iy + 1, ix] - 1e-12)
            dx = (ix - search_pad) + float(np.clip(dx_sub, -0.5, 0.5))
            dy = (iy - search_pad) + float(np.clip(dy_sub, -0.5, 0.5))
            if math.hypot(dx, dy) > 10.0:
                continue
            pts_v.append((float(cx + vx0), float(cy + vy0)))
            resids.append((dx, dy))
            scores.append(peak)

    return (
        np.asarray(pts_v, dtype=np.float64).reshape(-1, 2),
        np.asarray(resids, dtype=np.float64).reshape(-1, 2),
        np.asarray(scores, dtype=np.float64),
    )


def _fit_diffeomorphic_residual_field(
    me, van, A_main, mainland_mask_me,
    junc_pts_v, junc_resids,
    bnd_pts_v, bnd_resids, bnd_normals, bnd_weights,
    riv_pts_v, riv_resids, riv_normals,
    ncc_pts_v, ncc_resids, ncc_scores,
    bbox=VANILLA_EU_BBOX,
    step=RESIDUAL_GRID_STEP,
):
    """Solve regularized sparse bilinear lattice field with analytic Jacobian guard."""
    x0, y0, x1, y1 = bbox
    nx = int(math.ceil((x1 - x0) / step)) + 1
    ny = int(math.ceil((y1 - y0) / step)) + 1
    n_nodes = nx * ny

    # Determine land cell mask in vanilla crop for Jacobian checking
    cell_cx = np.clip(np.rint(x0 + (np.arange(nx - 1) + 0.5) * step).astype(np.int32), 0, van["width"] - 1)
    cell_cy = np.clip(np.rint(y0 + (np.arange(ny - 1) + 0.5) * step).astype(np.int32), 0, van["height"] - 1)
    land_cells = van["land_mask"][np.ix_(cell_cy, cell_cx)]

    def bilinear_entries(pts):
        """Return (valid_idx, node_ids (M,4), weights (M,4))."""
        if len(pts) == 0:
            return np.zeros(0, dtype=np.int64), np.zeros((0, 4), dtype=np.int64), np.zeros((0, 4), dtype=np.float64)
        gx = (pts[:, 0] - x0) / step
        gy = (pts[:, 1] - y0) / step
        ok = (gx >= 0.0) & (gy >= 0.0) & (gx <= nx - 1.000001) & (gy <= ny - 1.000001)
        vi = np.nonzero(ok)[0]
        gxi, gyi = gx[vi], gy[vi]
        ix = np.floor(gxi).astype(np.int64)
        iy = np.floor(gyi).astype(np.int64)
        fx, fy = gxi - ix, gyi - iy
        n00 = iy * nx + ix
        nodes = np.column_stack([n00, n00 + 1, n00 + nx, n00 + nx + 1])
        ws = np.column_stack([(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy])
        return vi, nodes, ws

    # Pre-build smoothness (1st-order gradient + 2nd-order Laplacian + tiny ridge)
    # on a single scalar component; coupled system has 2*n_nodes variables: [dx_0..n-1, dy_0..n-1]
    reg_rows, reg_cols, reg_vals = [], [], []
    rcnt = 0
    # Horizontal first differences
    for y in range(ny):
        base = y * nx
        for x in range(nx - 1):
            reg_rows.extend([rcnt, rcnt])
            reg_cols.extend([base + x, base + x + 1])
            reg_vals.extend([-1.0, 1.0])
            rcnt += 1
    # Vertical first differences
    for y in range(ny - 1):
        base = y * nx
        for x in range(nx):
            reg_rows.extend([rcnt, rcnt])
            reg_cols.extend([base + x, base + nx + x])
            reg_vals.extend([-1.0, 1.0])
            rcnt += 1
    G1 = sparse.csr_matrix((reg_vals, (reg_rows, reg_cols)), shape=(rcnt, n_nodes))
    L1 = (G1.T @ G1).tocsr()

    # Second-order bending (interior 2D Laplacian)
    b_rows, b_cols, b_vals = [], [], []
    bcnt = 0
    for y in range(1, ny - 1):
        for x in range(1, nx - 1):
            c = y * nx + x
            b_rows.extend([bcnt] * 5)
            b_cols.extend([c, c - 1, c + 1, c - nx, c + nx])
            b_vals.extend([-4.0, 1.0, 1.0, 1.0, 1.0])
            bcnt += 1
    B2 = sparse.csr_matrix((b_vals, (b_rows, b_cols)), shape=(bcnt, n_nodes))
    L2 = (B2.T @ B2).tocsr()
    I_n = sparse.eye(n_nodes, format="csr")

    # Assemble 2D observations (junctions + NCC) and 1D normal observations (borders + rivers)
    vi_j, nd_j, bw_j = bilinear_entries(junc_pts_v)
    r_j = junc_resids[vi_j] if len(vi_j) else np.zeros((0, 2))
    w_j_base = np.full(len(vi_j), 10.0, dtype=np.float64)

    vi_n, nd_n, bw_n = bilinear_entries(ncc_pts_v)
    r_n = ncc_resids[vi_n] if len(vi_n) else np.zeros((0, 2))
    w_n_base = (1.5 * ncc_scores[vi_n]) if len(vi_n) else np.zeros(0)

    # Combine 2D point obs
    nd_2d = np.vstack([nd_j, nd_n]) if (len(vi_j) + len(vi_n)) else np.zeros((0, 4), dtype=np.int64)
    bw_2d = np.vstack([bw_j, bw_n]) if (len(vi_j) + len(vi_n)) else np.zeros((0, 4), dtype=np.float64)
    r_2d = np.vstack([r_j, r_n]) if (len(vi_j) + len(vi_n)) else np.zeros((0, 2), dtype=np.float64)
    w_2d_base = np.concatenate([w_j_base, w_n_base]) if (len(vi_j) + len(vi_n)) else np.zeros(0)

    # 1D normal obs: borders + rivers
    vi_b, nd_b, bw_b = bilinear_entries(bnd_pts_v)
    r_b = bnd_resids[vi_b] if len(vi_b) else np.zeros((0, 2))
    nm_b = bnd_normals[vi_b] if len(vi_b) else np.zeros((0, 2))
    w_b_base = bnd_weights[vi_b] if len(vi_b) else np.zeros(0)

    vi_r, nd_r, bw_r = bilinear_entries(riv_pts_v)
    r_r = riv_resids[vi_r] if len(vi_r) else np.zeros((0, 2))
    nm_r = riv_normals[vi_r] if len(vi_r) else np.zeros((0, 2))
    w_r_base = np.full(len(vi_r), 0.5, dtype=np.float64)

    nd_1d = np.vstack([nd_b, nd_r]) if (len(vi_b) + len(vi_r)) else np.zeros((0, 4), dtype=np.int64)
    bw_1d = np.vstack([bw_b, bw_r]) if (len(vi_b) + len(vi_r)) else np.zeros((0, 4), dtype=np.float64)
    nm_1d = np.vstack([nm_b, nm_r]) if (len(vi_b) + len(vi_r)) else np.zeros((0, 2), dtype=np.float64)
    rn_1d = np.sum(np.vstack([r_b, r_r]) * nm_1d, axis=1) if (len(vi_b) + len(vi_r)) else np.zeros(0)
    w_1d_base = np.concatenate([w_b_base, w_r_base]) if (len(vi_b) + len(vi_r)) else np.zeros(0)

    # Build sparse design matrices M_2d (single-axis), M_1d_x, M_1d_y
    n2 = len(w_2d_base)
    if n2:
        M_2d = sparse.csr_matrix(
            (bw_2d.ravel(), (np.repeat(np.arange(n2), 4), nd_2d.ravel())),
            shape=(n2, n_nodes),
        )
    else:
        M_2d = sparse.csr_matrix((0, n_nodes))

    n1 = len(w_1d_base)
    if n1:
        r_idx1 = np.repeat(np.arange(n1), 4)
        c_idx1 = nd_1d.ravel()
        M_1d_x = sparse.csr_matrix(((bw_1d * nm_1d[:, 0:1]).ravel(), (r_idx1, c_idx1)), shape=(n1, n_nodes))
        M_1d_y = sparse.csr_matrix(((bw_1d * nm_1d[:, 1:2]).ravel(), (r_idx1, c_idx1)), shape=(n1, n_nodes))
        M_1d = sparse.hstack([M_1d_x, M_1d_y], format="csr")
    else:
        M_1d = sparse.csr_matrix((0, 2 * n_nodes))

    # Solve with increasing smoothness if diffeomorphism guard ever triggers
    lam_smooth = 6.0
    lam_bend = 4.0
    lam_ridge = 0.04
    dx_grid = np.zeros((ny, nx), dtype=np.float64)
    dy_grid = np.zeros((ny, nx), dtype=np.float64)
    guard_tries = 0
    min_jac_land = 1.0
    max_mag_land = 0.0

    for attempt in range(5):
        guard_tries = attempt + 1
        Reg1 = lam_smooth * L1 + lam_bend * L2 + lam_ridge * I_n
        Reg = sparse.block_diag([Reg1, Reg1], format="csr")
        w2 = w_2d_base.copy()
        w1 = w_1d_base.copy()

        for _ in range(3):  # Huber IRLS
            # Assemble normal equations:
            # 2D obs block-diagonal on x and y
            if n2:
                W2 = sparse.diags(w2)
                H2 = M_2d.T @ W2 @ M_2d
                A_sys = Reg + sparse.block_diag([H2, H2], format="csr")
                rhs_x = M_2d.T @ (w2 * r_2d[:, 0])
                rhs_y = M_2d.T @ (w2 * r_2d[:, 1])
                rhs = np.concatenate([rhs_x, rhs_y])
            else:
                A_sys = Reg.copy()
                rhs = np.zeros(2 * n_nodes, dtype=np.float64)

            if n1:
                W1 = sparse.diags(w1)
                A_sys = A_sys + M_1d.T @ W1 @ M_1d
                rhs = rhs + M_1d.T @ (w1 * rn_1d)

            sol = spla.spsolve(A_sys.tocsc(), rhs)
            dx_v = sol[:n_nodes]
            dy_v = sol[n_nodes:]

            # Huber reweight
            if n2:
                e2 = np.hypot(M_2d @ dx_v - r_2d[:, 0], M_2d @ dy_v - r_2d[:, 1])
                w2 = w_2d_base * np.where(e2 <= 2.5, 1.0, 2.5 / np.maximum(e2, 1e-9))
            if n1:
                e1 = np.abs(M_1d @ sol - rn_1d)
                w1 = w_1d_base * np.where(e1 <= 2.0, 1.0, 2.0 / np.maximum(e1, 1e-9))

        dx_grid = dx_v.reshape(ny, nx)
        dy_grid = dy_v.reshape(ny, nx)
        fld = SmoothResidualField(x0, y0, x1, y1, step, dx_grid, dy_grid, {})
        jac = fld.jacobian_det_grid()
        min_jac_land = float(jac[land_cells].min()) if land_cells.any() else float(jac.min())
        # Node-level magnitude on land
        node_x = np.clip(x0 + np.arange(nx) * step, 0, van["width"] - 1)
        node_y = np.clip(y0 + np.arange(ny) * step, 0, van["height"] - 1)
        land_nodes = van["land_mask"][np.ix_(node_y, node_x)]
        mag = np.hypot(dx_grid, dy_grid)
        max_mag_land = float(mag[land_nodes].max()) if land_nodes.any() else float(mag.max())
        if min_jac_land >= RESIDUAL_MIN_JAC and max_mag_land <= RESIDUAL_MAX_MAG_PX:
            break
        lam_smooth *= 2.2
        lam_bend *= 2.2

    meta = {
        "n_junction_obs": int(len(vi_j)),
        "n_ncc_inland_obs": int(len(vi_n)),
        "n_boundary_normal_obs": int(len(vi_b)),
        "n_river_normal_obs": int(len(vi_r)),
        "lam_smooth": float(lam_smooth),
        "lam_bend": float(lam_bend),
        "guard_attempts": int(guard_tries),
        "min_jacobian_det_on_land": round(min_jac_land, 4),
        "max_displacement_px_on_land": round(max_mag_land, 3),
        "p50_displacement_px_on_land": round(float(np.median(mag[land_nodes])), 3) if land_nodes.any() else 0.0,
        "p95_displacement_px_on_land": round(float(np.percentile(mag[land_nodes], 95)), 3) if land_nodes.any() else 0.0,
        "diffeomorphic_ok": bool(min_jac_land >= RESIDUAL_MIN_JAC),
    }
    return SmoothResidualField(x0, y0, x1, y1, step, dx_grid, dy_grid, meta)


# --------------------------------------------------------- full registration

class EuropeanRegistration:
    """Complete European ME -> Vanilla coordinate mapper (Stage 1 + Stage 3)."""

    def __init__(self, me, van, A_main, A_uk, A_ice, uk_mask_me, ice_mask_me,
                 mainland_mask_me, residual_field: SmoothResidualField | None,
                 clean_cores: dict, report: dict):
        self.me = me
        self.van = van
        self.A_main = A_main
        self.A_uk = A_uk
        self.A_ice = A_ice
        self.uk_mask_me = uk_mask_me
        self.ice_mask_me = ice_mask_me
        self.mainland_mask_me = mainland_mask_me
        self.residual_field = residual_field
        self.clean_cores = clean_cores
        self.report = report

    def map_stage1(self, xs: np.ndarray, ys: np.ndarray):
        """Map ME pixel coordinates (xs, ys) -> vanilla (vx, vy) using 3-landmass affines."""
        xs_f = np.asarray(xs, dtype=np.float64)
        ys_f = np.asarray(ys, dtype=np.float64)
        ix = np.clip(np.rint(xs_f).astype(np.int32), 0, self.me["width"] - 1)
        iy = np.clip(np.rint(ys_f).astype(np.int32), 0, self.me["height"] - 1)

        vx = self.A_main[0, 0] * xs_f + self.A_main[0, 1] * ys_f + self.A_main[0, 2]
        vy = self.A_main[1, 0] * xs_f + self.A_main[1, 1] * ys_f + self.A_main[1, 2]

        m_uk = self.uk_mask_me[iy, ix]
        if m_uk.any():
            vx[m_uk] = self.A_uk[0, 0] * xs_f[m_uk] + self.A_uk[0, 1] * ys_f[m_uk] + self.A_uk[0, 2]
            vy[m_uk] = self.A_uk[1, 0] * xs_f[m_uk] + self.A_uk[1, 1] * ys_f[m_uk] + self.A_uk[1, 2]

        m_ice = self.ice_mask_me[iy, ix]
        if m_ice.any():
            vx[m_ice] = self.A_ice[0, 0] * xs_f[m_ice] + self.A_ice[0, 1] * ys_f[m_ice] + self.A_ice[0, 2]
            vy[m_ice] = self.A_ice[1, 0] * xs_f[m_ice] + self.A_ice[1, 1] * ys_f[m_ice] + self.A_ice[1, 2]

        return vx, vy

    def map_points(self, xs: np.ndarray, ys: np.ndarray, use_residual: bool = True):
        """Map ME (xs, ys) -> vanilla (vx, vy) including Stage 3 residual on mainland."""
        vx, vy = self.map_stage1(xs, ys)
        if use_residual and self.residual_field is not None:
            ix = np.clip(np.rint(xs).astype(np.int32), 0, self.me["width"] - 1)
            iy = np.clip(np.rint(ys).astype(np.int32), 0, self.me["height"] - 1)
            # Apply smooth mainland residual ONLY to non-UK, non-Iceland pixels
            m_apply = (~self.uk_mask_me[iy, ix]) & (~self.ice_mask_me[iy, ix])
            if m_apply.any():
                dx, dy = self.residual_field.eval_xy(vx[m_apply], vy[m_apply])
                vx[m_apply] += dx
                vy[m_apply] += dy
        return vx, vy


def _boundary_normals_from_distance(mask_a: np.ndarray, pts_v: np.ndarray):
    """Estimate unit normals of boundary points in vanilla space using signed distance gradient."""
    sd = distance_transform_edt(mask_a) - distance_transform_edt(~mask_a)
    gy, gx = np.gradient(sd)
    ix = np.clip(np.rint(pts_v[:, 0]).astype(np.int32), 0, mask_a.shape[1] - 1)
    iy = np.clip(np.rint(pts_v[:, 1]).astype(np.int32), 0, mask_a.shape[0] - 1)
    nx = gx[iy, ix]
    ny = gy[iy, ix]
    norm = np.hypot(nx, ny)
    ok = norm > 1e-4
    out = np.zeros((len(pts_v), 2), dtype=np.float64)
    out[ok, 0] = nx[ok] / norm[ok]
    out[ok, 1] = ny[ok] / norm[ok]
    return out, ok


def run_registration(vanilla_root: str = DEFAULT_VANILLA, mod_dir: str | None = None,
                     apply_residual: bool = True) -> EuropeanRegistration:
    """Execute the complete Stage 1 + Stage 2 + Stage 3 European registration."""
    mod_dir = mod_dir or mod_root()
    me = load_map_side(mod_dir, is_me=True)
    van = load_map_side(vanilla_root, is_me=False)

    excl_sids = set(me["excluded_states"])
    excl_mask = np.isin(me["state_raster"], list(excl_sids)) if excl_sids else np.zeros_like(me["land_mask"])
    eu_land_me = me["land_mask"] & (me["state_raster"] > 0) & (~excl_mask)

    # Physical 4-connected land components in ME (on-map European scope)
    lab_m, _ = label(eu_land_me)
    sizes_m = np.bincount(lab_m.ravel())
    sizes_m[0] = 0
    main_cid = int(np.argmax(sizes_m))
    slices_m = find_objects(lab_m)

    # Build North Atlantic / British Isles mask & Iceland mask
    ice_sids = [sid for sid, st in me["states"].items() if st["owner36"] == "ICE" and sid not in excl_sids]
    ice_mask_me = eu_land_me & np.isin(me["state_raster"], ice_sids)

    uk_mask_me = np.zeros_like(eu_land_me)
    for cid in range(1, len(sizes_m)):
        if cid == main_cid or sizes_m[cid] == 0:
            continue
        sy, sx = slices_m[cid - 1]
        sub = lab_m[sy, sx] == cid
        if (ice_mask_me[sy, sx] & sub).any():
            continue
        ry, rx = np.nonzero(sub)
        cx = float(rx.mean() + sx.start)
        cy = float(ry.mean() + sy.start)
        # Disconnected islands in the North Atlantic / British Isles sector
        # (GB, Ireland, Man, Hebrides, Orkney, Shetland, Faroe Islands, Jan Mayen)
        if 650.0 <= cx <= 1360.0 and cy < 1615.0:
            uk_mask_me[sy, sx] |= sub

    mainland_mask_me = eu_land_me & (~uk_mask_me) & (~ice_mask_me)

    # Unified 1936 owner raster
    v_owners = sorted({st["owner36"] for st in van["states"].values() if st["owner36"]})
    tag2id = {t: i + 1 for i, t in enumerate(v_owners)}
    id2tag = {i + 1: t for i, t in enumerate(v_owners)}
    id2tag[-2] = "SEA"
    id2tag[-3] = "LAKE"

    u_me_all = _build_unified_label_raster(me, tag2id, ME_OWNER_TO_VANILLA, excl_sids)
    u_van = _build_unified_label_raster(van, tag2id, None, None)

    # Stage 1a: Mainland triple junctions -> initial A_main
    j_me = extract_triple_junctions(u_me_all, valid_mask=mainland_mask_me, radius=2, margin=12)
    j_van = extract_triple_junctions(u_van, valid_mask=None, radius=1, margin=10)
    common_j = sorted(set(j_me) & set(j_van))
    P_me_j = np.asarray([j_me[k] for k in common_j], dtype=np.float64)
    P_van_j = np.asarray([j_van[k] for k in common_j], dtype=np.float64)
    A_main_junc = _fit_affine_irls(P_me_j, P_van_j, iters=10, huber_delta=2.5)
    j_err_init = np.linalg.norm(_apply_affine(A_main_junc, P_me_j) - P_van_j, axis=1)

    # Stage 1b: Refine A_main via labeled mainland boundary ICP
    b_me_main = extract_labeled_boundaries(u_me_all, valid_mask=mainland_mask_me, grid_cell=4, margin=10)
    # Crop vanilla boundaries to Europe box for speed
    vx0, vy0, vx1, vy1 = VANILLA_EU_BBOX
    van_eu_mask = np.zeros_like(van["land_mask"])
    van_eu_mask[vy0:vy1, vx0:vx1] = True
    b_van_eu = extract_labeled_boundaries(u_van, valid_mask=van_eu_mask, grid_cell=1, margin=8)
    A_main = _refine_labeled_affine(b_me_main, b_van_eu, A_main_junc, max_dist=12.0, iters=15)
    j_err_stage1 = np.linalg.norm(_apply_affine(A_main, P_me_j) - P_van_j, axis=1)

    # Stage 1c: British Isles / North Atlantic cluster affine
    Hv, Wv = van["land_mask"].shape
    yy_v, xx_v = np.mgrid[0:Hv, 0:Wv]
    uk_v_mask = (
        van["land_mask"]
        & ((u_van == tag2id["ENG"]) | (u_van == tag2id["IRE"]))
        & (xx_v >= 2600) & (xx_v <= 2860) & (yy_v >= 220) & (yy_v <= 520)
    )
    # Fit on Great Britain + Ireland core (excluding distant Faroe/Jan Mayen so coast ICP is razor-sharp)
    Hm, Wm = me["land_mask"].shape
    yy_m, xx_m = np.mgrid[0:Hm, 0:Wm]
    uk_fit_me = uk_mask_me & (yy_m >= 950) & (yy_m <= 1615)
    A_uk, uk_p50, uk_p90 = _fit_island_cluster_affine(uk_fit_me, uk_v_mask, A_main, search_bbox_init=False, max_dist=16.0)

    # Stage 1d: Iceland affine
    ice_v_mask = van["land_mask"] & (u_van == tag2id["ICE"])
    A_ice, ice_p50, ice_p90 = _fit_island_cluster_affine(ice_mask_me, ice_v_mask, A_main, search_bbox_init=True, max_dist=20.0)

    # Build temporary Stage-1 registration object to evaluate clean shared cores
    reg_s1 = EuropeanRegistration(
        me, van, A_main, A_uk, A_ice, uk_mask_me, ice_mask_me, mainland_mask_me, None, {}, {}
    )

    # Stage 2: Automatically validate shared releasable core tags by geometric IoU under Stage 1
    v_owner_set = set(v_owners)
    v_cores = defaultdict(set)
    for sid, st in van["states"].items():
        for c in st["cores36"]:
            v_cores[c].add(sid)
    m_cores = defaultdict(set)
    for sid, st in me["states"].items():
        if sid in excl_sids:
            continue
        for c in st["cores36"]:
            m_cores[c].add(sid)

    shared_rel_tags = sorted((set(v_cores) & set(m_cores)) - v_owner_set)

    # Map all European ME land pixels once via Stage 1 to evaluate core IoUs cleanly
    eu_ys, eu_xs = np.nonzero(eu_land_me)
    vx_s1, vy_s1 = reg_s1.map_stage1(eu_xs, eu_ys)
    ix_s1 = np.rint(vx_s1).astype(np.int32)
    iy_s1 = np.rint(vy_s1).astype(np.int32)
    in_v = (ix_s1 >= 0) & (iy_s1 >= 0) & (ix_s1 < Wv) & (iy_s1 < Hv)
    m_sids_eu = me["state_raster"][eu_ys, eu_xs]
    v_sids_eu = np.zeros(len(eu_xs), dtype=np.int32)
    v_sids_eu[in_v] = van["state_raster"][iy_s1[in_v], ix_s1[in_v]]

    # Pre-tabulate (m_sid, v_sid) pixel counts under Stage 1
    pair_k = m_sids_eu.astype(np.int64) * 10000 + v_sids_eu.astype(np.int64)
    upk, ucnt = np.unique(pair_k, return_counts=True)
    # Build per-ME-state hit map & per-vanilla-state sampled area
    m_to_v_s1 = defaultdict(lambda: defaultdict(int))
    v_sampled_s1 = defaultdict(int)
    for k, c in zip(upk, ucnt):
        ms, vs, c = int(k // 10000), int(k % 10000), int(c)
        m_to_v_s1[ms][vs] += c
        if vs > 0:
            v_sampled_s1[vs] += c

    clean_cores = {}
    rejected_cores = {}
    for tag in shared_rel_tags:
        ms_set = m_cores[tag]
        vs_set = v_cores[tag]
        inter = 0
        m_tot = 0
        for ms in ms_set:
            for vs, c in m_to_v_s1[ms].items():
                if vs > 0:
                    m_tot += c
                    if vs in vs_set:
                        inter += c
        v_tot = sum(v_sampled_s1[vs] for vs in vs_set)
        union = m_tot + v_tot - inter
        iou = (inter / union) if union > 0 else 0.0
        if (iou >= 0.72 and v_tot >= 150) or (iou >= 0.85 and v_tot >= 30):
            clean_cores[tag] = {
                "iou": round(iou, 4),
                "me_states": len(ms_set),
                "v_states": len(vs_set),
                "inter_px": int(inter),
            }
        else:
            rejected_cores[tag] = {
                "iou": round(iou, 4),
                "me_states": len(ms_set),
                "v_states": len(vs_set),
            }

    # Stage 3: Bounded diffeomorphic smooth residual field on mainland
    residual_field = None
    if apply_residual:
        # 3a. Triple junctions: 1936 country junctions + high-IoU (>=0.85) core junctions!
        j_pts_list = [_apply_affine(A_main, P_me_j)]
        j_res_list = [P_van_j - _apply_affine(A_main, P_me_j)]

        # Also extract inland triple junctions from high-IoU core partitions (>= 0.85 IoU)
        hi_cores = sorted([t for t, info in clean_cores.items() if info["iou"] >= 0.85])
        if hi_cores:
            # Build sub-national label raster: (unified_owner, sorted_hi_cores_tuple) -> int id
            def _sub_raster(side, remap, excl):
                sig2id = {}
                max_sid = max(side["states"]) if side["states"] else 1
                lut = np.zeros(max_sid + 1, dtype=np.int32)
                for sid, st in side["states"].items():
                    if excl and sid in excl:
                        continue
                    o = remap.get(st["owner36"], st["owner36"]) if remap else st["owner36"]
                    if not o:
                        continue
                    cs = tuple(c for c in hi_cores if c in st["cores36"])
                    sig = (o, cs)
                    if sig not in sig2id:
                        sig2id[sig] = len(sig2id) + 1
                    lut[sid] = sig2id[sig]
                return lut, sig2id

            # Build shared sig2id across both maps
            all_sigs = set()
            for side, remap, excl in [(me, ME_OWNER_TO_VANILLA, excl_sids), (van, None, None)]:
                for sid, st in side["states"].items():
                    if excl and sid in excl:
                        continue
                    o = remap.get(st["owner36"], st["owner36"]) if remap else st["owner36"]
                    if o:
                        all_sigs.add((o, tuple(c for c in hi_cores if c in st["cores36"])))
            sig2id = {s: i + 1 for i, s in enumerate(sorted(all_sigs))}

            def _build_sub(side, remap, excl):
                h, w = side["land_mask"].shape
                out = np.zeros((h, w), dtype=np.int32)
                out[side["sea_mask"]] = -2
                out[side["lake_mask"]] = -3
                max_sid = max(side["states"]) if side["states"] else 1
                lut = np.zeros(max_sid + 1, dtype=np.int32)
                for sid, st in side["states"].items():
                    if excl and sid in excl:
                        continue
                    o = remap.get(st["owner36"], st["owner36"]) if remap else st["owner36"]
                    if o:
                        lut[sid] = sig2id.get((o, tuple(c for c in hi_cores if c in st["cores36"])), 0)
                sr = side["state_raster"]
                m = side["land_mask"] & (sr > 0)
                out[m] = lut[sr[m]]
                return out

            sub_me = _build_sub(me, ME_OWNER_TO_VANILLA, excl_sids)
            sub_van = _build_sub(van, None, None)
            sj_me = extract_triple_junctions(sub_me, valid_mask=mainland_mask_me, radius=2, margin=12)
            sj_van = extract_triple_junctions(sub_van, valid_mask=van_eu_mask, radius=1, margin=10)
            common_sj = sorted(set(sj_me) & set(sj_van))
            if common_sj:
                sm_p = np.asarray([sj_me[k] for k in common_sj], dtype=np.float64)
                sv_p = np.asarray([sj_van[k] for k in common_sj], dtype=np.float64)
                sm_v = _apply_affine(A_main, sm_p)
                sr_res = sv_p - sm_v
                keep_sj = np.linalg.norm(sr_res, axis=1) <= 12.0
                if keep_sj.any():
                    j_pts_list.append(sm_v[keep_sj])
                    j_res_list.append(sr_res[keep_sj])
            # Also collect high-IoU core internal boundary correspondences
            sb_me = extract_labeled_boundaries(sub_me, valid_mask=mainland_mask_me, grid_cell=5, margin=10)
            sb_van = extract_labeled_boundaries(sub_van, valid_mask=van_eu_mask, grid_cell=1, margin=8)
        else:
            sub_van = u_van
            sb_me, sb_van = {}, {}

        junc_pts_v = np.vstack(j_pts_list)
        junc_resids = np.vstack(j_res_list)
        keep_j = np.linalg.norm(junc_resids, axis=1) <= 13.0
        junc_pts_v = junc_pts_v[keep_j]
        junc_resids = junc_resids[keep_j]

        # 3b. Labeled boundary correspondences (normal-projected!)
        # Estimate unit normals on vanilla target boundaries using local tangent from kNN or distance transform
        bnd_pts_l, bnd_res_l, bnd_nrm_l, bnd_w_l = [], [], [], []

        def _collect_labeled_normals(src_b, dst_b, weight_border, weight_coast, max_d=11.0):
            for pair in sorted(set(src_b) & set(dst_b)):
                sp, dp = src_b[pair], dst_b[pair]
                if len(sp) < 5 or len(dp) < 5:
                    continue
                sp_v = _apply_affine(A_main, sp)
                si, di, dists = _mutual_nn(sp_v, dp, max_d)
                if len(si) < 4:
                    continue
                # Estimate local curve normal from k-nearest neighbors on dp
                k_nn = min(9, len(dp))
                t_dp = cKDTree(dp)
                _, nbrs = t_dp.query(dp[di], k=k_nn)
                local_pts = dp[nbrs]  # (M, k, 2)
                centered = local_pts - local_pts.mean(axis=1, keepdims=True)
                # Covariance 2x2 -> smallest eigenvector = normal!
                cxx = np.sum(centered[:, :, 0] ** 2, axis=1)
                cyy = np.sum(centered[:, :, 1] ** 2, axis=1)
                cxy = np.sum(centered[:, :, 0] * centered[:, :, 1], axis=1)
                # Principal tangent angle = 0.5 * atan2(2*cxy, cxx - cyy); normal = +pi/2
                ang = 0.5 * np.arctan2(2.0 * cxy, cxx - cyy) + 0.5 * math.pi
                nrm = np.column_stack([np.cos(ang), np.sin(ang)])
                # Reject degenerate isotropic blobs
                lam1 = 0.5 * (cxx + cyy + np.hypot(cxx - cyy, 2 * cxy))
                lam2 = 0.5 * (cxx + cyy - np.hypot(cxx - cyy, 2 * cxy))
                elong = lam1 > 2.5 * np.maximum(lam2, 1e-6)
                if not elong.any():
                    continue
                is_land_border = (pair[0] > 0 and pair[1] > 0)
                w = weight_border if is_land_border else weight_coast
                bnd_pts_l.append(sp_v[si[elong]])
                bnd_res_l.append(dp[di[elong]] - sp_v[si[elong]])
                bnd_nrm_l.append(nrm[elong])
                bnd_w_l.append(np.full(int(elong.sum()), w, dtype=np.float64))

        _collect_labeled_normals(b_me_main, b_van_eu, weight_border=2.0, weight_coast=1.2, max_d=11.0)
        if sb_me:
            # Add ONLY internal core borders (different sub-sigs within the SAME 1936 owner)
            id2owner = {i: s[0] for s, i in sig2id.items()}
            sb_me_int = {p: v for p, v in sb_me.items() if p[0] > 0 and p[1] > 0 and id2owner.get(p[0]) == id2owner.get(p[1])}
            sb_van_int = {p: v for p, v in sb_van.items() if p[0] > 0 and p[1] > 0 and id2owner.get(p[0]) == id2owner.get(p[1])}
            _collect_labeled_normals(sb_me_int, sb_van_int, weight_border=1.0, weight_coast=0.0, max_d=9.0)

        bnd_pts_v = np.vstack(bnd_pts_l) if bnd_pts_l else np.zeros((0, 2))
        bnd_resids = np.vstack(bnd_res_l) if bnd_res_l else np.zeros((0, 2))
        bnd_normals = np.vstack(bnd_nrm_l) if bnd_nrm_l else np.zeros((0, 2))
        bnd_weights = np.concatenate(bnd_w_l) if bnd_w_l else np.zeros(0)

        # 3c. River skeleton mutual-NN correspondences (normal-projected!)
        ry_m, rx_m = np.nonzero(me["river_mask"] & mainland_mask_me)
        if len(rx_m):
            # Downsample ME rivers onto 5px grid
            r_pts_m = np.column_stack([rx_m, ry_m]).astype(np.float64)
            gx = np.floor(r_pts_m[:, 0] / 5).astype(np.int64)
            gy = np.floor(r_pts_m[:, 1] / 5).astype(np.int64)
            gk = gy * 100000 + gx
            o = np.argsort(gk)
            _, st = np.unique(gk[o], return_index=True)
            cnt = np.diff(np.append(st, len(gk)))
            r_pts_m = np.column_stack([np.add.reduceat(r_pts_m[o, 0], st) / cnt,
                                       np.add.reduceat(r_pts_m[o, 1], st) / cnt])
            r_pts_mv = _apply_affine(A_main, r_pts_m)
        else:
            r_pts_mv = np.zeros((0, 2))

        ry_v, rx_v = np.nonzero(van["river_mask"] & van_eu_mask)
        r_pts_v = np.column_stack([rx_v, ry_v]).astype(np.float64) if len(rx_v) else np.zeros((0, 2))
        if len(r_pts_mv) >= 10 and len(r_pts_v) >= 10:
            si_r, di_r, _ = _mutual_nn(r_pts_mv, r_pts_v, max_dist=7.0)
            if len(si_r) >= 10:
                t_rv = cKDTree(r_pts_v)
                _, nbrs = t_rv.query(r_pts_v[di_r], k=min(9, len(r_pts_v)))
                lp = r_pts_v[nbrs]
                cc = lp - lp.mean(axis=1, keepdims=True)
                cxx = np.sum(cc[:, :, 0] ** 2, axis=1)
                cyy = np.sum(cc[:, :, 1] ** 2, axis=1)
                cxy = np.sum(cc[:, :, 0] * cc[:, :, 1], axis=1)
                ang = 0.5 * np.arctan2(2.0 * cxy, cxx - cyy) + 0.5 * math.pi
                nrm_r = np.column_stack([np.cos(ang), np.sin(ang)])
                lam1 = 0.5 * (cxx + cyy + np.hypot(cxx - cyy, 2 * cxy))
                lam2 = 0.5 * (cxx + cyy - np.hypot(cxx - cyy, 2 * cxy))
                el = lam1 > 2.5 * np.maximum(lam2, 1e-6)
                riv_pts_v = r_pts_mv[si_r[el]]
                riv_resids = r_pts_v[di_r[el]] - riv_pts_v
                riv_normals = nrm_r[el]
            else:
                riv_pts_v = riv_resids = riv_normals = np.zeros((0, 2))
        else:
            riv_pts_v = riv_resids = riv_normals = np.zeros((0, 2))

        # 3d. High-confidence inland local NCC anchors (heightmap + rivers)
        ncc_pts_v, ncc_resids, ncc_scores = _inland_ncc_anchors(
            me, van, A_main, mainland_mask_me, VANILLA_EU_BBOX
        )

        residual_field = _fit_diffeomorphic_residual_field(
            me, van, A_main, mainland_mask_me,
            junc_pts_v, junc_resids,
            bnd_pts_v, bnd_resids, bnd_normals, bnd_weights,
            riv_pts_v, riv_resids, riv_normals,
            ncc_pts_v, ncc_resids, ncc_scores,
            bbox=VANILLA_EU_BBOX,
            step=RESIDUAL_GRID_STEP,
        )

    reg = EuropeanRegistration(
        me, van, A_main, A_uk, A_ice, uk_mask_me, ice_mask_me, mainland_mask_me,
        residual_field, clean_cores, {},
    )

    # ---------------- Independent Benchmark Evaluation (Stage 1 vs Stage 3)
    benchmarks = {}
    dt_rv_van = distance_transform_edt(~van["river_mask"])
    for mode_name, use_res in [("stage1_3landmass", False), ("stage3_diffeomorphic", True)]:
        if use_res and residual_field is None:
            continue
        vx, vy = reg.map_points(eu_xs, eu_ys, use_residual=use_res)
        ix = np.rint(vx).astype(np.int32)
        iy = np.rint(vy).astype(np.int32)
        in_f = (ix >= 0) & (iy >= 0) & (ix < Wv) & (iy < Hv)
        v_s = np.zeros(len(eu_xs), dtype=np.int32)
        v_o = np.zeros(len(eu_xs), dtype=np.int32)
        v_s[in_f] = van["state_raster"][iy[in_f], ix[in_f]]
        v_o[in_f] = u_van[iy[in_f], ix[in_f]]
        m_o = u_me_all[eu_ys, eu_xs]

        both_own = (m_o > 0) & (v_o > 0)
        same_own_acc = float((m_o[both_own] == v_o[both_own]).mean()) if both_own.any() else 0.0

        # River distance on mainland rivers
        is_riv = me["river_mask"][eu_ys, eu_xs] & mainland_mask_me[eu_ys, eu_xs] & in_f
        riv_d = dt_rv_van[iy[is_riv], ix[is_riv]]
        riv_p50 = float(np.median(riv_d)) if len(riv_d) else 0.0
        riv_p90 = float(np.percentile(riv_d, 90)) if len(riv_d) else 0.0

        # Heightmap NCC on both-land
        both_l = in_f & van["land_mask"][np.clip(iy, 0, Hv - 1), np.clip(ix, 0, Wv - 1)]
        hm_m_vals = me["heightmap"][eu_ys[both_l], eu_xs[both_l]]
        hm_v_vals = van["heightmap"][iy[both_l], ix[both_l]]
        hm_ncc = float(np.corrcoef(hm_m_vals, hm_v_vals)[0, 1]) if len(hm_m_vals) > 10 else 0.0

        # State & province purity + independent national capital benchmark
        pk = m_sids_eu.astype(np.int64) * 10000 + v_s.astype(np.int64)
        uk2, uc2 = np.unique(pk, return_counts=True)
        st_same = defaultdict(lambda: defaultdict(int))
        st_all = defaultdict(lambda: defaultdict(int))
        v_sid_o = {sid: tag2id.get(st["owner36"], 0) for sid, st in van["states"].items()}
        m_sid_o = {sid: tag2id.get(ME_OWNER_TO_VANILLA.get(st["owner36"], st["owner36"]), 0)
                   for sid, st in me["states"].items()}
        for k, c in zip(uk2, uc2):
            ms, vs, c = int(k // 10000), int(k % 10000), int(c)
            if vs > 0:
                st_all[ms][vs] += c
                if v_sid_o.get(vs, -1) == m_sid_o.get(ms, -2):
                    st_same[ms][vs] += c

        pur_same = []
        best_same = {}
        for ms, d in st_same.items():
            tot = sum(d.values())
            bv, bc = max(d.items(), key=lambda x: x[1])
            pur_same.append(bc / tot)
            best_same[ms] = bv
        pur_same = np.asarray(pur_same, dtype=np.float64)

        # Province purity
        m_pids_eu = me["prov_raster"][eu_ys, eu_xs]
        ppk = m_pids_eu.astype(np.int64) * 10000 + v_s.astype(np.int64)
        uppk, uppc = np.unique(ppk, return_counts=True)
        p_tot = defaultdict(int)
        p_best = defaultdict(int)
        for k, c in zip(uppk, uppc):
            mp, vs, c = int(k // 10000), int(k % 10000), int(c)
            if vs > 0:
                p_tot[mp] += c
                if c > p_best[mp]:
                    p_best[mp] = c
        p_pur = np.asarray([p_best[p] / p_tot[p] for p in p_tot], dtype=np.float64)

        # Independent national capital benchmark (shared European 1936 countries)
        cap_ok = cap_tot = 0
        cap_misses = []
        shared_eu_owners = sorted(
            (set(me["capitals"]) & set(van["capitals"]) & set(v_owners)) - EXCLUDED_OWNERS
        )
        for tag in shared_eu_owners:
            mc = me["capitals"][tag]
            vc = van["capitals"][tag]
            if mc in excl_sids or mc not in best_same:
                continue
            cap_tot += 1
            if best_same[mc] == vc:
                cap_ok += 1
            else:
                cap_misses.append({"tag": tag, "me_cap": mc, "got_v": best_same[mc], "expected_v": vc})

        # 1936->1939 historical boundary IoU (Anschluss, Sudetenland, Bohemia/Slovakia, Albania, DMZ)
        hist_tests = {}
        for hname, m_pred, v_pred in [
            ("rhineland_dmz_1936", lambda s: s["owner36"] == "GER" and s["dmz36"],
             lambda s: s["owner36"] == "GER" and s["dmz36"]),
            ("anschluss_1938", lambda s: s["owner36"] == "AUS" and s["owner39"] == "GER",
             lambda s: s["owner36"] == "AUS" and s["owner39"] == "GER"),
            ("sudetenland_1938", lambda s: s["owner36"] == "CZE" and s["owner39"] == "GER",
             lambda s: s["owner36"] == "CZE" and s["owner39"] == "GER"),
            ("slovakia_1939", lambda s: s["owner36"] == "CZE" and s["owner39"] == "SLO",
             lambda s: s["owner36"] == "CZE" and s["owner39"] == "SLO"),
            ("albania_1939", lambda s: s["owner36"] == "ALB" and s["owner39"] == "ITA",
             lambda s: s["owner36"] == "ALB" and s["owner39"] == "ITA"),
        ]:
            ms_h = {sid for sid, st in me["states"].items() if sid not in excl_sids and m_pred(st)}
            vs_h = {sid for sid, st in van["states"].items() if v_pred(st)}
            if ms_h and vs_h:
                inter = sum(st_all[ms][vs] for ms in ms_h for vs in vs_h if vs in st_all[ms])
                mtot = sum(sum(st_all[ms].values()) for ms in ms_h)
                vtot = sum(uc2[k_i] for k_i, k in enumerate(uk2) if int(k % 10000) in vs_h)
                union = mtot + vtot - inter
                hist_tests[hname] = round(inter / union, 4) if union > 0 else 0.0

        # Triple junction residual after warp
        p_j_w_x, p_j_w_y = reg.map_points(P_me_j[:, 0], P_me_j[:, 1], use_residual=use_res)
        j_err_after = np.hypot(p_j_w_x - P_van_j[:, 0], p_j_w_y - P_van_j[:, 1])

        benchmarks[mode_name] = {
            "triple_junction_p50_px": round(float(np.median(j_err_after)), 3),
            "triple_junction_p90_px": round(float(np.percentile(j_err_after, 90)), 3),
            "same_1936_owner_px_acc": round(same_own_acc, 5),
            "river_dist_p50_px": round(riv_p50, 3),
            "river_dist_p90_px": round(riv_p90, 3),
            "heightmap_ncc": round(hm_ncc, 4),
            "me_state_same_owner_purity_ge95": round(float((pur_same >= 0.95).mean()), 4),
            "me_state_same_owner_purity_ge90": round(float((pur_same >= 0.90).mean()), 4),
            "me_state_same_owner_purity_ge80": round(float((pur_same >= 0.80).mean()), 4),
            "me_state_same_owner_purity_lt60": round(float((pur_same < 0.60).mean()), 4),
            "me_prov_purity_ge95": round(float((p_pur >= 0.95).mean()), 4),
            "me_prov_purity_ge90": round(float((p_pur >= 0.90).mean()), 4),
            "me_prov_purity_ge80": round(float((p_pur >= 0.80).mean()), 4),
            "national_capitals_hit": f"{cap_ok}/{cap_tot}",
            "national_capitals_acc": round(cap_ok / cap_tot, 4) if cap_tot else 0.0,
            "national_capital_misses": cap_misses,
            "historical_1936_1939_iou": hist_tests,
        }

    reg.report = {
        "schema": "eu_map_registration_report/v1",
        "scope": "European / MENA main map (excluding top-left NA inset, Congo/Indonesia offmap proxies, and MAP borders)",
        "inputs": {
            "vanilla_root": os.path.abspath(vanilla_root),
            "mod_root": os.path.abspath(mod_dir),
            "me_states_total": len(me["states"]),
            "me_states_excluded": len(excl_sids),
            "me_states_europe": len(me["states"]) - len(excl_sids),
            "vanilla_states_total": len(van["states"]),
        },
        "excluded_me_states": {str(k): {"owner": me["states"][k]["owner36"], "name": me["states"][k]["name"], "reason": v}
                               for k, v in sorted(me["excluded_states"].items())},
        "stage1_landmasses": {
            "mainland_eurasia_africa": {
                "affine_me_to_vanilla": [[round(float(x), 7) for x in row] for row in A_main.tolist()],
                "triple_junctions_common": len(common_j),
                "triple_junction_init_p50_px": round(float(np.median(j_err_init)), 3),
                "triple_junction_refined_p50_px": round(float(np.median(j_err_stage1)), 3),
                "triple_junction_refined_p90_px": round(float(np.percentile(j_err_stage1, 90)), 3),
                "area_px": int(mainland_mask_me.sum()),
            },
            "north_atlantic_british_isles": {
                "affine_me_to_vanilla": [[round(float(x), 7) for x in row] for row in A_uk.tolist()],
                "coast_p50_px": round(uk_p50, 3),
                "coast_p90_px": round(uk_p90, 3),
                "shift_vs_mainland_px": [round(float(A_uk[0, 2] - A_main[0, 2]), 2),
                                         round(float(A_uk[1, 2] - A_main[1, 2]), 2)],
                "area_px": int(uk_mask_me.sum()),
            },
            "iceland": {
                "affine_me_to_vanilla": [[round(float(x), 7) for x in row] for row in A_ice.tolist()],
                "coast_p50_px": round(ice_p50, 3),
                "coast_p90_px": round(ice_p90, 3),
                "shift_vs_mainland_px": [round(float(A_ice[0, 2] - A_main[0, 2]), 2),
                                         round(float(A_ice[1, 2] - A_main[1, 2]), 2)],
                "area_px": int(ice_mask_me.sum()),
            },
        },
        "stage2_core_validation": {
            "clean_cores_count": len(clean_cores),
            "rejected_cores_count": len(rejected_cores),
            "clean_cores": clean_cores,
            "rejected_cores": rejected_cores,
        },
        "stage3_residual_field": residual_field.meta if residual_field is not None else None,
        "benchmarks": benchmarks,
    }
    return reg


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run European ME -> Vanilla registration and benchmark.")
    parser.add_argument("--vanilla", default=DEFAULT_VANILLA)
    parser.add_argument("--mod", default=None)
    parser.add_argument("--no-residual", action="store_true")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args(argv)

    reg = run_registration(args.vanilla, args.mod, apply_residual=not args.no_residual)
    text = json.dumps(reg.report, indent=2, ensure_ascii=False)
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote registration report to {args.output_json}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
