#!/usr/bin/env python
"""Direct raster state-mask coverage audit layered on state_edges_1936.tsv.

This is a READ-ONLY evidence layer: it never edits canonical mappings, never
writes back to the ledger, and is never consumed by state_relations.py.

Geometry is measured by TWO independent partition censuses on the frozen
coast affine (state_geography_evidence.partition_census, nearest-neighbour
np.rint, all source land pixels):

  forward   ME-native frame:     me_land -> vanilla (affine)
  reverse   vanilla-native frame: v_land -> ME (inverse affine)

The two directions are reported in separate me_*/v_* column families and are
NEVER merged or averaged: a forward splat count must not be divided by a
native target area (the map scales differ), so each side keeps its own native
denominator. pair_class/entity_class use mask counts ONLY; the 1936 owner and
the carried ledger fields are parallel columns that may add a pair_reason but
can never change pair_class.

Registration reuses continent_registration.fit_coast_warp(..., initial="bbox")
-- the continent=1 coastline silhouette fit -- and the affine is validated
once and frozen BEFORE any state mask is produced. No state boundary,
history, ledger, VP or anchor data can reach the transform.

pair_class ladder (positive raster hit = me_hit_px>0 or v_hit_px>0):
  no hit in either census            -> no_sampled_overlap   (claim withheld)
  both directional native shares >= CONTAINMENT_SHARE -> mutual_contained
  me_share >= CONTAINMENT_SHARE      -> me_contained_in_vanilla
  v_share_native >= CONTAINMENT_SHARE -> vanilla_contained_in_me
  both native shares >= PARTIAL_SHARE -> partial_overlap
  any positive hit otherwise         -> sliver_overlap
Native shares are me_hit/me_land_px and v_hit/v_native_px. pair_claim is
"sampled_masks" only for rows with a positive raster hit, else "withheld";
it is never "proven"/"complete_subset": sampled pixel overlap is not a
complete-subset proof.

entity_class (per side, deterministic; uses native-share top partner):
  unmatched   no positive partner
  contained   top partner native share >= CONTAINMENT_SHARE and usable
  split       >=2 positive partners and top share < CONTAINMENT_SHARE
  ambiguous   anything else
ME usable = geometry_usable (me_land_px>=MIN_AREA and assigned share>=0.90);
vanilla usable mirrors it (v_native_px>=MIN_AREA and v_sampled/v_native>=0.90).

Outputs (LF, UTF-8, six-decimal ratios, byte-identical on repeated runs):
  docs/states/state_mask_audit_1936.tsv          one row per (me_id, vanilla_id)
  docs/states/state_mask_audit_1936_entities.tsv one row per (entity_side, id)
  docs/states/state_mask_audit_1936_summary.json schema
      state_mask_audit_1936_summary/v1

Optional diagnostics (off by default): --render-dir DIR plus repeatable
--render-owner TAG renders the selected owner's masks into the ME frame
under the frozen affine (green=both, red=ME-only, blue=vanilla-only,
black=neither) as <TAG>_owner_overlay_me_frame.png. Rendering is a human
diagnostic only; nothing it produces feeds the classification.

Usage:
  python tools/audit_state_masks_1936.py --help
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root, repo_root  # noqa: E402
import continent_registration as registration  # noqa: E402
import state_geography_evidence as geo  # noqa: E402
import build_state_edges_1936 as bse  # noqa: E402

HOI4 = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
MOD = os.environ.get("ME_MOD") or mod_root()
TABLES = Path(repo_root()) / "docs" / "states"
DEFAULT_EDGES = TABLES / "state_edges_1936.tsv"
DEFAULT_OUTPUT = TABLES / "state_mask_audit_1936.tsv"
DEFAULT_ENTITIES = TABLES / "state_mask_audit_1936_entities.tsv"
DEFAULT_SUMMARY_JSON = TABLES / "state_mask_audit_1936_summary.json"

CUTOFF = (1936, 1, 1)
MIN_AREA = 64                    # px; geometry floor for usable/entity masks
CONTAINMENT_SHARE = 0.90         # directional native share => contained
PARTIAL_SHARE = 0.20             # both native shares => partial_overlap
MIN_ASSIGNED_SHARE = 0.90        # geometry_usable assigned-share floor
CHUNK_PIXELS = geo.OVERLAP_CHUNK_PIXELS

SUMMARY_SCHEMA = "state_mask_audit_1936_summary/v1"
KIND = ("direct binary state-mask audit; sampled mask geometry only, "
        "not a complete-subset proof")

COLUMNS = [
    "vanilla_id", "vanilla_name", "vanilla_owner",
    "me_id", "me_name", "me_owner",
    "owner_relation", "country_scope",
    "me_land_px", "me_outside_px", "me_water_px", "me_unassigned_px",
    "me_assigned_px", "me_hit_px", "me_share", "me_registered_share",
    "me_spill_px", "me_rank",
    "me_modal_vanilla", "me_modal_share", "me_modal_margin",
    "v_native_px", "v_sampled_px", "v_unreached_px", "v_hit_px",
    "v_share_native", "v_share_sampled",
    "pair_class", "pair_reason", "pair_claim",
    "me_components", "me_largest_share", "me_largest_hit_share",
    "components_agree", "geometry_usable",
    "source_scope", "edge_source", "evidence_families", "is_canonical",
    "canonical_ancestor", "edge_class", "comment_votes", "manual_kind",
    "consistency",
]

ENTITY_COLUMNS = [
    "entity_side", "entity_id", "entity_name", "entity_owner",
    "land_px", "assigned_px", "assigned_share", "geometry_usable",
    "partner_count", "top_partner", "top_partner_px", "top_share",
    "runner_share", "entity_class", "entity_reason",
    "same_owner_partner_count", "cross_owner_partner_count",
]

_TAG = re.compile(r"^[A-Za-z0-9_]+$")


def fail(msg):
    raise SystemExit("state_mask_audit_1936: " + str(msg))


def _num(value):
    if value is None or value == "":
        return ""
    value = float(value)
    return f"{0.0 if abs(value) < 0.0000005 else value:.6f}"


# --------------------------------------------------------------- classifiers

def _pair_class(me_hit, v_hit, me_share, v_share_native):
    """Geometry-only pair ladder -> (pair_class, ordered reason list).

    Shares are the directional NATIVE shares (me_hit/me_land_px,
    v_hit/v_native_px); an unsampled side contributes 0.0. Reasons are
    emitted in a fixed order so pair_reason is deterministic; the caller
    appends availability/owner diagnostics afterwards.
    """
    ms = 0.0 if me_share is None else float(me_share)
    vs = 0.0 if v_share_native is None else float(v_share_native)
    reasons = []
    if me_hit <= 0 and v_hit <= 0:
        return "no_sampled_overlap", ["no_bidirectional_hit"]
    if me_hit <= 0:
        reasons.append("reverse_only_hit")
    elif v_hit <= 0:
        reasons.append("forward_only_hit")
    if ms >= CONTAINMENT_SHARE and vs >= CONTAINMENT_SHARE:
        reasons.append("mutual_containment")
        return "mutual_contained", reasons
    if ms >= CONTAINMENT_SHARE:
        reasons.append("v_share_below_containment")
        return "me_contained_in_vanilla", reasons
    if vs >= CONTAINMENT_SHARE:
        reasons.append("me_share_below_containment")
        return "vanilla_contained_in_me", reasons
    if ms >= PARTIAL_SHARE and vs >= PARTIAL_SHARE:
        reasons.append("me_share_below_containment")
        reasons.append("v_share_below_containment")
        return "partial_overlap", reasons
    if ms < PARTIAL_SHARE:
        reasons.append("me_share_below_partial")
    if vs < PARTIAL_SHARE:
        reasons.append("v_share_below_partial")
    return "sliver_overlap", reasons


def _entity_class(top_share, partner_count, usable):
    """Deterministic entity ladder -> entity_class string."""
    if partner_count <= 0:
        return "unmatched"
    if top_share is not None and top_share >= CONTAINMENT_SHARE and usable:
        return "contained"
    if partner_count >= 2 and \
            (top_share is None or top_share < CONTAINMENT_SHARE):
        return "split"
    return "ambiguous"


def _entity_reasons(partner_count, top_share, usable, owner):
    """Fixed-order reason list matching the _entity_class ladder."""
    reasons = []
    if not usable:
        reasons.append("geometry_not_usable")
    if partner_count <= 0:
        reasons.append("no_positive_partner")
    elif top_share is not None and top_share >= CONTAINMENT_SHARE:
        reasons.append("top_share_contained")
    else:
        reasons.append("top_share_below_containment")
        reasons.append("multiple_positive_partners" if partner_count >= 2
                       else "single_positive_partner")
    if not owner:
        reasons.append("unknown_owner")
    return reasons


# ------------------------------------------------------------------ helpers

def _read_edges(path):
    """1936 ledger rows keyed by (me_id, vanilla_id); fail closed."""
    if not path.is_file():
        fail(f"missing edge ledger {path}")
    edges = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            try:
                key = (int(row["me_id"]), int(row["vanilla_id"]))
            except (TypeError, ValueError, KeyError) as error:
                fail(f"{path}: malformed edge row ({error})")
            if key in edges:
                fail(f"{path}: duplicate edge row {key}")
            edges[key] = row
    if not edges:
        fail(f"{path}: no edge rows")
    return edges


def _rank(pairs, member):
    """1-based rank of `member` in a (-px, id)-sorted partner list."""
    return bse._rank(pairs, member)


def _consistency(carried, is_canonical, hit, modal, vid):
    """Ledger-vs-geometry parallel verdict; never feeds pair_class."""
    if not carried:
        return "raster_only" if hit else "raster_only_no_hit"
    if is_canonical:
        if not hit:
            return "canonical_geometry_conflict"
        return "canonical_geometry_agree" if modal == vid \
            else "canonical_geometry_partial"
    return "ledger_geometry_hit" if hit else "ledger_no_raster_hit"


def _sample_mask(source_shape, target_mask, affine,
                 chunk_pixels=CHUNK_PIXELS):
    """Backward-sample target_mask into the source-shaped frame.

    Same pixel-centre convention as partition_census: explicit float64
    ufunc arithmetic, np.rint, bounds re-checked after rounding.
    """
    height, width = target_mask.shape
    out = np.zeros(source_shape, dtype=bool)
    flat = out.ravel()
    for start in range(0, flat.size, chunk_pixels):
        stop = min(start + chunk_pixels, flat.size)
        positions = np.arange(start, stop, dtype=np.int64)
        x = (positions % source_shape[1]).astype(np.float64)
        y = (positions // source_shape[1]).astype(np.float64)
        with np.errstate(over="ignore", invalid="ignore"):
            mx = x * affine[0, 0] + y * affine[0, 1] + affine[0, 2]
            my = x * affine[1, 0] + y * affine[1, 1] + affine[1, 2]
        inside = np.isfinite(mx) & np.isfinite(my) & \
            (mx >= 0) & (mx < width) & (my >= 0) & (my < height)
        selected = np.flatnonzero(inside)
        xx = np.rint(mx[selected]).astype(np.int64)
        yy = np.rint(my[selected]).astype(np.int64)
        keep = (xx < width) & (yy < height)
        selected = selected[keep]
        flat[start + selected] = \
            target_mask[yy[keep], xx[keep]]
    return out


def _render_owner(tag, affine, me_land, v_land, me_owners, v_owners,
                  render_dir):
    """One owner overlay PNG in the ME frame; returns the file name."""
    if not _TAG.match(tag):
        fail(f"malformed --render-owner tag {tag!r}")
    me_ids = sorted(sid for sid, o in me_owners.items() if o == tag)
    v_ids = sorted(sid for sid, o in v_owners.items() if o == tag)
    if not me_ids and not v_ids:
        fail(f"--render-owner {tag!r} owns no state on either side")
    me_mask = np.isin(me_land, me_ids) if me_ids else \
        np.zeros(me_land.shape, dtype=bool)
    v_mask = np.isin(v_land, v_ids) if v_ids else \
        np.zeros(v_land.shape, dtype=bool)
    v_on_me = _sample_mask(me_land.shape, v_mask, affine)
    rgb = np.zeros((*me_land.shape, 3), dtype=np.uint8)
    rgb[v_on_me] = (0, 80, 255)          # blue: vanilla-only land
    rgb[me_mask] = (220, 0, 0)           # red: ME-only land
    rgb[me_mask & v_on_me] = (0, 200, 0)  # green: both
    name = f"{tag}_owner_overlay_me_frame.png"
    Image.fromarray(rgb, "RGB").save(str(render_dir / name))
    return name


# ---------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0])
    parser.add_argument("--vanilla", default=HOI4, help="vanilla game dir")
    parser.add_argument("--mod", default=MOD, help="mod dir")
    parser.add_argument("--edges", default=str(DEFAULT_EDGES),
                        help="state_edges_1936.tsv ledger to carry")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="state_mask_audit_1936.tsv output path")
    parser.add_argument("--entities", default=str(DEFAULT_ENTITIES),
                        help="entity-level TSV output path")
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON),
                        help="summary JSON output path")
    parser.add_argument("--cutoff", default="%d.%d.%d" % CUTOFF,
                        help="owner cutoff Y.M.D (default 1936.1.1)")
    parser.add_argument("--render-dir", default="",
                        help="directory for optional owner overlay PNGs")
    parser.add_argument("--render-owner", action="append", default=[],
                        metavar="TAG",
                        help="owner tag to render (repeatable)")
    args = parser.parse_args(argv)
    try:
        cutoff = tuple(int(p) for p in args.cutoff.split("."))
        assert len(cutoff) == 3 and all(0 <= p <= 9999 for p in cutoff)
    except (ValueError, AssertionError):
        fail(f"malformed --cutoff {args.cutoff!r} (expected Y.M.D)")
    if args.render_owner and not args.render_dir:
        fail("--render-owner requires --render-dir")
    render_dir = Path(args.render_dir) if args.render_dir else None
    render_tags = list(dict.fromkeys(args.render_owner))

    # ------------------------------------------------ ledger + states + owners
    ledger = _read_edges(Path(args.edges))
    try:
        me_states = geo.load_states(args.mod)
        v_states = geo.load_states(args.vanilla)
    except (OSError, ValueError, TypeError, IndexError) as error:
        fail(f"invalid state input: {error}")
    me_owners = bse.load_state_owners(args.mod, cutoff)
    v_owners = bse.load_state_owners(args.vanilla, cutoff)
    if set(me_owners) != set(me_states):
        fail("owner table drift: ME history/states id set changed mid-run")
    if set(v_owners) != set(v_states):
        fail("owner table drift: vanilla history/states id set changed")
    for mid, vid in ledger:
        if mid not in me_states:
            fail(f"ledger edge endpoint {mid} is not a live ME state")
        if vid not in v_states:
            fail(f"ledger edge endpoint {vid} is not a live vanilla state")

    me_names = {sid: s["name"] for sid, s in me_states.items()}
    v_names = {sid: s["name"] for sid, s in v_states.items()}

    # --------------------------------------- frozen coast affine + rasters
    cache = {}
    coast = registration.fit_coast_warp(
        args.vanilla, args.mod, cache=cache, initial="bbox")
    if not coast["ok"]:
        fail("coast affine unavailable: " + "; ".join(coast.get("notes", [])))
    affine = np.asarray(coast["affine_me_to_vanilla"], dtype=np.float64)
    if affine.shape != (2, 3) or not np.isfinite(affine).all():
        fail("coast affine is not a finite 2x3 matrix")

    try:
        me_prov = geo._province_raster(args.mod, cache)
        me_state = geo._state_raster(me_prov, me_states)
        me_mask = geo._land_mask(args.mod, me_prov, cache)
        me_land = np.where(me_mask & (me_state > 0), me_state, 0) \
            .astype(np.int32)
        v_prov = geo._province_raster(args.vanilla, cache)
        v_state = geo._state_raster(v_prov, v_states)
        v_mask = geo._land_mask(args.vanilla, v_prov, cache)
        v_land = np.where(v_mask & (v_state > 0), v_state, 0) \
            .astype(np.int32)
        forward = geo.partition_census(
            me_land, v_land, affine, target_land_mask=v_mask)
        reverse = geo.partition_census(
            v_land, me_land, bse._invert_affine(affine),
            target_land_mask=me_mask)
    except (OSError, ValueError, TypeError, IndexError) as error:
        fail(f"invalid state raster/census: {error}")
    del me_prov, me_state, v_prov, v_state, me_mask, v_mask
    if forward is None and reverse is None:
        fail("census produced no state pixels in either frame")

    def _index(census):
        return {int(sid): i for i, sid in enumerate(census["source_ids"])} \
            if census else {}

    def _dense(census):
        return {int(tid): j for j, tid in enumerate(census["target_ids"], 1)} \
            if census else {}

    me_idx, v_idx = _index(forward), _index(reverse)
    fwd_dense, rev_dense = _dense(forward), _dense(reverse)
    me_area = {int(sid): int(forward["source_area_px"][i])
               for i, sid in enumerate(forward["source_ids"])} \
        if forward else {}
    v_sampled = {int(sid): int(reverse["counts"][i][1:].sum())
                 for i, sid in enumerate(reverse["source_ids"])} \
        if reverse else {}
    v_native = {int(sid): int(n) for sid, n in zip(
        *np.unique(v_land[v_land > 0], return_counts=True))}

    me_partners = defaultdict(dict)      # me_id -> {vanilla_id: px}
    if forward is not None:
        for i, mid in enumerate(forward["source_ids"]):
            row = forward["counts"][i]
            for j in np.flatnonzero(row[1:]) + 1:
                me_partners[int(mid)][int(forward["target_ids"][j - 1])] = \
                    int(row[j])
    v_partners = defaultdict(dict)       # vanilla_id -> {me_id: px}
    if reverse is not None:
        for i, vid in enumerate(reverse["source_ids"]):
            row = reverse["counts"][i]
            for j in np.flatnonzero(row[1:]) + 1:
                v_partners[int(vid)][int(reverse["target_ids"][j - 1])] = \
                    int(row[j])

    # accounting parity with the canonical generators' closed-world checks
    for mid, i in me_idx.items():
        kinds = forward["kinds"][i]
        counts = forward["counts"][i]
        if int(kinds[0]) != int(counts[1:].sum()) or \
                int(kinds[1:].sum()) != int(counts[0]):
            fail(f"census accounting mismatch for ME {mid}")
    for vid, i in v_idx.items():
        kinds = reverse["kinds"][i]
        counts = reverse["counts"][i]
        if int(kinds[0]) != int(counts[1:].sum()) or \
                int(kinds[1:].sum()) != int(counts[0]):
            fail(f"census accounting mismatch for vanilla {vid}")

    # ----------------------------------- ME component stats (forward frame)
    me_components = {}
    if forward is not None:
        vanilla_ids = forward["target_ids"]
        bounds = geo.find_objects(
            forward["dense_source"], max_label=len(forward["source_ids"]))
        for i, mid in enumerate(forward["source_ids"]):
            counts = forward["counts"][i]
            area = int(counts.sum())
            window = bounds[i]
            votes, largest = geo._component_votes(
                forward["dense_source"][window] == i + 1,
                forward["registered"][window], counts, vanilla_ids)
            agree = all(
                component["best"] == largest["best"] and
                component["best"] != "" and
                not geo._overlap_coverage_reason(component)
                for component in votes)
            me_components[int(mid)] = {
                "components": len(votes),
                "largest_share": largest["area"] / area if area else "",
                "largest_hit_share": largest["share"],
                "agree": agree,
            }

    usable_me = {}
    for mid, area in me_area.items():
        assigned = int(forward["counts"][me_idx[mid]][1:].sum())
        usable_me[mid] = bool(
            area >= MIN_AREA and assigned / area >= MIN_ASSIGNED_SHARE)

    # ------------------------------------------------------ edge union + rows
    keys = set(ledger)
    for mid, partners in me_partners.items():
        keys.update((mid, vid) for vid in partners)
    for vid, partners in v_partners.items():
        keys.update((mid, vid) for mid in partners)

    rows = []
    for mid, vid in sorted(keys):
        mo = me_owners.get(mid, "")
        vo = v_owners.get(vid, "")
        if mo and vo:
            relation = "same_owner" if mo == vo else "cross_owner"
            scope = "within_country" if mo == vo \
                else "cross_country_diagnostic"
        else:
            relation = scope = "unknown_owner"

        me_hit = 0
        if forward is not None and mid in me_idx and vid in fwd_dense:
            me_hit = int(forward["counts"][me_idx[mid], fwd_dense[vid]])
        v_hit = 0
        if reverse is not None and vid in v_idx and mid in rev_dense:
            v_hit = int(reverse["counts"][v_idx[vid], rev_dense[mid]])
        area = me_area.get(mid)
        native = v_native.get(vid)
        sampled = v_sampled.get(vid)

        carried = ledger.get((mid, vid)) or {}
        is_canonical = carried.get("is_canonical") == "1"
        if carried:
            edge_class = carried.get("edge_class", "")
            source_scope = carried.get("source_scope", "")
        else:
            edge_class = bse._edge_class(
                mid, me_names.get(mid, ""), mo, is_canonical,
                me_hit, v_hit)
            source_scope = "excluded" if edge_class == "excluded" \
                else "observation"

        # forward geometry (ME-native frame)
        if area is not None:
            kinds = forward["kinds"][me_idx[mid]]
            outside_px, water_px, unassigned_px, assigned_px = (
                int(kinds[k]) for k in (1, 2, 3, 0))
            registered_px = area - outside_px
            partners = me_partners.get(mid, {})
            ranked = sorted(partners.items(), key=lambda kv: (-kv[1], kv[0]))
            modal = ranked[0][0] if ranked else None
            modal_share = ranked[0][1] / area if ranked else None
            margin = (ranked[0][1] - ranked[1][1]) / area \
                if len(ranked) > 1 else (modal_share if ranked else None)
            me_rank = _rank(partners, vid) if me_hit else None
            share = me_hit / area
            comp = me_components.get(mid)
        else:
            outside_px = water_px = unassigned_px = assigned_px = None
            registered_px = None
            ranked = []
            modal = modal_share = margin = me_rank = share = None
            comp = None

        # reverse geometry (vanilla-native frame)
        v_share_native = v_hit / native if native else None
        v_share_sampled = v_hit / sampled if sampled else None
        unreached = (native - sampled) \
            if native is not None and sampled is not None else None

        pclass, reasons = _pair_class(me_hit, v_hit, share, v_share_native)
        if area is None:
            reasons.append("me_not_sampled")
        if native is None:
            reasons.append("v_not_sampled")
        elif sampled is None:
            reasons.append("v_not_sampled")
        if area is not None and not usable_me[mid]:
            reasons.append("geometry_not_usable")
        if relation == "cross_owner":
            reasons.append("cross_owner")
        elif relation == "unknown_owner":
            reasons.append("unknown_owner")
        claim = "sampled_masks" if (me_hit or v_hit) else "withheld"

        usable = usable_me.get(mid)
        rows.append({
            "vanilla_id": str(vid),
            "vanilla_name": v_names.get(vid, ""),
            "vanilla_owner": vo,
            "me_id": str(mid),
            "me_name": me_names.get(mid, ""),
            "me_owner": mo,
            "owner_relation": relation,
            "country_scope": scope,
            "me_land_px": str(area) if area is not None else "",
            "me_outside_px": str(outside_px) if outside_px is not None else "",
            "me_water_px": str(water_px) if water_px is not None else "",
            "me_unassigned_px":
            str(unassigned_px) if unassigned_px is not None else "",
            "me_assigned_px":
            str(assigned_px) if assigned_px is not None else "",
            "me_hit_px": str(me_hit) if me_hit
            else ("0" if area is not None else ""),
            "me_share": _num(share) if share is not None else "",
            "me_registered_share": _num(me_hit / registered_px)
            if registered_px else "",
            "me_spill_px": str(assigned_px - me_hit)
            if assigned_px is not None else "",
            "me_rank": str(me_rank) if me_rank else "",
            "me_modal_vanilla": str(modal) if modal is not None else "",
            "me_modal_share": _num(modal_share),
            "me_modal_margin": _num(margin),
            "v_native_px": str(native) if native is not None else "",
            "v_sampled_px": str(sampled) if sampled is not None else "",
            "v_unreached_px":
            str(unreached) if unreached is not None else "",
            "v_hit_px": str(v_hit) if v_hit
            else ("0" if sampled is not None else ""),
            "v_share_native": _num(v_share_native),
            "v_share_sampled": _num(v_share_sampled),
            "pair_class": pclass,
            "pair_reason": "|".join(reasons),
            "pair_claim": claim,
            "me_components": str(comp["components"]) if comp else "",
            "me_largest_share": _num(comp["largest_share"]) if comp else "",
            "me_largest_hit_share":
            _num(comp["largest_hit_share"]) if comp else "",
            "components_agree":
            ("1" if comp["agree"] else "0") if comp else "",
            "geometry_usable":
            ("1" if usable else "0") if usable is not None else "",
            "source_scope": source_scope,
            "edge_source": carried.get("edge_source", "raster"),
            "evidence_families":
            carried.get("evidence_families", "raster"),
            "is_canonical": "1" if is_canonical else "0",
            "canonical_ancestor": carried.get("canonical_ancestor", ""),
            "edge_class": edge_class,
            "comment_votes": carried.get("comment_votes", ""),
            "manual_kind": carried.get("manual_kind", ""),
            "consistency": _consistency(
                carried, is_canonical, me_hit or v_hit, modal, vid),
        })

    if not rows:
        fail("no rows after union of ledger and raster census")
    if len({(r["me_id"], r["vanilla_id"]) for r in rows}) != len(rows):
        fail("duplicate (me_id, vanilla_id) edge keys")

    # -------------------------------------------------------- entity rows
    entity_rows = []
    for mid in sorted(me_states):
        mo = me_owners.get(mid, "")
        area = me_area.get(mid)
        assigned = int(forward["counts"][me_idx[mid]][1:].sum()) \
            if mid in me_idx else 0
        partners = me_partners.get(mid, {})
        ranked = sorted(partners.items(), key=lambda kv: (-kv[1], kv[0]))
        top_share = ranked[0][1] / area if (ranked and area) else None
        runner_share = ranked[1][1] / area \
            if len(ranked) > 1 and area else None
        usable = usable_me.get(mid, False)
        same = sum(1 for p in partners
                   if mo and v_owners.get(p, "") == mo)
        cross = sum(1 for p in partners
                    if mo and v_owners.get(p, "") and v_owners[p] != mo)
        entity_rows.append({
            "entity_side": "me", "entity_id": str(mid),
            "entity_name": me_names.get(mid, ""), "entity_owner": mo,
            "land_px": str(area) if area is not None else "0",
            "assigned_px": str(assigned),
            "assigned_share": _num(assigned / area) if area else "",
            "geometry_usable": "1" if usable else "0",
            "partner_count": str(len(partners)),
            "top_partner": str(ranked[0][0]) if ranked else "",
            "top_partner_px": str(ranked[0][1]) if ranked else "",
            "top_share": _num(top_share),
            "runner_share": _num(runner_share),
            "entity_class": _entity_class(top_share, len(partners), usable),
            "entity_reason": "|".join(
                _entity_reasons(len(partners), top_share, usable, mo)),
            "same_owner_partner_count": str(same),
            "cross_owner_partner_count": str(cross),
        })
    for vid in sorted(v_states):
        vo = v_owners.get(vid, "")
        native = v_native.get(vid)
        sampled = v_sampled.get(vid) or 0
        partners = v_partners.get(vid, {})
        ranked = sorted(partners.items(), key=lambda kv: (-kv[1], kv[0]))
        top_share = ranked[0][1] / native if (ranked and native) else None
        runner_share = ranked[1][1] / native \
            if len(ranked) > 1 and native else None
        usable = bool(native is not None and native >= MIN_AREA and
                      sampled / native >= MIN_ASSIGNED_SHARE) \
            if native else False
        same = sum(1 for p in partners
                   if vo and me_owners.get(p, "") == vo)
        cross = sum(1 for p in partners
                    if vo and me_owners.get(p, "") and me_owners[p] != vo)
        entity_rows.append({
            "entity_side": "vanilla", "entity_id": str(vid),
            "entity_name": v_names.get(vid, ""), "entity_owner": vo,
            "land_px": str(native) if native is not None else "0",
            "assigned_px": str(sampled),
            "assigned_share": _num(sampled / native) if native else "",
            "geometry_usable": "1" if usable else "0",
            "partner_count": str(len(partners)),
            "top_partner": str(ranked[0][0]) if ranked else "",
            "top_partner_px": str(ranked[0][1]) if ranked else "",
            "top_share": _num(top_share),
            "runner_share": _num(runner_share),
            "entity_class": _entity_class(top_share, len(partners), usable),
            "entity_reason": "|".join(
                _entity_reasons(len(partners), top_share, usable, vo)),
            "same_owner_partner_count": str(same),
            "cross_owner_partner_count": str(cross),
        })
    entity_rows.sort(key=lambda r: (r["entity_side"], int(r["entity_id"])))

    # --------------------------------------------- optional owner overlays
    rendered = []
    if render_dir is not None:
        render_dir.mkdir(parents=True, exist_ok=True)
        for tag in render_tags:
            rendered.append(_render_owner(
                tag, affine, me_land, v_land, me_owners, v_owners,
                render_dir))

    # ------------------------------------------------------------- summary
    def _histogram(values):
        out = defaultdict(int)
        for value in values:
            out[value or ""] += 1
        return dict(sorted(out.items()))

    owners = sorted({o for o in (*me_owners.values(), *v_owners.values())
                     if o})
    owner_rows = {}
    for tag in owners:
        tagged = [r for r in rows
                  if r["me_owner"] == tag or r["vanilla_owner"] == tag]
        owner_rows[tag] = {
            "me_states": sum(1 for o in me_owners.values() if o == tag),
            "vanilla_states": sum(1 for o in v_owners.values() if o == tag),
            "rows_total": len(tagged),
            "within_country": sum(
                1 for r in tagged if r["country_scope"] == "within_country"),
            "cross_country_diagnostic": sum(
                1 for r in tagged
                if r["country_scope"] == "cross_country_diagnostic"),
            "unknown_owner": sum(
                1 for r in tagged if r["country_scope"] == "unknown_owner"),
        }

    summary = {
        "schema": SUMMARY_SCHEMA,
        "kind": KIND,
        "cutoff": "%d.%d.%d" % cutoff,
        "inputs": {
            "vanilla": str(Path(args.vanilla)),
            "mod": str(Path(args.mod)),
            "edges": str(Path(args.edges)),
        },
        "registration": {
            "method": "coast_bbox_affine_raster",
            "initial": "bbox",
            "ok": bool(coast["ok"]),
            "affine_me_to_vanilla": [
                [round(float(x), 6) for x in row] for row in affine],
            "affine_det": round(float(coast["affine_det"]), 6),
            "n_matches": int(coast["n_matches"]),
            "chamfer_symmetric_p95_px": round(
                float(coast["chamfer_px"]["symmetric_p95"]), 6),
        },
        "gates": {
            "min_area_px": MIN_AREA,
            "containment_share": CONTAINMENT_SHARE,
            "partial_share": PARTIAL_SHARE,
            "min_assigned_share": MIN_ASSIGNED_SHARE,
            "geometry_usable":
            "me_land_px>=64 and me_assigned_px/me_land_px>=0.90",
        },
        "counts": {
            "rows_total": len(rows),
            "ledger_rows": sum(1 for r in rows if r["consistency"]
                               not in ("raster_only", "raster_only_no_hit")),
            "raster_only_rows": sum(
                1 for r in rows if r["consistency"] == "raster_only"),
            "ledger_only_rows": sum(
                1 for r in rows
                if r["consistency"] in ("canonical_geometry_conflict",
                                        "ledger_no_raster_hit")),
            "pair_class": _histogram(r["pair_class"] for r in rows),
            "pair_claim": _histogram(r["pair_claim"] for r in rows),
            "owner_relation": _histogram(r["owner_relation"] for r in rows),
            "country_scope": _histogram(r["country_scope"] for r in rows),
            "consistency": _histogram(r["consistency"] for r in rows),
        },
        "entities": {
            side: {
                "rows": sum(1 for r in entity_rows
                            if r["entity_side"] == side),
                "entity_class": _histogram(
                    r["entity_class"] for r in entity_rows
                    if r["entity_side"] == side),
            } for side in ("me", "vanilla")
        },
        "owners": owner_rows,
        "rendered_overlays": rendered,
    }

    # --------------------------------------------------------------- writes
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    entities_path = Path(args.entities)
    entities_path.parent.mkdir(parents=True, exist_ok=True)
    with entities_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ENTITY_COLUMNS,
                                delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(entity_rows)

    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    print(f"state_mask_audit_1936: {len(rows)} pairs, "
          f"{len(entity_rows)} entities, "
          f"{len(rendered)} overlays -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
