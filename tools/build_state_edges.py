#!/usr/bin/env python
"""Build the long-edge vanilla<->ME state relation table docs/states/state_edges.tsv.

One row per observed (me_id, vanilla_id) pair. Rows are generated, never
hand-edited, and sorted by (me_id, vanilla_id). Empty values are empty
strings; ratios are six decimals; no timestamp is emitted.

The edge table is the ONLY place where one-to-many (several ME descendants of
one vanilla state) and many-to-many (one ME state overlapping/claiming
several vanilla states) relations are spelled out exhaustively. The flat
v2m_states.tsv / m2v_states.tsv canonical partition stays the compatibility
main view: secondary edges here NEVER rewrite v_ancestor membership.

Edge discovery channels (edge_source, fixed alphabetical `|` join):

  raster    coast-affine pixel census — every positive (me, vanilla)
            intersection in the ME frame plus every positive (vanilla, me)
            intersection in the vanilla frame. Raster overlap is an
            observation only: it shows the two rasters share pixels, it
            can NEVER prove that an ME state is a complete subset of a
            vanilla state.
  canonical the adjudicated m2v_states.tsv ancestor edge.
  comment   an explicit `# was vanilla ...` migration comment. Multi-ID
            comment groups are read from docs/states/mapping_comment_edges.tsv
            when present; without it, comment_best/comment_runner from
            mapping_evidence.tsv supply the non-winning pairs.
  candidate a top-level evidence candidate from mapping_evidence.tsv:
            pixel_best/pixel_runner, vp_best/vp_runner, name_exact_me_ids,
            or the same-ID identity pair.

evidence_families records which channels corroborate the pair (alphabetical
`|` join of: canonical, comment, manual, name, pixel, raster, same_id, vp).
manual_kind lists which manual table asserted it: `member` (the MANUAL
vanilla-side member groups in build_state_map.py) and/or `direct`
(MANUAL_ME2V per-state claims).

edge_class, precedence order:

  excluded           MAP-owner/"Map Border" ME rows — never enter member
                     expansion regardless of other evidence.
  identity_accepted  the canonical m2v edge (is_canonical=1), even with no
                     raster overlap.
  significant        non-canonical edge with a real forward presence:
                     me_match_px >= MIN_OVERLAP_AREA (64).
  backward_only      no forward hits but the reverse census sees this ME
                     state covering >= 64 vanilla-side pixels.
  sliver             0 < me_match_px < 64: pixel dust only.
  evidence_only      no usable raster hits either direction; asserted solely
                     by comment/pixel/vp/name/same_id/manual evidence.

relation_scope, precedence order — how much weight a consumer may put on the
relation itself (independent of edge_class geometry buckets):

  excluded       synthetic MAP/"Map Border" edges.
  compatibility  the canonical edge (is_canonical=1): an existing
                 compatibility assertion inherited from the adjudicated
                 m2v/v2m primary views — NOT a mathematical complete-subset
                 proof.
  asserted       non-canonical edge with an explicit human relation
                 declaration: comment and/or manual provenance.
  observation    everything else: raster/candidate/pixel/vp/name/same_id
                 heuristic evidence only.

Only compatibility and asserted edges may drive relation expansion;
observation edges are stored for explicit query, never for automatic
reasoning. No row claims `proven`/`complete_subset` status: a province-level
crosswalk is a necessary prerequisite for any future complete-subset proof,
and this table deliberately does not ship one.

geometry_usable is a per-ME-state flag (1 iff me_area_px >= 64 AND
me_assigned_share >= 0.90 — the same floors as the overlap family's coverage
gates), not part of edge_class. Vanilla-side columns: v_native_area_px is the
true land pixel count in the vanilla raster (never a warped share);
v_observable_px is what the reverse census can see through the ME frame;
v_share_observable / v_share_native divide v_match_px by each denominator.

The build fails closed on: missing canonical tables, an unavailable or
non-finite coast affine, invalid state rasters, duplicate (me, vanilla)
edge keys, census accounting mismatches, canonical-table ID drift versus
history/states, or any edge endpoint ID unknown to the canonical tables.

Usage: python tools/build_state_edges.py [--vanilla DIR] [--mod DIR]
       [--output PATH]  (HOI4_DIR / ME_MOD environment overrides honoured;
       works from any cwd)
"""
import ast
import csv
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root, repo_root  # noqa: E402
import continent_registration as registration  # noqa: E402
import state_geography_evidence as geo  # noqa: E402

HOI4 = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
MOD = os.environ.get("ME_MOD") or mod_root()
TABLES = Path(repo_root()) / "docs" / "states"
DEFAULT_OUTPUT = TABLES / "state_edges.tsv"

COLUMNS = [
    "vanilla_id", "vanilla_name", "me_id", "me_name", "edge_source",
    "evidence_families", "is_canonical", "canonical_ancestor",
    "canonical_primary_of_vanilla", "me_area_px", "me_match_px", "me_share",
    "me_rank", "me_modal_vanilla", "me_modal_share", "me_modal_margin",
    "me_assigned_share", "me_outside_px", "me_water_px", "me_unassigned_px",
    "me_components", "relation_scope", "edge_class", "geometry_usable",
    "v_native_area_px",
    "v_observable_px", "v_match_px", "v_share_observable", "v_share_native",
    "v_rank", "comment_votes", "vp_votes", "name_exact", "same_id",
    "manual_kind",
]

MIN_AREA = geo.MIN_OVERLAP_AREA                    # 64
MIN_ASSIGNED = geo.MIN_OVERLAP_ASSIGNED_SHARE      # 0.90


def fail(msg):
    raise SystemExit("state_edges: " + str(msg))


def _num(value):
    if value is None or value == "":
        return ""
    value = float(value)
    return f"{0.0 if abs(value) < 0.0000005 else value:.6f}"


def _ints(cell):
    return [int(x) for x in str(cell).split("|") if str(x).strip()]


def _norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", value)


def load_tsv(path, key, required=True):
    if not path.is_file():
        if required:
            fail(f"missing canonical table {path}")
        return {}, []
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))
    if not rows:
        fail(f"empty canonical table {path}")
    out = {}
    for row in rows:
        k = int(row[key])
        if k in out:
            fail(f"duplicate {key}={k} in {path}")
        out[k] = row
    return out, rows


def _invert_affine(affine):
    """Invert a 2x3 affine with explicit float64 arithmetic."""
    a = np.asarray(affine, dtype=np.float64)
    if a.shape != (2, 3) or not np.isfinite(a).all():
        fail("coast affine is invalid")
    a00, a01, a02 = a[0]
    a10, a11, a12 = a[1]
    det = a00 * a11 - a01 * a10
    if det == 0:
        fail("coast affine is singular")
    inverse = np.empty((2, 3), dtype=np.float64)
    inverse[0] = (a11 / det, -a01 / det,
                  (a01 * a12 - a11 * a02) / det)
    inverse[1] = (-a10 / det, a00 / det,
                  (a10 * a02 - a00 * a12) / det)
    return inverse


def _manual_sets():
    """Read MANUAL / MANUAL_ME2V literal dicts out of the canonical generator.

    The adjudicated ancestors live in m2v_states.tsv; these tables only
    answer WHICH manual channel asserted each canonical pair (manual_kind).
    """
    path = Path(__file__).with_name("build_state_map.py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        fail(f"cannot parse {path}: {error}")
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name) and \
                node.targets[0].id in ("MANUAL", "MANUAL_ME2V"):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                fail(f"{path}: {node.targets[0].id} is not a literal dict")
    if set(found) != {"MANUAL", "MANUAL_ME2V"}:
        fail(f"{path}: MANUAL/MANUAL_ME2V tables not found")
    member, direct = defaultdict(set), defaultdict(set)
    for vid, mids in found["MANUAL"].items():
        for mid in mids:
            member[int(mid)].add(int(vid))
    for mid, vid in found["MANUAL_ME2V"].items():
        direct[int(mid)].add(int(vid))
    return member, direct


def _comment_edges(path):
    """Per-(me, vanilla) comment votes; absent file means column fallback."""
    if not path.is_file():
        return None
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))
    if not rows:
        return {}
    fields = set(rows[0])
    if not {"me_id", "vanilla_id"} <= fields:
        fail(f"{path}: expected me_id/vanilla_id columns, got {sorted(fields)}")
    vote_key = next((k for k in ("votes", "comment_votes", "vote_count")
                     if k in fields), None)
    votes = defaultdict(int)
    for row in rows:
        key = (int(row["me_id"]), int(row["vanilla_id"]))
        if vote_key is None:
            votes[key] += 1
        else:
            try:
                votes[key] += int(row[vote_key])
            except ValueError:
                fail(f"{path}: non-integer {vote_key} value {row[vote_key]!r}")
    return dict(votes)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vanilla", default=HOI4, help="vanilla game dir")
    parser.add_argument("--mod", default=MOD, help="mod dir")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="state_edges.tsv output path")
    args = parser.parse_args(argv)

    # ------------------------------------------------ canonical tables
    m2v, m2v_rows = load_tsv(TABLES / "m2v_states.tsv", "me_id")
    v2m, _ = load_tsv(TABLES / "v2m_states.tsv", "vanilla_id")
    evidence, _ = load_tsv(TABLES / "mapping_evidence.tsv", "me_id")
    comment_votes = _comment_edges(TABLES / "mapping_comment_edges.tsv")

    me_names = {mid: row["me_name"] for mid, row in m2v.items()}
    me_owner = {mid: row["owner"] for mid, row in m2v.items()}
    canonical = {}
    for mid, row in m2v.items():
        if row["v_ancestor"].strip():
            canonical[mid] = int(row["v_ancestor"])
    vanilla_names = {vid: row["vanilla_name"] for vid, row in v2m.items()}
    v_primary = {}
    for vid, row in v2m.items():
        if row["me_primary"].strip():
            v_primary[vid] = int(row["me_primary"])

    if set(m2v) != set(evidence):
        fail("m2v_states.tsv and mapping_evidence.tsv disagree on live ME ids")
    for vid, prim in v_primary.items():
        if prim not in m2v:
            fail(f"v2m primary {prim} of vanilla {vid} is not a live ME state")
        if canonical.get(prim) != vid:
            fail(f"v2m primary {prim} claims ancestor {canonical.get(prim)}, not {vid}")
    for mid, vid in canonical.items():
        if vid not in v2m:
            fail(f"m2v ancestor {vid} of ME {mid} is not a vanilla state row")

    # ------------------------------------------------ canonical generators
    manual_member, manual_direct = _manual_sets()

    # ------------------------------------------------ state rasters + census
    me_states = geo.load_states(args.mod)
    v_states = geo.load_states(args.vanilla)
    if set(me_states) != set(m2v):
        fail("canonical m2v table out of date: history/states differs")
    if not set(v_states) <= set(v2m):
        fail("canonical v2m table misses vanilla history/states ids")
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
        me_land = np.where(me_mask & (me_state > 0), me_state, 0).astype(np.int32)
        v_prov = geo._province_raster(args.vanilla, cache)
        v_state = geo._state_raster(v_prov, v_states)
        v_mask = geo._land_mask(args.vanilla, v_prov, cache)
        v_land = np.where(v_mask & (v_state > 0), v_state, 0).astype(np.int32)
    except (OSError, ValueError, TypeError, IndexError) as error:
        fail(f"invalid state raster: {error}")
    try:
        forward = geo.partition_census(
            me_land, v_land, affine, target_land_mask=v_mask)
        reverse = geo.partition_census(
            v_land, me_land, _invert_affine(affine), target_land_mask=me_mask)
    except (ValueError, TypeError, IndexError) as error:
        fail(f"partition census failed: {error}")
    del me_prov, me_state, v_prov, v_state
    if forward is None and reverse is None:
        fail("census produced no state pixels in either frame")



    # ------------------------------------------------ per-state rollups
    # index maps: id -> row position inside each census matrix
    def _index(census):
        if census is None:
            return {}
        return {int(sid): i for i, sid in enumerate(census["source_ids"])}

    me_idx = _index(forward)
    v_idx = _index(reverse)
    me_area = {int(sid): int(forward["source_area_px"][i])
               for i, sid in enumerate(forward["source_ids"])} if forward else {}
    v_observable = {int(sid): int(reverse["source_area_px"][i])
                    for i, sid in enumerate(reverse["source_ids"])} if reverse else {}
    v_native = {int(sid): int(n) for sid, n in zip(
        *np.unique(v_land[v_land > 0], return_counts=True))}

    # per-source dense-index lookup for target ids
    def _dense(census):
        return {int(tid): j for j, tid in enumerate(census["target_ids"], start=1)}

    fwd_dense = _dense(forward) if forward else {}
    rev_dense = _dense(reverse) if reverse else {}

    # per-state partner tallies
    me_partners = defaultdict(dict)   # me_id -> {vanilla_id: px}
    if forward is not None:
        for i, mid in enumerate(forward["source_ids"]):
            row = forward["counts"][i]
            for j in np.flatnonzero(row[1:]) + 1:
                me_partners[int(mid)][int(forward["target_ids"][j - 1])] = int(row[j])
    v_partners = defaultdict(dict)    # vanilla_id -> {me_id: px}
    if reverse is not None:
        for j, vid in enumerate(reverse["source_ids"]):
            row = reverse["counts"][j]
            for t in np.flatnonzero(row[1:]) + 1:
                v_partners[int(vid)][int(reverse["target_ids"][t - 1])] = int(row[t])

    # per-state modal + component tallies (forward frame)
    me_modal = {}
    me_components = {}
    if forward is not None:
        bounds = geo.find_objects(
            forward["dense_source"], max_label=len(forward["source_ids"]))
        for i, mid in enumerate(forward["source_ids"]):
            counts = forward["counts"][i]
            area = int(counts.sum())
            ranked = sorted(me_partners[int(mid)].items(),
                            key=lambda kv: (-kv[1], kv[0]))
            top = ranked[0][0] if ranked else ""
            top_count = ranked[0][1] if ranked else 0
            second = ranked[1][1] if len(ranked) > 1 else 0
            me_modal[int(mid)] = (
                top, top_count / area, (top_count - second) / area,
                int(counts[1:].sum()) / area)
            window = bounds[i]
            labels, n = geo.label(
                forward["dense_source"][window] == i + 1,
                structure=geo.FOUR_CONNECTED)
            me_components[int(mid)] = n

    # ------------------------------------------------ edge accumulation
    edges = {}

    def _edge(mid, vid, source):
        if mid not in me_names:
            fail(f"edge endpoint {mid} is not a live ME state")
        if vid not in vanilla_names:
            fail(f"edge endpoint {vid} is not a known vanilla state")
        key = (mid, vid)
        if key not in edges:
            edges[key] = {"sources": set(), "families": set(),
                          "comment_votes": 0, "vp_votes": 0}
        edges[key]["sources"].add(source)
        return edges[key]

    # 1. every observed positive pair, both directions
    for mid, partners in me_partners.items():
        for vid in partners:
            _edge(mid, vid, "raster")["families"].add("raster")
    for vid, partners in v_partners.items():
        for mid in partners:
            _edge(mid, vid, "raster")["families"].add("raster")

    # 2. canonical ancestor edges (+ manual channel)
    for mid, vid in canonical.items():
        edge = _edge(mid, vid, "canonical")
        edge["families"].add("canonical")
    manual_kind = defaultdict(set)
    for mid, vids in manual_member.items():
        for vid in vids:
            manual_kind[(mid, vid)].add("member")
    for mid, vids in manual_direct.items():
        for vid in vids:
            manual_kind[(mid, vid)].add("direct")
    for (mid, vid), kinds in manual_kind.items():
        # Dead manual entries (retired ids) are skipped exactly like the
        # generator skips them; a live contradiction is a hard failure.
        if mid not in canonical or vid not in vanilla_names:
            continue
        if canonical[mid] != vid:
            fail(f"manual claim {mid}->{vid} contradicts canonical ancestor")
        edge = _edge(mid, vid, "canonical")
        edge["families"].add("manual")
        edge["manual_kind"] = "|".join(sorted(kinds))

    # 3. comment edges — multi-ID table when present, column fallback else.
    # The file's votes win per-pair (never summed with the column fallback).
    comment_pairs = dict(comment_votes or {})
    for mid, row in evidence.items():
        for best_key, vote_key in (("comment_best", "comment_votes"),
                                   ("comment_runner", "comment_runner_votes")):
            if row[best_key].strip() and row[vote_key].strip():
                pair = (mid, int(row[best_key]))
                if pair not in comment_pairs:
                    comment_pairs[pair] = int(row[vote_key])
    for (mid, vid), votes in comment_pairs.items():
        if votes <= 0:
            continue
        edge = _edge(mid, vid, "comment")
        edge["families"].add("comment")
        edge["comment_votes"] += votes

    # 4. top-level evidence candidates
    for mid, row in evidence.items():
        for col, family, vote_col in (
                ("pixel_best", "pixel", None), ("pixel_runner", "pixel", None),
                ("vp_best", "vp", "vp_votes"), ("vp_runner", "vp", "vp_runner_votes")):
            if row[col].strip():
                edge = _edge(mid, int(row[col]), "candidate")
                edge["families"].add(family)
                if vote_col and row[vote_col].strip():
                    edge["vp_votes"] += int(row[vote_col])
        for vid in _ints(row["name_exact_me_ids"]):
            _edge(mid, vid, "candidate")["families"].add("name")
        if mid in vanilla_names and _norm(me_names[mid]) == _norm(vanilla_names[mid]):
            _edge(mid, mid, "candidate")["families"].add("same_id")

    # ------------------------------------------------ row assembly
    rows = []
    for (mid, vid), edge in sorted(edges.items()):
        area = me_area.get(mid)
        match_px = 0
        if forward is not None and mid in me_idx and vid in fwd_dense:
            match_px = int(forward["counts"][me_idx[mid], fwd_dense[vid]])
        v_match = 0
        if reverse is not None and vid in v_idx and mid in rev_dense:
            v_match = int(reverse["counts"][v_idx[vid], rev_dense[mid]])
        observable = v_observable.get(vid)
        native = v_native.get(vid)

        usable = ""
        assigned = ""
        modal_v, modal_share, modal_margin, components = "", "", "", ""
        rank = ""
        if area is not None:
            ranked = sorted(me_partners[mid].items(), key=lambda kv: (-kv[1], kv[0]))
            if match_px:
                rank = str(1 + [p[0] for p in ranked].index(vid))
            modal_v, modal_share, modal_margin, assigned = me_modal[mid]
            assigned = _num(assigned)
            usable = "1" if (area >= MIN_AREA and me_modal[mid][3] >= MIN_ASSIGNED) else "0"
            components = str(me_components[mid])
            # accounting: land pixels == positive hits + outside+water+unassigned
            kinds = forward["kinds"][me_idx[mid]]
            if int(kinds[0]) != int(forward["counts"][me_idx[mid]][1:].sum()) or \
                    int(kinds[1:].sum()) != int(forward["counts"][me_idx[mid]][0]):
                fail(f"census accounting mismatch for ME {mid}")
            outside_px, water_px, unassigned_px = (str(int(kinds[k])) for k in (1, 2, 3))
        else:
            outside_px = water_px = unassigned_px = ""
        v_rank = ""
        share_observable = share_native = ""
        if observable is not None and observable > 0:
            ranked = sorted(v_partners[vid].items(), key=lambda kv: (-kv[1], kv[0]))
            if v_match:
                v_rank = str(1 + [p[0] for p in ranked].index(mid))
            share_observable = _num(v_match / observable)
        if native is not None and native > 0:
            share_native = _num(v_match / native)
        if reverse is not None and vid in v_idx:
            kinds = reverse["kinds"][v_idx[vid]]
            if int(kinds[0]) != int(reverse["counts"][v_idx[vid]][1:].sum()) or \
                    int(kinds[1:].sum()) != int(reverse["counts"][v_idx[vid]][0]):
                fail(f"census accounting mismatch for vanilla {vid}")

        is_canonical = "1" if canonical.get(mid) == vid else "0"
        excluded = me_owner.get(mid) == "MAP" or "map border" in me_names[mid].lower()
        if excluded:
            edge_class = "excluded"
        elif is_canonical == "1":
            edge_class = "identity_accepted"
        elif match_px >= MIN_AREA:
            edge_class = "significant"
        elif match_px == 0 and v_match >= MIN_AREA:
            edge_class = "backward_only"
        elif match_px:
            edge_class = "sliver"
        else:
            edge_class = "evidence_only"

        # relation_scope: how much weight the relation claim carries.
        # Never "proven"/"complete_subset" — raster overlap is an
        # observation; a province-level crosswalk is required for any
        # future complete-subset proof.
        if excluded:
            relation_scope = "excluded"
        elif is_canonical == "1":
            relation_scope = "compatibility"
        elif "comment" in edge["families"] or "manual" in edge["families"]:
            relation_scope = "asserted"
        else:
            relation_scope = "observation"

        rows.append({
            "vanilla_id": str(vid), "vanilla_name": vanilla_names[vid],
            "me_id": str(mid), "me_name": me_names[mid],
            "edge_source": "|".join(sorted(edge["sources"])),
            "evidence_families": "|".join(sorted(edge["families"])),
            "is_canonical": is_canonical,
            "canonical_ancestor": str(canonical.get(mid, "")),
            "canonical_primary_of_vanilla": "1" if v_primary.get(vid) == mid else "0",
            "me_area_px": str(area) if area is not None else "",
            "me_match_px": str(match_px) if match_px else ("0" if area is not None else ""),
            "me_share": _num(match_px / area) if area else "",
            "me_rank": rank,
            "me_modal_vanilla": str(modal_v),
            "me_modal_share": _num(modal_share) if modal_share != "" else "",
            "me_modal_margin": _num(modal_margin) if modal_margin != "" else "",
            "me_assigned_share": assigned,
            "me_outside_px": outside_px, "me_water_px": water_px,
            "me_unassigned_px": unassigned_px,
            "me_components": components,
            "relation_scope": relation_scope,
            "edge_class": edge_class,
            "geometry_usable": usable,
            "v_native_area_px": str(native) if native is not None else "",
            "v_observable_px": str(observable) if observable is not None else "",
            "v_match_px": str(v_match) if v_match else ("0" if observable is not None else ""),
            "v_share_observable": share_observable,
            "v_share_native": share_native,
            "v_rank": v_rank,
            "comment_votes": str(edge["comment_votes"]) if edge["comment_votes"] else "",
            "vp_votes": str(edge["vp_votes"]) if edge["vp_votes"] else "",
            "name_exact": "1" if "name" in edge["families"] else "0",
            "same_id": "1" if "same_id" in edge["families"] else "0",
            "manual_kind": edge.get("manual_kind", ""),
        })

    if not rows:
        fail("census produced no edges at all")
    if len({(r["me_id"], r["vanilla_id"]) for r in rows}) != len(rows):
        fail("duplicate (me_id, vanilla_id) edge keys")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"state_edges: {len(rows)} edges "
          f"({sum(1 for r in rows if r['is_canonical'] == '1')} canonical, "
          f"{sum(1 for r in rows if r['edge_class'] == 'excluded')} excluded) "
          f"-> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
