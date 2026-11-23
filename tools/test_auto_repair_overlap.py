#!/usr/bin/env python
"""Synthetic overlap contracts for conservative state-repair proposals."""

from __future__ import annotations

import csv
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


# The repair module imports audit_state_mapping as a sibling. Keep that import
# working both for direct execution and root-level unittest discovery.
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import auto_repair_state_mapping as repair  # noqa: E402
from audit_state_mapping import OVERLAP_COLUMNS  # noqa: E402


ME_ID = 7001
CURRENT = 101
ALTERNATE = 202
OTHER = 303
VANILLA_IDS = {CURRENT, ALTERNATE, OTHER}
ME_NAME = "Synthetic Coast"
GEOGRAPHY_PATH = Path("synthetic_geography.tsv")
OVERLAP_METHOD = "coast_bbox_affine_raster"
OVERLAP_DOMAIN = "me_native_land"
OVERLAP_MARKER = "coast_bbox_affine_raster_overlap"
RAW_MARKER = (
    "coast river lake local translation; pairs=12; residual_p50=2; "
    "residual_p95=4; center=1; radius=8"
)
RATIO_FIELDS = (
    "overlap_share", "overlap_runner_share", "overlap_margin",
    "overlap_assigned_share", "overlap_containment", "overlap_largest_share",
    "overlap_largest_candidate_share", "overlap_unregistered_share",
)


def tsv_bytes(rows, columns=None):
    """Serialize fixtures in memory; never touch canonical/generated tables."""
    if columns is None:
        columns = tuple(rows[0])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def accepted_overlap(candidate=ALTERNATE):
    runner = OTHER if candidate != OTHER else CURRENT
    return {
        "overlap_ok": "1",
        "overlap_method": OVERLAP_METHOD,
        "overlap_domain": OVERLAP_DOMAIN,
        "overlap_area_px": "100",
        "overlap_best": str(candidate),
        "overlap_share": "0.900000",
        "overlap_runner": str(runner),
        "overlap_runner_share": "0.100000",
        "overlap_margin": "0.800000",
        "overlap_assigned_share": "1.000000",
        "overlap_containment": "0.500000",
        "overlap_components": "1",
        "overlap_largest_share": "1.000000",
        "overlap_largest_best": str(candidate),
        "overlap_largest_candidate_share": "0.900000",
        "overlap_components_agree": "1",
        "overlap_unregistered_share": "0.000000",
        "overlap_ancestor": str(candidate),
        "overlap_marker": OVERLAP_MARKER,
        "overlap_reason": "accepted",
    }


def withheld_overlap(candidate=ALTERNATE):
    row = accepted_overlap(candidate)
    row.update(
        overlap_ok="0", overlap_share="0.600000", overlap_runner_share="0.400000",
        overlap_margin="0.200000", overlap_largest_candidate_share="0.600000",
        overlap_components_agree="0", overlap_ancestor="", overlap_marker="",
        overlap_reason="low_share",
    )
    return row


def component_disagreement_overlap():
    row = accepted_overlap()
    row.update(
        overlap_ok="0", overlap_components="2", overlap_largest_share="0.900000",
        overlap_largest_candidate_share="1.000000", overlap_components_agree="0",
        overlap_ancestor="", overlap_marker="", overlap_reason="component_disagreement",
    )
    return row


def zero_runner_overlap():
    row = accepted_overlap()
    row.update(
        overlap_share="1.000000", overlap_runner="0", overlap_runner_share="0.000000",
        overlap_margin="1.000000", overlap_largest_candidate_share="1.000000",
    )
    return row


def unassigned_overlap():
    row = withheld_overlap()
    row.update(
        overlap_best="", overlap_share="0.000000", overlap_runner="",
        overlap_runner_share="0.000000", overlap_margin="0.000000",
        overlap_assigned_share="0.000000", overlap_containment="",
        overlap_largest_best="", overlap_largest_candidate_share="0.000000",
        overlap_unregistered_share="1.000000", overlap_reason="low_assigned_share",
    )
    return row


def geography_fixture(overlap=None, raw_candidate=None):
    candidate = CURRENT if raw_candidate is None else raw_candidate
    row = {
        "me_id": str(ME_ID),
        "me_name": ME_NAME,
        "natural_ok": "1",
        "natural_method": "translation",
        "natural_pairs": "12",
        "natural_residual_p50": "2.000000",
        "natural_residual_p95": "4.000000",
        "natural_best": str(candidate),
        "natural_share": "0.900000",
        "natural_runner": str(OTHER if candidate != OTHER else CURRENT),
        "natural_runner_share": "0.100000",
        "natural_margin": "0.800000",
        "raw_geography_ancestor": "" if raw_candidate is None else str(candidate),
        "raw_geography_marker": "" if raw_candidate is None else RAW_MARKER,
        "natural_reason": "accepted",
        "natural_sampled_share": "1.000000",
    }
    if overlap is not None:
        row.update(overlap)
    return row


def read_geography(row):
    return repair.geography_rows(tsv_bytes([row]), GEOGRAPHY_PATH)


def evidence_fixture(ancestor=CURRENT, basis="pixel-geo", mid=ME_ID):
    return {
        "me_id": str(mid), "me_name": ME_NAME,
        "ancestor": "" if ancestor is None else str(ancestor),
        "basis": basis, "status": "unmapped" if ancestor is None else "mapped",
        "manual_override": "false",
    }


def family_fields(family, candidate=ALTERNATE):
    if family == "pixel":
        return {
            "pixel_best": str(candidate), "pixel_share": "0.900000",
            "pixel_runner": str(OTHER if candidate != OTHER else CURRENT),
            "pixel_runner_share": "0.100000", "pixel_margin": "0.800000",
        }
    if family in {"comment", "vp"}:
        return {
            family + "_best": str(candidate), family + "_votes": "3",
            family + "_runner": "", family + "_runner_votes": "0",
            family + "_margin": "3",
        }
    if family == "name":
        return {"name_exact_me_ids": str(candidate)}
    if family == "raw_geography":
        return {"raw_geography_ancestor": str(candidate), "raw_geography_marker": RAW_MARKER}
    raise ValueError(f"unknown fixture evidence family: {family}")


def proposal(overlap=None, *, ancestor=CURRENT, basis="pixel-geo", evidence_updates=None,
             audit_updates=None, protected=None, mid=ME_ID):
    raw = evidence_fixture(ancestor, basis, mid)
    if overlap is not None:
        geography = geography_fixture(overlap)
        geography["me_id"] = str(mid)
        raw = repair.merge_geography({mid: raw}, read_geography(geography), GEOGRAPHY_PATH)[mid]
    if evidence_updates:
        raw.update(evidence_updates)
    current = {
        "me_id": str(mid), "me_name": ME_NAME,
        "v_ancestor": "" if ancestor is None else str(ancestor),
        "basis": basis, "status": "unmapped" if ancestor is None else "mapped",
    }
    checked = {
        "me_id": str(mid), "me_name": ME_NAME,
        "ancestor": "" if ancestor is None else str(ancestor),
        "basis": basis, "class": "no_action", "reason_codes": "",
    }
    if audit_updates:
        checked.update(audit_updates)
    source = SimpleNamespace(protected={} if protected is None else protected)
    [row] = repair.proposal_rows({mid: raw}, {mid: checked}, {mid: current}, VANILLA_IDS, source)
    return row


class GeographyOverlapInputTests(unittest.TestCase):
    def test_legacy_fixture_is_accepted_and_merged_without_new_fields(self):
        geography = geography_fixture(raw_candidate=CURRENT)
        indexed = read_geography(geography)
        self.assertEqual(indexed, {ME_ID: geography})
        evidence = evidence_fixture()
        merged = repair.merge_geography({ME_ID: evidence}, indexed, GEOGRAPHY_PATH)
        self.assertEqual(merged, {ME_ID: {
            **evidence, "raw_geography_ancestor": str(CURRENT), "raw_geography_marker": RAW_MARKER,
        }})
        self.assertFalse(any(field.startswith("overlap_") for field in merged[ME_ID]))
        self.assertNotIn("raw_geography_ancestor", evidence)

    def test_complete_empty_extension_is_allowed_for_unavailable_producer(self):
        row = geography_fixture(dict.fromkeys(OVERLAP_COLUMNS, ""))
        self.assertEqual(read_geography(row), {ME_ID: row})
        merged = repair.merge_geography({ME_ID: evidence_fixture()}, read_geography(row), GEOGRAPHY_PATH)
        self.assertEqual({field: merged[ME_ID][field] for field in OVERLAP_COLUMNS},
                         dict.fromkeys(OVERLAP_COLUMNS, ""))

    def test_all_overlap_columns_are_required_atomically(self):
        complete = geography_fixture(accepted_overlap())
        for field in OVERLAP_COLUMNS:
            with self.subTest(missing=field):
                partial = {key: value for key, value in complete.items() if key != field}
                with self.assertRaises(repair.RepairError):
                    read_geography(partial)
        with self.assertRaises(repair.RepairError):
            read_geography(geography_fixture({"overlap_ok": ""}))

    def test_accepted_agreed_conflicting_and_withheld_rows_keep_original_values(self):
        fixtures = (
            accepted_overlap(CURRENT), accepted_overlap(ALTERNATE), withheld_overlap(),
            component_disagreement_overlap(), zero_runner_overlap(), unassigned_overlap(),
        )
        for overlap in fixtures:
            with self.subTest(ok=overlap["overlap_ok"], best=overlap["overlap_best"],
                              reason=overlap["overlap_reason"]):
                row = geography_fixture(overlap)
                self.assertEqual(read_geography(row), {ME_ID: row})

    def test_absent_runner_accepts_blank_or_zero_id_and_share(self):
        for runner in ("", "0"):
            for runner_share in ("", "0.000000"):
                with self.subTest(runner=runner, runner_share=runner_share):
                    overlap = zero_runner_overlap()
                    overlap.update(overlap_runner=runner, overlap_runner_share=runner_share)
                    row = geography_fixture(overlap)
                    self.assertEqual(read_geography(row), {ME_ID: row})

    def test_unassigned_vote_accepts_empty_candidate_and_absent_metrics(self):
        for runner in ("", "0"):
            for runner_share in ("", "0.000000"):
                for containment in ("", "0.000000"):
                    with self.subTest(runner=runner, runner_share=runner_share, containment=containment):
                        overlap = unassigned_overlap()
                        overlap.update(overlap_runner=runner, overlap_runner_share=runner_share,
                                       overlap_containment=containment)
                        row = geography_fixture(overlap)
                        self.assertEqual(read_geography(row), {ME_ID: row})

    def test_ratio_arithmetic_allows_producer_rounding_tolerance(self):
        overlap = accepted_overlap()
        overlap.update(overlap_share="0.800001", overlap_runner_share="0.200000",
                       overlap_margin="0.600002", overlap_largest_candidate_share="0.800001")
        row = geography_fixture(overlap)
        self.assertEqual(read_geography(row), {ME_ID: row})

    def test_malformed_positive_ids_are_rejected(self):
        for field in ("overlap_best", "overlap_largest_best", "overlap_ancestor", "overlap_runner"):
            invalid_values = ("-1", "202junk", "202.0", "202|303", "NaN")
            if field != "overlap_runner":
                invalid_values += ("0",)
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    overlap = accepted_overlap()
                    overlap[field] = invalid
                    with self.assertRaises(repair.RepairError):
                        read_geography(geography_fixture(overlap))

    def test_ratios_are_strict_finite_fractions_not_percentages(self):
        for field in RATIO_FIELDS:
            for invalid in ("-0.000001", "1.000001", "80", "80%", "NaN", "Infinity", "0.9junk"):
                with self.subTest(field=field, invalid=invalid):
                    overlap = accepted_overlap()
                    overlap[field] = invalid
                    with self.assertRaises(repair.RepairError):
                        read_geography(geography_fixture(overlap))

    def test_flags_counts_method_domain_and_marker_are_strict(self):
        mutations = []
        for field in ("overlap_ok", "overlap_components_agree"):
            mutations.extend((field, invalid) for invalid in ("", "true", "false", "yes", "2", "-1"))
        for field in ("overlap_area_px", "overlap_components"):
            mutations.extend((field, invalid) for invalid in ("", "-1", "1.5", "many"))
        mutations.extend(("overlap_method", invalid) for invalid in ("", "translation", "vp_affine"))
        mutations.extend(("overlap_domain", invalid) for invalid in ("", "vanilla_native_land", "candidate_land"))
        mutations.extend(("overlap_marker", invalid) for invalid in (
            "", "true", "v2m_states.tsv", RAW_MARKER, OVERLAP_MARKER + ";approved=1",
        ))
        mutations.extend(("overlap_reason", invalid) for invalid in ("", "low_share", "approved"))
        for field, invalid in mutations:
            with self.subTest(field=field, invalid=invalid):
                overlap = accepted_overlap()
                overlap[field] = invalid
                with self.assertRaises(repair.RepairError):
                    read_geography(geography_fixture(overlap))

    def test_inconsistent_ratios_and_component_metrics_are_rejected(self):
        mutations = (
            {"overlap_runner": str(ALTERNATE)},
            {"overlap_runner_share": ""},
            {"overlap_runner": "", "overlap_runner_share": "0.100000"},
            {"overlap_margin": "0.799990"},
            {"overlap_unregistered_share": "0.100000"},
            {"overlap_assigned_share": "0.950000", "overlap_unregistered_share": "0.050000"},
            {"overlap_components": "101"},
            {"overlap_components": "0"},
            {"overlap_largest_share": "0.900000"},
            {"overlap_largest_candidate_share": "0.850000"},
            {"overlap_largest_best": ""},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                overlap = accepted_overlap()
                overlap.update(changes)
                with self.assertRaises(repair.RepairError):
                    read_geography(geography_fixture(overlap))

    def test_acceptance_requires_every_producer_gate_and_matching_ancestor(self):
        mutations = (
            {"overlap_area_px": "63"},
            {"overlap_assigned_share": "0.890000", "overlap_unregistered_share": "0.110000",
             "overlap_share": "0.800000", "overlap_runner_share": "0.090000",
             "overlap_margin": "0.710000", "overlap_largest_candidate_share": "0.800000"},
            {"overlap_share": "0.790000", "overlap_runner_share": "0.210000",
             "overlap_margin": "0.580000", "overlap_largest_candidate_share": "0.790000"},
            {"overlap_share": "0.600000", "overlap_runner_share": "0.400000",
             "overlap_margin": "0.200000", "overlap_largest_candidate_share": "0.600000"},
            {"overlap_components_agree": "0"},
            {"overlap_components": "2", "overlap_largest_share": "0.900000",
             "overlap_largest_best": str(OTHER)},
            {"overlap_components": "2", "overlap_largest_share": "0.900000",
             "overlap_largest_candidate_share": "0.790000"},
            {"overlap_ancestor": ""},
            {"overlap_ancestor": str(CURRENT)},
            {"overlap_best": ""},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                overlap = accepted_overlap()
                overlap.update(changes)
                with self.assertRaises(repair.RepairError):
                    read_geography(geography_fixture(overlap))

    def test_withheld_rows_cannot_claim_an_ancestor_marker_or_accepted_status(self):
        mutations = (
            {"overlap_ancestor": str(ALTERNATE)},
            {"overlap_marker": OVERLAP_MARKER},
            {"overlap_reason": "accepted"},
            {"overlap_reason": ""},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                overlap = withheld_overlap()
                overlap.update(changes)
                with self.assertRaises(repair.RepairError):
                    read_geography(geography_fixture(overlap))


class GeographyOverlapMergeTests(unittest.TestCase):
    def test_merge_preserves_every_overlap_diagnostic_and_keeps_markers_separate(self):
        for overlap in (accepted_overlap(), withheld_overlap(), component_disagreement_overlap()):
            with self.subTest(reason=overlap["overlap_reason"]):
                geography = geography_fixture(overlap, raw_candidate=CURRENT)
                raw = {**evidence_fixture(), "unrelated_evidence": "retain me"}
                snapshot = dict(raw)
                indexed = read_geography(geography)
                merged = repair.merge_geography({ME_ID: raw}, indexed, GEOGRAPHY_PATH)[ME_ID]
                self.assertEqual(raw, snapshot)
                self.assertEqual(indexed[ME_ID], geography)
                self.assertEqual(merged["unrelated_evidence"], "retain me")
                self.assertEqual({field: merged[field] for field in OVERLAP_COLUMNS}, overlap)
                self.assertEqual(merged["raw_geography_ancestor"], str(CURRENT))
                self.assertEqual(merged["raw_geography_marker"], RAW_MARKER)
                self.assertNotEqual(merged["raw_geography_marker"], merged["overlap_marker"])

    def test_overlap_marker_never_becomes_raw_geography_marker(self):
        merged = repair.merge_geography(
            {ME_ID: evidence_fixture()}, read_geography(geography_fixture(accepted_overlap())), GEOGRAPHY_PATH,
        )[ME_ID]
        self.assertEqual(merged["raw_geography_ancestor"], "")
        self.assertEqual(merged["raw_geography_marker"], "")
        self.assertEqual(merged["overlap_ancestor"], str(ALTERNATE))
        self.assertEqual(merged["overlap_marker"], OVERLAP_MARKER)

    def test_merge_refuses_to_replace_existing_values_in_any_extension_field(self):
        indexed = read_geography(geography_fixture(accepted_overlap(), raw_candidate=CURRENT))
        fields = ("raw_geography_ancestor", "raw_geography_marker") + tuple(OVERLAP_COLUMNS)
        for field in fields:
            for existing in ("different-existing-value", ""):
                with self.subTest(field=field, existing=existing):
                    raw = {**evidence_fixture(), field: existing}
                    with self.assertRaises(repair.RepairError):
                        repair.merge_geography({ME_ID: raw}, indexed, GEOGRAPHY_PATH)

    def test_identical_existing_extensions_are_preserved(self):
        geography = geography_fixture(accepted_overlap(), raw_candidate=CURRENT)
        fields = ("raw_geography_ancestor", "raw_geography_marker") + tuple(OVERLAP_COLUMNS)
        raw = {**evidence_fixture(), **{field: geography[field] for field in fields}}
        merged = repair.merge_geography({ME_ID: raw}, read_geography(geography), GEOGRAPHY_PATH)
        self.assertEqual(merged, {ME_ID: raw})


class OverlapIndependentPairTests(unittest.TestCase):
    def test_overlap_cannot_corroborate_dependent_positioning_families(self):
        for families in (
            ("overlap",), ("overlap", "pixel"), ("overlap", "vp"),
            ("overlap", "raw_geography"), ("overlap", "pixel", "vp"),
            ("overlap", "raw_geography", "vp"),
        ):
            with self.subTest(families=families):
                self.assertEqual(repair.independent_pair(families), "")
                self.assertEqual(repair.independent_pair(tuple(reversed(families))), "")

    def test_overlap_with_comment_or_name_is_independent(self):
        for direct in ("comment", "name"):
            with self.subTest(direct=direct):
                self.assertEqual(repair.independent_pair(("overlap", direct)), direct + "+overlap")
                self.assertEqual(repair.independent_pair((direct, "overlap")), direct + "+overlap")

    def test_historical_pairs_and_precedence_are_unchanged(self):
        cases = (
            (("pixel", "comment"), "pixel+comment"),
            (("pixel", "name"), "pixel+name"),
            (("pixel", "raw_geography"), "pixel+raw_geography"),
            (("comment", "name"), "name+comment"),
            (("comment", "vp"), "vp+comment"),
            (("name", "vp"), "name+vp"),
            (("comment", "raw_geography"), "comment+raw_geography"),
            (("name", "raw_geography"), "name+raw_geography"),
            (("pixel", "vp"), ""),
            (("vp", "raw_geography"), ""),
            (("pixel", "raw_geography", "overlap"), "pixel+raw_geography"),
            (("pixel", "comment", "overlap"), "pixel+comment"),
            (("name", "vp", "overlap"), "name+vp"),
        )
        for families, expected in cases:
            with self.subTest(families=families):
                self.assertEqual(repair.independent_pair(families), expected)


class OverlapProposalTests(unittest.TestCase):
    def test_accepted_agreement_is_no_action(self):
        row = proposal(accepted_overlap(CURRENT))
        self.assertEqual(row["decision"], "no_action")
        self.assertEqual(row["current_ancestor"], str(CURRENT))
        self.assertEqual(row["proposed_ancestor"], str(CURRENT))
        self.assertEqual(row["evidence_families"], "overlap")
        self.assertNotIn("overlap-conflict", row["reason_codes"].split("|"))
        self.assertIn("overlap_marker=" + OVERLAP_MARKER, row["evidence_summary"])
        self.assertNotIn("raw_geography_marker=" + OVERLAP_MARKER, row["evidence_summary"])

    def test_overlap_only_alternate_is_review_not_automatic_repair(self):
        row = proposal(accepted_overlap())
        self.assertEqual(row["decision"], "review")
        self.assertEqual(row["current_ancestor"], str(CURRENT))
        self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
        self.assertEqual(row["evidence_families"], "overlap")
        self.assertIn("overlap-conflict", row["reason_codes"].split("|"))
        self.assertIn("insufficient-independent-evidence", row["reason_codes"].split("|"))

    def test_zero_runner_acceptance_can_supply_the_overlap_vote(self):
        row = proposal(zero_runner_overlap(), evidence_updates=family_fields("comment"))
        self.assertEqual(row["decision"], "auto_repair")
        self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
        self.assertEqual(set(row["evidence_families"].split("|")), {"comment", "overlap"})

    def test_withheld_diagnostic_candidate_cannot_vote_or_propose_itself(self):
        for overlap in (withheld_overlap(), unassigned_overlap()):
            with self.subTest(reason=overlap["overlap_reason"]):
                row = proposal(overlap)
                self.assertNotEqual(row["decision"], "auto_repair")
                self.assertEqual(row["proposed_ancestor"], "")
                self.assertEqual(row["evidence_families"], "")
                self.assertIn("overlap_ok=0", row["evidence_summary"])
                self.assertIn("overlap_reason=" + overlap["overlap_reason"], row["evidence_summary"])
                if overlap["overlap_best"]:
                    self.assertIn("overlap_best=" + overlap["overlap_best"], row["evidence_summary"])

    def test_withheld_plus_comment_does_not_create_an_independent_pair(self):
        row = proposal(withheld_overlap(), evidence_updates=family_fields("comment"))
        self.assertEqual(row["decision"], "review")
        self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
        self.assertEqual(row["evidence_families"], "comment")
        self.assertIn("qualified=comment", row["evidence_summary"])
        self.assertNotIn("independent_pair=comment+overlap", row["evidence_summary"])

    def test_withheld_best_does_not_conflict_with_valid_identity_votes(self):
        fields = {**family_fields("comment", CURRENT), **family_fields("name", CURRENT)}
        row = proposal(withheld_overlap(), evidence_updates=fields)
        self.assertEqual(row["decision"], "no_action")
        self.assertEqual(row["proposed_ancestor"], str(CURRENT))
        self.assertEqual(set(row["evidence_families"].split("|")), {"comment", "name"})
        self.assertNotIn("evidence-family-conflict", row["reason_codes"].split("|"))

    def test_empty_and_legacy_extensions_do_not_change_proposals(self):
        legacy = proposal()
        unavailable = proposal(dict.fromkeys(OVERLAP_COLUMNS, ""))
        self.assertEqual(legacy, unavailable)
        self.assertEqual(legacy["decision"], "no_action")
        self.assertNotIn("overlap", legacy["evidence_summary"])

    def test_overlap_and_direct_identity_can_repair_pixel_or_unmapped_basis(self):
        for direct in ("comment", "name"):
            for ancestor, basis in ((CURRENT, "pixel-geo"), (None, "unmapped"), (None, "")):
                with self.subTest(direct=direct, ancestor=ancestor, basis=basis):
                    row = proposal(accepted_overlap(), ancestor=ancestor, basis=basis,
                                   evidence_updates=family_fields(direct))
                    self.assertEqual(row["decision"], "auto_repair")
                    self.assertEqual(row["confidence"], "high")
                    self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
                    self.assertEqual(set(row["evidence_families"].split("|")), {direct, "overlap"})
                    self.assertIn("independent_pair=" + direct + "+overlap", row["evidence_summary"])

    def test_dependent_positioning_combinations_never_auto_repair(self):
        for families in (("pixel",), ("vp",), ("raw_geography",), ("pixel", "vp")):
            with self.subTest(families=families):
                fields = {}
                for family in families:
                    fields.update(family_fields(family))
                row = proposal(accepted_overlap(), evidence_updates=fields)
                self.assertEqual(row["decision"], "review")
                self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
                self.assertEqual(set(row["evidence_families"].split("|")), {"overlap", *families})
                self.assertNotIn("independent_pair=", row["evidence_summary"])

    def test_existing_direct_canonical_identity_stays_review(self):
        for basis in ("comment", "comment(member)", "name-singleton", "name~", "same-id", "vp-geo"):
            for direct in ("comment", "name"):
                with self.subTest(basis=basis, direct=direct):
                    row = proposal(accepted_overlap(), basis=basis, evidence_updates=family_fields(direct))
                    self.assertEqual(row["decision"], "review")
                    self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
                    self.assertIn("existing-identity-mapping", row["reason_codes"].split("|"))

    def test_malformed_direct_extension_blocks_otherwise_independent_repair(self):
        malformed = accepted_overlap()
        malformed["overlap_ancestor"] = ""
        for overlap in (malformed, {"overlap_best": str(ALTERNATE)}):
            with self.subTest(overlap=overlap):
                fields = {**family_fields("comment"), **family_fields("name"), **overlap}
                row = proposal(evidence_updates=fields)
                self.assertEqual(row["decision"], "review")
                self.assertNotIn("overlap", row["evidence_families"].split("|"))
                self.assertIn("overlap-invalid", row["reason_codes"].split("|"))

    def test_component_disagreement_blocks_even_an_existing_independent_pair(self):
        fields = {**family_fields("comment"), **family_fields("name")}
        row = proposal(component_disagreement_overlap(), evidence_updates=fields)
        self.assertEqual(row["decision"], "review")
        self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
        self.assertEqual(set(row["evidence_families"].split("|")), {"comment", "name"})
        self.assertIn("overlap-component-disagreement", row["reason_codes"].split("|"))
        self.assertIn("overlap_reason=component_disagreement", row["evidence_summary"])

    def test_audit_component_disagreement_remains_a_blocker(self):
        row = proposal(accepted_overlap(), evidence_updates=family_fields("comment"), audit_updates={
            "class": "review_required", "reason_codes": "overlap-component-disagreement",
        })
        self.assertEqual(row["decision"], "review")
        self.assertIn("overlap-component-disagreement", row["reason_codes"].split("|"))

    def test_existing_blockers_are_not_bypassed_by_overlap_and_comment(self):
        cases = (
            ({"name_collision_count": "2"}, {}, "name-collision"),
            ({}, {"vp_table_cross_state_rows": "1"}, "vp-cross-state"),
            ({"me_core_tags": "AAA", "ancestor_core_tags": "BBB"}, {}, "core-disjoint"),
            ({"ancestor": str(OTHER)}, {}, "canonical-evidence-mismatch"),
            (family_fields("pixel", CURRENT), {}, "comment-vs-pixel-conflict"),
            ({"comment_votes": "0"}, {}, "comment-evidence-invalid"),
        )
        for extra, checked, reason in cases:
            with self.subTest(reason=reason):
                row = proposal(accepted_overlap(), evidence_updates={**family_fields("comment"), **extra},
                               audit_updates=checked)
                self.assertEqual(row["decision"], "review")
                self.assertIn(reason, row["reason_codes"].split("|"))

    def test_conflicting_or_non_vanilla_accepted_candidates_are_not_promoted(self):
        row = proposal(accepted_overlap(), evidence_updates={
            **family_fields("comment", OTHER), **family_fields("name"),
        })
        self.assertEqual(row["decision"], "review")
        self.assertIn("evidence-family-conflict", row["reason_codes"].split("|"))
        unknown = 9999
        row = proposal(accepted_overlap(unknown), evidence_updates=family_fields("comment", unknown))
        self.assertEqual(row["decision"], "review")
        self.assertIn("candidate-not-vanilla", row["reason_codes"].split("|"))

    def test_manual_rows_remain_already_manual(self):
        cases = (
            {"basis": "manual"},
            {"protected": {ME_ID: CURRENT}},
            {"evidence_updates": {"manual_override": "true"}},
            {"audit_updates": {"manual_override": "true"}},
            {"evidence_updates": {"known_manual_correction": "true"}},
            {"audit_updates": {"known_manual_correction": "true"}},
        )
        for options in cases:
            with self.subTest(options=options):
                fields = {**family_fields("comment"), **options.get("evidence_updates", {})}
                options = {**options, "evidence_updates": fields}
                row = proposal(accepted_overlap(), **options)
                self.assertEqual(row["decision"], "already_manual")
                self.assertEqual(row["confidence"], "manual")
                self.assertIn("existing-manual", row["reason_codes"].split("|"))

    def test_legacy_independent_repairs_and_pixel_vp_dependency_are_unchanged(self):
        for families in (
            ("pixel", "comment"), ("pixel", "name"), ("comment", "name"),
            ("pixel", "raw_geography"), ("comment", "raw_geography"), ("name", "raw_geography"),
        ):
            with self.subTest(families=families):
                fields = {}
                for family in families:
                    fields.update(family_fields(family))
                row = proposal(evidence_updates=fields)
                self.assertEqual(row["decision"], "auto_repair")
                self.assertEqual(row["proposed_ancestor"], str(ALTERNATE))
                self.assertEqual(set(row["evidence_families"].split("|")), set(families))
                self.assertNotIn("overlap", row["reason_codes"] + row["evidence_summary"])
        row = proposal(evidence_updates={**family_fields("pixel"), **family_fields("vp")})
        self.assertEqual(row["decision"], "review")
        self.assertIn("pixel-vp-dependent", row["reason_codes"].split("|"))


class OverlapApprovalTests(unittest.TestCase):
    @staticmethod
    def approvals(candidate=ALTERNATE):
        return repair.approval_rows(tsv_bytes([{
            "me_id": str(ME_ID), "approved_ancestor": str(candidate),
            "review_note": "Inspected synthetic overlap and independent source",
        }], repair.APPROVAL_COLUMNS), Path("synthetic_approvals.tsv"))

    def test_explicit_approval_can_select_a_review_proposal(self):
        row = proposal(accepted_overlap())
        self.assertEqual(row["decision"], "review")
        [approved] = repair.approved_rows(self.approvals(), [row], VANILLA_IDS, SimpleNamespace(protected={}))
        self.assertEqual(approved["approved_ancestor"], str(ALTERNATE))
        self.assertEqual(approved["approval_kind"], "normal_approval")
        self.assertEqual(row["decision"], "review")

    def test_explicit_review_can_override_the_proposed_candidate(self):
        row = proposal(accepted_overlap())
        [approved] = repair.approved_rows(
            self.approvals(OTHER), [row], VANILLA_IDS, SimpleNamespace(protected={}),
        )
        self.assertEqual(approved["approved_ancestor"], str(OTHER))
        self.assertEqual(approved["approval_kind"], "manual_candidate_override")

    def test_approval_cannot_promote_existing_manual_decisions(self):
        for options in ({"basis": "manual"}, {"protected": {ME_ID: CURRENT}},
                        {"evidence_updates": {"manual_override": "true"}}):
            with self.subTest(options=options):
                row = proposal(accepted_overlap(), **options)
                self.assertEqual(row["decision"], "already_manual")
                source = SimpleNamespace(protected=options.get("protected", {}))
                with self.assertRaises(repair.RepairError):
                    repair.approved_rows(self.approvals(), [row], VANILLA_IDS, source)


if __name__ == "__main__":
    unittest.main()
