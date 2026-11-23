#!/usr/bin/env python
"""Coastline-driven ME -> vanilla global affine registration.

Reviewable replacement for the VP-Delaunay warp used by
render_state_overlay.py: instead of trusting ~560 province anchor
correspondences, we register the two maps by their *coastlines* and use
the anchors (if at all) only as a warm start.

Method, end to end:

  1. For each side (vanilla game dir, mod dir) read map/definition.csv
     and map/provinces.bmp. Province id 0 is excluded explicitly.
  2. From definition.csv alone:
       - "land1" = rows with type=land AND continent=1
       - "sea"   = rows with type=sea
     Note: the continent column is a landmass tag written into
     definition.csv by the map authors. continent=1 is simply "the
     landmass both maps label 1" (the Europe-centred continent on both
     sides). It is NOT a hand-drawn Ural boundary and is not corrected
     to any historical geographic convention -- the fit only sees the
     land<->sea outline of whatever landmass the tag selects.
     Lakes (type=lake, e.g. the Caspian) are neither land1 nor sea, so
     their shores never participate; the same rule applies on both
     maps, which keeps the silhouette comparison symmetric.
  3. Coastline = the union of (a) land1 pixels 8-adjacent to a sea
     province and (b) sea pixels 8-adjacent to a land1 province.
     This is the outer land<->sea silhouette only: state borders,
     province borders, rivers, VPs and cities never contribute a
     point. Pixels within EDGE_MARGIN px of an image edge are dropped
     (a map edge is a crop artefact, not a coastline). The surviving
     pixels are downsampled onto a fixed GRID_CELL grid (cell mean).
  4. fit_coast_warp() runs trimmed nearest-neighbour ICP: transform ME
     coast points by the current affine, keep only matches within
     MAX_DIST px (then the best TRIM_FRACTION of those), refit a
     least-squares affine on the surviving pairs, iterate to
     convergence. Distance-capped trimming lets the matchable set size
     emerge from the data -- essential because ME's continent=1
     silhouette contains coastline vanilla has no counterpart for
     (landmass vanilla tags as other continents, ME-internal seas).
  5. Initialisations, both fit-and-compare by default:
       - "bbox": anisotropic scale+translate aligning each side's
         continent=1 land bounding box. Self-contained, no anchors.
       - "vp":   least-squares affine through the province-centroid
         pairs in docs/states/registration_anchors.tsv. Warm start
         ONLY -- the anchors never enter the coast fit and must not
         be cited as final registration evidence (that is the exact
         circularity this module exists to remove).
     A caller may also pass its own 2x3 matrix.
  6. The returned JSON-serialisable dict carries the winning ME(x,y)
     -> vanilla(x,y) 2x3 affine, its determinant (must be > 0; a
     non-positive determinant means the map folded and the run is
     flagged ok=False), matched-pair count, inlier distance stats and
     one-way + symmetric Chamfer p50/p95 in vanilla pixels, plus the
     per-initialisation runs for review.

Dependencies: numpy, scipy (cKDTree + ndimage), PIL, stdlib only.
Importable by tools/render_state_overlay.py; no file outside this
module is touched.

Local natural-feature refinement (opt-in, does not alter the coast
fit):

  The frozen coast affine can still be locally biased inland (the
  Iletsk region, for example, sits ~8-9 vanilla px off). For a chosen
  ME-space centre/radius, natural_feature_correction() registers
  *physical* anchors only:
    - rivers.bmp palette indices 0-11 (river strokes). Each map
      re-draws its river network independently, so river points carry
      no river ID and are soft hints -- used locally, never as a
      global/hard fit. Indices 254/255 (background) and >=12
      (annotation) are excluded by palette index, not RGB colour.
    - map/definition.csv type=lake shorelines, kept as 4-connected
      blobs of >= MIN_LAKE_PX native px so small lakes/noise drop out
      (Caspian/Aral-class lakes dominate what survives).
  Both classes are matched SEPARATELY by mutual nearest neighbour in
  vanilla space under the coast affine, with a distance cap and
  MAD/trimmed outlier filtering; the surviving pairs fit ONE
  conservative local correction (translation by default, optional
  bounded affine) composed onto the coast affine. Under-matched,
  degenerate or det<=0 corrections fall back to the plain coast
  affine with an explicit fallback_reason -- no guessing. No
  candidate state, history/states, review_queue, VP or city data ever
  enters the fit.

  For review-only validation, natural_feature_topology() extracts
  coarse landmarks -- river confluence clusters, major
  river-component centroids and lake centroids -- region-selects and
  matches them like the point clouds, then scores RELATIONAL
  agreement (pairwise-distance preservation, dominant-axis ordering,
  junction->nearest-lake identity) against a deterministic
  shifted-pairing null. A nested river_branch_graph() diagnostic
  additionally coarsens each river mask into an undirected
  node/edge cell graph (junctions, endpoints, lake contacts,
  degree-2 chain lengths, preserved component IDs) and scores
  branch/connectivity preservation plus degree/length agreement
  under a strict displacement gate. Both return an explicit
  insufficient_evidence result whenever landmarks or graph nodes
  are absent or under-powered; the scores are diagnostic evidence
  about the registration, never proof of a state identity.
"""

import csv
import json
import os
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import (binary_dilation, convolve,
                           distance_transform_edt, label)
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import repo_root  # noqa: E402

DEFAULT_VANILLA = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
DEFAULT_ANCHORS = os.path.join(
    repo_root(), "docs", "states", "registration_anchors.tsv")

EDGE_MARGIN = 8       # px; crop-artefact exclusion band at image edges
GRID_CELL = 8         # px; fixed downsampling grid for coast points
MAX_DIST = 30.0       # px; NN distance cap = ICP trimming threshold
TRIM_FRACTION = 1.0   # fraction of within-cap pairs kept; 1.0 = cap only
                      # (fraction-trimming inside the cap biases the fit
                      # and makes the two initialisations diverge)
MAX_ITERS = 150
TOL = 5e-4            # convergence: max |dA| element change

# ---- natural-feature local refinement (does not feed fit_coast_warp)
FOUR_CONN = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
RIVER_MAX_INDEX = 11    # rivers.bmp palette indices 0-11 = river px;
                        # 254/255 = background, >=12 = annotation
MIN_LAKE_PX = 256       # native px; 4-connected lake blob area floor
NAT_MAX_DIST = 24.0     # vanilla px; mutual-NN distance cap
NAT_MAD_Z = 3.5         # sigma-equivalent robust-z outlier cut
NAT_MIN_PAIRS = 10      # surviving pairs required to fit a correction
NAT_BLEND = 2.0         # map_points taper: full corr <= radius,
                        # zero at radius * NAT_BLEND (smoothstep)
NAT_JAC_MIN_RATIO = 0.05  # transition Jacobian floor relative to coast det

# ---- topology diagnostics (validation evidence only, never a fit)
TOPO_MAX_DIST = 36.0    # vanilla px; landmark mutual-NN cap (coarser
                        # than NAT_MAX_DIST: junction/centroid
                        # landmarks are less localised than stroke px)
TOPO_MIN_LANDMARKS = 4  # matched landmarks required before scoring
TOPO_REL_TOL = 0.25     # retained pairwise-distance relative error
TOPO_ABS_FLOOR = 6.0    # vanilla px; pairwise-distance abs floor
TOPO_SCORE_OK = 0.60    # "consistent": retention >= this AND
TOPO_MARGIN_OK = 0.15   #   margin over shifted-null mean >= this
TOPO_SCORE_BAD = 0.35   # retention below this (or margin <= 0)
                        # means the landmark sets disagree
TOPO_RESIDUAL_P50_OK = 8.25 # vanilla px; reject translation-only matches
TOPO_RESIDUAL_P95_OK = 24.0 # vanilla px; tail must remain local
TOPO_CLASSES = ("river_junction", "river_component", "lake")

# ---- river branch/connectivity graph diagnostic (validation only)
# Rivers.bmp carries no river IDs and no flow direction, and each map
# redraws the network independently, so the pixel graph is reduced to a
# coarse UNDIRECTED cell graph: occupancy cells of TOPO_GRAPH_CELL
# native px, degree-!=2 cell clusters become nodes (junction/endpoint/
# lake-contact/isolated/cycle), degree-2 chains between them become
# edges with arc lengths. Component IDs are preserved per node/edge so
# same-component concordance can be checked across the affine.
TOPO_GRAPH_CELL = 8       # ME native px; vanilla cell follows affine scale
TOPO_GRAPH_MIN_NODES = 4  # matched graph nodes required for scoring
TOPO_GRAPH_MIN_EDGES = 3  # pooled candidate relation edges required
TOPO_GRAPH_SCORE_OK = 0.60    # edge-preservation fraction for
TOPO_GRAPH_MARGIN_OK = 0.15   #   "consistent" + margin over null
TOPO_GRAPH_SCORE_BAD = 0.35   # below this (or margin <= 0) conflicting
TOPO_GRAPH_DEG_MEAN_OK = 1.0  # mean |deg_me - deg_v| gate
TOPO_GRAPH_LEN_P50_OK = 0.40  # p50 relative edge-length error gate
TOPO_GRAPH_LEN_FLOOR = 24.0   # native px; length-error denominator
                              # floor so tiny twigs cannot dominate
TOPO_GRAPH_DISP_P50_OK = 6.0
TOPO_GRAPH_DISP_P95_OK = 18.0
TOPO_GRAPH_DISP_REL_OK = 0.20
TOPO_GRAPH_DISP_P50_NEAR = 18.0  # coarse graph node quantisation/redraw
TOPO_GRAPH_DISP_P95_NEAR = 36.0
TOPO_GRAPH_DISP_REL_NEAR = 3.0  # coarse node matching is not pixel exact


# ---------------------------------------------------------------- map inputs

def _map_dir(root):
    """Accept either a game/mod dir (containing map/) or a map dir."""
    for cand in (os.path.join(root, "map"), root):
        if os.path.isfile(os.path.join(cand, "provinces.bmp")) and \
                os.path.isfile(os.path.join(cand, "definition.csv")):
            return cand
    raise FileNotFoundError(
        "no map/provinces.bmp + definition.csv under %r" % root)


def _parse_definition(path):
    """definition.csv -> (land1_ids, sea_ids, lake_ids) frozensets.

    Province id 0 (the row-0 dummy) is excluded unconditionally.
    land1 = type 'land' with continent column == 1; sea = type 'sea';
    lake = type 'lake' (shorelines only; lakes never enter the coast
    fit, they feed the natural-feature local refinement).
    """
    land1, sea, lake = set(), set(), set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if not row or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            if pid == 0:
                continue
            typ = row[4].strip().lower()
            if typ == "land" and row[7].strip() == "1":
                land1.add(pid)
            elif typ == "sea":
                sea.add(pid)
            elif typ == "lake":
                lake.add(pid)
    return frozenset(land1), frozenset(sea), frozenset(lake)


def _rasterize(bmp_path, colour_of):
    """provinces.bmp + {pid:(r,g,b)} -> int32 HxW raster (-1 unknown)."""
    img = np.asarray(Image.open(bmp_path).convert("RGB"), dtype=np.uint32)
    packed = (img[..., 0] << 16) | (img[..., 1] << 8) | img[..., 2]
    items = sorted(colour_of.items())
    keys = np.array([k for k, _ in items], dtype=np.int64)
    vals = np.array([(r << 16) | (g << 8) | b
                     for _, (r, g, b) in items], dtype=np.int64)
    order = np.argsort(vals)
    keys, vals = keys[order], vals[order]
    pos = np.searchsorted(vals, packed.ravel())
    pos_clip = np.clip(pos, 0, len(vals) - 1)
    hit = vals[pos_clip] == packed.ravel()
    out = np.full(packed.size, -1, dtype=np.int32)
    out[hit] = keys[pos_clip[hit]]
    return out.reshape(packed.shape)


def _all_colours(path):
    """definition.csv -> {pid: (r,g,b)} for every row (needed to raster)."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if row and row[0].strip().isdigit():
                out[int(row[0])] = (int(row[1]), int(row[2]), int(row[3]))
    return out


def _grid_downsample(pts, cell, weights=None):
    """(N,2) xy -> (weighted) mean point per fixed cell-sized grid bin."""
    if not len(pts):
        return pts.reshape(0, 2)
    ckey = (pts[:, 0] // cell) * 1_000_003 + (pts[:, 1] // cell)
    order = np.argsort(ckey, kind="stable")
    ck, pts_s = ckey[order], pts[order]
    bounds = np.flatnonzero(np.r_[True, ck[1:] != ck[:-1], True])
    if weights is not None:
        w = np.asarray(weights, dtype=np.float64)[order]
        sums = np.add.reduceat(pts_s * w[:, None], bounds[:-1], axis=0)
        return sums / np.add.reduceat(w, bounds[:-1])[:, None]
    sums = np.add.reduceat(pts_s, bounds[:-1], axis=0)
    cnt = np.diff(bounds)[:, None]
    return sums / cnt

def _id_mask(flat, ids):
    """bool mask over a flat province raster for a set of ids (-1 safe)."""
    n_bins = int(flat.max()) + 1 if flat.size else 1
    lut = np.zeros(max(n_bins, 1), dtype=bool)
    ids_arr = np.fromiter(ids, dtype=np.int64, count=len(ids))
    lut[ids_arr[ids_arr < n_bins]] = True
    return lut[np.clip(flat, 0, n_bins - 1)] & (flat >= 0)



def _load_side(root, grid_cell=GRID_CELL, margin=EDGE_MARGIN,
               cache=None, min_lake_px=MIN_LAKE_PX):
    """One map -> dict(coast_xy, bbox1, centroids, feature pts, size).

    Natural-feature fields (river_xy / lake_xy) are extracted
    alongside the coastline but are ONLY consumed by the local
    natural-feature refinement; the global coast fit ignores them.
    """
    mdir = _map_dir(root)
    key = ("side", os.path.abspath(mdir), int(grid_cell), int(margin),
           int(min_lake_px))
    if cache is not None and key in cache:
        return cache[key]

    colours = _all_colours(os.path.join(mdir, "definition.csv"))
    land1, sea, lake = _parse_definition(
        os.path.join(mdir, "definition.csv"))
    prov = _rasterize(os.path.join(mdir, "provinces.bmp"), colours)
    h, w = prov.shape

    flat = prov.ravel()
    keep = flat >= 0
    lsea = _id_mask(flat, sea).reshape(prov.shape)
    lland = _id_mask(flat, land1).reshape(prov.shape)

    # centroids for every rasterised province (VP warm start only)
    f = flat[keep]
    yy, xx = np.nonzero(keep.reshape(prov.shape))
    n = np.bincount(f, minlength=int(f.max()) + 1 if len(f) else 1)
    sx = np.bincount(f, weights=xx.astype(np.float64), minlength=n.size)
    sy = np.bincount(f, weights=yy.astype(np.float64), minlength=n.size)
    centroids = {int(p): (float(sx[p] / n[p]), float(sy[p] / n[p]))
                 for p in np.nonzero(n)[0]}

    # coastline = land1 px touching sea, union sea px touching land1
    struct = np.ones((3, 3), dtype=bool)  # 8-connectivity
    land_coast = lland & binary_dilation(lsea, structure=struct)
    sea_coast = lsea & binary_dilation(lland, structure=struct)
    coast = land_coast | sea_coast
    if margin:
        coast[:margin, :] = False
        coast[-margin:, :] = False
        coast[:, :margin] = False
        coast[:, -margin:] = False
    cy, cx = np.nonzero(coast)
    coast_xy = _grid_downsample(
        np.stack([cx, cy], axis=1).astype(np.float64), grid_cell)

    ys, xs = np.nonzero(lland)
    bbox1 = (float(xs.min()), float(ys.min()),
             float(xs.max()), float(ys.max())) if len(xs) else None

    # -- natural features (local refinement only, see module docstring)
    river_xy, n_river_px = _river_points(mdir, grid_cell)
    lake_xy, n_lakes, n_lake_px = _lake_shore_points(
        prov, lake, grid_cell, min_lake_px)

    side = {
        "map_dir": mdir,
        "width": w, "height": h,
        "n_land1_provinces": len(land1),
        "n_sea_provinces": len(sea),
        "n_coast_px": int(len(cx)),
        "coast_xy": coast_xy,
        "land1_bbox_xyxy": bbox1,
        "centroids": centroids,
        "n_river_px": n_river_px,
        "river_xy": river_xy,
        "n_lake_provinces": len(lake),
        "n_lakes_kept": n_lakes,
        "lake_xy": lake_xy,
    }
    if cache is not None:
        cache[key] = side
    return side


def _river_mask(mdir):
    """rivers.bmp -> bool river-pixel mask (palette indices 0-11).

    8-bit palette-indexed raster: 254/255 are background and >=12 are
    annotation glyphs, so selection is by palette index ONLY (never
    an RGB-blue threshold). Missing file -> empty mask.
    """
    path = os.path.join(mdir, "rivers.bmp")
    if not os.path.isfile(path):
        return np.zeros((0, 0), dtype=bool)
    img = Image.open(path)
    if img.mode != "P":
        raise ValueError(
            "rivers.bmp must be palette-indexed (mode P); got mode %r "
            "-- index semantics 0-11=river/254-255=bg do not apply"
            % img.mode)
    return np.asarray(img, dtype=np.int32) <= RIVER_MAX_INDEX


def _river_points(mdir, grid_cell):
    """rivers.bmp river strokes -> (N,2) centreline-ish xy + px count.

    Per grid cell the mean is weighted by each pixel's
    distance-transform value, which pulls samples toward the stroke
    centreline and lets river width act as a soft weight -- the two
    river networks are drawn independently, so exact pixel overlap is
    neither expected nor required (confluences keep their mass).
    Empty mask -> empty point cloud.
    """
    riv = _river_mask(mdir)
    if not riv.any():
        return np.zeros((0, 2), dtype=np.float64), 0
    dist = distance_transform_edt(riv)
    ry, rx = np.nonzero(riv)
    pts = np.stack([rx, ry], axis=1).astype(np.float64)
    xy = _grid_downsample(pts, grid_cell, weights=dist[ry, rx])
    return xy, int(riv.sum())


def _lake_shore_points(prov, lake_ids, grid_cell, min_lake_px):
    """Lake body -> (N,2) shoreline xy, count of kept lakes, px total.

    Lake provinces (definition.csv type=lake) are unioned on the
    province raster, then 4-connected components smaller than
    min_lake_px native pixels are dropped (small lakes/noise; the
    Caspian/Aral-class bodies dominate the survivors). The shoreline
    mirrors the coastline convention: union of kept-lake pixels
    8-adjacent to a non-lake pixel and non-lake pixels 8-adjacent to a
    kept lake -- a symmetric silhouette that is robust to sub-pixel
    drawing differences between the two maps. Lakes are matched
    separately from rivers upstream.
    """
    flat = prov.ravel()
    llake = _id_mask(flat, lake_ids).reshape(prov.shape)
    if not llake.any() or not lake_ids:
        return np.zeros((0, 2), dtype=np.float64), 0, 0
    lab, n_comp = label(llake, structure=FOUR_CONN)
    sizes = np.bincount(lab.ravel(), minlength=n_comp + 1)
    sizes[0] = 0
    keep_ids = np.nonzero(sizes >= int(min_lake_px))[0]
    if not len(keep_ids):
        return np.zeros((0, 2), dtype=np.float64), 0, 0
    lake_big = np.isin(lab, keep_ids)
    struct = np.ones((3, 3), dtype=bool)  # 8-connectivity, as coast
    in_shore = lake_big & binary_dilation(~lake_big, structure=struct)
    out_shore = (~lake_big) & binary_dilation(lake_big,
                                              structure=struct)
    ly, lx = np.nonzero(in_shore | out_shore)
    xy = _grid_downsample(
        np.stack([lx, ly], axis=1).astype(np.float64), grid_cell)
    return xy, int(len(keep_ids)), int(lake_big.sum())


def coast_points(root, grid_cell=GRID_CELL, margin=EDGE_MARGIN,
                 cache=None):
    """Downsampled continent=1 coastline points, (N,2) float64 (x,y).

    Root is a game/mod directory (or a bare map/ directory). Use the
    `cache` dict to share rasterised sides with fit_coast_warp().
    """
    return _load_side(root, grid_cell, margin, cache)["coast_xy"]


# ------------------------------------------------------------------ fitting

def _apply(A, pts):
    return pts @ A[:, :2].T + A[:, 2]


def _fit_affine(src, dst):
    """Least-squares 2x3 affine with src @ A.T ~ dst. src is (N,2)."""
    M = np.concatenate([src, np.ones((len(src), 1))], axis=1)
    sol, *_ = np.linalg.lstsq(M, dst, rcond=None)
    return sol.T  # (2,3)


def _bbox_init(v_side, m_side):
    """Anisotropic scale+translate aligning the two land1 bboxes."""
    vx0, vy0, vx1, vy1 = v_side["land1_bbox_xyxy"]
    mx0, my0, mx1, my1 = m_side["land1_bbox_xyxy"]
    sx = (vx1 - vx0) / max(mx1 - mx0, 1e-9)
    sy = (vy1 - vy0) / max(my1 - my0, 1e-9)
    return np.array([[sx, 0.0, vx0 - sx * mx0],
                     [0.0, sy, vy0 - sy * my0]])


def _vp_init(v_side, m_side, anchors_path):
    """Warm start: global affine through anchor province centroids."""
    mp, vp = [], []
    with open(anchors_path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            m = m_side["centroids"].get(int(r["me_prov"]))
            v = v_side["centroids"].get(int(r["v_prov"]))
            if m is not None and v is not None:
                mp.append(m)
                vp.append(v)
    if len(mp) < 3:
        raise ValueError("fewer than 3 usable anchor centroid pairs")
    return _fit_affine(np.asarray(mp), np.asarray(vp))


def _icp(src, dst, A0, max_dist, trim_fraction, max_iters, tol):
    """Trimmed nearest-neighbour affine ICP. Returns info dict.

    Each iteration keeps the pairs whose NN distance is <= max_dist,
    then (if trim_fraction < 1) the best trim_fraction of those, and
    refits a least-squares affine. Distance-capped trimming lets the
    matchable set size emerge from the data instead of forcing a fixed
    overlap fraction -- essential here because ME's continent=1
    silhouette contains coastline vanilla has no counterpart for
    (landmass vanilla tags as other continents, ME-internal seas).
    """
    tree = cKDTree(dst)
    A = np.asarray(A0, dtype=np.float64).copy()
    converged = False
    det_abort = False
    it = 0

    def pick(d):
        keep = np.nonzero(d <= max_dist)[0]
        if len(keep) >= 3 and trim_fraction < 1.0:
            n = max(3, int(round(len(keep) * trim_fraction)))
            keep = keep[np.argpartition(d[keep], n - 1)[:n]]
        return keep

    for it in range(1, max_iters + 1):
        ts = _apply(A, src)
        d, idx = tree.query(ts)
        sel = pick(d)
        if len(sel) < 3:
            det_abort = True
            break
        A_new = _fit_affine(src[sel], dst[idx[sel]])
        det = np.linalg.det(A_new[:, :2])
        if not np.isfinite(det) or det <= 0:
            det_abort = True
            break
        delta = float(np.abs(A_new - A).max())
        A = A_new
        if delta < tol:
            converged = True
            break
    # final inlier set under the last affine
    ts = _apply(A, src)
    d, idx = tree.query(ts)
    sel = pick(d)
    inlier_d = d[sel] if len(sel) else d
    return {
        "affine": A,
        "det": float(np.linalg.det(A[:, :2])),
        "det_abort": det_abort,
        "iterations": it,
        "converged": converged,
        "n_matches": int(len(sel)),
        "inlier_mean_px": float(inlier_d.mean()),
        "inlier_p25_px": float(np.percentile(inlier_d, 25)),
        "inlier_p50_px": float(np.percentile(inlier_d, 50)),
        "inlier_p75_px": float(np.percentile(inlier_d, 75)),
        "inlier_p95_px": float(np.percentile(inlier_d, 95)),
        "inlier_le_px": {str(b): int(np.count_nonzero(inlier_d <= b))
                         for b in (2, 5, 10, 20)},
    }


def _chamfer(src_to, dst):
    """p50/p95 NN distance of every src_to point against dst points."""
    d, _ = cKDTree(dst).query(src_to)
    return float(np.percentile(d, 50)), float(np.percentile(d, 95))


# --------------------------------------- natural-feature local refinement
#
# Reviewable LOCAL correction on top of the frozen coast affine.
# Physical anchors only: rivers.bmp strokes (palette indices 0-11)
# and definition.csv type=lake shorelines. Each class is matched
# separately (mutual NN + distance cap + MAD/trimmed outliers); the
# surviving pairs fit ONE conservative correction composed onto the
# coast affine. Rivers are redrawn independently on the two maps, so
# this is deliberately a local, soft correction -- never a global or
# per-river-ID hard fit. No candidate state, history/states,
# review_queue, VP or city data participates.


def _as_affine(affine):
    """list/dict/ndarray -> validated 2x3 float64 ME->vanilla affine."""
    if isinstance(affine, dict):
        affine = affine.get("affine_combined",
                            affine.get("affine_me_to_vanilla"))
    A = np.asarray(affine, dtype=np.float64)
    if A.shape != (2, 3) or not np.isfinite(A).all():
        raise ValueError("affine must be a finite 2x3 ME->vanilla "
                         "matrix, got %s" % (A.shape,))
    if np.linalg.det(A[:, :2]) <= 0:
        raise ValueError("affine det <= 0 (mirror/degenerate)")
    return A


def _region_select(m_xy, v_xy, A, center, radius, max_dist):
    """ME circle + vanilla superset box selection under affine A.

    ME points are kept inside a true ME-space circle
    (center/radius). Vanilla points are kept inside the axis-aligned
    box centred on A(center) with half-side max_sv(A)*radius +
    max_dist -- a guaranteed superset of the mapped ME circle plus
    the matching cap, so no legitimately matchable vanilla point is
    excluded.
    """
    c = np.asarray(center, dtype=np.float64)
    d = np.linalg.norm(m_xy - c, axis=1) if len(m_xy) else m_xy[:, 0]
    me_sel = m_xy[d <= float(radius)]
    vc = _apply(A, c[None, :])[0]
    sv = np.linalg.svd(A[:, :2], compute_uv=False)
    reach = float(sv[0]) * float(radius) + float(max_dist)
    lo, hi = vc - reach, vc + reach
    v_sel = v_xy[(v_xy >= lo).all(axis=1) & (v_xy <= hi).all(axis=1)] \
        if len(v_xy) else v_xy
    return me_sel, v_sel, vc


def _mutual_nn_match(me_m, v_pts, max_dist, mad_z, trim=None):
    """Mutual nearest neighbours + distance cap + MAD/trimmed filter.

    me_m: ME feature points already mapped to vanilla space.
    Returns kept pair arrays and review counts; residuals are
    (vanilla_pt - mapped_ME_pt) in vanilla px.
    """
    out = {
        "n_mutual": 0, "n_capped": 0, "n_kept": 0,
        "mad_px": None, "cut_px": None,
        "me_idx": np.zeros(0, dtype=np.int64),
        "v_idx": np.zeros(0, dtype=np.int64),
        "me_vanilla_xy": np.zeros((0, 2)), "vanilla_xy":
        np.zeros((0, 2)), "residual_xy": np.zeros((0, 2)),
    }
    if not len(me_m) or not len(v_pts):
        return out
    d_f, j = cKDTree(v_pts).query(me_m)
    _, i_back = cKDTree(me_m).query(v_pts)
    mutual = i_back[j] == np.arange(len(me_m))
    out["n_mutual"] = int(mutual.sum())
    sel = mutual & (d_f <= float(max_dist))
    out["n_capped"] = int(sel.sum())
    if not sel.any():
        return out
    resid = v_pts[j[sel]] - me_m[sel]
    # MAD outlier cut on the residual vectors (median-centred); if
    # the MAD degenerates to 0 (>=50% share one residual), fall back
    # to a trimmed-quantile cut so a lone wild pair still drops out.
    med = np.median(resid, axis=0)
    dev = np.linalg.norm(resid - med, axis=1)
    mad = float(np.median(dev))
    out["mad_px"] = mad
    if mad > 0:
        cut = mad_z * 1.4826 * mad
        keep = dev <= cut
    else:
        cut = float(np.quantile(dev, 0.8)) if trim is None else \
            float(np.quantile(dev, trim))
        keep = dev <= cut
    if trim is not None and mad > 0:
        keep &= dev <= float(np.quantile(dev, trim))
    out["cut_px"] = float(cut)
    idx = np.flatnonzero(sel)[keep]
    out["n_kept"] = int(len(idx))
    # kept-pair indices into me_m/v_pts so callers can relate matches
    # back to their own per-node bookkeeping (graph diagnostics)
    out["me_idx"] = idx.astype(np.int64)
    out["v_idx"] = j[idx].astype(np.int64)
    out["me_vanilla_xy"] = me_m[idx]
    out["vanilla_xy"] = v_pts[j[idx]]
    out["residual_xy"] = resid[keep]
    return out


def _pct(vals, q):
    return float(np.percentile(vals, q)) if len(vals) else None


def _feature_match(side_m, side_v, A, center, radius, max_dist,
                   mad_z, trim):
    """Match one feature class per side; rivers and lakes stay
    separate all the way through (separate NN pools, separate
    outlier filtering)."""
    feats, pairs_m, pairs_v = {}, [], []
    for feat in ("river", "lake"):
        m_xy = side_m["%s_xy" % feat]
        v_xy = side_v["%s_xy" % feat]
        me_sel, v_sel, vc = _region_select(m_xy, v_xy, A, center,
                                           radius, max_dist)
        me_m = _apply(A, me_sel)
        r = _mutual_nn_match(me_m, v_sel, max_dist, mad_z, trim)
        norms = np.linalg.norm(r["residual_xy"], axis=1)
        feats[feat] = {
            "n_me_region": int(len(me_sel)),
            "n_vanilla_region": int(len(v_sel)),
            "n_mutual": r["n_mutual"],
            "n_capped": r["n_capped"],
            "n_kept": r["n_kept"],
            "mad_px": r["mad_px"],
            "cut_px": r["cut_px"],
            "residual_p50_px": _pct(norms, 50),
            "residual_p95_px": _pct(norms, 95),
            "residual_median_xy": [float(x) for x in np.median(
                r["residual_xy"], axis=0)] if r["n_kept"] else None,
            "pairs": {
                "me_vanilla_xy": r["me_vanilla_xy"].tolist(),
                "vanilla_xy": r["vanilla_xy"].tolist(),
                "residual_xy": r["residual_xy"].tolist(),
            },
        }
        if r["n_kept"]:
            pairs_m.append(r["me_vanilla_xy"])
            pairs_v.append(r["vanilla_xy"])
    src = np.concatenate(pairs_m) if pairs_m else np.zeros((0, 2))
    dst = np.concatenate(pairs_v) if pairs_v else np.zeros((0, 2))
    return feats, src, dst, vc


def _degenerate_spread(pts):
    """True if the matched point cloud cannot support a 2x2 fit."""
    if len(pts) < 3:
        return True
    sv = np.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)
    return sv[0] <= 0 or sv[1] <= 0.02 * sv[0]

def _blend_jacobian_guard(A, C, center, radius, blend):
    """Return (minimum sampled determinant, failure reason).

    The natural correction is affine inside the target radius and is
    smoothly blended back to the coast affine outside it. Positive
    determinants of the two endpoint affines do not guarantee that the
    interpolated annulus is non-folding, so sample its actual Jacobian.
    """
    A = np.asarray(A, dtype=np.float64)
    C = np.asarray(C, dtype=np.float64)
    I = np.eye(2)
    base_lin = A[:, :2]
    delta_lin = (C[:, :2] - I) @ base_lin
    det_base = float(np.linalg.det(base_lin))
    r0 = max(float(radius), 0.0)
    r1 = r0 * float(blend)
    if r1 <= r0:
        return det_base, None
    radii = (0.0, r0, (r0 + r1) * 0.5, r1)
    angles = np.linspace(0.0, 2.0 * np.pi, 16, endpoint=False)
    min_det = float("inf")
    for rr in radii:
        theta = (0.0,) if rr == 0.0 else angles
        for aa in theta:
            p = np.asarray(center, dtype=np.float64) + rr * \
                np.array([np.cos(aa), np.sin(aa)])
            if rr <= r0 or rr >= r1:
                w, grad = (1.0, np.zeros(2)) if rr <= r0 else \
                    (0.0, np.zeros(2))
            else:
                t = (r1 - rr) / (r1 - r0)
                w = t * t * (3.0 - 2.0 * t)
                dw_dr = -6.0 * t * (1.0 - t) / (r1 - r0)
                grad = dw_dr * (p - np.asarray(center)) / rr
            base = _apply(A, p[None, :])[0]
            delta = _apply(C, base[None, :])[0] - base
            J = base_lin + w * delta_lin + np.outer(delta, grad)
            det = float(np.linalg.det(J))
            if not np.isfinite(det):
                return None, "non-finite tapered correction Jacobian"
            min_det = min(min_det, det)
    floor = max(abs(det_base) * NAT_JAC_MIN_RATIO, 1e-6)
    if min_det < floor:
        return min_det, ("tapered correction Jacobian too small or folded "
                         "(min_det=%.6g floor=%.6g)" %
                         (min_det, floor))
    return min_det, None



def natural_feature_correction(vanilla_root, mod_root, affine, center,
                               radius, cache=None,
                               grid_cell=GRID_CELL, margin=EDGE_MARGIN,
                               min_lake_px=MIN_LAKE_PX,
                               max_dist=NAT_MAX_DIST, mad_z=NAT_MAD_Z,
                               min_pairs=NAT_MIN_PAIRS,
                               method="auto", trim=None,
                               blend=NAT_BLEND):
    """Local natural-feature correction composed onto the coast affine.

    affine:   frozen coast affine -- the fit_coast_warp() result dict
              or its 2x3 'affine_me_to_vanilla'.
    center:   (x, y) in MOD (ME) pixel space.
    radius:   ME px; defines both the match region and the map_points
              full-correction zone (tapered to the coast affine by
              radius*blend).
    method:   "auto"      affine if the matched cloud supports it,
                          else a pure translation;
              "affine"    least-squares 2x3 on the matched pairs
                          (rejected and reported if the point spread
                          is degenerate, det<=0, or scale sv outside
                          [0.5, 2]);
              "translation" median residual vector only.
    trim:     optional extra quantile cut on kept residuals.

    Returns a JSON-serialisable dict. If matching is under-powered,
    degenerate, or the fitted correction fails its guards, the
    correction is the identity, affine_combined equals the coast
    affine, and fallback_reason explains why -- never a guess.
    """
    A = _as_affine(affine)
    if cache is None:
        cache = {}
    v = _load_side(vanilla_root, grid_cell, margin, cache, min_lake_px)
    m = _load_side(mod_root, grid_cell, margin, cache, min_lake_px)
    feats, src, dst, vc = _feature_match(
        m, v, A, center, radius, max_dist, mad_z, trim)

    ident = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    reason, C, used = None, ident, "coast"
    resid = dst - src

    if not len(src):
        reason = ("no river/lake feature points inside the region "
                  "(check center/radius)")
    elif len(src) < int(min_pairs):
        reason = ("too few matched pairs after mutual-NN + cap + "
                  "outlier filter: %d < %d" % (len(src), min_pairs))
    elif method in ("auto", "affine"):
        if _degenerate_spread(src):
            if method == "affine":
                reason = ("matched point cloud is degenerate "
                          "(collinear/too few) -- cannot constrain a "
                          "2x2 correction")
            else:
                used = "translation"
        if reason is None and used == "coast":
            C = _fit_affine(src, dst)
            det_c = float(np.linalg.det(C[:, :2]))
            svc = np.linalg.svd(C[:, :2], compute_uv=False)
            if det_c <= 0 or svc[0] > 2.0 or svc[1] < 0.5:
                if method == "affine":
                    reason = ("local affine correction failed guards "
                              "(det=%.4f, sv=[%.3f, %.3f])"
                              % (det_c, svc[0], svc[-1]))
                    C = ident
                else:
                    C, used = ident, "translation"
            else:
                used = "affine"
        if used == "translation":
            d = np.median(resid, axis=0)
            C = np.array([[1.0, 0.0, d[0]], [0.0, 1.0, d[1]]])
    elif method == "translation":
        d = np.median(resid, axis=0)
        C = np.array([[1.0, 0.0, d[0]], [0.0, 1.0, d[1]]])
        used = "translation"
    else:
        raise ValueError("unknown method %r "
                         "(auto|affine|translation)" % method)

    jacobian_min_det = None
    jacobian_reason = None
    if reason is None:
        jacobian_min_det, jacobian_reason = _blend_jacobian_guard(
            A, C, center, radius, blend)
        if jacobian_reason is not None:
            reason = jacobian_reason
            C = ident
            used = "coast"

    A_comb = np.hstack([C[:, :2] @ A[:, :2],
                        (C[:, :2] @ A[:, 2] + C[:, 2])[:, None]])
    norms = np.linalg.norm(resid, axis=1)
    delta = np.median(resid, axis=0) if len(resid) else np.zeros(2)
    notes = [
        "rivers matched by rivers.bmp palette indices 0-11; 254/255 "
        "background and >=12 annotation excluded by index, not colour",
        "river networks are redrawn on each map: local soft "
        "correction only, never a global or per-river-ID hard fit",
        "lake shorelines = type=lake blobs >= %d native px "
        "(4-connected), matched separately from rivers" % min_lake_px,
        "no state/history/review_queue/VP/city data feeds this fit",
    ]
    if method == "auto" and used == "translation" and \
            len(src) >= int(min_pairs):
        notes.append("auto: affine path rejected, used translation")
    if jacobian_reason is not None:
        notes.append("transition guard fallback: " + jacobian_reason)
    return {
        "ok": reason is None,
        "fallback_reason": reason,
        "method_requested": method,
        "method_used": used if reason is None else "coast",
        "center_me_xy": [float(center[0]), float(center[1])],
        "center_vanilla_xy": [float(vc[0]), float(vc[1])],
        "radius_me_px": float(radius),
        "affine_me_to_vanilla": A.tolist(),
        "correction_affine": C.tolist(),
        "correction_det": float(np.linalg.det(C[:, :2])),
        "affine_combined": A_comb.tolist(),
        "affine_combined_det": float(np.linalg.det(A_comb[:, :2])),
        "blend_radius_factor": float(blend),
        "blend_jacobian_min_det": jacobian_min_det,
        "n_pairs_kept": int(len(src)),
        "residuals": {
            "median_xy": [float(x) for x in delta],
            "p50_px": _pct(norms, 50),
            "p95_px": _pct(norms, 95),
        },
        "matches": feats,
        "params": {
            "grid_cell": grid_cell, "edge_margin": margin,
            "min_lake_px": min_lake_px, "max_dist": max_dist,
            "mad_z": mad_z, "min_pairs": min_pairs, "trim": trim,
        },
        "inputs": {
            "vanilla_map_dir": v["map_dir"], "mod_map_dir": m["map_dir"],
            "vanilla_river_points": int(len(v["river_xy"])),
            "mod_river_points": int(len(m["river_xy"])),
            "vanilla_lake_points": int(len(v["lake_xy"])),
            "mod_lake_points": int(len(m["lake_xy"])),
            "vanilla_lakes_kept": v["n_lakes_kept"],
            "mod_lakes_kept": m["n_lakes_kept"],
        },
        "notes": notes,
    }


def natural_feature_points(vanilla_root, mod_root, affine, center,
                           radius, cache=None, grid_cell=GRID_CELL,
                           margin=EDGE_MARGIN, min_lake_px=MIN_LAKE_PX,
                           max_dist=NAT_MAX_DIST):
    """Region-selected natural-feature point clouds, for review.

    Returns {"me": {...}, "me_mapped": {...}, "vanilla": {...}} with
    per-class (river/lake) [x, y] lists. me/vanilla are native pixel
    coords; me_mapped is the ME selection in vanilla space under the
    coast affine. No matching is performed here.
    """
    A = _as_affine(affine)
    if cache is None:
        cache = {}
    v = _load_side(vanilla_root, grid_cell, margin, cache, min_lake_px)
    m = _load_side(mod_root, grid_cell, margin, cache, min_lake_px)
    out = {"me": {}, "me_mapped": {}, "vanilla": {},
           "center_me_xy": [float(center[0]), float(center[1])],
           "radius_me_px": float(radius)}
    for feat in ("river", "lake"):
        me_sel, v_sel, vc = _region_select(
            m["%s_xy" % feat], v["%s_xy" % feat], A, center, radius,
            max_dist)
        out["center_vanilla_xy"] = [float(vc[0]), float(vc[1])]
        out["me"][feat] = me_sel.tolist()
        out["me_mapped"][feat] = _apply(A, me_sel).tolist()
        out["vanilla"][feat] = v_sel.tolist()
    return out


# -------------------------------------------- topology diagnostics
#
# Review-only structural evidence on top of a frozen registration.
# The two river networks are redrawn independently and rivers.bmp
# indices carry no river IDs, so raw stroke pixels cannot prove
# alignment. Instead each side is reduced to a few coarse landmarks
# -- river confluence clusters, major river-component centroids,
# kept-lake centroids -- and RELATIONS between them are compared:
# pairwise-distance preservation, dominant-axis ordering, and
# junction->nearest-lake identity. This is validation evidence about
# a registration inside one region, never a fit and never proof
# that a state on one map IS a state on the other.


def _feature_masks(mdir, cache=None, min_lake_px=MIN_LAKE_PX):
    """Native-resolution river mask + kept-lake label raster.

    Topology landmarks need pixel-level structure that the
    downsampled side dicts deliberately do not carry, so the
    province raster is rebuilt here once per map and memoised under
    its own cache key; callers running the whole review set should
    pass one shared cache dict.
    """
    key = ("topo_masks", os.path.abspath(mdir), int(min_lake_px))
    if cache is not None and key in cache:
        return cache[key]
    prov = _rasterize(os.path.join(mdir, "provinces.bmp"),
                      _all_colours(os.path.join(mdir, "definition.csv")))
    _, _, lake_ids = _parse_definition(
        os.path.join(mdir, "definition.csv"))
    lake_lab = np.zeros(prov.shape, dtype=np.int32)
    llake = _id_mask(prov.ravel(), lake_ids).reshape(prov.shape)
    if llake.any():
        lab, n_comp = label(llake, structure=FOUR_CONN)
        sizes = np.bincount(lab.ravel(), minlength=n_comp + 1)
        sizes[0] = 0
        keep = np.nonzero(sizes >= int(min_lake_px))[0]
        if len(keep):
            lake_lab = lab * np.isin(lab, keep)
    out = {"river": _river_mask(mdir), "lake_lab": lake_lab}
    if cache is not None:
        cache[key] = out
    return out


def _label_centroids(lab, ids):
    """(N,2) xy centroids + per-component px areas for label ids."""
    ids = np.asarray(ids, dtype=np.int64)
    if not len(ids):
        return np.zeros((0, 2)), np.zeros(0)
    sel = np.isin(lab, ids)
    yy, xx = np.nonzero(sel)
    L = lab[yy, xx].astype(np.int64)
    cnt = np.bincount(L, minlength=int(ids.max()) + 1)
    sx = np.bincount(L, weights=xx, minlength=cnt.size)
    sy = np.bincount(L, weights=yy, minlength=cnt.size)
    n = np.maximum(cnt[ids], 1)
    xy = np.stack([sx[ids] / n, sy[ids] / n], axis=1)
    return xy, cnt[ids].astype(np.int64)


def _river_landmarks(river, min_river_px):
    """river mask -> {'river_junction', 'river_component'} (N,2) xy.

    Junctions: 8-connected clusters of river pixels with >=3 river
    neighbours, kept only when >=3 distinct river "arms" (4-connected
    components of river-minus-junction) leave the cluster -- a wide
    straight stroke also produces high-degree pixels but has only
    two exits, so it is not a confluence. Endpoints are dropped
    deliberately: stroke truncation at lakes/map borders is a
    drawing artefact, not a stable feature. Components: 8-connected
    blobs of >= min_river_px native px, one centroid each; no river
    IDs exist, so component identity is positional only.
    """
    out = {"river_junction": np.zeros((0, 2)),
           "river_component": np.zeros((0, 2))}
    if not river.any():
        return out
    kern = np.ones((3, 3), dtype=np.int32)
    riv_i = river.astype(np.int32)
    deg = convolve(riv_i, kern, mode="constant") - riv_i
    jm = (deg >= 3) & river
    jlab, nj = label(jm, structure=kern.astype(bool))
    if nj:
        arms_lab, _ = label(river & ~jm, structure=FOUR_CONN)
        inds = distance_transform_edt(~jm, return_distances=False,
                                      return_indices=True)
        ring = binary_dilation(jm, structure=np.ones((3, 3), bool)) \
            & (arms_lab > 0)
        clu_of_ring = jlab[inds[0][ring], inds[1][ring]]
        arms = {}
        for a, c in zip(arms_lab[ring].tolist(),
                        clu_of_ring.tolist()):
            arms.setdefault(c, set()).add(a)
        jkeep = np.array([i for i in range(1, nj + 1)
                          if len(arms.get(i, ())) >= 3],
                         dtype=np.int64)
        out["river_junction"] = _label_centroids(jlab, jkeep)[0]
    clab, nc = label(river, structure=kern.astype(bool))
    csizes = np.bincount(clab.ravel(), minlength=nc + 1)
    csizes[0] = 0
    keep = np.nonzero(csizes >= int(min_river_px))[0]
    out["river_component"] = _label_centroids(clab, keep)[0]
    return out


def _lake_landmarks(lake_lab):
    """kept-lake label raster -> (N,2) centroid xy + px areas."""
    if not lake_lab.any():
        return np.zeros((0, 2)), np.zeros(0)
    counts = np.bincount(lake_lab.ravel())
    ids = np.flatnonzero(counts[1:]) + 1
    return _label_centroids(lake_lab, ids)


def _coarse_river_graph(mdir, cache=None, cell=TOPO_GRAPH_CELL,
                        min_river_px=MIN_LAKE_PX,
                        min_lake_px=MIN_LAKE_PX,
                        edge_margin=EDGE_MARGIN):
    """rivers.bmp mask -> undirected coarse branch graph (arrays).

    The river raster is reduced to occupancy cells of `cell` native px.
    Per 8-connected occupied component of >= min_river_px native px
    (component IDs preserved from raster labelling), cells whose
    occupied-neighbour count is != 2 are clustered into NODES and the
    degree-2 chains between node clusters become EDGES whose weight is
    the chain arc length in native px. Node degree counts incident
    chain stubs (a self-loop adds two), so a wide straight stroke --
    one chain, no interior degree!=2 cluster -- contributes no node,
    and dense stroke pixels never count as independent evidence.

    Lake contacts: a node cluster or endpoint touching a kept
    type=lake blob (dilated by one cell) is marked lake_contact.
    Boundary nodes: clusters within edge_margin px of the raster
    frame -- truncation artefacts, never confluence evidence.

    Undirected by construction: rivers.bmp indices carry no river ID,
    no flow direction, and no named branch, so "branch structure"
    here means degree/arm counts, node-to-node connectivity and chain
    lengths only. Returns ndarrays, memoised under a cache key;
    JSON-facing callers go through river_branch_graph().
    """
    key = ("river_graph", os.path.abspath(mdir), int(cell),
           int(min_river_px), int(min_lake_px), int(edge_margin))
    if cache is not None and key in cache:
        return cache[key]
    empty = {
        "cell": int(cell), "n_components": 0,
        "n_components_total": 0,
        "comp_cells": np.zeros(0, dtype=np.int64),
        "comp_centroid": np.zeros((0, 2)),
        "node_pos": np.zeros((0, 2)),
        "node_deg": np.zeros(0, dtype=np.int64),
        "node_comp": np.zeros(0, dtype=np.int64),
        "node_kind": np.zeros(0, dtype=object),
        "node_map_edge": np.zeros(0, dtype=bool),
        "edges": np.zeros((0, 2), dtype=np.int64),
        "edge_len": np.zeros(0),
        "edge_comp": np.zeros(0, dtype=np.int64),
    }
    fm = _feature_masks(mdir, cache, min_lake_px)
    river = fm["river"]
    if not river.any():
        if cache is not None:
            cache[key] = empty
        return empty
    h, w = river.shape
    gh, gw = (h + cell - 1) // cell, (w + cell - 1) // cell
    pad = np.zeros((gh * cell, gw * cell), dtype=bool)
    pad[:h, :w] = river
    occ = pad.reshape(gh, cell, gw, cell).any(axis=(1, 3))
    # a partially-out-of-frame cell counts as occupied only inside the
    # raster; frame-adjacent cells get the map-edge flag instead
    if not occ.any():
        if cache is not None:
            cache[key] = empty
        return empty

    # per-cell native river px (component-size floors + map-edge flag)
    cnt = pad.reshape(gh, cell, gw, cell).sum(axis=(1, 3))
    comp_lab, n_comp = label(occ, structure=np.ones((3, 3), bool))
    cell_count = np.bincount(comp_lab.ravel(), weights=cnt.ravel(),
                             minlength=n_comp + 1)
    cell_count[0] = 0
    comp_cells = np.bincount(comp_lab.ravel(), minlength=n_comp + 1)
    comp_cells[0] = 0
    graphed = np.nonzero(cell_count >= int(min_river_px))[0]
    graphed_set = np.zeros(n_comp + 1, dtype=bool)
    graphed_set[graphed] = True

    kern = np.ones((3, 3), dtype=np.int32)
    deg_img = convolve(occ.astype(np.int32), kern,
                       mode="constant") - occ.astype(np.int32)
    node_mask = occ & (deg_img != 2) & graphed_set[comp_lab]
    # pure closed loops have no degree!=2 cell; anchor one node at the
    # component centroid cell so the ring still yields a relation
    for cid in graphed:
        if not (node_mask & (comp_lab == cid)).any():
            yy, xx = np.nonzero(comp_lab == cid)
            k = int(np.argmin((xx - xx.mean()) ** 2 +
                              (yy - yy.mean()) ** 2))
            node_mask[yy[k], xx[k]] = True
    node_lab, n_nodes = label(node_mask,
                              structure=np.ones((3, 3), bool))
    node_of = node_lab - 1  # -1 outside node clusters

    n8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
          (1, -1), (1, 0), (1, 1)]

    def neighbours(y, x):
        for dy, dx in n8:
            ny, nx = y + dy, x + dx
            if 0 <= ny < gh and 0 <= nx < gw and occ[ny, nx]:
                yield ny, nx

    n_occ = int(occ.sum())
    edges, visited = [], set()
    ys, xs = np.nonzero(node_mask)
    for cy, cx in zip(ys.tolist(), xs.tolist()):
        i = int(node_of[cy, cx])
        if i < 0:
            continue
        for vy, vx in neighbours(cy, cx):
            if node_of[vy, vx] == i:
                continue  # same cluster: interior, not a stub
            if (cy, cx, vy, vx) in visited:
                continue
            py, px, ny, nx = cy, cx, vy, vx
            length = 0.0
            end_node = None
            for _ in range(n_occ):
                if (py, px, ny, nx) in visited:
                    # already traced from another boundary cell of the
                    # same cluster (fan-in): one arm, one stub, one
                    # edge -- not one per boundary pixel
                    end_node = None
                    break
                visited.add((py, px, ny, nx))
                visited.add((ny, nx, py, px))
                length += float(np.hypot(ny - py, nx - px))
                if node_of[ny, nx] >= 0:
                    end_node = int(node_of[ny, nx])
                    break
                nxts = [(a, b) for a, b in neighbours(ny, nx)
                        if (a, b) != (py, px)]
                if not nxts:
                    break  # cannot happen for a true degree-2 chain
                (py, px), (ny, nx) = (ny, nx), nxts[0]
            if end_node is not None:
                edges.append((i, end_node, length))
    # degree = number of DISTINCT arms at the cluster: each other
    # node it connects to counts once (parallel chains to the same
    # node are one wide arm, not N branches); a self-loop is the
    # cluster's own ring and contributes two stubs
    nbrs = [set() for _ in range(n_nodes)]
    loops = np.zeros(n_nodes, dtype=np.int64)
    for a, b, _ln in edges:
        if a == b:
            loops[a] += 1
        else:
            nbrs[a].add(b)
            nbrs[b].add(a)
    stub_deg = np.asarray([len(n) for n in nbrs],
                          dtype=np.int64) + 2 * loops
    edge_arr = (np.asarray(edges, dtype=np.float64)
                .reshape(-1, 3) if edges else np.zeros((0, 3)))
    edge_nodes = edge_arr[:, :2].astype(np.int64) if len(edge_arr) \
        else np.zeros((0, 2), dtype=np.int64)
    edge_len = edge_arr[:, 2] * float(cell) if len(edge_arr) \
        else np.zeros(0)

    # node cluster members, in label order (single pass, no per-node
    # full-raster scans)
    order = np.argsort(node_lab[ys, xs], kind="stable")
    gys, gxs = ys[order], xs[order]
    glab = node_lab[gys, gxs]
    bounds = np.flatnonzero(np.r_[True, glab[1:] != glab[:-1], True])
    node_pos = np.stack([
        np.array([(gxs[bounds[i]:bounds[i + 1]].mean() + 0.5)
                  * float(cell) for i in range(n_nodes)]),
        np.array([(gys[bounds[i]:bounds[i + 1]].mean() + 0.5)
                  * float(cell) for i in range(n_nodes)])], axis=1) \
        if n_nodes else np.zeros((0, 2))
    node_comp = np.asarray(
        [comp_lab[gys[bounds[i]], gxs[bounds[i]]]
         for i in range(n_nodes)], dtype=np.int64)
    edge_band = max(1, int(np.ceil(float(edge_margin) / float(cell))))
    frame = np.zeros(occ.shape, dtype=bool)
    frame[:edge_band, :] = frame[-edge_band:, :] = True
    frame[:, :edge_band] = frame[:, -edge_band:] = True
    node_map_edge = np.asarray(
        [bool(frame[gys[bounds[i]:bounds[i + 1]],
                    gxs[bounds[i]:bounds[i + 1]]].any())
         for i in range(n_nodes)], dtype=bool)

    # lake contact: kept-lake pixels dilated by one occupancy cell,
    # tested on member cells of each cluster (reliable contact, not a
    # nearest-lake guess)
    lake_cells = np.zeros(occ.shape, dtype=bool)
    if fm["lake_lab"].any():
        lpad = np.zeros((gh * cell, gw * cell), dtype=bool)
        lh, lw = fm["lake_lab"].shape
        lpad[:lh, :lw] = fm["lake_lab"] > 0
        lake_occ = lpad.reshape(gh, cell, gw, cell).any(axis=(1, 3))
        lake_cells = binary_dilation(
            lake_occ, structure=np.ones((3, 3), bool))
    node_lake = np.asarray(
        [bool(lake_cells[gys[bounds[i]:bounds[i + 1]],
                         gxs[bounds[i]:bounds[i + 1]]].any())
         for i in range(n_nodes)], dtype=bool)

    kinds = []
    for i in range(n_nodes):
        n_mem = bounds[i + 1] - bounds[i]
        if stub_deg[i] == 0:
            kinds.append("cycle" if n_mem == 1 and
                         deg_img[gys[bounds[i]], gxs[bounds[i]]] == 2
                         else "isolated")
        elif node_lake[i]:
            kinds.append("lake_contact")
        elif stub_deg[i] >= 3:
            kinds.append("junction")
        elif stub_deg[i] == 1:
            kinds.append("endpoint")
        else:
            kinds.append("inline")
    graph = {
        "cell": int(cell), "n_components": int(len(graphed)),
        "n_components_total": int(n_comp),
        "comp_cells": comp_cells[graphed],
        "comp_centroid": np.stack(
            [np.bincount(comp_lab.ravel(),
                         weights=(np.indices(occ.shape)[1].ravel()
                                  + 0.5) * float(cell),
                         minlength=n_comp + 1)[graphed] /
             np.maximum(comp_cells[graphed], 1),
             np.bincount(comp_lab.ravel(),
                         weights=(np.indices(occ.shape)[0].ravel()
                                  + 0.5) * float(cell),
                         minlength=n_comp + 1)[graphed] /
             np.maximum(comp_cells[graphed], 1)], axis=1)
        if len(graphed) else np.zeros((0, 2)),
        "node_pos": node_pos,
        "node_deg": stub_deg,
        "node_comp": node_comp,
        "node_kind": np.asarray(kinds, dtype=object),
        "node_map_edge": node_map_edge,
        "edges": edge_nodes,
        "edge_len": edge_len,
        # every chain lives inside one component: comp of either end
        "edge_comp": node_comp[edge_nodes[:, 0]] \
            if len(edge_nodes) else np.zeros(0, dtype=np.int64),
    }
    if cache is not None:
        cache[key] = graph
    return graph


def _graph_edge_multiset(edges):
    """{(u,v) sorted: multiplicity} for an (E,2) undirected edge list."""
    out = {}
    for a, b in np.asarray(edges, dtype=np.int64):
        k = (int(min(a, b)), int(max(a, b)))
        out[k] = out.get(k, 0) + 1
    return out


def river_branch_graph(vanilla_root, mod_root, affine, center,
                       radius, cache=None, cell=TOPO_GRAPH_CELL,
                       min_river_px=MIN_LAKE_PX,
                       min_lake_px=MIN_LAKE_PX,
                       edge_margin=EDGE_MARGIN,
                       max_dist=TOPO_MAX_DIST, mad_z=NAT_MAD_Z,
                       min_nodes=TOPO_GRAPH_MIN_NODES,
                       min_edges=TOPO_GRAPH_MIN_EDGES):
    """Coarse river branch/connectivity diagnostic for one region.

    Both maps' rivers.bmp masks are coarsened into UNDIRECTED cell
    graphs by _coarse_river_graph() (nodes = degree!=2 cell clusters
    and lake contacts, edges = degree-2 chains between them weighted
    by arc length, component IDs preserved). `cell` is the ME native
    cell size; the vanilla cell size is derived from the affine scale
    so one graph cell represents a comparable physical distance.
    Because rivers.bmp is an indexed stroke raster with no river IDs
    or flow direction, and each map redraws the network independently,
    this graph can only compare STRUCTURE: which nodes connect to which
    nodes, arm counts at confluences, and chain lengths -- never
    named-branch identity or upstream/downstream direction.

    Nodes inside `radius` of `center` (ME px circle; vanilla keeps a
    matching superset box like _region_select) are matched by mutual NN
    under the frozen affine with the same cap + MAD rules as the point
    clouds. Map-specific 2*cell ROI bands and `edge_margin` exclude
    clipped nodes and relation edges: a node clipped by the review
    window or map frame is a truncation artefact, not a confluence. An
    ME edge is "preserved" when the vanilla partners of both its
    endpoints are themselves edge-adjacent (and vice versa); the score
    is the pooled preserved/candidate fraction and the margin subtracts
    the mean of deterministic shifted null pairings of the same node
    set.

    assessment:
      consistent            requires ALL of: >= min_nodes matched
                            nodes, >= min_edges pooled candidate
                            edges, >=2 matched interior junction
                            pairs, >=2 preserved edges, score and
                            margin over the null, mean degree error,
                            edge-length p50 (when computable), the
                            displacement gate on matched-node
                            residuals, and component-membership
                            concordance when testable;
      conflicting           score below TOPO_GRAPH_SCORE_BAD, margin
                            <= 0, or the displacement gate fails
                            outright (a translated copy is reported
                            as disagreement, not agreement);
      marginal              everything in between, including near-
                            miss displacements;
      insufficient_evidence empty/under-powered input, too few
                            matched nodes or candidate relation
                            edges -- never silently scored.

    This is VALIDATION EVIDENCE about the registration only:
    candidate_independent=True, state_identity_assessed=False. It
    consumes rivers.bmp masks, kept type=lake masks and the supplied
    affine/center/radius; no state/candidate/VP/city/history input.
    Returns a JSON-serialisable dict; never raises for missing,
    empty or degenerate graph data.
    """
    A = _as_affine(affine)
    if cache is None:
        cache = {}
    mdir_m = _map_dir(mod_root)
    mdir_v = _map_dir(vanilla_root)
    sv = np.linalg.svd(A[:, :2], compute_uv=False)
    scale_mean = float(np.mean(sv))
    cell_v = max(2, int(round(float(cell) * scale_mean)))
    gm = _coarse_river_graph(mdir_m, cache, cell, min_river_px,
                             min_lake_px, edge_margin)
    gv = _coarse_river_graph(mdir_v, cache, cell_v, min_river_px,
                             min_lake_px, edge_margin)

    band_me = 2.0 * float(cell)
    band_v = 2.0 * float(cell_v)
    c = np.asarray(center, dtype=np.float64)
    vc = _apply(A, c[None, :])[0]
    reach = float(sv[0]) * float(radius) + float(max_dist)
    lo, hi = vc - reach, vc + reach

    def region(g, me_side, band_local):
        pos, comp_c = g["node_pos"], g["comp_centroid"]
        if me_side:
            d = np.linalg.norm(pos - c, axis=1) if len(pos) \
                else np.zeros(0)
            in_reg = d <= float(radius)
            roi_edge = d > float(radius) - band_local
            cd = np.linalg.norm(comp_c - c, axis=1) \
                if len(comp_c) else np.zeros(0)
            comp_reg = cd <= float(radius) - band_local
        else:
            in_reg = ((pos >= lo).all(axis=1) &
                      (pos <= hi).all(axis=1)) if len(pos) \
                else np.zeros(0, dtype=bool)
            near_lo = (pos <= lo + band_local).any(axis=1) \
                if len(pos) else np.zeros(0, dtype=bool)
            near_hi = (pos >= hi - band_local).any(axis=1) \
                if len(pos) else np.zeros(0, dtype=bool)
            roi_edge = near_lo | near_hi
            comp_reg = ((comp_c >= lo + band_local).all(axis=1) &
                        (comp_c <= hi - band_local).all(axis=1)) \
                if len(comp_c) else np.zeros(0, dtype=bool)
        elig = in_reg & ~roi_edge & ~g["node_map_edge"]
        return {"in_reg": in_reg, "roi_edge": roi_edge,
                "elig": elig, "comp_reg": comp_reg}

    rm = region(gm, True, band_me)
    rv = region(gv, False, band_v)
    counts = {}
    for side, g, r in (("me", gm, rm), ("vanilla", gv, rv)):
        n_edge = int((r["in_reg"][g["edges"][:, 0]] &
                      r["in_reg"][g["edges"][:, 1]]).sum()) \
            if len(g["edges"]) else 0
        kinds = {}
        for k in g["node_kind"][r["in_reg"]]:
            kinds[str(k)] = kinds.get(str(k), 0) + 1
        counts[side] = {
            "n_components_region": int(r["comp_reg"].sum()),
            "n_nodes_region": int(r["in_reg"].sum()),
            "n_nodes_eligible": int(r["elig"].sum()),
            "n_boundary_excluded":
                int((r["in_reg"] & ~r["elig"]).sum()),
            "n_edges_region": n_edge,
            "node_kinds_region": kinds,
        }

    def insufficient(reason, matching=None):
        return {
            "ok": False, "insufficient_evidence": True,
            "fallback_reason": reason,
            "assessment": "insufficient_evidence",
            "candidate_independent": True,
            "state_identity_assessed": False,
            "method": "undirected coarse river branch/connectivity "
                      "graph diagnostic (occupancy-cell nodes/edges; "
                      "mutual NN + cap + MAD; edge preservation vs "
                      "shifted null; validation only)",
            "center_me_xy": [float(c[0]), float(c[1])],
            "center_vanilla_xy": [float(vc[0]), float(vc[1])],
            "radius_me_px": float(radius),
            "counts": counts,
            "matching": matching, "relations": None,
            "errors": None, "displacement_ok": None,
            "score": None, "margin": None,
            "params": {
                "cell_me": int(cell), "cell_vanilla": int(cell_v),
                "min_river_px": int(min_river_px),
                "min_lake_px": int(min_lake_px),
                "edge_margin": int(edge_margin),
                "roi_band_px": {"me": band_me, "vanilla": band_v},
                "mad_z": float(mad_z),
                "min_nodes": int(min_nodes),
                "min_edges": int(min_edges),
            },
        }

    if counts["me"]["n_nodes_eligible"] == 0 or \
            counts["vanilla"]["n_nodes_eligible"] == 0:
        return insufficient(
            "no eligible river-graph nodes %s in region "
            "(me=%d, vanilla=%d eligible after ROI/frame exclusion)"
            % ("on either side" if not counts["me"]["n_nodes_eligible"]
               and not counts["vanilla"]["n_nodes_eligible"] else
               ("on the ME side" if not counts["me"]["n_nodes_eligible"]
                else "on the vanilla side"),
               counts["me"]["n_nodes_eligible"],
               counts["vanilla"]["n_nodes_eligible"]))

    me_elig = np.flatnonzero(rm["elig"])
    v_elig = np.flatnonzero(rv["elig"])
    r = _mutual_nn_match(_apply(A, gm["node_pos"][me_elig]),
                         gv["node_pos"][v_elig], max_dist, mad_z)
    resid_norms = np.linalg.norm(r["residual_xy"], axis=1)
    matched_me = me_elig[r["me_idx"]]
    matched_v = v_elig[r["v_idx"]]
    matching = {
        "n_mutual": r["n_mutual"], "n_capped": r["n_capped"],
        "n_kept": r["n_kept"], "mad_px": r["mad_px"],
        "cut_px": r["cut_px"],
        "residual_p50_px": _pct(resid_norms, 50),
        "residual_p95_px": _pct(resid_norms, 95),
        "residual_median_xy":
            [float(x) for x in np.median(r["residual_xy"], axis=0)]
            if r["n_kept"] else None,
    }
    if r["n_kept"] < int(min_nodes):
        return insufficient(
            "too few matched river-graph nodes for relational "
            "scoring: %d < %d" % (r["n_kept"], int(min_nodes)),
            matching=matching)

    n = r["n_kept"]
    # partner maps between GLOBAL node ids under the kept pairing and
    # under each circular shift (the deterministic null)
    v_of_me_shift = [dict((int(matched_me[k]),
                          int(matched_v[(k + s) % n]))
                         for k in range(n)) for s in range(n)]
    v_of_me = v_of_me_shift[0]
    me_of_v = {int(matched_v[k]): int(matched_me[k])
               for k in range(n)}

    me_edges, v_edges = gm["edges"], gv["edges"]
    me_edge_sets = _graph_edge_multiset(me_edges)
    v_edge_sets = _graph_edge_multiset(v_edges)
    v_len = {}
    for (a, b), ln in zip(v_edges.tolist(), gv["edge_len"].tolist()):
        v_len.setdefault((min(a, b), max(a, b)), []).append(ln)
    me_len = {}
    for (a, b), ln in zip(me_edges.tolist(), gm["edge_len"].tolist()):
        me_len.setdefault((min(a, b), max(a, b)), []).append(ln)

    me_redges = me_edges[rm["elig"][me_edges[:, 0]] &
                         rm["elig"][me_edges[:, 1]]] \
        if len(me_edges) else me_edges
    v_redges = v_edges[rv["elig"][v_edges[:, 0]] &
                       rv["elig"][v_edges[:, 1]]] \
        if len(v_edges) else v_edges

    def score_pairing(v_of, me_of):
        """(candidate, preserved) pooled over both directions."""
        cand = pres = 0
        for a, b in me_redges:
            a, b = int(a), int(b)
            if a in v_of and b in v_of:
                cand += 1
                k = (min(v_of[a], v_of[b]), max(v_of[a], v_of[b]))
                pres += min(1, v_edge_sets.get(k, 0))
        for a, b in v_redges:
            a, b = int(a), int(b)
            if a in me_of and b in me_of:
                cand += 1
                k = (min(me_of[a], me_of[b]), max(me_of[a], me_of[b]))
                pres += min(1, me_edge_sets.get(k, 0))
        return cand, pres

    cand, pres = score_pairing(v_of_me, me_of_v)
    if cand < int(min_edges):
        return insufficient(
            "too few candidate branch relations between matched "
            "nodes: %d < %d" % (cand, int(min_edges)),
            matching=matching)
    score = pres / float(cand)
    null_scores = []
    for s in range(1, n):
        v_of_s = v_of_me_shift[s]
        me_of_s = {vv: mm for mm, vv in v_of_s.items()}
        c_s, p_s = score_pairing(v_of_s, me_of_s)
        null_scores.append(p_s / float(c_s) if c_s else 0.0)
    null_mean = float(np.mean(null_scores)) if null_scores else 0.0
    margin = score - null_mean

    # per-pair degree/kind/component diagnostics
    deg_err = np.abs(gm["node_deg"][matched_me].astype(float) -
                     gv["node_deg"][matched_v].astype(float))
    kind_pairs = list(zip(gm["node_kind"][matched_me].tolist(),
                          gv["node_kind"][matched_v].tolist()))
    kind_same = sum(1 for a, b in kind_pairs if a == b)
    junction_pairs = sum(1 for a, b in kind_pairs
                         if a == "junction" and b == "junction")
    junction_me_only = sum(1 for a, b in kind_pairs
                           if a == "junction" and b != "junction")
    junction_v_only = sum(1 for a, b in kind_pairs
                          if a != "junction" and b == "junction")
    comp_agree = comp_test = 0
    for i in range(n):
        for j2 in range(i + 1, n):
            comp_test += 1
            comp_agree += int(
                (gm["node_comp"][matched_me[i]] ==
                 gm["node_comp"][matched_me[j2]]) ==
                (gv["node_comp"][matched_v[i]] ==
                 gv["node_comp"][matched_v[j2]]))
    comp_concord = (comp_agree / float(comp_test)) \
        if comp_test else None

    # edge-length errors on preserved ME-side relations (parallel
    # chains collapse to their best-matching length)
    s_len = float(np.mean(sv))
    len_errs = []
    for a, b in me_redges:
        a, b = int(a), int(b)
        if a in v_of_me and b in v_of_me:
            k = (min(v_of_me[a], v_of_me[b]),
                 max(v_of_me[a], v_of_me[b]))
            if k in v_len:
                lm = min(me_len[(min(a, b), max(a, b))]) * s_len
                len_errs.append(min(
                    abs(lv - lm) / max(lm, TOPO_GRAPH_LEN_FLOOR)
                    for lv in v_len[k]))
    len_p50 = _pct(np.asarray(len_errs), 50)
    len_p95 = _pct(np.asarray(len_errs), 95)

    # displacement gate on matched-node residuals: stricter than the
    # landmark gates so an ~8px translated copy cannot pass
    p50 = float(np.percentile(resid_norms, 50))
    p95 = float(np.percentile(resid_norms, 95))
    pool_len = []
    if len(me_redges):
        pool_len.append(gm["edge_len"][
            rm["elig"][me_edges[:, 0]] & rm["elig"][me_edges[:, 1]]]
            * s_len)
    if len(v_redges):
        pool_len.append(gv["edge_len"][
            rv["elig"][v_edges[:, 0]] & rv["elig"][v_edges[:, 1]]])
    med_edge = float(np.median(np.concatenate(pool_len))) \
        if pool_len else None
    rel = p50 / med_edge if med_edge and med_edge > 0 else None
    disp_ok = (p50 <= TOPO_GRAPH_DISP_P50_OK and
               p95 <= TOPO_GRAPH_DISP_P95_OK and
               (rel is None or rel <= TOPO_GRAPH_DISP_REL_OK))
    disp_near = (p50 <= TOPO_GRAPH_DISP_P50_NEAR and
                 p95 <= TOPO_GRAPH_DISP_P95_NEAR and
                 (rel is None or rel <= TOPO_GRAPH_DISP_REL_NEAR))

    relations = {
        "candidate_edges": {
            "me": int(sum(1 for a, b in me_redges
                          if int(a) in v_of_me and
                          int(b) in v_of_me)),
            "vanilla": int(sum(1 for a, b in v_redges
                               if int(a) in me_of_v and
                               int(b) in me_of_v))},
        "n_candidate_pooled": int(cand),
        "n_preserved": int(pres),
        "score": float(score),
        "null_mean": float(null_mean),
        "margin": float(margin),
    }
    errors = {
        "degree_err_mean": float(deg_err.mean()),
        "degree_err_p95": _pct(deg_err, 95),
        "degree_exact": int((deg_err == 0).sum()),
        "kind_agreement": float(kind_same / float(n)),
        "n_junction_pairs": int(junction_pairs),
        "n_junction_me_only": int(junction_me_only),
        "n_junction_vanilla_only": int(junction_v_only),
        "component_pairs_tested": int(comp_test),
        "component_concordance": comp_concord,
        "edge_len_err_p50_rel": len_p50,
        "edge_len_err_p95_rel": len_p95,
        "n_len_measured": int(len(len_errs)),
        "matched_node_p50_px": p50,
        "matched_node_p95_px": p95,
        "residual_vs_edge_len": rel,
    }

    junction_ok = junction_pairs >= 2
    edge_support = pres >= 2
    degree_ok = float(deg_err.mean()) <= TOPO_GRAPH_DEG_MEAN_OK
    length_ok = len_p50 is None or len_p50 <= TOPO_GRAPH_LEN_P50_OK
    comp_ok = comp_concord is None or comp_concord >= 0.5
    if (score >= TOPO_GRAPH_SCORE_OK and
            margin >= TOPO_GRAPH_MARGIN_OK and
            junction_ok and edge_support and degree_ok and
            length_ok and comp_ok and disp_ok):
        assessment = "consistent"
    elif score < TOPO_GRAPH_SCORE_BAD or margin <= 0 or \
            not disp_near:
        assessment = "conflicting"
    else:
        assessment = "marginal"

    return {
        "ok": True, "insufficient_evidence": False,
        "fallback_reason": None,
        "assessment": assessment,
        "candidate_independent": True,
        "state_identity_assessed": False,
        "method": "undirected coarse river branch/connectivity "
                  "graph diagnostic (occupancy-cell nodes/edges; "
                  "mutual NN + cap + MAD; edge preservation vs "
                  "shifted null; validation only)",
        "center_me_xy": [float(c[0]), float(c[1])],
        "center_vanilla_xy": [float(vc[0]), float(vc[1])],
        "radius_me_px": float(radius),
        "counts": counts,
        "matching": matching,
        "relations": relations,
        "errors": errors,
        "displacement_ok": bool(disp_ok),
        "score": float(score),
        "margin": float(margin),
        "params": {
            "cell_me": int(cell), "cell_vanilla": int(cell_v),
            "min_river_px": int(min_river_px),
            "min_lake_px": int(min_lake_px),
            "edge_margin": int(edge_margin),
            "roi_band_px": {"me": band_me, "vanilla": band_v},
            "mad_z": float(mad_z),
            "min_nodes": int(min_nodes),
            "min_edges": int(min_edges),
            "score_ok": TOPO_GRAPH_SCORE_OK,
            "margin_ok": TOPO_GRAPH_MARGIN_OK,
            "score_bad": TOPO_GRAPH_SCORE_BAD,
            "deg_mean_ok": TOPO_GRAPH_DEG_MEAN_OK,
            "len_p50_ok": TOPO_GRAPH_LEN_P50_OK,
            "len_floor_px": TOPO_GRAPH_LEN_FLOOR,
            "disp_p50_ok": TOPO_GRAPH_DISP_P50_OK,
            "disp_p95_ok": TOPO_GRAPH_DISP_P95_OK,
            "disp_rel_ok": TOPO_GRAPH_DISP_REL_OK,
            "disp_p50_near": TOPO_GRAPH_DISP_P50_NEAR,
            "disp_p95_near": TOPO_GRAPH_DISP_P95_NEAR,
            "disp_rel_near": TOPO_GRAPH_DISP_REL_NEAR,
            "min_junction_pairs": 2, "min_preserved": 2,
        },
        "notes": [
            "graph is undirected: rivers.bmp carries no river IDs "
            "and no flow direction; 'branch relation' = node-to-node "
            "adjacency through a degree-2 chain, not a named branch",
            "nodes within 2*cell of the ROI boundary or edge_margin "
            "of the raster frame are excluded before matching: "
            "clipped nodes/edges are truncation artefacts, not "
            "confluences",
            "consistent requires >=2 matched interior junction "
            "pairs, >=2 preserved edges, degree/length/component "
            "compatibility AND the strict displacement gate "
            "(p50<=6px, p95<=18px, residual<=20% of median edge "
            "length); near misses remain marginal",
            "edge multiplicities and parallel chains collapse to "
            "one relation for scoring; chain lengths are compared "
            "in vanilla px under the affine scale",
            "validation evidence about the registration only; no "
            "state/candidate/VP/city/history data participates",
        ],
    }


def _landmark_relations(src, dst, cls, rel_tol, abs_floor):
    """Score relational agreement of one matched landmark pairing.

    src: matched ME landmarks mapped to vanilla space; dst: their
    vanilla counterparts; cls: per-landmark class label (same order
    on both sides -- matching is per class). Candidates = all
    unordered landmark pairs (i<j). A pair is retained when the
    vanilla-side distance reproduces the ME-side distance within
    max(rel_tol*d, abs_floor) -- a translation/rotation-insensitive
    relation, so a uniform local offset cannot manufacture agreement
    and one redrawn river cannot destroy all of it.

    score = retained fraction. margin = score minus the mean of the
    n-1 circularly shifted null pairings of dst (deterministic, no
    RNG): how much better the true pairing preserves distances than
    systematically wrong pairings of the same landmark set.
    order_concordance = sign agreement of pair differences projected
    on dst's dominant axis.
    """
    n = len(src)
    iu = np.triu_indices(n, 1)
    d_src = np.linalg.norm(src[iu[0]] - src[iu[1]], axis=1)
    d_dst = np.linalg.norm(dst[iu[0]] - dst[iu[1]], axis=1)
    tol = np.maximum(rel_tol * d_src, float(abs_floor))
    err = np.abs(d_dst - d_src)
    kept = err <= tol
    score = float(kept.mean()) if len(kept) else None
    null_scores = [
        float(np.mean(np.abs(np.linalg.norm(
            dst[(iu[0] + s) % n] - dst[(iu[1] + s) % n], axis=1)
            - d_src) <= tol))
        for s in range(1, n)]
    null_mean = float(np.mean(null_scores)) if null_scores else 0.0
    concordance = None
    if n >= 2:
        _, sv, vt = np.linalg.svd(dst - dst.mean(axis=0))
        if sv[0] > 0:
            ps = (src - src.mean(axis=0)) @ vt[0]
            pd = (dst - dst.mean(axis=0)) @ vt[0]
            concordance = float(np.mean(
                np.sign(ps[iu[0]] - ps[iu[1]]) ==
                np.sign(pd[iu[0]] - pd[iu[1]])))
    pair_cls = cls[iu[0]]
    same = pair_cls == cls[iu[1]]
    by_class = {}
    for c in TOPO_CLASSES:
        sel = same & (pair_cls == c)
        by_class[c] = {"candidate_pairs": int(sel.sum()),
                       "retained_pairs": int((sel & kept).sum())}
    return {
        "candidate_pairs": int(len(d_src)),
        "retained_pairs": int(kept.sum()),
        "by_class": by_class,
        "pair_dist_err_p50_px": _pct(err, 50),
        "pair_dist_err_p95_px": _pct(err, 95),
        "score": score,
        "null_mean": null_mean,
        "margin": None if score is None else float(score - null_mean),
        "order_concordance": concordance,
    }


def natural_feature_topology(vanilla_root, mod_root, affine, center,
                             radius, cache=None,
                             grid_cell=GRID_CELL, margin=EDGE_MARGIN,
                             min_lake_px=MIN_LAKE_PX,
                             min_river_px=MIN_LAKE_PX,
                             max_dist=TOPO_MAX_DIST, mad_z=NAT_MAD_Z,
                             min_landmarks=TOPO_MIN_LANDMARKS,
                             rel_tol=TOPO_REL_TOL,
                             abs_floor=TOPO_ABS_FLOOR):
    """Coarse topology diagnostics for a registration in one region.

    affine/center/radius mirror natural_feature_correction(): center
    is (x, y) in MOD pixel space, radius the ME-px half-extent of the
    region of interest, affine the registration being assessed --
    a fit_coast_warp() dict, a natural_feature_correction() dict (its
    affine_combined is preferred), or a bare 2x3 matrix.

    Landmarks (both sides, native px):
      river_junction   clusters of river pixels with >=3 river
                       neighbours AND >=3 outgoing arms (confluences);
      river_component  centroid of each 8-connected river blob of
                       >= min_river_px native px;
      lake             centroid of each kept lake (the same
                       >= min_lake_px 4-connected blobs used by the
                       local correction).
    Each class is region-selected with the same ME-circle /
    vanilla-superset-box rule as the point clouds, matched by mutual
    NN under the affine (distance cap + MAD cut), and then scored
    RELATIONALLY: distances between matched landmark pairs,
    dominant-axis ordering, and junction->nearest-lake identity.

    assessment is 'consistent' | 'marginal' | 'conflicting' when the
    evidence supports scoring, else 'insufficient_evidence'. A
    'consistent' assessment also requires absolute landmark residuals to
    stay below the configured p50/p95 floors, so translation-only
    relational agreement cannot pass by itself. These diagnostics are
    VALIDATION EVIDENCE about the registration in this region, not proof
    of state identity. Rivers are redrawn
    independently on each map and the indexed raster carries no
    river IDs, so a "consistent" assessment corroborates the affine
    locally; it never assigns a state, and weak or under-powered
    cases return an explicit insufficient_evidence result
    (score/margin = null) instead of fabricating a match.

    The "branch_graph" sub-dict (river_branch_graph()) adds a second,
    independent diagnostic: each side's river mask coarsened into an
    undirected cell graph (junction/endpoint/lake-contact nodes,
    degree-2 chain edges with arc lengths, component IDs preserved),
    matched under the same affine and scored on branch-relation
    preservation, degree/length agreement and a strict displacement
    gate. It is nested evidence only; `overall_assessment` is
    conservative and cannot be `consistent` unless both diagnostics
    pass. No field assigns a state.

    Returns a JSON-serialisable dict; never raises for missing,
    empty or under-powered feature data.
    """
    A = _as_affine(affine)
    if cache is None:
        cache = {}
    v = _load_side(vanilla_root, grid_cell, margin, cache, min_lake_px)
    m = _load_side(mod_root, grid_cell, margin, cache, min_lake_px)
    fm = _feature_masks(m["map_dir"], cache, min_lake_px)
    fv = _feature_masks(v["map_dir"], cache, min_lake_px)

    me_lm = _river_landmarks(fm["river"], min_river_px)
    me_lm["lake"] = _lake_landmarks(fm["lake_lab"])[0]
    v_lm = _river_landmarks(fv["river"], min_river_px)
    v_lm["lake"] = _lake_landmarks(fv["lake_lab"])[0]

    region, landmark_counts = {}, {"me": {}, "vanilla": {}}
    vc = _apply(A, np.asarray(center, dtype=np.float64)[None, :])[0]
    for cls in TOPO_CLASSES:
        me_c, v_c, _ = _region_select(me_lm[cls], v_lm[cls], A,
                                      center, radius, max_dist)
        region[cls] = (me_c, v_c)
        landmark_counts["me"][cls] = int(len(me_c))
        landmark_counts["vanilla"][cls] = int(len(v_c))
    n_me = sum(landmark_counts["me"].values())
    n_v = sum(landmark_counts["vanilla"].values())

    reason = None
    matches, raw = {}, {}
    if n_me == 0 or n_v == 0:
        side = "both" if n_me == 0 and n_v == 0 else \
            ("ME" if n_me == 0 else "vanilla")
        reason = ("no landmarks on %s side in region "
                  "(me=%d, vanilla=%d)" % (side, n_me, n_v))
    else:
        for cls in TOPO_CLASSES:
            me_c, v_c = region[cls]
            r = _mutual_nn_match(_apply(A, me_c), v_c, max_dist, mad_z)
            raw[cls] = r
            norms = np.linalg.norm(r["residual_xy"], axis=1)
            matches[cls] = {
                "n_me_region": int(len(me_c)),
                "n_vanilla_region": int(len(v_c)),
                "n_mutual": r["n_mutual"],
                "n_capped": r["n_capped"],
                "n_kept": r["n_kept"],
                "mad_px": r["mad_px"],
                "cut_px": r["cut_px"],
                "residual_p50_px": _pct(norms, 50),
                "residual_p95_px": _pct(norms, 95),
            }

    src_parts, dst_parts, cls_parts = [], [], []
    for cls in TOPO_CLASSES:
        r = raw.get(cls)
        if r is not None and r["n_kept"]:
            src_parts.append(r["me_vanilla_xy"])
            dst_parts.append(r["vanilla_xy"])
            cls_parts += [cls] * r["n_kept"]
    n_landmarks = len(cls_parts)
    min_lm = max(2, int(min_landmarks))
    if reason is None and n_landmarks < min_lm:
        reason = ("too few matched landmarks for relational scoring: "
                  "%d < %d" % (n_landmarks, min_lm))

    resid_norms = np.linalg.norm(
        np.concatenate(dst_parts) - np.concatenate(src_parts),
        axis=1) if src_parts else np.zeros(0)

    score = relation_margin = concordance = prox = None
    absolute_residuals_ok = None
    assessment = "insufficient_evidence"
    relations = {"candidate_pairs": 0, "retained_pairs": 0,
                 "by_class": {}, "pair_dist_err_p50_px": None,
                 "pair_dist_err_p95_px": None}
    if reason is None:
        src = np.concatenate(src_parts)
        dst = np.concatenate(dst_parts)
        cls_arr = np.asarray(cls_parts)
        relations = _landmark_relations(src, dst, cls_arr,
                                        rel_tol, abs_floor)
        score, relation_margin = relations["score"], relations["margin"]
        concordance = relations["order_concordance"]
        jmask = cls_arr == "river_junction"
        v_lakes = region["lake"][1]
        if jmask.any() and len(v_lakes):
            lt = cKDTree(v_lakes)
            prox = float(np.mean(
                lt.query(src[jmask])[1] == lt.query(dst[jmask])[1]))
        residual_p50 = _pct(resid_norms, 50)
        residual_p95 = _pct(resid_norms, 95)
        absolute_residuals_ok = (
            residual_p50 is not None and residual_p95 is not None and
            residual_p50 <= TOPO_RESIDUAL_P50_OK and
            residual_p95 <= TOPO_RESIDUAL_P95_OK)
        if (score >= TOPO_SCORE_OK and
                relation_margin >= TOPO_MARGIN_OK and
                absolute_residuals_ok):
            assessment = "consistent"
        elif score < TOPO_SCORE_BAD or relation_margin <= 0:
            assessment = "conflicting"
        else:
            assessment = "marginal"

    # second, independent diagnostic: coarse river branch/connectivity
    # graph. Always evaluated (under-powered input returns an explicit
    # insufficient_evidence sub-dict, never an exception) and nested
    # beside the landmark score as validation evidence only.
    branch = river_branch_graph(vanilla_root, mod_root, A, center,
                                radius, cache=cache,
                                min_river_px=min_river_px,
                                min_lake_px=min_lake_px,
                                edge_margin=margin,
                                max_dist=max_dist, mad_z=mad_z)

    if assessment == "conflicting" or \
            branch["assessment"] == "conflicting":
        overall = "conflicting"
    elif assessment == "insufficient_evidence" or \
            branch["assessment"] == "insufficient_evidence":
        overall = "insufficient_evidence"
    elif assessment == "consistent" and \
            branch["assessment"] == "consistent":
        overall = "consistent"
    else:
        overall = "marginal"

    return {
        "ok": reason is None,
        "insufficient_evidence": reason is not None,
        "fallback_reason": reason,
        "assessment": assessment,
        "overall_assessment": overall,
        "candidate_independent": True,
        "state_identity_assessed": False,
        "method": "coarse river/lake landmark relational diagnostic "
                  "(confluence clusters + river-component/lake "
                  "centroids; mutual NN + cap + MAD; pairwise-distance "
                  "retention vs shifted null; validation only)",
        "center_me_xy": [float(center[0]), float(center[1])],
        "center_vanilla_xy": [float(vc[0]), float(vc[1])],
        "radius_me_px": float(radius),
        "landmarks": landmark_counts,
        "matches": matches,
        "n_matched_landmarks": int(n_landmarks),
        "relations": relations,
        "score": score,
        "margin": relation_margin,
        "order_concordance": concordance,
        "junction_lake_agreement": prox,
        "absolute_residuals_ok": absolute_residuals_ok,
        "branch_graph": branch,
        "residuals": {
            "p50_px": _pct(resid_norms, 50),
            "p95_px": _pct(resid_norms, 95),
        },
        "params": {
            "grid_cell": grid_cell, "edge_margin": margin,
            "min_lake_px": min_lake_px, "min_river_px": min_river_px,
            "max_dist": max_dist, "mad_z": mad_z,
            "min_landmarks": min_lm,
            "rel_tol": rel_tol, "abs_floor": abs_floor,
            "residual_p50_ok": TOPO_RESIDUAL_P50_OK,
            "residual_p95_ok": TOPO_RESIDUAL_P95_OK,
        },
        "inputs": {
            "vanilla_map_dir": v["map_dir"],
            "mod_map_dir": m["map_dir"],
            "vanilla_river_points": int(len(v["river_xy"])),
            "mod_river_points": int(len(m["river_xy"])),
            "vanilla_lake_points": int(len(v["lake_xy"])),
            "mod_lake_points": int(len(m["lake_xy"])),
            "vanilla_lakes_kept": v["n_lakes_kept"],
            "mod_lakes_kept": m["n_lakes_kept"],
        },
        "notes": [
            "landmarks are confluence clusters (deg>=3 with >=3 "
            "arms), river-component centroids >= %d native px and "
            "kept-lake centroids; endpoints and raw stroke pixels "
            "are never landmarks" % int(min_river_px),
            "score = retained pairwise-distance fraction; margin = "
            "score minus the mean of the deterministic shifted null "
            "pairings (a score inflated by accident must still beat "
            "wrong pairings of the same landmark set)",
            "consistent additionally requires absolute landmark residuals "
            "within p50<=%.1f and p95<=%.1f vanilla px; this blocks "
            "translation-only false positives" %
            (TOPO_RESIDUAL_P50_OK, TOPO_RESIDUAL_P95_OK),
            "order_concordance and junction_lake_agreement are "
            "reported for review but do not gate the assessment",
            "branch_graph nests the undirected river "
            "branch/connectivity diagnostic (river_branch_graph): "
            "it never feeds the landmark score above and never "
            "assigns a state",
            "topology diagnostics are validation evidence about the "
            "registration, not proof of state identity; no "
            "state/history/review_queue/VP/city data participates",
        ],
    }


def map_points(registration, pts):
    """ME (N,2) px -> vanilla px under a registration result.

    registration may be:
      - a natural_feature_correction() dict: affine_combined applied
        inside the region, smoothly tapered to the coast affine over
        [radius, radius*blend_radius_factor] so callers can map a
        bounded target area without a visible seam;
      - a fit_coast_warp() dict: affine_me_to_vanilla applied;
      - a bare 2x3 affine.
    All output is finite (a single affine cannot fold).
    """
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if not isinstance(registration, dict) or (
            "affine_combined" not in registration and
            "correction_affine" not in registration):
        return _apply(_as_affine(registration), pts)
    A = _as_affine(registration["affine_me_to_vanilla"])
    base = _apply(A, pts)
    if registration.get("fallback_reason") is not None:
        return base
    C = np.asarray(registration["correction_affine"],
                   dtype=np.float64)
    if np.allclose(C, np.eye(2, 3)):
        return base
    c = np.asarray(registration["center_me_xy"], dtype=np.float64)
    r0 = float(registration["radius_me_px"])
    r1 = r0 * float(registration.get("blend_radius_factor", NAT_BLEND))
    w = np.ones(len(pts)) if r1 <= r0 else np.clip(
        (r1 - np.linalg.norm(pts - c, axis=1)) / (r1 - r0), 0.0, 1.0)
    w = w * w * (3.0 - 2.0 * w)  # smoothstep taper
    return base + w[:, None] * (_apply(C, base) - base)


# ------------------------------------------------------------- public entry

def fit_coast_warp(vanilla_root, mod_root, cache=None, initial="auto",
                   grid_cell=GRID_CELL, margin=EDGE_MARGIN,
                   trim_fraction=TRIM_FRACTION, max_dist=MAX_DIST,
                   max_iters=MAX_ITERS, tol=TOL, anchors_path=None):
    """Fit the ME(x,y) -> vanilla(x,y) 2x3 affine from coastlines.

    vanilla_root / mod_root: game or mod directories (each containing
    map/provinces.bmp + map/definition.csv; a bare map dir also works).

    initial:
      "auto"/"both"  run "bbox" and "vp" (if anchors available), report
                     both, keep the better symmetric Chamfer p95.
      "bbox"|"vp"    run exactly one initialisation.
      array-like     caller-supplied 2x3 ME->vanilla affine warm start.
    cache: optional dict, caches rasterised sides across calls.
    Returns a JSON-serialisable dict; see module docstring for fields.
    """
    if cache is None:
        cache = {}
    v = _load_side(vanilla_root, grid_cell, margin, cache)
    m = _load_side(mod_root, grid_cell, margin, cache)
    anchors_path = anchors_path or DEFAULT_ANCHORS

    inits, notes = [], []
    if isinstance(initial, str):
        want = {"auto": ["bbox", "vp"], "both": ["bbox", "vp"]
                }.get(initial, [initial])
        for name in want:
            if name == "bbox" and v["land1_bbox_xyxy"] and \
                    m["land1_bbox_xyxy"]:
                inits.append(("bbox", _bbox_init(v, m)))
            elif name == "vp":
                if os.path.isfile(anchors_path):
                    try:
                        inits.append(("vp", _vp_init(
                            v, m, anchors_path)))
                    except ValueError as e:
                        notes.append("vp init skipped: %s" % e)
                else:
                    notes.append("vp init skipped: %s not found"
                                 % anchors_path)
            else:
                raise ValueError("unknown initial %r" % name)
    else:
        inits.append(("custom", np.asarray(initial, dtype=np.float64)))

    if not inits:
        raise ValueError("no usable initialisation (initial=%r)"
                         % initial)

    src, dst = m["coast_xy"], v["coast_xy"]
    runs = {}
    for name, A0 in inits:
        info = _icp(src, dst, A0, max_dist, trim_fraction, max_iters, tol)
        A = info.pop("affine")
        ts = _apply(A, src)
        f50, f95 = _chamfer(ts, dst)          # ME -> vanilla
        b50, b95 = _chamfer(dst, ts)          # vanilla -> ME
        pool = np.concatenate([
            cKDTree(dst).query(ts)[0],
            cKDTree(ts).query(dst)[0]])
        info.update({
            "affine": [[float(x) for x in row] for row in A.tolist()],
            "affine_det": float(np.linalg.det(A[:, :2])),
            "chamfer_me_to_vanilla_p50_px": f50,
            "chamfer_me_to_vanilla_p95_px": f95,
            "chamfer_vanilla_to_me_p50_px": b50,
            "chamfer_vanilla_to_me_p95_px": b95,
            "chamfer_symmetric_p50_px": float(np.percentile(pool, 50)),
            "chamfer_symmetric_p95_px": float(np.percentile(pool, 95)),
        })
        runs[name] = info

    # winner: lowest symmetric p95, ties -> more matches
    best = min(runs, key=lambda k: (
        runs[k]["chamfer_symmetric_p95_px"], -runs[k]["n_matches"]))
    win = runs[best]
    ok = (win["affine_det"] > 0 and not win["det_abort"] and
          win["converged"] and win["n_matches"] >= 3)

    # init agreement: max deviation between run affines evaluated over
    # the ME coast point cloud (px in vanilla space)
    agree = None
    if len(runs) > 1:
        mats = {k: np.array(r["affine"]) for k, r in runs.items()}
        diffs = []
        names = sorted(mats)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = _apply(mats[names[i]], src), \
                    _apply(mats[names[j]], src)
                diffs.append(float(np.median(
                    np.linalg.norm(a - b, axis=1))))
        agree = max(diffs) if diffs else 0.0

    return {
        "ok": bool(ok),
        "method": "trimmed nearest-coast affine ICP on continent=1 "
                  "land<->sea silhouette (no VP/state/city/history "
                  "input to the fit)",
        "affine_me_to_vanilla": win["affine"],
        "affine_det": win["affine_det"],
        "init_source": best,
        "n_matches": win["n_matches"],
        "inlier_p50_px": win["inlier_p50_px"],
        "inlier_p95_px": win["inlier_p95_px"],
        "inlier_le_px": win["inlier_le_px"],
        "init_agreement_px": agree,
        "iterations": win["iterations"],
        "converged": win["converged"],
        "chamfer_px": {
            "me_to_vanilla_p50": win["chamfer_me_to_vanilla_p50_px"],
            "me_to_vanilla_p95": win["chamfer_me_to_vanilla_p95_px"],
            "vanilla_to_me_p50": win["chamfer_vanilla_to_me_p50_px"],
            "vanilla_to_me_p95": win["chamfer_vanilla_to_me_p95_px"],
            "symmetric_p50": win["chamfer_symmetric_p50_px"],
            "symmetric_p95": win["chamfer_symmetric_p95_px"],
        },
        "runs": runs,
        "params": {"grid_cell": grid_cell, "edge_margin": margin,
                   "max_dist": max_dist, "trim_fraction": trim_fraction,
                   "max_iters": max_iters, "tol": tol},
        "inputs": {
            "vanilla_map_dir": v["map_dir"],
            "mod_map_dir": m["map_dir"],
            "vanilla_size": [v["width"], v["height"]],
            "mod_size": [m["width"], m["height"]],
            "vanilla_land1_provinces": v["n_land1_provinces"],
            "mod_land1_provinces": m["n_land1_provinces"],
            "vanilla_coast_points": int(len(v["coast_xy"])),
            "mod_coast_points": int(len(m["coast_xy"])),
            "anchors_tsv": anchors_path,
        },
        "notes": notes + [
            "continent=1 is the landmass tag from each definition.csv, "
            "not a hand-drawn Ural boundary",
            "vp run is a warm start only; it is not registration "
            "evidence",
        ],
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Coastline-driven ME -> vanilla affine fit")
    ap.add_argument("--vanilla", default=DEFAULT_VANILLA,
                    help="vanilla game dir (or bare map dir)")
    ap.add_argument("--mod", default=None,
                    help="mod dir; default = this repo's mods/* root")
    ap.add_argument("--initial", default="auto",
                    help="auto|bbox|vp")
    ap.add_argument("--grid-cell", type=int, default=GRID_CELL)
    ap.add_argument("--trim", type=float, default=TRIM_FRACTION)
    ap.add_argument("--max-dist", type=float, default=MAX_DIST)
    ap.add_argument("--anchors", default=None,
                    help="registration_anchors.tsv override (vp init)")
    args = ap.parse_args(argv)

    mod = args.mod
    if mod is None:
        from _modroot import mod_root
        mod = mod_root()
    res = fit_coast_warp(args.vanilla, mod, initial=args.initial,
                         grid_cell=args.grid_cell,
                         trim_fraction=args.trim, max_dist=args.max_dist,
                         anchors_path=args.anchors)
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
