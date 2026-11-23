#!/usr/bin/env python
"""Build the 1936.1.1 owner-constrained vanilla<->ME edge ledger.

Companion to build_state_edges.py: same (me_id, vanilla_id) long-edge union
(current docs/states/state_edges.tsv rows UNION the live raster census, both
sampling directions), but every row is additionally classified by the
1936.1.1 state owner on each side so consumers can restrict the many-to-many
candidate space to relations inside one country's borders.

This is a 1936 owner-constrained OBSERVATION/FITTING ledger: `same_owner`
rows are candidates inside a country's border, `cross_owner` rows are
retained only as border/pollution diagnostics, and `unknown_owner` rows get
no within-country conclusion at all. No row claims complete-subset proof:
owner equality is a scoping filter over the census/edge observation, never
a proof, exactly like the canonical edge table's own relation_scope ladder.

Owner resolution (pure helper `state_owner_at`, unit-testable):
  * read the `history` block of each history/states/*.txt on BOTH sides;
  * start from the undated `owner = TAG` leaf (last one wins);
  * apply dated `Y.M.D[.H]` blocks in ascending order when their date is
    <= 1936.1.1 (hour suffixes on that date still count);
  * a state with no owner resolves to "" -> the row's owner_relation becomes
    `unknown_owner`. `owner` is the state owner, never core/controller.

Owner-filtered census columns (denominators always explicit, never
silently renormalised onto global shares):
  me_area_px        ME land pixels registered in the forward census
  me_owner_area_px  subset of me_area_px sampled onto vanilla states whose
                    owner equals the ME state's owner (0 when owner unknown
                    or no same-owner partner was hit)
  me_owner_share    me_owner_match_px / me_owner_area_px — emitted only for
                    same_owner rows with a positive owner denominator
  v_native_area_px  true vanilla land pixels in the vanilla raster
  v_observable_px   vanilla land pixels seen through the reverse census
                    (v_share denominator, explicit)
  v_owner_area_px / v_owner_share  mirror the forward owner columns in the
                    reverse direction (same-owner ME partner mass)

Ranks: deterministic (-pixels, partner_id) inside the global partner set
(me_rank_global / v_rank_global) and inside the same-owner partner subset
(me_rank_owner / v_rank_owner; empty for non-same_owner rows). No
thresholds: every raster pair and every current ledger edge is kept.

source_scope / evidence_families / edge_source / edge_class /
comment_votes / manual_kind / is_canonical / canonical_ancestor are carried
verbatim from state_edges.tsv rows; raster-only pairs (stale ledger) get
edge_source=raster, evidence_families=raster, source_scope=observation and
a freshly computed edge_class.

Outputs (no timestamps):
  docs/states/state_edges_1936.tsv          one row per (me_id, vanilla_id)
  docs/states/state_edges_1936_summary.tsv  one row per owner tag
  docs/states/state_edges_1936_summary.json fixed schema v1: inputs, counts,
                                            unknown-owner state lists

The build fails closed on: malformed history files or rasters, an
unavailable/non-finite coast affine, owner-table/state-id drift, ledger
endpoints unknown to the current history/states sets, duplicate edge keys,
or census accounting mismatches.

Usage: python tools/build_state_edges_1936.py [--vanilla DIR] [--mod DIR]
       [--edges PATH] [--output PATH] [--summary PATH] [--summary-json PATH]
       (HOI4_DIR / ME_MOD environment overrides honoured; works from any cwd)
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root, repo_root  # noqa: E402
import continent_registration as registration  # noqa: E402
import state_geography_evidence as geo  # noqa: E402
from pdx_parse import Parser  # noqa: E402

HOI4 = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
MOD = os.environ.get("ME_MOD") or mod_root()
TABLES = Path(repo_root()) / "docs" / "states"
DEFAULT_EDGES = TABLES / "state_edges.tsv"
DEFAULT_OUTPUT = TABLES / "state_edges_1936.tsv"
DEFAULT_SUMMARY = TABLES / "state_edges_1936_summary.tsv"
DEFAULT_SUMMARY_JSON = TABLES / "state_edges_1936_summary.json"

CUTOFF = (1936, 1, 1)                          # year, month, day
MIN_AREA = geo.MIN_OVERLAP_AREA                # 64, edge_class only

COLUMNS = [
    "vanilla_id", "vanilla_name", "vanilla_owner",
    "me_id", "me_name", "me_owner",
    "owner_relation", "candidate_scope", "source_scope",
    "edge_source", "evidence_families",
    "is_canonical", "canonical_ancestor",
    "edge_class", "comment_votes", "manual_kind",
    "me_area_px", "me_match_px", "me_share",
    "me_owner_area_px", "me_owner_match_px", "me_owner_share",
    "me_rank_global", "me_rank_owner",
    "v_native_area_px", "v_observable_px", "v_match_px", "v_share",
    "v_owner_area_px", "v_owner_match_px", "v_owner_share",
    "v_rank_global", "v_rank_owner",
]

SUMMARY_COLUMNS = [
    "owner",
    "me_states", "me_land_px",
    "me_owner_sampled_px", "me_owner_sampled_share",
    "vanilla_states", "v_land_px",
    "v_owner_sampled_px", "v_owner_sampled_share",
    "rows_total", "within_country", "cross_country_diagnostic",
    "unknown_owner_rows",
    "canonical_rows", "canonical_within_country", "canonical_cross_owner",
    "same_owner_partners", "cross_owner_partners",
]

LEDGER_COPY = ("edge_source", "evidence_families", "is_canonical",
               "canonical_ancestor", "edge_class", "comment_votes",
               "manual_kind", "relation_scope")

_DATE_KEY = re.compile(r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?$")


def fail(msg):
    raise SystemExit("state_edges_1936: " + str(msg))


def _num(value):
    if value is None or value == "":
        return ""
    return f"{0.0 if abs(value) < 0.0000005 else value:.6f}"


def _invert_affine(affine):
    """Invert a 2x3 affine with explicit float64 arithmetic."""
    a, b, c = affine[0]
    d, e, f = affine[1]
    det = a * e - b * d
    if not np.isfinite(det) or det == 0.0:
        raise ValueError("coast affine is not invertible")
    inverse = np.zeros((2, 3), dtype=np.float64)
    inverse[0, 0] = e / det
    inverse[0, 1] = -b / det
    inverse[0, 2] = (b * f - e * c) / det
    inverse[1, 0] = -d / det
    inverse[1, 1] = a / det
    inverse[1, 2] = (d * c - a * f) / det
    return inverse


# ------------------------------------------------------------- owner parsing

def _parse_date_key(key):
    """Clausewitz date literal `Y.M.D[.H]` -> (y, m, d, h), else None."""
    match = _DATE_KEY.match((key or "").strip())
    if not match:
        return None
    hour = int(match.group(4)) if match.group(4) is not None else 0
    return (int(match.group(1)), int(match.group(2)),
            int(match.group(3)), hour)


def state_owner_at(history_children, cutoff=CUTOFF):
    """Resolve the state owner at `cutoff` from a parsed history block.

    Pure helper: takes the `children` list of a parsed `history = { ... }`
    block (pdx_parse nodes) and a (year, month, day) tuple. The undated
    `owner = TAG` leaf is the base (last assignment wins); dated
    `Y.M.D[.H]` sub-blocks are then applied in ascending (date, file-order)
    sequence while their date is <= cutoff — any hour of the cutoff day
    counts. `owner` inside non-dated nested blocks (buildings, victory
    points, ...) is never read. Returns "" when no owner ever applied.
    """
    limit = (cutoff[0], cutoff[1], cutoff[2], 99)
    owner = ""
    dated = []
    for order, node in enumerate(history_children or []):
        if node.key == "owner" and not node.is_block:
            owner = node.val or ""
        elif node.is_block:
            stamp = _parse_date_key(node.key)
            if stamp is not None and stamp <= limit:
                dated.append((stamp, order, node))
    for _stamp, _order, node in sorted(dated, key=lambda item: item[:2]):
        for child in node.children:
            if child.key == "owner" and not child.is_block:
                owner = child.val or ""
    return owner.strip()


def load_state_owners(root, cutoff=CUTOFF):
    """{state_id: owner_tag} for every history/states file; "" = unknown.

    Same ID source as the canonical loaders: the numeric filename prefix.
    Fails closed on parser errors, non-single state blocks or duplicate
    history blocks; a missing history block or missing owner yields "".
    """
    directory = Path(root) / "history" / "states"
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        fail(f"no history/states/*.txt under {root!s}")
    owners = {}
    for path in paths:
        match = re.match(r"^(\d+)\s*-", path.name)
        if not match:
            fail(f"{path}: filename must start with a numeric state id")
        sid = int(match.group(1))
        parser = Parser(path.read_text(encoding="utf-8-sig", errors="replace"))
        blocks = [n for n in parser.parse()
                  if n.key == "state" and n.is_block]
        if parser.errors or len(blocks) != 1:
            fail(f"{path}: malformed state: "
                 f"{parser.errors or 'expected one state block'}")
        history = [n for n in blocks[0].children
                   if n.key == "history" and n.is_block]
        if len(history) > 1:
            fail(f"{path}: duplicate history block")
        owners[sid] = state_owner_at(
            history[0].children, cutoff) if history else ""
    return owners


# ------------------------------------------------------------------- helpers

def _read_edges(path):
    """Current ledger rows keyed by (me_id, vanilla_id)."""
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


def _edge_class(me_id, me_name, me_owner, is_canonical, match_px, v_match):
    """Same precedence ladder as the canonical generator (raster-only rows)."""
    if me_owner == "MAP" or "map border" in (me_name or "").lower():
        return "excluded"
    if is_canonical:
        return "identity_accepted"
    if match_px >= MIN_AREA:
        return "significant"
    if match_px == 0 and v_match >= MIN_AREA:
        return "backward_only"
    if match_px:
        return "sliver"
    return "evidence_only"


def _rank(pairs, member):
    """1-based rank of `member` in a (-px, id)-sorted partner list."""
    ranked = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    for index, (pid, _px) in enumerate(ranked, start=1):
        if pid == member:
            return index
    return None


# ---------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vanilla", default=HOI4, help="vanilla game dir")
    parser.add_argument("--mod", default=MOD, help="mod dir")
    parser.add_argument("--edges", default=str(DEFAULT_EDGES),
                        help="current state_edges.tsv ledger")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="state_edges_1936.tsv output path")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY),
                        help="per-owner summary TSV output path")
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON),
                        help="summary JSON output path")
    parser.add_argument("--cutoff", default="%d.%d.%d" % CUTOFF,
                        help="owner cutoff Y.M.D (default 1936.1.1)")
    args = parser.parse_args(argv)
    try:
        cutoff = tuple(int(p) for p in args.cutoff.split("."))
        assert len(cutoff) == 3 and all(0 <= p <= 9999 for p in cutoff)
    except (ValueError, AssertionError):
        fail(f"malformed --cutoff {args.cutoff!r} (expected Y.M.D)")

    # ------------------------------------------------ ledger + states + owners
    ledger = _read_edges(Path(args.edges))
    try:
        me_states = geo.load_states(args.mod)
        v_states = geo.load_states(args.vanilla)
    except (OSError, ValueError, TypeError, IndexError) as error:
        fail(f"invalid state input: {error}")
    me_owners = load_state_owners(args.mod, cutoff)
    v_owners = load_state_owners(args.vanilla, cutoff)
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

    # ------------------------------------------------ rasters + dual census
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
            v_land, me_land, _invert_affine(affine), target_land_mask=me_mask)
    except (OSError, ValueError, TypeError, IndexError) as error:
        fail(f"invalid state raster/census: {error}")
    del me_prov, me_state, v_prov, v_state
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
    v_observable = {int(sid): int(reverse["source_area_px"][i])
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

    # accounting parity with the canonical generator's closed-world checks
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

    # same-owner partner denominators — never renormalised from global
    # shares. Census-known states without partners get an explicit 0.
    me_owner_area = {mid: 0 for mid in me_area}
    for mid, partners in me_partners.items():
        mo = me_owners.get(mid, "")
        me_owner_area[mid] = sum(
            px for vid, px in partners.items()
            if mo and v_owners.get(vid, "") == mo)
    v_owner_area = {vid: 0 for vid in v_observable}
    for vid, partners in v_partners.items():
        vo = v_owners.get(vid, "")
        v_owner_area[vid] = sum(
            px for mid, px in partners.items()
            if vo and me_owners.get(mid, "") == vo)

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
            same = mo == vo
            relation = "same_owner" if same else "cross_owner"
            scope = "within_country" if same else "cross_country_diagnostic"
        else:
            same = False
            relation = "unknown_owner"
            scope = "unknown_owner"

        match_px = 0
        if forward is not None and mid in me_idx and vid in fwd_dense:
            match_px = int(forward["counts"][me_idx[mid], fwd_dense[vid]])
        v_match = 0
        if reverse is not None and vid in v_idx and mid in rev_dense:
            v_match = int(reverse["counts"][v_idx[vid], rev_dense[mid]])
        area = me_area.get(mid)
        observable = v_observable.get(vid)
        native = v_native.get(vid)

        carried = ledger.get((mid, vid)) or {}
        is_canonical = carried.get("is_canonical") == "1"
        if carried:
            edge_class = carried.get("edge_class", "")
            source_scope = carried.get("relation_scope", "")
        else:
            # raster-only pair missing from a stale ledger
            edge_class = _edge_class(
                mid, me_names.get(mid, ""), mo, is_canonical,
                match_px, v_match)
            source_scope = "excluded" if edge_class == "excluded" \
                else "observation"

        partners = me_partners.get(mid, {})
        owner_partners = {p: px for p, px in partners.items()
                          if mo and v_owners.get(p, "") == mo}
        rank_global = _rank(partners, vid) if match_px else None
        rank_owner = _rank(owner_partners, vid) \
            if same and match_px else None
        owner_area = me_owner_area.get(mid)
        owner_match = match_px if same else 0

        v_part = v_partners.get(vid, {})
        v_owner_partners = {p: px for p, px in v_part.items()
                            if vo and me_owners.get(p, "") == vo}
        v_rank_global = _rank(v_part, mid) if v_match else None
        v_rank_owner = _rank(v_owner_partners, mid) \
            if same and v_match else None
        v_own_area = v_owner_area.get(vid)
        v_owner_match = v_match if same else 0

        rows.append({
            "vanilla_id": str(vid),
            "vanilla_name": v_names.get(vid, ""),
            "vanilla_owner": vo,
            "me_id": str(mid),
            "me_name": me_names.get(mid, ""),
            "me_owner": mo,
            "owner_relation": relation,
            "candidate_scope": scope,
            "source_scope": source_scope,
            "edge_source": carried.get("edge_source", "raster"),
            "evidence_families": carried.get("evidence_families", "raster"),
            "is_canonical": "1" if is_canonical else "0",
            "canonical_ancestor": carried.get("canonical_ancestor", ""),
            "edge_class": edge_class,
            "comment_votes": carried.get("comment_votes", ""),
            "manual_kind": carried.get("manual_kind", ""),
            "me_area_px": str(area) if area is not None else "",
            "me_match_px": str(match_px) if match_px
            else ("0" if area is not None else ""),
            "me_share": _num(match_px / area) if area else "",
            "me_owner_area_px":
            str(owner_area) if owner_area is not None else "",
            "me_owner_match_px":
            str(owner_match) if owner_area is not None else "",
            "me_owner_share": _num(owner_match / owner_area)
            if same and owner_area else "",
            "me_rank_global": str(rank_global) if rank_global else "",
            "me_rank_owner": str(rank_owner) if rank_owner else "",
            "v_native_area_px": str(native) if native is not None else "",
            "v_observable_px":
            str(observable) if observable is not None else "",
            "v_match_px": str(v_match) if v_match
            else ("0" if observable is not None else ""),
            "v_share": _num(v_match / observable)
            if observable else "",
            "v_owner_area_px":
            str(v_own_area) if v_own_area is not None else "",
            "v_owner_match_px":
            str(v_owner_match) if v_own_area is not None else "",
            "v_owner_share": _num(v_owner_match / v_own_area)
            if same and v_own_area else "",
            "v_rank_global": str(v_rank_global) if v_rank_global else "",
            "v_rank_owner": str(v_rank_owner) if v_rank_owner else "",
        })

    if not rows:
        fail("no edges after union of ledger and raster census")
    if len({(r["me_id"], r["vanilla_id"]) for r in rows}) != len(rows):
        fail("duplicate (me_id, vanilla_id) edge keys")

    # ------------------------------------------------------------- summaries
    owners = sorted({*me_owners.values(), *v_owners.values()})
    summary_rows = []
    for tag in owners:
        me_ids = [sid for sid, o in me_owners.items() if o == tag]
        v_ids = [sid for sid, o in v_owners.items() if o == tag]
        tagged = [r for r in rows
                  if r["me_owner"] == tag or r["vanilla_owner"] == tag]
        me_land = sum(me_area.get(s, 0) for s in me_ids)
        me_own_px = sum(me_owner_area.get(s, 0) for s in me_ids)
        v_land = sum(v_native.get(s, 0) for s in v_ids)
        v_own_px = sum(v_owner_area.get(s, 0) for s in v_ids)
        same_partners = {r["vanilla_id"] for r in tagged
                         if r["me_owner"] == tag
                         and r["owner_relation"] == "same_owner"}
        cross_partners = {r["vanilla_id"] for r in tagged
                          if r["me_owner"] == tag
                          and r["owner_relation"] == "cross_owner"}
        summary_rows.append({
            "owner": tag,
            "me_states": str(len(me_ids)),
            "me_land_px": str(me_land),
            "me_owner_sampled_px": str(me_own_px),
            "me_owner_sampled_share": _num(me_own_px / me_land)
            if me_land else "",
            "vanilla_states": str(len(v_ids)),
            "v_land_px": str(v_land),
            "v_owner_sampled_px": str(v_own_px),
            "v_owner_sampled_share": _num(v_own_px / v_land)
            if v_land else "",
            "rows_total": str(len(tagged)),
            "within_country":
            str(sum(1 for r in tagged
                    if r["candidate_scope"] == "within_country")),
            "cross_country_diagnostic":
            str(sum(1 for r in tagged
                    if r["candidate_scope"] == "cross_country_diagnostic")),
            "unknown_owner_rows":
            str(sum(1 for r in tagged
                    if r["candidate_scope"] == "unknown_owner")),
            "canonical_rows":
            str(sum(1 for r in tagged if r["is_canonical"] == "1")),
            "canonical_within_country":
            str(sum(1 for r in tagged if r["is_canonical"] == "1"
                    and r["candidate_scope"] == "within_country")),
            "canonical_cross_owner":
            str(sum(1 for r in tagged if r["is_canonical"] == "1"
                    and r["candidate_scope"] == "cross_country_diagnostic")),
            "same_owner_partners":
            "|".join(sorted(same_partners, key=int)),
            "cross_owner_partners":
            "|".join(sorted(cross_partners, key=int)),
        })

    unknown_me = sorted(s for s, o in me_owners.items() if not o)
    unknown_v = sorted(s for s, o in v_owners.items() if not o)
    summary_json = {
        "schema": "state_edges_1936_summary/v1",
        "kind": "1936 owner-constrained observation/fitting ledger "
                "(not a complete-subset proof)",
        "cutoff": "%d.%d.%d" % cutoff,
        "inputs": {
            "vanilla": str(Path(args.vanilla)),
            "mod": str(Path(args.mod)),
            "edges": str(Path(args.edges)),
        },
        "counts": {
            "me_states": len(me_states),
            "vanilla_states": len(v_states),
            "ledger_rows": len(ledger),
            "raster_pairs_forward":
            sum(len(p) for p in me_partners.values()),
            "raster_pairs_reverse":
            sum(len(p) for p in v_partners.values()),
            "rows": len(rows),
            "within_country":
            sum(1 for r in rows
                if r["candidate_scope"] == "within_country"),
            "cross_country_diagnostic":
            sum(1 for r in rows
                if r["candidate_scope"] == "cross_country_diagnostic"),
            "unknown_owner":
            sum(1 for r in rows
                if r["candidate_scope"] == "unknown_owner"),
            "canonical_rows":
            sum(1 for r in rows if r["is_canonical"] == "1"),
            "canonical_within_country":
            sum(1 for r in rows if r["is_canonical"] == "1"
                and r["candidate_scope"] == "within_country"),
            "canonical_cross_owner":
            sum(1 for r in rows if r["is_canonical"] == "1"
                and r["candidate_scope"] == "cross_country_diagnostic"),
            "owner_rows": len(summary_rows),
        },
        "unknown_owner": {
            "me_states": unknown_me,
            "vanilla_states": unknown_v,
        },
    }

    # ---------------------------------------------------------------- output
    for target, writer_fn in (
            (Path(args.output), "edges"),
            (Path(args.summary), "summary"),
            (Path(args.summary_json), "json")):
        target.parent.mkdir(parents=True, exist_ok=True)
        if writer_fn == "edges":
            with target.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=COLUMNS, delimiter="\t",
                    lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
        elif writer_fn == "summary":
            with target.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=SUMMARY_COLUMNS, delimiter="\t",
                    lineterminator="\n")
                writer.writeheader()
                writer.writerows(summary_rows)
        else:
            target.write_text(json.dumps(summary_json, indent=2,
                                         sort_keys=True) + "\n",
                              encoding="utf-8")

    counts = summary_json["counts"]
    print(f"state_edges_1936: {counts['rows']} edges "
          f"({counts['within_country']} within-country, "
          f"{counts['cross_country_diagnostic']} cross-owner diagnostic, "
          f"{counts['unknown_owner']} unknown-owner) "
          f"-> {args.output}; {counts['owner_rows']} owner rows -> "
          f"{args.summary}; json -> {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
