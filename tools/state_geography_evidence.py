#!/usr/bin/env python
"""Batch candidate-independent physical geography evidence for ME states.

Inputs are map/provinces.bmp, definition.csv, rivers.bmp and history/states
province membership on both sides. English STATE_<id> localisation is used
only for the output label, never for fitting or voting. No identity table,
review queue, named anchor or control-point manifest is opened.

One global continent=1 land/sea coast affine is fitted with bbox initialisation
ONLY. A shared continent_registration map-side cache feeds every subsequent
river/lake correction. Each ME state's province pixel areas weight its centre
and RMS extent; the local circle also contains every province centroid. A pure
translation is fitted to mutual, distance-capped, robustly filtered river/lake
pairs, preserving the coast affine's scale and shear. This intentionally does
not use the less constrained local affine mode.

The existing natural votes backward-sample ME province centroids under each
local correction and weight them by province pixel areas. ALL province area is
their denominator: outside/sea/unassigned samples never inflate the share.

The appended overlap family instead samples every positive ME land-state pixel
in the common ME-native frame under the SINGLE bbox-initialised coast affine.
It never uses local corrections, canonical ancestry, or overlap-driven fitting.
Vanilla sea/lakes/outside/unassigned samples remain in each ME denominator.
Reverse containment divides the best intersection by that Vanilla state's
sampled positive area over the entire ME frame, not just ME land. It is a
diagnostic, not an acceptance gate. Four-connected land components must agree
with the largest component and independently meet the coverage/share/margin
gates; an unregistered or dissenting detached island withholds ancestry.
overlap_ok=1 additionally requires >=64 ME land pixels, assigned share>=0.90,
best share>=0.80 and margin>=0.25. The safe overlap marker is separate from
raw_geography_marker; neither new field changes canonical mappings.

Fixed TSV columns are COLUMNS below. natural_residual_p50/p95 are POST-local-
correction Euclidean residuals in vanilla pixels, recomputed from the kept
physical pairs; continent_registration's residuals field is pre-correction.
natural_ok=1 requires a successful, non-fallback translation, >=10 kept pairs,
p50<=6 and p95<=12. Failed natural quality produces no candidate at all.
raw_geography_ancestor additionally requires share>=0.85, margin>=0.60 and
sampled state area>=0.95. These fixed gates are deliberately not CLI overrides.
The marker records physical pair counts, fitting method, residuals and local
circle, not an identity assertion. It is one evidence family, not permission
to automatically rewrite a mapping without another independent family.

natural_reason explains withheld evidence: no_province_pixels,
missing_province_pixels, coast_fit_failed, no_feature_points,
no_matched_pairs, weak_pairs, fallback:<reason>, missing_residuals,
pair_count_mismatch, residual_p50, residual_p95, outside, unassigned,
low_sampled_share, low_share or low_margin. A blank runner means no distinct
second state was sampled (its share is zero); blank geometric diagnostics mean
that no local fit was attempted. Accepted rows have natural_reason=accepted.

Usage:
  python tools/state_geography_evidence.py --help
  python tools/state_geography_evidence.py --me-id 2624 --output _ctx/geo.tsv
  python -m unittest discover -s tools -p test_state_geography_evidence.py

Without --me-id the output contains one row per ME state, in numeric ID order.
Repeated inputs produce byte-identical UTF-8, LF-terminated TSVs. Dependencies:
only the existing numpy/scipy/Pillow stack and the standard library.
"""

import argparse
import csv
import os
from pathlib import Path
import re
from collections import Counter

import numpy as np
from scipy.ndimage import find_objects, label

import continent_registration as registration
from _modroot import mod_root, repo_root
from pdx_parse import Parser


COLUMNS = [
    "me_id", "me_name", "natural_ok", "natural_method", "natural_pairs",
    "natural_residual_p50", "natural_residual_p95", "natural_best",
    "natural_share", "natural_runner", "natural_runner_share", "natural_margin",
    "raw_geography_ancestor", "raw_geography_marker", "natural_reason",
    "natural_river_pairs", "natural_lake_pairs", "natural_center_x",
    "natural_center_y", "natural_radius", "natural_sampled_share",
]
OVERLAP_COLUMNS = [
    "overlap_ok", "overlap_method", "overlap_domain", "overlap_area_px",
    "overlap_best", "overlap_share", "overlap_runner", "overlap_runner_share",
    "overlap_margin", "overlap_assigned_share", "overlap_containment",
    "overlap_components", "overlap_largest_share", "overlap_largest_best",
    "overlap_largest_candidate_share", "overlap_components_agree",
    "overlap_unregistered_share", "overlap_ancestor", "overlap_marker",
    "overlap_reason",
]
COLUMNS += OVERLAP_COLUMNS
MIN_PAIRS = 10
MAX_RESIDUAL_P50 = 6.0
MAX_RESIDUAL_P95 = 12.0
MIN_SHARE = 0.85
MIN_MARGIN = 0.60
MIN_SAMPLED_SHARE = 0.95
MIN_RADIUS = 128.0       # ME px; supplies local context for compact states
RADIUS_PADDING = 64.0   # ME px; fixed physical context, never candidate-based
MIN_OVERLAP_AREA = 64
MIN_OVERLAP_ASSIGNED_SHARE = 0.90
MIN_OVERLAP_SHARE = 0.80
MIN_OVERLAP_MARGIN = 0.25
OVERLAP_CHUNK_PIXELS = 262_144
OVERLAP_METHOD = "coast_bbox_affine_raster"
OVERLAP_DOMAIN = "me_native_land"
OVERLAP_MARKER = "coast_bbox_affine_raster_overlap"
FOUR_CONNECTED = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
DEFAULT_OUTPUT = Path(repo_root()) / "docs" / "states" / "geography_evidence.tsv"


def _number(value):
    if value is None or value == "":
        return ""
    value = float(value)
    return f"{0.0 if abs(value) < 0.0000005 else value:.6f}"


def _state_names(root):
    """Match the canonical generator's label convention, not its identities."""
    names = {}
    pattern = re.compile(r'\s*(STATE_\d+)(?::\d+)?\s*[=:]?\s*"([^"]*)"')
    for path in sorted((Path(root) / "localisation" / "english").glob("*.yml")):
        with path.open(encoding="utf-8-sig", errors="replace") as stream:
            for line in stream:
                match = pattern.match(line)
                if match:
                    value = match[2].replace(r"\t", "").replace(r"\n", "").strip()
                    names.setdefault(match[1], value)
    return names


def load_states(root):
    """Read only state IDs, province membership and display labels.

    The shared Clausewitz parser ignores comments/quoted braces; history data
    is never used. Duplicate IDs or ambiguous province lists fail explicitly.
    """
    directory = Path(root) / "history" / "states"
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        raise FileNotFoundError(f"no history/states/*.txt under {root!s}")
    names, states = _state_names(root), {}
    for path in paths:
        # Keep the same ID source as build_state_map.py: the numeric filename
        # prefix. Legacy ports contain files whose in-block id differs from the
        # filename (and even duplicate filename prefixes); using the block id
        # would create geography rows that can never join canonical evidence.
        match = re.match(r"^(\d+)\s*-", path.name)
        if not match:
            raise ValueError(f"{path}: filename must start with a numeric state id")
        sid = int(match.group(1))
        parser = Parser(path.read_text(encoding="utf-8-sig", errors="replace"))
        blocks = [n for n in parser.parse() if n.key == "state" and n.is_block]
        if parser.errors or len(blocks) != 1:
            raise ValueError(f"{path}: malformed state: {parser.errors or 'expected one state block'}")
        children = blocks[0].children
        ids = [n.val for n in children if n.key == "id" and not n.is_block]
        province_blocks = [n for n in children if n.key == "provinces" and n.is_block]
        if len(ids) != 1 or not str(ids[0]).isdigit() or int(ids[0]) <= 0:
            raise ValueError(f"{path}: expected one positive state id")
        if len(province_blocks) > 1:
            raise ValueError(f"{path}: duplicate province list")
        provinces = []
        for node in province_blocks[0].children if province_blocks else []:
            if not node.key.isdigit() or node.val is not None or node.children != []:
                raise ValueError(f"{path}: non-numeric province list member")
            provinces.append(int(node.key))
        suffix = path.stem.partition("-")[2] or f"STATE_{sid}"
        states[sid] = {"name": names.get(f"STATE_{sid}") or suffix,
                       "provinces": sorted(set(provinces))}
    return states


def state_geometry(provinces, centroids, areas):
    """Return points, pixel-area weights, weighted centre and a local radius.

    RMS extent is area-weighted, and the maximum extent ensures that no sampled
    centroid uses the taper or a coast-only part of the natural map. Missing
    province pixels are not silently discarded from the denominator.
    """
    provinces = sorted(set(provinces))
    if not provinces:
        raise ValueError("no_province_pixels")
    present = [p for p in provinces
               if 0 < p < len(areas) and areas[p] > 0 and p in centroids]
    if not present:
        raise ValueError("no_province_pixels")
    if len(present) != len(provinces):
        raise ValueError("missing_province_pixels")
    points = np.asarray([centroids[p] for p in present], dtype=np.float64)
    weights = np.asarray(areas[present], dtype=np.float64)
    center = np.average(points, axis=0, weights=weights)
    squared = np.sum((points - center) ** 2, axis=1)
    rms = np.sqrt(np.average(squared, weights=weights))
    radius = max(MIN_RADIUS, float(np.sqrt(squared.max())) + RADIUS_PADDING,
                 2.0 * float(rms) + RADIUS_PADDING)
    return points, weights, center, radius


def _province_raster(root, cache):
    """Decode once for the census; natural/coast extraction shares its cache."""
    side = registration._load_side(root, cache=cache)
    directory = Path(side["map_dir"])
    return registration._rasterize(
        str(directory / "provinces.bmp"),
        registration._all_colours(str(directory / "definition.csv")))


def _state_raster(provinces, states):
    max_pid = max(0, int(provinces.max()), max(
        (p for state in states.values() for p in state["provinces"]), default=0))
    lut = np.full(max_pid + 1, -1, dtype=np.int32)
    for sid, state in sorted(states.items()):
        for pid in state["provinces"]:
            # State files can list dummy/sea/lake provinces for non-land
            # records. They are intentionally absent from the land raster;
            # only positive province IDs can index the state LUT.
            if pid <= 0:
                continue
            if lut[pid] != -1:
                raise ValueError(f"province {pid} belongs to multiple states")
            lut[pid] = sid
    return np.take(lut, provinces, mode="clip")


def _land_mask(root, provinces, cache):
    """All definition.csv land, irrespective of the coast fit's continent=1."""
    side = registration._load_side(root, cache=cache)
    lookup = np.zeros(max(0, int(provinces.max())) + 1, dtype=bool)
    path = Path(side["map_dir"]) / "definition.csv"
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream, delimiter=";"):
            if not row or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            if 0 < pid < len(lookup) and row[4].strip().lower() == "land":
                lookup[pid] = True
    # Dummy province 0, unknown colours, sea and lakes are always empty.
    return np.take(lookup, provinces, mode="clip")


def _overlap_vote(counts, vanilla_ids):
    """Rank dense positive-ID bins; the zero bin stays in the denominator."""
    area = int(counts.sum())
    ranked = np.flatnonzero(counts[1:]) + 1
    # Dense Vanilla IDs are ascending, so a stable count sort breaks ties by ID.
    ranked = ranked[np.argsort(-counts[ranked], kind="stable")]
    best_index = int(ranked[0]) if len(ranked) else 0
    runner_index = int(ranked[1]) if len(ranked) > 1 else 0
    best_count = int(counts[best_index]) if best_index else 0
    runner_count = int(counts[runner_index]) if runner_index else 0
    return {
        "area": area,
        "best": int(vanilla_ids[best_index - 1]) if best_index else "",
        "best_index": best_index,
        "best_count": best_count,
        "share": best_count / area,
        "runner": int(vanilla_ids[runner_index - 1]) if runner_index else "",
        "runner_share": runner_count / area,
        "margin": (best_count - runner_count) / area,
        "assigned_share": int(counts[1:].sum()) / area,
    }


def _overlap_coverage_reason(vote):
    if vote["assigned_share"] < MIN_OVERLAP_ASSIGNED_SHARE:
        return "low_assigned_share"
    if vote["share"] < MIN_OVERLAP_SHARE:
        return "low_share"
    if vote["margin"] < MIN_OVERLAP_MARGIN:
        return "low_margin"
    return ""


def _component_votes(state_mask, registered, state_counts, vanilla_ids):
    """Label only this state's bounding window, never a full raster per state."""
    components, count = label(state_mask, structure=FOUR_CONNECTED)
    occupied = components > 0
    component_ids = components[occupied].astype(np.int64) - 1
    # Use only this state's observed Vanilla IDs, plus the empty coverage bin.
    # A fragmented island state must not allocate components x all Vanilla IDs.
    candidate_bins = np.r_[0, np.flatnonzero(state_counts[1:]) + 1]
    sampled_bins = np.searchsorted(candidate_bins, registered[occupied])
    counts = np.bincount(
        component_ids * len(candidate_bins) + sampled_bins,
        minlength=count * len(candidate_bins),
    ).reshape(count, len(candidate_bins))
    candidate_ids = vanilla_ids[candidate_bins[1:] - 1]
    votes = [_overlap_vote(component_counts, candidate_ids) for component_counts in counts]
    # scipy's scan-order labels make equal-area largest components deterministic.
    largest = votes[int(np.argmax(counts.sum(axis=1)))]
    return votes, largest


def partition_census(source_state_raster, target_state_raster, affine,
                     *, target_land_mask=None, chunk_pixels=OVERLAP_CHUNK_PIXELS):
    """Full (source_id, target_id) partition census in the source-native frame.

    Same chunked pixel-centre sampling as the overlap family: source pixel
    centres are mapped through the bare 2x3 affine (float64 ufunc arithmetic,
    np.rint, rounded-outside pixels unassigned). Every source pixel is
    registered into a dense target-ID bin; positive source (land-state) pixels
    additionally populate the per-state count matrix and the zero-bin kind
    tallies. Non-positive targets are classified only when target_land_mask is
    given: 1=outside, 2=water/non-land, 3=land pixel with no state (unassigned).
    Returns None when the source raster holds no positive state pixels.
    """
    source, target = np.asarray(source_state_raster), np.asarray(target_state_raster)
    for name, raster in (("source", source), ("target", target)):
        if raster.ndim != 2 or raster.dtype.kind not in "iu":
            raise ValueError(f"{name} must be a 2-D integer state raster")
    matrix = np.asarray(affine, dtype=np.float64)
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all():
        raise ValueError("partition census requires a finite global 2x3 affine")
    if isinstance(chunk_pixels, (bool, np.bool_)) or \
            not isinstance(chunk_pixels, (int, np.integer)) or chunk_pixels <= 0:
        raise ValueError("chunk_pixels must be a positive integer")
    if target_land_mask is not None:
        target_land_mask = np.asarray(target_land_mask)
        if target_land_mask.shape != target.shape or \
                target_land_mask.dtype != np.bool_:
            raise ValueError("target_land_mask must be a bool raster shaped like target")
    source_ids = np.unique(source[source > 0])
    target_ids = np.unique(target[target > 0])
    if not len(source_ids):
        return None
    stride = len(target_ids) + 1
    counts = np.zeros((len(source_ids), stride), dtype=np.int64)
    kinds = np.zeros((len(source_ids), 4), dtype=np.int64)
    target_areas = np.zeros(stride, dtype=np.int64)
    dense_source = np.zeros(source.shape, dtype=np.int32)
    registered = np.zeros(source.shape, dtype=np.int32)
    source_flat, dense_flat = source.ravel(), dense_source.ravel()
    registered_flat = registered.ravel()
    height, width = target.shape
    for start in range(0, source.size, chunk_pixels):
        stop = min(start + chunk_pixels, source.size)
        positions = np.arange(start, stop, dtype=np.int64)
        x = (positions % source.shape[1]).astype(np.float64)
        y = (positions // source.shape[1]).astype(np.float64)
        # Explicit ufunc arithmetic avoids chunk-size-dependent BLAS rounding.
        with np.errstate(over="ignore", invalid="ignore"):
            mapped_x = x * matrix[0, 0] + y * matrix[0, 1] + matrix[0, 2]
            mapped_y = x * matrix[1, 0] + y * matrix[1, 1] + matrix[1, 2]
        inside = np.isfinite(mapped_x) & np.isfinite(mapped_y) & \
            (mapped_x >= 0) & (mapped_x < width) & \
            (mapped_y >= 0) & (mapped_y < height)
        selected = np.flatnonzero(inside)
        xx = np.rint(mapped_x[selected]).astype(np.int64)
        yy = np.rint(mapped_y[selected]).astype(np.int64)
        rounded_inside = (xx < width) & (yy < height)
        selected = selected[rounded_inside]
        sampled_ids = target[yy[rounded_inside], xx[rounded_inside]]
        positive = sampled_ids > 0
        sampled = registered_flat[start:stop]
        sampled[selected[positive]] = np.searchsorted(
            target_ids, sampled_ids[positive]) + 1
        target_areas += np.bincount(sampled, minlength=stride)

        land = source_flat[start:stop] > 0
        if not land.any():
            continue
        present, inverse = np.unique(source_flat[start:stop][land], return_inverse=True)
        state_indices = np.searchsorted(source_ids, present)
        dense_flat[start:stop][land] = state_indices[inverse] + 1
        if target_land_mask is not None:
            # Kind 0 = positive target ID; outside the target frame wins over
            # water/unassigned because the pixel never reaches the target raster.
            kind = np.ones(land.sum(), dtype=np.int64)
            kind[np.isin(np.flatnonzero(land), selected)] = 0
            inside_land = np.isin(selected, np.flatnonzero(land))
            inside_kind = np.where(
                positive, 0,
                np.where(target_land_mask[yy[rounded_inside], xx[rounded_inside]], 3, 2))
            # Order of inverse/kind arrays matches the order of
            # source_flat[start:stop][land], so flat land indices locate pixels.
            land_flat_index = np.flatnonzero(land)
            kind[np.searchsorted(land_flat_index, selected[inside_land])] = \
                inside_kind[inside_land]
            kinds[state_indices] += np.bincount(
                inverse * 4 + kind, minlength=len(present) * 4,
            ).reshape(len(present), 4)
        # Chunk-local dense source bins avoid repeatedly zeroing a full matrix.
        local_counts = np.bincount(
            inverse * stride + sampled[land], minlength=len(present) * stride,
        ).reshape(len(present), stride)
        counts[state_indices] += local_counts

    return {
        "source_ids": source_ids,
        "target_ids": target_ids,
        # counts[i, j>0]: source_ids[i] pixels sampled onto target_ids[j-1];
        # counts[i, 0]: sampled non-positive (outside/water/unassigned).
        "counts": counts,
        # target_areas[j>0]: ALL source-frame pixels (land and non-land) sampled
        # onto target_ids[j-1]; target_areas[0]: the non-positive bin.
        "target_areas": target_areas,
        "source_area_px": counts.sum(axis=1),
        # kinds[i]: [0=positive, 1=outside, 2=water, 3=land-unassigned] source
        # land pixel tallies; zeros when target_land_mask is not supplied.
        "kinds": kinds,
        # Dense source labels (index+1 into source_ids) and sampled target bins;
        # kept for downstream component labelling without a second raster pass.
        "dense_source": dense_source,
        "registered": registered,
    }


def raster_overlap_evidence(me_state_raster, vanilla_state_raster, coast_affine,
                            *, chunk_pixels=OVERLAP_CHUNK_PIXELS):
    """Measure one shared coast-affine partition census in the ME-native frame.

    Inputs contain positive land-state IDs and non-positive empty pixels. The
    bare 2x3 affine is the global bbox coast fit, NOT a local natural correction.
    Dense ID indexing bounds count storage by actual state counts, not raw IDs.
    Deterministic float64 arithmetic and np.rint match pixel-centre sampling;
    geometric and rounded outside pixels both remain unassigned. Sampling the
    full frame also supplies reverse-coverage denominators for containment.
    """
    census = partition_census(
        me_state_raster, vanilla_state_raster, coast_affine,
        chunk_pixels=chunk_pixels)
    if census is None:
        return {}
    me_ids = census["source_ids"]
    vanilla_ids = census["target_ids"]
    counts = census["counts"]
    vanilla_areas = census["target_areas"]
    dense_me = census["dense_source"]
    registered = census["registered"]
    rows = {}
    bounds = find_objects(dense_me, max_label=len(me_ids))
    for index, sid in enumerate(me_ids):
        vote = _overlap_vote(counts[index], vanilla_ids)
        window = bounds[index]
        components, largest = _component_votes(
            dense_me[window] == index + 1, registered[window], counts[index], vanilla_ids)
        agree = all(component["best"] == largest["best"] and component["best"] != "" and
                    not _overlap_coverage_reason(component) for component in components)
        if vote["area"] < MIN_OVERLAP_AREA:
            reason = "insufficient_area"
        elif len(components) > 1 and not agree:
            reason = "component_disagreement"
        else:
            reason = _overlap_coverage_reason(vote)
            if not reason:
                reason = "accepted" if agree else "component_disagreement"
        containment = ""
        if vote["best_index"] and vanilla_areas[vote["best_index"]] > 0:
            containment = _number(vote["best_count"] / vanilla_areas[vote["best_index"]])
        best = str(vote["best"]) if vote["best"] != "" else ""
        row = dict.fromkeys(OVERLAP_COLUMNS, "")
        row.update(
            overlap_ok="1" if reason == "accepted" else "0",
            overlap_method=OVERLAP_METHOD, overlap_domain=OVERLAP_DOMAIN,
            overlap_area_px=str(vote["area"]), overlap_best=best,
            overlap_share=_number(vote["share"]),
            overlap_runner=str(vote["runner"]) if vote["runner"] != "" else "",
            overlap_runner_share=_number(vote["runner_share"]),
            overlap_margin=_number(vote["margin"]),
            overlap_assigned_share=_number(vote["assigned_share"]),
            overlap_containment=containment, overlap_components=str(len(components)),
            overlap_largest_share=_number(largest["area"] / vote["area"]),
            overlap_largest_best=str(largest["best"]) if largest["best"] != "" else "",
            overlap_largest_candidate_share=_number(largest["share"]),
            overlap_components_agree="1" if agree else "0",
            overlap_unregistered_share=_number(1.0 - vote["assigned_share"]),
            overlap_reason=reason,
        )
        if reason == "accepted":
            row.update(overlap_ancestor=best, overlap_marker=OVERLAP_MARKER)
        rows[int(sid)] = row
    return rows


def vote_centroids(points, weights, natural, vanilla_state_raster):
    """Area census at backwards-sampled native pixel centres; never renormalise."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    weights = np.asarray(weights, dtype=np.float64)
    if len(points) != len(weights) or not np.isfinite(weights).all() or \
            (weights <= 0).any() or weights.sum() <= 0:
        raise ValueError("no_province_pixels")
    total = float(weights.sum())
    mapped = registration.map_points(natural, points)
    height, width = vanilla_state_raster.shape
    inside = np.isfinite(mapped).all(axis=1) & \
        (mapped[:, 0] >= 0) & (mapped[:, 0] < width) & \
        (mapped[:, 1] >= 0) & (mapped[:, 1] < height)
    selected = np.flatnonzero(inside)
    xy = np.rint(mapped[selected]).astype(np.int64)
    rounded_inside = (xy[:, 0] < width) & (xy[:, 1] < height)
    selected, xy = selected[rounded_inside], xy[rounded_inside]
    sampled = vanilla_state_raster[xy[:, 1], xy[:, 0]]
    assigned = sampled > 0
    votes = {}
    for sid, weight in zip(sampled[assigned], weights[selected[assigned]]):
        sid = int(sid)
        votes[sid] = votes.get(sid, 0.0) + float(weight)
    ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    result = {"natural_best": "", "natural_share": "", "natural_runner": "",
              "natural_runner_share": 0.0, "natural_margin": "",
              "natural_sampled_share": sum(votes.values()) / total,
              "reason": "outside" if not len(selected) else "unassigned"}
    if ranked:
        best, count = ranked[0]
        runner, runner_count = ranked[1] if len(ranked) > 1 else ("", 0.0)
        result.update(natural_best=best, natural_share=count / total,
                      natural_runner=runner, natural_runner_share=runner_count / total,
                      natural_margin=(count - runner_count) / total, reason="")
    return result


def _residuals(natural):
    """Residuals after the correction, over the actual kept physical pairs."""
    correction = np.asarray(natural["correction_affine"], dtype=np.float64)
    norms, counts = [], {}
    for feature in ("river", "lake"):
        match = natural.get("matches", {}).get(feature, {})
        pairs = match.get("pairs", {})
        source = np.asarray(pairs.get("me_vanilla_xy", []), dtype=np.float64).reshape(-1, 2)
        target = np.asarray(pairs.get("vanilla_xy", []), dtype=np.float64).reshape(-1, 2)
        counts[feature] = int(match.get("n_kept", 0))
        if len(source) != len(target) or len(source) != counts[feature]:
            return None, None, counts, "pair_count_mismatch"
        if len(source):
            corrected = source @ correction[:, :2].T + correction[:, 2]
            norms.extend(np.linalg.norm(target - corrected, axis=1))
    if len(norms) != int(natural.get("n_pairs_kept", 0)):
        return None, None, counts, "pair_count_mismatch"
    if not norms or not np.isfinite(norms).all():
        return None, None, counts, "missing_residuals"
    return float(np.percentile(norms, 50)), float(np.percentile(norms, 95)), counts, ""


def _empty_row(me_id, me_name, reason):
    row = dict.fromkeys(COLUMNS, "")
    row.update(me_id=str(me_id), me_name=me_name, natural_ok="0", natural_pairs="0",
               natural_river_pairs="0", natural_lake_pairs="0", natural_reason=reason)
    return row


def evidence_row(me_id, me_name, points, weights, natural, vanilla_state_raster):
    """Build one diagnostic/evidence row, suppressing candidates on fit failure."""
    row = _empty_row(me_id, me_name, "")
    pairs = int(natural.get("n_pairs_kept", 0))
    method = natural.get("method_used", "coast")
    p50, p95, counts, residual_reason = _residuals(natural)
    center = natural["center_me_xy"]
    row.update(natural_method=method, natural_pairs=str(pairs),
               natural_residual_p50=_number(p50), natural_residual_p95=_number(p95),
               natural_river_pairs=str(counts.get("river", 0)),
               natural_lake_pairs=str(counts.get("lake", 0)),
               natural_center_x=_number(center[0]), natural_center_y=_number(center[1]),
               natural_radius=_number(natural["radius_me_px"]))
    if pairs == 0:
        matches = natural.get("matches", {}).values()
        has_features = any(m.get("n_me_region", 0) and m.get("n_vanilla_region", 0)
                           for m in matches)
        reason = "no_matched_pairs" if has_features else "no_feature_points"
    elif pairs < MIN_PAIRS:
        reason = "weak_pairs"
    elif not natural.get("ok") or natural.get("fallback_reason") is not None or method == "coast":
        reason = "fallback:" + str(natural.get("fallback_reason") or "coast_only")
    elif method != "translation":
        reason = "unsupported_method"
    elif residual_reason:
        reason = residual_reason
    elif p50 > MAX_RESIDUAL_P50:
        reason = "residual_p50"
    elif p95 > MAX_RESIDUAL_P95:
        reason = "residual_p95"
    else:
        reason = ""
    if reason:
        row["natural_reason"] = reason
        return row
    row["natural_ok"] = "1"
    votes = vote_centroids(points, weights, natural, vanilla_state_raster)
    for column in ("natural_best", "natural_runner"):
        row[column] = str(votes[column]) if votes[column] != "" else ""
    for column in ("natural_share", "natural_runner_share", "natural_margin", "natural_sampled_share"):
        row[column] = _number(votes[column])
    if votes["natural_best"] == "":
        reason = votes["reason"]
    elif votes["natural_sampled_share"] < MIN_SAMPLED_SHARE:
        reason = "low_sampled_share"
    elif votes["natural_share"] < MIN_SHARE:
        reason = "low_share"
    elif votes["natural_margin"] < MIN_MARGIN:
        reason = "low_margin"
    else:
        reason = "accepted"
        row["raw_geography_ancestor"] = row["natural_best"]
        row["raw_geography_marker"] = (
            f"coast_bbox_icp;local_river_lake_translation;river_pairs={counts['river']};"
            f"lake_pairs={counts['lake']};residual_p50_px={_number(p50)};"
            f"residual_p95_px={_number(p95)};center_me_xy={_number(center[0])},{_number(center[1])};"
            f"radius_me_px={_number(natural['radius_me_px'])}")
    row["natural_reason"] = reason
    return row


def produce_evidence(vanilla_root, me_root, me_ids=None):
    """One global raster census/fit, plus selected states' existing local fits."""
    me_states = load_states(me_root)
    selected = sorted(me_states) if me_ids is None else sorted(set(me_ids))
    unknown = sorted(set(selected) - me_states.keys())
    if unknown:
        raise ValueError("unknown ME state id(s): " + ",".join(map(str, unknown)))
    vanilla_states = load_states(vanilla_root)
    cache = {}
    coast = registration.fit_coast_warp(vanilla_root, me_root, cache=cache, initial="bbox")
    if not coast["ok"]:
        return [_empty_row(sid, me_states[sid]["name"], "coast_fit_failed")
                for sid in selected], coast
    vanilla_provinces = _province_raster(vanilla_root, cache)
    vanilla = _state_raster(vanilla_provinces, vanilla_states)
    me_provinces = _province_raster(me_root, cache)
    flat = me_provinces.ravel()
    areas = np.bincount(flat[flat >= 0])
    overlaps = {}
    me_land = vanilla_land = None
    try:
        me_land = _state_raster(me_provinces, me_states)
        me_land[~_land_mask(me_root, me_provinces, cache) | (me_land <= 0)] = 0
        vanilla_land = np.where(
            _land_mask(vanilla_root, vanilla_provinces, cache) & (vanilla > 0), vanilla, 0)
        overlaps = raster_overlap_evidence(
            me_land, vanilla_land, coast["affine_me_to_vanilla"])
    except (OSError, ValueError, TypeError, IndexError):
        # Overlap is optional: missing/invalid raster data must not change any
        # natural-field value, reason, candidate, or marker in the old family.
        pass
    del flat, me_provinces, vanilla_provinces, me_land, vanilla_land
    centroids = registration._load_side(me_root, cache=cache)["centroids"]
    rows = []
    for sid in selected:
        state = me_states[sid]
        try:
            points, weights, center, radius = state_geometry(state["provinces"], centroids, areas)
        except ValueError as error:
            row = _empty_row(sid, state["name"], str(error))
        else:
            natural = registration.natural_feature_correction(
                vanilla_root, me_root, coast["affine_me_to_vanilla"], center, radius,
                cache=cache, min_pairs=MIN_PAIRS, method="translation")
            row = evidence_row(sid, state["name"], points, weights, natural, vanilla)
        row.update(overlaps.get(sid, {}))
        rows.append(row)
    return rows, coast


def write_evidence(output_path, rows):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: int(row["me_id"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vanilla", default=registration.DEFAULT_VANILLA,
                        help="vanilla game root (default: HOI4_DIR or standard Steam install)")
    parser.add_argument("--mod", default=None, help="ME root (default: ME_MOD or repository mod)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output TSV (default: %(default)s)")
    parser.add_argument("--me-id", type=int, action="append", metavar="ID",
                        help="only this ME state; repeat for a small diagnostic batch")
    args = parser.parse_args(argv)
    try:
        rows, coast = produce_evidence(args.vanilla, args.mod or mod_root(), args.me_id)
        write_evidence(args.output, rows)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    counts = Counter(row["natural_reason"].split(":", 1)[0] for row in rows)
    good = sum(row["natural_ok"] == "1" for row in rows)
    accepted = sum(bool(row["raw_geography_ancestor"]) for row in rows)
    print(f"states={len(rows)} natural_ok={good} raw_geography={accepted} withheld={len(rows) - accepted}")
    print(f"coast_ok={int(coast['ok'])} init=bbox matches={coast['n_matches']}")
    print("reasons: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    print(f"output={os.path.abspath(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
