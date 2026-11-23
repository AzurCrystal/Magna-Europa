#!/usr/bin/env python
"""Small function-level smoke checks for optional geography audit evidence."""

from pathlib import Path
import unittest

try:
    from tools.audit_state_mapping import (
        AuditInputError,
        OVERLAP_COLUMNS,
        audit_rows,
        evaluate_row,
        overlap_candidate_ids,
        overlap_quality_ok,
        reason_counter,
        validate_geography_rows,
        validate_overlap_row,
    )
except ModuleNotFoundError:  # direct ``python tools/test_*.py`` invocation
    from audit_state_mapping import (
        AuditInputError,
        OVERLAP_COLUMNS,
        audit_rows,
        evaluate_row,
        overlap_candidate_ids,
        overlap_quality_ok,
        reason_counter,
        validate_geography_rows,
        validate_overlap_row,
    )


class GeographyAuditSmoke(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "me_id": "1",
            "me_name": "One",
            "ancestor": "1",
            "ancestor_name": "One",
            "basis": "comment",
            "status": "mapped",
            "manual_override": "false",
        }
        self.canonical = {
            "me_id": "1",
            "me_name": "One",
            "v_ancestor": "1",
            "v_name": "One",
            "basis": "comment",
            "status": "mapped",
        }

    def geography(self, candidate="1", marker="coast_bbox_icp;river_pairs=10;lake_pairs=0"):
        return {
            "me_id": "1",
            "me_name": "One",
            "natural_ok": "1",
            "natural_method": "coast_bbox_icp;local_river_lake_translation",
            "natural_pairs": "10",
            "natural_residual_p50": "1.000000",
            "natural_residual_p95": "2.000000",
            "natural_best": candidate,
            "natural_share": "0.900000",
            "natural_runner": "",
            "natural_runner_share": "",
            "natural_margin": "0.900000",
            "raw_geography_ancestor": candidate,
            "raw_geography_marker": marker,
        }

    def overlap_geography(self, candidate="1", **changes):
        row = self.geography()
        row.update(
            overlap_ok="1",
            overlap_method="coast_bbox_affine_raster",
            overlap_domain="me_native_land",
            overlap_area_px="100",
            overlap_best=candidate,
            overlap_share="0.900000",
            overlap_runner="2" if candidate == "1" else "1",
            overlap_runner_share="0.050000",
            overlap_margin="0.850000",
            overlap_assigned_share="0.950000",
            overlap_containment="0.600000",
            overlap_components="1",
            overlap_largest_share="1.000000",
            overlap_largest_best=candidate,
            overlap_largest_candidate_share="0.900000",
            overlap_components_agree="1",
            overlap_unregistered_share="0.050000",
            overlap_ancestor=candidate,
            overlap_marker="coast_bbox_affine_raster_overlap",
            overlap_reason="accepted",
        )
        row.update(changes)
        return row

    def withheld_overlap(self, **changes):
        row = self.overlap_geography(
            candidate="2",
            overlap_ok="0",
            overlap_area_px="63",
            overlap_ancestor="",
            overlap_marker="",
            overlap_reason="insufficient_area",
        )
        row.update(changes)
        return row

    def validate_geography(self, row):
        return validate_geography_rows([row], list(row), Path("geography_fixture.tsv"))

    def audit_one(self, geography=None):
        return evaluate_row(
            1,
            self.evidence,
            self.canonical,
            {},
            {},
            {},
            (),
            geography,
        )

    def test_missing_optional_keeps_legacy_row_schema(self):
        rows = audit_rows([self.evidence], {1: self.canonical}, {}, {}, {})
        self.assertNotIn("geography_best", rows[0])
        self.assertNotIn("raw-geography-weak", rows[0]["reason_codes"])

    def test_agreement_is_retained_without_correction(self):
        row = self.audit_one(self.geography())
        self.assertEqual(row["geography_best"], "1")
        self.assertNotIn("raw-geography-conflict", row["reason_codes"])
        self.assertEqual(row["class"], "no_action")

    def test_conflict_requires_review_and_keeps_current_ancestor(self):
        row = self.audit_one(self.geography(candidate="2"))
        self.assertEqual(row["ancestor"], "1")
        self.assertIn("raw-geography-conflict", row["reason_codes"])
        self.assertEqual(row["class"], "review_required")

    def test_unsafe_marker_requires_review(self):
        row = self.audit_one(self.geography(marker="v2m_states"))
        self.assertIn("raw-geography-invalid", row["reason_codes"])
        self.assertEqual(row["class"], "review_required")

    def test_overlap_agreement_retains_all_diagnostics_without_correction(self):
        geography = self.overlap_geography()
        self.assertEqual(len(OVERLAP_COLUMNS), 20)
        self.assertEqual(
            set(geography) - set(self.geography()), set(OVERLAP_COLUMNS)
        )
        row = self.audit_one(geography)
        self.assertEqual(row["ancestor"], "1")
        self.assertEqual(row["class"], "no_action")
        self.assertFalse(any(reason.startswith("overlap-") for reason in row["reason_codes"].split("|")))
        for field in OVERLAP_COLUMNS:
            with self.subTest(field=field):
                self.assertEqual(row["geography_" + field], geography[field])
                self.assertNotIn(field, row)
        self.assertEqual(row["raw_geography_marker"], self.geography()["raw_geography_marker"])
        self.assertNotEqual(row["raw_geography_marker"], row["geography_overlap_marker"])

    def test_accepted_overlap_conflict_requires_review_without_changing_ancestor(self):
        row = self.audit_one(self.overlap_geography(candidate="2"))
        self.assertEqual(row["ancestor"], "1")
        self.assertEqual(row["canonical_ancestor"], "1")
        self.assertEqual(row["geography_overlap_ancestor"], "2")
        self.assertIn("overlap-conflict", row["reason_codes"].split("|"))
        self.assertNotIn("raw-geography-conflict", row["reason_codes"].split("|"))
        self.assertEqual(row["class"], "review_required")

    def test_overlap_conflict_compares_canonical_even_when_evidence_differs(self):
        self.evidence.update(ancestor="2", ancestor_name="Two")
        for candidate, conflict in (("2", True), ("1", False)):
            with self.subTest(candidate=candidate):
                geography = self.overlap_geography(candidate=candidate)
                geography.update(self.geography(candidate="2"))
                row = self.audit_one(geography)
                reasons = row["reason_codes"].split("|")
                self.assertEqual(row["ancestor"], "2")
                self.assertEqual(row["canonical_ancestor"], "1")
                self.assertIn("canonical-evidence-mismatch", reasons)
                self.assertEqual("overlap-conflict" in reasons, conflict)
                self.assertNotIn("raw-geography-conflict", reasons)
                self.assertEqual(row["class"], "review_required")

    def test_withheld_overlap_best_is_context_not_an_accepted_candidate(self):
        geography = self.withheld_overlap()
        self.assertEqual(self.validate_geography(geography), {1: geography})
        self.assertEqual(overlap_candidate_ids(geography), ())
        self.assertFalse(overlap_quality_ok(geography))
        row = self.audit_one(geography)
        self.assertEqual(row["geography_overlap_best"], "2")
        self.assertEqual(row["geography_overlap_ancestor"], "")
        self.assertEqual(row["geography_overlap_reason"], "insufficient_area")
        self.assertIn("overlap-weak", row["reason_codes"].split("|"))
        self.assertNotIn("overlap-conflict", row["reason_codes"].split("|"))
        self.assertEqual(row["class"], "context_only")
        self.assertEqual(row["ancestor"], "1")

    def test_component_disagreement_requires_review_and_retains_diagnostics(self):
        geography = self.withheld_overlap(
            overlap_area_px="100",
            overlap_components="2",
            overlap_largest_share="0.900000",
            overlap_largest_candidate_share="1.000000",
            overlap_components_agree="0",
            overlap_reason="component_disagreement",
        )
        self.assertEqual(self.validate_geography(geography), {1: geography})
        self.assertEqual(overlap_candidate_ids(geography), ())
        row = self.audit_one(geography)
        self.assertIn("overlap-component-disagreement", row["reason_codes"].split("|"))
        self.assertNotIn("overlap-conflict", row["reason_codes"].split("|"))
        self.assertEqual(row["class"], "review_required")
        for field in OVERLAP_COLUMNS:
            self.assertEqual(row["geography_" + field], geography[field])

    def test_malformed_direct_overlap_row_fails_closed_in_audit(self):
        malformed = [
            self.overlap_geography(overlap_ancestor=""),
            self.overlap_geography(overlap_marker="v2m_states"),
            self.overlap_geography(overlap_share="nan"),
        ]
        partial = self.overlap_geography()
        partial.pop("overlap_method")
        malformed.append(partial)
        for geography in malformed:
            with self.subTest(geography=geography):
                row = self.audit_one(geography)
                self.assertIn("overlap-invalid", row["reason_codes"].split("|"))
                self.assertEqual(row["class"], "review_required")
                self.assertEqual(row["ancestor"], "1")
                self.assertEqual(row["geography_overlap_best"], "1")
                self.assertEqual(overlap_candidate_ids(geography), ())
                self.assertFalse(overlap_quality_ok(geography))

    def test_geography_validation_accepts_legacy_and_full_overlap_schemas(self):
        for geography in (self.geography(), self.overlap_geography()):
            with self.subTest(columns=list(geography)):
                self.assertEqual(self.validate_geography(geography), {1: geography})
                validate_overlap_row(geography)
        legacy = self.audit_one(self.geography())
        self.assertNotIn("geography_overlap_best", legacy)
        self.assertEqual(overlap_candidate_ids(self.geography()), ())
        self.assertFalse(overlap_quality_ok(self.geography()))

    def test_allblank_overlap_extension_is_optional_unavailable_evidence(self):
        geography = self.geography()
        geography.update(dict.fromkeys(OVERLAP_COLUMNS, ""))
        self.assertEqual(self.validate_geography(geography), {1: geography})
        validate_overlap_row(geography)
        self.assertEqual(overlap_candidate_ids(geography), ())
        self.assertFalse(overlap_quality_ok(geography))
        row = self.audit_one(geography)
        self.assertEqual(row["class"], "no_action")
        self.assertFalse(any(reason.startswith("overlap-") for reason in row["reason_codes"].split("|")))
        for field in OVERLAP_COLUMNS:
            self.assertEqual(row["geography_" + field], "")

    def test_partial_overlap_schema_is_rejected_even_without_data_rows(self):
        for field in OVERLAP_COLUMNS:
            geography = self.overlap_geography()
            geography.pop(field)
            with self.subTest(missing=field):
                with self.assertRaises(AuditInputError):
                    self.validate_geography(geography)
                with self.assertRaises(AuditInputError):
                    validate_overlap_row(geography)
                with self.assertRaises(AuditInputError):
                    validate_geography_rows([], list(geography), Path("geography_fixture.tsv"))

    def test_overlap_candidate_extraction_never_falls_back_to_best(self):
        accepted = self.overlap_geography(candidate="2")
        self.assertEqual(overlap_candidate_ids(accepted), (2,))
        self.assertTrue(overlap_quality_ok(accepted))
        for geography in (
            self.withheld_overlap(),
            self.overlap_geography(candidate="2", overlap_ancestor=""),
            self.overlap_geography(candidate="2", overlap_ancestor="1"),
        ):
            with self.subTest(geography=geography):
                self.assertEqual(overlap_candidate_ids(geography), ())
                self.assertFalse(overlap_quality_ok(geography))

    def test_absent_runner_accepts_blank_or_zero_share_and_zero_id_sentinel(self):
        for runner in ("", "0"):
            for runner_share in ("", "0", "0.000000"):
                with self.subTest(runner=runner, runner_share=runner_share):
                    geography = self.overlap_geography(
                        overlap_runner=runner,
                        overlap_runner_share=runner_share,
                        overlap_share="0.950000",
                        overlap_margin="0.950000",
                        overlap_largest_candidate_share="0.950000",
                    )
                    self.assertEqual(self.validate_geography(geography), {1: geography})
                    self.assertEqual(overlap_candidate_ids(geography), (1,))
                    self.assertTrue(overlap_quality_ok(geography))
                    self.assertEqual(self.audit_one(geography)["class"], "no_action")

    def test_unassigned_withheld_row_accepts_absent_candidate_metrics(self):
        geography = self.withheld_overlap(
            overlap_area_px="100",
            overlap_best="",
            overlap_share="0.000000",
            overlap_runner="",
            overlap_runner_share="0.000000",
            overlap_margin="0.000000",
            overlap_assigned_share="0.000000",
            overlap_containment="",
            overlap_largest_best="",
            overlap_largest_candidate_share="0.000000",
            overlap_components_agree="0",
            overlap_unregistered_share="1.000000",
            overlap_reason="low_assigned_share",
        )
        self.assertEqual(self.validate_geography(geography), {1: geography})
        self.assertEqual(overlap_candidate_ids(geography), ())
        row = self.audit_one(geography)
        self.assertEqual(row["class"], "context_only")
        self.assertIn("overlap-weak", row["reason_codes"].split("|"))
        self.assertNotIn("overlap-invalid", row["reason_codes"].split("|"))

    def test_overlap_validation_rejects_malformed_ids(self):
        for field in ("overlap_best", "overlap_ancestor", "overlap_largest_best", "overlap_runner"):
            invalid_values = ("word", "-1", "1.5")
            if field != "overlap_runner":
                invalid_values += ("0",)
            for raw in invalid_values:
                with self.subTest(field=field, value=raw):
                    geography = self.overlap_geography(**{field: raw})
                    with self.assertRaises(AuditInputError):
                        self.validate_geography(geography)
                    self.assertEqual(overlap_candidate_ids(geography), ())

    def test_overlap_validation_rejects_nonfinite_or_out_of_range_ratios(self):
        fields = (
            "overlap_share", "overlap_runner_share", "overlap_margin",
            "overlap_assigned_share", "overlap_containment", "overlap_largest_share",
            "overlap_largest_candidate_share", "overlap_unregistered_share",
        )
        for field in fields:
            for raw in ("word", "nan", "inf", "-0.01", "1.000001", "90%"):
                with self.subTest(field=field, value=raw):
                    with self.assertRaises(AuditInputError):
                        self.validate_geography(self.overlap_geography(**{field: raw}))

    def test_overlap_validation_rejects_invalid_flags_method_domain_and_marker(self):
        invalid = (
            {"overlap_ok": "true"},
            {"overlap_ok": "2"},
            {"overlap_ok": ""},
            {"overlap_components_agree": "yes"},
            {"overlap_components_agree": "2"},
            {"overlap_components_agree": ""},
            {"overlap_method": "coast_bbox_icp;local_river_lake_translation"},
            {"overlap_method": ""},
            {"overlap_domain": "vanilla_native_land"},
            {"overlap_domain": ""},
            {"overlap_marker": "coast_bbox_icp;river_pairs=10;lake_pairs=0"},
            {"overlap_marker": "v2m_states"},
            {"overlap_marker": "coast_bbox_affine_raster_overlap;approved"},
            {"overlap_marker": ""},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(AuditInputError):
                    self.validate_geography(self.overlap_geography(**changes))

    def test_overlap_validation_rejects_failed_gates_or_inconsistent_acceptance(self):
        invalid = (
            {"overlap_area_px": "-1"},
            {"overlap_area_px": "1.5"},
            {"overlap_area_px": ""},
            {"overlap_area_px": "63"},
            {"overlap_assigned_share": "0.890000", "overlap_unregistered_share": "0.110000"},
            {"overlap_share": "0.790000", "overlap_margin": "0.740000", "overlap_largest_candidate_share": "0.790000"},
            {"overlap_margin": "0.240000"},
            {"overlap_components": "0"},
            {"overlap_components": "1.5"},
            {"overlap_components": "-1"},
            {"overlap_components_agree": "0"},
            {"overlap_largest_best": "2"},
            {"overlap_largest_candidate_share": "0.790000"},
            {"overlap_ancestor": "2"},
            {"overlap_ancestor": ""},
            {"overlap_runner": "1"},
            {"overlap_runner_share": ""},
            {"overlap_runner_share": "0.950000"},
            {"overlap_share": "0.960000", "overlap_margin": "0.910000", "overlap_largest_candidate_share": "0.960000"},
            {"overlap_margin": "0.850003"},
            {"overlap_assigned_share": "0.950003"},
            {"overlap_reason": "low_share"},
            {"overlap_ok": "0"},
            {"overlap_ok": "0", "overlap_ancestor": "", "overlap_marker": ""},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                geography = self.overlap_geography(**changes)
                with self.assertRaises(AuditInputError):
                    self.validate_geography(geography)
                with self.assertRaises(AuditInputError):
                    validate_overlap_row(geography)
                self.assertEqual(overlap_candidate_ids(geography), ())
                self.assertFalse(overlap_quality_ok(geography))

    def test_overlap_validation_accepts_gate_boundaries_and_rounding_tolerance(self):
        rows = (
            self.overlap_geography(
                overlap_area_px="64",
                overlap_share="0.800000",
                overlap_runner_share="0.100000",
                overlap_margin="0.700000",
                overlap_assigned_share="0.900000",
                overlap_unregistered_share="0.100000",
                overlap_largest_candidate_share="0.800000",
            ),
            self.overlap_geography(
                overlap_margin="0.850001",
                overlap_assigned_share="0.950001",
            ),
        )
        for geography in rows:
            with self.subTest(geography=geography):
                self.assertEqual(self.validate_geography(geography), {1: geography})
                self.assertEqual(overlap_candidate_ids(geography), (1,))
                self.assertTrue(overlap_quality_ok(geography))

    def test_reason_counter_includes_overlap_audit_reasons(self):
        disagreement = self.withheld_overlap(
            overlap_area_px="100",
            overlap_components="2",
            overlap_largest_share="0.900000",
            overlap_largest_candidate_share="1.000000",
            overlap_components_agree="0",
            overlap_reason="component_disagreement",
        )
        rows = [self.audit_one(geography) for geography in (
            self.overlap_geography(),
            self.overlap_geography(candidate="2"),
            self.withheld_overlap(),
            disagreement,
            self.overlap_geography(overlap_marker="v2m_states"),
        )]
        counts = reason_counter(rows)
        self.assertEqual(counts["overlap-conflict"], 1)
        self.assertEqual(counts["overlap-invalid"], 1)
        self.assertEqual(counts["overlap-component-disagreement"], 1)
        self.assertGreaterEqual(counts["overlap-weak"], 1)
        self.assertFalse(any(reason.startswith("raw-geography-") for reason in counts))


if __name__ == "__main__":
    unittest.main()
