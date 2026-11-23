"""Candidate-independent partition diagnostics for two same-grid state rasters.

Both inputs must already occupy the same display frame. Positive integer IDs
identify land states; zero and negative IDs are empty (water or outside).
These functions measure spatial overlap only: no canonical membership, state
ancestry, fitting, or relabelling is consulted or inferred. NumPy is the only
non-standard-library dependency, and no function mutates its inputs.
"""

import numpy as np


BACKGROUND_RGBA = (224, 224, 224, 255)
ME_BOUNDARY_RGBA = (31, 68, 50, 255)
VANILLA_BOUNDARY_RGBA = (206, 92, 24, 255)
VANILLA_DASH_PERIOD = 8
VANILLA_DASH_LENGTH = 4


def _id_raster(ids, name):
    """Accept 2-D integer array-likes without coercing floats or booleans."""
    raster = np.asarray(ids)
    if raster.ndim != 2:
        raise ValueError(f"{name} must be a 2-D integer ID raster")
    if raster.dtype.kind not in "iu":
        raise TypeError(f"{name} must have an integer dtype; booleans are not state IDs")
    return raster


def _same_grid(me_ids, vanilla_ids):
    me = _id_raster(me_ids, "me_ids")
    vanilla = _id_raster(vanilla_ids, "vanilla_ids")
    if me.shape != vanilla.shape:
        raise ValueError("me_ids and vanilla_ids must have the same shape")
    return me, vanilla


def boundary_mask(ids):
    """Return a boolean mask of the positive side of 4-neighbour boundaries.

    A positive pixel is a boundary if any orthogonal neighbour has a different
    ID, including water, or is beyond the frame. Both land sides of a state
    border are marked, but empty pixels are never marked. Diagonal neighbours
    do not contribute. Zero-width/height integer rasters are supported.
    """
    ids = _id_raster(ids, "ids")
    boundary = np.zeros(ids.shape, dtype=np.bool_)
    if not ids.shape[0] or not ids.shape[1]:
        return boundary

    boundary[0, :] = True
    boundary[-1, :] = True
    boundary[:, 0] = True
    boundary[:, -1] = True

    different = ids[1:, :] != ids[:-1, :]
    boundary[1:, :] |= different
    boundary[:-1, :] |= different
    different = ids[:, 1:] != ids[:, :-1]
    boundary[:, 1:] |= different
    boundary[:, :-1] |= different
    boundary &= ids > 0
    return boundary


def ranked_overlaps(me_ids, vanilla_ids):
    """Return one overlap record per positive ME ID, sorted by ME ID.

    Each record contains ``me_id``, ``sampled_pixels``, ``candidates``,
    ``runner_share`` and ``margin``. Candidates contain ``vanilla_id``,
    ``pixels`` and ``share`` and are sorted by decreasing shared-pixel count,
    then increasing Vanilla ID. Only positive Vanilla IDs are candidates.

    The denominator is ALL positive pixels of that ME state in the supplied
    grid, including pixels where Vanilla is zero or negative. Thus missing
    Vanilla coverage cannot inflate candidate shares. ``runner_share`` is the
    second candidate's share, or 0.0; ``margin`` is the best share minus that
    runner share, or 0.0 when there is no candidate. All returned values are
    plain JSON-safe Python primitives, not NumPy scalars.

    Pixel grouping is vectorized; Python loops visit only distinct ID pairs
    and records, never every pixel for every state. Structured pairs preserve
    signed/unsigned integer IDs without float conversion or packed-ID limits.
    """
    me, vanilla = _same_grid(me_ids, vanilla_ids)
    me_land = me > 0
    sampled_pixels = int(np.count_nonzero(me_land))
    if not sampled_pixels:
        return []

    pairs = np.empty(
        sampled_pixels,
        dtype=[("me_id", me.dtype), ("vanilla_id", vanilla.dtype)],
    )
    pairs["me_id"] = me[me_land]
    pairs["vanilla_id"] = vanilla[me_land]
    # All non-positive samples are one empty bucket for the denominator only.
    vanilla_samples = pairs["vanilla_id"]
    vanilla_samples[vanilla_samples < 0] = 0
    unique_pairs, counts = np.unique(pairs, return_counts=True)

    records = []
    for pair, count in zip(unique_pairs, counts):
        me_id, vanilla_id = int(pair["me_id"]), int(pair["vanilla_id"])
        pixels = int(count)
        if not records or records[-1]["me_id"] != me_id:
            records.append({
                "me_id": me_id,
                "sampled_pixels": 0,
                "candidates": [],
                "runner_share": 0.0,
                "margin": 0.0,
            })
        record = records[-1]
        record["sampled_pixels"] += pixels
        if vanilla_id > 0:
            record["candidates"].append({
                "vanilla_id": vanilla_id,
                "pixels": pixels,
            })

    for record in records:
        candidates = record["candidates"]
        candidates.sort(key=lambda candidate: (-candidate["pixels"], candidate["vanilla_id"]))
        for candidate in candidates:
            candidate["share"] = candidate["pixels"] / record["sampled_pixels"]
        if len(candidates) > 1:
            record["runner_share"] = candidates[1]["share"]
        if candidates:
            record["margin"] = candidates[0]["share"] - record["runner_share"]
    return records


def partition_summary(me_ids, vanilla_ids):
    """Return JSON-safe land coverage counts and per-ME ranked overlaps.

    Counts are pixels, not state counts. ``union_land_pixels`` counts any
    positive ID on either side; ``shared_land_pixels`` counts positive IDs on
    both sides regardless of identity. ``me_only_pixels`` and
    ``vanilla_only_pixels`` count positive land without land on the other side.
    """
    me, vanilla = _same_grid(me_ids, vanilla_ids)
    me_land, vanilla_land = me > 0, vanilla > 0
    me_pixels = int(np.count_nonzero(me_land))
    vanilla_pixels = int(np.count_nonzero(vanilla_land))
    shared_pixels = int(np.count_nonzero(me_land & vanilla_land))
    return {
        "union_land_pixels": me_pixels + vanilla_pixels - shared_pixels,
        "me_land_pixels": me_pixels,
        "vanilla_land_pixels": vanilla_pixels,
        "shared_land_pixels": shared_pixels,
        "me_only_pixels": me_pixels - shared_pixels,
        "vanilla_only_pixels": vanilla_pixels - shared_pixels,
        "overlaps": ranked_overlaps(me, vanilla),
    }


def draw_partition_overlay(me_ids, vanilla_ids):
    """Return an opaque ``(height, width, 4)`` uint8 boundary-only overlay.

    State interiors and water stay neutral gray. Native ME boundaries are
    continuous dark green; transformed Vanilla boundaries are orange with a
    stable ``(x + y) % 8 < 4`` dash. Orange is drawn last, so coincident borders
    alternate orange/green rather than concealing the ME border entirely.
    Inputs are used in place: no fit, resampling, or identity-based fill occurs.
    """
    me, vanilla = _same_grid(me_ids, vanilla_ids)
    image = np.empty((*me.shape, 4), dtype=np.uint8)
    image[:] = BACKGROUND_RGBA
    image[boundary_mask(me)] = ME_BOUNDARY_RGBA

    vanilla_boundary = boundary_mask(vanilla)
    y, x = np.ogrid[:me.shape[0], :me.shape[1]]
    vanilla_boundary &= (x + y) % VANILLA_DASH_PERIOD < VANILLA_DASH_LENGTH
    image[vanilla_boundary] = VANILLA_BOUNDARY_RGBA
    return image
