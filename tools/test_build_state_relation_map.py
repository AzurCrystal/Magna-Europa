"""Pure synthetic tests for build_state_relation_map helpers.

No map rasters, no game/mod directories: every case uses in-memory
dictionaries and tiny synthetic rows. The heavy registration/census
build path is not exercised here.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_state_relation_map as brm  # noqa: E402


def _edge(me_id, vanilla_id, significant=1, confident=1000,
          uncertain=0, area=1000, status_ok=True,
          me_owner="GER", vanilla_owner="GER"):
    return {
        "me_id": me_id,
        "vanilla_id": vanilla_id,
        "me_owner_1936": me_owner,
        "vanilla_owner_1936": vanilla_owner,
        "significant": significant,
        "confident_hits_px": confident,
        "uncertain_hits_px": uncertain,
        "source_area_px": area,
        "component_status_ok": status_ok,
    }


def _identity(vid, mid, source="comment", strength="explicit", votes=""):
    return {
        "vanilla_id": vid, "me_id": mid, "source": source,
        "evidence_strength": strength, "comment_votes": votes,
        "claim": brm.CLAIM_IDENTITY,
    }


def _focus_row(**kw):
    row = {
        "side": "mod", "file": "a.txt", "line": "10",
        "focus_id": "F1", "tree_id": "", "block_path": "reward",
        "construct": "scalar_key", "key": "state", "raw_value": "5",
        "vanilla_id": "9", "quantifier": "direct", "literal": "5",
        "literal_is_live_me": "1", "resolution": "ok",
        "current_me_refs": "5", "me_member_count": "1",
        "me_members_ordered": "5", "covers": "full",
        "missing_members": "", "coverage_scope": "occurrence",
        "collision_live_me": "", "collision_me_ancestor": "",
        "comment_vanilla_id": "", "edge_ancestors": "",
        "observed_ancestors": "", "migration_comment": "",
    }
    row.update(kw)
    return row


def _write_tsv(rows, fieldnames):
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".tsv", delete=False, encoding="utf-8", newline="")
    writer = csv.DictWriter(tmp, fieldnames=fieldnames, delimiter="\t",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    tmp.close()
    return tmp.name


class TestNormName(unittest.TestCase):
    def test_strips_accents_punctuation(self):
        # Mirrors build_state_map.norm: NFKD + ascii drop; ligatures like
        # OE without an NFKD decomposition are dropped, not transliterated.
        self.assertEqual(brm.norm_name("Œuvre d'État!"), "uvredetat")
        self.assertEqual(brm.norm_name("Haute-Corse"), "hautecorse")
        self.assertEqual(brm.norm_name("São  Tomé"), "saotome")

    def test_empty_and_none(self):
        self.assertEqual(brm.norm_name(""), "")
        self.assertEqual(brm.norm_name(None), "")
        self.assertEqual(brm.norm_name("---"), "")


class TestStrictStateId(unittest.TestCase):
    def test_accepts_positive_int_and_digits(self):
        self.assertEqual(brm.strict_state_id("42", "x"), 42)
        self.assertEqual(brm.strict_state_id(7, "x"), 7)
        self.assertEqual(brm.strict_state_id(" 12 ", "x"), 12)

    def test_rejects_bad(self):
        for bad in ("", "0", "-3", "abc", "4.5", "+5", None, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                brm.strict_state_id(bad, "x")


class TestParseManualDicts(unittest.TestCase):
    def test_parses_literal_dicts(self):
        source = (
            "MANUAL = {\n"
            "  77: [1579, 818],  # comment\n"
            "  971: [767, 793],\n"
            "}\n"
            "MANUAL_ME2V = {\n"
            "  25: 144,\n"
            "  40: 100,\n"
            "}\n")
        manual, me2v = brm.parse_manual_dicts(source)
        self.assertEqual(manual, {77: [1579, 818], 971: [767, 793]})
        self.assertEqual(me2v, {25: 144, 40: 100})

    def test_duplicate_key_fails(self):
        source = "MANUAL = {1: [2], 1: [3]}\nMANUAL_ME2V = {}\n"
        with self.assertRaises(ValueError):
            brm.parse_manual_dicts(source)

    def test_non_literal_fails(self):
        source = "x = 1\nMANUAL = {1: x}\nMANUAL_ME2V = {}\n"
        with self.assertRaises(ValueError):
            brm.parse_manual_dicts(source)

    def test_missing_dict_fails(self):
        with self.assertRaises(ValueError):
            brm.parse_manual_dicts("MANUAL = {}\n")

    def test_real_file_parses(self):
        # The real dicts must stay AST-parseable literals.
        text = (TOOLS / "build_state_map.py").read_text(encoding="utf-8")
        manual, me2v = brm.parse_manual_dicts(text)
        self.assertIn(77, manual)
        self.assertIn(25, me2v)


class TestCommentEdges(unittest.TestCase):
    FIELDNAMES = ["vanilla_id", "me_id", "comment_votes"]

    def _parse(self, rows, v_ids, me_ids):
        path = _write_tsv(rows, self.FIELDNAMES)
        return brm.read_comment_edges(path, v_ids, me_ids)

    def test_parses_rows(self):
        rows = self._parse(
            [{"vanilla_id": "1", "me_id": "1422", "comment_votes": "9"}],
            {1}, {1422})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vanilla_id"], 1)
        self.assertEqual(rows[0]["me_id"], 1422)
        self.assertEqual(rows[0]["comment_votes"], 9)
        self.assertEqual(rows[0]["claim"], brm.CLAIM_IDENTITY)

    def test_malformed_id_fails(self):
        with self.assertRaises(ValueError):
            self._parse(
                [{"vanilla_id": "x", "me_id": "1", "comment_votes": "1"}],
                {1}, {1})

    def test_unknown_endpoint_fails(self):
        with self.assertRaises(ValueError):
            self._parse(
                [{"vanilla_id": "2", "me_id": "1", "comment_votes": "1"}],
                {1}, {1})
        with self.assertRaises(ValueError):
            self._parse(
                [{"vanilla_id": "1", "me_id": "2", "comment_votes": "1"}],
                {1}, {1})


class TestManualIdentityRows(unittest.TestCase):
    def test_directions(self):
        rows = brm.manual_identity_rows(
            {77: [1579, 818]}, {25: 144}, {77, 144}, {25, 818, 1579})
        member = [r for r in rows if r["source"] == "manual_member"]
        direct = [r for r in rows if r["source"] == "manual_direct"]
        self.assertEqual(
            {(r["vanilla_id"], r["me_id"]) for r in member},
            {(77, 1579), (77, 818)})
        self.assertEqual(
            [(r["vanilla_id"], r["me_id"]) for r in direct],
            [(144, 25)])
        for row in rows:
            self.assertEqual(row["evidence_strength"], "explicit")

    def test_unknown_endpoint_fails(self):
        with self.assertRaises(ValueError):
            brm.manual_identity_rows({77: [9999]}, {}, {77}, {9998})
        with self.assertRaises(ValueError):
            brm.manual_identity_rows({}, {1: 9999}, {1}, {1})

    def test_duplicate_member_fails(self):
        with self.assertRaises(ValueError):
            brm.manual_identity_rows({77: [5, 5]}, {}, {77}, {5})


class TestNameIdentityRows(unittest.TestCase):
    def test_unique_exact_pair(self):
        rows = brm.name_identity_rows(
            {1: "Corsica", 2: "Lazio"}, {10: "Corsica"})
        self.assertEqual(
            [(r["vanilla_id"], r["me_id"], r["source"]) for r in rows],
            [(1, 10, "name")])

    def test_collision_suppresses(self):
        rows = brm.name_identity_rows(
            {1: "Corsica"}, {10: "Corsica", 11: "Corsica"})
        self.assertEqual([r for r in rows if r["source"] == "name"], [])

    def test_same_id_name(self):
        rows = brm.name_identity_rows(
            {5: "Tyrol"}, {5: "tyrol"})
        self.assertEqual(
            [(r["vanilla_id"], r["me_id"], r["source"]) for r in rows],
            [(5, 5, "same_id_name")])

    def test_same_id_different_name_no_row(self):
        rows = brm.name_identity_rows({5: "Tyrol"}, {5: "Tirol"})
        self.assertEqual(rows, [])


class TestDedupeIdentity(unittest.TestCase):
    def test_dedupes_same_triple(self):
        rows = brm.dedupe_identity([
            _identity(1, 2, "comment", votes="3"),
            _identity(1, 2, "comment", votes="3"),
            _identity(1, 2, "manual_member"),
            _identity(1, 3, "comment"),
        ])
        self.assertEqual(
            [(r["vanilla_id"], r["me_id"], r["source"]) for r in rows],
            [(1, 2, "comment"), (1, 2, "manual_member"),
             (1, 3, "comment")])

    def test_conflicting_duplicate_fails(self):
        with self.assertRaises(ValueError):
            brm.dedupe_identity([
                _identity(1, 2, "comment", votes="3"),
                _identity(1, 2, "comment", votes="4"),
            ])


class TestFocusSubstitutions(unittest.TestCase):
    FIELDNAMES = [
        "side", "file", "line", "focus_id", "tree_id", "block_path",
        "construct", "key", "raw_value", "vanilla_id", "quantifier",
        "literal", "literal_is_live_me", "resolution", "current_me_refs",
        "me_member_count", "me_members_ordered", "covers",
        "missing_members", "coverage_scope", "collision_live_me",
        "collision_me_ancestor", "comment_vanilla_id", "edge_ancestors",
        "observed_ancestors", "migration_comment",
    ]

    def _parse(self, rows, v_ids, me_ids):
        path = _write_tsv(rows, self.FIELDNAMES)
        return brm.read_focus_substitutions(path, v_ids, me_ids)

    def test_live_literal_edge(self):
        rows = self._parse([_focus_row()], {9}, {5})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["vanilla_id"], row["me_id"]), (9, 5))
        self.assertEqual(row["ref_kind"], "literal")
        self.assertEqual(row["source"], "focus_script")
        self.assertEqual(row["claim"], brm.CLAIM_SUBSTITUTION)
        self.assertEqual(row["file"], "a.txt")
        self.assertEqual(row["line"], "10")

    def test_current_refs_expand(self):
        rows = self._parse(
            [_focus_row(literal_is_live_me="", current_me_refs="5|7",
                        literal="5")],
            {9}, {5, 7})
        self.assertEqual(
            sorted((r["me_id"], r["ref_kind"]) for r in rows),
            [(5, "current_ref"), (7, "current_ref")])

    def test_vanilla_side_skipped(self):
        rows = self._parse([_focus_row(side="vanilla")], {9}, {5})
        self.assertEqual(rows, [])

    def test_empty_vanilla_id_skipped(self):
        rows = self._parse(
            [_focus_row(vanilla_id="", literal_is_live_me="",
                        current_me_refs="")],
            {9}, {5})
        self.assertEqual(rows, [])

    def test_comment_claim_emits_extra_edge(self):
        rows = self._parse(
            [_focus_row(comment_vanilla_id="77")], {9, 77}, {5})
        self.assertEqual(
            sorted((r["vanilla_id"], r["source"]) for r in rows),
            [(9, "focus_script"), (77, "focus_comment_claim")])

    def test_same_comment_vid_no_extra(self):
        rows = self._parse(
            [_focus_row(comment_vanilla_id="9")], {9}, {5})
        self.assertEqual(len(rows), 1)

    def test_malformed_vanilla_id_fails(self):
        with self.assertRaises(ValueError):
            self._parse([_focus_row(vanilla_id="x")], {9}, {5})

    def test_unknown_me_ref_fails(self):
        with self.assertRaises(ValueError):
            self._parse(
                [_focus_row(current_me_refs="5|999",
                            literal_is_live_me="")],
                {9}, {5})

    def test_unknown_comment_vid_fails(self):
        with self.assertRaises(ValueError):
            self._parse([_focus_row(comment_vanilla_id="55")], {9}, {5})

    def test_no_me_target_skipped(self):
        rows = self._parse(
            [_focus_row(literal_is_live_me="0", current_me_refs="")],
            {9}, {5})
        self.assertEqual(rows, [])


class TestAggregateEdges(unittest.TestCase):
    def _comp(self, cid=0, status="registered", metrics=None,
              method="local_affine"):
        return {
            "component_id": cid, "status": status,
            "registration": {"registration_method": method},
            "metrics": metrics or {},
        }

    def _row(self, sid, tid, conf=100, unc=0, reverse=None):
        if reverse is None:
            reverse = conf
        return {
            "source_id": sid, "target_id": tid,
            "confident_hits_px": conf, "uncertain_hits_px": unc,
            "source_area_px": 500, "source_sampled_px": 250,
            "target_native_area_px": 900,
            "reverse_hits_px": reverse,
        }

    def test_merges_components(self):
        comp_a = self._comp(0)
        comp_b = self._comp(1)
        merged = brm.aggregate_edges([
            (self._row(5, 9, 100), comp_a),
            (self._row(5, 9, 50), comp_b),
        ])
        acc = merged[(5, 9)]
        self.assertEqual(acc["confident_hits_px"], 150)
        self.assertEqual(acc["reverse_hits_px"], 150)
        self.assertEqual(acc["source_sampled_px"], 500)
        self.assertEqual(acc["component_ids"], {0, 1})
        self.assertEqual(acc["statuses"], {"registered"})

    def test_flags_propagate(self):
        merged = brm.aggregate_edges([
            (self._row(5, 9),
             self._comp(0, "global_fallback",
                        metrics={"fallback_reason": "under-powered",
                                 "natural_fallback_reason": "no pairs"})),
            (self._row(5, 9),
             self._comp(1, "unmatched_component",
                        metrics={"local_residual_p95_px": 20.0})),
        ])
        self.assertEqual(
            merged[(5, 9)]["flags"],
            {"unmatched_component", "local_fallback",
             "natural_fallback", "high_residual"})


class TestEdgeFlagColumn(unittest.TestCase):
    def _acc(self, conf=100, unc=0, flags=(), reverse=100, native=200):
        return {
            "confident_hits_px": conf, "uncertain_hits_px": unc,
            "reverse_hits_px": reverse, "target_native_area_px": native,
            "flags": set(flags),
        }

    def test_cross_owner(self):
        self.assertEqual(
            brm.edge_flag_column(self._acc(), "GER", "ITA"),
            "cross_owner")
        self.assertEqual(brm.edge_flag_column(self._acc(), "GER", "GER"), "")
        self.assertEqual(brm.edge_flag_column(self._acc(), "", "ITA"), "")

    def test_uncertain_flags(self):
        self.assertIn("uncertain_dominant",
                      brm.edge_flag_column(self._acc(conf=10, unc=50),
                                           "GER", "GER"))
        self.assertIn("uncertain_only",
                      brm.edge_flag_column(self._acc(conf=0, unc=50),
                                           "GER", "GER"))

    def test_reverse_overlap_possible_flag(self):
        # Merged unique-target-pixel reverse hits above the whole native
        # area mean component registrations overlapped: flag it, never
        # let a ratio > 1 reach the TSV.
        self.assertIn(
            "reverse_overlap_possible",
            brm.edge_flag_column(
                self._acc(reverse=250, native=200), "GER", "GER"))
        self.assertNotIn(
            "reverse_overlap_possible",
            brm.edge_flag_column(
                self._acc(reverse=200, native=200), "GER", "GER"))


class TestDeriveView(unittest.TestCase):
    def test_unique_area_parent_wins(self):
        derived, source, status = brm.derive_view(
            [_edge(5, 9)], [7], [8])
        self.assertEqual((derived, source, status), (9, "area_parent", "derived"))

    def test_ambiguous_area_review(self):
        derived, source, status = brm.derive_view(
            [_edge(5, 9), _edge(5, 10)], [7], [8])
        self.assertEqual((derived, source, status),
                         (None, "area_parent", "review"))

    def test_below_threshold_falls_to_identity(self):
        rows = [_edge(5, 9, confident=500, area=1000)]
        derived, source, status = brm.derive_view(rows, [7], [8])
        self.assertEqual((derived, source, status),
                         (7, "identity", "derived"))

    def test_excluded_component_not_area(self):
        rows = [_edge(5, 9, status_ok=False)]
        derived, source, status = brm.derive_view(rows, [7], [8])
        self.assertEqual((derived, source, status),
                         (7, "identity", "derived"))

    def test_cross_owner_area_never_derives(self):
        # A high-confidence edge across different owners is diagnostics
        # only; it can never drive the derived compatibility ancestor.
        rows = [_edge(5, 9, me_owner="GER", vanilla_owner="ITA")]
        derived, source, status = brm.derive_view(rows, [7], [8])
        self.assertEqual((derived, source, status),
                         (7, "identity", "derived"))

    def test_unknown_owner_area_never_derives(self):
        # Missing owner data is not "same owner": it also stays a
        # diagnostic edge and falls through to identity.
        rows = [_edge(5, 9, me_owner="", vanilla_owner="GER")]
        derived, source, status = brm.derive_view(rows, [7], [8])
        self.assertEqual((derived, source, status),
                         (7, "identity", "derived"))

    def test_uncertain_share_blocks_area(self):
        rows = [_edge(5, 9, confident=960, uncertain=100, area=1000)]
        derived, source, status = brm.derive_view(rows, [7], [8])
        self.assertEqual((derived, source, status),
                         (7, "identity", "derived"))

    def test_identity_ambiguity(self):
        self.assertEqual(
            brm.derive_view([], [7, 8], [9]),
            (None, "identity", "review"))

    def test_unique_substitution(self):
        self.assertEqual(
            brm.derive_view([], [], [9]),
            (9, "substitution", "derived"))

    def test_substitution_ambiguity(self):
        self.assertEqual(
            brm.derive_view([], [], [9, 10]),
            (None, "substitution", "review"))

    def test_candidates_but_none_unique_review(self):
        self.assertEqual(
            brm.derive_view([], [7, 8], []),
            (None, "identity", "review"))
        self.assertEqual(
            brm.derive_view([_edge(5, 9, significant=0)], [], []),
            (None, "none", "review"))

    def test_no_candidates_unmapped(self):
        self.assertEqual(
            brm.derive_view([], [], []),
            (None, "none", "unmapped"))


class TestAreaParentIds(unittest.TestCase):
    def test_order_by_confident_share_then_id(self):
        rows = [
            _edge(5, 9, confident=400, area=1000),
            _edge(5, 10, confident=500, area=1000),
            _edge(5, 11, confident=500, area=1000),
        ]
        self.assertEqual(brm.area_parent_ids(rows)[5], [10, 11, 9])


class TestBuildViewRows(unittest.TestCase):
    def test_columns_and_derived(self):
        edge_rows = [_edge(5, 9)]
        identity = [_identity(9, 5, "comment")]
        subs = [{"me_id": 5, "vanilla_id": 9}]
        rows = brm.build_view_rows(
            {5, 6}, {5: "Alpha", 6: "Beta"},
            {5: "GER", 6: "ITA"}, edge_rows, identity, subs)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["me_id"], 5)
        self.assertEqual(first["derived_v_ancestor"], "9")
        self.assertEqual(first["area_parent_ids"], "9")
        self.assertEqual(first["identity_ids"], "9")
        self.assertEqual(first["substitution_ids"], "9")
        self.assertEqual(first["status"], "derived")
        self.assertEqual(first["claim"], brm.CLAIM_VIEW)
        second = rows[1]
        self.assertEqual(second["derived_v_ancestor"], "")
        self.assertEqual(second["status"], "unmapped")

    def test_weak_identity_not_derived(self):
        identity = [_identity(7, 5, "name", strength="weak")]
        rows = brm.build_view_rows(
            {5}, {5: "Alpha"}, {5: "GER"}, [], identity, [])
        row = rows[0]
        # Weak evidence is listed but cannot drive the derived ancestor.
        self.assertEqual(row["identity_ids"], "7")
        self.assertEqual(row["derived_v_ancestor"], "")
        self.assertEqual(row["status"], "review")


class TestBuildV2mRows(unittest.TestCase):
    def test_members(self):
        view_rows = [
            {"me_id": 5, "derived_v_ancestor": "9",
             "view_source": "area_parent"},
            {"me_id": 6, "derived_v_ancestor": "9",
             "view_source": "identity"},
            {"me_id": 7, "derived_v_ancestor": "",
             "view_source": "none"},
        ]
        rows = brm.build_v2m_rows(
            {9, 10}, {9: "Nine", 10: "Ten"},
            {9: "GER", 10: "ITA"}, view_rows)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["derived_members"], "5|6")
        self.assertEqual(first["primary_me_member"], "5")
        self.assertEqual(first["view_source"], "area_parent|identity")
        self.assertEqual(first["status"], "derived")
        self.assertEqual(rows[1]["status"], "unmapped")


class TestCommentSubstitutionRows(unittest.TestCase):
    def test_rows(self):
        rows = brm.comment_substitution_rows(
            [_identity(9, 5, "comment")])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["vanilla_id"], row["me_id"]), (9, 5))
        self.assertEqual(row["source"], "comment")
        self.assertEqual(row["ref_kind"], "comment_edge")
        self.assertEqual(row["claim"], brm.CLAIM_SUBSTITUTION)


class TestNoProofInvariants(unittest.TestCase):
    def test_no_proven_labels_anywhere(self):
        # The contract forbids proven/complete-subset claims; the module
        # constants and column sets must not introduce them.
        self.assertNotIn("proven", brm.CLAIM_AREA)
        self.assertNotIn("proof", brm.CLAIM_IDENTITY)
        self.assertNotIn("proof", brm.CLAIM_SUBSTITUTION)
        self.assertIn("candidate", brm.CLAIM_IDENTITY)
        self.assertEqual(brm.EDGE_COLUMNS[-1], "diagnostic_flags")
        self.assertIn("complete_subset", brm.EDGE_COLUMNS)
        self.assertIn("reverse_hits_px", brm.EDGE_COLUMNS)
        self.assertIn("reverse_share", brm.EDGE_COLUMNS)
        self.assertIn("reverse_overlap_possible", brm.EDGE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
