#!/usr/bin/env python
"""Unit tests for tools/build_state_mapping_confirmation.py pure helpers.

These tests pin the emitted contract: threshold literal, evidence-cap
ladder, fixed reason-code order, deterministic review tiers, the
owner-scoped confirmed_95 gate (me_owner_share + owner-family reverse
union over state_edges_1936.tsv denominators), non-blocking audit
context, and fail-closed loading of canonical, mask, edges, audit and
comment tables. They never import pandas/numpy, never touch the game
install, and never read files outside TemporaryDirectory fixtures.
"""
import sys
import tempfile
from pathlib import Path

import unittest

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_state_mapping_confirmation as conf  # noqa: E402


# Documented reason-code emit order (module REASON_CODES, contract order).
EXPECTED_REASON_ORDER = [
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
]


def _mask_row(**over):
    """A synthetic state_mask_audit_1936.tsv row (all fields as strings)."""
    row = {
        "vanilla_id": "1",
        "vanilla_name": "Victoria",
        "vanilla_owner": "FRA",
        "me_id": "101",
        "me_name": "Alpha",
        "me_owner": "FRA",
        "owner_relation": "same_owner",
        "country_scope": "within_country",
        "me_land_px": "1000",
        "me_outside_px": "0",
        "me_water_px": "0",
        "me_unassigned_px": "0",
        "me_assigned_px": "1000",
        "me_hit_px": "980",
        "me_share": "0.980000",
        "me_registered_share": "0.980000",
        "me_spill_px": "20",
        "me_rank": "1",
        "me_modal_vanilla": "1",
        "me_modal_share": "0.980000",
        "me_modal_margin": "0.900000",
        "v_native_px": "500",
        "v_sampled_px": "500",
        "v_unreached_px": "0",
        "v_hit_px": "490",
        "v_share_native": "0.980000",
        "v_share_sampled": "0.980000",
        "pair_class": "mutual_contained",
        "pair_reason": "mutual_containment",
        "pair_claim": "sampled_masks",
        "me_components": "1",
        "me_largest_share": "1.000000",
        "me_largest_hit_share": "0.980000",
        "components_agree": "1",
        "geometry_usable": "1",
        "source_scope": "compatibility",
        "edge_source": "canonical|comment",
        "evidence_families": "canonical|comment",
        "is_canonical": "1",
        "canonical_ancestor": "1",
        "edge_class": "identity_accepted",
        "comment_votes": "3",
        "manual_kind": "",
        "consistency": "canonical_geometry_agree",
    }
    row.update(over)
    return row


def _pair_entry(me_share, v_hit_px, v_native_px, **row_over):
    """load_mask_pairs-style parsed entry around a synthetic mask row."""
    return {
        "row": _mask_row(**row_over),
        "me_share": me_share,
        "v_hit_px": v_hit_px,
        "v_native_px": v_native_px,
    }


def _edges_row(**over):
    """A synthetic state_edges_1936.tsv row (all fields as strings)."""
    row = {
        "vanilla_id": "1",
        "vanilla_name": "Victoria",
        "vanilla_owner": "FRA",
        "me_id": "101",
        "me_name": "Alpha",
        "me_owner": "FRA",
        "owner_relation": "same_owner",
        "candidate_scope": "within_country",
        "source_scope": "compatibility",
        "edge_source": "canonical|comment",
        "evidence_families": "canonical|comment",
        "is_canonical": "1",
        "canonical_ancestor": "1",
        "edge_class": "identity_accepted",
        "comment_votes": "3",
        "manual_kind": "",
        "me_area_px": "1000",
        "me_match_px": "980",
        "me_share": "0.980000",
        "me_owner_area_px": "1000",
        "me_owner_match_px": "980",
        "me_owner_share": "0.980000",
        "me_rank_global": "1",
        "me_rank_owner": "1",
        "v_native_area_px": "500",
        "v_observable_px": "500",
        "v_match_px": "490",
        "v_share": "0.980000",
        "v_owner_area_px": "500",
        "v_owner_match_px": "490",
        "v_owner_share": "0.980000",
        "v_rank_global": "1",
        "v_rank_owner": "1",
    }
    row.update(over)
    return row


def _opt_int(text):
    return None if text in (None, "") else int(text)


def _opt_float(text):
    return None if text in (None, "") else float(text)


def _edge_entry(**row_over):
    """load_owner_edges-style parsed entry around a synthetic edges row."""
    row = _edges_row(**row_over)
    same = row["owner_relation"] == "same_owner"
    return {
        "row": row,
        "same_owner": same,
        "family_member": same and
            row["source_scope"] in conf.OWNER_FAMILY_SCOPES,
        "me_owner_area_px": _opt_int(row["me_owner_area_px"]),
        "me_owner_match_px": _opt_int(row["me_owner_match_px"]),
        "me_owner_share": _opt_float(row["me_owner_share"]),
        "v_owner_area_px": _opt_int(row["v_owner_area_px"]),
        "v_owner_match_px": _opt_int(row["v_owner_match_px"]),
        "v_owner_share": _opt_float(row["v_owner_share"]),
    }


_SCOPE_BY_RELATION = {
    "same_owner": "within_country",
    "cross_owner": "cross_country_diagnostic",
    "unknown_owner": "unknown_owner",
}


def _world(member_shares=(0.98, 0.97), member_hits=(490, 10),
           v_native=500, owner_relation="same_owner",
           audit_class="no_action", audit_codes=None,
           geometry_usable="1", synthetic_member=None,
           owner_shares=(0.98, 0.97), v_owner_area=500,
           v_owner_matches=None, me_owner_areas=None):
    """Two-member canonical family plus consistent mask/edges fixtures.

    Returns (m2v, v2m, pairs, edges, audit). Member owner metrics default
    to a confirmed world: me_owner_share >= 0.95 per member and a perfect
    owner-family reverse union (490 + 10 hits over a 500 px same-owner
    vanilla denominator).
    """
    m2v = {
        101: {"name": "Alpha", "owner": "FRA", "ancestor": 1,
              "basis": "comment", "status": "mapped", "synthetic": False},
        102: {"name": "Beta", "owner": "FRA", "ancestor": 1,
              "basis": "comment", "status": "mapped", "synthetic": False},
    }
    members = [101, 102]
    if synthetic_member is not None:
        mid, name, owner = synthetic_member
        m2v[mid] = {"name": name, "owner": owner, "ancestor": 1,
                    "basis": "pixel-geo-low", "status": "mapped",
                    "synthetic": True}
        members.append(mid)
    v2m = {
        1: {"name": "Victoria", "owner": "FRA", "members": members,
            "primary": 101, "basis": "comment", "status": "1:many"},
        2: {"name": "Rival", "owner": "FRA", "members": [],
            "primary": None, "basis": "", "status": "unmapped"},
    }
    same = owner_relation == "same_owner"
    country_scope = _SCOPE_BY_RELATION[owner_relation]
    if v_owner_matches is None:
        v_owner_matches = list(member_hits)
    if me_owner_areas is None:
        me_owner_areas = [1000, 1000]

    pairs = {}
    edges = {}
    for idx, (mid, share, hit) in enumerate(
            zip((101, 102), member_shares, member_hits)):
        pairs[(mid, 1)] = _pair_entry(
            share, hit, v_native, me_id=str(mid),
            me_name=m2v[mid]["name"],
            owner_relation=owner_relation,
            country_scope=country_scope,
            geometry_usable=geometry_usable,
            is_canonical="1", canonical_ancestor="1")
        me_owner_area = me_owner_areas[idx]
        v_owner_match = v_owner_matches[idx] if same else 0
        edges[(mid, 1)] = _edge_entry(
            me_id=str(mid), me_name=m2v[mid]["name"],
            owner_relation=owner_relation,
            candidate_scope=country_scope,
            me_match_px=str(hit),
            me_share=f"{hit / 1000:.6f}",
            me_owner_area_px=str(me_owner_area),
            me_owner_match_px=str(hit if same else 0),
            me_owner_share=(f"{owner_shares[idx]:.6f}"
                            if same and me_owner_area
                            and owner_shares[idx] is not None else ""),
            v_match_px=str(v_owner_match if same else hit),
            v_owner_area_px=str(v_owner_area),
            v_owner_match_px=str(v_owner_match),
            v_owner_share=(f"{v_owner_match / v_owner_area:.6f}"
                           if same and v_owner_area else ""),
            is_canonical="1", canonical_ancestor="1")
    if synthetic_member is not None:
        mid, name, owner = synthetic_member
        pairs[(mid, 1)] = _pair_entry(
            0.5, 4, v_native, me_id=str(mid), me_name=name,
            me_owner=owner, owner_relation="cross_owner",
            country_scope="cross_country_diagnostic",
            is_canonical="1", canonical_ancestor="1")
        edges[(mid, 1)] = _edge_entry(
            me_id=str(mid), me_name=name, me_owner=owner,
            owner_relation="cross_owner",
            candidate_scope="cross_country_diagnostic",
            me_match_px="4", me_owner_match_px="0",
            me_owner_share="", v_match_px="4", v_owner_match_px="0",
            v_owner_share="",
            is_canonical="1", canonical_ancestor="1")

    def _audit(mid):
        klass = (audit_class.get(mid) if isinstance(audit_class, dict)
                 else audit_class)
        codes = (audit_codes.get(mid) if isinstance(audit_codes, dict)
                 else audit_codes) or ""
        return {"class": klass, "reason_codes": codes,
                "canonical_ancestor": "1"}

    audit = {mid: _audit(mid) for mid in members}
    return m2v, v2m, pairs, edges, audit


def _ref_row(**over):
    """A synthetic focus_state_refs.tsv row (string fields)."""
    row = {
        "side": "mod",
        "file": "foci.txt",
        "line": "10",
        "block_path": "focus_x",
        "key": "states",
        "literal": "101",
        "vanilla_id": "1",
        "current_me_refs": "101|102",
        "literal_is_live_me": "1",
        "resolution": "ok",
    }
    row.update(over)
    return row


class ConstantTests(unittest.TestCase):
    """Gate thresholds and vocabulary pinned as literals."""

    def test_threshold_literal(self):
        self.assertEqual(conf.CONFIRMATION_THRESHOLD, 0.95)

    def test_evidence_cap_ladder(self):
        self.assertEqual(conf.CAP_EXPLICIT, 1.00)
        self.assertEqual(conf.CAP_PIXEL_VP, 0.80)
        self.assertEqual(conf.CAP_NAME, 0.65)
        self.assertEqual(conf.CAP_FALLBACK, 0.50)

    def test_status_vocabulary(self):
        self.assertEqual(conf.STATUS_CONFIRMED, "confirmed_95")
        self.assertEqual(conf.STATUS_REVIEW, "review_candidate")
        self.assertEqual(conf.STATUS_EXCLUDED, "excluded_synthetic")

    def test_relation_kind_vocabulary(self):
        self.assertEqual(conf.KIND_CANONICAL, "canonical")
        self.assertEqual(conf.KIND_CANDIDATE, "candidate")

    def test_gate_vocabulary(self):
        self.assertEqual(conf.GATE_PASS, "pass")
        self.assertEqual(conf.GATE_FAIL, "fail")
        self.assertEqual(conf.GATE_EXCLUDED, "excluded")

    def test_reference_status_vocabulary(self):
        self.assertEqual(conf.REF_CONFIRMED, "confirmed_target")
        self.assertEqual(conf.REF_REVIEW, "review_target")
        self.assertEqual(conf.REF_EXCLUDED, "excluded_target")
        self.assertEqual(conf.REF_UNRESOLVED, "unresolved")

    def test_reason_code_order_is_fixed(self):
        self.assertEqual(list(conf.REASON_CODES), EXPECTED_REASON_ORDER)

    def test_review_priority_tiers(self):
        self.assertEqual(conf.PRIORITY_CANONICAL_EXPLICIT, 0)
        self.assertEqual(conf.PRIORITY_CANONICAL_OTHER, 1)
        self.assertEqual(conf.PRIORITY_CANDIDATE_EXPLICIT, 2)
        self.assertEqual(conf.PRIORITY_CANDIDATE_OTHER, 3)

    def test_summary_schema_literal(self):
        self.assertEqual(
            conf.SUMMARY_SCHEMA, "state_mapping_confirmation_summary/v2")

    def test_kind_disclaimer_is_gate_not_probability(self):
        self.assertIn("not calibrated probabilities", conf.KIND)
        self.assertIn("conservative", conf.KIND)
        self.assertIn("not a complete-subset proof", conf.KIND)

    def test_owner_family_scope_vocabulary(self):
        self.assertEqual(conf.OWNER_FAMILY_SCOPES,
                         {"compatibility", "asserted"})
        self.assertNotIn("observation", conf.OWNER_FAMILY_SCOPES)

    def test_nonblocking_audit_codes_cover_soft_and_member(self):
        for code in ("vp-absence-gap", "region-contradiction",
                     "raw-geography-weak", "overlap-weak",
                     "comment(member)", "explicit-independent-correction"):
            self.assertIn(code, conf.NONBLOCKING_AUDIT_CODES)

    def test_default_output_names(self):
        self.assertEqual(
            conf.DEFAULT_OUTPUT.name, "state_mapping_confirmation.tsv")
        self.assertEqual(
            conf.DEFAULT_REVIEW_QUEUE.name,
            "state_mapping_review_queue_95.tsv")
        self.assertEqual(
            conf.DEFAULT_REFS_OUTPUT.name,
            "state_mapping_confirmation_refs.tsv")
        self.assertEqual(
            conf.DEFAULT_SUMMARY.name,
            "state_mapping_confirmation_summary.json")
        self.assertEqual(
            conf.DEFAULT_EDGES.name, "state_edges_1936.tsv")


class NumFormatTests(unittest.TestCase):
    """_num: six-decimal fixed format, blank stays blank."""

    def test_six_decimals(self):
        self.assertEqual(conf._num(0.95), "0.950000")
        self.assertEqual(conf._num(1.0), "1.000000")
        self.assertEqual(conf._num(0.949999), "0.949999")

    def test_blank_stays_blank(self):
        self.assertEqual(conf._num(None), "")
        self.assertEqual(conf._num(""), "")

    def test_tiny_negative_zero_collapses(self):
        self.assertEqual(conf._num(-0.0), "0.000000")
        self.assertEqual(conf._num(0.0000004), "0.000000")


class SyntheticTests(unittest.TestCase):
    """is_synthetic gates on owner MAP or a Map Border name."""

    def test_map_owner(self):
        self.assertTrue(conf.is_synthetic("MAP", "Pennsylvania"))
        self.assertTrue(conf.is_synthetic("MAP", "Map Border"))

    def test_map_border_name_any_owner(self):
        self.assertTrue(conf.is_synthetic("GER", "Map Border"))
        self.assertTrue(conf.is_synthetic("FRA", "map border"))
        self.assertTrue(conf.is_synthetic("", "Eastern Map Border strip"))

    def test_real_states_not_synthetic(self):
        self.assertFalse(conf.is_synthetic("FRA", "Victoria"))
        self.assertFalse(conf.is_synthetic("MAPLE", "Border"))
        self.assertFalse(conf.is_synthetic("GER", ""))
        self.assertFalse(conf.is_synthetic("", ""))


class NumParseTests(unittest.TestCase):
    """_float/_int: non-finite input fails closed; finite parses."""

    def test_blank_is_none(self):
        self.assertIsNone(conf._float(None, "t"))
        self.assertIsNone(conf._float("", "t"))

    def test_finite_parses(self):
        self.assertEqual(conf._float("0.95", "t"), 0.95)
        self.assertEqual(conf._float("-1e3", "t"), -1000.0)

    def test_non_finite_fails_closed(self):
        for text in ("nan", "NaN", "inf", "-inf", "Infinity"):
            with self.assertRaises(SystemExit, msg=text):
                conf._float(text, "x me_share")

    def test_malformed_fails_closed(self):
        with self.assertRaises(SystemExit):
            conf._float("abc", "x me_share")

    def test_nonneg_int_rejects_negative(self):
        self.assertEqual(conf._nonneg_int("5", "t"), 5)
        self.assertIsNone(conf._nonneg_int("", "t"))
        with self.assertRaises(SystemExit):
            conf._nonneg_int("-1", "t")


class LoadCanonicalTests(unittest.TestCase):
    """load_canonical fails closed on duplicate v2m me_members."""

    M2V_TSV = (
        "me_id\tme_name\towner\tv_ancestor\tbasis\tstatus\n"
        "101\tAlpha\tFRA\t1\tcomment\tmapped\n"
        "102\tBeta\tFRA\t1\tcomment\tmapped\n")
    V2M_TSV = (
        "vanilla_id\tvanilla_name\towner\tme_primary\tme_members\tbasis"
        "\tstatus\n"
        "1\tVictoria\tFRA\t101\t{members}\tcomment\t1:many\n")

    def _load(self, members):
        with tempfile.TemporaryDirectory() as tmp:
            m2v_path = Path(tmp) / "m2v_states.tsv"
            v2m_path = Path(tmp) / "v2m_states.tsv"
            m2v_path.write_text(self.M2V_TSV, encoding="utf-8")
            v2m_path.write_text(
                self.V2M_TSV.format(members=members), encoding="utf-8")
            return conf.load_canonical(m2v_path, v2m_path)

    def test_duplicate_member_ids_fail_closed(self):
        # A repeated member would double-count reverse hits.
        with self.assertRaises(SystemExit):
            self._load("101|101|102")

    def test_distinct_members_load(self):
        m2v, v2m = self._load("101|102")
        self.assertEqual(v2m[1]["members"], [101, 102])
        self.assertEqual(sorted(m2v), [101, 102])


class LoadOwnerEdgesTests(unittest.TestCase):
    """load_owner_edges: strict owner-metric validation, fail closed."""

    HEADER = "\t".join(conf.EDGE_COLUMNS) + "\n"

    def _load(self, *rows):
        m2v, v2m, _, _, _ = _world()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state_edges_1936.tsv"
            with path.open("w", encoding="utf-8", newline="") as out:
                out.write(self.HEADER)
                for row in rows:
                    out.write("\t".join(
                        row.get(col, "") for col in conf.EDGE_COLUMNS)
                        + "\n")
            return conf.load_owner_edges(path, m2v, v2m)

    def test_valid_rows_load(self):
        edges = self._load(
            _edges_row(),
            _edges_row(me_id="102", me_name="Beta",
                       me_match_px="970", me_owner_match_px="970",
                       me_owner_share="0.970000",
                       v_match_px="10", v_owner_match_px="10",
                       v_owner_share="0.020000",
                       comment_votes="1"))
        self.assertEqual(sorted(edges), [(101, 1), (102, 1)])
        self.assertTrue(edges[(101, 1)]["family_member"])
        self.assertAlmostEqual(edges[(101, 1)]["me_owner_share"], 0.98)

    def test_missing_columns_fail_closed(self):
        m2v, v2m, _, _, _ = _world()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state_edges_1936.tsv"
            path.write_text("me_id\tvanilla_id\n101\t1\n",
                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                conf.load_owner_edges(path, m2v, v2m)

    def test_duplicate_pair_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(), _edges_row())

    def test_unknown_me_id_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_id="777"))

    def test_unknown_vanilla_id_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(vanilla_id="9"))

    def test_blank_ids_fail_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_id=""))

    def test_unknown_owner_relation_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(owner_relation="sometimes_owner"))

    def test_is_canonical_drift_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(is_canonical="0", canonical_ancestor=""))

    def test_canonical_ancestor_drift_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(canonical_ancestor="2"))

    def test_negative_pixel_count_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_area_px="-5"))

    def test_non_integer_pixel_count_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_area_px="1.5"))

    def test_match_exceeds_owner_area_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_area_px="900"))

    def test_owner_match_differs_from_match_on_same_owner(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_match_px="900"))

    def test_nonzero_owner_match_on_cross_owner(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(
                owner_relation="cross_owner",
                candidate_scope="cross_country_diagnostic",
                me_owner_match_px="10", me_owner_share="",
                v_owner_match_px="0", v_owner_share=""))

    def test_share_set_without_denominator_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_area_px="0",
                                  me_owner_share="0.500000"))

    def test_share_blank_with_usable_denominator_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_share=""))

    def test_share_out_of_unit_interval_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(me_owner_share="1.500000"))

    def test_v_owner_match_differs_on_same_owner(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(v_owner_match_px="400"))

    def test_me_owner_area_drift_across_rows_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(
                _edges_row(),
                _edges_row(vanilla_id="2", vanilla_name="Rival",
                           me_owner_area_px="999",
                           me_owner_share="0.980981",
                           is_canonical="0", canonical_ancestor="",
                           source_scope="asserted",
                           me_match_px="980", me_owner_match_px="980"))

    def test_v_owner_area_drift_across_rows_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(
                _edges_row(),
                _edges_row(me_id="102", me_name="Beta",
                           me_match_px="970", me_owner_match_px="970",
                           me_owner_share="0.970000",
                           v_match_px="10", v_owner_match_px="10",
                           v_owner_share="0.016667",
                           v_owner_area_px="600"))

    def test_observation_row_never_family_member(self):
        edges = self._load(_edges_row(
            vanilla_id="2", vanilla_name="Rival",
            source_scope="observation", edge_source="raster",
            evidence_families="raster", comment_votes="",
            is_canonical="0", canonical_ancestor="",
            me_match_px="60", me_owner_match_px="60",
            me_share="0.060000", me_owner_share="0.060000",
            v_match_px="60", v_owner_match_px="60",
            v_owner_share="0.120000"))
        self.assertFalse(edges[(101, 2)]["family_member"])

    def test_same_owner_canonical_observation_scope_fails_closed(self):
        with self.assertRaises(SystemExit):
            self._load(_edges_row(source_scope="observation"))

    def test_asserted_row_is_family_member(self):
        edges = self._load(_edges_row(
            vanilla_id="2", vanilla_name="Rival",
            source_scope="asserted", edge_source="comment",
            evidence_families="comment",
            is_canonical="0", canonical_ancestor="",
            me_match_px="60", me_owner_match_px="60",
            me_share="0.060000", me_owner_share="0.060000",
            v_match_px="60", v_owner_match_px="60",
            v_owner_share="0.120000"))
        self.assertTrue(edges[(101, 2)]["family_member"])


class AuditBlockingTests(unittest.TestCase):
    """audit_blocking_codes: context signals never veto identity."""

    def _row(self, klass, codes):
        return {"class": klass, "reason_codes": codes}

    def test_context_only_never_blocks(self):
        self.assertEqual(conf.audit_blocking_codes(
            self._row("context_only", "overlap-weak|vp-absence-gap")), [])

    def test_no_action_and_clear_correction_never_block(self):
        self.assertEqual(conf.audit_blocking_codes(
            self._row("no_action", "")), [])
        self.assertEqual(conf.audit_blocking_codes(
            self._row("clear_correction",
                      "explicit-independent-correction")), [])

    def test_comment_member_reason_is_nonblocking_context(self):
        self.assertEqual(conf.audit_blocking_codes(
            self._row("review_required", "comment(member)")), [])

    def test_soft_reasons_never_block(self):
        for code in ("vp-absence-gap", "region-contradiction",
                     "raw-geography-weak", "overlap-weak"):
            self.assertEqual(conf.audit_blocking_codes(
                self._row("review_required", code)), [], code)

    def test_identity_risk_reasons_block(self):
        for code in ("overlap-conflict", "raw-geography-conflict",
                     "comment-vs-pixel-conflict", "name-collision",
                     "manual-automatic-conflict", "core-disjoint",
                     "vp-cross-state"):
            self.assertIn(
                code, conf.audit_blocking_codes(
                    self._row("review_required", code)), code)

    def test_mixed_codes_keep_only_identity_risk(self):
        self.assertEqual(
            conf.audit_blocking_codes(
                self._row("review_required",
                          "comment(member)|overlap-weak|overlap-conflict")),
            ["overlap-conflict"])

    def test_missing_row_never_blocks(self):
        self.assertEqual(conf.audit_blocking_codes(None), [])
        self.assertEqual(conf.audit_blocking_codes({}), [])


class OwnerFamilyStatsTests(unittest.TestCase):
    """owner_family_stats: union over live same-owner asserted members."""

    def _member(self, me_id, synthetic=False, me_owner_share=0.9,
                v_owner_match_px=100):
        return {"me_id": me_id, "synthetic": synthetic,
                "me_owner_share": me_owner_share,
                "v_owner_match_px": v_owner_match_px}

    def test_reverse_match_sums_live_members(self):
        stats = conf.owner_family_stats([
            self._member(1, me_owner_share=0.98, v_owner_match_px=490),
            self._member(2, me_owner_share=0.97, v_owner_match_px=10),
        ])
        self.assertEqual(stats["reverse_match_px"], 500)
        self.assertEqual(stats["member_count"], 2)
        self.assertEqual(stats["live_count"], 2)
        self.assertEqual(stats["member_ids"], [1, 2])
        self.assertFalse(stats["has_synthetic"])
        self.assertAlmostEqual(stats["min_me_owner_share"], 0.97)

    def test_synthetic_excluded_from_live_and_union(self):
        stats = conf.owner_family_stats([
            self._member(1, me_owner_share=0.98, v_owner_match_px=490),
            self._member(9, synthetic=True, me_owner_share=0.5,
                         v_owner_match_px=4),
        ])
        self.assertEqual(stats["member_count"], 2)
        self.assertEqual(stats["live_count"], 1)
        self.assertTrue(stats["has_synthetic"])
        self.assertEqual(stats["reverse_match_px"], 490)
        self.assertAlmostEqual(stats["min_me_owner_share"], 0.98)

    def test_min_share_none_when_any_live_share_missing(self):
        stats = conf.owner_family_stats([
            self._member(1, me_owner_share=0.98, v_owner_match_px=10),
            self._member(2, me_owner_share=None, v_owner_match_px=5),
        ])
        self.assertIsNone(stats["min_me_owner_share"])

    def test_empty_family(self):
        stats = conf.owner_family_stats([])
        self.assertEqual(stats["member_count"], 0)
        self.assertEqual(stats["live_count"], 0)
        self.assertEqual(stats["reverse_match_px"], 0)
        self.assertIsNone(stats["min_me_owner_share"])
        self.assertFalse(stats["has_synthetic"])


class EvidenceCapTests(unittest.TestCase):
    """evidence_cap: only human channels reach 1.00."""

    def test_explicit_bases(self):
        for basis in ("manual", "comment", "comment(member)"):
            cap, explicit = conf.evidence_cap({}, basis)
            self.assertEqual(cap, 1.00, basis)
            self.assertIn("basis:" + basis, explicit)

    def test_comment_votes_make_explicit(self):
        cap, explicit = conf.evidence_cap({"comment_votes": "3"}, "")
        self.assertEqual(cap, 1.00)
        self.assertIn("comment_votes:3", explicit)
        cap, _ = conf.evidence_cap({"comment_votes": "7"}, "")
        self.assertEqual(cap, 1.00)

    def test_zero_votes_not_explicit_by_votes(self):
        cap, _ = conf.evidence_cap({"comment_votes": "0"}, "")
        self.assertNotEqual(cap, 1.00)
        cap, _ = conf.evidence_cap({"comment_votes": ""}, "")
        self.assertNotEqual(cap, 1.00)

    def test_manual_kind_explicit(self):
        cap, explicit = conf.evidence_cap({"manual_kind": "member"}, "")
        self.assertEqual(cap, 1.00)
        self.assertIn("manual_kind:member", explicit)

    def test_comment_or_manual_family_explicit(self):
        cap, explicit = conf.evidence_cap(
            {"evidence_families": "raster|comment"}, "")
        self.assertEqual(cap, 1.00)
        self.assertIn("evidence:comment", explicit)
        cap, explicit = conf.evidence_cap(
            {"evidence_families": "manual|pixel"}, "")
        self.assertEqual(cap, 1.00)

    def test_pixel_and_vp_cap_at_080(self):
        for row, basis in (({"evidence_families": "pixel"}, ""),
                           ({"evidence_families": "vp|raster"}, ""),
                           ({}, "pixel-geo"),
                           ({}, "vp-geo")):
            cap, explicit = conf.evidence_cap(row, basis)
            self.assertEqual(cap, 0.80, (row, basis))
            self.assertEqual(explicit, "")

    def test_name_and_same_id_cap_at_065(self):
        for row, basis in (({"evidence_families": "name"}, ""),
                           ({"evidence_families": "same_id"}, ""),
                           ({}, "name"),
                           ({}, "name~"),
                           ({}, "name-singleton")):
            cap, _ = conf.evidence_cap(row, basis)
            self.assertEqual(cap, 0.65, (row, basis))

    def test_no_evidence_caps_at_050(self):
        cap, explicit = conf.evidence_cap({}, "")
        self.assertEqual(cap, 0.50)
        self.assertEqual(explicit, "")
        cap, _ = conf.evidence_cap({"evidence_families": "raster"}, "")
        self.assertEqual(cap, 0.50)

    def test_explicit_beats_weaker_families(self):
        cap, _ = conf.evidence_cap(
            {"evidence_families": "comment|pixel|name"}, "pixel-geo")
        self.assertEqual(cap, 1.00)

    def test_pixel_beats_name(self):
        cap, _ = conf.evidence_cap(
            {"evidence_families": "pixel|name"}, "")
        self.assertEqual(cap, 0.80)

    def test_pixel_only_never_reaches_threshold(self):
        cap, _ = conf.evidence_cap(
            {"evidence_families": "pixel|raster"}, "")
        self.assertLess(cap, conf.CONFIRMATION_THRESHOLD)

    def test_name_only_never_reaches_threshold(self):
        cap, _ = conf.evidence_cap({}, "name")
        self.assertLess(cap, conf.CONFIRMATION_THRESHOLD)


class FamilyStatsTests(unittest.TestCase):
    """family_stats: canonical-family diagnostics (never gating)."""

    def _member(self, me_id, synthetic=False, me_share=0.9, v_hit_px=100):
        return {"me_id": me_id, "synthetic": synthetic,
                "me_share": me_share, "v_hit_px": v_hit_px}

    def test_reverse_hits_sum_across_members(self):
        stats = conf.family_stats([
            self._member(1, me_share=0.98, v_hit_px=490),
            self._member(2, me_share=0.97, v_hit_px=10),
        ])
        self.assertEqual(stats["reverse_hit_px"], 500)
        self.assertEqual(stats["member_count"], 2)
        self.assertEqual(stats["live_count"], 2)
        self.assertEqual(stats["member_ids"], [1, 2])
        self.assertFalse(stats["has_synthetic"])
        self.assertAlmostEqual(stats["min_me_share"], 0.97)

    def test_synthetic_excluded_from_live_and_union(self):
        stats = conf.family_stats([
            self._member(1, me_share=0.98, v_hit_px=490),
            self._member(9, synthetic=True, me_share=0.5, v_hit_px=4),
        ])
        self.assertEqual(stats["member_count"], 2)
        self.assertEqual(stats["live_count"], 1)
        self.assertTrue(stats["has_synthetic"])
        self.assertEqual(stats["reverse_hit_px"], 490)
        self.assertAlmostEqual(stats["min_me_share"], 0.98)

    def test_min_share_none_when_any_live_share_missing(self):
        stats = conf.family_stats([
            self._member(1, me_share=0.98, v_hit_px=10),
            self._member(2, me_share=None, v_hit_px=5),
        ])
        self.assertIsNone(stats["min_me_share"])

    def test_no_live_members(self):
        stats = conf.family_stats([
            self._member(9, synthetic=True, me_share=0.5, v_hit_px=4)])
        self.assertEqual(stats["live_count"], 0)
        self.assertIsNone(stats["min_me_share"])
        self.assertEqual(stats["reverse_hit_px"], 0)

    def test_empty_family(self):
        stats = conf.family_stats([])
        self.assertEqual(stats["member_count"], 0)
        self.assertEqual(stats["live_count"], 0)
        self.assertEqual(stats["reverse_hit_px"], 0)
        self.assertIsNone(stats["min_me_share"])
        self.assertFalse(stats["has_synthetic"])

    def test_missing_v_hit_counts_as_zero_in_sum(self):
        stats = conf.family_stats([
            self._member(1, me_share=0.9, v_hit_px=None),
            self._member(2, me_share=0.9, v_hit_px=30),
        ])
        self.assertEqual(stats["reverse_hit_px"], 30)


class ConfidenceScoreTests(unittest.TestCase):
    """confidence_score = min(cap, me_owner_share, owner reverse union)."""

    def test_minimum_of_three(self):
        self.assertAlmostEqual(
            conf.confidence_score(1.0, 0.98, 0.97), 0.97)
        self.assertAlmostEqual(
            conf.confidence_score(0.80, 0.98, 0.97), 0.80)
        self.assertAlmostEqual(
            conf.confidence_score(1.0, 0.90, 0.97), 0.90)

    def test_missing_metrics_count_as_zero(self):
        self.assertEqual(conf.confidence_score(1.0, None, 1.0), 0.0)
        self.assertEqual(conf.confidence_score(1.0, 1.0, None), 0.0)
        self.assertEqual(conf.confidence_score(1.0, None, None), 0.0)

    def test_at_threshold_is_095(self):
        self.assertAlmostEqual(
            conf.confidence_score(1.0, 0.95, 0.95), 0.95)

    def test_cap_is_the_ceiling(self):
        self.assertAlmostEqual(
            conf.confidence_score(0.50, 1.0, 1.0), 0.50)


class ReasonCodesTests(unittest.TestCase):
    """reason_codes emits the fixed contract order regardless of input."""

    def test_order_follows_reason_codes_not_insertion(self):
        flags = {
            "no_explicit_identity_evidence": True,
            "synthetic_state": True,
            "owner_mismatch": True,
        }
        self.assertEqual(
            conf.reason_codes(flags),
            "synthetic_state|owner_mismatch|no_explicit_identity_evidence")

    def test_full_set_emits_every_code_in_order(self):
        flags = {code: True for code in EXPECTED_REASON_ORDER}
        self.assertEqual(
            conf.reason_codes(flags).split("|"), EXPECTED_REASON_ORDER)

    def test_false_and_missing_flags_dropped(self):
        flags = {"synthetic_state": False, "owner_mismatch": True}
        self.assertEqual(conf.reason_codes(flags), "owner_mismatch")

    def test_unknown_codes_never_emitted(self):
        self.assertEqual(conf.reason_codes({"not_a_code": True}), "")

    def test_empty_flags_empty_string(self):
        self.assertEqual(conf.reason_codes({}), "")


class ReviewPriorityTests(unittest.TestCase):
    """review_priority: canonical+explicit first, candidates last."""

    def test_four_tiers(self):
        self.assertEqual(conf.review_priority(True, 1.0), 0)
        self.assertEqual(conf.review_priority(True, 0.80), 1)
        self.assertEqual(conf.review_priority(False, 1.0), 2)
        self.assertEqual(conf.review_priority(False, 0.80), 3)

    def test_tiers_sort_in_documented_order(self):
        tiers = [
            conf.review_priority(False, 0.50),
            conf.review_priority(True, 0.50),
            conf.review_priority(False, 1.0),
            conf.review_priority(True, 1.0),
        ]
        self.assertEqual(sorted(tiers), [0, 1, 2, 3])


class ReferenceStatusTests(unittest.TestCase):
    """reference_status aggregates resolved pair statuses."""

    def test_all_empty_is_unresolved(self):
        self.assertEqual(conf.reference_status([]), conf.REF_UNRESOLVED)
        self.assertEqual(conf.reference_status([""]), conf.REF_UNRESOLVED)

    def test_any_excluded_dominates(self):
        self.assertEqual(
            conf.reference_status(
                [conf.STATUS_CONFIRMED, conf.STATUS_EXCLUDED]),
            conf.REF_EXCLUDED)

    def test_all_confirmed_is_confirmed(self):
        self.assertEqual(
            conf.reference_status(
                [conf.STATUS_CONFIRMED, conf.STATUS_CONFIRMED]),
            conf.REF_CONFIRMED)

    def test_any_review_makes_review(self):
        self.assertEqual(
            conf.reference_status(
                [conf.STATUS_CONFIRMED, conf.STATUS_REVIEW]),
            conf.REF_REVIEW)
        self.assertEqual(
            conf.reference_status([conf.STATUS_REVIEW]), conf.REF_REVIEW)


class BuildPairsTests(unittest.TestCase):
    """build_pairs over a synthetic two-member canonical family."""

    def _rows(self, **world_over):
        m2v, v2m, pairs, edges, audit = _world(**world_over)
        votes = {(101, 1): 3, (102, 1): 1}
        rows, family, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, votes, audit, {})
        return rows, family, owner_family

    def _row(self, rows, mid, vid=1):
        key = (str(mid), str(vid))
        for row in rows:
            if (row["me_id"], row["vanilla_id"]) == key:
                return row
        self.fail(f"no pair row for {key}")

    def test_confirmed_95_when_all_gates_pass(self):
        rows, _, owner_family = self._rows()
        self.assertEqual(owner_family[1]["reverse_match_px"], 500)
        for mid in (101, 102):
            row = self._row(rows, mid)
            self.assertEqual(row["relation_status"], "confirmed_95")
            self.assertEqual(row["confidence_gate"], "pass")
            self.assertEqual(row["relation_kind"], "canonical")
            self.assertEqual(row["is_canonical"], "1")
            self.assertEqual(row["owner_family_reverse_share"], "1.000000")
            self.assertEqual(row["confidence_reasons"], "")

    def test_owner_family_reverse_union_reported(self):
        rows, _, owner_family = self._rows()
        self.assertEqual(
            self._row(rows, 101)["owner_family_reverse_match_px"], "500")
        self.assertEqual(
            self._row(rows, 101)["owner_family_me_ids"], "101|102")
        self.assertEqual(
            self._row(rows, 101)["owner_family_member_count"], "2")
        self.assertEqual(
            self._row(rows, 101)["owner_family_live_member_count"], "2")

    def test_family_min_is_weakest_member_diagnostic(self):
        rows, _, _ = self._rows()
        self.assertEqual(self._row(rows, 101)["family_min_me_share"],
                         "0.970000")
        self.assertEqual(
            self._row(rows, 101)["owner_family_min_me_owner_share"],
            "0.970000")

    def test_threshold_boundary_just_below_is_review(self):
        rows, _, _ = self._rows(owner_shares=(0.949999, 0.97))
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertEqual(row["confidence_gate"], "fail")
        self.assertIn("me_owner_share_below_95",
                      row["confidence_reasons"])

    def test_threshold_boundary_exact_confirms(self):
        rows, _, _ = self._rows(owner_shares=(0.95, 0.95))
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "confirmed_95")
        self.assertEqual(row["confidence_score"], "0.950000")

    def test_owner_family_reverse_below_95_blocks_every_member(self):
        # 490 + 10 observed family hits over a 600 px same-owner mass.
        rows, _, _ = self._rows(v_owner_area=600)
        for mid in (101, 102):
            row = self._row(rows, mid)
            self.assertEqual(row["relation_status"], "review_candidate")
            self.assertIn("owner_family_reverse_share_below_95",
                          row["confidence_reasons"])
        self.assertEqual(
            self._row(rows, 101)["owner_family_reverse_share"],
            "0.833333")

    def test_asserted_family_member_included_in_union(self):
        m2v, v2m, pairs, edges, audit = _world()
        # mid 103 canonically belongs elsewhere but asserts a same-owner
        # relation to vanilla 1: asserted scope joins the owner family.
        m2v[103] = {"name": "Gamma", "owner": "FRA", "ancestor": 2,
                    "basis": "comment", "status": "mapped",
                    "synthetic": False}
        audit[103] = {"class": "no_action", "reason_codes": "",
                      "canonical_ancestor": "2"}
        pairs[(103, 1)] = _pair_entry(
            0.9, 100, 500, me_id="103", me_name="Gamma",
            is_canonical="0", canonical_ancestor="",
            source_scope="asserted", edge_source="comment",
            evidence_families="comment", comment_votes="2")
        edges[(103, 1)] = _edge_entry(
            me_id="103", me_name="Gamma",
            source_scope="asserted", edge_source="comment",
            evidence_families="comment", comment_votes="2",
            is_canonical="0", canonical_ancestor="",
            me_match_px="100", me_owner_match_px="100",
            me_owner_share="0.100000",
            v_match_px="100", v_owner_match_px="100",
            v_owner_area_px="600", v_owner_share="0.166667")
        # the asserted member adds observed mass: the vanilla same-owner
        # denominator is now 600 px and the union still covers it.
        for key in ((101, 1), (102, 1)):
            edges[key]["v_owner_area_px"] = 600
            edges[key]["row"]["v_owner_area_px"] = "600"
            edges[key]["row"]["v_owner_share"] = (
                f"{edges[key]['v_owner_match_px'] / 600:.6f}")
        votes = {(101, 1): 3, (102, 1): 1}
        rows, _, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, votes, audit, {})
        self.assertEqual(owner_family[1]["member_ids"], [101, 102, 103])
        self.assertEqual(owner_family[1]["reverse_match_px"], 600)
        row = self._row(rows, 101)
        self.assertIn("103", row["owner_family_me_ids"].split("|"))
        self.assertEqual(row["relation_status"], "confirmed_95")
        self.assertEqual(row["owner_family_reverse_share"], "1.000000")

    def test_observation_edges_never_expand_owner_family(self):
        m2v, v2m, pairs, edges, audit = _world()
        m2v[104] = {"name": "Delta", "owner": "FRA", "ancestor": 2,
                    "basis": "pixel-geo", "status": "mapped",
                    "synthetic": False}
        audit[104] = {"class": "no_action", "reason_codes": "",
                      "canonical_ancestor": "2"}
        pairs[(104, 1)] = _pair_entry(
            0.9, 60, 500, me_id="104", me_name="Delta",
            is_canonical="0", canonical_ancestor="",
            source_scope="observation", edge_source="raster",
            evidence_families="raster", comment_votes="")
        edges[(104, 1)] = _edge_entry(
            me_id="104", me_name="Delta",
            source_scope="observation", edge_source="raster",
            evidence_families="raster", comment_votes="",
            is_canonical="0", canonical_ancestor="",
            me_match_px="60", me_owner_match_px="60",
            me_owner_share="0.060000",
            v_match_px="60", v_owner_match_px="60",
            v_owner_share="0.120000")
        rows, _, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        self.assertEqual(owner_family[1]["member_ids"], [101, 102])
        self.assertEqual(owner_family[1]["reverse_match_px"], 500)
        # The observation member still shows up as its own pair row.
        obs = self._row(rows, 104)
        self.assertEqual(obs["relation_kind"], "candidate")
        self.assertEqual(obs["owner_family_member_count"], "2")

    def test_pair_confirmed_without_family_min(self):
        # Old primary-family minimum is diagnostic only: a weak member
        # mask share no longer vetoes pair-level confirmed_95.
        rows, _, _ = self._rows(member_shares=(0.99, 0.50))
        strong = self._row(rows, 101)
        weak = self._row(rows, 102)
        self.assertEqual(strong["relation_status"], "confirmed_95")
        self.assertEqual(weak["relation_status"], "confirmed_95")
        self.assertEqual(strong["family_min_me_share"], "0.500000")
        self.assertIn("family_member_below_95",
                      strong["confidence_reasons"])
        self.assertIn("me_share_below_95", weak["confidence_reasons"])

    def test_family_reverse_share_below_95_is_diagnostic_only(self):
        rows, _, _ = self._rows(member_hits=(460, 10),
                                v_owner_matches=(490, 10))
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "confirmed_95")
        self.assertEqual(row["family_reverse_share"], "0.940000")
        self.assertIn("family_reverse_share_below_95",
                      row["confidence_reasons"])

    def test_geometry_and_mask_evidence_are_diagnostics(self):
        m2v, v2m, pairs, edges, audit = _world(geometry_usable="0")
        pairs[(101, 1)]["me_share"] = None
        pairs[(101, 1)]["row"]["me_share"] = ""
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "confirmed_95")
        self.assertIn("geometry_not_usable", row["confidence_reasons"])
        self.assertIn("missing_mask_evidence",
                      row["confidence_reasons"])

    def test_missing_owner_edge_blocks_confirmation(self):
        m2v, v2m, pairs, edges, audit = _world()
        del edges[(101, 1)]
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("missing_owner_edge", row["confidence_reasons"])
        self.assertEqual(row["confidence_score"], "0.000000")
        self.assertEqual(row["me_owner_share"], "")

    def test_owner_denominator_unusable_blocks(self):
        m2v, v2m, pairs, edges, audit = _world()
        for key in ((101, 1), (102, 1)):
            edges[key]["me_owner_area_px"] = 0
            edges[key]["me_owner_match_px"] = 0
            edges[key]["me_owner_share"] = None
            edges[key]["row"]["me_owner_area_px"] = "0"
            edges[key]["row"]["me_owner_match_px"] = "0"
            edges[key]["row"]["me_owner_share"] = ""
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("owner_denominator_unusable",
                      row["confidence_reasons"])

    def test_v_owner_denominator_unusable_blocks(self):
        m2v, v2m, pairs, edges, audit = _world(v_owner_area=0)
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("owner_denominator_unusable",
                      row["confidence_reasons"])
        self.assertEqual(row["owner_family_reverse_share"], "")

    def test_context_only_audit_never_blocks(self):
        rows, _, _ = self._rows(audit_class="context_only",
                                audit_codes="overlap-weak|vp-absence-gap")
        for mid in (101, 102):
            row = self._row(rows, mid)
            self.assertEqual(row["relation_status"], "confirmed_95")
            self.assertEqual(row["mapping_audit_class"], "context_only")
            self.assertNotIn("mapping_audit_review_required",
                             row["confidence_reasons"])

    def test_comment_member_audit_reason_is_nonblocking(self):
        rows, _, _ = self._rows(audit_class="review_required",
                                audit_codes="comment(member)")
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "confirmed_95")
        self.assertNotIn("mapping_audit_review_required",
                         row["confidence_reasons"])

    def test_identity_risk_audit_reason_blocks(self):
        rows, _, _ = self._rows(
            audit_class="review_required",
            audit_codes="comment(member)|overlap-conflict")
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("mapping_audit_review_required",
                      row["confidence_reasons"])

    def test_clear_correction_audit_never_blocks(self):
        rows, _, _ = self._rows(
            audit_class="clear_correction",
            audit_codes="explicit-independent-correction")
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "confirmed_95")

    def test_noncanonical_candidate_never_confirmed(self):
        m2v, v2m, pairs, edges, audit = _world()
        # A perfect-coverage raster rival still stays review_candidate.
        pairs[(101, 2)] = _pair_entry(
            1.0, 500, 500, me_id="101", me_name="Alpha",
            vanilla_id="2", vanilla_name="Rival",
            is_canonical="0", canonical_ancestor="",
            source_scope="observation", edge_source="raster",
            evidence_families="raster", comment_votes="")
        edges[(101, 2)] = _edge_entry(
            me_id="101", me_name="Alpha",
            vanilla_id="2", vanilla_name="Rival",
            source_scope="observation", edge_source="raster",
            evidence_families="raster", comment_votes="",
            is_canonical="0", canonical_ancestor="",
            me_match_px="1000", me_owner_match_px="1000",
            me_owner_share="1.000000",
            v_match_px="500", v_owner_match_px="500",
            v_owner_share="1.000000")
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        rival = self._row(rows, 101, 2)
        self.assertEqual(rival["relation_kind"], "candidate")
        self.assertEqual(rival["relation_status"], "review_candidate")
        self.assertEqual(rival["confidence_gate"], "fail")
        self.assertIn("candidate_not_canonical",
                      rival["confidence_reasons"])
        # With no asserted family the owner reverse union is missing:
        # the perfect raster rival still scores 0 and never confirms.
        self.assertEqual(rival["confidence_score"], "0.000000")

    def test_synthetic_member_excluded_not_confirmed(self):
        m2v, v2m, pairs, edges, audit = _world(
            synthetic_member=(199, "Map Border", "MAP"))
        votes = {(101, 1): 3, (102, 1): 1}
        rows, family, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, votes, audit, {})
        syn = self._row(rows, 199)
        self.assertEqual(syn["relation_status"], "excluded_synthetic")
        self.assertEqual(syn["confidence_gate"], "excluded")
        self.assertIn("synthetic_state", syn["confidence_reasons"])
        # Synthetic members are recorded but leave every live denominator.
        self.assertTrue(family[1]["has_synthetic"])
        self.assertEqual(family[1]["live_count"], 2)
        self.assertEqual(family[1]["reverse_hit_px"], 500)
        live = self._row(rows, 101)
        self.assertEqual(live["family_has_synthetic_member"], "1")
        self.assertIn("family_has_synthetic_member",
                      live["confidence_reasons"])
        # A synthetic member vetoes confirmed_95 for the whole family.
        self.assertEqual(live["relation_status"], "review_candidate")
        self.assertEqual(live["confidence_gate"], "fail")

    def test_owner_family_synthetic_member_vetoes(self):
        m2v, v2m, pairs, edges, audit = _world()
        # A MAP-owned same-"owner" asserted member joins the family.
        m2v[199] = {"name": "Map Border", "owner": "MAP", "ancestor": 2,
                    "basis": "pixel-geo-low", "status": "mapped",
                    "synthetic": True}
        audit[199] = {"class": "no_action", "reason_codes": "",
                      "canonical_ancestor": "2"}
        pairs[(199, 1)] = _pair_entry(
            0.5, 4, 500, me_id="199", me_name="Map Border",
            me_owner="MAP", vanilla_owner="MAP",
            owner_relation="same_owner", country_scope="within_country",
            is_canonical="0", canonical_ancestor="",
            source_scope="asserted", edge_source="candidate|comment",
            evidence_families="comment", comment_votes="")
        edges[(199, 1)] = _edge_entry(
            me_id="199", me_name="Map Border", me_owner="MAP",
            vanilla_owner="MAP",
            source_scope="asserted", edge_source="candidate|comment",
            evidence_families="comment", comment_votes="",
            is_canonical="0", canonical_ancestor="",
            me_match_px="4", me_owner_match_px="4",
            me_owner_share="0.004000",
            v_match_px="4", v_owner_match_px="4",
            v_owner_share="0.008000")
        rows, _, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        self.assertTrue(owner_family[1]["has_synthetic"])
        self.assertEqual(owner_family[1]["live_count"], 2)
        live = self._row(rows, 101)
        self.assertEqual(live["owner_family_has_synthetic_member"], "1")
        self.assertIn("owner_family_has_synthetic_member",
                      live["confidence_reasons"])
        self.assertEqual(live["relation_status"], "review_candidate")

    def test_cross_owner_is_gate_and_annotation(self):
        rows, _, _ = self._rows(owner_relation="cross_owner")
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertEqual(row["owner_relation"], "cross_owner")
        self.assertIn("owner_mismatch", row["confidence_reasons"])

    def test_unknown_owner_reason(self):
        rows, _, _ = self._rows(owner_relation="unknown_owner")
        row = self._row(rows, 101)
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("owner_unknown", row["confidence_reasons"])

    def test_no_explicit_identity_never_confirms(self):
        m2v, v2m, pairs, edges, audit = _world()
        for rec in m2v.values():
            rec["basis"] = "pixel-geo"
        for entry in pairs.values():
            entry["row"]["comment_votes"] = ""
            entry["row"]["evidence_families"] = "canonical|pixel"
        rows, _, _ = conf.build_pairs(m2v, v2m, pairs, edges, {},
                                    audit, {})
        row = self._row(rows, 101)
        self.assertEqual(row["evidence_cap"], "0.800000")
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertIn("no_explicit_identity_evidence",
                      row["confidence_reasons"])
        # cap < 1.0 ceilings the score below the 0.95 gate.
        self.assertLess(float(row["confidence_score"]), 0.95)

    def test_canonical_comment_conflict(self):
        m2v, v2m, pairs, edges, audit = _world()
        # A comment edge citing a different vanilla ancestor is a conflict.
        pairs[(101, 2)] = _pair_entry(
            0.5, 100, 600, me_id="101", me_name="Alpha",
            vanilla_id="2", vanilla_name="Rival",
            is_canonical="0", canonical_ancestor="",
            source_scope="asserted", edge_source="comment",
            evidence_families="comment", comment_votes="2")
        votes = {(101, 1): 3, (102, 1): 1, (101, 2): 2}
        rows, _, _ = conf.build_pairs(m2v, v2m, pairs, edges,
                                    votes, audit, {})
        row = self._row(rows, 101)
        self.assertIn("canonical_comment_conflict",
                      row["confidence_reasons"])
        # The conflict vetoes confirmed_95 for the live canonical pair.
        self.assertEqual(row["relation_status"], "review_candidate")
        self.assertEqual(row["confidence_gate"], "fail")

    def test_reason_order_fixed_across_combined_flags(self):
        m2v, v2m, pairs, edges, audit = _world(
            owner_relation="cross_owner",
            audit_class="review_required",
            audit_codes="overlap-conflict",
            member_shares=(0.90, 0.97))
        # Drop explicit identity on 101 to add a late-order reason.
        m2v[101]["basis"] = "pixel-geo"
        pairs[(101, 1)]["row"]["comment_votes"] = ""
        pairs[(101, 1)]["row"]["evidence_families"] = "canonical|pixel"
        votes = {(101, 1): 3, (102, 1): 1}
        rows, _, _ = conf.build_pairs(m2v, v2m, pairs, edges,
                                    votes, audit, {})
        codes = self._row(rows, 101)["confidence_reasons"].split("|")
        self.assertEqual(
            codes,
            [c for c in EXPECTED_REASON_ORDER if c in set(codes)])
        for expected in ("me_share_below_95", "owner_mismatch",
                         "mapping_audit_review_required",
                         "no_explicit_identity_evidence"):
            self.assertIn(expected, codes)

    def test_rows_sorted_by_pair(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(101, 2)] = _pair_entry(
            0.5, 100, 600, me_id="101", me_name="Alpha",
            vanilla_id="2", vanilla_name="Rival",
            is_canonical="0", canonical_ancestor="",
            evidence_families="raster", comment_votes="")
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        keys = [(r["me_id"], r["vanilla_id"]) for r in rows]
        self.assertEqual(
            keys, sorted(keys, key=lambda kv: (int(kv[0]), int(kv[1]))))

    def test_fail_closed_on_missing_canonical_pair(self):
        m2v, v2m, pairs, edges, audit = _world()
        del pairs[(102, 1)]
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_missing_owner_family_pair(self):
        m2v, v2m, pairs, edges, audit = _world()
        # An asserted same-owner member with no mask-audit pair is an
        # input inconsistency, not a silent zero in the owner union.
        m2v[103] = {"name": "Gamma", "owner": "FRA", "ancestor": 2,
                    "basis": "comment", "status": "mapped",
                    "synthetic": False}
        audit[103] = {"class": "no_action", "reason_codes": "",
                      "canonical_ancestor": "2"}
        edges[(103, 1)] = _edge_entry(
            me_id="103", me_name="Gamma",
            source_scope="asserted", edge_source="comment",
            evidence_families="comment",
            is_canonical="0", canonical_ancestor="",
            me_match_px="50", me_owner_match_px="50",
            me_owner_share="0.050000",
            v_match_px="50", v_owner_match_px="50",
            v_owner_share="0.100000")
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_unknown_me(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(777, 1)] = _pair_entry(
            0.5, 10, 500, me_id="777", me_name="Ghost",
            is_canonical="0", canonical_ancestor="")
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_unknown_vanilla(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(101, 9)] = _pair_entry(
            0.5, 10, 500, me_id="101", me_name="Alpha",
            vanilla_id="9", vanilla_name="Ghost",
            is_canonical="0", canonical_ancestor="")
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_is_canonical_drift(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(101, 1)]["row"]["is_canonical"] = "0"
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_name_drift(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(101, 1)]["row"]["me_name"] = "Renamed"
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_owner_relation_drift(self):
        m2v, v2m, pairs, edges, audit = _world()
        pairs[(101, 1)]["row"]["owner_relation"] = "cross_owner"
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_fail_closed_on_owner_tag_drift(self):
        m2v, v2m, pairs, edges, audit = _world()
        edges[(101, 1)]["row"]["me_owner"] = "GER"
        with self.assertRaises(SystemExit):
            conf.build_pairs(m2v, v2m, pairs, edges, {}, audit, {})

    def test_project_ref_counts_joined(self):
        m2v, v2m, pairs, edges, audit = _world()
        votes = {(101, 1): 3, (102, 1): 1}
        refs = {(101, 1): (4, 2)}
        rows, _, _ = conf.build_pairs(m2v, v2m, pairs, edges,
                                    votes, audit, refs)
        row = self._row(rows, 101)
        self.assertEqual(row["project_ref_count"], "4")
        self.assertEqual(row["project_review_ref_count"], "2")


class BuildReferenceRowsTests(unittest.TestCase):
    """build_reference_rows over synthetic focus_state_refs rows."""

    def _index(self, **world_over):
        m2v, v2m, pairs, edges, audit = _world(**world_over)
        rows, _, _ = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        index = {(int(r["me_id"]), int(r["vanilla_id"])): r for r in rows}
        return m2v, index

    def test_confirmed_target_when_all_members_confirmed(self):
        m2v, index = self._index()
        out = conf.build_reference_rows(
            list(_ref_row().keys()), [_ref_row()], m2v, index)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["resolved_me_id"], "101|102")
        self.assertEqual(row["resolved_vanilla_id"], "1")
        self.assertEqual(row["reference_status"], "confirmed_target")
        self.assertEqual(row["review_reason"], "")
        self.assertEqual(row["relation_status"], "confirmed_95")

    def test_review_target_when_any_member_review(self):
        m2v, index = self._index(owner_shares=(0.99, 0.90))
        out = conf.build_reference_rows(
            list(_ref_row().keys()), [_ref_row()], m2v, index)
        row = out[0]
        self.assertEqual(row["reference_status"], "review_target")
        self.assertTrue(row["review_reason"])
        self.assertIn("review_candidate", row["relation_status"])
        # confidence_score is the minimum across resolved pairs.
        self.assertEqual(
            float(row["confidence_score"]),
            min(float(index[(m, 1)]["confidence_score"]) for m in (101, 102)))

    def test_unresolved_reference(self):
        m2v, index = self._index()
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(vanilla_id="9", current_me_refs="")],
            m2v, index)
        row = out[0]
        self.assertEqual(row["reference_status"], "unresolved")
        self.assertEqual(row["review_reason"], "unresolved_reference")
        self.assertEqual(row["confidence_score"], "")

    def test_missing_member_pair_is_unresolved(self):
        # A stale resolved member with no pair row must not be dropped
        # into a confirmed/review aggregate: fail closed to unresolved.
        m2v, index = self._index()
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(literal="", literal_is_live_me="0",
                      current_me_refs="101|150")],
            m2v, index)
        row = out[0]
        # All member ids are retained even though 150 has no pair row.
        self.assertEqual(row["resolved_me_id"], "101|150")
        self.assertEqual(row["reference_status"], "unresolved")
        self.assertEqual(row["review_reason"], "unresolved_reference")
        self.assertEqual(row["relation_status"], "")
        self.assertEqual(row["confidence_score"], "")

    def test_excluded_target_when_member_synthetic(self):
        m2v, index = self._index(
            synthetic_member=(199, "Map Border", "MAP"))
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(current_me_refs="101|199")],
            m2v, index)
        self.assertEqual(out[0]["reference_status"], "excluded_target")

    def test_live_literal_added_to_members(self):
        m2v, index = self._index()
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(literal="102", current_me_refs="101")],
            m2v, index)
        self.assertEqual(out[0]["resolved_me_id"], "101|102")

    def test_literal_not_in_m2v_not_added(self):
        m2v, index = self._index()
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(literal="777", current_me_refs="101")],
            m2v, index)
        self.assertEqual(out[0]["resolved_me_id"], "101")

    def test_foreign_literal_not_added(self):
        m2v, index = self._index()
        # literal maps to a different ancestor -> not a covering member.
        out = conf.build_reference_rows(
            list(_ref_row().keys()),
            [_ref_row(literal="102", vanilla_id="2",
                      current_me_refs="101")],
            m2v, index)
        self.assertEqual(out[0]["resolved_me_id"], "101")

    def test_review_reason_joins_member_reason_strings(self):
        m2v, index = self._index(owner_shares=(0.99, 0.90),
                                 owner_relation="cross_owner")
        out = conf.build_reference_rows(
            list(_ref_row().keys()), [_ref_row()], m2v, index)
        # review_reason joins each resolved member's own fixed-order
        # reason string; identical member strings collapse, differing
        # ones stay "|"-joined in member order.
        expected = "|".join(dict.fromkeys(
            index[(m, 1)]["confidence_reasons"] for m in (101, 102)))
        self.assertEqual(out[0]["review_reason"], expected)
        self.assertTrue(out[0]["review_reason"])


class BuildSummaryTests(unittest.TestCase):
    """build_summary emits the v2 schema document over synthetic rows."""

    def _summary(self):
        m2v, v2m, pairs, edges, audit = _world(owner_shares=(0.99, 0.90))
        votes = {(101, 1): 3, (102, 1): 1}
        pair_rows, family, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, votes, audit, {})
        index = {(int(r["me_id"]), int(r["vanilla_id"])): r
                 for r in pair_rows}
        ref_rows = conf.build_reference_rows(
            list(_ref_row().keys()), [_ref_row()], m2v, index)
        review_rows = [
            dict(r, review_priority=str(conf.review_priority(
                r["is_canonical"] == "1",
                float(r["evidence_cap"] or 0))))
            for r in pair_rows if r["relation_status"] == conf.STATUS_REVIEW]
        return conf.build_summary(
            {"m2v_states": {"path": "x", "sha256": "0" * 64, "rows": 2}},
            pair_rows, review_rows, ref_rows, family, owner_family)

    def test_schema_and_kind(self):
        summary = self._summary()
        self.assertEqual(summary["schema"],
                         "state_mapping_confirmation_summary/v2")
        self.assertIn("not calibrated probabilities", summary["kind"])

    def test_threshold_contract_echoed(self):
        contract = self._summary()["threshold_contract"]
        self.assertEqual(contract["confirmation_threshold"], 0.95)
        self.assertIn("min(evidence_cap", contract["confidence_score"])
        self.assertIn("me_owner_share", contract["confidence_score"])
        self.assertIn("owner_family_reverse_share",
                      contract["confidence_score"])
        self.assertIn("pair_level_only", contract)
        self.assertIn("complete-subset", contract["pair_level_only"])
        self.assertEqual(contract["evidence_caps"]["explicit_manual_or_comment"],
                         1.0)
        self.assertEqual(contract["evidence_caps"]["pixel_or_vp_only"], 0.80)
        self.assertEqual(contract["evidence_caps"]["name_only"], 0.65)
        self.assertEqual(contract["evidence_caps"]["none"], 0.50)
        self.assertEqual(contract["reason_code_order"],
                         EXPECTED_REASON_ORDER)
        self.assertTrue(any("non-canonical" in item
                            for item in contract["confirmed_95_requires"]))
        requires = " ".join(contract["confirmed_95_requires"])
        self.assertIn("canonical_comment_conflict", requires)
        self.assertIn("owner_family_reverse_share", requires)
        self.assertIn("me_owner_share", requires)

    def test_counts_histograms(self):
        summary = self._summary()
        counts = summary["counts"]
        self.assertEqual(counts["pairs_total"], 2)
        self.assertEqual(
            counts["by_relation_status"],
            {"confirmed_95": 1, "review_candidate": 1})
        self.assertEqual(counts["review_queue_rows"], 1)
        self.assertEqual(counts["vanilla_families_total"], 2)
        self.assertEqual(counts["families_with_synthetic_member"], 0)
        self.assertEqual(counts["owner_families_total"], 1)
        self.assertEqual(counts["owner_families_with_synthetic_member"], 0)
        self.assertEqual(counts["project_refs_total"], 1)
        self.assertEqual(
            counts["project_refs_by_status"], {"review_target": 1})
        for code in counts["by_reason_code"]:
            self.assertIn(code, EXPECTED_REASON_ORDER)

    def test_confirmed_family_counts(self):
        m2v, v2m, pairs, edges, audit = _world()
        pair_rows, family, owner_family = conf.build_pairs(
            m2v, v2m, pairs, edges, {(101, 1): 3, (102, 1): 1}, audit, {})
        summary = conf.build_summary(
            {}, pair_rows, [], [], family, owner_family)
        self.assertEqual(
            summary["counts"]["by_relation_status"],
            {"confirmed_95": 2})
        self.assertEqual(summary["counts"]["by_confidence_gate"],
                         {"pass": 2})
        self.assertEqual(summary["counts"]["review_queue_rows"], 0)


class ColumnSchemaTests(unittest.TestCase):
    """Output column lists stay fixed and review extends pairs."""

    def test_pair_columns_contract(self):
        for field in ("me_id", "vanilla_id", "relation_kind",
                      "relation_status", "me_share",
                      "family_min_me_share", "family_reverse_share",
                      "me_owner_area_px", "me_owner_match_px",
                      "me_owner_share", "v_owner_area_px",
                      "v_owner_match_px", "v_owner_share",
                      "owner_family_member_count", "owner_family_me_ids",
                      "owner_family_live_member_count",
                      "owner_family_has_synthetic_member",
                      "owner_family_min_me_owner_share",
                      "owner_family_reverse_match_px",
                      "owner_family_reverse_share",
                      "evidence_cap", "confidence_score",
                      "confidence_gate", "confidence_reasons"):
            self.assertIn(field, conf.PAIR_COLUMNS)
        self.assertEqual(len(conf.PAIR_COLUMNS),
                         len(set(conf.PAIR_COLUMNS)))

    def test_review_columns_extend_pairs(self):
        self.assertEqual(conf.REVIEW_COLUMNS[:-1], conf.PAIR_COLUMNS)
        self.assertEqual(conf.REVIEW_COLUMNS[-1], "review_priority")

    def test_ref_append_columns(self):
        self.assertEqual(
            conf.REF_APPEND_COLUMNS,
            ["resolved_me_id", "resolved_vanilla_id", "relation_status",
             "confidence_score", "reference_status", "review_reason"])


if __name__ == "__main__":
    unittest.main()
