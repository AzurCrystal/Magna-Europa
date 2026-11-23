#!/usr/bin/env python
"""Build the state-relation map: area-overlap edges, semantic candidates,
script substitutions, derived compatibility views, and diagnostics.

  python tools/build_state_relation_map.py \
      [--vanilla DIR] [--mod DIR] [--output-dir docs/states] \
      [--no-natural] [--border-radius 2] [--min-relation-px 64]

Three deliberately separate meanings, never conflated:

  area_parent    -- sampled raster overlap under the topology registration
                    (state_relation_registration + state_relation_census);
                    claim=sampled_area_overlap, complete_subset=unproven.
  identity       -- semantic identity candidates from explicit evidence
                    only: mapping_comment_edges.tsv, the literal
                    MANUAL/MANUAL_ME2V dicts parsed (never executed) out of
                    tools/build_state_map.py, plus unique exact normalized
                    name matches. Never raster output; claim=
                    semantic_identity_candidate.
  substitution   -- occurrence-level script-compat edges: mod-side
                    focus_state_refs.tsv occurrences plus aggregated
                    comment edges; claim=script_substitution.

Derived compatibility views (state_relation_views.tsv and
state_relation_v2m.tsv) combine the three with a conservative precedence:
a unique high-confidence area-parent candidate first, then a unique
explicit identity, then a unique substitution; anything ambiguous or
absent leaves derived_v_ancestor empty with status review/unmapped. The
view is a convenience join only -- never a complete-subset proof.

All outputs are deterministic (sorted rows, no timestamps), UTF-8, LF.
Semantic inputs fail closed: malformed IDs, duplicate explicit pairs, or
unknown state endpoints raise instead of being skipped silently.
"""

import argparse
import ast
import csv
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from _modroot import mod_root, repo_root  # noqa: E402

HOI4 = os.environ.get("HOI4_DIR") or \
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"

SCHEMA_REGISTRATION = "state_relation_registration/v1"
SCHEMA_SUMMARY = "state_relation_summary/v1"

CLAIM_AREA = "sampled_area_overlap"
CLAIM_IDENTITY = "semantic_identity_candidate"
CLAIM_SUBSTITUTION = "script_substitution"
CLAIM_VIEW = "compatibility_view"
CLAIM_BOUNDARY = "sampled_boundary_geometry"

# Identity evidence strengths: explicit evidence may drive the derived
# view; name matches are annotations only and never derive an ancestor.
STRENGTH_EXPLICIT = "explicit"
STRENGTH_WEAK = "weak"
EXPLICIT_IDENTITY_SOURCES = ("manual_member", "manual_direct", "comment")

# Registration statuses that disqualify an edge from the derived
# area-parent tier.
EXCLUDED_EDGE_STATUSES = ("unmatched_component", "excluded_inset")

# Derived-view thresholds for the unique high-confidence area parent.
DERIVE_CONFIDENT_SHARE_MIN = 0.95
DERIVE_UNCERTAIN_SHARE_MAX = 0.05

# A component whose local refinement residual exceeds half the global
# mutual-NN cap is listed as an outlier diagnostic.
HIGH_RESIDUAL_P95_PX = 12.0

EDGE_COLUMNS = [
    "me_id", "me_name", "me_owner_1936", "me_component_ids",
    "vanilla_id", "vanilla_name", "vanilla_owner_1936",
    "relation", "significant", "confident_hits_px", "uncertain_hits_px",
    "source_area_px", "source_sampled_px", "forward_share",
    "target_native_area_px", "reverse_hits_px", "reverse_share",
    "reverse_overlap_possible",
    "outside_px", "water_px", "unassigned_px",
    "claim", "complete_subset", "diagnostic_flags",
]

IDENTITY_COLUMNS = [
    "vanilla_id", "vanilla_name", "me_id", "me_name",
    "source", "evidence_strength", "comment_votes", "claim",
]

SUBSTITUTION_COLUMNS = [
    "file", "line", "focus_id", "block_path", "construct", "key",
    "literal", "me_id", "vanilla_id", "ref_kind", "source",
    "comment_vanilla_id", "migration_comment", "claim",
]

VIEW_M2V_COLUMNS = [
    "me_id", "me_name", "me_owner_1936", "area_parent_ids",
    "identity_ids", "substitution_ids", "derived_v_ancestor",
    "view_source", "status", "claim",
]

VIEW_V2M_COLUMNS = [
    "vanilla_id", "vanilla_name", "vanilla_owner_1936",
    "derived_members", "primary_me_member", "view_source",
    "status", "claim",
]


# ------------------------------------------------------------ parsing

def _fail(msg):
    raise ValueError(msg)


def strict_state_id(value, what):
    """Parse one positive integer state id; fail closed on anything else."""
    if isinstance(value, bool):
        _fail("%s: boolean is not a state id" % what)
    if isinstance(value, int):
        num = value
    else:
        text = str(value).strip()
        if not re.fullmatch(r"\d+", text):
            _fail("%s: malformed state id %r" % (what, value))
        num = int(text)
    if num <= 0:
        _fail("%s: state id must be positive, got %r" % (what, value))
    return num


def _id_list(text, what):
    """Pipe-separated positive id list -> list of ints (order kept)."""
    text = str(text or "").strip()
    if not text:
        return []
    return [strict_state_id(part, what) for part in text.split("|")]


def require_id(sid, known, what):
    if sid not in known:
        _fail("%s: unknown state id %d" % (what, sid))
    return sid


def norm_name(name):
    """NFKD/ascii/lower/alphanumeric name key (same rule as build_state_map)."""
    s = unicodedata.normalize("NFKD", name or "")
    s = s.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


# ------------------------------------------------------------ names

def _parse_loc_key(path, prefix):
    out = {}
    rx = re.compile(r'\s*(\w+)(?::\d+)?\s*[=:]?\s*"([^"]*)"')
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            m = rx.match(line)
            if not m:
                continue
            key = m.group(1)
            if key.startswith(prefix) and key not in out:
                out[key] = m.group(2).replace(r"\t", "").replace(
                    r"\n", "").strip()
    return out


def state_names(root, states):
    """{sid: display name}: STATE_<id> localisation, else filename title."""
    loc = {}
    for path in sorted(glob.glob(
            os.path.join(root, "localisation", "english", "*.yml"))):
        for key, value in _parse_loc_key(path, "STATE_").items():
            loc.setdefault(key, value)
    names = {}
    for sid in states:
        name = loc.get("STATE_%d" % sid)
        if not name:
            for path in sorted(glob.glob(os.path.join(
                    root, "history", "states", "%d-*.txt" % sid))):
                name = os.path.basename(path).split("-", 1)[1][:-4]
                break
        names[sid] = name or ""
    return names


# -------------------------------------------------------- MANUAL dicts

def _literal_dict(node, name, path):
    """ast.Dict -> {int: int|list[int]} without executing the source."""
    out = {}
    for key_node, val_node in zip(node.keys, node.values):
        if not isinstance(key_node, ast.Constant) or \
                not isinstance(key_node.value, int):
            _fail("%s:%d %s: non-literal dict key" %
                  (path, getattr(key_node, "lineno", 0), name))
        key = strict_state_id(key_node.value,
                              "%s:%d %s key" % (path, key_node.lineno, name))
        if key in out:
            _fail("%s:%d %s: duplicate key %d"
                  % (path, key_node.lineno, name, key))
        if isinstance(val_node, (ast.List, ast.Tuple)):
            vals = []
            for elt in val_node.elts:
                if not isinstance(elt, ast.Constant) or \
                        not isinstance(elt.value, int):
                    _fail("%s:%d %s: non-literal list member"
                          % (path, getattr(elt, "lineno", 0), name))
                vals.append(strict_state_id(
                    elt.value,
                    "%s:%d %s member" % (path, elt.lineno, name)))
            out[key] = vals
        elif isinstance(val_node, ast.Constant) and \
                isinstance(val_node.value, int):
            out[key] = strict_state_id(
                val_node.value,
                "%s:%d %s value" % (path, val_node.lineno, name))
        else:
            _fail("%s:%d %s: non-literal dict value"
                  % (path, getattr(val_node, "lineno", 0), name))
    return out


def parse_manual_dicts(source):
    """Extract MANUAL and MANUAL_ME2V literals from build_state_map.py text.

    Pure AST read: the script is parsed, never imported or executed.
    """
    tree = ast.parse(source, filename="build_state_map.py")
    found = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and \
                isinstance(node.target, ast.Name):
            target = node.target.id
        if target in ("MANUAL", "MANUAL_ME2V"):
            if not isinstance(node.value, ast.Dict):
                _fail("build_state_map.py:%d %s: not a literal dict"
                      % (getattr(node.value, "lineno", 0), target))
            found[target] = _literal_dict(
                node.value, target, "build_state_map.py")
    missing = [n for n in ("MANUAL", "MANUAL_ME2V") if n not in found]
    if missing:
        _fail("build_state_map.py: missing literal dict(s) %s" % missing)
    return found["MANUAL"], found["MANUAL_ME2V"]


# ------------------------------------------------------ identity edges

def read_comment_edges(path, vanilla_ids, me_ids):
    """mapping_comment_edges.tsv -> identity rows (fail-closed)."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None or \
                {"vanilla_id", "me_id"} - set(reader.fieldnames):
            _fail("%s: expected vanilla_id/me_id columns" % path)
        for line_no, row in enumerate(reader, start=2):
            vid = require_id(
                strict_state_id(row.get("vanilla_id"),
                                "%s:%d vanilla_id" % (path, line_no)),
                vanilla_ids, "%s:%d vanilla_id" % (path, line_no))
            mid = require_id(
                strict_state_id(row.get("me_id"),
                                "%s:%d me_id" % (path, line_no)),
                me_ids, "%s:%d me_id" % (path, line_no))
            votes = row.get("comment_votes") or ""
            if votes != "":
                votes = strict_state_id(
                    votes, "%s:%d comment_votes" % (path, line_no))
            rows.append({
                "vanilla_id": vid, "me_id": mid,
                "source": "comment",
                "evidence_strength": STRENGTH_EXPLICIT,
                "comment_votes": votes,
                "claim": CLAIM_IDENTITY,
            })
    return rows


def manual_identity_rows(manual, manual_me2v, vanilla_ids, me_ids):
    """MANUAL (vanilla -> [me]) and MANUAL_ME2V (me -> vanilla) rows."""
    rows = []
    for vid, mids in sorted(manual.items()):
        vid = require_id(vid, vanilla_ids, "MANUAL key")
        seen = set()
        for mid in mids:
            mid = require_id(mid, me_ids, "MANUAL[%d] member" % vid)
            if mid in seen:
                _fail("MANUAL[%d]: duplicate member %d" % (vid, mid))
            seen.add(mid)
            rows.append({
                "vanilla_id": vid, "me_id": mid,
                "source": "manual_member",
                "evidence_strength": STRENGTH_EXPLICIT,
                "comment_votes": "",
                "claim": CLAIM_IDENTITY,
            })
    for mid, vid in sorted(manual_me2v.items()):
        mid = require_id(mid, me_ids, "MANUAL_ME2V key")
        vid = require_id(vid, vanilla_ids,
                         "MANUAL_ME2V[%d] value" % mid)
        rows.append({
            "vanilla_id": vid, "me_id": mid,
            "source": "manual_direct",
            "evidence_strength": STRENGTH_EXPLICIT,
            "comment_votes": "",
            "claim": CLAIM_IDENTITY,
        })
    return rows


def name_identity_rows(vanilla_names, me_names):
    """Unique exact normalized-name pairs plus same-id same-name pairs.

    'Unique exact' means the normalized name is held by exactly one
    vanilla state and exactly one ME state; collisions yield no row.
    Same-id rows additionally require equal normalized names and that the
    id exists on both sides.
    """
    v_by_norm = {}
    for sid, name in sorted(vanilla_names.items()):
        key = norm_name(name)
        if key:
            v_by_norm.setdefault(key, []).append(sid)
    m_by_norm = {}
    for sid, name in sorted(me_names.items()):
        key = norm_name(name)
        if key:
            m_by_norm.setdefault(key, []).append(sid)
    same_id_pairs = {
        sid for sid in set(vanilla_names) & set(me_names)
        if norm_name(vanilla_names[sid])
        and norm_name(vanilla_names[sid]) == norm_name(me_names[sid])}
    rows = []
    for key in sorted(set(v_by_norm) & set(m_by_norm)):
        vids, mids = v_by_norm[key], m_by_norm[key]
        if len(vids) == 1 and len(mids) == 1:
            if vids[0] == mids[0] and vids[0] in same_id_pairs:
                continue  # carried by the stronger same_id_name source
            rows.append({
                "vanilla_id": vids[0], "me_id": mids[0],
                "source": "name",
                "evidence_strength": STRENGTH_WEAK,
                "comment_votes": "",
                "claim": CLAIM_IDENTITY,
            })
    for sid in sorted(same_id_pairs):
        rows.append({
            "vanilla_id": sid, "me_id": sid,
            "source": "same_id_name",
            "evidence_strength": STRENGTH_WEAK,
            "comment_votes": "",
            "claim": CLAIM_IDENTITY,
        })
    return rows


def dedupe_identity(rows):
    """Dedupe on (vanilla_id, me_id, source); exact duplicate -> error."""
    seen = {}
    out = []
    for row in rows:
        key = (row["vanilla_id"], row["me_id"], row["source"])
        prev = seen.get(key)
        if prev is not None:
            if prev.get("comment_votes") != row.get("comment_votes"):
                _fail("duplicate identity edge %r with differing evidence"
                      % (key,))
            continue
        seen[key] = row
        out.append(row)
    out.sort(key=lambda r: (r["vanilla_id"], r["me_id"], r["source"]))
    return out


# -------------------------------------------------- substitution edges

def read_focus_substitutions(path, vanilla_ids, me_ids):
    """Mod-side focus_state_refs rows -> occurrence-level edges.

    One row per (occurrence, me_id): the live ME literal plus every
    resolved current_me_ref. Rows qualify when side == 'mod' AND the
    resolved vanilla_id is positive AND there is at least one live ME id
    to point at (literal_is_live_me or current_me_refs). Occurrences keep
    file/line/focus/block/key/literal verbatim; an additional
    comment_claim row is emitted when comment_vanilla_id carries a
    different explicit vanilla claim for the same occurrence.
    """
    rows = []
    seen = set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = {"side", "file", "line", "focus_id", "block_path",
                    "construct", "key", "literal", "vanilla_id",
                    "literal_is_live_me", "current_me_refs"}
        if reader.fieldnames is None or required - set(reader.fieldnames):
            _fail("%s: missing columns %s"
                  % (path, sorted(required - set(reader.fieldnames or []))))
        for line_no, row in enumerate(reader, start=2):
            if row.get("side") != "mod":
                continue
            vid_text = (row.get("vanilla_id") or "").strip()
            if vid_text == "":
                continue
            vid = strict_state_id(
                vid_text, "%s:%d vanilla_id" % (path, line_no))
            require_id(vid, vanilla_ids,
                       "%s:%d vanilla_id" % (path, line_no))
            live_literal = (row.get("literal_is_live_me") or "").strip()
            me_refs = _id_list(row.get("current_me_refs"),
                               "%s:%d current_me_refs" % (path, line_no))
            literal_text = (row.get("literal") or "").strip()
            literal_id = None
            if literal_text != "" and re.fullmatch(r"\d+", literal_text):
                literal_id = int(literal_text)
            me_targets = []
            if live_literal == "1":
                if literal_id is None:
                    _fail("%s:%d: literal_is_live_me=1 but literal %r "
                          "is not an id" % (path, line_no, literal_text))
                me_targets.append((require_id(
                    literal_id, me_ids,
                    "%s:%d literal" % (path, line_no)), "literal"))
            for mid in me_refs:
                require_id(mid, me_ids,
                           "%s:%d current_me_refs" % (path, line_no))
            me_targets += [(mid, "current_ref") for mid in me_refs
                           if mid != literal_id or live_literal != "1"]
            if not me_targets:
                continue
            comment_text = (row.get("comment_vanilla_id") or "").strip()
            comment_vids = _id_list(
                comment_text, "%s:%d comment_vanilla_id" % (path, line_no))
            for cvid in comment_vids:
                require_id(cvid, vanilla_ids,
                           "%s:%d comment_vanilla_id" % (path, line_no))
            for mid, kind in me_targets:
                base = (row.get("file") or "", row.get("line") or "",
                        row.get("focus_id") or "",
                        row.get("block_path") or "",
                        row.get("construct") or "",
                        row.get("key") or "",
                        row.get("literal") or "", mid, vid, kind,
                        "focus_script")
                if base in seen:
                    continue
                seen.add(base)
                rows.append({
                    "file": row.get("file") or "",
                    "line": row.get("line") or "",
                    "focus_id": row.get("focus_id") or "",
                    "block_path": row.get("block_path") or "",
                    "construct": row.get("construct") or "",
                    "key": row.get("key") or "",
                    "literal": row.get("literal") or "",
                    "me_id": mid, "vanilla_id": vid,
                    "ref_kind": kind, "source": "focus_script",
                    "comment_vanilla_id": comment_text,
                    "migration_comment": row.get("migration_comment") or "",
                    "claim": CLAIM_SUBSTITUTION,
                })
                for cvid in comment_vids:
                    if cvid == vid:
                        continue
                    ckey = base[:-2] + (cvid, "comment_claim",
                                        "focus_comment_claim")
                    if ckey in seen:
                        continue
                    seen.add(ckey)
                    rows.append({
                        "file": row.get("file") or "",
                        "line": row.get("line") or "",
                        "focus_id": row.get("focus_id") or "",
                        "block_path": row.get("block_path") or "",
                        "construct": row.get("construct") or "",
                        "key": row.get("key") or "",
                        "literal": row.get("literal") or "",
                        "me_id": mid,
                        "vanilla_id": cvid,
                        "ref_kind": "comment_claim",
                        "source": "focus_comment_claim",
                        "comment_vanilla_id": comment_text,
                        "migration_comment":
                            row.get("migration_comment") or "",
                        "claim": CLAIM_SUBSTITUTION,
                    })
    return rows


def comment_substitution_rows(comment_rows):
    """Aggregated comment edges as substitution candidates (no location)."""
    return [{
        "file": "", "line": "", "focus_id": "", "block_path": "",
        "construct": "", "key": "", "literal": "",
        "me_id": row["me_id"], "vanilla_id": row["vanilla_id"],
        "ref_kind": "comment_edge", "source": "comment",
        "comment_vanilla_id": row["vanilla_id"],
        "migration_comment": "",
        "claim": CLAIM_SUBSTITUTION,
    } for row in comment_rows]


def sort_substitutions(rows):
    return sorted(rows, key=lambda r: (
        r["file"], _sort_int(r["line"]), r["focus_id"], r["block_path"],
        r["construct"], _sort_int(r["key"]), _sort_int(r["literal"]),
        r["me_id"], r["vanilla_id"], r["ref_kind"], r["source"]))


def _sort_int(text):
    text = str(text or "").strip()
    return (0, int(text)) if re.fullmatch(r"\d+", text) else (1, text)


# -------------------------------------------------------- edge table

def aggregate_edges(paired_rows):
    """Merge census rows -> per-(me, vanilla) edge accumulators.

    ``paired_rows``: [(census_component() row, fit_components() entry)].
    Returns {(me_id, vanilla_id): accumulator} with hit sums (including
    reverse_hits_px unique-target-pixel counts), component provenance,
    and per-edge diagnostic flags.
    """
    merged = {}
    for row, comp in paired_rows:
        key = (int(row["source_id"]), int(row["target_id"]))
        acc = merged.get(key)
        if acc is None:
            acc = {
                "me_id": key[0], "vanilla_id": key[1],
                "confident_hits_px": 0, "uncertain_hits_px": 0,
                "source_area_px": 0, "source_sampled_px": 0,
                "reverse_hits_px": 0,
                "target_native_area_px": 0,
                "component_ids": set(), "statuses": set(),
                "methods": set(), "flags": set(),
            }
            merged[key] = acc
        acc["confident_hits_px"] += int(row["confident_hits_px"])
        acc["uncertain_hits_px"] += int(row["uncertain_hits_px"])
        acc["reverse_hits_px"] += int(row["reverse_hits_px"])
        acc["source_sampled_px"] += int(row["source_sampled_px"])
        acc["source_area_px"] = max(
            acc["source_area_px"], int(row["source_area_px"]))
        acc["target_native_area_px"] = max(
            acc["target_native_area_px"], int(row["target_native_area_px"]))
        acc["component_ids"].add(int(comp["component_id"]))
        acc["statuses"].add(comp["status"])
        method = comp["registration"].get("registration_method") or \
            comp["registration"].get("method_used") or "affine"
        acc["methods"].add(method)
        if comp["status"] in EXCLUDED_EDGE_STATUSES:
            acc["flags"].add(comp["status"])
        metrics = comp.get("metrics") or {}
        if metrics.get("fallback_reason"):
            acc["flags"].add("local_fallback")
        if metrics.get("natural_fallback_reason"):
            acc["flags"].add("natural_fallback")
        res = metrics.get("local_residual_p95_px")
        if res is not None and res > HIGH_RESIDUAL_P95_PX:
            acc["flags"].add("high_residual")
    return merged


def edge_flag_column(acc, me_owner, v_owner):
    flags = set(acc["flags"])
    if me_owner and v_owner and me_owner != v_owner:
        flags.add("cross_owner")
    if acc["uncertain_hits_px"] > acc["confident_hits_px"]:
        flags.add("uncertain_dominant")
    if not acc["confident_hits_px"]:
        flags.add("uncertain_only")
    # Merged unique-target-pixel reverse hits may exceed the whole target
    # native area when component registrations overlap on the same pixels.
    if acc["reverse_hits_px"] > acc["target_native_area_px"]:
        flags.add("reverse_overlap_possible")
    return "|".join(sorted(flags))


# --------------------------------------------------------- derived view

def area_parent_ids(edge_rows):
    """me_id -> [(vanilla_id, confident_share, vanilla_id)] sort key."""
    out = {}
    for row in edge_rows:
        denom = row["source_area_px"]
        share = row["confident_hits_px"] / denom if denom else 0.0
        out.setdefault(row["me_id"], []).append(
            (row["vanilla_id"], share))
    return {
        mid: [vid for vid, _ in sorted(
            members, key=lambda kv: (-kv[1], kv[0]))]
        for mid, members in out.items()
    }


def _by_target(rows, id_key):
    out = {}
    for row in rows:
        out.setdefault(row[id_key], set()).add(row["vanilla_id"])
    return {k: sorted(v) for k, v in out.items()}


def derive_view(area_rows, identity_ids, substitution_ids):
    """Conservative ME -> vanilla derived ancestor.

    ``area_rows``: merged+classified edge rows for this ME state, each
    carrying ``component_status_ok`` plus both 1936 owner tags.
    ``identity_ids``/``substitution_ids`` are sorted distinct vanilla-side
    ids for this ME state. An area-parent candidate additionally requires
    KNOWN equal owners (non-empty me_owner_1936 == vanilla_owner_1936):
    cross-owner or unknown-owner edges stay diagnostics-only and can
    never derive compatibility. Returns (derived_vid_or_None,
    view_source, status). Precedence: unique high-confidence area parent
    -> unique explicit identity -> unique substitution; multiple or no
    candidates never guess.
    """
    candidates = [
        r for r in area_rows
        if r["significant"]
        and r["component_status_ok"]
        and r["me_owner_1936"]
        and r["me_owner_1936"] == r["vanilla_owner_1936"]
        and (r["confident_hits_px"] / r["source_area_px"]
             if r["source_area_px"] else 0.0) >= DERIVE_CONFIDENT_SHARE_MIN
        and (r["uncertain_hits_px"] / r["source_area_px"]
             if r["source_area_px"] else 0.0) <= DERIVE_UNCERTAIN_SHARE_MAX
    ]
    if len(candidates) == 1:
        return candidates[0]["vanilla_id"], "area_parent", "derived"
    if len(candidates) > 1:
        return None, "area_parent", "review"
    if len(identity_ids) == 1:
        return identity_ids[0], "identity", "derived"
    if len(identity_ids) > 1:
        return None, "identity", "review"
    if len(substitution_ids) == 1:
        return substitution_ids[0], "substitution", "derived"
    if len(substitution_ids) > 1:
        return None, "substitution", "review"
    if area_rows or identity_ids or substitution_ids:
        return None, "none", "review"
    return None, "none", "unmapped"


def build_view_rows(me_ids, me_names, me_owners, edge_rows,
                    identity_rows, substitution_rows):
    """One derived view row per ME state."""
    area = area_parent_ids(edge_rows)
    edges_by_me = {}
    for row in edge_rows:
        edges_by_me.setdefault(row["me_id"], []).append(row)
    ident = _by_target(
        [r for r in identity_rows
         if r["source"] in EXPLICIT_IDENTITY_SOURCES], "me_id")
    weak_ident = _by_target(
        [r for r in identity_rows
         if r["source"] not in EXPLICIT_IDENTITY_SOURCES], "me_id")
    subs = _by_target(substitution_rows, "me_id")
    out = []
    for mid in sorted(me_ids):
        me_edges = edges_by_me.get(mid, [])
        identity_ids = sorted(set(ident.get(mid, [])) |
                              set(weak_ident.get(mid, [])))
        sub_ids = subs.get(mid, [])
        derived, source, status = derive_view(
            me_edges, ident.get(mid, []), sub_ids)
        if status == "unmapped" and identity_ids:
            # Weak (name-only) candidates never derive but still mark
            # the state as having evidence worth a look.
            status = "review"
        out.append({
            "me_id": mid,
            "me_name": me_names.get(mid, ""),
            "me_owner_1936": me_owners.get(mid, ""),
            "area_parent_ids": "|".join(str(v) for v in area.get(mid, [])),
            "identity_ids": "|".join(str(v) for v in identity_ids),
            "substitution_ids": "|".join(str(v) for v in sub_ids),
            "derived_v_ancestor": "" if derived is None else str(derived),
            "view_source": source,
            "status": status,
            "claim": CLAIM_VIEW,
        })
    return out


def build_v2m_rows(vanilla_ids, vanilla_names, vanilla_owners, view_rows):
    """Vanilla -> derived ME members from the m2v view."""
    members = {}
    for row in view_rows:
        if row["derived_v_ancestor"]:
            members.setdefault(int(row["derived_v_ancestor"]), []).append(
                (int(row["me_id"]), row["view_source"]))
    out = []
    for vid in sorted(vanilla_ids):
        mem = sorted(members.get(vid, []))
        sources = {src for _, src in mem}
        view_source = "|".join(sorted(sources)) if sources else "none"
        out.append({
            "vanilla_id": vid,
            "vanilla_name": vanilla_names.get(vid, ""),
            "vanilla_owner_1936": vanilla_owners.get(vid, ""),
            "derived_members": "|".join(str(m) for m, _ in mem),
            "primary_me_member": "" if not mem else str(mem[0][0]),
            "view_source": view_source,
            "status": "derived" if mem else "unmapped",
            "claim": CLAIM_VIEW,
        })
    return out


# ------------------------------------------------------------- IO

def _fmt(value):
    if isinstance(value, float):
        return "%.6f" % value
    return str(value)


def write_tsv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_fmt(row.get(c, "")) for c in columns])


def write_json(path, payload):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True,
                  ensure_ascii=False)
        fh.write("\n")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_rel(path):
    try:
        return os.path.relpath(path, repo_root()).replace(os.sep, "/")
    except ValueError:
        return os.path.abspath(path).replace(os.sep, "/")


def hash_inputs(files):
    """[{path, sha256}] for existing files, repo-relative, sorted."""
    out = []
    for path in sorted(set(files)):
        if path and os.path.isfile(path):
            out.append({"path": _repo_rel(path), "sha256": _sha256(path)})
        elif path:
            out.append({"path": _repo_rel(path), "sha256": None})
    return out


# ------------------------------------------------------------- build

def build(vanilla_root, mod_dir, out_dir, apply_natural=True,
          border_radius=2, min_relation_px=64, progress=None):
    """Run the full relation build; returns the written paths."""
    import numpy as np
    import state_relation_registration as reg
    import state_relation_census as census

    def note(msg):
        if progress:
            progress(msg)

    note("loading rasters")
    me = reg.load_side(mod_dir)
    van = reg.load_side(vanilla_root)

    me_names = state_names(me["root"], me["states"])
    v_names = state_names(van["root"], van["states"])
    me_owners = {sid: s.get("owner", "") or ""
                 for sid, s in me["states"].items()}
    v_owners = {sid: s.get("owner", "") or ""
                for sid, s in van["states"].items()}
    me_ids = set(me["states"])
    v_ids = set(van["states"])

    note("fitting global registration")
    glob_res = reg.fit_global(me, van, initial="bbox")
    note("fitting components (natural=%s)" % apply_natural)
    comp_results = reg.fit_components(
        me, van, glob_res, apply_natural=apply_natural)

    me_comp_index = {c["component_id"]: c for c in me["components"]}
    note("building vanilla border band (radius=%s)" % border_radius)
    band = census.build_border_band(van["state_raster"], radius=border_radius)

    note("running component census")
    shape = me["state_raster"].shape
    raw_rows = []          # (row, comp_entry)
    source_totals = {}     # me_id -> kind tallies
    for comp in comp_results:
        src = me_comp_index[comp["component_id"]]
        full_mask = np.zeros(shape, dtype=bool)
        x0, y0 = src["bbox_xyxy"][0], src["bbox_xyxy"][1]
        sub = np.asarray(comp["mask"], dtype=bool)
        full_mask[y0:y0 + sub.shape[0], x0:x0 + sub.shape[1]] = sub
        result = census.census_component(
            me["state_raster"], van["state_raster"], van["land_mask"],
            full_mask, comp["registration"], band,
            min_relation_px=min_relation_px)
        for row in result["rows"]:
            raw_rows.append((row, comp))
        for sid, totals in result["source_totals"].items():
            acc = source_totals.setdefault(sid, {
                "source_area_px": 0, "source_sampled_px": 0,
                "confident_px": 0, "uncertain_px": 0, "outside_px": 0,
                "water_px": 0, "unassigned_px": 0})
            for key in acc:
                if key == "source_area_px":
                    # whole-state native area is constant across the
                    # components that share a state; keep the max.
                    acc[key] = max(acc[key], int(totals[key]))
                else:
                    acc[key] += int(totals[key])

    merged = aggregate_edges(raw_rows)
    classified = census.classify_relation_matrix(
        [row for row, _ in raw_rows], min_relation_px=min_relation_px)

    note("writing edge table")
    edge_rows = []
    for row in classified:
        key = (row["source_id"], row["target_id"])
        acc = merged[key]
        totals = source_totals.get(key[0], {})
        methods = sorted(acc["methods"])
        statuses = sorted(acc["statuses"])
        edge_rows.append({
            "me_id": key[0],
            "me_name": me_names.get(key[0], ""),
            "me_owner_1936": me_owners.get(key[0], ""),
            "me_component_ids": "|".join(
                str(c) for c in sorted(acc["component_ids"])),
            "vanilla_id": key[1],
            "vanilla_name": v_names.get(key[1], ""),
            "vanilla_owner_1936": v_owners.get(key[1], ""),
            "relation": row["relation"],
            "significant": 1 if row["significant"] else 0,
            "confident_hits_px": row["confident_hits_px"],
            "uncertain_hits_px": row["uncertain_hits_px"],
            "source_area_px": row["source_area_px"],
            "source_sampled_px": row["source_sampled_px"],
            "forward_share": row["forward_share"],
            "target_native_area_px": row["target_native_area_px"],
            "reverse_hits_px": row["reverse_hits_px"],
            "reverse_share": row["reverse_share"],
            "reverse_overlap_possible": row["reverse_overlap_possible"],
            "outside_px": totals.get("outside_px", 0),
            "water_px": totals.get("water_px", 0),
            "unassigned_px": totals.get("unassigned_px", 0),
            "registration_statuses": "|".join(statuses),
            "registration_methods": "|".join(methods),
            "claim": CLAIM_AREA,
            "complete_subset": "unproven",
            "diagnostic_flags": edge_flag_column(
                acc, me_owners.get(key[0], ""),
                v_owners.get(key[1], "")),
            "component_status_ok": not (
                set(acc["statuses"]) & set(EXCLUDED_EDGE_STATUSES)),
        })
    edge_rows.sort(key=lambda r: (r["me_id"], r["vanilla_id"]))

    note("parsing semantic identity inputs")
    comment_path = os.path.join(out_dir, "mapping_comment_edges.tsv")
    comment_rows = []
    if os.path.isfile(comment_path):
        comment_rows = read_comment_edges(comment_path, v_ids, me_ids)
    with open(os.path.join(TOOLS, "build_state_map.py"),
              encoding="utf-8") as fh:
        manual, manual_me2v = parse_manual_dicts(fh.read())
    identity_rows = dedupe_identity(
        comment_rows
        + manual_identity_rows(manual, manual_me2v, v_ids, me_ids)
        + name_identity_rows(v_names, me_names))
    for row in identity_rows:
        row["vanilla_name"] = v_names.get(row["vanilla_id"], "")
        row["me_name"] = me_names.get(row["me_id"], "")

    note("parsing script substitutions")
    focus_path = os.path.join(out_dir, "focus_state_refs.tsv")
    substitution_rows = []
    focus_rows_present = os.path.isfile(focus_path)
    if focus_rows_present:
        substitution_rows += read_focus_substitutions(
            focus_path, v_ids, me_ids)
    substitution_rows += comment_substitution_rows(comment_rows)
    substitution_rows = sort_substitutions(substitution_rows)

    note("deriving compatibility views")
    view_rows = build_view_rows(
        me_ids, me_names, me_owners, edge_rows,
        identity_rows, substitution_rows)
    v2m_rows = build_v2m_rows(v_ids, v_names, v_owners, view_rows)

    note("writing outputs")
    os.makedirs(out_dir, exist_ok=True)
    edge_out = [{k: v for k, v in r.items() if k != "component_status_ok"}
                for r in edge_rows]
    paths = {
        "edges": os.path.join(out_dir, "state_relation_edges.tsv"),
        "identity": os.path.join(out_dir, "state_relation_identity.tsv"),
        "substitution": os.path.join(
            out_dir, "state_relation_substitution.tsv"),
        "views": os.path.join(out_dir, "state_relation_views.tsv"),
        "v2m": os.path.join(out_dir, "state_relation_v2m.tsv"),
        "registration": os.path.join(
            out_dir, "state_relation_registration.json"),
        "summary": os.path.join(out_dir, "state_relation_summary.json"),
    }
    write_tsv(paths["edges"], EDGE_COLUMNS, edge_out)
    write_tsv(paths["identity"], IDENTITY_COLUMNS, identity_rows)
    write_tsv(paths["substitution"], SUBSTITUTION_COLUMNS,
              substitution_rows)
    write_tsv(paths["views"], VIEW_M2V_COLUMNS, view_rows)
    write_tsv(paths["v2m"], VIEW_V2M_COLUMNS, v2m_rows)

    components_out = []
    for comp in comp_results:
        entry = reg.serialisable_component(comp)
        src = me_comp_index[comp["component_id"]]
        entry["source_bbox_xyxy"] = list(src["bbox_xyxy"])
        entry["source_area_px"] = int(src["area_px"])
        entry["source_centroid_xy"] = list(src["centroid_xy"])
        components_out.append(entry)

    input_files = [
        os.path.join(van["map_dir"], "provinces.bmp"),
        os.path.join(van["map_dir"], "definition.csv"),
        os.path.join(me["map_dir"], "provinces.bmp"),
        os.path.join(me["map_dir"], "definition.csv"),
        # rivers.bmp feeds the natural-correction layer (apply_natural);
        # hash it so registration output is stale-detectable. Lake inputs
        # ride on provinces.bmp/definition.csv already listed above.
        os.path.join(van["map_dir"], "rivers.bmp"),
        os.path.join(me["map_dir"], "rivers.bmp"),
        os.path.join(TOOLS, "build_state_map.py"),
        comment_path, focus_path,
    ]
    input_files += glob.glob(os.path.join(
        van["root"], "history", "states", "*.txt"))
    input_files += glob.glob(os.path.join(
        me["root"], "history", "states", "*.txt"))

    registration_payload = {
        "schema": SCHEMA_REGISTRATION,
        "claim": CLAIM_BOUNDARY,
        "inputs": {
            "vanilla_root": _repo_rel(van["root"]),
            "mod_root": _repo_rel(me["root"]),
            "cutoff": list(reg.CUTOFF),
            "files": hash_inputs(input_files),
        },
        "parameters": {
            "border_radius_px": border_radius,
            "min_relation_px": min_relation_px,
            "apply_natural": bool(apply_natural),
        },
        "global": glob_res,
        "components": components_out,
        "notes": [
            "registrations are topology-only affine fits; no VP, name, "
            "comment, or canonical-ancestry input entered any transform",
            "component census rows are sampled pixel overlap; "
            "complete_subset proof is unavailable",
        ],
    }
    write_json(paths["registration"], registration_payload)

    status_counts = {}
    for comp in comp_results:
        status_counts[comp["status"]] = status_counts.get(
            comp["status"], 0) + 1
    relation_counts = {}
    for row in edge_rows:
        relation_counts[row["relation"]] = relation_counts.get(
            row["relation"], 0) + 1
    identity_source_counts = {}
    for row in identity_rows:
        identity_source_counts[row["source"]] = \
            identity_source_counts.get(row["source"], 0) + 1
    view_status_counts = {}
    for row in view_rows:
        view_status_counts[row["status"]] = view_status_counts.get(
            row["status"], 0) + 1

    edge_pairs = {(r["vanilla_id"], r["me_id"]) for r in edge_rows}
    unreachable_identity = sorted(
        {(r["vanilla_id"], r["me_id"]) for r in identity_rows}
        - edge_pairs)
    sub_pairs = {
        (r["vanilla_id"], r["me_id"]) for r in substitution_rows}
    unreachable_substitution = sorted(sub_pairs - edge_pairs)

    outliers = []
    for comp in comp_results:
        metrics = comp.get("metrics") or {}
        reasons = []
        if comp["status"] in EXCLUDED_EDGE_STATUSES:
            reasons.append(comp["status"])
        if metrics.get("fallback_reason"):
            reasons.append("local_fallback")
        if metrics.get("natural_fallback_reason"):
            reasons.append("natural_fallback")
        res = metrics.get("local_residual_p95_px")
        if res is not None and res > HIGH_RESIDUAL_P95_PX:
            reasons.append("high_residual_p95_px=%.3f" % res)
        if reasons:
            outliers.append({
                "owner": comp["owner"],
                "component_id": comp["component_id"],
                "target_component_id": comp["target_component_id"],
                "status": comp["status"],
                "flags": sorted(set(reasons)),
            })
    outliers.sort(key=lambda o: (o["owner"], o["component_id"]))

    cross_owner_pairs = sorted({
        (r["me_id"], r["vanilla_id"], r["me_owner_1936"],
         r["vanilla_owner_1936"])
        for r in edge_rows
        if r["me_owner_1936"] and r["vanilla_owner_1936"]
        and r["me_owner_1936"] != r["vanilla_owner_1936"]})

    summary_payload = {
        "schema": SCHEMA_SUMMARY,
        "claims": {
            "area_overlap": CLAIM_AREA,
            "identity": CLAIM_IDENTITY,
            "substitution": CLAIM_SUBSTITUTION,
            "derived_view": CLAIM_VIEW,
            "complete_subset_proof": "unavailable",
        },
        "counts": {
            "me_states": len(me_ids),
            "vanilla_states": len(v_ids),
            "components": len(comp_results),
            "component_status": dict(sorted(status_counts.items())),
            "edge_rows": len(edge_rows),
            "edge_significant": sum(
                1 for r in edge_rows if r["significant"]),
            "relation_classes": dict(sorted(relation_counts.items())),
            "identity_rows": len(identity_rows),
            "identity_pairs": len(
                {(r["vanilla_id"], r["me_id"]) for r in identity_rows}),
            "identity_by_source": dict(sorted(
                identity_source_counts.items())),
            "substitution_rows": len(substitution_rows),
            "substitution_pairs": len(sub_pairs),
            "substitution_focus_rows": sum(
                1 for r in substitution_rows
                if r["source"] in ("focus_script", "focus_comment_claim")),
            "view_rows": len(view_rows),
            "view_status": dict(sorted(view_status_counts.items())),
            "view_derived": sum(
                1 for r in view_rows if r["derived_v_ancestor"]),
            "v2m_rows": len(v2m_rows),
            "v2m_derived": sum(
                1 for r in v2m_rows if r["derived_members"]),
            "focus_state_refs_present": int(focus_rows_present),
            "comment_edge_rows": len(comment_rows),
            "cross_owner_edge_rows": len(cross_owner_pairs),
        },
        "diagnostics": {
            "unreachable_identity_pairs": [
                "%d:%d" % pair for pair in unreachable_identity],
            "unreachable_substitution_pairs": [
                "%d:%d" % pair for pair in unreachable_substitution],
            "component_outliers": outliers,
            "cross_owner_pairs": [
                {"me_id": m, "vanilla_id": v,
                 "me_owner_1936": mo, "vanilla_owner_1936": vo}
                for m, v, mo, vo in cross_owner_pairs],
            "global_refinement_status":
                (glob_res.get("metrics") or {}).get("refinement_status"),
        },
        "outputs": {name: _repo_rel(path)
                    for name, path in sorted(paths.items())},
        "notes": [
            "reverse_share counts unique confident target pixels per "
            "component cell; merged across components it is capped at "
            "1.0 and flagged reverse_overlap_possible, still a sampled "
            "coverage estimate -- never a proof",
            "no row in any output asserts a proven or complete-subset "
            "relation; complete_subset_proof is explicitly unavailable",
            "identity candidates come from comments/manual tables/name "
            "matches only -- never from raster fitting",
        ],
    }
    write_json(paths["summary"], summary_payload)
    note("done")
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the state-relation map outputs.")
    parser.add_argument("--vanilla", default=HOI4,
                        help="vanilla game dir (env HOI4_DIR)")
    parser.add_argument("--mod", default=None,
                        help="mod dir (env ME_MOD or mods/* auto)")
    parser.add_argument("--output-dir",
                        default=os.path.join(repo_root(), "docs", "states"),
                        help="output directory")
    parser.add_argument("--no-natural", action="store_true",
                        help="skip the natural-feature drift correction")
    parser.add_argument("--border-radius", type=int, default=2,
                        help="vanilla border-band half-width in px")
    parser.add_argument("--min-relation-px", type=int, default=64,
                        help="min confident px for a significant edge")
    args = parser.parse_args(argv)
    if args.border_radius < 0:
        parser.error("--border-radius must be >= 0")
    if args.min_relation_px < 1:
        parser.error("--min-relation-px must be >= 1")
    mod = args.mod or mod_root()
    paths = build(args.vanilla, mod, args.output_dir,
                  apply_natural=not args.no_natural,
                  border_radius=args.border_radius,
                  min_relation_px=args.min_relation_px,
                  progress=lambda m: print("[relation-map]", m))
    for name in ("edges", "identity", "substitution", "views", "v2m",
                 "registration", "summary"):
        print("%s: %s" % (name, paths[name]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
