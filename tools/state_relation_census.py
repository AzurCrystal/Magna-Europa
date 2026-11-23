#!/usr/bin/env python
"""Province-level crosswalk census for topology-registered state rasters.

This module measures *sampled* pixel overlap between a source state raster
(ME side) and a target state raster (vanilla side) under an already-fitted
registration. It is candidate-independent: no victory points, names,
comments, canonical ancestry, or review queue enter the counting. The only
inputs are the two state rasters, the target land mask, a source component
mask, the registration dict produced by state_relation_registration (any
``continent_registration.map_points``-compatible value: a correction dict, a
fit_coast_warp dict, or a bare 2x3 affine), and the vanilla border band.

Every source pixel whose flat position is selected by ``source_mask`` is
mapped through ``continent_registration.map_points`` in float64, rounded to
the nearest target pixel centre with ``np.rint``, and classified exactly
once:

- ``outside``:    the mapped point is outside the target frame, either
                  geometrically or after rounding to a pixel centre;
- ``water``:      the sampled target pixel is inside the frame but not in
                  ``target_land_mask`` and carries no positive state id
                  (sea or lake);
- ``unassigned``: the sampled pixel is target land with no positive state
                  id;
- ``uncertain``:  the sampled pixel carries a positive state id but lies in
                  ``border_band``, the dilated vanilla-boundary band, so a
                  +/-radius registration error could have landed it on a
                  different state or on water — it is evidence for the
                  (source, target) cell but not a confident hit;
- ``confident``:  the sampled pixel carries a positive state id outside the
                  border band.

Confident and uncertain hits both accumulate on their (source_id,
target_id) cell; every positive cell is retained in ``rows`` and is never
collapsed into a winner-take-all vote. Reverse coverage is tracked in
target-frame terms, not source-frame hits: ``reverse_hits_px`` counts the
UNIQUE confident target pixels the cell's samples landed on within this
component (border-band hits are uncertain and never count as confident
reverse hits). Unique means several source samples landing on the same
target pixel still count once, so per-component ``reverse_share`` is a
true coverage ratio bounded by the target state's area. Each row reports
its explicit denominators: ``source_area_px`` (the source state's native
pixel count), ``source_sampled_px`` (the masked pixels this census
actually sampled for that source id, the ``forward_share`` denominator),
and ``target_native_area_px`` (the target state's native pixel count, the
``reverse_share`` denominator, so ``reverse_share`` is a sampled coverage
estimate against the whole target state, not only against its observed
part).

The ``claim`` field is fixed to ``sampled_area_overlap``: raster overlap is
sampled/area evidence only. Nothing here is a ``proven`` or
``complete_subset`` relation; classification assigns deterministic
``one_to_one`` / ``one_to_many`` / ``many_to_one`` / ``many_to_many`` /
``uncertain`` labels from significant confident cells and keeps uncertain
rows visible instead of dropping them.

Coordinates are provinces.bmp x/y with no y-flip. Repeated inputs produce
identical output: rows are sorted by (source_id, target_id) and all counts
are int64 tallies. Dependencies: numpy, scipy.ndimage.binary_dilation, and
the sibling tools/continent_registration.py module.
"""

import numpy as np
from scipy.ndimage import binary_dilation

import continent_registration


# Chunked flat-pixel processing keeps bounded memory on full-map rasters;
# 262144 matches the existing overlap family's bound.
CHUNK_PIXELS = 262_144
# A confident cell needs at least this many sampled pixels to count as a
# significant relation edge; below it the edge stays visible but cannot
# drive the one_to_*/many_to_* classification.
MIN_RELATION_PX = 64
# Default half-width of the uncertain band, in target pixels (Chebyshev).
BORDER_BAND_RADIUS = 2

# Relation classes emitted by classify_relation_matrix.
RELATION_CLASSES = (
    "one_to_one", "one_to_many", "many_to_one", "many_to_many", "uncertain",
)

# Fixed row-level claim: sampled area evidence only, never a proof.
CLAIM = "sampled_area_overlap"

# Status strings for census rows.
STATUS_CONFIDENT = "sampled_confident"
STATUS_UNCERTAIN_ONLY = "sampled_uncertain_only"

_BOUNDARY_STRUCTURE = np.ones((3, 3), dtype=bool)


def _id_raster(raster, name):
    """Accept 2-D integer array-likes without coercing floats or booleans."""
    arr = np.asarray(raster)
    if arr.ndim != 2:
        raise ValueError(f"{name} must be a 2-D integer state raster")
    if arr.dtype.kind not in "iu":
        raise TypeError(
            f"{name} must have an integer dtype; booleans are not state IDs")
    return arr


def _bool_raster(mask, name, shape):
    arr = np.asarray(mask)
    if arr.shape != shape or arr.dtype != np.bool_:
        raise ValueError(f"{name} must be a bool raster shaped {shape}")
    return arr


def _positive_int(value, name, allow_zero=False):
    if isinstance(value, (bool, np.bool_)) or \
            not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    if allow_zero:
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
    elif value <= 0:
        raise ValueError(f"{name} must be > 0")
    return int(value)


def _state_boundary(raster):
    """Positive-side 4-neighbour state-boundary pixels, frame edge included.

    A positive pixel is a boundary if any orthogonal neighbour holds a
    different value (other state, water, unassigned) or lies beyond the
    frame. Both land sides of a state border are marked; water and other
    non-positive pixels are never seeds of the band. Matches the
    partition_matching.boundary_mask convention.
    """
    boundary = np.zeros(raster.shape, dtype=bool)
    if not raster.shape[0] or not raster.shape[1]:
        return boundary
    boundary[0, :] = True
    boundary[-1, :] = True
    boundary[:, 0] = True
    boundary[:, -1] = True
    different = raster[1:, :] != raster[:-1, :]
    boundary[1:, :] |= different
    boundary[:-1, :] |= different
    different = raster[:, 1:] != raster[:, :-1]
    boundary[:, 1:] |= different
    boundary[:, :-1] |= different
    boundary &= raster > 0
    return boundary


def build_border_band(v_state_raster, radius=BORDER_BAND_RADIUS):
    """Boolean uncertain band within ``radius`` px of vanilla state borders.

    Seeds are positive-side 4-neighbour boundary pixels (frame edge counts
    as a boundary, since there is nothing beyond it to sample onto). The
    bounded dilation uses a 3x3 (Chebyshev) structuring element applied
    ``radius`` times, so no pixel more than ``radius`` steps from a seed is
    marked and the raster never wraps: pixels near one edge are never
    marked by boundaries on the opposite edge. ``radius=0`` returns just
    the seed boundary pixels.
    """
    raster = _id_raster(v_state_raster, "v_state_raster")
    radius = _positive_int(radius, "radius", allow_zero=True)
    seeds = _state_boundary(raster)
    if radius == 0 or not seeds.any():
        return seeds
    return binary_dilation(
        seeds, structure=_BOUNDARY_STRUCTURE, iterations=radius)


def _empty_result():
    return {
        "kind": "state_relation_census",
        "claim": CLAIM,
        "rows": [],
        "source_totals": {},
        "target_ids": [],
        "masked_px": 0,
        "sampled_state_px": 0,
    }


def census_component(source_state_raster, target_state_raster,
                     target_land_mask, source_mask, registration,
                     border_band, min_relation_px=MIN_RELATION_PX):
    """Sample masked source pixels onto the target state raster.

    ``source_mask`` selects source-frame pixels (normally one connected
    owner-landmass component). Both state pixels and non-positive pixels
    inside the mask are mapped; only positive source state pixels
    accumulate per-(source_id, target_id) cells and per-source kind
    tallies, so masked water/outside padding never inflates an edge.

    ``registration`` is any ``continent_registration.map_points``-compatible
    value: a natural_feature_correction dict (local correction tapered to
    its ``affine_me_to_vanilla``), a fit_coast_warp/global dict, or a bare
    2x3 affine.

    Returns a JSON-serialisable dict:
      rows:          one dict per positive (source_id, target_id) cell,
                     sorted by (source_id, target_id), each with
                     confident/uncertain hit counts, reverse_hits_px
                     (unique confident target pixels covered by this
                     cell's samples, the ``reverse_share`` numerator),
                     explicit denominators, forward/reverse sampled
                     shares, a ``significant`` flag (confident_hits_px >=
                     min_relation_px), and status/claim fields;
      source_totals: per positive source_id dict with source_area_px
                     (native state pixel count), source_sampled_px
                     (masked pixels sampled for this census), and
                     confident/uncertain/outside/water/unassigned
                     tallies (they sum to source_sampled_px);
      target_ids:    sorted positive target state ids present in the
                     target raster (dense bin order used internally);
      masked_px:     total source_mask pixels;
      sampled_state_px: masked pixels carrying a positive source state id.
    """
    source = _id_raster(source_state_raster, "source_state_raster")
    target = _id_raster(target_state_raster, "target_state_raster")
    _positive_int(min_relation_px, "min_relation_px")
    target_land_mask = _bool_raster(
        target_land_mask, "target_land_mask", target.shape)
    source_mask = _bool_raster(
        source_mask, "source_mask", source.shape)
    border_band = _bool_raster(
        border_band, "border_band", target.shape)
    # Validate early: map_points-compatible registration values are checked
    # for shape/finiteness/positive determinant before any pixel is mapped.
    continent_registration.map_points(registration, np.zeros((1, 2)))

    target_ids = np.unique(target[target > 0])
    n_tgt = len(target_ids)
    flat_source = source.ravel()
    positions = np.flatnonzero(source_mask.ravel())
    src_w = source.shape[1]
    tgt_h, tgt_w = target.shape

    if not positions.size:
        return _empty_result()

    masked_state = positions[flat_source[positions] > 0]
    if not masked_state.size:
        return _empty_result()
    source_ids = np.unique(flat_source[masked_state])
    n_src = len(source_ids)
    cells_confident = np.zeros((n_src, n_tgt), dtype=np.int64)
    cells_uncertain = np.zeros((n_src, n_tgt), dtype=np.int64)
    kinds = np.zeros((n_src, 5), dtype=np.int64)
    sampled_per_source = np.zeros(n_src, dtype=np.int64)
    # Reverse coverage is counted in target pixels, not source samples:
    # ``reverse_seen`` holds the set of unique confident target pixels
    # hit per source bin, encoded as int64
    # (source_dense_index * target_size + target_flat_index), so several
    # source samples landing on the same target pixel count once and
    # per-component reverse_share stays a bounded coverage ratio.
    # Border-band hits are uncertain and never enter the set.
    reverse_seen = set()
    target_size = tgt_h * tgt_w
    # Native denominators: the whole positive state raster, independent of
    # the component mask, so forward/reverse shares stay comparable across
    # components that split a state.
    source_native = np.bincount(
        flat_source[flat_source > 0],
        minlength=int(source_ids.max()) + 1)

    for start in range(0, positions.size, CHUNK_PIXELS):
        pos = positions[start:start + CHUNK_PIXELS]
        # float64 pixel-centre coordinates, provinces.bmp x/y, no y-flip.
        x = (pos % src_w).astype(np.float64)
        y = (pos // src_w).astype(np.float64)
        pts = np.stack([x, y], axis=1)
        with np.errstate(over="ignore", invalid="ignore"):
            mapped = continent_registration.map_points(registration, pts)
            mx = mapped[:, 0]
            my = mapped[:, 1]
        inside = np.isfinite(mx) & np.isfinite(my) & \
            (mx >= 0) & (mx < tgt_w) & (my >= 0) & (my < tgt_h)
        sel = np.flatnonzero(inside)
        xx = np.rint(mx[sel]).astype(np.int64)
        yy = np.rint(my[sel]).astype(np.int64)
        # Rounding can push a geometrically inside point onto a pixel centre
        # outside the frame; those join the outside denominator.
        keep = (xx < tgt_w) & (yy < tgt_h)
        kept_sel = sel[keep]
        xx = xx[keep]
        yy = yy[keep]
        sampled_ids = target[yy, xx]
        positive = sampled_ids > 0

        # Per-masked-pixel source bin (-1 = non-positive source pixel).
        src_vals = flat_source[pos]
        si = np.searchsorted(source_ids, src_vals)
        si[src_vals <= 0] = -1
        state_idx = np.flatnonzero(si >= 0)
        sampled_per_source += np.bincount(
            si[state_idx], minlength=n_src)

        # kind per state pixel: 0 confident, 1 uncertain, 2 outside,
        # 3 water, 4 unassigned. Geometric-outside and rounded-outside are
        # both kind 2.
        kind = np.full(si.size, -1, dtype=np.int64)
        kind[state_idx] = 2
        kept_kind = np.where(
            positive, 0,
            np.where(target_land_mask[yy, xx], 4, 3))
        kept_kind[positive & border_band[yy, xx]] = 1
        kind[kept_sel] = kept_kind
        kind[sel[~keep]] = 2
        kinds += np.bincount(
            si[state_idx] * 5 + kind[state_idx],
            minlength=n_src * 5).reshape(n_src, 5)

        # Only positive SOURCE pixels accumulate cells: masked non-state
        # pixels may sample a target state but are not a state overlap.
        hit_si = si[kept_sel][positive]
        state_hit = hit_si >= 0
        if not state_hit.any():
            continue
        sbin = hit_si[state_hit]
        yy_hit = yy[positive][state_hit]
        xx_hit = xx[positive][state_hit]
        tbin = np.searchsorted(
            target_ids, sampled_ids[positive][state_hit])
        band_hit = border_band[yy_hit, xx_hit]
        counts = np.bincount(
            sbin * n_tgt + tbin, minlength=n_src * n_tgt)
        if band_hit.any():
            uncertain_counts = np.bincount(
                (sbin * n_tgt + tbin)[band_hit], minlength=n_src * n_tgt)
            cells_uncertain += uncertain_counts.reshape(n_src, n_tgt)
            cells_confident += (counts - uncertain_counts
                                ).reshape(n_src, n_tgt)
        else:
            cells_confident += counts.reshape(n_src, n_tgt)
        # Confident (non-band) hits also feed the unique-target-pixel
        # reverse coverage set; int64-encoded (source bin, target flat).
        conf = ~band_hit
        if conf.any():
            reverse_seen.update(
                (sbin[conf] * target_size
                 + yy_hit[conf] * tgt_w + xx_hit[conf]).tolist())

    cells_reverse = np.zeros((n_src, n_tgt), dtype=np.int64)
    if reverse_seen:
        codes = np.fromiter(reverse_seen, dtype=np.int64)
        decoded_tbin = np.searchsorted(
            target_ids, target.ravel()[codes % target_size])
        cells_reverse = np.bincount(
            (codes // target_size) * n_tgt + decoded_tbin,
            minlength=n_src * n_tgt).reshape(n_src, n_tgt)

    target_native = np.bincount(
        target.ravel()[target.ravel() > 0],
        minlength=int(target_ids.max()) + 1 if n_tgt else 1)

    rows = []
    source_totals = {}
    for si_idx, sid in enumerate(source_ids):
        confident_row = cells_confident[si_idx]
        uncertain_row = cells_uncertain[si_idx]
        reverse_row = cells_reverse[si_idx]
        native_px = int(source_native[sid])
        sampled_px = int(sampled_per_source[si_idx])
        source_totals[int(sid)] = {
            "source_area_px": native_px,
            "source_sampled_px": sampled_px,
            "confident_px": int(kinds[si_idx, 0]),
            "uncertain_px": int(kinds[si_idx, 1]),
            "outside_px": int(kinds[si_idx, 2]),
            "water_px": int(kinds[si_idx, 3]),
            "unassigned_px": int(kinds[si_idx, 4]),
        }
        positive_bins = np.flatnonzero(
            (confident_row > 0) | (uncertain_row > 0))
        for tbin in positive_bins:
            confident_px = int(confident_row[tbin])
            uncertain_px = int(uncertain_row[tbin])
            reverse_px = int(reverse_row[tbin])
            tid = int(target_ids[tbin])
            tgt_native_px = int(target_native[tid]) \
                if tid < len(target_native) else 0
            rows.append({
                "source_id": int(sid),
                "target_id": tid,
                "confident_hits_px": confident_px,
                "uncertain_hits_px": uncertain_px,
                "source_area_px": native_px,
                "source_sampled_px": sampled_px,
                "forward_share": confident_px / sampled_px
                if sampled_px else 0.0,
                "target_native_area_px": tgt_native_px,
                "reverse_hits_px": reverse_px,
                "reverse_share": min(
                    reverse_px / tgt_native_px
                    if tgt_native_px else 0.0, 1.0),
                "significant": confident_px >= min_relation_px,
                "status": STATUS_CONFIDENT if confident_px
                else STATUS_UNCERTAIN_ONLY,
                "claim": CLAIM,
            })
    rows.sort(key=lambda row: (row["source_id"], row["target_id"]))

    return {
        "kind": "state_relation_census",
        "claim": CLAIM,
        "rows": rows,
        "source_totals": source_totals,
        "target_ids": [int(t) for t in target_ids],
        "masked_px": int(positions.size),
        "sampled_state_px": int(sampled_per_source.sum()),
    }


def _row_key(row):
    return (int(row["source_id"]), int(row["target_id"]))


def classify_relation_matrix(rows, min_relation_px=MIN_RELATION_PX):
    """Assign a deterministic relation class to every census row.

    ``rows`` is an iterable of census_component()-style cell dicts. Cells
    sharing (source_id, target_id) are merged first — hit counts sum and
    denominators keep the max — so duplicate components can never inflate a
    degree. ``reverse_hits_px`` (each component's UNIQUE confident target
    pixels) sums the same way, but distinct component registrations may
    sample the same target pixel, so the merged sum can exceed
    ``target_native_area_px``: ``reverse_share`` is then capped at 1.0 and
    ``reverse_overlap_possible`` is set, never emitting a ratio > 1. A
    merged cell is *significant* when confident_hits_px >=
    min_relation_px. Let forward_degree(source) and
    reverse_degree(target) count significant partners on each side; then:

      1 : 1   -> one_to_one
      1 : n>1 -> one_to_many      (one source, several targets)
      m>1 : 1 -> many_to_one      (several sources, one target)
      m>1 : n>1 -> many_to_many
      else    -> uncertain        (no significant confident cell, or the
                                   row itself is below threshold)

    Classification never drops or merges away a positive edge: every input
    cell yields exactly one output row carrying ``relation`` and the
    refreshed ``significant`` flag. Returns a new list of new dicts sorted
    by (source_id, target_id); inputs are not mutated.
    """
    _positive_int(min_relation_px, "min_relation_px")
    merged = {}
    for row in rows:
        key = _row_key(row)
        if key in merged:
            acc = merged[key]
            acc["confident_hits_px"] += int(row["confident_hits_px"])
            acc["uncertain_hits_px"] += int(row["uncertain_hits_px"])
            # Unique-target-pixel reverse hits add across components;
            # registrations may overlap, so the cap/flag below applies.
            acc["reverse_hits_px"] += int(row["reverse_hits_px"])
            # Sampled denominators add (each component covers distinct
            # source pixels); native areas are whole-state constants and
            # keep the max, never a sum.
            acc["source_sampled_px"] += int(row["source_sampled_px"])
            acc["source_area_px"] = max(
                acc["source_area_px"], int(row["source_area_px"]))
            acc["target_native_area_px"] = max(
                acc["target_native_area_px"],
                int(row["target_native_area_px"]))
        else:
            acc = {
                "source_id": key[0],
                "target_id": key[1],
                "confident_hits_px": int(row["confident_hits_px"]),
                "uncertain_hits_px": int(row["uncertain_hits_px"]),
                "source_area_px": int(row["source_area_px"]),
                "source_sampled_px": int(row["source_sampled_px"]),
                "target_native_area_px": int(row["target_native_area_px"]),
                "reverse_hits_px": int(row["reverse_hits_px"]),
            }
            merged[key] = acc

    forward_degree = {}
    reverse_degree = {}
    for key, acc in merged.items():
        if acc["confident_hits_px"] >= min_relation_px:
            forward_degree[key[0]] = forward_degree.get(key[0], 0) + 1
            reverse_degree[key[1]] = reverse_degree.get(key[1], 0) + 1

    out = []
    for key, acc in merged.items():
        confident_px = acc["confident_hits_px"]
        significant = confident_px >= min_relation_px
        if significant:
            fdeg = forward_degree.get(key[0], 0)
            rdeg = reverse_degree.get(key[1], 0)
            if fdeg == 1 and rdeg == 1:
                relation = "one_to_one"
            elif fdeg == 1:
                relation = "many_to_one"
            elif rdeg == 1:
                relation = "one_to_many"
            else:
                relation = "many_to_many"
        else:
            relation = "uncertain"
        area_px = acc["source_area_px"]
        sampled_px = acc["source_sampled_px"]
        native_px = acc["target_native_area_px"]
        out.append({
            "source_id": key[0],
            "target_id": key[1],
            "confident_hits_px": confident_px,
            "uncertain_hits_px": acc["uncertain_hits_px"],
            "source_area_px": area_px,
            "source_sampled_px": sampled_px,
            "forward_share": confident_px / sampled_px
            if sampled_px else 0.0,
            "target_native_area_px": native_px,
            "reverse_hits_px": acc["reverse_hits_px"],
            "reverse_share": min(
                acc["reverse_hits_px"] / native_px
                if native_px else 0.0, 1.0),
            "reverse_overlap_possible": 1
            if acc["reverse_hits_px"] > native_px else 0,
            "significant": significant,
            "relation": relation,
            "status": STATUS_CONFIDENT if confident_px
            else STATUS_UNCERTAIN_ONLY,
            "claim": CLAIM,
        })
    out.sort(key=_row_key)
    return out
