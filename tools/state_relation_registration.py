#!/usr/bin/env python
"""Topology-based ME -> vanilla registration for the state relation map.

Candidate-independent companion to continent_registration: the global fit
and every per-component refinement see ONLY pixel topology -- the 1936.1.1
owner partition (country borders), land<->sea coastlines and land<->lake
shorelines. No VP, state name, comment, canonical-ancestry or review-queue
input enters any transform produced here. Owner tags and owner connected
components participate ONLY as scope/refinement geometry; they are a
physical partition of the map, never an identity claim. All raster overlap
is sampled evidence; nothing here proves a subset or an identity.

Pipeline:

  1. load_side() rasterises one map root (game/mod dir or bare map dir):
     provinces.bmp -> int32 province raster through definition.csv colours;
     history/states/*.txt -> state province membership (same filename
     convention as the canonical loaders) plus the owner tag at CUTOFF
     (1936.1.1, the build_state_edges_1936 rule: undated owner base then
     dated blocks <= cutoff, any hour of the cutoff day counts).
     From those it derives the land/sea/lake masks, a numeric owner
     raster (-1 non-land, 0 unowned land, >=1 owned -> owner_tags index+1),
     owner-connected 4-components and three boundary point classes:
       country -- land px whose 8-neighbourhood contains a DIFFERENT
                  nonzero owner code (owned-vs-owned borders only;
                  owned-vs-unowned is a frontier, not a border);
       coast   -- land px 8-adjacent to sea + sea px 8-adjacent to land;
       lake    -- land px 8-adjacent to lake + lake px 8-adjacent to land.
     All adjacency is computed with non-wrapping windows (map edges are
     crop artefacts, never wrap neighbours), pixels within EDGE_MARGIN of
     an edge are dropped, and survivors are downsampled to fixed GRID_CELL
     cell means (x, y provinces.bmp coordinates; no y-flip).

  2. fit_global() = stage 1: continent_registration.fit_coast_warp with
     initial="bbox" ONLY (coast points; the VP warm start is never
     requested). Stage 2: mutual-nearest-neighbour affine ICP against the
     union of owner-independent boundary classes. Each class matches
     separately (a country point can only pair with a country point) via
     continent_registration._mutual_nn_match (mutual NN + distance cap +
     MAD outlier cut); the surviving pairs are pooled per class and refit
     to one affine, iterated to convergence. Finite positive det required
     throughout; an aborted refinement returns the stage-1 coast affine
     flagged ok=False with an explicit diagnostic -- never a guess.

  3. fit_components() walks the ME owner-connected components, restricts
     the target search to same-1936-owner vanilla components, and picks
     the candidate whose geometry sits nearest the globally mapped ME
     centroid (boundary-point NN distance, 0 inside the mask; deterministic
     (-area, id) tie-break). No candidate -> "unmatched_component".
     Beyond the distance cap -> "unmatched_component" when the mapped
     footprint still lands on same-owner land (registration likely off),
     "excluded_inset" when it lands on foreign/unowned/non-land pixels
     (enclave/out-of-scope territory: no same-owner counterpart exists on
     this landmass). A selected component refits its own boundary points
     against the target's under a bounded LOCAL affine: the composed
     local-after-global transform must keep det > 0 and singular values
     within LOCAL_SV_MIN..LOCAL_SV_MAX of the global fit; otherwise the
     component falls back to the global affine with status
     "global_fallback" and a recorded reason. With apply_natural=True a
     powered component additionally composes
     continent_registration.natural_feature_correction (river/lake pixel
     anchors, cached map sides) on top of the local affine; under-powered
     or guard-failed corrections keep their explicit fallback_reason and
     the registration degrades to the bounded local (or global) affine.

Registration dicts are continent_registration.map_points-compatible:
"affine_me_to_vanilla" plus, when a natural correction ran, the
"correction_affine"/"center_me_xy"/"radius_me_px"/"blend_radius_factor"
fields that drive the tapered blend. Everything returned is
JSON-serialisable except the in-memory "mask" fields (boolean rasters);
serialisable_component() strips them for output. All coordinates are
provinces.bmp x/y.

Dependencies: numpy, scipy, Pillow, stdlib only.
"""

import csv
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
from scipy.ndimage import find_objects, label
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root  # noqa: E402
from build_state_edges_1936 import state_owner_at  # noqa: E402
import continent_registration as cr  # noqa: E402
from pdx_parse import Parser  # noqa: E402

CUTOFF = (1936, 1, 1)

HOI4 = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"

GRID_CELL = cr.GRID_CELL          # px; fixed downsampling grid
EDGE_MARGIN = cr.EDGE_MARGIN      # px; crop-artefact exclusion band
FOUR_CONN = cr.FOUR_CONN

# stage-2 global boundary refinement (mutual-NN ICP, owner-independent)
GLOBAL_MAX_DIST = 24.0            # vanilla px; mutual-NN distance cap
GLOBAL_MAD_Z = cr.NAT_MAD_Z       # sigma-equivalent robust-z outlier cut
GLOBAL_MIN_PAIRS = 12             # kept pairs required to refit one step
GLOBAL_MAX_ITERS = 60
GLOBAL_TOL = cr.TOL               # convergence: max |dA| element change

# per-component local registration
LOCAL_MAX_DIST = 16.0             # vanilla px; mutual-NN distance cap
LOCAL_MAD_Z = cr.NAT_MAD_Z
LOCAL_MIN_PAIRS = 12
LOCAL_MAX_ITERS = 30
LOCAL_TOL = cr.TOL
LOCAL_SV_MIN = 0.80               # composed local-after-global singular
LOCAL_SV_MAX = 1.25               # values must stay inside this band
TARGET_SELECT_MAX_DIST = 64.0     # vanilla px; nearest same-owner
                                  # component must sit inside this cap
NATURAL_RADIUS_PAD = 24.0         # ME px; region pad past comp extent
INSET_SAME_OWNER_SHARE = 0.10     # mapped-footprint share below which a
                                  # distant component reads as an inset

BOUNDARY_CLASSES = ("country", "coast", "lake")


# ------------------------------------------------------------- map inputs

def _definition_classes(path):
    """definition.csv -> (land, sea, lake) province-id frozensets.

    Unlike continent_registration._parse_definition the land set keeps
    EVERY type=land province regardless of continent tag: owner
    partitions and components must cover all drawn land, not only the
    continent=1 silhouette used for the coast warm start.
    """
    land, sea, lake = set(), set(), set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if not row or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            if pid == 0:
                continue
            typ = row[4].strip().lower()
            if typ == "land":
                land.add(pid)
            elif typ == "sea":
                sea.add(pid)
            elif typ == "lake":
                lake.add(pid)
    return frozenset(land), frozenset(sea), frozenset(lake)


def _game_root(root):
    """Accept either a game/mod dir (containing map/) or a map dir;
    return the dir that holds history/states (root above map/)."""
    mdir = cr._map_dir(root)
    if os.path.isdir(os.path.join(mdir, "map")):
        return mdir                      # root is already the game dir
    return os.path.dirname(mdir)         # bare map dir -> parent


def _load_states(root, cutoff=CUTOFF):
    """history/states -> {sid: {"provinces": [pid..], "owner": tag}}.

    One parse per file; same filename-prefix ID source and malformed-
    file failures as the canonical loaders. Owner resolves through the
    shared build_state_edges_1936 rule (undated base then dated blocks
    while <= cutoff, hour-insensitive on the cutoff day); a missing
    history block or owner yields "".
    """
    directory = Path(root) / "history" / "states"
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        raise FileNotFoundError(
            "no history/states/*.txt under %s" % root)
    states = {}
    for path in paths:
        match = re.match(r"^(\d+)\s*-", path.name)
        if not match:
            raise ValueError(
                "%s: filename must start with a numeric state id" % path)
        sid = int(match.group(1))
        parser = Parser(path.read_text(encoding="utf-8-sig",
                                       errors="replace"))
        blocks = [n for n in parser.parse()
                  if n.key == "state" and n.is_block]
        if parser.errors or len(blocks) != 1:
            raise ValueError("%s: malformed state: %s"
                             % (path, parser.errors or
                                "expected one state block"))
        children = blocks[0].children
        province_blocks = [n for n in children
                           if n.key == "provinces" and n.is_block]
        if len(province_blocks) > 1:
            raise ValueError("%s: duplicate province list" % path)
        provinces = []
        for node in province_blocks[0].children if province_blocks else []:
            if not node.key.isdigit() or node.val is not None \
                    or node.children != []:
                raise ValueError(
                    "%s: non-numeric province list member" % path)
            provinces.append(int(node.key))
        history = [n for n in children
                   if n.key == "history" and n.is_block]
        if len(history) > 1:
            raise ValueError("%s: duplicate history block" % path)
        states[sid] = {
            "provinces": sorted(set(provinces)),
            "owner": state_owner_at(history[0].children, cutoff)
            if history else "",
        }
    return states


def _state_raster(province_raster, states):
    """int32 HxW state raster (-1 unassigned); pid 0 never a state."""
    max_pid = max(0, int(province_raster.max()), max(
        (p for s in states.values() for p in s["provinces"]), default=0))
    lut = np.full(max_pid + 1, -1, dtype=np.int32)
    for sid, state in sorted(states.items()):
        for pid in state["provinces"]:
            if pid <= 0:
                continue
            if lut[pid] != -1:
                raise ValueError(
                    "province %d belongs to multiple states" % pid)
            lut[pid] = sid
    return np.take(lut, province_raster, mode="clip")


def _owner_codebook(states):
    """Sorted unique non-empty owner tags; "" reserved for unowned."""
    tags = sorted({s["owner"] for s in states.values() if s["owner"]})
    return tags


def _owner_raster(state_raster, states, land_mask):
    """int32 HxW owner code: -1 non-land, 0 unowned land, >=1 -> tag+1."""
    tags = _owner_codebook(states)
    code_of_tag = {tag: i + 1 for i, tag in enumerate(tags)}
    sids = sorted(states)
    max_sid = max(sids, default=0)
    lut = np.zeros(max_sid + 1, dtype=np.int32)
    for sid in sids:
        lut[sid] = code_of_tag.get(states[sid]["owner"], 0)
    out = lut[np.clip(state_raster, 0, max_sid)]
    out = np.where(land_mask, out, -1).astype(np.int32)
    return out, tags


# -------------------------------------------------------- boundary masks

def _edge_touch(mask):
    """Non-wrapping 8-adjacency: px whose 3x3 neighbourhood holds mask.

    Pure-slice implementation: image edges truncate the window, so a
    pixel on x=0 is never adjacent to one on x=W-1 (no np.roll wrap).
    """
    out = np.zeros(mask.shape, dtype=bool)
    h, w = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sy0, sy1 = max(0, dy), min(h, h + dy)
            sx0, sx1 = max(0, dx), min(w, w + dx)
            ty0, ty1 = max(0, -dy), min(h, h - dy)
            tx0, tx1 = max(0, -dx), min(w, w - dx)
            out[ty0:ty1, tx0:tx1] |= mask[sy0:sy1, sx0:sx1]
    return out


def _country_mask(owner_raster):
    """Land px whose 8-neighbourhood holds a DIFFERENT nonzero owner.

    Owned-vs-owned borders only: code 0 (unowned) and -1 (non-land)
    never form a country boundary, and unowned-vs-unowned matches are
    undifferentiated territory.
    """
    own = owner_raster
    out = np.zeros(own.shape, dtype=bool)
    h, w = own.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sy0, sy1 = max(0, dy), min(h, h + dy)
            sx0, sx1 = max(0, dx), min(w, w + dx)
            ty0, ty1 = max(0, -dy), min(h, h - dy)
            tx0, tx1 = max(0, -dx), min(w, w - dx)
            nb = own[sy0:sy1, sx0:sx1]
            out[ty0:ty1, tx0:tx1] |= (nb > 0) & (nb != own[ty0:ty1,
                                                          tx0:tx1])
    return out & (own > 0)


def _boundary_masks(land_mask, sea_mask, lake_mask, owner_raster):
    """Class masks: country/coast/lake boolean rasters (non-wrapping).

    Classes are independent and may overlap: a land pixel touching both
    sea and lake counts in both classes. Per-class mutual-NN matching
    keeps them from pairing across classes downstream anyway.
    """
    coast = (land_mask & _edge_touch(sea_mask)) | \
        (sea_mask & _edge_touch(land_mask))
    lake = (land_mask & _edge_touch(lake_mask)) | \
        (lake_mask & _edge_touch(land_mask))
    return {
        "country": _country_mask(owner_raster),
        "coast": coast,
        "lake": lake,
    }


def _boundary_points(masks, grid_cell=GRID_CELL, margin=EDGE_MARGIN):
    """Per-class downsampled boundary point clouds ((N,2) x/y float64)."""
    out = {}
    for name, mask in masks.items():
        m = mask.copy()
        if margin:
            m[:margin, :] = False
            m[-margin:, :] = False
            m[:, :margin] = False
            m[:, -margin:] = False
        yy, xx = np.nonzero(m)
        pts = np.stack([xx, yy], axis=1).astype(np.float64) \
            if len(xx) else np.zeros((0, 2), dtype=np.float64)
        out[name] = cr._grid_downsample(pts, grid_cell)
    return out


# ----------------------------------------------------- connected components

def _connected_components(owner_raster, owner_tags, boundary_points):
    """4-connected per-owner components over the owned land raster.

    Returns [{component_id, owner, centroid_xy, area_px, bbox_xyxy, mask,
    boundary_points}] in deterministic (owner tag, scan-order label)
    order. mask is the component bounding-window boolean raster (memory
    only; never serialised). Unowned code 0 forms no components.
    """
    comps = []
    for code, tag in enumerate(owner_tags, start=1):
        lab, n = label(owner_raster == code, structure=FOUR_CONN)
        if not n:
            continue
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        sizes[0] = 0
        slices = find_objects(lab)
        for comp in np.nonzero(sizes)[0]:
            sy, sx = slices[comp - 1]
            sub = lab[sy, sx] == comp
            yy, xx = np.nonzero(sub)
            area = int(sizes[comp])
            centroid = [float(xx.mean() + sx.start),
                        float(yy.mean() + sy.start)]
            cmask = sub
            pts = {}
            for name, cloud in boundary_points.items():
                if not len(cloud):
                    pts[name] = cloud
                    continue
                sel = _points_in_mask(cloud, cmask, sx.start, sy.start)
                pts[name] = cloud[sel]
            comps.append({
                "component_id": len(comps),
                "owner": tag,
                "centroid_xy": centroid,
                "area_px": area,
                "bbox_xyxy": [int(sx.start), int(sy.start),
                              int(sx.stop - 1), int(sy.stop - 1)],
                "mask": cmask,
                "boundary_points": pts,
            })
    return comps


def _points_in_mask(pts, mask, x0, y0):
    """Bool selection: pts (x,y) inside mask anchored at (x0, y0)."""
    ix = np.rint(pts[:, 0]).astype(np.int64) - x0
    iy = np.rint(pts[:, 1]).astype(np.int64) - y0
    ok = (ix >= 0) & (iy >= 0) & (ix < mask.shape[1]) & \
        (iy < mask.shape[0])
    out = np.zeros(len(pts), dtype=bool)
    out[ok] = mask[iy[ok], ix[ok]]
    return out


def _mask_contains(mask, x0, y0, pt):
    """True when point pt (x,y) rounds inside the cropped mask."""
    ix = int(np.rint(pt[0])) - x0
    iy = int(np.rint(pt[1])) - y0
    return 0 <= ix < mask.shape[1] and 0 <= iy < mask.shape[0] and \
        bool(mask[iy, ix])


def _all_boundary(comp):
    """Concatenated per-class component boundary points."""
    clouds = [c for c in comp["boundary_points"].values() if len(c)]
    if not clouds:
        return np.zeros((0, 2), dtype=np.float64)
    return np.concatenate(clouds)


# ------------------------------------------------------------------ load

def load_side(root, cutoff=CUTOFF, cache=None):
    """One map -> rasters, owner partition, boundaries and components.

    root: game/mod dir (map/ + history/states/) or a bare map dir whose
    parent holds history/states. cache: shared dict forwarded to
    continent_registration._load_side so later natural-feature calls
    reuse the rasterised sides; kept on the result as side["cache"].
    Returns the fixed dict documented in the module docstring.
    """
    if cache is None:
        cache = {}
    mdir = cr._map_dir(root)
    game = _game_root(root)
    cr._load_side(mdir, cache=cache)     # warm the shared coast cache

    colours = cr._all_colours(os.path.join(mdir, "definition.csv"))
    prov = cr._rasterize(os.path.join(mdir, "provinces.bmp"), colours)
    land, sea, lake = _definition_classes(
        os.path.join(mdir, "definition.csv"))
    flat = prov.ravel()
    land_mask = cr._id_mask(flat, land).reshape(prov.shape)
    sea_mask = cr._id_mask(flat, sea).reshape(prov.shape)
    lake_mask = cr._id_mask(flat, lake).reshape(prov.shape)

    states = _load_states(game, cutoff)
    state_raster = _state_raster(prov, states)
    owner_raster, owner_tags = _owner_raster(state_raster, states,
                                             land_mask)

    masks = _boundary_masks(land_mask, sea_mask, lake_mask, owner_raster)
    points = _boundary_points(masks)
    components = _connected_components(owner_raster, owner_tags, points)

    return {
        "map_dir": mdir,
        "root": game,
        "cache": cache,
        "width": int(prov.shape[1]),
        "height": int(prov.shape[0]),
        "province_raster": prov,
        "state_raster": state_raster,
        "land_mask": land_mask,
        "sea_mask": sea_mask,
        "lake_mask": lake_mask,
        "owner_raster": owner_raster,
        "owner_tags": owner_tags,
        "states": states,
        "boundary_masks": masks,
        "boundary_points": points,
        "components": components,
        "cutoff": list(cutoff),
    }


# --------------------------------------------------------- boundary ICP

def _class_pairs(src_cls, dst_cls, A, max_dist, mad_z):
    """Per-class mutual-NN match under affine A -> pooled pair arrays.

    src classes missing on either side contribute nothing (a ME-only
    border class must not pair with vanilla coast). Returns
    (src_xy_in_vanilla_space, dst_xy, per_class_kept_counts).
    """
    srcs, dsts, counts = [], [], {}
    for name in BOUNDARY_CLASSES:
        s = src_cls.get(name)
        d = dst_cls.get(name)
        if s is None or d is None or not len(s) or not len(d):
            counts[name] = 0
            continue
        matched = cr._mutual_nn_match(
            cr._apply(A, s), d, max_dist, mad_z)
        counts[name] = matched["n_kept"]
        if matched["n_kept"]:
            srcs.append(matched["me_vanilla_xy"])
            dsts.append(matched["vanilla_xy"])
    if srcs:
        return np.concatenate(srcs), np.concatenate(dsts), counts
    return np.zeros((0, 2)), np.zeros((0, 2)), counts


def _boundary_icp(src_cls, dst_cls, A0, max_dist=GLOBAL_MAX_DIST,
                  mad_z=GLOBAL_MAD_Z, min_pairs=GLOBAL_MIN_PAIRS,
                  max_iters=GLOBAL_MAX_ITERS, tol=GLOBAL_TOL):
    """Mutual-NN per-class boundary ICP around affine A0.

    src point clouds live in ME px; dst in vanilla px. Each iteration
    matches every boundary class SEPARATELY in vanilla space (mutual NN
    + cap + MAD cut), pools the surviving pairs across classes and
    refits one least-squares affine; a non-positive determinant aborts
    (the map folded -- never repaired silently). Returns a
    JSON-serialisable info dict with the final affine and pair stats.
    """
    A = np.asarray(A0, dtype=np.float64).copy()
    converged = False
    det_abort = False
    it = 0
    for it in range(1, max_iters + 1):
        src_m, dst_m, _ = _class_pairs(src_cls, dst_cls, A, max_dist,
                                       mad_z)
        if len(src_m) < int(min_pairs):
            det_abort = True
            break
        # pairs are vanilla->vanilla (src already mapped); refit the
        # relative affine and compose it onto the running estimate so
        # convergence measures the update, not the absolute map
        src_me = _invert2(A, src_m)
        A_new = cr._fit_affine(src_me, dst_m)
        det = np.linalg.det(A_new[:, :2])
        if not np.isfinite(det) or det <= 0:
            det_abort = True
            break
        delta = float(np.abs(A_new - A).max())
        A = A_new
        if delta < tol:
            converged = True
            break
    src_m, dst_m, counts = _class_pairs(src_cls, dst_cls, A, max_dist,
                                        mad_z)
    resid = np.linalg.norm(dst_m - src_m, axis=1) \
        if len(src_m) else np.zeros(0)
    return {
        "affine": [[float(x) for x in row] for row in A.tolist()],
        "affine_np": A,
        "det": float(np.linalg.det(A[:, :2])),
        "converged": bool(converged),
        "det_abort": bool(det_abort),
        "iterations": int(it),
        "n_pairs": int(len(src_m)),
        "pairs_by_class": {k: int(v) for k, v in counts.items()},
        "residual_p50_px": cr._pct(resid, 50),
        "residual_p95_px": cr._pct(resid, 95),
    }


def _invert2(A, pts):
    """Apply the inverse of 2x3 affine A to (N,2) points."""
    lin = A[:, :2]
    return (pts - A[:, 2]) @ np.linalg.inv(lin).T


# ------------------------------------------------------------- global fit

def fit_global(me_side, v_side, initial="bbox"):
    """Coast-bbox warm start + owner-independent boundary refinement.

    initial: "bbox" (the ONLY supported string -- the vp anchor path is
    deliberately unreachable here) or a caller-supplied 2x3 affine.
    Returns the registration dict: finite positive-det
    affine_me_to_vanilla, method, metrics and serialisable diagnostics.
    """
    cache = _merged_cache(me_side, v_side)
    if isinstance(initial, str):
        if initial != "bbox":
            raise ValueError(
                "fit_global only supports the candidate-independent "
                "'bbox' initialisation (got %r)" % initial)
        coast = cr.fit_coast_warp(
            v_side["map_dir"], me_side["map_dir"], cache=cache,
            initial="bbox")
        A0 = np.asarray(coast["affine_me_to_vanilla"], dtype=np.float64)
        coast_meta = {
            "ok": bool(coast["ok"]),
            "converged": bool(coast["converged"]),
            "n_matches": int(coast["n_matches"]),
            "inlier_p50_px": coast["inlier_p50_px"],
            "inlier_p95_px": coast["inlier_p95_px"],
            "chamfer_px": coast["chamfer_px"],
        }
        init_desc = "bbox"
    else:
        A0 = np.asarray(initial, dtype=np.float64)
        if A0.shape != (2, 3) or not np.isfinite(A0).all() or \
                np.linalg.det(A0[:, :2]) <= 0:
            raise ValueError(
                "initial affine must be finite 2x3 with det > 0")
        coast = None
        coast_meta = None
        init_desc = "custom"

    info = _boundary_icp(me_side["boundary_points"],
                         v_side["boundary_points"], A0,
                         GLOBAL_MAX_DIST, GLOBAL_MAD_Z,
                         GLOBAL_MIN_PAIRS, GLOBAL_MAX_ITERS, GLOBAL_TOL)
    A = info.pop("affine_np")
    refined_ok = (not info["det_abort"]) and info["converged"] and \
        info["n_pairs"] >= GLOBAL_MIN_PAIRS
    if not refined_ok or info["det"] <= 0:
        A = A0
        refinement_status = "coast_fallback"
    else:
        refinement_status = "refined"

    return {
        "kind": "state_relation_registration",
        "claim": "sampled_boundary_geometry",
        "ok": bool(np.isfinite(np.linalg.det(A[:, :2])) and
                   np.linalg.det(A[:, :2]) > 0),
        "method": "coast-only %s warm start + mutual-NN affine ICP on "
                  "owner-independent country/coast/lake boundary "
                  "points (no VP/name/comment/ancestry input)"
                  % init_desc,
        "affine_me_to_vanilla": [[float(x) for x in row]
                                 for row in A.tolist()],
        "affine_det": float(np.linalg.det(A[:, :2])),
        "initial": init_desc,
        "metrics": {
            "refinement_status": refinement_status,
            "refine_converged": info["converged"],
            "refine_iterations": info["iterations"],
            "refine_pairs": info["n_pairs"],
            "refine_pairs_by_class": info["pairs_by_class"],
            "refine_residual_p50_px": info["residual_p50_px"],
            "refine_residual_p95_px": info["residual_p95_px"],
            "refine_det": info["det"],
            "coast": coast_meta,
        },
        "diagnostics": {
            "kind": "topology_boundary_registration",
            "claim": "sampled_geometry_only",
            "boundary_classes": list(BOUNDARY_CLASSES),
            "params": {
                "global_max_dist_px": GLOBAL_MAX_DIST,
                "global_mad_z": GLOBAL_MAD_Z,
                "global_min_pairs": GLOBAL_MIN_PAIRS,
                "global_max_iters": GLOBAL_MAX_ITERS,
                "grid_cell": GRID_CELL,
                "edge_margin": EDGE_MARGIN,
            },
            "det_abort": info["det_abort"],
            "notes": [
                "boundary classes matched separately via mutual NN + "
                "cap + MAD cut, then pooled; owner partition supplies "
                "country borders only -- never a state identity",
                "refinement failure keeps the coast-only affine and "
                "flags refinement_status=coast_fallback",
            ],
        },
    }


def _merged_cache(me_side, v_side, cache=None):
    """Shared continent_registration cache: explicit + per-side stores."""
    merged = {}
    for c in (me_side.get("cache"), v_side.get("cache"), cache):
        if c:
            merged.update(c)
    return merged


def _affine_dict(A, reason=None):
    """map_points-compatible bare registration around 2x3 affine A."""
    return {
        "affine_me_to_vanilla": [[float(x) for x in row]
                                 for row in np.asarray(
                                     A, dtype=np.float64).tolist()],
        "fallback_reason": reason,
    }


# --------------------------------------------------------- local guard

def _local_transform_ok(A_global, A_local,
                        sv_min=LOCAL_SV_MIN, sv_max=LOCAL_SV_MAX):
    """Guard for the bounded component refinement.

    The local affine is only allowed to perturb the global one: the
    composed local-after-global transform T = L o G^-1 must keep a
    positive determinant and singular values inside [sv_min, sv_max].
    Returns (ok, reason, rel_sv, rel_det).
    """
    G = np.asarray(A_global, dtype=np.float64)
    L = np.asarray(A_local, dtype=np.float64)
    if not np.isfinite(L).all():
        return False, "non-finite local affine", None, None
    g_inv = np.linalg.inv(G[:, :2])
    t_lin = L[:, :2] @ g_inv
    det_t = float(np.linalg.det(t_lin))
    if not np.isfinite(det_t) or det_t <= 0:
        return False, "local transform folds (rel det %.4f)" % det_t, \
            None, det_t
    sv = np.linalg.svd(t_lin, compute_uv=False)
    if sv[0] > float(sv_max) or sv[-1] < float(sv_min):
        return False, \
            "local transform scale out of bounds (sv=[%.4f, %.4f] " \
            "allowed [%.3f, %.3f])" % (sv[0], sv[-1], sv_min, sv_max), \
            [float(sv[0]), float(sv[-1])], det_t
    return True, None, [float(sv[0]), float(sv[-1])], det_t


# --------------------------------------------------- component matching

def _select_target(mapped_centroid, candidates, trees,
                   max_dist=TARGET_SELECT_MAX_DIST):
    """Nearest same-owner component under the global affine.

    Distance = 0 when the mapped ME centroid rounds inside the
    candidate's mask, else the NN distance to its pooled boundary
    points; deterministic (distance, -area, id) ordering. Returns
    (component, distance) or (None, None) beyond the cap.
    """
    best, best_d = None, float("inf")
    for cand in candidates:
        if _mask_contains(cand["mask"], cand["bbox_xyxy"][0],
                          cand["bbox_xyxy"][1], mapped_centroid):
            d = 0.0
        else:
            pts = _all_boundary(cand)
            if not len(pts):
                continue
            tree = trees.get(cand["component_id"])
            if tree is None:
                tree = cKDTree(pts)
                trees[cand["component_id"]] = tree
            d = float(tree.query(mapped_centroid)[0])
        if (d, -cand["area_px"], cand["component_id"]) < \
                (best_d, -(best["area_px"] if best else 0),
                 best["component_id"] if best else 0):
            best, best_d = cand, d
    if best is None or best_d > float(max_dist):
        return None, (best_d if best is not None else None)
    return best, best_d


def _footprint_shares(comp, v_side, G):
    """Where the mapped ME component lands on the vanilla partition.

    ME component pixel centres mapped through the global affine are
    classified against the vanilla owner raster. Denominators are
    explicit: samples_total covers every component pixel; same-owner
    land share is taken over samples that landed on ANY vanilla land
    (foreign/unowned land still counts as land).
    """
    mask, (x0, y0, _, _) = comp["mask"], comp["bbox_xyxy"]
    yy, xx = np.nonzero(mask)
    pts = np.stack([xx + x0, yy + y0], axis=1).astype(np.float64)
    mapped = cr._apply(np.asarray(G, dtype=np.float64), pts)
    ix = np.rint(mapped[:, 0]).astype(np.int64)
    iy = np.rint(mapped[:, 1]).astype(np.int64)
    vown = v_side["owner_raster"]
    vland = v_side["land_mask"]
    inside = (ix >= 0) & (iy >= 0) & (ix < vown.shape[1]) & \
        (iy < vown.shape[0])
    total = len(ix)
    n_in = int(inside.sum())
    vcode = np.zeros(total, dtype=np.int32)
    vcode[inside] = vown[iy[inside], ix[inside]]
    v_is_land = np.zeros(total, dtype=bool)
    v_is_land[inside] = vland[iy[inside], ix[inside]]
    same_code = None
    for i, tag in enumerate(v_side["owner_tags"], start=1):
        if tag == comp["owner"]:
            same_code = i
            break
    n_same = int(np.count_nonzero(vcode == same_code)) \
        if same_code is not None else 0
    n_land = int(v_is_land.sum())
    return {
        "samples_total": int(total),
        "inside_vanilla": n_in,
        "outside_vanilla": int(total - n_in),
        "landed_land_px": n_land,
        "landed_water_or_unassigned_px": int(
            np.count_nonzero((vcode <= 0) & inside & ~v_is_land)),
        "landed_same_owner_px": n_same,
        "same_owner_land_share": (n_same / n_land) if n_land else None,
    }


def _component_extent(comp):
    """Max centroid->pixel distance over the component mask (ME px)."""
    mask, (x0, y0, _, _) = comp["mask"], comp["bbox_xyxy"]
    yy, xx = np.nonzero(mask)
    if not len(xx):
        return 1.0
    cx, cy = comp["centroid_xy"]
    d2 = (xx + x0 - cx) ** 2 + (yy + y0 - cy) ** 2
    return float(np.sqrt(d2.max()))


def fit_components(me_side, v_side, global_result, apply_natural=True,
                   cache=None):
    """Per-ME-component bounded refinement against same-owner targets.

    Returns [{owner, component_id, target_component_id, status,
    registration, metrics, mask}] in ME component order. status:
      registered          -- local affine inside the global-relative
                             bounds (natural layer may still taper on);
      global_fallback     -- refinement rejected/out of bounds; the
                             registration carries the global affine and
                             metrics.fallback_reason;
      unmatched_component -- no same-owner target inside the select cap
                             while the mapped footprint still lands on
                             same-owner vanilla land;
      excluded_inset      -- no same-owner target AND the mapped
                             footprint lands on foreign/unowned/
                             non-land (out-of-scope enclave).
    Unmatched/excluded components still carry the global-affine
    registration so downstream code can map them explicitly.
    """
    G = np.asarray(global_result["affine_me_to_vanilla"],
                   dtype=np.float64)
    mcache = _merged_cache(me_side, v_side, cache)
    v_by_owner = {}
    for cand in v_side["components"]:
        v_by_owner.setdefault(cand["owner"], []).append(cand)
    trees = {}
    out = []
    for comp in me_side["components"]:
        vc = cr._apply(G, np.asarray(comp["centroid_xy"],
                                     dtype=np.float64)[None, :])[0]
        cands = v_by_owner.get(comp["owner"], [])
        target, dist = _select_target(vc, cands, trees)
        extent = _component_extent(comp)
        radius = extent + NATURAL_RADIUS_PAD

        if target is None:
            shares = _footprint_shares(comp, v_side, G)
            same_share = shares["same_owner_land_share"]
            if cands and same_share is not None and \
                    same_share >= INSET_SAME_OWNER_SHARE:
                status = "unmatched_component"
                reason = ("no same-owner component within %.1f vanilla "
                          "px of the mapped centroid (nearest %.1f) yet "
                          "the footprint lands on same-owner land"
                          % (TARGET_SELECT_MAX_DIST,
                             dist if dist is not None else float("nan")))
            elif cands:
                status = "excluded_inset"
                reason = ("no same-owner component within %.1f vanilla "
                          "px and the mapped footprint lands on "
                          "foreign/unowned/non-land (same-owner land "
                          "share %s)" % (
                              TARGET_SELECT_MAX_DIST,
                              "%.3f" % same_share
                              if same_share is not None else "n/a"))
            else:
                status = "unmatched_component"
                reason = ("no same-owner component exists on the "
                          "vanilla landmass")
            out.append({
                "owner": comp["owner"],
                "component_id": comp["component_id"],
                "target_component_id": None,
                "status": status,
                "registration": _affine_dict(G, reason),
                "metrics": {
                    "fallback_reason": reason,
                    "nearest_candidate_distance_px": dist,
                    "mapped_centroid_xy": [float(vc[0]), float(vc[1])],
                    "extent_radius_me_px": extent,
                    "footprint": shares,
                },
                "mask": comp["mask"],
            })
            continue

        info = _boundary_icp(comp["boundary_points"],
                             target["boundary_points"], G,
                             LOCAL_MAX_DIST, LOCAL_MAD_Z,
                             LOCAL_MIN_PAIRS, LOCAL_MAX_ITERS,
                             LOCAL_TOL)
        A_local = info.pop("affine_np")
        if info["det_abort"] or info["n_pairs"] < LOCAL_MIN_PAIRS or \
                not info["converged"]:
            # an under-powered or unconverged refinement is a guess,
            # never a registration -- keep the global affine verbatim
            ok = False
            why = ("local boundary ICP under-powered/unconverged "
                   "(%d pairs, converged=%s)"
                   % (info["n_pairs"], info["converged"]))
            rel_sv = rel_det = None
        else:
            ok, why, rel_sv, rel_det = _local_transform_ok(G, A_local)
        base = A_local if ok else G
        status = "registered" if ok else "global_fallback"
        fallback_reason = why if not ok else None

        registration = _affine_dict(base, fallback_reason)
        natural = None
        if apply_natural:
            natural = cr.natural_feature_correction(
                v_side["map_dir"], me_side["map_dir"],
                [[float(x) for x in row] for row in base.tolist()],
                comp["centroid_xy"], radius, cache=mcache)
            registration = dict(natural)
            registration["registration_method"] = \
                "local_affine_plus_natural" if ok else \
                "global_plus_natural"
        else:
            registration["registration_method"] = \
                "local_affine" if ok else "global_passthrough"

        metrics = {
            "fallback_reason": fallback_reason,
            "local_converged": info["converged"],
            "local_iterations": info["iterations"],
            "local_pairs": info["n_pairs"],
            "local_pairs_by_class": info["pairs_by_class"],
            "local_residual_p50_px": info["residual_p50_px"],
            "local_residual_p95_px": info["residual_p95_px"],
            "local_rel_det": rel_det,
            "local_rel_sv": rel_sv,
            "target_distance_px": dist,
            "mapped_centroid_xy": [float(vc[0]), float(vc[1])],
            "extent_radius_me_px": extent,
            "natural_radius_me_px": float(radius),
        }
        if natural is not None:
            metrics["natural_ok"] = bool(natural["ok"])
            metrics["natural_method_used"] = natural["method_used"]
            metrics["natural_fallback_reason"] = \
                natural["fallback_reason"]
            metrics["natural_pairs_kept"] = natural["n_pairs_kept"]
        out.append({
            "owner": comp["owner"],
            "component_id": comp["component_id"],
            "target_component_id": int(target["component_id"]),
            "status": status,
            "registration": registration,
            "metrics": metrics,
            "mask": comp["mask"],
        })
    return out


# ------------------------------------------------------------ serialise

def _jsonable(obj):
    """Recursively convert numpy scalars/arrays for JSON output."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def serialisable_component(entry):
    """fit_components() entry minus the in-memory boolean mask."""
    out = {k: _jsonable(v) for k, v in entry.items() if k != "mask"}
    mask = entry.get("mask")
    if mask is not None:
        out["mask_shape"] = [int(v) for v in mask.shape]
    return out


# ------------------------------------------------------------------ main

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vanilla", default=HOI4)
    parser.add_argument("--mod", default=mod_root())
    parser.add_argument("--output", default=None,
                        help="write the registration JSON here")
    parser.add_argument("--no-natural", action="store_true",
                        help="skip the powered river/lake local layer")
    args = parser.parse_args(argv)

    cache = {}
    me = load_side(args.mod, cache=cache)
    van = load_side(args.vanilla, cache=cache)
    glob = fit_global(me, van)
    comps = fit_components(me, van, glob,
                           apply_natural=not args.no_natural,
                           cache=cache)
    result = {
        "global": _jsonable(glob),
        "components": [serialisable_component(c) for c in comps],
        "inputs": {
            "vanilla_map_dir": van["map_dir"],
            "mod_map_dir": me["map_dir"],
            "mod_components": len(me["components"]),
            "vanilla_components": len(van["components"]),
        },
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if glob["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
