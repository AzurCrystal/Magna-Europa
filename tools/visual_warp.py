#!/usr/bin/env python
"""Evidence-driven local displacement-field warp (VISUAL AID prototype).

The existing review warps cannot express Magna Europa's local map
stretch:

  - "coastline" is one global 2x3 affine: zero interior DOF; the fit
    averages the continent-wide residual field, so any region whose
    local stretch differs from the global mean keeps a residual that
    no translation/rotation/scale can remove.
  - "natural" composes ONE correction affine (or translation) inside a
    disc. At Iletsk 2624 it converges cleanly yet still leaves a
    residual p95 ~13 px: after it, the remaining displacement field
    has a spatially varying direction/magnitude -- a non-linear local
    stretch. Aligning the northern boundary pushes the southern
    boundary off; a single affine has no DOF to bend a boundary.
  - "vp" is piecewise affine on ~560 province-centroid anchors. It CAN
    bend, but only at triangle vertices that are (a) far from most
    state boundaries, (b) noisy pixel centroids reproduced exactly by
    the interpolant, and (c) sparse enough that large triangles
    extrapolate a stretch observed tens of km away. Folded simplices
    are masked out, so the map has holes precisely where the anchor
    triangulation misleads it.

This module adds a bounded free-form deformation on top of the frozen
coast affine:

    F(x) = A @ x + w(x) * D(x)                    x in ME px
    w    : smoothstep taper, 1 inside radius r0 in ME space, 0 at
           r0 * blend -- the field NEVER affects points beyond the
           finite support.
    D(x) : a bilinear lattice displacement field fitted in ME space to
           residual evidence pairs, solved as a regularised least
           squares (data + graph-Laplacian smoothness + ridge-to-zero).

Evidence, all expressed as residual vectors r_i = v_i - A @ m_i at
ME-space source positions m_i:

  - "anchor":   named VP/province centroid pairs from
                docs/states/registration_anchors.tsv (the canonical
                build manifest). Position evidence only; it carries
                state names but never decides which vanilla state a ME
                state is -- it pulls the picture toward verified named
                locations.
  - "natural":  river/lake mutual-NN matches, reusing
                continent_registration._feature_match unchanged
                (same cap/MAD filtering as the natural mode).
  - "boundary": OPTIONAL, candidate-conditioned. Boundary pixels of
                the target ME state matched (mutual NN + cap + MAD)
                against the boundary pixels of its adjudicated vanilla
                ancestor, and likewise for raster-adjacent ME
                neighbours. This is the only evidence class that can
                place DOF exactly where alignment is judged -- on the
                shared border. It is a VISUAL AID: it must be clearly
                labelled in every artefact and never cited as proof of
                a state identity.

Classes are capped so no class can dominate by sheer point count
(rivers produce thousands of stroke pixels; anchors may be ~dozens).

Guards, same philosophy as the coast/natural fits:
  - finite support (taper to the coast affine at r1),
  - analytic Jacobian of F sampled on a grid covering the support;
    min det must stay above det(A)*NAT_JAC_MIN_RATIO or the solve is
    retried once with stronger smoothing and then abandoned to the
    identity field + explicit fallback_reason -- never a folded map,
  - no canonical data (m2v_states.tsv etc.) is ever modified; the
    boundary evidence only READS the adjudicated ancestors.

Returns a JSON-serialisable dict consumed by WarpMap.from_visual and
visual_map_points(); deliberately a superset of the
natural_feature_correction() result shape.
"""

import os
import sys

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import continent_registration as cr  # noqa: E402
from continent_registration import (  # noqa: E402
    GRID_CELL, EDGE_MARGIN, MIN_LAKE_PX, NAT_MAX_DIST, NAT_MAD_Z,
    NAT_BLEND, NAT_JAC_MIN_RATIO, DEFAULT_ANCHORS,
    _apply, _as_affine, _feature_match, _mutual_nn_match,
    _region_select, _grid_downsample, _pct,
)


# ---------------------------------------------------------------- evidence

def _anchor_evidence(m_side, v_side, A, center, radius, anchors_path,
                     max_dist=None):
    """registration_anchors.tsv -> in-region residual pairs.

    Returns (src_me (N,2), resid (N,2), rows). Points are selected by
    ME-space distance to `center` (radius is the support radius r1 +
    cell pad decided by the caller). No filtering: the manifest is the
    canonical cleaned pair list; degenerate rows are dropped by missing
    centroids only.
    """
    src, dst, rows = [], [], []
    if not os.path.isfile(anchors_path):
        return (np.zeros((0, 2)), np.zeros((0, 2)), [])
    c = np.asarray(center, dtype=np.float64)
    import csv as _csv
    with open(anchors_path, encoding="utf-8") as f:
        for r in _csv.DictReader(f, delimiter="\t"):
            mp, vp = int(r["me_prov"]), int(r["v_prov"])
            if mp not in m_side["centroids"] or \
                    vp not in v_side["centroids"]:
                continue
            mxy = np.asarray(m_side["centroids"][mp], dtype=np.float64)
            if np.linalg.norm(mxy - c) > radius:
                continue
            vxy = np.asarray(v_side["centroids"][vp], dtype=np.float64)
            src.append(mxy)
            dst.append(vxy)
            rows.append({"me_prov": mp, "v_prov": vp,
                         "me_state": int(r["me_state"]),
                         "v_state": int(r["v_state"]),
                         "me_xy": [float(mxy[0]), float(mxy[1])],
                         "v_xy": [float(vxy[0]), float(vxy[1])]})
    if not src:
        return (np.zeros((0, 2)), np.zeros((0, 2)), [])
    src = np.asarray(src)
    dst = np.asarray(dst)
    return src, dst - _apply(A, src), rows


def _natural_evidence(m_side, v_side, A, center, radius, max_dist,
                      mad_z):
    """Reuse the natural match verbatim; invert A to get ME-space srcs."""
    feats, src_v, dst_v, vc = _feature_match(
        m_side, v_side, A, center, radius, max_dist, mad_z, None)
    if len(src_v):
        Ainv = np.linalg.inv(A[:, :2])
        src_m = (src_v - A[:, 2]) @ Ainv.T
    else:
        src_m = np.zeros((0, 2))
    return src_m, dst_v - src_v, feats


def state_boundary_points(state_raster, state_id, grid_cell=4):
    """(N,2) boundary-pixel centres of `state_id`, grid-downsampled."""
    mask = state_raster == state_id
    edge = np.zeros_like(mask)
    edge[1:, :] |= mask[1:, :] != mask[:-1, :]
    edge[:-1, :] |= mask[1:, :] != mask[:-1, :]
    edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    edge[:, :-1] |= mask[:, 1:] != mask[:, :-1]
    ys, xs = np.nonzero(edge & mask)
    if not len(xs):
        return np.zeros((0, 2))
    return _grid_downsample(
        np.stack([xs, ys], axis=1).astype(np.float64), grid_cell)


def me_state_neighbours(state_raster, state_id):
    """Raster-adjacent (8-conn) other state ids of `state_id`."""
    mask = state_raster == state_id
    from scipy.ndimage import binary_dilation
    ring = binary_dilation(mask, structure=np.ones((3, 3), bool)) & ~mask
    vals = state_raster[ring]
    vals = vals[vals >= 0]
    return sorted(int(v) for v in np.unique(vals))

def shared_segment(state_raster, id_a, id_b, grid_cell=4):
    """Border segment between states id_a/id_b: pixels of a touching b
    plus pixels of b touching a, grid-downsampled (N,2) xy.

    id_a == id_b (e.g. two ME states adjudicated to the SAME vanilla
    ancestor) means the ME border is an internal divider with no
    vanilla counterpart; every pixel of the state is self-adjacent, so
    the computation below would return the whole raster -- return
    empty instead.
    """
    if id_a == id_b:
        return np.zeros((0, 2))
    from scipy.ndimage import binary_dilation
    a = state_raster == id_a
    b = state_raster == id_b
    seg = (a & binary_dilation(b, structure=np.ones((3, 3), bool))) | \
          (b & binary_dilation(a, structure=np.ones((3, 3), bool)))
    ys, xs = np.nonzero(seg)
    if not len(xs):
        return np.zeros((0, 2))
    return _grid_downsample(
        np.stack([xs, ys], axis=1).astype(np.float64), grid_cell)


def _boundary_evidence(boundary_pairs, A, center, radius, max_dist,
                       mad_z):
    """Match each supplied (me_xy, v_xy) boundary cloud by mutual NN.

    boundary_pairs: [{"me_xy","v_xy","label"}] -- e.g. label=
    {"me":[2624,464],"v":[652,402]} for the ME border segment between
    states 2624/464 matched against the vanilla border segment
    between ancestors 652/402. Region-selects the ME cloud to the
    support disc and the vanilla cloud to the mapped reach box, then
    mutual-NN + cap + MAD.

    Returns (src_me, resid, detail list). Explicitly candidate-
    conditioned: detail carries the state ids so every consumer can
    label it a visual aid.
    """

    src_all, res_all, detail = [], [], []
    for bp in boundary_pairs:
        m_b = np.asarray(bp["me_xy"], dtype=np.float64).reshape(-1, 2)
        v_b = np.asarray(bp["v_xy"], dtype=np.float64).reshape(-1, 2)
        me_sel, v_sel, _ = _region_select(
            m_b, v_b, A, center, radius, max_dist)
        r = _mutual_nn_match(_apply(A, me_sel), v_sel, max_dist, mad_z)
        if r["n_kept"] >= 8:
            kept_src_v, kept_res, n_kept, tag = (
                r["me_vanilla_xy"], r["residual_xy"], r["n_kept"],
                "mutual")
        else:
            # Border segments are thin 1-D clouds: mutual-NN starves
            # them. Directed NN (each mapped ME pixel -> nearest
            # vanilla segment px) + cap + MAD gives enough pairs to
            # place DOF on the border; duplicates are harmless to a
            # least-squares fit.
            mm = _apply(A, me_sel)
            if len(v_sel):
                dd, jj = cKDTree(v_sel).query(mm)
                sel = dd <= max_dist
                resid = v_sel[jj[sel]] - mm[sel]
                if sel.sum() >= 3:
                    med = np.median(resid, axis=0)
                    dev = np.linalg.norm(resid - med, axis=1)
                    mad = float(np.median(dev))
                    cut = (mad_z * 1.4826 * mad) if mad > 0 else \
                        float(np.quantile(dev, 0.8))
                    keep = np.flatnonzero(sel)[dev <= cut]
                else:
                    keep = np.flatnonzero(sel)
                kept_src_v = mm[keep]
                kept_res = v_sel[jj[keep]] - mm[keep] \
                    if len(keep) else np.zeros((0, 2))
                n_kept, tag = int(len(keep)), "directed"
            else:
                kept_src_v = np.zeros((0, 2))
                kept_res = np.zeros((0, 2))
                n_kept, tag = 0, "directed"
        detail.append({
            "label": bp.get("label"),
            "matching": tag,
            "n_me_region": int(len(me_sel)),
            "n_vanilla_region": int(len(v_sel)),
            "n_mutual": r["n_mutual"], "n_capped": r["n_capped"],
            "n_kept": n_kept,
            "residual_p50_px": _pct(
                np.linalg.norm(kept_res, axis=1), 50)
            if n_kept else None,
            "candidate_conditioned": True,
            "purpose": "visual aid only - boundary agreement hint, "
                       "not identity evidence",
        })
        if not n_kept:
            continue
        Ainv = np.linalg.inv(A[:, :2])
        src_all.append((kept_src_v - A[:, 2]) @ Ainv.T)
        res_all.append(kept_res)
    if not src_all:
        return np.zeros((0, 2)), np.zeros((0, 2)), detail
    return np.concatenate(src_all), np.concatenate(res_all), detail


# ------------------------------------------------------------- field fit

def _bilinear_rows(pts, origin, cell, nx, ny):
    """(N,4) node indices + (N,4) weights; mask of in-lattice points."""
    u = (pts - np.asarray(origin, dtype=np.float64)) / float(cell)
    i = np.floor(u).astype(np.int64)
    f = u - i
    ok = (i[:, 0] >= 0) & (i[:, 0] < nx - 1) & \
         (i[:, 1] >= 0) & (i[:, 1] < ny - 1)
    iu = np.clip(i[:, 0], 0, nx - 2)
    iv = np.clip(i[:, 1], 0, ny - 2)
    idx = np.stack([iv * nx + iu, iv * nx + iu + 1,
                    (iv + 1) * nx + iu, (iv + 1) * nx + iu + 1], axis=1)
    fx, fy = f[:, 0], f[:, 1]
    w = np.stack([(1 - fx) * (1 - fy), fx * (1 - fy),
                  (1 - fx) * fy, fx * fy], axis=1)
    w[~ok] = 0.0
    return idx, w, ok


def _solve_lattice(src, resid, wts, center, r_support, cell,
                   lam_smooth, lam_ridge):
    """Regularised bilinear-lattice displacement field.

    minimise  sum_i w_i |D(x_i) - r_i|^2
            + lam_smooth * sum_edges |d_u - d_v|^2
            + lam_ridge  * sum_nodes |d_u|^2

    Returns dict(origin, cell, nx, ny, disp(ny,nx,2), n_evidence).
    Normal equations are SPD; solved per component with spsolve.
    """
    c = np.asarray(center, dtype=np.float64)
    half = float(r_support) + float(cell)
    origin = c - half
    n_side = int(np.ceil(2.0 * half / float(cell))) + 1
    nx = ny = n_side
    n_nodes = nx * ny

    idx, bw, ok = _bilinear_rows(src, origin, cell, nx, ny)
    src, resid, wts = src[ok], resid[ok], wts[ok]
    idx, bw = idx[ok], bw[ok]

    # data block: each evidence point touches 4 nodes
    rows = np.repeat(np.arange(len(src)), 4)
    L = sparse.csr_matrix(
        (bw.ravel(), (rows, idx.ravel())),
        shape=(len(src), n_nodes))

    # smoothness: horizontal + vertical neighbour differences
    eu, ev = [], []
    for j in range(ny):
        for i in range(nx):
            n = j * nx + i
            if i + 1 < nx:
                eu.append(n); ev.append(n + 1)
            if j + 1 < ny:
                eu.append(n); ev.append(n + nx)
    eu = np.asarray(eu); ev = np.asarray(ev)
    E = sparse.csr_matrix(
        (np.r_[np.ones(len(eu)), -np.ones(len(eu))],
         (np.r_[np.arange(len(eu)), np.arange(len(eu))],
          np.r_[eu, ev])),
        shape=(len(eu), n_nodes))

    W = sparse.diags(np.asarray(wts, dtype=np.float64))
    N = L.T @ W @ L \
        + float(lam_smooth) * (E.T @ E) \
        + float(lam_ridge) * sparse.identity(n_nodes)
    rhsx = L.T @ (wts * resid[:, 0])
    rhsy = L.T @ (wts * resid[:, 1])
    dx = spla.spsolve(N.tocsc(), rhsx)
    dy = spla.spsolve(N.tocsc(), rhsy)
    disp = np.stack([dx, dy], axis=1).reshape(ny, nx, 2)
    return {"origin": [float(origin[0]), float(origin[1])],
            "cell_px": float(cell), "nx": int(nx), "ny": int(ny),
            "disp": disp, "n_evidence": int(len(src)),
            "lam_smooth": float(lam_smooth),
            "lam_ridge": float(lam_ridge)}


def _lattice_eval(field, pts):
    """Bilinear D(x); (N,2), zero outside the lattice."""
    origin = np.asarray(field["origin"], dtype=np.float64)
    cell = field["cell_px"]; nx, ny = field["nx"], field["ny"]
    disp = field["disp"]
    idx, bw, ok = _bilinear_rows(np.asarray(pts, dtype=np.float64),
                                 origin, cell, nx, ny)
    flat = disp.reshape(-1, 2)
    d = (bw[:, 0, None] * flat[idx[:, 0]] +
         bw[:, 1, None] * flat[idx[:, 1]] +
         bw[:, 2, None] * flat[idx[:, 2]] +
         bw[:, 3, None] * flat[idx[:, 3]])
    d[~ok] = 0.0
    return d


def _taper(pts, center, r0, r1):
    """(w, dw/dxy): smoothstep 1 inside r0, 0 at r1 (ME space)."""
    c = np.asarray(center, dtype=np.float64)
    d = np.linalg.norm(pts - c, axis=1)
    t = np.clip((r1 - d) / max(r1 - r0, 1e-9), 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)
    dt = np.where((d > r0) & (d < r1) & (r1 > r0),
                  -6.0 * t * (1.0 - t) / (r1 - r0), 0.0)
    grad = np.zeros((len(pts), 2))
    nz = d > 1e-9
    grad[nz] = (dt[nz] / d[nz])[:, None] * (pts[nz] - c)
    return w, grad


def _field_jacobian_guard(A, field, center, r0, r1, step):
    """Sampled min det of J(F) over the support disc.

    F(x) = A x + w(x) D(x)  =>  J = A + w dD + D x grad(w)^T.
    dD is piecewise-constant per lattice cell (bilinear); sampled on a
    `step`-px grid so every cell interior (cell >> step) is visited.
    """
    base_lin = A[:, :2]
    det_base = float(np.linalg.det(base_lin))
    c = np.asarray(center, dtype=np.float64)
    g = np.arange(-r1, r1 + step, step)
    gx, gy = np.meshgrid(g, g)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1) + c
    sel = np.linalg.norm(pts - c, axis=1) <= r1 + 1e-6
    pts = pts[sel]

    w, gw = _taper(pts, c, r0, r1)
    D = _lattice_eval(field, pts)

    # cell-local bilinear gradient of D
    origin = np.asarray(field["origin"])
    cell = field["cell_px"]; nx, ny = field["nx"], field["ny"]
    u = (pts - origin) / cell
    i = np.floor(u).astype(np.int64)
    f = u - i
    iu = np.clip(i[:, 0], 0, nx - 2); iv = np.clip(i[:, 1], 0, ny - 2)
    inside = (i[:, 0] >= 0) & (i[:, 0] < nx - 1) & \
             (i[:, 1] >= 0) & (i[:, 1] < ny - 1)
    disp = field["disp"]
    d00 = disp[iv, iu];     d10 = disp[iv, iu + 1]
    d01 = disp[iv + 1, iu]; d11 = disp[iv + 1, iu + 1]
    fx = f[:, 0:1]; fy = f[:, 1:2]
    dD_du = (1 - fy) * (d10 - d00) + fy * (d11 - d01)   # /du
    dD_dv = (1 - fx) * (d01 - d00) + fx * (d11 - d10)   # /dv
    # D lives in vanilla space, u/v in ME space -> dD/dx = dD/du / cell
    dD = np.zeros((len(pts), 2, 2))
    dD[:, :, 0] = dD_du / cell
    dD[:, :, 1] = dD_dv / cell
    dD[~inside] = 0.0

    J = base_lin[None, :, :] + w[:, None, None] * dD + \
        D[:, :, None] * gw[:, None, :]
    det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
    finite = np.isfinite(det)
    if not finite.all():
        return None, "non-finite field Jacobian", det_base
    min_det = float(det.min())
    floor = max(abs(det_base) * NAT_JAC_MIN_RATIO, 1e-6)
    if min_det < floor:
        return min_det, ("field Jacobian too small or folded "
                         "(min_det=%.6g floor=%.6g)" %
                         (min_det, floor)), det_base
    # sanity magnitudes for review
    sv = np.linalg.svd(J, compute_uv=False)
    info = {"max_singular": float(sv[:, 0].max()),
            "min_singular": float(sv[:, 1].min()),
            "max_disp_px": float(np.linalg.norm(D, axis=1).max())
            if len(D) else 0.0}
    return min_det, info, det_base


# ------------------------------------------------------------------ entry

def visual_feature_warp(vanilla_root, mod_root, affine, center, radius,
                        cache=None, grid_cell=GRID_CELL,
                        margin=EDGE_MARGIN, min_lake_px=MIN_LAKE_PX,
                        max_dist=NAT_MAX_DIST, mad_z=NAT_MAD_Z,
                        blend=NAT_BLEND,
                        anchors_path=None,
                        boundary_pairs=None,
                        bnd_max_dist=28.0, bnd_mad_z=NAT_MAD_Z,
                        cell_px=None, lam_smooth=2.0, lam_ridge=0.25,
                        class_weight=None, pair_cap=240,
                        jac_step=8.0):
    """Fit the visual displacement field. See module docstring.

    boundary_pairs: None (off) or list of {"me_xy","v_xy","label"}
        boundary point clouds (see _boundary_evidence).
        Candidate-conditioned visual aid, always labelled in output.
    """
    A = _as_affine(affine)
    if cache is None:
        cache = {}
    v = cr._load_side(vanilla_root, grid_cell, margin, cache,
                      min_lake_px)
    m = cr._load_side(mod_root, grid_cell, margin, cache, min_lake_px)
    center = np.asarray(center, dtype=np.float64)
    r0 = float(radius)
    r1 = r0 * float(blend)
    cell = float(cell_px) if cell_px else max(24.0, r0 / 10.0)
    anchors_path = anchors_path or DEFAULT_ANCHORS
    cw = {"anchor": 1.0, "natural": 0.5, "boundary": 2.0}
    if class_weight:
        cw.update(class_weight)

    ev = {}          # class -> (src_me, resid)
    a_src, a_res, a_rows = _anchor_evidence(
        m, v, A, center, r0 + cell, anchors_path)

    ev["anchor"] = (a_src, a_res)
    n_src, n_res, feats = _natural_evidence(
        m, v, A, center, r0, max_dist, mad_z)
    ev["natural"] = (n_src, n_res)
    b_detail = []
    if boundary_pairs:
        b_src, b_res, b_detail = _boundary_evidence(
            boundary_pairs, A, center, r0 + cell, bnd_max_dist,
            bnd_mad_z)
        ev["boundary"] = (b_src, b_res)

    srcs, resds, wts, ev_report = [], [], [], {}
    for cls, (s, r) in ev.items():
        n = len(s)
        norms = np.linalg.norm(r, axis=1) if n else np.zeros(0)
        w = cw.get(cls, 1.0) * (min(n, pair_cap) / max(n, 1))
        ev_report[cls] = {
            "n_pairs": int(n), "weight_total": float(w * n),
            "residual_pre_p50_px": _pct(norms, 50),
            "residual_pre_p95_px": _pct(norms, 95),
            "residual_pre_median_xy":
                [float(x) for x in np.median(r, axis=0)] if n else None,
        }
        if n:
            srcs.append(s); resds.append(r)
            wts.append(np.full(n, w))
    if srcs:
        src = np.concatenate(srcs)
        resid = np.concatenate(resds)
        wts = np.concatenate(wts)
    else:
        src = np.zeros((0, 2)); resid = np.zeros((0, 2))
        wts = np.zeros(0)

    reason, field, used = None, None, "coast"
    jac_min, jac_info = None, {}
    if len(src) < 6:
        reason = ("too few residual evidence pairs: %d < 6"
                  % len(src))
    else:
        lam_s, lam_r = lam_smooth, lam_ridge
        for attempt in (0, 1):
            field = _solve_lattice(src, resid, wts, center, r1, cell,
                                   lam_s, lam_r)
            jac_min, jac_info, det_base = _field_jacobian_guard(
                A, field, center, r0, r1, jac_step)
            if isinstance(jac_info, dict):
                used = "field"
                break
            if attempt == 0:
                lam_s *= 8.0; lam_r *= 4.0
            else:
                reason = jac_info
                field = None
                used = "coast"

    # post-fit residuals (evidence satisfaction, NOT accuracy proof)
    if field is not None:
        D_at = _lattice_eval(field, src)
        w_at, _ = _taper(src, center, r0, r1)
        post = resid - w_at[:, None] * D_at
        pn = np.linalg.norm(post, axis=1)
        post_stats = {"p50_px": _pct(pn, 50), "p95_px": _pct(pn, 95)}
        # per-class post residuals
        off = 0
        for cls, (s, r) in ev.items():
            k = len(s)
            if k:
                cn = np.linalg.norm(post[off:off + k], axis=1)
                ev_report[cls]["residual_post_p50_px"] = _pct(cn, 50)
                ev_report[cls]["residual_post_p95_px"] = _pct(cn, 95)
            off += k
    else:
        post_stats = None

    vc = _apply(A, center[None, :])[0]
    ident = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    notes = [
        "VISUAL AID mode: produces overlay alignment for review; "
        "never identity proof and never written into canonical TSVs",
        "F(x) = A@x + w(x) D(x): coast affine + bilinear lattice "
        "displacement field, smoothstep-tapered to the coast affine "
        "between radius and radius*blend (finite support)",
        "anchor class = registration_anchors.tsv named pairs "
        "(position evidence only); natural class = river/lake "
        "mutual-NN residuals (same matcher as natural mode)",
        "class weights %s, per-class pair cap %d -> total weight "
        "capped so rivers cannot swamp named anchors" % (cw, pair_cap),
        "Jacobian of F sampled every %.1f ME px over the support; "
        "min det floor = det(A)*%.3f; fold => stronger smoothing "
        "retry => identity field + fallback_reason"
        % (jac_step, NAT_JAC_MIN_RATIO),
    ]
    boundary_active = bool(
        boundary_pairs and any(d.get("n_kept", 0) for d in b_detail))
    if boundary_active:
        n_b = sum(d["n_kept"] for d in b_detail)
        notes.append("BOUNDARY EVIDENCE ACTIVE (%d pairs): matched ME "
                     "state boundary pixels to adjudicated-ancestor "
                     "boundaries -- candidate-conditioned visual aid "
                     "ONLY; it aligns the overlay picture and must "
                     "never be cited as proof the mapping is correct"
                     % n_b)
    if reason:
        notes.append("field abandoned: " + reason)

    out_field = None
    if field is not None:
        out_field = dict(field)
        out_field["disp"] = field["disp"].reshape(-1, 2).tolist()
    return {
        "ok": reason is None,
        "mode": "visual",
        "fallback_reason": reason,
        "method_used": used,
        "center_me_xy": [float(center[0]), float(center[1])],
        "center_vanilla_xy": [float(vc[0]), float(vc[1])],
        "radius_me_px": r0,
        "blend_radius_factor": float(blend),
        "affine_me_to_vanilla": A.tolist(),
        "affine_combined": A.tolist(),       # base layer only
        "affine_combined_det": float(np.linalg.det(A[:, :2])),
        "correction_affine": ident.tolist(),  # field replaces it
        "field": out_field,
        "field_jacobian_min_det": jac_min,
        "field_jacobian_info": jac_info if isinstance(jac_info, dict)
        else {},
        "jacobian_sample_step_px": float(jac_step),
        "evidence": ev_report,
        "residuals_post_fit": post_stats,
        "matches": feats,
        "boundary_evidence": b_detail,
        "boundary_requested": bool(boundary_pairs),
        "candidate_conditioned": boundary_active,
        "n_anchor_rows": len(a_rows),
        "anchor_rows": a_rows,
        "params": {
            "grid_cell": grid_cell, "edge_margin": margin,
            "min_lake_px": min_lake_px, "max_dist": max_dist,
            "mad_z": mad_z, "blend": blend, "cell_px": cell,
            "lam_smooth": lam_smooth, "lam_ridge": lam_ridge,
            "class_weight": cw, "pair_cap": pair_cap,
            "bnd_max_dist": bnd_max_dist, "bnd_mad_z": bnd_mad_z,
        },
        "inputs": {
            "vanilla_map_dir": v["map_dir"], "mod_map_dir": m["map_dir"],
            "anchors_tsv": anchors_path,
        },
        "notes": notes,
    }


def visual_map_points(registration, pts):
    """ME (N,2) -> vanilla (N,2) under a visual_feature_warp result."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    A = _as_affine(registration["affine_me_to_vanilla"])
    base = _apply(A, pts)
    field = registration.get("field")
    if field is None or registration.get("fallback_reason"):
        return base
    f = {"origin": field["origin"], "cell_px": field["cell_px"],
         "nx": field["nx"], "ny": field["ny"],
         "disp": np.asarray(field["disp"], dtype=np.float64)
                 .reshape(field["ny"], field["nx"], 2)}
    D = _lattice_eval(f, pts)
    c = np.asarray(registration["center_me_xy"], dtype=np.float64)
    r0 = float(registration["radius_me_px"])
    r1 = r0 * float(registration.get("blend_radius_factor", NAT_BLEND))
    w, _ = _taper(pts, c, r0, r1)
    return base + w[:, None] * D
