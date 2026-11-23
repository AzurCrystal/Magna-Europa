#!/usr/bin/env python
"""Precise review overlay: ME state -> vanilla state backward raster warp.

Diagnostic companion to build_state_map.py. For one ME state and a list of
candidate vanilla states (default: the review_queue.tsv row), renders a PNG
overlay plus a same-name .json with machine-readable coverage numbers.

Four ME-px -> vanilla-px maps are available (--warp; default natural):

  natural (default): the frozen coastline affine (loaded via
    --coast-warp-json or fitted live exactly as --warp coastline)
    composed with ONE local correction fitted on natural features
    around the target ME state: rivers.bmp strokes (palette indices
    0-11) and definition.csv type=lake shorelines, matched per class
    by mutual NN + distance cap + MAD outlier cut
    (tools/continent_registration.py natural_feature_correction).
    --natural-radius (default 500 ME px) sets the match region around
    the target's pixel centroid; map_points tapers the correction back
    to the coast affine over [radius, radius*blend]. If matching is
    under-powered or the fit fails its guards the correction is the
    identity and the map is the plain coast affine -- fallback_reason
    is reported in JSON/legend, never silently patched. Only physical
    features feed this fit: no vps/anchors, no state rasters, no
    review candidates, no VP/city data. Validity = the mapped point
    lands inside the vanilla raster; no convex hull, no folded
    triangles.
    The sidecar also records review-only landmark topology diagnostics
    plus a nested river branch/connectivity graph diagnostic for the
    coast and natural affine; these never assign a state.

  visual: a coast affine plus a regularised bilinear-lattice displacement
    field fitted to named VP/city anchors and river/lake residuals. The
    default --visual-boundary neighbours adds adjudicated neighbouring-state
    border hints at the exact edges being reviewed. This is a bounded,
    sampled-Jacobian-guarded deformation for visual aid only: boundary hints
    are candidate-conditioned and never prove state identity. Use
    --visual-boundary off for the candidate-independent field, or
    --visual-ancestor <id> once per alternative candidate.

  coastline: G is ONE global 2x3 affine fitted to the two maps'
    continent=1 land<->sea coastlines (tools/continent_registration.py).
    The fit consumes ONLY coastline points -- never vps.tsv/anchors, state
    rasters/history, or the review candidates (candidates enter only after
    the affine is frozen, to seed sampling). A precomputed fit can be
    injected via --coast-warp-json (JSON containing affine_me_to_vanilla).
    Validity = the mapped point lands inside the vanilla raster; pixels
    mapping outside the map frame are invalid gray. No convex hull, no
    folded triangles.

  vp (legacy): control points are the canonical generator manifest
    docs/states/registration_anchors.tsv, written by build_state_map.py
    (kept verbatim; vp_name backfilled from vps.tsv for display). Fallback
    when the manifest is absent: vps.tsv status=name rows, with duplicate
    endpoints, ambiguous pairs and cross-state pairs dropped BEFORE
    triangulation. G is a Delaunay piecewise-affine map
    (scipy LinearNDInterpolator); points outside the convex hull return
    NaN -> invalid gray, and simplices whose affine map flips orientation
    or collapses are likewise invalid (folded). No RBF/TPS.

Common to all modes:

  1. province raster + province->state raster are built on BOTH sides from
     map/provinces.bmp + map/definition.csv + history/states/*.txt.
  2. Backward warp only: for every pixel of the ME-side crop we evaluate G
     and sample the vanilla state raster at the mapped pixel. Vanilla
     outlines are never vector-traced.
  3. The JSON records the status=near VP holdout rows, excluded from every
     fit, as a global registration check. The visual field additionally uses
     named anchors and may use candidate-conditioned boundary hints; those
     hints are labelled visual aid and are not independent identity evidence.

The crop defaults to the target ME state bbox plus --padding, so candidate
fills are clipped to that window (a clipped edge is not the candidate's
full extent). --expand-candidates restores the legacy behaviour of probing
the map domain (vp: the control hull; coastline/natural: the whole ME
map) for candidate pixels and growing the crop to cover them.
All coordinates are provinces.bmp image coordinates (x=column, y=row,
origin top-left). No y-flip anywhere.

Usage:
  python tools/render_state_overlay.py --me-id 2624 --warp visual
  python tools/render_state_overlay.py --me-id 2379 --candidates 397,262,825 \
      --visual-ancestor 397 --warp visual --output _ctx/ukhta.png --padding 96
  python tools/render_state_overlay.py --me-id 2624 --warp vp   # legacy
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay, cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root, repo_root

VANILLA = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"

CAND_COLORS = [  # first color is reserved for the claimed ancestor
    (80, 140, 255), (60, 190, 120), (200, 90, 220),
    (235, 160, 40), (0, 180, 200), (170, 150, 60),
]


# ---------------------------------------------------------------- map inputs

def load_definition(path):
    """definition.csv -> {prov_id: (r,g,b)}."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if row and row[0].strip().isdigit():
                out[int(row[0])] = (int(row[1]), int(row[2]), int(row[3]))
    return out


def province_raster(bmp_path, defn):
    """provinces.bmp -> int32 HxW raster of province ids (-1 = unknown color)."""
    img = np.asarray(Image.open(bmp_path).convert("RGB"), dtype=np.uint32)
    packed = (img[..., 0] << 16) | (img[..., 1] << 8) | img[..., 2]
    keys = np.array(sorted(defn), dtype=np.int64)
    vals = np.array([(r << 16) | (g << 8) | b
                     for _, (r, g, b) in sorted(defn.items())], dtype=np.int64)
    order = np.argsort(vals)
    keys, vals = keys[order], vals[order]
    pos = np.searchsorted(vals, packed.ravel())
    pos_clip = np.clip(pos, 0, len(vals) - 1)
    hit = vals[pos_clip] == packed.ravel()
    out = np.full(packed.size, -1, dtype=np.int32)
    out[hit] = keys[pos_clip[hit]]
    return out.reshape(packed.shape)


def load_state_provinces(states_dir):
    """history/states/*.txt -> {state_id: [prov_id,...]} (filename ignored)."""
    grab_re = re.compile(r"\bprovinces\s*=\s*\{")
    id_re = re.compile(r"\bid\s*=\s*(\d+)")
    out = {}
    for path in sorted(glob.glob(os.path.join(states_dir, "*.txt"))):
        src = open(path, encoding="utf-8-sig", errors="replace").read()
        m = id_re.search(src)
        if not m:
            continue
        sid = int(m.group(1))
        pm = grab_re.search(src)
        if not pm:
            out[sid] = []
            continue
        depth, i = 1, pm.end()
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        out[sid] = [int(x) for x in re.findall(r"\d+", src[pm.end():i - 1])]
    return out


def state_raster(prov_raster, state_provs):
    """province-id raster -> state-id raster (-1 = unassigned) via LUT."""
    max_pid = max(prov_raster.max(), max(
        (p for ps in state_provs.values() for p in ps), default=0))
    lut = np.full(max_pid + 2, -1, dtype=np.int32)
    for sid, provs in state_provs.items():
        for p in provs:
            lut[p] = sid
    flat = prov_raster.ravel()
    out = np.full(flat.shape, -1, dtype=np.int32)
    ok = flat >= 0
    out[ok] = lut[flat[ok]]
    return out.reshape(prov_raster.shape)


def province_centroids(prov_raster, cache, cache_key):
    """{prov_id: (x,y)} centroid in image coords.

    Prefers _ctx/cents.json entries under `cache_key` ('vc'/'mc'); provinces
    missing from the cache are computed from the raster (never partially
    cached — if the cache is unusable we compute the whole set).
    """
    ids = np.unique(prov_raster)
    ids = ids[ids >= 0]
    cached = {}
    if cache and cache_key in cache:
        cached = {int(k): tuple(v) for k, v in cache[cache_key].items()}
    if cached and set(ids) <= set(cached):
        return {i: cached[i] for i in ids}
    h, w = prov_raster.shape
    flat = prov_raster.ravel()
    keep = flat >= 0
    f = flat[keep]
    yy, xx = np.nonzero(keep.reshape(prov_raster.shape))
    n = np.bincount(f, minlength=int(ids.max()) + 1)
    sx = np.bincount(f, weights=xx.astype(np.float64), minlength=n.size)
    sy = np.bincount(f, weights=yy.astype(np.float64), minlength=n.size)
    return {int(p): (sx[p] / n[p], sy[p] / n[p]) for p in ids if n[p]}


# ------------------------------------------------------------ control points

def _anchor_controls(docs, v_cent, m_cent):
    """docs/states/registration_anchors.tsv -> control pairs.

    Canonical manifest written by build_state_map.py: the exact 560 cleaned
    (v_prov, v_state, me_prov, me_state) anchors the generator used for its
    ME->vanilla pixel census. Rows are kept verbatim -- the generator
    already removed ambiguous/cross-state duplicates while building it, so
    the vps.tsv heuristics (status=name, by_v/by_m uniqueness, m2v
    cross-state check) must NOT re-filter it. vp_name is backfilled from
    vps.tsv for display only; rows without a name row there get "anchor".
    """
    names = {}
    with open(os.path.join(docs, "vps.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["v_prov"].isdigit() and r["me_prov"].isdigit():
                key = (int(r["v_prov"]), int(r["me_prov"]))
                names.setdefault(key, r["vp_name"])

    kept, dropped = [], []
    seen_xy = set()
    with open(os.path.join(docs, "registration_anchors.tsv"),
              encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            vp, mp = int(r["v_prov"]), int(r["me_prov"])
            r["vp_name"] = names.get((vp, mp), "anchor")
            if vp not in v_cent or mp not in m_cent:
                r["_drop"] = "no-centroid"
            elif m_cent[mp] in seen_xy:
                # identical ME coordinates collapse to one vertex (qhull)
                r["_drop"] = "same-me-xy"
            else:
                seen_xy.add(m_cent[mp])
                kept.append(r)
                continue
            dropped.append(r)
    return kept, dropped


def _vps_controls(docs, me2v, v_cent, m_cent):
    """vps.tsv status=name rows -> cleaned control pairs (legacy fallback).

    Drops: duplicate identical pairs, ambiguous pairs (same endpoint paired
    to >1 partner), cross-state pairs (ME state's adjudicated ancestor !=
    v_state), pairs without a centroid on either side.
    """
    rows, seen = [], set()
    with open(os.path.join(docs, "vps.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["status"] != "name":
                continue
            if not (r["v_prov"].isdigit() and r["me_prov"].isdigit()):
                continue
            key = (int(r["v_prov"]), int(r["me_prov"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)

    by_v, by_m = defaultdict(set), defaultdict(set)
    for r in rows:
        by_v[int(r["v_prov"])].add(int(r["me_prov"]))
        by_m[int(r["me_prov"])].add(int(r["v_prov"]))

    kept, dropped = [], []
    for r in rows:
        vp, mp, ms = int(r["v_prov"]), int(r["me_prov"]), int(r["me_state"])
        vs = int(r["v_state"])
        if len(by_v[vp]) > 1 or len(by_m[mp]) > 1:
            r["_drop"] = "ambiguous"
        elif me2v.get(ms) != vs:
            r["_drop"] = "cross-state"
        elif vp not in v_cent or mp not in m_cent:
            r["_drop"] = "no-centroid"
        else:
            kept.append(r)
            continue
        dropped.append(r)

    # identical ME coordinates collapse to one vertex (qhull safety)
    seen_xy, unique = set(), []
    for r in kept:
        xy = m_cent[int(r["me_prov"])]
        if xy in seen_xy:
            r["_drop"] = "same-me-xy"
            dropped.append(r)
            continue
        seen_xy.add(xy)
        unique.append(r)
    return unique, dropped


def load_controls(docs, me2v, v_cent, m_cent):
    """Kept control pairs + dropped rows.

    Source: docs/states/registration_anchors.tsv when present (canonical
    build_state_map.py manifest; not re-filtered by m2v/vps rules), else
    the vps.tsv status=name fallback. Returns (pts_me Nx2, pts_v Nx2,
    kept_rows, dropped_rows) where each row is the raw TSV dict.
    """
    if os.path.isfile(os.path.join(docs, "registration_anchors.tsv")):
        unique, dropped = _anchor_controls(docs, v_cent, m_cent)
    else:
        unique, dropped = _vps_controls(docs, me2v, v_cent, m_cent)

    pts_me = np.array([m_cent[int(r["me_prov"])] for r in unique])
    pts_v = np.array([v_cent[int(r["v_prov"])] for r in unique])
    return pts_me, pts_v, unique, dropped


def load_m2v(docs):
    me2v, names = {}, {}
    with open(os.path.join(docs, "m2v_states.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            mid = int(r["me_id"])
            names[mid] = r["me_name"]
            try:
                me2v[mid] = int(r["v_ancestor"])
            except ValueError:
                pass
    return me2v, names


def load_m2v_basis(docs):
    """me_id -> basis string (same-id/name/.../pixel-geo-low).

    Used to grade boundary evidence provenance: arcs whose ME
    endpoints' ancestry is warp-derived (vp-geo/pixel-geo) are
    labelled as such, never as independent identity evidence.
    """
    basis = {}
    with open(os.path.join(docs, "m2v_states.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            basis[int(r["me_id"])] = r.get("basis", "")
    return basis


def load_vanilla_names(docs):
    names = {}
    with open(os.path.join(docs, "v2m_states.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            names[int(r["vanilla_id"])] = r["vanilla_name"]
    return names


def default_candidates(docs, me_id):
    """review_queue.tsv row -> [claimed ancestor, ...neighbour ancestors]."""
    with open(os.path.join(docs, "review_queue.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if int(r["me_id"]) != me_id:
                continue
            cands = [int(r["v_ancestor"])] if r["v_ancestor"].isdigit() else []
            for v in re.findall(r"->v(\d+)", r.get("neighbour_ancestors", "")):
                if int(v) not in cands:
                    cands.append(int(v))
            return cands
    return []


# --------------------------------------------------------------- warp maps

class WarpMap:
    """Closed set of ME-px -> vanilla-px maps
    ("natural" | "coastline" | "vp" | "visual").


    map_points() returns NaN rows only in vp mode (outside the Delaunay
    hull); the coast affine and the natural-correction map are finite
    everywhere, as is the visual displacement-field map (tapered to the
    coast affine outside its finite support). probe_bbox() is the
    ME-side rect scanned for candidate pixels by --expand-candidates:
    the control hull in vp mode, the whole ME map for the global affine,
    the natural map and the visual map.
    """


    def __init__(self, mode, affine=None, tri=None, g=None,
                 good_simplex=None, pts_me=None, registration=None):
        self.mode = mode
        self.affine = affine              # coastline: (2,3) ME->vanilla
        self.tri = tri                    # vp: Delaunay over pts_me
        self.g = g                        # vp: LinearNDInterpolator
        self.good_simplex = good_simplex  # vp: per-simplex validity flags
        self.pts_me = pts_me              # vp: kept control points (Nx2)
        self.registration = registration  # natural/visual: reg dict


    @classmethod
    def from_affine(cls, affine):
        A = np.asarray(affine, dtype=np.float64)
        if A.shape != (2, 3):
            raise ValueError("affine_me_to_vanilla must be 2x3, got %s"
                             % (A.shape,))
        if not np.isfinite(A).all():
            raise ValueError("affine_me_to_vanilla has non-finite values")
        if np.linalg.det(A[:, :2]) <= 0:
            raise ValueError("affine_me_to_vanilla det <= 0 (mirror/"
                             "degenerate map)")
        return cls("coastline", affine=A)

    @classmethod
    def from_natural(cls, registration):
        """Coast affine + local natural-feature correction.

        `registration` is a continent_registration.natural_feature_
        correction() result dict; map_points() defers to
        continent_registration.map_points(registration, pts) so the
        blend taper and the explicit coast-affine fallback live in one
        place. A single composed affine (or a tapered pair of affines)
        cannot fold -- no Delaunay hull, no simplex validity.
        """
        A = np.asarray(registration["affine_combined"],
                       dtype=np.float64)
        if A.shape != (2, 3) or not np.isfinite(A).all():
            raise ValueError("natural registration affine_combined "
                             "must be a finite 2x3, got %s" % (A.shape,))
        if np.linalg.det(A[:, :2]) <= 0:
            raise ValueError("natural registration affine_combined "
                             "det <= 0 (mirror/degenerate map)")
        return cls("natural", registration=registration)

    @classmethod
    def from_visual(cls, registration):
        """Coast affine + regularised local displacement field.

        `registration` is a visual_warp.visual_feature_warp() result
        dict; map_points() defers to visual_warp.visual_map_points().
        The field is sampled-guarded against folding at fit time and
        tapered to the coast affine outside its finite support --
        no hull, but a recorded field_jacobian_min_det.
        """
        A = np.asarray(registration["affine_me_to_vanilla"],
                       dtype=np.float64)
        if A.shape != (2, 3) or not np.isfinite(A).all():
            raise ValueError("visual registration affine_me_to_vanilla "
                             "must be a finite 2x3, got %s" % (A.shape,))
        if np.linalg.det(A[:, :2]) <= 0:
            raise ValueError("visual registration base affine det <= 0")
        return cls("visual", registration=registration)


    @classmethod
    def from_delaunay(cls, pts_me, pts_v):
        """Delaunay piecewise-affine ME px -> vanilla px (legacy vp mode).

        good_simplex[k] is False when simplex k's target triangle flips
        orientation (det(J) < 0, folded) or is degenerate (zero area,
        collapsed); pixels landing on such a simplex must not sample
        vanilla.
        """
        tri = Delaunay(pts_me)
        s = pts_me[tri.simplices]         # (K, 3, 2) source triangles
        d = pts_v[tri.simplices]          # (K, 3, 2) mapped triangles
        s_det = ((s[:, 1, 0] - s[:, 0, 0]) * (s[:, 2, 1] - s[:, 0, 1]) -
                 (s[:, 1, 1] - s[:, 0, 1]) * (s[:, 2, 0] - s[:, 0, 0]))
        d_det = ((d[:, 1, 0] - d[:, 0, 0]) * (d[:, 2, 1] - d[:, 0, 1]) -
                 (d[:, 1, 1] - d[:, 0, 1]) * (d[:, 2, 0] - d[:, 0, 0]))
        # det(J) sign = sign(d_det * s_det); Delaunay s_det != 0 always.
        good_simplex = (d_det * s_det) > 0  # False = folded or collapsed
        return cls("vp", tri=tri,
                   g=LinearNDInterpolator(tri, pts_v),
                   good_simplex=good_simplex, pts_me=pts_me)

    def map_points(self, pts):
        """(N,2) ME xy -> (N,2) vanilla xy (NaN outside the vp hull)."""
        if self.mode == "coastline":
            return pts @ self.affine[:, :2].T + self.affine[:, 2]
        if self.mode == "visual":
            from visual_warp import visual_map_points
            return visual_map_points(self.registration, pts)
        if self.mode == "natural":
            from continent_registration import map_points as cr_map
            return cr_map(self.registration, pts)

        return self.g(pts)

    def probe_bbox(self, me_size):
        """ME-side candidate search rect for --expand-candidates."""
        mw, mh = me_size
        if self.mode in ("coastline", "natural", "visual"):
            return [0, 0, mw, mh]
        return [max(0, int(np.floor(self.pts_me[:, 0].min()))),
                max(0, int(np.floor(self.pts_me[:, 1].min()))),
                min(mw, int(np.ceil(self.pts_me[:, 0].max())) + 1),
                min(mh, int(np.ceil(self.pts_me[:, 1].max())) + 1)]


def warp_sample(warp, v_state, xs, ys):
    """Backward warp: ME pixels (xs,ys) -> vanilla state ids.

    Returns (states int32, valid bool, folded bool). folded is always
    False in coastline, natural and visual mode (a composed affine or a
    fold-guarded tapered field cannot fold in the sampled sense).
    Validity:
      - coastline / natural / visual: the mapped point lands inside
        the vanilla raster (the only validity condition); outside the
        map frame -> invalid, never sampled.
      - vp: inside the control hull on a good (non-folded, non-degenerate)
        simplex; mapped points outside the vanilla image keep valid=True
        but get state -1.
    """

    pts = np.column_stack([xs, ys])
    mapped = warp.map_points(pts)
    finite = np.isfinite(mapped).all(axis=1)
    mapped = np.nan_to_num(mapped, nan=0.0, posinf=0.0, neginf=0.0)
    vh, vw = v_state.shape
    vx = np.round(mapped[:, 0]).astype(np.int64)
    vy = np.round(mapped[:, 1]).astype(np.int64)
    in_raster = (vx >= 0) & (vx < vw) & (vy >= 0) & (vy < vh)

    states = np.full(len(pts), -1, dtype=np.int32)
    folded = np.zeros(len(pts), dtype=bool)
    if warp.mode in ("coastline", "natural", "visual"):
        valid = finite & in_raster
    else:
        simp = warp.tri.find_simplex(pts)
        in_hull = simp >= 0
        folded[in_hull] = ~warp.good_simplex[simp[in_hull]]
        valid = in_hull & ~folded & finite
    inside = valid & in_raster
    states[inside] = v_state[vy[inside], vx[inside]]
    return states, valid, folded


def coarse_candidate_bbox(warp, v_state, bbox, cands, stride):
    """Strided probe of `bbox` for pixels mapping into `cands` -> bbox or None."""
    x0, y0, x1, y1 = bbox
    ys, xs = np.mgrid[y0:y1:stride, x0:x1:stride]
    states, _, _ = warp_sample(warp, v_state, xs.ravel(), ys.ravel())
    sel = np.isin(states, list(cands))
    if not sel.any():
        return None
    px, py = xs.ravel()[sel], ys.ravel()[sel]
    pad = stride + 1
    return (int(px.min()) - pad, int(py.min()) - pad,
            int(px.max()) + pad + 1, int(py.max()) + pad + 1)


# ------------------------------------------------- registration verification

def registration_check(docs, warp, pts_v, v_cent, m_cent):
    """Independent accuracy checks for the active warp map.

    Holdout = vps.tsv status=near rows: name-verified pairs excluded from
    the vp triangulation, and never seen by the coastline fit or the
    natural-feature correction at all (coastlines / river+lake features
    only -- no VP/state data feeds either). Each gives
    me_xy -> G(me_xy) vs the recorded vanilla_xy.

    NOTE: in vp mode the kept control points are NOT an accuracy measure -
    G is a linear interpolant and reproduces them exactly by construction.
    In vp mode also reports per-simplex Jacobian sanity of the
    piecewise-affine map: negative determinant = the affine map flips a
    triangle's orientation; zero = the target triangle collapses to a
    line/point.
    """
    n_rows, outside = 0, 0
    errs = []
    with open(os.path.join(docs, "vps.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["status"] != "near":
                continue
            if not (r["v_prov"].isdigit() and r["me_prov"].isdigit()):
                continue
            vp, mp = int(r["v_prov"]), int(r["me_prov"])
            if vp not in v_cent or mp not in m_cent:
                continue
            n_rows += 1
            pred = warp.map_points(
                np.asarray(m_cent[mp], dtype=np.float64).reshape(1, 2))
            if not np.isfinite(pred).all():
                outside += 1              # holdout point beyond the hull
                continue
            errs.append(np.linalg.norm(pred[0] - np.asarray(v_cent[vp])))

    out = {
        "holdout": {
            "source": "vps.tsv status=near (excluded from fit)",
            "rows": n_rows,
            "outside_hull": outside,
            "count": len(errs),
            "median_px": round(float(np.median(errs)), 2) if errs else None,
            "p90_px": round(float(np.percentile(errs, 90)), 2)
                      if errs else None,
            "max_px": round(float(np.max(errs)), 2) if errs else None,
        } if (n_rows or errs) else None,
    }
    if warp.mode == "vp":
        out["note"] = ("kept control points reproduce exactly (linear "
                       "interpolant) and are NOT an accuracy measure")
        s = warp.pts_me[warp.tri.simplices]   # (K, 3, 2) source triangles
        d = pts_v[warp.tri.simplices]         # (K, 3, 2) mapped triangles
        s_det = ((s[:, 1, 0] - s[:, 0, 0]) * (s[:, 2, 1] - s[:, 0, 1]) -
                 (s[:, 1, 1] - s[:, 0, 1]) * (s[:, 2, 0] - s[:, 0, 0]))
        d_det = ((d[:, 1, 0] - d[:, 0, 0]) * (d[:, 2, 1] - d[:, 0, 1]) -
                 (d[:, 1, 1] - d[:, 0, 1]) * (d[:, 2, 0] - d[:, 0, 0]))
        # det(J) sign = sign(d_det * s_det); Delaunay s_det != 0 always.
        out["triangles_negative_jacobian"] = \
            int(np.count_nonzero(d_det * s_det < 0))
        out["triangles_collapsed"] = int(np.count_nonzero(d_det == 0))
    elif warp.mode == "visual":
        vreg = warp.registration
        cc = vreg.get("candidate_conditioned")
        out["note"] = (
            "visual field used named-anchor + river/lake residuals%s; "
            "near-holdout rows are still independent of the field fit"
            % (" AND candidate-conditioned boundary hints (VISUAL AID, "
               "not identity evidence)" if cc else ""))
        out["affine_combined_det"] = float(np.linalg.det(
            np.asarray(vreg["affine_me_to_vanilla"],
                       dtype=np.float64)[:, :2]))
        out["field_jacobian_min_det"] = vreg.get(
            "field_jacobian_min_det")
        out["candidate_conditioned"] = bool(cc)
    elif warp.mode == "natural":
        out["note"] = ("neither the coast affine nor the local "
                       "river/lake correction saw any VP/state data; "
                       "the holdout above is a pure independent "
                       "evaluation")
        out["affine_combined_det"] = float(np.linalg.det(
            np.asarray(warp.registration["affine_combined"],
                       dtype=np.float64)[:, :2]))
    else:
        out["note"] = ("coastline affine never saw VP/state data; the "
                       "holdout above is a pure independent evaluation")
        out["affine_det"] = float(np.linalg.det(warp.affine[:, :2]))
    return out


# ------------------------------------------------------------------ rendering

def boundary_mask(mask):
    """True on pixels of `mask` that touch a different-or-outside pixel."""
    m = mask
    edge = np.zeros_like(m)
    edge[1:, :] |= m[1:, :] != m[:-1, :]
    edge[:-1, :] |= m[1:, :] != m[:-1, :]
    edge[:, 1:] |= m[:, 1:] != m[:, :-1]
    edge[:, :-1] |= m[:, 1:] != m[:, :-1]
    return edge & m

def _latin1(s):
    """PIL's default bitmap font is latin-1; degrade gracefully."""
    return s.encode("latin-1", "replace").decode("latin-1")


def blend(img, mask, rgb, alpha):
    img[mask] = (img[mask] * (1 - alpha) + np.asarray(rgb) * alpha)


def render(me_state, v_state, warp, crop, me_id, cand_ids, ancestor_id,
           v_names, me_name, crop_mode):
    """Backward-warp the crop and paint the overlay. Returns (img, stats)."""
    x0, y0, x1, y1 = crop
    h, w = y1 - y0, x1 - x0
    ys, xs = np.mgrid[y0:y1, x0:x1]
    xf, yf = xs.ravel(), ys.ravel()
    states = np.empty(xf.size, dtype=np.int32)
    valid = np.zeros(xf.size, dtype=bool)
    folded = np.zeros(xf.size, dtype=bool)
    CH = 1 << 22
    for i in range(0, xf.size, CH):
        states[i:i + CH], valid[i:i + CH], folded[i:i + CH] = warp_sample(
            warp, v_state, xf[i:i + CH], yf[i:i + CH])
    states = states.reshape(h, w)
    valid = valid.reshape(h, w)
    folded = folded.reshape(h, w)

    img = np.full((h, w, 3), 235, dtype=np.float64)          # base light gray
    img[~valid] = 205                                        # invalid (see legend)
    m_crop = me_state[y0:y1, x0:x1]
    edge = boundary_mask(m_crop >= 0) & (m_crop != me_id)
    img[edge] = (150, 150, 150)                              # province edges
    target = m_crop == me_id
    img[target] = (220, 30, 30)                              # target red base
    cand_px = {}
    overlap_px = {}
    yy, xx = np.indices(states.shape)
    dash = ((xx + yy) // 4 % 2) == 0
    for i, cid in enumerate(cand_ids):
        col = CAND_COLORS[i % len(CAND_COLORS)]
        m = states == cid
        cand_px[cid] = int(m.sum())
        overlap_px[cid] = int((m & target).sum())
        blend(img, m, col, 0.45)
        img[boundary_mask(m) & dash] = np.asarray(col) * 0.55
    img[boundary_mask(target)] = (120, 0, 0)                 # target edge on top

    # Controls and the hull remain in JSON; neither is a state boundary.
    pil = Image.fromarray(img.astype(np.uint8))

    # legend strip
    if warp.mode == "coastline":
        method_tag = "continent coastline affine"
        invalid_legend = ("dark gray = INVALID: affine maps the pixel "
                          "outside the vanilla map frame (no sampling)")
        expand_legend = ("crop = candidate pixels over the ME map + pad; "
                         "candidate fills still clip at the crop edge")
    elif warp.mode == "natural":
        method_tag = ("continent coastline + local river/lake "
                      "correction")
        invalid_legend = ("dark gray = INVALID: warp maps the pixel "
                          "outside the vanilla map frame (no sampling); "
                          "not a candidate-screening signal")
        expand_legend = ("crop = candidate pixels over the ME map + pad; "
                         "candidate fills still clip at the crop edge")
    elif warp.mode == "visual":
        method_tag = ("coast affine + local displacement field "
                      "(VISUAL AID - not identity evidence)")
        invalid_legend = ("dark gray = INVALID: warp maps the pixel "
                          "outside the vanilla map frame (no sampling); "
                          "not a candidate-screening signal")
        expand_legend = ("crop = candidate pixels over the ME map + pad; "
                         "candidate fills still clip at the crop edge")
    else:
        method_tag = "city VP legacy Delaunay hull only"
        invalid_legend = ("dark gray = INVALID: outside control hull or "
                          "folded/degenerate simplex (no vanilla sampling)")
        expand_legend = ("crop = candidate pixels inside hull + pad; "
                         "candidate fills still clip at the crop edge")

    lines = [(None, "ME %d %s -> vanilla candidates "
                    "(backward warp, %s)" % (me_id, me_name, method_tag))]
    for i, cid in enumerate(cand_ids):
        tag = "  [claimed ancestor]" if cid == ancestor_id else ""
        lines.append((CAND_COLORS[i % len(CAND_COLORS)],
                      "vanilla %d %s%s - %d px (%d overlap)" %
                      (cid, v_names.get(cid, "?"), tag, cand_px[cid],
                       overlap_px[cid])))
    frac = valid.sum() / valid.size
    n_fold = int(folded.sum())
    n_out = int((~valid).sum()) - n_fold
    lines += [
        (None, "crop_mode=%s: %s" % (
            crop_mode,
            expand_legend if crop_mode == "context-expanded" else
            "crop = target ME bbox + --padding; candidate fills are "
            "clipped to this window, NOT full candidate extent")),
         ((220, 30, 30), "solid red base = target ME state; candidates "
                        "blend on top; dark red = target boundary"),
        ((205, 205, 205), invalid_legend),
    ]
    if warp.mode == "coastline":
        lines += [
            (None, "invalid split: %d px outside vanilla map frame "
                   "(folded: %d)" % (n_out, n_fold)),
            (None, "raster coverage: %d/%d px inside vanilla map "
                   "(%.1f%%) | one global 2x3 affine (continent coastline "
                   "fit)" % (valid.sum(), valid.size, 100 * frac)),
        ]
        coverage = {
            "valid_fraction_of_crop": round(float(frac), 6),
            "crop_px": int(valid.size),
            "invalid_meaning": "affine maps the pixel outside the vanilla "
                               "raster (map frame); a single affine has "
                               "no hull and no folded simplices",
            "outside_raster_pixels": n_out,
            "folded_invalid_pixels": n_fold,
        }
    elif warp.mode == "natural":
        reg = warp.registration
        fb = reg.get("fallback_reason")
        used = reg.get("method_used")
        corr_line = (
            "natural correction FALLBACK to coast affine: %s" % fb
            if fb else
            "natural correction: method=%s, %d matched river/lake "
            "pairs, radius %.0f ME px around target centroid"
            % (used, reg.get("n_pairs_kept", 0),
               reg.get("radius_me_px", 0.0)))
        lines += [
            (None, "invalid split: %d px outside vanilla map frame "
                   "(folded: %d)" % (n_out, n_fold)),
            (None, corr_line),
            (None, "raster coverage: %d/%d px inside vanilla map "
                   "(%.1f%%) | coastline affine + local river/lake "
                   "correction (tapered beyond radius)"
                   % (valid.sum(), valid.size, 100 * frac)),
        ]
        coverage = {
            "valid_fraction_of_crop": round(float(frac), 6),
            "crop_px": int(valid.size),
            "invalid_meaning": "warp maps the pixel outside the vanilla "
                               "raster (map frame); composed affines "
                               "have no hull and no folded simplices",
            "outside_raster_pixels": n_out,
            "folded_invalid_pixels": n_fold,
            "natural_method_used": used,
            "natural_fallback_reason": fb,
            "natural_radius_me_px": reg.get("radius_me_px"),
            "natural_center_me_xy": reg.get("center_me_xy"),
        }
    elif warp.mode == "visual":
        reg = warp.registration
        fb = reg.get("fallback_reason")
        ev = reg.get("evidence", {})
        counts = ", ".join("%s=%d" % (k, v.get("n_pairs", 0))
                           for k, v in sorted(ev.items()))
        fld = ("visual field FALLBACK to coast affine: %s" % fb) if fb \
            else ("visual field: pairs[%s], min det %.4f of affine "
                  "field over support" % (
                      counts, reg.get("field_jacobian_min_det") or 0.0))
        lines += [
            (None, "invalid split: %d px outside vanilla map frame "
                   "(folded: %d)" % (n_out, n_fold)),
            (None, fld),
            (None, "VISUAL AID ONLY: field alignment incl. %s is not "
                   "identity proof" % (
                       "candidate-conditioned boundary evidence"
                       if reg.get("candidate_conditioned")
                       else "named-anchor residuals")),
            (None, "raster coverage: %d/%d px inside vanilla map "
                   "(%.1f%%) | coast affine + tapered displacement "
                   "field (finite support)" %
                   (valid.sum(), valid.size, 100 * frac)),
        ]
        coverage = {
            "valid_fraction_of_crop": round(float(frac), 6),
            "crop_px": int(valid.size),
            "invalid_meaning": "warp maps the pixel outside the vanilla "
                               "raster (map frame); tapered field has "
                               "no hull and no folded simplices",
            "outside_raster_pixels": n_out,
            "folded_invalid_pixels": n_fold,
            "visual_method_used": reg.get("method_used"),
            "visual_fallback_reason": fb,
            "visual_radius_me_px": reg.get("radius_me_px"),
            "visual_center_me_xy": reg.get("center_me_xy"),
            "visual_jacobian_min_det": reg.get(
                "field_jacobian_min_det"),
            "visual_candidate_conditioned": reg.get(
                "candidate_conditioned"),
            "visual_evidence_pairs":
                {k: v.get("n_pairs") for k, v in ev.items()},
        }
    else:
        lines += [
            (None, "invalid split: %d px outside hull + %d px "
                   "folded/degenerate" % (n_out, n_fold)),
            (None, "hull coverage: %d/%d px valid (%.1f%%) | controls %d | "
                   "triangles %d (folded/degenerate %d)" %
                   (valid.sum(), valid.size, 100 * frac,
                    len(warp.pts_me), warp.tri.nsimplex,
                    int((~warp.good_simplex).sum()))),
        ]
        coverage = {
            "valid_fraction_of_crop": round(float(frac), 6),
            "crop_px": int(valid.size),
            "invalid_meaning": "outside control hull OR on a simplex whose "
                               "affine map is folded (negative Jacobian) "
                               "or degenerate (zero-area target)",
            "outside_hull_pixels": n_out,
            "folded_invalid_pixels": n_fold,
            "folded_or_degenerate_triangles":
                int((~warp.good_simplex).sum()),
            "hull_bbox_me_px": [float(warp.pts_me[:, 0].min()),
                                float(warp.pts_me[:, 1].min()),
                                float(warp.pts_me[:, 0].max()),
                                float(warp.pts_me[:, 1].max())],
            "n_triangles": int(warp.tri.nsimplex),
        }
    lh = 6 + 14 * len(lines) + 6
    out = Image.new("RGB", (w, h + lh), (250, 250, 250))
    out.paste(pil, (0, 0))
    dr = ImageDraw.Draw(out)
    for i, (col, text) in enumerate(lines):
        y = h + 6 + 14 * i
        if col:
            dr.rectangle([6, y + 1, 16, y + 10], fill=tuple(int(c) for c in col),
                         outline=(0, 0, 0))
            dr.text((22, y), _latin1(text), fill=(0, 0, 0))
        else:
            dr.text((6, y), _latin1(text), fill=(0, 0, 0))
    target_pixels = int(target.sum())
    return out, {
        "valid_pixels": int(valid.sum()),
        "folded_invalid_pixels": n_fold,
        "candidate_pixels": cand_px,
        "target_pixels": target_pixels,
        "target_overlap_pixels": overlap_px,
        "target_overlap_fraction": {
            cid: round(overlap_px[cid] / target_pixels, 6)
            if target_pixels else None
            for cid in cand_ids
        },
        "hull_coverage": coverage,
    }


# ------------------------------------------------------------------------ CLI

# --------------------------------------------- visual-selection metrics


def boundary_agreement_metrics(warp, v_state, m_state, me_id, cand_ids,
                               me2v=None, target_ancestor=None):
    """Shared-boundary agreement metrics for the ACTIVE warp.

    Two levels:

    1. "candidates": for every candidate vanilla state, distance from
       each mapped target boundary pixel to the nearest candidate
       boundary pixel (vanilla px) + frac within 4px + fraction of
       mapped target boundary pixels landing inside the candidate.
       Whole-boundary view; includes target edges that never faced
       this candidate, so it is a coarse proxy.
    2. "shared_segments" (needs me2v): for each ME neighbour nb of the
       target, measure the ME border against the vanilla border between
       the selected target_ancestor (when supplied) or me2v[target] and
       me2v[nb]. This is the real "do shared borders coincide" quantity:
       it only counts pixels of a border that is
       actually shared on the ME side, against only the vanilla
       segment that is supposed to match it. If the two adjudicated
       ancestors are NOT adjacent on the vanilla map, the pair is
       reported with adjacent_on_vanilla=False -- a finding on its
       own (the mapping predicts a border vanilla does not draw).

    VISUAL-SELECTION metrics: they compare pictures under each warp
    mode, are not strict pass/fail identity tests, and for
    candidate-conditioned warps partly measure the hint the warp was
    given (see warp.registration["candidate_conditioned"]).
    """
    from visual_warp import (state_boundary_points,
                             me_state_neighbours)
    out = {"candidates": {}, "shared_segments": []}
    m_bnd = state_boundary_points(m_state, me_id, grid_cell=2)
    if not len(m_bnd):
        return out
    mapped = warp.map_points(m_bnd)
    ok = np.isfinite(mapped).all(axis=1)
    vh, vw = v_state.shape
    vx = np.clip(np.round(mapped[ok, 0]).astype(np.int64), 0, vw - 1)
    vy = np.clip(np.round(mapped[ok, 1]).astype(np.int64), 0, vh - 1)
    landed_state = v_state[vy, vx]
    for cid in cand_ids:
        v_bnd = state_boundary_points(v_state, cid, grid_cell=2)
        if not len(v_bnd):
            continue
        d, _ = cKDTree(v_bnd).query(mapped[ok])
        out["candidates"][int(cid)] = {
            "n_mapped_boundary": int(ok.sum()),
            "shared_chamfer_p50_px": round(
                float(np.percentile(d, 50)), 2),
            "shared_chamfer_p95_px": round(
                float(np.percentile(d, 95)), 2),
            "frac_within_4px": round(float((d <= 4.0).mean()), 4),
            "target_boundary_px_landing_in_candidate": round(
                float((landed_state == cid).mean()), 4),
        }
    if me2v is None:
        return out
    a_t = (target_ancestor if target_ancestor is not None
           else me2v.get(me_id))
    if a_t is None:
        return out
    from scipy.ndimage import binary_dilation
    t_mask = m_state == me_id
    for nb in me_state_neighbours(m_state, me_id):
        seg = {"me_neighbour": int(nb), "v_neighbour": me2v.get(nb),
               "v_target": int(a_t)}
        a_nb = me2v.get(nb)
        # ME-side pixels of the target adjacent to this neighbour
        nb_mask = m_state == nb
        seg_px = t_mask & binary_dilation(
            nb_mask, structure=np.ones((3, 3), bool))
        ys, xs = np.nonzero(seg_px)
        seg["me_segment_px"] = int(len(xs))
        if not len(xs):
            seg["note"] = "no raster adjacency (impossible?)"
            out["shared_segments"].append(seg)
            continue
        mseg = np.stack([xs, ys], axis=1).astype(np.float64)
        mseg = warp.map_points(mseg)
        fin = np.isfinite(mseg).all(axis=1)
        if a_nb is None or a_nb not in set(
                np.unique(v_state[v_state >= 0]).tolist()):
            seg["adjacent_on_vanilla"] = None
            seg["note"] = "neighbour has no adjudicated vanilla ancestor"
            out["shared_segments"].append(seg)
            continue
        if int(a_nb) == int(a_t):
            # Internal divider: both sides adjudicate to the same
            # vanilla state, so vanilla has NO counterpart border.
            # Comparing the mapped ME segment against the state raster
            # (a==a everywhere) would fake a "shared border"; report
            # the finding instead.
            seg["adjacent_on_vanilla"] = False
            seg["note"] = ("internal divider: ME %d and %d both "
                           "adjudicate to vanilla %d - vanilla draws "
                           "no border here" % (me_id, nb, a_t))
            out["shared_segments"].append(seg)
            continue
        a_mask = v_state == int(a_t)
        nb_mask_v = v_state == int(a_nb)
        v_seg = a_mask & binary_dilation(
            nb_mask_v, structure=np.ones((3, 3), bool)) | \
            nb_mask_v & binary_dilation(
                a_mask, structure=np.ones((3, 3), bool))
        vys, vxs = np.nonzero(v_seg)
        if not len(vxs):
            seg["adjacent_on_vanilla"] = False
            seg["note"] = ("ancestors %d and %d share no vanilla border"
                           " - the adjudicated mapping predicts a ME "
                           "border with no vanilla counterpart"
                           % (a_t, a_nb))
            out["shared_segments"].append(seg)
            continue
        seg["adjacent_on_vanilla"] = True
        vb = np.stack([vxs, vys], axis=1).astype(np.float64)
        d, _ = cKDTree(vb).query(mseg[fin])
        seg["shared_chamfer_p50_px"] = round(
            float(np.percentile(d, 50)), 2)
        seg["shared_chamfer_p95_px"] = round(
            float(np.percentile(d, 95)), 2)
        seg["frac_within_4px"] = round(float((d <= 4.0).mean()), 4)
        out["shared_segments"].append(seg)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--me-id", type=int, required=True, help="ME state id")
    ap.add_argument("--candidates", default="",
                    help="comma-separated vanilla state ids; default = "
                         "review_queue.tsv row (ancestor + neighbour v-ids)")
    ap.add_argument("--output", default="",
                    help="PNG path (json gets same stem); default "
                         "tools/out/state_overlay_<me>.png")
    ap.add_argument("--padding", type=int, default=64,
                    help="crop padding px around the ME target bbox")
    ap.add_argument("--vanilla", default=VANILLA, help="vanilla game dir")
    ap.add_argument("--warp",
                    choices=("natural", "coastline", "vp", "visual"),
                    default="natural",
                    help="ME->vanilla map: 'natural' = coastline affine "
                         "+ local river/lake correction around the "
                         "target state (default); 'coastline' = one "
                         "global 2x3 affine fitted on continent "
                         "coastlines only; 'vp' = legacy Delaunay "
                         "piecewise-affine over city-VP control points; "
                         "'visual' = coast affine + regularised "
                         "displacement field from named-anchor, "
                         "river/lake and (optionally) adjudicated "
                         "boundary residuals -- VISUAL AID, never "
                         "identity evidence")
    ap.add_argument("--natural-radius", type=float, default=500.0,
                    help="natural/visual warp: match-region radius in "
                         "ME px around the target state's pixel "
                         "centroid (default 500); the correction/field "
                         "tapers to the coast affine beyond it")
    ap.add_argument("--visual-boundary", default="neighbours",
                    choices=("off", "ancestor", "neighbours"),
                    help="visual warp only: boundary-evidence class. "
                         "'neighbours' (default) matches the target's "
                         "AND raster-adjacent ME neighbours' boundary "
                         "pixels to their adjudicated ancestors; "
                         "'ancestor' = target only; 'off' = named "
                         "anchors + rivers/lakes only. All boundary "
                         "evidence is candidate-conditioned: a visual "
                         "aid, never identity proof")
    ap.add_argument("--visual-ancestor", type=int, default=None,
                    help="visual warp only: condition boundary hints on this "
                         "vanilla ancestor instead of the canonical one; "
                         "use once per candidate for a fair visual comparison")
    ap.add_argument("--visual-lambda", type=float, default=2.0,
                    help="visual warp: graph-Laplacian smoothing weight "
                         "of the displacement lattice (default 2.0; "
                         "higher = flatter field)")
    ap.add_argument("--visual-cell", type=float, default=0.0,
                    help="visual warp: displacement lattice cell in ME "
                         "px (default 0 = radius/10)")

    ap.add_argument("--coast-warp-json", default="",
                    help="natural/coastline warp: load a precomputed "
                         "continent_registration fit (JSON containing "
                         "affine_me_to_vanilla) instead of fitting "
                         "live with fit_coast_warp(initial='auto')")
    ap.add_argument("--expand-candidates", action="store_true",
                    help="also probe the map domain (vp: control hull; "
                         "coastline/natural: whole ME map) for "
                         "candidate pixels and grow the crop to cover "
                         "them (legacy behaviour; default crops to the "
                         "ME target bbox + --padding only)")
    ap.add_argument("--mod", default="", help="mod dir (default: _modroot)")
    args = ap.parse_args()

    if args.coast_warp_json and args.warp == "vp":
        ap.error("--coast-warp-json only applies to --warp "
                 "natural/coastline/visual")


    mod = args.mod or mod_root()
    repo = repo_root()
    docs = os.path.join(repo, "docs", "states")
    me_id = args.me_id
    if args.candidates.strip():
        cand_ids = [int(x) for x in args.candidates.split(",") if x.strip()]
        ancestor = args.visual_ancestor
    else:
        cand_ids = default_candidates(docs, me_id)
        ancestor = cand_ids[0] if cand_ids else None
        if not cand_ids:
            sys.exit("no --candidates and ME %d not in review_queue.tsv" % me_id)
    output = args.output or os.path.join(
        repo, "tools", "out", "state_overlay_%d.png" % me_id)

    cache = {}
    cpath = os.path.join(repo, "_ctx", "cents.json")
    if os.path.isfile(cpath):
        cache = json.load(open(cpath, encoding="utf-8"))

    def rasters(root):
        defn = load_definition(os.path.join(root, "map", "definition.csv"))
        prov = province_raster(os.path.join(root, "map", "provinces.bmp"), defn)
        provs = load_state_provinces(os.path.join(root, "history", "states"))
        return prov, state_raster(prov, provs)

    v_prov, v_state = rasters(args.vanilla)
    m_prov, m_state = rasters(mod)
    v_cent = province_centroids(v_prov, cache, "vc")
    m_cent = province_centroids(m_prov, cache, "mc")

    me2v, me_names = load_m2v(docs)
    v_names = load_vanilla_names(docs)

    # Locate the target BEFORE the warp: natural mode centres its local
    # correction on the target state's pixel centroid, so the warp
    # cannot be frozen until m_state is known. The correction fit itself
    # only sees river/lake feature points -- never the state raster;
    # the centroid is a region selector, not fit input.
    mh, mw = m_state.shape
    tgt = np.argwhere(m_state == me_id)
    if not tgt.size:
        sys.exit("ME state %d has no rasterized provinces" % me_id)
    center = (float(tgt[:, 1].mean()), float(tgt[:, 0].mean()))

    # Freeze the warp BEFORE any candidate/sampling work. Coastline and
    # natural mode share the same frozen coast affine (fitted on
    # continent coastline points only -- vps.tsv, anchors, state
    # rasters/history and the review candidates never enter the fit);
    # natural then composes ONE local river/lake correction on top.
    # Candidates are consumed only by the crop probe and the render
    # sampling below.
    reg_cache = {}
    coast_reg = None
    natural_reg = None
    topology_reg = None
    pts_v = None
    kept, dropped = [], []
    visual_reg = None
    if args.warp in ("coastline", "natural", "visual"):
        from continent_registration import (
            fit_coast_warp, natural_feature_correction,
            natural_feature_topology)
        if args.coast_warp_json:
            with open(args.coast_warp_json, encoding="utf-8") as f:
                coast_reg = json.load(f)
            try:
                WarpMap.from_affine(coast_reg["affine_me_to_vanilla"])
            except (KeyError, ValueError) as e:
                sys.exit("--coast-warp-json: %s" % e)
            coast_reg.setdefault("source", args.coast_warp_json)
            coast_reg["affine_det"] = float(np.linalg.det(np.asarray(
                coast_reg["affine_me_to_vanilla"],
                dtype=np.float64)[:, :2]))
        else:
            coast_reg = fit_coast_warp(args.vanilla, mod, initial="auto",
                                       cache=reg_cache)
            coast_reg["source"] = "fit_coast_warp(initial=auto)"
            if not coast_reg.get("ok"):
                sys.exit("coastline fit unusable: "
                         "det_abort=%s converged=%s matches=%s" %
                         (coast_reg.get("det_abort"),
                          coast_reg.get("converged"),
                          coast_reg.get("n_matches")))
        if args.warp == "coastline":
            topology_reg = {
                "coast": natural_feature_topology(
                    args.vanilla, mod, coast_reg, center,
                    args.natural_radius, cache=reg_cache)}
            topology_reg["coast"]["basis"] = "coast_affine"
            try:
                warp = WarpMap.from_affine(
                    coast_reg["affine_me_to_vanilla"])
            except ValueError as e:
                sys.exit("coastline fit unusable: %s" % e)
        elif args.warp == "natural":
            natural_reg = natural_feature_correction(
                args.vanilla, mod, coast_reg["affine_me_to_vanilla"],
                center, args.natural_radius, cache=reg_cache)
            natural_reg["source"] = (
                "natural_feature_correction(center=target centroid, "
                "radius=%s)" % args.natural_radius)
            topology_reg = {
                "coast": natural_feature_topology(
                    args.vanilla, mod, coast_reg, center,
                    args.natural_radius, cache=reg_cache),
                "natural": natural_feature_topology(
                    args.vanilla, mod, natural_reg, center,
                    args.natural_radius, cache=reg_cache),
            }
            topology_reg["coast"]["basis"] = "coast_affine"
            topology_reg["natural"]["basis"] = "natural_affine"
            try:
                warp = WarpMap.from_natural(natural_reg)
            except (KeyError, ValueError) as e:
                sys.exit("natural correction unusable: %s" % e)
            if natural_reg.get("fallback_reason"):
                print("warning: natural correction fell back to the "
                      "coast affine (%s)"
                      % natural_reg["fallback_reason"], file=sys.stderr)
        elif args.warp == "visual":
            from visual_warp import (
                visual_feature_warp, state_boundary_points,
                me_state_neighbours, shared_segment)
            bnd = None
            if args.visual_boundary != "off":
                bnd = []
                a_t = (args.visual_ancestor if
                       args.visual_ancestor is not None else
                       me2v.get(me_id))
                if a_t is None:
                    print("warning: ME %d has no adjudicated ancestor; "
                          "--visual-boundary %s degrades to anchors + "
                          "rivers/lakes only" % (me_id,
                          args.visual_boundary), file=sys.stderr)
                else:
                    nb_ids = me_state_neighbours(m_state, me_id) \
                        if args.visual_boundary == "neighbours" else []
                    m2v_basis = load_m2v_basis(docs)
                    _warp_derived = {"vp-geo", "pixel-geo",
                                     "pixel-geo-low"}
                    def _prov(a, b):
                        bs = (m2v_basis.get(a), m2v_basis.get(b))
                        return ("warp_derived" if any(
                            x in _warp_derived for x in bs)
                            else "independent")

                    for nb in nb_ids:
                        a_nb = me2v.get(nb)
                        if a_nb is None:
                            continue
                        if int(a_nb) == int(a_t):
                            # ME internal divider: the adjudicated
                            # ancestors are the same vanilla state, so
                            # vanilla draws no border here. Skipping is
                            # REQUIRED - shared_segment(a,a) would
                            # degenerate to the whole-state raster and
                            # inject a bogus "boundary" pair.
                            print("info: neighbour ME %d shares the "
                                  "ancestor %d with the target - "
                                  "internal divider contributes no "
                                  "vanilla boundary evidence"
                                  % (nb, a_nb), file=sys.stderr)
                            continue
                        mseg = shared_segment(m_state, me_id, nb)
                        vseg = shared_segment(v_state, a_t, a_nb)
                        if len(mseg) and len(vseg):
                            bnd.append({
                                "me_xy": mseg, "v_xy": vseg,
                                "label": {"me": [me_id, nb],
                                          "v": [a_t, a_nb],
                                          "provenance": _prov(
                                              me_id, nb),
                                          "kind": "shared_segment"}})
                        elif len(mseg):
                            print("info: ancestors %d/%d share no "
                                  "vanilla border - segment for ME "
                                  "%d/%d contributes no evidence"
                                  % (a_t, a_nb, me_id, nb),
                                  file=sys.stderr)
                    # also the target's outer silhouette against the
                    # ancestor's, so non-neighbour border arcs still
                    # get boundary pull. Candidate-conditioned visual
                    # aid ONLY: the pairing is adjudication-derived,
                    # never identity evidence; the label carries the
                    # basis provenance and visual_warp stamps every
                    # kept pair candidate_conditioned=True.
                    mt = state_boundary_points(m_state, me_id)
                    vt = state_boundary_points(v_state, a_t)
                    bnd.append({
                        "me_xy": mt, "v_xy": vt,
                        "label": {"me": [me_id], "v": [a_t],
                                  "provenance": _prov(me_id, me_id),
                                  "kind": "state_silhouette"}})
            visual_reg = visual_feature_warp(
                args.vanilla, mod, coast_reg["affine_me_to_vanilla"],
                center, args.natural_radius, cache=reg_cache,
                boundary_pairs=bnd, lam_smooth=args.visual_lambda,
                cell_px=args.visual_cell or None)
            visual_reg["source"] = (
                "visual_feature_warp(center=target centroid, "
                "radius=%s, boundary=%s)"
                % (args.natural_radius, args.visual_boundary))
            visual_reg["conditioning_ancestor"] = (
                a_t if args.visual_boundary != "off" else None)
            visual_reg["conditioning_source"] = (
                "cli" if args.visual_boundary != "off" and
                args.visual_ancestor is not None
                else "canonical_m2v")
            topology_reg = {
                "coast": natural_feature_topology(
                    args.vanilla, mod, coast_reg, center,
                    args.natural_radius, cache=reg_cache)}
            topology_reg["coast"]["basis"] = "coast_affine"
            try:
                warp = WarpMap.from_visual(visual_reg)
            except (KeyError, ValueError) as e:
                sys.exit("visual warp unusable: %s" % e)
            if visual_reg.get("fallback_reason"):
                print("warning: visual field fell back to the coast "
                      "affine (%s)"
                      % visual_reg["fallback_reason"], file=sys.stderr)
    else:
        pts_me, pts_v, kept, dropped = load_controls(
            docs, me2v, v_cent, m_cent)
        if len(kept) < 3:
            sys.exit("only %d clean control points - cannot triangulate"
                     % len(kept))
        warp = WarpMap.from_delaunay(pts_me, pts_v)

    pad = args.padding
    crop = [max(0, int(tgt[:, 1].min()) - pad), max(0, int(tgt[:, 0].min()) - pad),
            min(mw, int(tgt[:, 1].max()) + pad + 1),
            min(mh, int(tgt[:, 0].max()) + pad + 1)]

    # Default: target-only crop. With --expand-candidates, also grow the
    # crop so candidate regions in the warp's ME-side domain are not
    # clipped (vp: the control hull; coastline/natural: the whole ME map).
    crop_mode = "context-expanded" if args.expand_candidates else "target-only"
    if args.expand_candidates:
        probe = warp.probe_bbox((mw, mh))
        stride = max(2, int(np.ceil(max(probe[2] - probe[0],
                                        probe[3] - probe[1]) / 512)))
        cb = coarse_candidate_bbox(warp, v_state, probe, set(cand_ids),
                                   stride)
        if cb:
            crop = [min(crop[0], max(0, cb[0] - pad // 4)),
                    min(crop[1], max(0, cb[1] - pad // 4)),
                    max(crop[2], min(mw, cb[2] + pad // 4)),
                    max(crop[3], min(mh, cb[3] + pad // 4))]

    img, stats = render(m_state, v_state, warp, crop, me_id, cand_ids,
                        ancestor, v_names, me_names.get(me_id, "?"),
                        crop_mode)
    reg = registration_check(docs, warp, pts_v, v_cent, m_cent)
    bnd_metrics = boundary_agreement_metrics(
        warp, v_state, m_state, me_id, cand_ids, me2v,
        target_ancestor=(args.visual_ancestor
                         if args.warp == "visual" and
                         args.visual_boundary != "off" else None))

    if not output.lower().endswith(".png"):
        output += ".png"
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    img.save(output)
    if warp.mode == "coastline":
        mapping_method = (
            "global 2x3 affine backward raster warp (ME px -> vanilla "
            "px) fitted on continent=1 land<->sea coastlines "
            "(tools/continent_registration.py, trimmed nearest-coast "
            "affine ICP); valid only where the affine maps inside the "
            "vanilla raster; no hull, no folded simplices")
    elif warp.mode == "natural":
        mapping_method = (
            "continent coastline affine + ONE local natural-feature "
            "correction (rivers.bmp palette 0-11 + type=lake "
            "shorelines, mutual-NN + cap + MAD; tapered to the coast "
            "affine beyond --natural-radius) backward raster warp "
            "(ME px -> vanilla px); valid only where the composed map "
            "lands inside the vanilla raster; no hull, no folded "
            "simplices")
    elif warp.mode == "visual":
        mapping_method = (
            "continent coastline affine + regularised bilinear-lattice "
            "displacement field fitted to named-anchor + river/lake "
            "residuals%s, smoothstep-tapered to the coast affine at "
            "radius*blend; sampled Jacobian fold guard; backward "
            "raster warp (ME px -> vanilla px). VISUAL AID: alignment "
            "quality is not evidence for or against any state "
            "mapping" % (" and candidate-conditioned state-boundary "
                         "residuals" if visual_reg and visual_reg.get(
                             "candidate_conditioned") else ""))
    else:
        mapping_method = (
            "delaunay-piecewise-affine backward raster warp "
            "(ME px -> vanilla px); valid only inside the "
            "control hull on non-folded, non-degenerate "
            "simplices; no extrapolation")
    meta = {
        "warp_mode": warp.mode,
        "mapping_method": mapping_method,
        "coast_registration": coast_reg,
        "natural_registration": natural_reg,
        "visual_registration": visual_reg,
        "topology_registration": topology_reg,
        "target": {"me_state": me_id, "name": me_names.get(me_id)},
        "candidates": [{"vanilla_state": c, "name": v_names.get(c),
                        "pixels": stats["candidate_pixels"].get(c, 0),
                        "target_overlap_pixels":
                            stats["target_overlap_pixels"].get(c, 0),
                        "target_overlap_fraction":
                            stats["target_overlap_fraction"].get(c)}
                       for c in cand_ids],
        "registration": reg,
        "boundary_metrics": bnd_metrics,
        "hull_coverage": stats["hull_coverage"],
        "valid_pixels": stats["valid_pixels"],
        "folded_invalid_pixels": stats["folded_invalid_pixels"],
        "candidate_pixels": stats["candidate_pixels"],
        "target_pixels": stats["target_pixels"],
        "target_overlap_pixels": stats["target_overlap_pixels"],
        "target_overlap_fraction": stats["target_overlap_fraction"],
        "crop_me_px": crop,
        "crop_mode": crop_mode,
        "padding": pad,
    }
    if warp.mode == "vp":
        meta["controls"] = {
            "kept": len(kept),
            "dropped": len(dropped),
            "pairs": [
                {"v_state": int(r["v_state"]), "v_prov": int(r["v_prov"]),
                 "me_state": int(r["me_state"]), "me_prov": int(r["me_prov"]),
                 "vp_name": r["vp_name"],
                 "me_xy": [round(float(c), 2)
                           for c in m_cent[int(r["me_prov"])]],
                 "v_xy": [round(float(c), 2)
                          for c in v_cent[int(r["v_prov"])]]}
                for r in kept],
            "dropped_detail": [
                {"v_prov": int(r["v_prov"]), "me_prov": int(r["me_prov"]),
                 "vp_name": r["vp_name"], "reason": r["_drop"]}
                for r in dropped],
        }
    jpath = os.path.splitext(output)[0] + ".json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    if warp.mode == "vp":
        print("controls kept=%d dropped=%d | crop=%s | valid=%.1f%% | "
              "cands=%s" % (len(kept), len(dropped), crop,
                            100 * stats["hull_coverage"]
                            ["valid_fraction_of_crop"],
                            stats["candidate_pixels"]))
    elif warp.mode == "natural":
        fb = natural_reg.get("fallback_reason")
        print("warp=natural method=%s%s | crop=%s | valid=%.1f%% | "
              "cands=%s" % (
                  natural_reg.get("method_used"),
                  " FALLBACK: %s" % fb if fb else "",
                  crop, 100 * stats["hull_coverage"]
                  ["valid_fraction_of_crop"],
                  stats["candidate_pixels"]))
    elif warp.mode == "visual":
        fb = visual_reg.get("fallback_reason")
        ev = visual_reg.get("evidence", {})
        print("warp=visual method=%s%s | pairs=%s | minJac=%s | "
              "crop=%s | valid=%.1f%% | cands=%s" % (
                  visual_reg.get("method_used"),
                  " FALLBACK: %s" % fb if fb else "",
                  {k: v.get("n_pairs") for k, v in ev.items()},
                  visual_reg.get("field_jacobian_min_det"),
                  crop, 100 * stats["hull_coverage"]
                  ["valid_fraction_of_crop"],
                  stats["candidate_pixels"]))
    else:
        print("warp=coastline affine det=%.6f | crop=%s | valid=%.1f%% | "
              "cands=%s" % (np.linalg.det(warp.affine[:, :2]), crop,
                            100 * stats["hull_coverage"]
                            ["valid_fraction_of_crop"],
                            stats["candidate_pixels"]))
    print("wrote %s + %s" % (output, jpath))


if __name__ == "__main__":
    main()
