#!/usr/bin/env python
"""Read-only confirmation overlay over the existing vanilla<->ME state tables.

Consumes the canonical m2v/v2m partition, the 1936 mask audit pair ledger,
the 1936 owner-constrained edge ledger (state_edges_1936.tsv), the mapping
audit verdicts, the explicit comment-edge vote table and every project
focus state reference. Emits, per (me_id, vanilla_id) pair, a conservative
confidence score and a 95% confirmation gate:

  confidence_score = min(evidence_cap, me_owner_share,
                         owner_family_reverse_share)

Missing metrics count as 0. `confirmed_95` is pair-level only and only
ever produced for the canonical one-to-many family relation, and only
when every gate passes:

  canonical pair, non-synthetic ME state, same_owner relation,
  an edges-ledger row with usable owner denominators
  (me_owner_area_px > 0 and v_owner_area_px > 0),
  explicit identity evidence (evidence_cap == 1.0),
  me_owner_share / owner_family_reverse_share both >= 0.95,
  no identity-risk audit reason, no comment edge citing a different
  ancestor, and no synthetic member in either family.

The owner relation family is the set of same_owner edges rows whose
source_scope is compatibility or asserted (canonical rows always join);
observation-scope edges never expand it. `owner_family_reverse_share`
is the family union share of the vanilla state's observed same-owner ME
mass (v_owner_area_px): it measures coverage of what the census saw
inside the owner's border, NOT a complete-subset proof.

The old primary-family metrics (family_reverse_share, family_min_me_share)
and the mask-geometry columns stay as canonical-family diagnostics; they
never gate. context_only/clear_correction audit rows and a comment(member)
reason are non-blocking context; only identity-risk audit reasons block.

Non-canonical candidate pairs may reach high scores but stay
`review_candidate`; synthetic ME states (owner MAP / "Map Border") are
`excluded_synthetic`. Owner, region, focus coverage and names gate or
annotate only - they are never identity proof.

Deterministic output: UTF-8, LF line endings, six-decimal ratios, fixed
reason-code order, no timestamps. Fails closed on malformed input,
duplicate ids/edges, missing canonical pairs, canonical-family drift
between m2v_states.tsv, v2m_states.tsv and state_mask_audit_1936.tsv, or
owner-metric drift/inconsistency inside state_edges_1936.tsv.

Usage:
  python tools/build_state_mapping_confirmation.py \
      [--tables PATH] [--mask-audit PATH] [--edges PATH] \
      [--mapping-audit PATH] [--comments PATH] [--focus-refs PATH] \
      [--output PATH] [--review-queue PATH] [--refs-output PATH] \
      [--summary-json PATH]

`--tables` accepts either m2v_states.tsv directly or the docs/states
directory containing it.
"""
import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _modroot import repo_root  # noqa: E402

TABLES = Path(repo_root()) / "docs" / "states"
DEFAULT_TABLES = TABLES / "m2v_states.tsv"
DEFAULT_V2M = TABLES / "v2m_states.tsv"
DEFAULT_MASK_AUDIT = TABLES / "state_mask_audit_1936.tsv"
DEFAULT_MAPPING_AUDIT = TABLES / "mapping_audit.tsv"
DEFAULT_COMMENTS = TABLES / "mapping_comment_edges.tsv"
DEFAULT_FOCUS_REFS = TABLES / "focus_state_refs.tsv"
DEFAULT_EDGES = TABLES / "state_edges_1936.tsv"
DEFAULT_OUTPUT = TABLES / "state_mapping_confirmation.tsv"
DEFAULT_REVIEW_QUEUE = TABLES / "state_mapping_review_queue_95.tsv"
DEFAULT_REFS_OUTPUT = TABLES / "state_mapping_confirmation_refs.tsv"
DEFAULT_SUMMARY = TABLES / "state_mapping_confirmation_summary.json"

SUMMARY_SCHEMA = "state_mapping_confirmation_summary/v2"
KIND = ("read-only mapping confirmation overlay; 1936 owner-constrained "
        "conservative gate thresholds, not calibrated probabilities and "
        "not a complete-subset proof")

CONFIRMATION_THRESHOLD = 0.95
CAP_EXPLICIT = 1.00       # manual/comment basis, votes, or manual_kind
CAP_PIXEL_VP = 0.80       # pixel/vp evidence only
CAP_NAME = 0.65           # name evidence only
CAP_FALLBACK = 0.50       # no usable identity evidence at all

STATUS_CONFIRMED = "confirmed_95"
STATUS_REVIEW = "review_candidate"
STATUS_EXCLUDED = "excluded_synthetic"

KIND_CANONICAL = "canonical"
KIND_CANDIDATE = "candidate"

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_EXCLUDED = "excluded"

REF_CONFIRMED = "confirmed_target"
REF_REVIEW = "review_target"
REF_EXCLUDED = "excluded_target"
REF_UNRESOLVED = "unresolved"

EXPLICIT_BASES = frozenset({"manual", "comment", "comment(member)"})

# Audit reason codes that never veto confirmation by themselves: the
# audit tool's SOFT_REASON_CODES, comment(member) membership context and
# resolved explicit corrections. Everything else is identity risk and
# blocks confirmed_95 (mirrors audit_state_mapping's risk/soft ladder).
NONBLOCKING_AUDIT_CODES = frozenset({
    "vp-absence-gap", "region-contradiction", "raw-geography-weak",
    "overlap-weak", "comment(member)", "explicit-independent-correction",
})

# source_scope values that join the same-owner relation family; canonical
# rows always join, observation-scope rows never do.
OWNER_FAMILY_SCOPES = frozenset({"compatibility", "asserted"})

# Fixed emission order; a row reports every applicable reason.
REASON_CODES = (
    "synthetic_state",
    "candidate_not_canonical",
    "missing_mask_evidence",
    "geometry_not_usable",
    "missing_owner_edge",
    "me_share_below_95",
    "me_owner_share_below_95",
    "family_reverse_share_below_95",
    "family_member_below_95",
    "owner_family_reverse_share_below_95",
    "owner_denominator_unusable",
    "owner_mismatch",
    "owner_unknown",
    "mapping_audit_review_required",
    "canonical_comment_conflict",
    "no_explicit_identity_evidence",
    "family_has_synthetic_member",
    "owner_family_has_synthetic_member",
)

# Review queue priority tiers (lower first): canonical pairs with explicit
# identity evidence first, then canonical without, then asserted
# non-canonical candidates, then everything else.
PRIORITY_CANONICAL_EXPLICIT = 0
PRIORITY_CANONICAL_OTHER = 1
PRIORITY_CANDIDATE_EXPLICIT = 2
PRIORITY_CANDIDATE_OTHER = 3

PAIR_COLUMNS = [
    "me_id", "me_name", "me_owner", "vanilla_id", "vanilla_name",
    "vanilla_owner", "relation_kind", "relation_status", "source_scope",
    "edge_source", "evidence_families", "is_canonical", "canonical_basis",
    "canonical_status", "owner_relation", "country_scope", "edge_class",
    "me_share", "me_registered_share", "me_geometry_usable",
    "v_hit_px", "v_native_px", "v_share_native",
    "family_member_count", "family_me_ids", "family_live_member_count",
    "family_has_synthetic_member", "family_min_me_share",
    "family_reverse_hit_px", "family_reverse_native_px",
    "family_reverse_share",
    "me_owner_area_px", "me_owner_match_px", "me_owner_share",
    "v_owner_area_px", "v_owner_match_px", "v_owner_share",
    "owner_family_member_count", "owner_family_me_ids",
    "owner_family_live_member_count",
    "owner_family_has_synthetic_member",
    "owner_family_min_me_owner_share",
    "owner_family_reverse_match_px", "owner_family_reverse_share",
    "mapping_audit_class",
    "mapping_audit_reason_codes", "comment_votes",
    "explicit_identity_evidence", "evidence_cap", "confidence_score",
    "confidence_gate", "confidence_reasons",
    "project_ref_count", "project_review_ref_count",
]

REVIEW_COLUMNS = PAIR_COLUMNS + ["review_priority"]

REF_APPEND_COLUMNS = [
    "resolved_me_id", "resolved_vanilla_id", "relation_status",
    "confidence_score", "reference_status", "review_reason",
]

MASK_COLUMNS = [
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

# state_edges_1936.tsv: the 1936 owner-constrained edge ledger supplying
# every owner-scoped metric (loaded fail-closed by load_owner_edges).
EDGE_COLUMNS = [
    "vanilla_id", "vanilla_name", "vanilla_owner",
    "me_id", "me_name", "me_owner",
    "owner_relation", "candidate_scope", "source_scope",
    "edge_source", "evidence_families", "is_canonical",
    "canonical_ancestor", "edge_class", "comment_votes", "manual_kind",
    "me_area_px", "me_match_px", "me_share",
    "me_owner_area_px", "me_owner_match_px", "me_owner_share",
    "me_rank_global", "me_rank_owner",
    "v_native_area_px", "v_observable_px", "v_match_px", "v_share",
    "v_owner_area_px", "v_owner_match_px", "v_owner_share",
    "v_rank_global", "v_rank_owner",
]


def fail(msg):
    raise SystemExit("state_mapping_confirmation: " + str(msg))


def _num(value):
    """Six-decimal fixed format; blank stays blank."""
    if value is None or value == "":
        return ""
    return f"{0.0 if abs(value) < 0.0000005 else value:.6f}"


def _int(text, where):
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        fail(f"{where}: malformed integer {text!r}")


def _float(text, where):
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        fail(f"{where}: malformed number {text!r}")
    if not math.isfinite(value):
        fail(f"{where}: non-finite number {text!r}")
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_tsv(path, required):
    """Read a TSV table fail-closed: header must contain `required`."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames is None:
                fail(f"{path.name}: empty file")
            missing = [c for c in required if c not in reader.fieldnames]
            if missing:
                fail(f"{path.name}: missing columns {missing}")
            return reader.fieldnames, list(reader)
    except OSError as error:
        fail(f"{path.name}: unreadable: {error}")


def is_synthetic(me_owner, me_name):
    """Synthetic ME state: MAP owner or a 'Map Border' name. Never a member."""
    return me_owner == "MAP" or "map border" in (me_name or "").casefold()


def evidence_cap(row, canonical_basis):
    """Identity-evidence ceiling -> (cap, explicit_evidence_string).

    1.0 needs an explicit human channel: manual/comment basis on the
    canonical edge, nonzero comment votes, manual_kind, or comment/manual
    evidence families on the pair. Pixel/vp caps at 0.80 (same dependency
    family), name at 0.65, everything else at 0.50.
    """
    tokens = []
    explicit = False
    if canonical_basis in EXPLICIT_BASES:
        explicit = True
        tokens.append(f"basis:{canonical_basis}")
    if row.get("comment_votes") not in (None, "", "0"):
        explicit = True
        tokens.append(f"comment_votes:{row['comment_votes']}")
    if row.get("manual_kind"):
        explicit = True
        tokens.append(f"manual_kind:{row['manual_kind']}")
    families = set((row.get("evidence_families") or "").split("|"))
    for family in ("comment", "manual"):
        if family in families:
            explicit = True
            tokens.append(f"evidence:{family}")
    if explicit:
        return CAP_EXPLICIT, "|".join(tokens)
    if families & {"pixel", "vp"} or \
            (canonical_basis or "").startswith(("pixel", "vp")):
        return CAP_PIXEL_VP, ""
    if families & {"name", "same_id"} or \
            (canonical_basis or "").startswith("name"):
        return CAP_NAME, ""
    return CAP_FALLBACK, ""


def family_stats(members):
    """Reverse-union coverage for one vanilla family -> dict of metrics.

    `members` is an iterable of dicts {me_id, synthetic, me_share, v_hit_px};
    me_share/v_hit_px are None when the mask census has no value. Synthetic
    members are reported but never counted: reverse census hits are mutually
    exclusive per vanilla pixel, so live-member v_hit_px sums are the union.
    """
    member_ids = [m["me_id"] for m in members]
    synthetic = sum(1 for m in members if m["synthetic"])
    live = [m for m in members if not m["synthetic"]]
    reverse_hit = sum(m["v_hit_px"] or 0 for m in live)
    shares = [m["me_share"] for m in live]
    min_share = min(shares) if live and None not in shares else None
    return {
        "member_count": len(member_ids),
        "member_ids": member_ids,
        "live_count": len(live),
        "has_synthetic": synthetic > 0,
        "reverse_hit_px": reverse_hit,
        "min_me_share": min_share,
    }


def confidence_score(cap, me_owner_share, owner_family_reverse_share):
    """min(cap, me_owner_share, owner_family_reverse_share); missing -> 0."""
    return min(
        cap,
        me_owner_share if me_owner_share is not None else 0.0,
        (owner_family_reverse_share
         if owner_family_reverse_share is not None else 0.0),
    )


def audit_blocking_codes(audit_row):
    """Sorted identity-risk audit reasons for one ME row.

    context_only / clear_correction / no_action rows carry no blockers;
    within review_required, the NONBLOCKING_AUDIT_CODES context signals
    (soft reasons, comment(member), resolved corrections) never veto —
    every remaining reason is an identity risk and blocks confirmed_95.
    """
    if not audit_row:
        return []
    return sorted(code for code in
                  (audit_row.get("reason_codes") or "").split("|")
                  if code and code not in NONBLOCKING_AUDIT_CODES)


def owner_family_stats(members):
    """Same-owner relation-family union metrics for one vanilla state.

    `members` is an iterable of dicts {me_id, synthetic, me_owner_share,
    v_owner_match_px} covering the vanilla state's same_owner
    compatibility/asserted edges (observation rows never join). Synthetic
    members are reported but never counted; reverse hits are mutually
    exclusive per vanilla pixel, so live-member match sums are the union.
    The union covers only observed same-owner mass — coverage of a
    constrained census, never a complete-subset proof.
    """
    member_ids = [m["me_id"] for m in members]
    synthetic = sum(1 for m in members if m["synthetic"])
    live = [m for m in members if not m["synthetic"]]
    reverse_match = sum(m["v_owner_match_px"] or 0 for m in live)
    shares = [m["me_owner_share"] for m in live]
    min_share = min(shares) if live and None not in shares else None
    return {
        "member_count": len(member_ids),
        "member_ids": member_ids,
        "live_count": len(live),
        "has_synthetic": synthetic > 0,
        "reverse_match_px": reverse_match,
        "min_me_owner_share": min_share,
    }


def reason_codes(flags):
    """Ordered reason-code string from a {code: bool} mapping."""
    return "|".join(code for code in REASON_CODES if flags.get(code))


def review_priority(is_canonical, cap):
    """Deterministic review-queue tier."""
    if is_canonical:
        return (PRIORITY_CANONICAL_EXPLICIT if cap >= CAP_EXPLICIT
                else PRIORITY_CANONICAL_OTHER)
    return (PRIORITY_CANDIDATE_EXPLICIT if cap >= CAP_EXPLICIT
            else PRIORITY_CANDIDATE_OTHER)


def reference_status(statuses):
    """Aggregate one focus reference over its resolved pair statuses."""
    live = [s for s in statuses if s]
    if not live:
        return REF_UNRESOLVED
    if any(s == STATUS_EXCLUDED for s in live):
        return REF_EXCLUDED
    if all(s == STATUS_CONFIRMED for s in live):
        return REF_CONFIRMED
    return REF_REVIEW


def load_canonical(m2v_path, v2m_path):
    """Canonical partition + synthetic flags; fail closed on drift."""
    _, m2v_rows = _read_tsv(
        m2v_path, ["me_id", "me_name", "owner", "v_ancestor", "basis",
                   "status"])
    _, v2m_rows = _read_tsv(
        v2m_path, ["vanilla_id", "vanilla_name", "owner", "me_primary",
                   "me_members", "basis", "status"])

    m2v = {}
    for row in m2v_rows:
        mid = _int(row["me_id"], "m2v_states.tsv me_id")
        anc = _int(row["v_ancestor"], "m2v_states.tsv v_ancestor")
        if mid is None or anc is None:
            fail(f"m2v_states.tsv: blank id pair {row!r}")
        if mid in m2v:
            fail(f"m2v_states.tsv: duplicate me_id {mid}")
        m2v[mid] = {
            "name": row["me_name"], "owner": row["owner"],
            "ancestor": anc, "basis": row["basis"],
            "status": row["status"],
            "synthetic": is_synthetic(row["owner"], row["me_name"]),
        }

    v2m = {}
    for row in v2m_rows:
        vid = _int(row["vanilla_id"], "v2m_states.tsv vanilla_id")
        if vid is None:
            fail(f"v2m_states.tsv: blank vanilla_id {row!r}")
        if vid in v2m:
            fail(f"v2m_states.tsv: duplicate vanilla_id {vid}")
        members = [_int(x, "v2m_states.tsv me_members")
                   for x in (row["me_members"].split("|")
                             if row["me_members"] else [])]
        if None in members:
            fail(f"v2m_states.tsv {vid}: malformed me_members "
                 f"{row['me_members']!r}")
        if len(set(members)) != len(members):
            fail(f"v2m_states.tsv {vid}: duplicate me_members "
                 f"{row['me_members']!r}")
        primary = _int(row["me_primary"], "v2m_states.tsv me_primary")
        v2m[vid] = {
            "name": row["vanilla_name"], "owner": row["owner"],
            "members": members, "primary": primary,
            "basis": row["basis"], "status": row["status"],
        }

    # canonical-family consistency: the two tables must mirror each other.
    for mid, rec in m2v.items():
        anc = rec["ancestor"]
        if anc not in v2m:
            fail(f"m2v {mid} -> {anc}: ancestor missing from v2m_states")
        if mid not in v2m[anc]["members"]:
            fail(f"m2v {mid} -> {anc}: not listed in v2m me_members")
    for vid, rec in v2m.items():
        for mid in rec["members"]:
            if mid not in m2v:
                fail(f"v2m {vid}: member {mid} missing from m2v_states")
            if m2v[mid]["ancestor"] != vid:
                fail(f"v2m {vid}: member {mid} claims ancestor "
                     f"{m2v[mid]['ancestor']}")
        if rec["members"] and rec["primary"] not in rec["members"]:
            fail(f"v2m {vid}: me_primary {rec['primary']} not in me_members")
    return m2v, v2m


def load_mask_pairs(path):
    """Pair ledger keyed by (me_id, vanilla_id) with parsed numerics."""
    _, rows = _read_tsv(path, MASK_COLUMNS)
    pairs = {}
    for i, row in enumerate(rows, 2):
        where = f"{Path(path).name}:{i}"
        mid = _int(row["me_id"], where + " me_id")
        vid = _int(row["vanilla_id"], where + " vanilla_id")
        if mid is None or vid is None:
            fail(f"{where}: blank pair ids")
        key = (mid, vid)
        if key in pairs:
            fail(f"{where}: duplicate pair {key}")
        pairs[key] = {
            "row": row,
            "me_share": _float(row["me_share"], where + " me_share"),
            "v_hit_px": _int(row["v_hit_px"], where + " v_hit_px"),
            "v_native_px": _int(row["v_native_px"], where + " v_native_px"),
        }
    return pairs


def _nonneg_int(text, where):
    """Non-negative integer or None; negative values fail closed."""
    value = _int(text, where)
    if value is not None and value < 0:
        fail(f"{where}: negative pixel count {text!r}")
    return value


def _check_share(row, where, share_col, same, match, area):
    """Owner-share arithmetic: match/area iff same_owner and area > 0."""
    if same and area:
        if not row[share_col]:
            fail(f"{where}: {share_col} blank with usable denominator")
        return _float(row[share_col], f"{where} {share_col}")
    if row[share_col]:
        fail(f"{where}: {share_col} set without a usable denominator")
    return None


def load_owner_edges(path, m2v, v2m):
    """Strict state_edges_1936.tsv load -> {(me_id, vanilla_id): edge}.

    Fails closed on: missing columns, blank/malformed ids or owners,
    duplicate pairs, unknown endpoints, unknown vocabulary, negative or
    inconsistent owner metrics (match > area, a share not matching its
    explicit denominator, owner_match != match on same_owner rows or != 0
    elsewhere), or any drift of the per-entity owner denominators.
    """
    _, rows = _read_tsv(path, EDGE_COLUMNS)
    edges = {}
    me_owner_area = {}
    v_owner_area = {}
    for i, row in enumerate(rows, 2):
        where = f"{Path(path).name}:{i}"
        mid = _int(row["me_id"], where + " me_id")
        vid = _int(row["vanilla_id"], where + " vanilla_id")
        if mid is None or vid is None:
            fail(f"{where}: blank pair ids")
        if mid not in m2v:
            fail(f"{where}: me_id {mid} unknown to m2v_states")
        if vid not in v2m:
            fail(f"{where}: vanilla_id {vid} unknown to v2m_states")
        key = (mid, vid)
        if key in edges:
            fail(f"{where}: duplicate pair {key}")
        ancestor = m2v[mid]["ancestor"]
        if (row["is_canonical"] == "1") != (ancestor == vid):
            fail(f"{where}: is_canonical flag contradicts m2v "
                 f"ancestor {ancestor}")
        if ancestor == vid and row["canonical_ancestor"] not in \
                ("", str(ancestor)):
            fail(f"{where}: canonical_ancestor drift "
                 f"{row['canonical_ancestor']!r} != {ancestor}")
        if row["me_name"] and m2v[mid]["name"] \
                and row["me_name"] != m2v[mid]["name"]:
            fail(f"{where}: me_name drift {row['me_name']!r} "
                 f"!= {m2v[mid]['name']!r}")
        if row["vanilla_name"] and v2m[vid]["name"] \
                and row["vanilla_name"] != v2m[vid]["name"]:
            fail(f"{where}: vanilla_name drift")
        relation = row["owner_relation"]
        if relation not in ("same_owner", "cross_owner", "unknown_owner"):
            fail(f"{where}: unknown owner_relation {relation!r}")
        same = relation == "same_owner"
        if same and row["is_canonical"] == "1" \
                and row["source_scope"] not in OWNER_FAMILY_SCOPES:
            fail(f"{where}: same_owner canonical row carries "
                 f"non-family source_scope {row['source_scope']!r}")

        me_area = _nonneg_int(row["me_area_px"], where + " me_area_px")
        me_match = _nonneg_int(row["me_match_px"], where + " me_match_px")
        mo_area = _nonneg_int(
            row["me_owner_area_px"], where + " me_owner_area_px")
        mo_match = _nonneg_int(
            row["me_owner_match_px"], where + " me_owner_match_px")
        v_native = _nonneg_int(
            row["v_native_area_px"], where + " v_native_area_px")
        v_obs = _nonneg_int(
            row["v_observable_px"], where + " v_observable_px")
        v_match = _nonneg_int(row["v_match_px"], where + " v_match_px")
        vo_area = _nonneg_int(
            row["v_owner_area_px"], where + " v_owner_area_px")
        vo_match = _nonneg_int(
            row["v_owner_match_px"], where + " v_owner_match_px")

        me_share = _check_share(row, where, "me_owner_share", same,
                                mo_match, mo_area)
        v_share = _check_share(row, where, "v_owner_share", same,
                               vo_match, vo_area)
        for share, col, match, area in (
                (me_share, "me_owner_share", mo_match, mo_area),
                (v_share, "v_owner_share", vo_match, vo_area)):
            if share is None:
                continue
            if not 0.0 <= share <= 1.0:
                fail(f"{where}: {col} {share!r} outside [0, 1]")
            if match is not None and area \
                    and abs(share - match / area) > 0.000001:
                fail(f"{where}: {col} {share!r} != {match}/{area}")

        # owner-scoped accounting identities — never renormalise.
        if mo_area is not None and me_area is not None \
                and mo_area > me_area:
            fail(f"{where}: me_owner_area_px exceeds me_area_px")
        if mo_match is not None and mo_area is not None \
                and mo_match > mo_area:
            fail(f"{where}: me_owner_match_px exceeds me_owner_area_px")
        if mo_match is not None and me_match is not None \
                and mo_match > me_match:
            fail(f"{where}: me_owner_match_px exceeds me_match_px")
        if same and mo_match not in (None, me_match):
            fail(f"{where}: me_owner_match_px != me_match_px on "
                 "a same_owner row")
        if not same and mo_match not in (None, 0):
            fail(f"{where}: nonzero me_owner_match_px on a "
                 f"{relation} row")
        if vo_area is not None and v_obs is not None \
                and vo_area > v_obs:
            fail(f"{where}: v_owner_area_px exceeds v_observable_px")
        if vo_match is not None and vo_area is not None \
                and vo_match > vo_area:
            fail(f"{where}: v_owner_match_px exceeds v_owner_area_px")
        if vo_match is not None and v_match is not None \
                and vo_match > v_match:
            fail(f"{where}: v_owner_match_px exceeds v_match_px")
        if same and vo_match not in (None, v_match):
            fail(f"{where}: v_owner_match_px != v_match_px on "
                 "a same_owner row")
        if not same and vo_match not in (None, 0):
            fail(f"{where}: nonzero v_owner_match_px on a "
                 f"{relation} row")

        # Entity denominators are census facts: identical across rows.
        if mo_area is not None:
            if me_owner_area.setdefault(mid, mo_area) != mo_area:
                fail(f"{where}: me_owner_area_px for {mid} "
                     "contradicts an earlier row")
        if vo_area is not None:
            if v_owner_area.setdefault(vid, vo_area) != vo_area:
                fail(f"{where}: v_owner_area_px for {vid} "
                     "contradicts an earlier row")

        edges[key] = {
            "row": row,
            "same_owner": same,
            "family_member": same and
            row["source_scope"] in OWNER_FAMILY_SCOPES,
            "me_owner_area_px": mo_area,
            "me_owner_match_px": mo_match,
            "me_owner_share": me_share,
            "v_owner_area_px": vo_area,
            "v_owner_match_px": vo_match,
            "v_owner_share": v_share,
        }
    return edges


def load_comment_votes(path):
    """Explicit pair-level comment votes -> {(me_id, vanilla_id): votes}."""
    _, rows = _read_tsv(path, ["vanilla_id", "me_id", "comment_votes"])
    votes = {}
    for i, row in enumerate(rows, 2):
        where = f"{Path(path).name}:{i}"
        mid = _int(row["me_id"], where + " me_id")
        vid = _int(row["vanilla_id"], where + " vanilla_id")
        count = _int(row["comment_votes"], where + " comment_votes")
        if mid is None or vid is None or count is None:
            fail(f"{where}: malformed comment edge")
        if (mid, vid) in votes:
            fail(f"{where}: duplicate comment edge ({mid}, {vid})")
        votes[(mid, vid)] = count
    return votes


def load_audit_classes(path, m2v):
    """me_id -> (class, reason_codes); every canonical ME must be audited."""
    _, rows = _read_tsv(path, ["me_id", "class", "reason_codes",
                               "canonical_ancestor"])
    audit = {}
    for i, row in enumerate(rows, 2):
        where = f"{Path(path).name}:{i}"
        mid = _int(row["me_id"], where + " me_id")
        if mid is None:
            fail(f"{where}: blank me_id")
        if mid in audit:
            fail(f"{where}: duplicate me_id {mid}")
        audit[mid] = row
    for mid, rec in m2v.items():
        if mid not in audit:
            fail(f"mapping_audit.tsv: canonical ME {mid} missing")
        anc = _int(audit[mid]["canonical_ancestor"],
                   f"mapping_audit.tsv {mid} canonical_ancestor")
        if anc != rec["ancestor"]:
            fail(f"mapping_audit.tsv {mid}: canonical_ancestor {anc} "
                 f"!= m2v ancestor {rec['ancestor']}")
    return audit


def build_pairs(m2v, v2m, pairs, edges, votes, audit, refs_by_pair):
    """One confirmation row per mask-audit pair, sorted (me, vanilla).

    Returns (rows, family, owner_family): canonical-family diagnostics
    plus the same-owner asserted/compatibility relation families that
    drive the 0.95 owner gates.
    """
    # comment edges disagreeing with the adjudicated ancestor: mid-level.
    comment_conflict = defaultdict(set)
    for (mid, vid) in votes:
        comment_conflict[mid].add(vid)
    comment_conflict = {mid: vids - {m2v[mid]["ancestor"]}
                        for mid, vids in comment_conflict.items()
                        if mid in m2v and vids - {m2v[mid]["ancestor"]}}

    # pre-compute family metrics per vanilla over canonical members.
    family = {}
    for vid, rec in v2m.items():
        members = []
        for mid in rec["members"]:
            pair = pairs.get((mid, vid))
            if pair is None:
                fail(f"mask audit missing canonical pair ({mid}, {vid})")
            members.append({
                "me_id": mid,
                "synthetic": m2v[mid]["synthetic"],
                "me_share": pair["me_share"],
                "v_hit_px": pair["v_hit_px"],
            })
        family[vid] = family_stats(members)

    # owner relation families per vanilla: same_owner compatibility /
    # asserted edges only (canonical rows join, observation never does).
    owner_family = {}
    for (mid, vid), edge in sorted(edges.items()):
        if not edge["family_member"]:
            continue
        if (mid, vid) not in pairs:
            fail(f"mask audit missing owner-family pair ({mid}, {vid})")
        owner_family.setdefault(vid, []).append({
            "me_id": mid,
            "synthetic": m2v[mid]["synthetic"],
            "me_owner_share": edge["me_owner_share"],
            "v_owner_match_px": edge["v_owner_match_px"],
        })
    v_owner_area = {}
    for (mid, vid), edge in edges.items():
        if edge["v_owner_area_px"] is not None:
            v_owner_area[vid] = edge["v_owner_area_px"]
    for vid, members in owner_family.items():
        stats = owner_family_stats(members)
        area = v_owner_area.get(vid)
        stats["reverse_share"] = (
            stats["reverse_match_px"] / area if area else None)
        if stats["reverse_share"] is not None \
                and stats["reverse_share"] > 1.0 + 0.000001:
            fail(f"owner family union ({stats['reverse_match_px']} px) "
                 f"exceeds v_owner_area_px ({area}) for vanilla {vid}")
        owner_family[vid] = stats

    rows = []
    for (mid, vid), pair in sorted(pairs.items()):
        src = pair["row"]
        if mid not in m2v:
            fail(f"mask pair ({mid}, {vid}): me_id not in m2v_states")
        if vid not in v2m:
            fail(f"mask pair ({mid}, {vid}): vanilla_id not in v2m_states")
        me = m2v[mid]
        anc = me["ancestor"]
        canonical = anc == vid
        if (src["is_canonical"] == "1") != canonical:
            fail(f"mask pair ({mid}, {vid}): is_canonical flag "
                 f"contradicts m2v ancestor {anc}")
        if canonical and src["canonical_ancestor"] not in ("", str(anc)):
            fail(f"mask pair ({mid}, {vid}): canonical_ancestor drift")
        if src["me_name"] and me["name"] and src["me_name"] != me["name"]:
            fail(f"mask pair ({mid}, {vid}): me_name drift "
                 f"{src['me_name']!r} != {me['name']!r}")
        if src["vanilla_name"] and v2m[vid]["name"] and \
                src["vanilla_name"] != v2m[vid]["name"]:
            fail(f"mask pair ({mid}, {vid}): vanilla_name drift")
        # the owner-constrained ledger mirrors owner/canonical facts.
        edge = edges.get((mid, vid))
        if edge is not None:
            erow = edge["row"]
            if erow["owner_relation"] != src["owner_relation"]:
                fail(f"mask pair ({mid}, {vid}): owner_relation drift "
                     f"{src['owner_relation']!r} != "
                     f"{erow['owner_relation']!r}")
            if erow["me_owner"] != src["me_owner"] \
                    or erow["vanilla_owner"] != src["vanilla_owner"]:
                fail(f"mask pair ({mid}, {vid}): owner tag drift")

        synthetic = me["synthetic"]
        fam = family[vid]
        ofam = owner_family.get(vid) or {
            "member_count": 0, "member_ids": [], "live_count": 0,
            "has_synthetic": False, "reverse_match_px": 0,
            "min_me_owner_share": None, "reverse_share": None}
        reverse_native = pair["v_native_px"]
        reverse_share = (fam["reverse_hit_px"] / reverse_native
                         if fam["live_count"] and reverse_native
                         else (0.0 if reverse_native is not None else None))
        cap, explicit = evidence_cap(src, me["basis"] if canonical else "")
        me_owner_share = edge["me_owner_share"] if edge else None
        owner_reverse_share = ofam["reverse_share"]
        score = confidence_score(cap, me_owner_share, owner_reverse_share)
        audit_row = audit[mid]
        blocking = audit_blocking_codes(audit_row)
        denominator_usable = edge is not None \
            and (edge["me_owner_area_px"] or 0) > 0 \
            and (edge["v_owner_area_px"] or 0) > 0

        flags = {
            "synthetic_state": synthetic,
            "candidate_not_canonical": not canonical,
            "missing_mask_evidence": (
                pair["me_share"] is None or pair["v_hit_px"] is None
                or reverse_native is None
                or src["geometry_usable"] == ""),
            "geometry_not_usable": src["geometry_usable"] == "0",
            "missing_owner_edge": edge is None,
            "me_share_below_95": pair["me_share"] is not None
                and pair["me_share"] < CONFIRMATION_THRESHOLD,
            "me_owner_share_below_95": me_owner_share is not None
                and me_owner_share < CONFIRMATION_THRESHOLD,
            "family_reverse_share_below_95":
                fam["live_count"] > 0 and reverse_native is not None
                and (reverse_share or 0.0) < CONFIRMATION_THRESHOLD,
            "family_member_below_95":
                fam["live_count"] == 0
                or fam["min_me_share"] is None
                or fam["min_me_share"] < CONFIRMATION_THRESHOLD,
            "owner_family_reverse_share_below_95":
                edge is not None and owner_reverse_share is not None
                and owner_reverse_share < CONFIRMATION_THRESHOLD,
            "owner_denominator_unusable":
                edge is not None and not denominator_usable,
            "owner_mismatch": src["owner_relation"] == "cross_owner",
            "owner_unknown": src["owner_relation"] == "unknown_owner",
            "mapping_audit_review_required": bool(blocking),
            "canonical_comment_conflict": mid in comment_conflict,
            "no_explicit_identity_evidence": cap < CAP_EXPLICIT,
            "family_has_synthetic_member": fam["has_synthetic"],
            "owner_family_has_synthetic_member": ofam["has_synthetic"],
        }
        reasons = reason_codes(flags)

        confirmed = (
            canonical and not synthetic
            and src["owner_relation"] == "same_owner"
            and edge is not None
            and denominator_usable
            and not blocking
            and cap >= CAP_EXPLICIT
            and mid not in comment_conflict
            and not fam["has_synthetic"]
            and not ofam["has_synthetic"]
            and me_owner_share is not None
            and me_owner_share >= CONFIRMATION_THRESHOLD
            and owner_reverse_share is not None
            and owner_reverse_share >= CONFIRMATION_THRESHOLD)
        if synthetic:
            status = STATUS_EXCLUDED
        elif confirmed:
            status = STATUS_CONFIRMED
        else:
            status = STATUS_REVIEW

        ref_total, ref_review = refs_by_pair.get((mid, vid), (0, 0))
        erow = edge["row"] if edge else {}
        rows.append({
            "me_id": str(mid), "me_name": src["me_name"],
            "me_owner": src["me_owner"],
            "vanilla_id": str(vid), "vanilla_name": src["vanilla_name"],
            "vanilla_owner": src["vanilla_owner"],
            "relation_kind": KIND_CANONICAL if canonical else KIND_CANDIDATE,
            "relation_status": status,
            "source_scope": src["source_scope"],
            "edge_source": src["edge_source"],
            "evidence_families": src["evidence_families"],
            "is_canonical": "1" if canonical else "0",
            "canonical_basis": me["basis"],
            "canonical_status": me["status"],
            "owner_relation": src["owner_relation"],
            "country_scope": src["country_scope"],
            "edge_class": src["edge_class"],
            "me_share": src["me_share"],
            "me_registered_share": src["me_registered_share"],
            "me_geometry_usable": src["geometry_usable"],
            "v_hit_px": src["v_hit_px"],
            "v_native_px": src["v_native_px"],
            "v_share_native": src["v_share_native"],
            "family_member_count": str(fam["member_count"]),
            "family_me_ids": "|".join(str(x) for x in fam["member_ids"]),
            "family_live_member_count": str(fam["live_count"]),
            "family_has_synthetic_member":
                "1" if fam["has_synthetic"] else "0",
            "family_min_me_share": _num(fam["min_me_share"]),
            "family_reverse_hit_px": str(fam["reverse_hit_px"]),
            "family_reverse_native_px":
                str(reverse_native) if reverse_native is not None else "",
            "family_reverse_share": _num(reverse_share),
            "me_owner_area_px": erow.get("me_owner_area_px", ""),
            "me_owner_match_px": erow.get("me_owner_match_px", ""),
            "me_owner_share": erow.get("me_owner_share", ""),
            "v_owner_area_px": erow.get("v_owner_area_px", ""),
            "v_owner_match_px": erow.get("v_owner_match_px", ""),
            "v_owner_share": erow.get("v_owner_share", ""),
            "owner_family_member_count": str(ofam["member_count"]),
            "owner_family_me_ids":
                "|".join(str(x) for x in ofam["member_ids"]),
            "owner_family_live_member_count": str(ofam["live_count"]),
            "owner_family_has_synthetic_member":
                "1" if ofam["has_synthetic"] else "0",
            "owner_family_min_me_owner_share":
                _num(ofam["min_me_owner_share"]),
            "owner_family_reverse_match_px":
                str(ofam["reverse_match_px"]),
            "owner_family_reverse_share": _num(owner_reverse_share),
            "mapping_audit_class": audit_row["class"],
            "mapping_audit_reason_codes": audit_row["reason_codes"],
            "comment_votes": src["comment_votes"],
            "explicit_identity_evidence": explicit,
            "evidence_cap": _num(cap),
            "confidence_score": _num(score),
            "confidence_gate": (GATE_EXCLUDED if synthetic
                                else GATE_PASS if confirmed else GATE_FAIL),
            "confidence_reasons": reasons,
            "project_ref_count": str(ref_total),
            "project_review_ref_count": str(ref_review),
        })
    return rows, family, owner_family


def build_reference_rows(fields, rows, m2v, pair_index):
    """Append confirmation context to every focus_state_refs row.

    resolved_vanilla_id = the row's resolved vanilla_id column.
    resolved_me_id      = canonical members covering the reference:
                          `current_me_refs` plus the literal itself when it
                          is a live ME state resolving to that vanilla. The
    reference is `unresolved` unless every resolved member has a pair row.
    """
    out = []
    for i, row in enumerate(rows, 2):
        where = f"focus_state_refs.tsv:{i}"
        vid = _int(row["vanilla_id"], where + " vanilla_id")
        literal = _int(row["literal"], where + " literal")
        covered = [_int(x, where + " current_me_refs")
                   for x in row["current_me_refs"].split("|")] \
            if row["current_me_refs"] else []
        if any(x is None for x in covered):
            fail(f"{where}: malformed current_me_refs")
        members = set(covered)
        if literal is not None and row["literal_is_live_me"] == "1" \
                and literal in m2v and m2v[literal]["ancestor"] == vid:
            members.add(literal)
        members = sorted(members)
        complete = vid is not None \
            and all((m, vid) in pair_index for m in members)
        resolved = [pair_index[(m, vid)] for m in members] \
            if complete else []
        statuses = [r["relation_status"] for r in resolved]
        scores = [r["confidence_score"] for r in resolved]
        reasons = [r["confidence_reasons"] for r in resolved]
        status = reference_status(statuses)
        new = dict(row)
        new["resolved_me_id"] = "|".join(str(x) for x in members)
        new["resolved_vanilla_id"] = str(vid) if vid is not None else ""
        new["relation_status"] = "|".join(dict.fromkeys(statuses))
        new["confidence_score"] = _num(
            min((float(s) for s in scores), default=None))
        new["reference_status"] = status
        new["review_reason"] = (
            "" if status == REF_CONFIRMED
            else "unresolved_reference" if status == REF_UNRESOLVED
            else "|".join(dict.fromkeys(r for r in reasons if r)))
        out.append(new)
    return out


def _write_tsv(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames,
                                delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _input_record(path):
    """Input provenance: repo-relative path, sha256, data-row count."""
    path = Path(path)
    digest = hashlib.sha256()
    lines = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            lines += chunk.count(b"\n")
    root = Path(repo_root()).resolve()
    resolved = path.resolve()
    try:
        display = resolved.relative_to(root).as_posix()
    except ValueError:
        display = resolved.as_posix()
    return {"path": display, "sha256": digest.hexdigest(),
            "rows": max(lines - 1, 0)}


def build_summary(inputs, pair_rows, review_rows, ref_rows, family,
                  owner_family):
    """state_mapping_confirmation_summary/v2 document."""
    reason_counts = Counter()
    for row in pair_rows:
        for code in row["confidence_reasons"].split("|"):
            if code:
                reason_counts[code] += 1
    return {
        "schema": SUMMARY_SCHEMA,
        "kind": KIND,
        "threshold_contract": {
            "confirmation_threshold": CONFIRMATION_THRESHOLD,
            "confidence_score":
                "min(evidence_cap, me_owner_share, "
                "owner_family_reverse_share); missing metrics count as 0",
            "pair_level_only":
                "confirmed_95 is a pair-level gate; the owner-family "
                "reverse union covers only observed same-owner mass, "
                "never a complete-subset proof",
            "owner_family":
                "same_owner edges rows whose source_scope is "
                "compatibility or asserted (canonical rows join, "
                "observation rows never do); reverse_share = live-member "
                "v_owner_match_px union / v_owner_area_px",
            "evidence_caps": {
                "explicit_manual_or_comment": CAP_EXPLICIT,
                "pixel_or_vp_only": CAP_PIXEL_VP,
                "name_only": CAP_NAME,
                "none": CAP_FALLBACK,
            },
            "audit_blocking":
                "any audit reason outside the non-blocking context set "
                + str(sorted(NONBLOCKING_AUDIT_CODES))
                + "; context_only/clear_correction rows never block",
            "confirmed_95_requires": [
                "canonical one-to-many pair (non-canonical candidates "
                "are never confirmed)",
                "non-synthetic ME state (owner!=MAP, not a Map Border)",
                "owner_relation == same_owner",
                "a state_edges_1936 row with usable owner denominators "
                "(me_owner_area_px > 0 and v_owner_area_px > 0)",
                "explicit identity evidence (evidence_cap == 1.0)",
                "no identity-risk audit reason (context_only, "
                "clear_correction and comment(member) never veto)",
                "no comment edge citing a different ancestor "
                "(canonical_comment_conflict vetoes)",
                "no synthetic member in the canonical or owner family",
                "me_owner_share >= threshold",
                "owner_family_reverse_share >= threshold",
            ],
            "diagnostics_only": [
                "me_share / family_reverse_share / family_min_me_share "
                "and mask geometry columns describe the canonical "
                "family; they never gate",
            ],
            "reason_code_order": list(REASON_CODES),
        },
        "inputs": inputs,
        "counts": {
            "pairs_total": len(pair_rows),
            "by_relation_status": dict(sorted(Counter(
                r["relation_status"] for r in pair_rows).items())),
            "by_confidence_gate": dict(sorted(Counter(
                r["confidence_gate"] for r in pair_rows).items())),
            "by_owner_relation": dict(sorted(Counter(
                r["owner_relation"] for r in pair_rows).items())),
            "by_source_scope": dict(sorted(Counter(
                r["source_scope"] for r in pair_rows).items())),
            "by_edge_class": dict(sorted(Counter(
                r["edge_class"] for r in pair_rows).items())),
            "by_reason_code": dict(sorted(reason_counts.items())),
            "review_queue_rows": len(review_rows),
            "by_review_priority": dict(sorted(Counter(
                r["review_priority"] for r in review_rows).items())),
            "vanilla_families_total": len(family),
            "families_with_synthetic_member": sum(
                1 for f in family.values() if f["has_synthetic"]),
            "owner_families_total": len(owner_family),
            "owner_families_with_synthetic_member": sum(
                1 for f in owner_family.values() if f["has_synthetic"]),
            "project_refs_total": len(ref_rows),
            "project_refs_by_status": dict(sorted(Counter(
                r["reference_status"] for r in ref_rows).items())),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0])
    parser.add_argument("--tables", default=str(DEFAULT_TABLES),
                        help="m2v_states.tsv path or its docs/states dir")
    parser.add_argument("--v2m", default=str(DEFAULT_V2M),
                        help="v2m_states.tsv path")
    parser.add_argument("--mask-audit", default=str(DEFAULT_MASK_AUDIT),
                        help="state_mask_audit_1936.tsv pair ledger")
    parser.add_argument("--edges", default=str(DEFAULT_EDGES),
                        help="state_edges_1936.tsv owner-constrained ledger")
    parser.add_argument("--mapping-audit", default=str(DEFAULT_MAPPING_AUDIT),
                        help="mapping_audit.tsv verdict table")
    parser.add_argument("--comments", default=str(DEFAULT_COMMENTS),
                        help="mapping_comment_edges.tsv vote table")
    parser.add_argument("--focus-refs", default=str(DEFAULT_FOCUS_REFS),
                        help="focus_state_refs.tsv reference table")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="state_mapping_confirmation.tsv output path")
    parser.add_argument("--review-queue", default=str(DEFAULT_REVIEW_QUEUE),
                        help="review queue TSV output path")
    parser.add_argument("--refs-output", default=str(DEFAULT_REFS_OUTPUT),
                        help="confirmation refs TSV output path")
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY),
                        help="summary JSON output path")
    args = parser.parse_args(argv)

    tables = Path(args.tables)
    m2v_path = tables / "m2v_states.tsv" if tables.is_dir() else tables
    v2m_path = tables / "v2m_states.tsv" if tables.is_dir() \
        else Path(args.v2m)

    m2v, v2m = load_canonical(m2v_path, v2m_path)
    pairs = load_mask_pairs(args.mask_audit)
    edges = load_owner_edges(args.edges, m2v, v2m)
    votes = load_comment_votes(args.comments)
    audit = load_audit_classes(args.mapping_audit, m2v)
    ref_fields, ref_rows = _read_tsv(
        args.focus_refs, ["side", "literal", "vanilla_id",
                          "current_me_refs", "literal_is_live_me"])

    # every canonical pair must exist in the mask ledger (closed world).
    for mid, rec in m2v.items():
        if (mid, rec["ancestor"]) not in pairs:
            fail(f"mask audit missing canonical pair "
                 f"({mid}, {rec['ancestor']})")
    # every comment edge endpoint must be a known state.
    for mid, vid in votes:
        if mid not in m2v:
            fail(f"comment edge me_id {mid} unknown to m2v_states")
        if vid not in v2m:
            fail(f"comment edge vanilla_id {vid} unknown to v2m_states")

    # first pass without ref counts; refs need pair statuses, pairs need
    # ref counts — resolve refs first against statuses, then fill counts.
    pair_rows, family, owner_family = build_pairs(
        m2v, v2m, pairs, edges, votes, audit, {})
    pair_index = {(int(r["me_id"]), int(r["vanilla_id"])): r
                  for r in pair_rows}
    resolved_refs = build_reference_rows(ref_fields, ref_rows, m2v,
                                         pair_index)

    refs_by_pair = defaultdict(lambda: [0, 0])
    for ref in resolved_refs:
        for token in ref["resolved_me_id"].split("|"):
            if not token:
                continue
            key = (int(token), int(ref["resolved_vanilla_id"]))
            if key in pair_index:
                refs_by_pair[key][0] += 1
                if ref["reference_status"] != REF_CONFIRMED:
                    refs_by_pair[key][1] += 1
    for row in pair_rows:
        total, review = refs_by_pair.get(
            (int(row["me_id"]), int(row["vanilla_id"])), (0, 0))
        row["project_ref_count"] = str(total)
        row["project_review_ref_count"] = str(review)

    review_rows = [
        dict(row, review_priority=str(review_priority(
            row["is_canonical"] == "1",
            float(row["evidence_cap"] or 0))))
        for row in pair_rows if row["relation_status"] == STATUS_REVIEW]
    review_rows.sort(key=lambda r: (int(r["review_priority"]),
                                    int(r["me_id"]), int(r["vanilla_id"])))

    inputs = {
        "m2v_states": _input_record(m2v_path),
        "v2m_states": _input_record(v2m_path),
        "mask_audit": _input_record(args.mask_audit),
        "state_edges_1936": _input_record(args.edges),
        "mapping_audit": _input_record(args.mapping_audit),
        "comment_edges": _input_record(args.comments),
        "focus_refs": _input_record(args.focus_refs),
    }
    summary = build_summary(inputs, pair_rows, review_rows, resolved_refs,
                            family, owner_family)

    _write_tsv(args.output, PAIR_COLUMNS, pair_rows)
    _write_tsv(args.review_queue, REVIEW_COLUMNS, review_rows)
    _write_tsv(args.refs_output, list(ref_fields) + REF_APPEND_COLUMNS,
               resolved_refs)
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    counts = summary["counts"]
    print(f"state_mapping_confirmation: {counts['pairs_total']} pairs, "
          f"{counts['by_relation_status'].get(STATUS_CONFIRMED, 0)} "
          f"confirmed_95, {counts['review_queue_rows']} review rows, "
          f"{counts['project_refs_total']} refs -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
