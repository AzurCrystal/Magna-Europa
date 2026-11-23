"""Small deterministic tests for the candidate-independent geography evidence tool."""

from __future__ import annotations

import contextlib
import io
import csv
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

# unittest discovery adds ``tools`` for this suite, but keep sibling imports
# explicit when the file is run directly as well.
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import state_geography_evidence as geography  # noqa: E402
from state_geography_evidence import (  # noqa: E402
    COLUMNS,
    evidence_row,
    load_states,
    main,
    produce_evidence,
    raster_overlap_evidence,
    state_geometry,
    vote_centroids,
    write_evidence,
)


IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

LEGACY_COLUMNS = [
    "me_id", "me_name", "natural_ok", "natural_method", "natural_pairs",
    "natural_residual_p50", "natural_residual_p95", "natural_best",
    "natural_share", "natural_runner", "natural_runner_share", "natural_margin",
    "raw_geography_ancestor", "raw_geography_marker", "natural_reason",
    "natural_river_pairs", "natural_lake_pairs", "natural_center_x",
    "natural_center_y", "natural_radius", "natural_sampled_share",
]
OVERLAP_COLUMNS = [
    "overlap_ok", "overlap_method", "overlap_domain", "overlap_area_px",
    "overlap_best", "overlap_share", "overlap_runner", "overlap_runner_share",
    "overlap_margin", "overlap_assigned_share", "overlap_containment",
    "overlap_components", "overlap_largest_share", "overlap_largest_best",
    "overlap_largest_candidate_share", "overlap_components_agree",
    "overlap_unregistered_share", "overlap_ancestor", "overlap_marker",
    "overlap_reason",
]


class RasterOverlapEvidenceTests(unittest.TestCase):
    """Native-frame pixel contracts do not need province files or real maps."""

    def test_columns_preserve_legacy_prefix_and_append_ordered_overlap_suffix(self):
        self.assertEqual(COLUMNS, LEGACY_COLUMNS + OVERLAP_COLUMNS)

    def test_complete_single_component_emits_overlap_only_string_fields(self):
        rows = raster_overlap_evidence(
            np.full((8, 8), 17, dtype=np.int32),
            np.full((8, 8), 7, dtype=np.int32),
            IDENTITY,
        )
        self.assertEqual(
            rows,
            {
                17: {
                    "overlap_ok": "1",
                    "overlap_method": "coast_bbox_affine_raster",
                    "overlap_domain": "me_native_land",
                    "overlap_area_px": "64",
                    "overlap_best": "7",
                    "overlap_share": "1.000000",
                    "overlap_runner": "",
                    "overlap_runner_share": "0.000000",
                    "overlap_margin": "1.000000",
                    "overlap_assigned_share": "1.000000",
                    "overlap_containment": "1.000000",
                    "overlap_components": "1",
                    "overlap_largest_share": "1.000000",
                    "overlap_largest_best": "7",
                    "overlap_largest_candidate_share": "1.000000",
                    "overlap_components_agree": "1",
                    "overlap_unregistered_share": "0.000000",
                    "overlap_ancestor": "7",
                    "overlap_marker": "coast_bbox_affine_raster_overlap",
                    "overlap_reason": "accepted",
                },
            },
        )

    def test_pixel_split_beats_a_province_centroid_in_the_minority_candidate(self):
        me = np.full((10, 10), 17, dtype=np.int32)
        vanilla = np.full((10, 10), 7, dtype=np.int32)
        vanilla[4:6, 4:6] = 8
        centroid_vote = vote_centroids(
            np.asarray([[4.5, 4.5]], dtype=np.float64),
            np.asarray([100.0]),
            IDENTITY,
            vanilla,
        )
        self.assertEqual(centroid_vote["natural_best"], 8)
        self.assertEqual(centroid_vote["natural_share"], 1.0)

        row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
        self.assertEqual(row["overlap_best"], "7")
        self.assertEqual(row["overlap_share"], "0.960000")
        self.assertEqual(row["overlap_runner"], "8")
        self.assertEqual(row["overlap_runner_share"], "0.040000")
        self.assertEqual(row["overlap_margin"], "0.920000")
        self.assertEqual(row["overlap_ancestor"], "7")

    def test_water_negative_ids_and_outside_pixels_never_renormalise(self):
        me = np.full((10, 10), 17, dtype=np.int32)
        vanilla = np.full((10, 8), 7, dtype=np.int32)
        vanilla[:, 0] = 0
        vanilla[:, 1] = -9
        row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
        self.assertEqual(row["overlap_area_px"], "100")
        self.assertEqual(row["overlap_best"], "7")
        self.assertEqual(row["overlap_share"], "0.600000")
        self.assertEqual(row["overlap_runner"], "")
        self.assertEqual(row["overlap_runner_share"], "0.000000")
        self.assertEqual(row["overlap_margin"], "0.600000")
        self.assertEqual(row["overlap_assigned_share"], "0.600000")
        self.assertEqual(row["overlap_unregistered_share"], "0.400000")
        self.assertEqual(row["overlap_containment"], "1.000000")
        self.assertEqual(row["overlap_largest_candidate_share"], "0.600000")
        self.assertEqual(row["overlap_components_agree"], "0")
        self.assertEqual(row["overlap_ok"], "0")
        self.assertEqual(row["overlap_ancestor"], "")
        self.assertEqual(row["overlap_marker"], "")
        self.assertEqual(row["overlap_reason"], "low_assigned_share")

    def test_numerical_ties_choose_lower_positive_id_even_for_sparse_large_ids(self):
        me_id = (1 << 24) + 17
        candidate = (1 << 24) + 7
        me = np.zeros((10, 10), dtype=np.int64)
        me[1:9, 1:9] = me_id
        me[0, 0] = -5
        vanilla = np.full((10, 10), -9, dtype=np.int64)
        vanilla[1:9, 1:5] = candidate  # Encounter the higher ID first.
        vanilla[1:9, 5:9] = 3
        rows = raster_overlap_evidence(me, vanilla, IDENTITY, chunk_pixels=7)
        self.assertEqual(set(rows), {me_id})
        row = rows[me_id]
        self.assertEqual(row["overlap_area_px"], "64")
        self.assertEqual(row["overlap_best"], "3")
        self.assertEqual(row["overlap_runner"], str(candidate))
        self.assertEqual(row["overlap_share"], "0.500000")
        self.assertEqual(row["overlap_runner_share"], "0.500000")
        self.assertEqual(row["overlap_margin"], "0.000000")
        self.assertEqual(row["overlap_largest_best"], "3")
        self.assertEqual(row["overlap_reason"], "low_share")
        self.assertEqual(row["overlap_ancestor"], "")

    def test_diagonal_contact_is_detached_and_largest_ties_keep_scan_label_best(self):
        me = np.zeros((16, 16), dtype=np.int32)
        me[:8, :8] = 17
        me[8:, 8:] = 17
        vanilla = np.zeros_like(me)
        vanilla[:8, :8] = 9
        vanilla[8:, 8:] = 7
        row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
        self.assertEqual(row["overlap_components"], "2")
        self.assertEqual(row["overlap_best"], "7")
        self.assertEqual(row["overlap_runner"], "9")
        self.assertEqual(row["overlap_share"], "0.500000")
        self.assertEqual(row["overlap_largest_share"], "0.500000")
        self.assertEqual(row["overlap_largest_best"], "9")
        self.assertEqual(row["overlap_largest_candidate_share"], "1.000000")
        self.assertEqual(row["overlap_components_agree"], "0")
        self.assertEqual(row["overlap_ancestor"], "")
        self.assertEqual(row["overlap_marker"], "")
        self.assertEqual(row["overlap_reason"], "component_disagreement")

    def test_small_detached_components_must_agree_but_need_not_have_64_pixels(self):
        me = np.zeros((12, 12), dtype=np.int32)
        me[:10, :10] = 17
        me[11, 11] = 17
        for detached_candidate in (7, 8, 0, -9):
            with self.subTest(detached_candidate=detached_candidate):
                vanilla = np.zeros_like(me)
                vanilla[:10, :10] = 7
                vanilla[11, 11] = detached_candidate
                row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
                accepted = detached_candidate == 7
                self.assertEqual(row["overlap_area_px"], "101")
                self.assertEqual(row["overlap_components"], "2")
                self.assertEqual(row["overlap_best"], "7")
                self.assertEqual(row["overlap_largest_share"], "0.990099")
                self.assertEqual(row["overlap_largest_best"], "7")
                self.assertEqual(row["overlap_largest_candidate_share"], "1.000000")
                self.assertEqual(row["overlap_components_agree"], "1" if accepted else "0")
                self.assertEqual(row["overlap_ok"], "1" if accepted else "0")
                self.assertEqual(row["overlap_ancestor"], "7" if accepted else "")
                self.assertEqual(
                    row["overlap_marker"],
                    "coast_bbox_affine_raster_overlap" if accepted else "",
                )
                self.assertEqual(
                    row["overlap_reason"],
                    "accepted" if accepted else "component_disagreement",
                )
                self.assertEqual(
                    row["overlap_assigned_share"],
                    "1.000000" if detached_candidate > 0 else "0.990099",
                )
                self.assertEqual(
                    row["overlap_unregistered_share"],
                    "0.000000" if detached_candidate > 0 else "0.009901",
                )

    def test_same_best_detached_component_still_needs_coverage_and_share_gates(self):
        me = np.zeros((16, 12), dtype=np.int32)
        me[:10, :10] = 17
        me[13:15, :5] = 17
        for detached_votes, accepted in (
            ([7] * 8 + [0] * 2, False),
            ([7] * 7 + [8] * 3, False),
            ([7] * 8 + [8] * 2, True),
        ):
            with self.subTest(detached_votes=detached_votes):
                vanilla = np.zeros_like(me)
                vanilla[:10, :10] = 7
                vanilla[13:15, :5] = np.asarray(detached_votes).reshape(2, 5)
                row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
                self.assertEqual(row["overlap_components"], "2")
                self.assertEqual(row["overlap_best"], "7")
                self.assertEqual(row["overlap_largest_best"], "7")
                self.assertEqual(row["overlap_components_agree"], "1" if accepted else "0")
                self.assertEqual(row["overlap_ancestor"], "7" if accepted else "")
                self.assertEqual(
                    row["overlap_reason"],
                    "accepted" if accepted else "component_disagreement",
                )

    def test_whole_state_area_threshold_precedes_component_disagreement(self):
        for side, accepted in ((7, False), (8, True)):
            with self.subTest(side=side):
                row = raster_overlap_evidence(
                    np.full((side, 8), 17, dtype=np.int32),
                    np.full((side, 8), 7, dtype=np.int32),
                    IDENTITY,
                )[17]
                self.assertEqual(row["overlap_ok"], "1" if accepted else "0")
                self.assertEqual(
                    row["overlap_reason"], "accepted" if accepted else "insufficient_area"
                )
        me = np.zeros((4, 32), dtype=np.int32)
        me[:2, :31] = 17
        me[3, 31] = 17
        vanilla = np.zeros_like(me)
        vanilla[:2, :31] = 7
        vanilla[3, 31] = 8
        row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
        self.assertEqual(row["overlap_area_px"], "63")
        self.assertEqual(row["overlap_components_agree"], "0")
        self.assertEqual(row["overlap_reason"], "insufficient_area")
        self.assertEqual(row["overlap_ancestor"], "")

    def test_island_only_rows_use_full_state_coverage_and_best_share_gates(self):
        me = np.zeros((14, 14), dtype=np.int32)
        me[2:12, 2:12] = 17
        cases = (
            (90, 0, "accepted"),
            (89, 0, "low_assigned_share"),
            (80, 8, "accepted"),
            (79, 8, "low_share"),
            (0, 0, "low_assigned_share"),
        )
        for best_pixels, remainder, reason in cases:
            with self.subTest(best_pixels=best_pixels, remainder=remainder):
                vanilla = np.zeros_like(me)
                votes = np.full(100, remainder, dtype=np.int32)
                votes[:best_pixels] = 7
                vanilla[2:12, 2:12] = votes.reshape(10, 10)
                row = raster_overlap_evidence(me, vanilla, IDENTITY)[17]
                accepted = reason == "accepted"
                self.assertEqual(row["overlap_area_px"], "100")
                self.assertEqual(row["overlap_components"], "1")
                self.assertEqual(row["overlap_largest_share"], "1.000000")
                self.assertEqual(row["overlap_share"], f"{best_pixels / 100:.6f}")
                self.assertEqual(row["overlap_reason"], reason)
                self.assertEqual(row["overlap_ok"], "1" if accepted else "0")
                self.assertEqual(row["overlap_ancestor"], "7" if accepted else "")

    def test_containment_uses_candidate_area_in_entire_me_frame_including_empty(self):
        me = np.zeros((10, 20), dtype=np.int32)
        me[:, :8] = 17
        me[:, 8:10] = 18
        me[:, 10] = -9
        # The extra ten Vanilla columns are outside the sampling domain. The
        # ten ME-empty columns inside it still contribute to reverse coverage.
        vanilla = np.full((10, 30), 7, dtype=np.int32)
        rows = raster_overlap_evidence(me, vanilla, IDENTITY)
        self.assertEqual(set(rows), {17, 18})
        self.assertEqual(rows[17]["overlap_area_px"], "80")
        self.assertEqual(rows[17]["overlap_share"], "1.000000")
        self.assertEqual(rows[17]["overlap_containment"], "0.400000")
        self.assertEqual(rows[17]["overlap_ancestor"], "7")
        self.assertEqual(rows[18]["overlap_containment"], "0.100000")
        self.assertEqual(rows[18]["overlap_reason"], "insufficient_area")

    def test_nearest_even_rounding_and_both_bounds_are_chunk_independent(self):
        me = np.full((1, 7), 17, dtype=np.int32)
        vanilla = np.asarray([[4, 5]], dtype=np.int32)
        affine = np.asarray([[0.5, 0.0, -0.5], [0.0, 1.0, 0.0]], dtype=np.float64)
        # x = -.5, 0, .5, 1, 1.5, 2, 2.5: -.5 is geometrically out even
        # though it rounds to zero; .5 rounds to zero; 1.5 rounds beyond width.
        expected = raster_overlap_evidence(me, vanilla, affine)
        for chunk_pixels in (1, 2, 3, 7, 8, 262144):
            with self.subTest(chunk_pixels=chunk_pixels):
                self.assertEqual(
                    raster_overlap_evidence(me, vanilla, affine, chunk_pixels=chunk_pixels),
                    expected,
                )
        row = expected[17]
        self.assertEqual(row["overlap_area_px"], "7")
        self.assertEqual(row["overlap_best"], "4")
        self.assertEqual(row["overlap_runner"], "5")
        self.assertEqual(row["overlap_share"], "0.285714")
        self.assertEqual(row["overlap_runner_share"], "0.142857")
        self.assertEqual(row["overlap_margin"], "0.142857")
        self.assertEqual(row["overlap_assigned_share"], "0.428571")
        self.assertEqual(row["overlap_unregistered_share"], "0.571429")

    def test_empty_me_frame_has_no_rows_and_only_bare_finite_affines_are_used(self):
        vanilla = np.full((8, 8), 7, dtype=np.int32)
        self.assertEqual(
            raster_overlap_evidence(np.full((8, 8), -1, dtype=np.int32), vanilla, IDENTITY),
            {},
        )
        me = np.full((8, 8), 17, dtype=np.int32)
        bad_affines = (
            {"affine_me_to_vanilla": IDENTITY, "affine_combined": IDENTITY},
            np.eye(3, dtype=np.float64),
            np.asarray([[np.nan, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            np.asarray([[1.0, 0.0, np.inf], [0.0, 1.0, 0.0]]),
        )
        for affine in bad_affines:
            with self.subTest(affine=affine):
                with self.assertRaises((TypeError, ValueError)):
                    raster_overlap_evidence(me, vanilla, affine)


class StateGeographyEvidenceTests(unittest.TestCase):
    """Contract tests use only synthetic arrays and real temporary map files."""

    @staticmethod
    def _natural(
        pairs=10,
        correction=(2.0, 0.0),
        *,
        ok=True,
        fallback_reason=None,
        method="translation",
        residual_offset=None,
    ):
        source = np.asarray(
            [[1.0 + (index % 5), 1.0 + (index // 5)] for index in range(pairs)],
            dtype=np.float64,
        )
        correction = np.asarray(correction, dtype=np.float64)
        target = source + correction
        if residual_offset is not None:
            target = target + np.asarray(residual_offset, dtype=np.float64)
        correction_affine = np.array(
            [[1.0, 0.0, correction[0]], [0.0, 1.0, correction[1]]]
        )
        return {
            "ok": ok,
            "fallback_reason": fallback_reason,
            "method_used": method,
            "n_pairs_kept": pairs,
            "center_me_xy": [10.0, 10.0],
            "center_vanilla_xy": [12.0, 10.0],
            "radius_me_px": 128.0,
            "affine_me_to_vanilla": IDENTITY.tolist(),
            "correction_affine": correction_affine.tolist(),
            "affine_combined": correction_affine.tolist(),
            "blend_radius_factor": 2.0,
            "matches": {
                "river": {
                    "n_me_region": pairs,
                    "n_vanilla_region": pairs,
                    "n_kept": pairs,
                    "pairs": {
                        "me_vanilla_xy": source.tolist(),
                        "vanilla_xy": target.tolist(),
                    },
                },
                "lake": {
                    "n_me_region": 0,
                    "n_vanilla_region": 0,
                    "n_kept": 0,
                    "pairs": {"me_vanilla_xy": [], "vanilla_xy": []},
                },
            },
        }

    @classmethod
    def _accepted_row(cls, me_id=42, name="Synthetic", **natural_kwargs):
        natural = cls._natural(**natural_kwargs)
        points = np.asarray(
            [[1.0 + (index % 5), 1.0 + (index // 5)] for index in range(10)],
            dtype=np.float64,
        )
        weights = np.ones(len(points), dtype=np.float64)
        raster = np.full((32, 32), 7, dtype=np.int32)
        return evidence_row(me_id, name, points, weights, natural, raster)

    def test_state_geometry_is_area_weighted_and_covers_centroids(self):
        points, weights, center, radius = state_geometry(
            [2, 1],
            {1: (0.0, 0.0), 2: (300.0, 0.0)},
            np.asarray([0, 1, 3], dtype=np.int64),
        )
        np.testing.assert_allclose(points, [[0.0, 0.0], [300.0, 0.0]])
        np.testing.assert_allclose(weights, [1.0, 3.0])
        np.testing.assert_allclose(center, [225.0, 0.0])
        squared = np.sum((points - center) ** 2, axis=1)
        weighted_rms = np.sqrt(np.average(squared, weights=weights))
        self.assertGreaterEqual(radius, float(np.sqrt(squared.max())))
        self.assertGreaterEqual(radius, weighted_rms)
        # The radius is not allowed to collapse to the weighted RMS and leave
        # the furthest province centroid outside the natural-feature circle.
        self.assertGreater(radius, weighted_rms)

    def test_state_geometry_rejects_empty_and_partial_geometry(self):
        with self.assertRaisesRegex(ValueError, "^no_province_pixels$"):
            state_geometry([], {}, np.asarray([0, 0], dtype=np.int64))
        with self.assertRaisesRegex(ValueError, "^no_province_pixels$"):
            state_geometry([1], {1: (2.0, 2.0)}, np.asarray([0, 0], dtype=np.int64))
        with self.assertRaisesRegex(ValueError, "^missing_province_pixels$"):
            state_geometry(
                [1, 2],
                {1: (2.0, 2.0)},
                np.asarray([0, 4, 0], dtype=np.int64),
            )

    def test_vote_uses_pixel_area_weights_not_province_counts(self):
        raster = np.zeros((3, 4), dtype=np.int32)
        raster[1, 1] = 2
        raster[1, 2] = 1
        result = vote_centroids(
            np.asarray([[1.0, 1.0], [2.0, 1.0]]),
            np.asarray([9.0, 1.0]),
            IDENTITY,
            raster,
        )
        self.assertEqual(result["natural_best"], 2)
        self.assertAlmostEqual(result["natural_share"], 0.9)
        self.assertEqual(result["natural_runner"], 1)
        self.assertAlmostEqual(result["natural_runner_share"], 0.1)
        self.assertAlmostEqual(result["natural_margin"], 0.8)
        self.assertAlmostEqual(result["natural_sampled_share"], 1.0)

    def test_vote_keeps_outside_and_unassigned_area_in_denominator(self):
        raster = np.zeros((3, 3), dtype=np.int32)
        raster[0, 0] = 7
        raster[2, 2] = 8
        result = vote_centroids(
            np.asarray([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 1.0]]),
            np.asarray([4.0, 2.0, 1.0, 3.0]),
            IDENTITY,
            raster,
        )
        self.assertEqual(result["natural_best"], 7)
        self.assertAlmostEqual(result["natural_share"], 0.4)
        self.assertEqual(result["natural_runner"], 8)
        self.assertAlmostEqual(result["natural_runner_share"], 0.1)
        self.assertAlmostEqual(result["natural_margin"], 0.3)
        self.assertAlmostEqual(result["natural_sampled_share"], 0.5)

    def test_vote_rounds_nearest_pixels_and_rejects_geometric_or_rounded_outside(self):
        raster = np.zeros((2, 2), dtype=np.int32)
        raster[0, 0] = 4
        raster[1, 1] = 5
        result = vote_centroids(
            np.asarray(
                [
                    [0.49, 0.49],  # nearest pixel (0, 0)
                    [1.49, 1.49],  # nearest pixel (1, 1)
                    [1.99, 0.40],  # geometrically in, rounds beyond width
                    [-0.10, 0.40],  # geometrically outside
                ]
            ),
            np.ones(4, dtype=np.float64),
            IDENTITY,
            raster,
        )
        self.assertEqual(result["natural_best"], 4)
        self.assertEqual(result["natural_runner"], 5)
        self.assertAlmostEqual(result["natural_share"], 0.25)
        self.assertAlmostEqual(result["natural_runner_share"], 0.25)
        self.assertAlmostEqual(result["natural_sampled_share"], 0.5)

    def test_vote_ties_choose_lower_positive_state_and_blank_runner_when_absent(self):
        raster = np.zeros((1, 3), dtype=np.int32)
        raster[0, 0] = 2
        raster[0, 1] = 1
        raster[0, 2] = -9
        tied = vote_centroids(
            np.asarray([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]),
            np.ones(3, dtype=np.float64),
            IDENTITY,
            raster,
        )
        self.assertEqual(tied["natural_best"], 1)
        self.assertEqual(tied["natural_runner"], 2)
        self.assertAlmostEqual(tied["natural_sampled_share"], 2.0 / 3.0)

        only_one = vote_centroids(
            np.asarray([[0.0, 0.0], [3.0, 0.0]]),
            np.asarray([2.0, 1.0]),
            IDENTITY,
            raster,
        )
        self.assertEqual(only_one["natural_best"], 2)
        self.assertEqual(only_one["natural_runner"], "")
        self.assertEqual(only_one["natural_runner_share"], 0.0)
        self.assertAlmostEqual(only_one["natural_sampled_share"], 2.0 / 3.0)

    def test_successful_translation_emits_candidate_and_physical_marker(self):
        row = self._accepted_row()
        self.assertEqual(set(row), set(COLUMNS))
        self.assertEqual(row["natural_ok"], "1")
        self.assertEqual(row["natural_best"], "7")
        self.assertEqual(row["natural_share"], "1.000000")
        self.assertEqual(row["natural_runner"], "")
        self.assertEqual(row["natural_runner_share"], "0.000000")
        self.assertEqual(row["natural_margin"], "1.000000")
        self.assertEqual(row["natural_residual_p50"], "0.000000")
        self.assertEqual(row["natural_residual_p95"], "0.000000")
        self.assertEqual(row["raw_geography_ancestor"], "7")
        self.assertEqual(
            row["raw_geography_marker"],
            "coast_bbox_icp;local_river_lake_translation;river_pairs=10;"
            "lake_pairs=0;residual_p50_px=0.000000;residual_p95_px=0.000000;"
            "center_me_xy=10.000000,10.000000;radius_me_px=128.000000",
        )
        self.assertEqual(row["natural_reason"], "accepted")
        self.assertTrue(all(row[column] == "" for column in OVERLAP_COLUMNS))

    def test_fallback_weak_pairs_and_excess_residual_withhold_candidates(self):
        fallback = self._natural(
            ok=False, fallback_reason="no_feature_points", method="coast"
        )
        fallback_row = evidence_row(
            1,
            "Fallback",
            np.asarray([[1.0, 1.0]]),
            np.asarray([1.0]),
            fallback,
            np.full((8, 8), 7, dtype=np.int32),
        )
        weak = self._natural(pairs=9)
        weak_row = evidence_row(
            2,
            "Weak",
            np.asarray([[1.0, 1.0]]),
            np.asarray([1.0]),
            weak,
            np.full((8, 8), 7, dtype=np.int32),
        )
        excessive = self._natural(residual_offset=(20.0, 0.0))
        excessive_row = evidence_row(
            3,
            "Excessive",
            np.asarray([[1.0, 1.0]]),
            np.asarray([1.0]),
            excessive,
            np.full((32, 32), 7, dtype=np.int32),
        )
        for row in (fallback_row, weak_row, excessive_row):
            self.assertEqual(row["natural_ok"], "0")
            self.assertEqual(row["natural_best"], "")
            self.assertEqual(row["natural_runner"], "")
            self.assertEqual(row["raw_geography_ancestor"], "")
            self.assertEqual(row["raw_geography_marker"], "")
        self.assertEqual(fallback_row["natural_reason"], "fallback:no_feature_points")
        self.assertEqual(weak_row["natural_reason"], "weak_pairs")
        self.assertEqual(excessive_row["natural_reason"], "residual_p50")

    def test_weak_vote_keeps_diagnostics_but_emits_no_ancestor(self):
        natural = self._natural()
        raster = np.zeros((8, 8), dtype=np.int32)
        raster[1, 3] = 7
        raster[1, 4] = 8
        row = evidence_row(
            9,
            "Weak vote",
            np.asarray([[1.0, 1.0], [2.0, 1.0]]),
            np.asarray([4.0, 1.0]),
            natural,
            raster,
        )
        self.assertEqual(row["natural_ok"], "1")
        self.assertEqual(row["natural_best"], "7")
        self.assertEqual(row["natural_runner"], "8")
        self.assertEqual(row["natural_share"], "0.800000")
        self.assertEqual(row["natural_margin"], "0.600000")
        self.assertEqual(row["raw_geography_ancestor"], "")
        self.assertEqual(row["raw_geography_marker"], "")
        self.assertEqual(row["natural_reason"], "low_share")

    def test_write_evidence_is_sorted_fixed_schema_and_byte_stable(self):
        row_10 = self._accepted_row(10, "Ten")
        row_2 = self._accepted_row(2, "Two")
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as directory:
            root = Path(directory)
            first = root / "nested" / "first.tsv"
            second = root / "nested" / "second.tsv"
            write_evidence(first, [row_10, row_2])
            write_evidence(second, [row_2, row_10])
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()
        self.assertEqual(first_bytes, second_bytes)
        self.assertTrue(first_bytes.endswith(b"\n"))
        lines = first_bytes.decode("utf-8").splitlines()
        self.assertEqual(lines[0], "\t".join(COLUMNS))
        parsed = list(csv.DictReader(lines, delimiter="\t"))
        self.assertEqual([row["me_id"] for row in parsed], ["2", "10"])
        self.assertEqual(list(parsed[0]), COLUMNS)

    def test_load_states_uses_filename_id_and_comment_aware_membership(self):
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as directory:
            root = Path(directory)
            states_dir = root / "history" / "states"
            loc_dir = root / "localisation" / "english"
            states_dir.mkdir(parents=True)
            loc_dir.mkdir(parents=True)
            (states_dir / "001-deceptive-name.txt").write_text(
                """
                # id = 999 provinces = { 999 }
                state = {
                    id = 42
                    provinces = {
                        12
                        # provinces = { 999 }
                        10
                    }
                }
                """,
                encoding="utf-8",
            )
            (states_dir / "017-file-suffix.txt").write_text(
                "state = { id = 7 provinces = { 20 } }\n",
                encoding="utf-8",
            )
            (loc_dir / "states_l_english.yml").write_text(
                """
                l_english:
                  STATE_42:0 "Localized Forty-Two"
                  PROV_10:0 "Not a state label"
                  # STATE_7:0 "Comment must not win"
                """,
                encoding="utf-8",
            )
            states = load_states(root)
        self.assertEqual(
            states,
            {
                1: {"name": "deceptive-name", "provinces": [10, 12]},
                17: {"name": "file-suffix", "provinces": [20]},
            },
        )

    @staticmethod
    def _write_synthetic_map(root, state_ids):
        root = Path(root)
        map_dir = root / "map"
        state_dir = root / "history" / "states"
        map_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        height = width = 96
        provinces = np.full((height, width), 3, dtype=np.int32)
        provinces[16:80, 16:48] = 1
        provinces[16:80, 48:80] = 2
        colours = {0: (0, 0, 0), 1: (255, 0, 0), 2: (0, 255, 0), 3: (0, 0, 255)}
        image = np.zeros((height, width, 3), dtype=np.uint8)
        for province, colour in colours.items():
            image[provinces == province] = colour
        Image.fromarray(image, mode="RGB").save(map_dir / "provinces.bmp")
        (map_dir / "definition.csv").write_text(
            "\n".join(
                [
                    "0;0;0;0;sea;false;false;0",
                    "1;255;0;0;land;false;false;1",
                    "2;0;255;0;land;false;false;1",
                    "3;0;0;255;sea;false;false;0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        rivers = np.full((height, width), 255, dtype=np.uint8)
        rivers[20:76, 30] = 0
        rivers[20:76, 50] = 0
        river_image = Image.fromarray(rivers, mode="P")
        # A complete BMP palette avoids Pillow writing a truncated palette for this
        # sparse synthetic image on Windows; registration reads the index values.
        palette = [0] * (256 * 3)
        palette[255 * 3:255 * 3 + 3] = [255, 255, 255]
        river_image.putpalette(palette)
        river_image.save(map_dir / "rivers.bmp", format="BMP")
        for filename, sid, province in state_ids:
            (state_dir / filename).write_text(
                f"state = {{ id = {sid} provinces = {{ {province} }} }}\n",
                encoding="utf-8",
            )

    def test_synthetic_identical_maps_exercise_real_coast_and_natural_pipeline(self):
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as directory:
            root = Path(directory)
            vanilla = root / "vanilla"
            mod = root / "mod"
            self._write_synthetic_map(
                vanilla,
                [("001-vanilla.txt", 7, 1), ("002-vanilla.txt", 8, 2)],
            )
            self._write_synthetic_map(
                mod,
                [("017-me.txt", 17, 1), ("018-me.txt", 18, 2)],
            )
            with (
                mock.patch.object(
                    geography.registration, "fit_coast_warp",
                    wraps=geography.registration.fit_coast_warp,
                ) as full_fit,
                mock.patch.object(
                    geography, "raster_overlap_evidence", wraps=raster_overlap_evidence,
                ) as full_overlap,
            ):
                rows, coast = produce_evidence(vanilla, mod)
            output = root / "output" / "geography.tsv"
            write_evidence(output, list(reversed(rows)))
            data = output.read_text(encoding="utf-8")
            cli_output = root / "output" / "cli.tsv"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch.object(
                    geography.registration, "fit_coast_warp",
                    wraps=geography.registration.fit_coast_warp,
                ) as selected_fit,
                mock.patch.object(
                    geography, "raster_overlap_evidence", wraps=raster_overlap_evidence,
                ) as selected_overlap,
            ):
                self.assertEqual(
                    main(
                        [
                            "--vanilla",
                            str(vanilla),
                            "--mod",
                            str(mod),
                            "--me-id",
                            "17",
                            "--output",
                            str(cli_output),
                        ]
                    ),
                    0,
                )
            cli_data = cli_output.read_text(encoding="utf-8")
            (vanilla / "map" / "rivers.bmp").unlink()
            (mod / "map" / "rivers.bmp").unlink()
            no_feature_rows, no_feature_coast = produce_evidence(vanilla, mod)
        full_fit.assert_called_once()
        selected_fit.assert_called_once()
        full_overlap.assert_called_once()
        selected_overlap.assert_called_once()
        for full_raster, selected_raster in zip(
            full_overlap.call_args.args[:2], selected_overlap.call_args.args[:2]
        ):
            np.testing.assert_array_equal(full_raster, selected_raster)
        self.assertEqual(set(np.unique(selected_overlap.call_args.args[0])), {0, 17, 18})
        self.assertEqual(set(np.unique(selected_overlap.call_args.args[1])), {0, 1, 2})
        np.testing.assert_array_equal(
            full_overlap.call_args.args[2],
            np.asarray(coast["affine_me_to_vanilla"], dtype=np.float64),
        )
        np.testing.assert_array_equal(
            full_overlap.call_args.args[2], selected_overlap.call_args.args[2]
        )
        self.assertTrue(coast["ok"])
        self.assertEqual([row["me_id"] for row in rows], ["17", "18"])
        self.assertEqual([row["raw_geography_ancestor"] for row in rows], ["1", "2"])
        self.assertTrue(all(row["natural_ok"] == "1" for row in rows))
        self.assertTrue(all("local_river_lake_translation" in row["raw_geography_marker"] for row in rows))
        self.assertEqual(data.splitlines()[0], "\t".join(COLUMNS))
        self.assertEqual([line.split("\t", 1)[0] for line in data.splitlines()[1:]], ["17", "18"])
        self.assertEqual(cli_data.splitlines()[0], "\t".join(COLUMNS))
        self.assertEqual(
            [line.split("\t", 1)[0] for line in cli_data.splitlines()[1:]],
            ["17"],
        )
        self.assertEqual(list(csv.DictReader(cli_data.splitlines(), delimiter="\t")), rows[:1])
        for row, expected_best in zip(rows, ("1", "2")):
            self.assertEqual(row["overlap_ok"], "1")
            self.assertEqual(row["overlap_method"], "coast_bbox_affine_raster")
            self.assertEqual(row["overlap_domain"], "me_native_land")
            self.assertEqual(row["overlap_area_px"], "2048")
            self.assertEqual(row["overlap_best"], expected_best)
            self.assertEqual(row["overlap_share"], "1.000000")
            self.assertEqual(row["overlap_runner"], "")
            self.assertEqual(row["overlap_runner_share"], "0.000000")
            self.assertEqual(row["overlap_margin"], "1.000000")
            self.assertEqual(row["overlap_assigned_share"], "1.000000")
            self.assertEqual(row["overlap_containment"], "1.000000")
            self.assertEqual(row["overlap_components"], "1")
            self.assertEqual(row["overlap_largest_share"], "1.000000")
            self.assertEqual(row["overlap_largest_best"], expected_best)
            self.assertEqual(row["overlap_largest_candidate_share"], "1.000000")
            self.assertEqual(row["overlap_components_agree"], "1")
            self.assertEqual(row["overlap_unregistered_share"], "0.000000")
            self.assertEqual(row["overlap_ancestor"], expected_best)
            self.assertEqual(row["overlap_marker"], "coast_bbox_affine_raster_overlap")
            self.assertEqual(row["overlap_reason"], "accepted")
            self.assertNotIn("coast_bbox_affine_raster_overlap", row["raw_geography_marker"])
        self.assertEqual(
            [{column: row[column] for column in OVERLAP_COLUMNS} for row in no_feature_rows],
            [{column: row[column] for column in OVERLAP_COLUMNS} for row in rows],
        )
        self.assertTrue(no_feature_coast["ok"])
        self.assertEqual([row["natural_best"] for row in no_feature_rows], ["", ""])
        self.assertEqual(
            [row["raw_geography_ancestor"] for row in no_feature_rows], ["", ""]
        )
        self.assertEqual(
            [row["natural_reason"] for row in no_feature_rows],
            ["no_feature_points", "no_feature_points"],
        )

    def test_missing_or_failed_overlap_keeps_natural_and_raw_rows_unchanged(self):
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as directory:
            root = Path(directory)
            vanilla, mod = root / "vanilla", root / "mod"
            self._write_synthetic_map(
                vanilla, [("001-vanilla.txt", 7, 1), ("002-vanilla.txt", 8, 2)]
            )
            self._write_synthetic_map(
                mod, [("017-me.txt", 17, 1), ("018-me.txt", 18, 2)]
            )
            baseline, _ = produce_evidence(vanilla, mod)
            expected = [
                {column: row[column] for column in LEGACY_COLUMNS} for row in baseline
            ]
            for case, patch_kwargs in (
                ("missing", {"return_value": {}}),
                ("failed", {"side_effect": ValueError("synthetic overlap unavailable")}),
            ):
                with self.subTest(case=case):
                    with mock.patch.object(
                        geography, "raster_overlap_evidence", **patch_kwargs
                    ) as overlap:
                        rows, coast = produce_evidence(vanilla, mod)
                    overlap.assert_called_once()
                    self.assertTrue(coast["ok"])
                    self.assertEqual(
                        [{column: row[column] for column in LEGACY_COLUMNS} for row in rows],
                        expected,
                    )
                    self.assertTrue(
                        all(row[column] == "" for row in rows for column in OVERLAP_COLUMNS)
                    )

    def test_pipeline_land_census_excludes_sea_lake_and_dummy_state_members(self):
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as directory:
            root = Path(directory)
            vanilla, mod = root / "vanilla", root / "mod"
            for map_root, state_ids in (
                (vanilla, [("001-vanilla.txt", 7, 1), ("002-vanilla.txt", 8, 2)]),
                (mod, [("017-me.txt", 17, 1), ("018-me.txt", 18, 2)]),
            ):
                self._write_synthetic_map(map_root, state_ids)
                province_path = map_root / "map" / "provinces.bmp"
                with Image.open(province_path) as image:
                    colours = np.asarray(image).copy()
                colours[0, 0] = [0, 0, 0]
                colours[0, 1] = [255, 255, 0]
                Image.fromarray(colours, mode="RGB").save(province_path)
                with (map_root / "map" / "definition.csv").open(
                    "a", encoding="utf-8"
                ) as definitions:
                    definitions.write("4;255;255;0;lake;false;false;0\n")
                (map_root / "history" / "states" / "019-nonland.txt").write_text(
                    "state = { id = 19 provinces = { 0 3 4 } }\n", encoding="utf-8"
                )
            with mock.patch.object(
                geography, "raster_overlap_evidence", wraps=raster_overlap_evidence
            ) as overlap:
                rows, coast = produce_evidence(vanilla, mod, me_ids=[17])
        self.assertTrue(coast["ok"])
        overlap.assert_called_once()
        me_raster, vanilla_raster = overlap.call_args.args[:2]
        self.assertEqual(set(np.unique(me_raster)), {0, 17, 18})
        self.assertEqual(set(np.unique(vanilla_raster)), {0, 1, 2})
        for raster in (me_raster, vanilla_raster):
            self.assertEqual(raster[0, 0], 0)  # Dummy province 0.
            self.assertEqual(raster[0, 1], 0)  # Lake province 4.
            self.assertEqual(raster[0, 2], 0)  # Sea province 3.
            self.assertEqual(np.count_nonzero(raster), 4096)
        self.assertEqual([row["me_id"] for row in rows], ["17"])
        self.assertEqual(rows[0]["overlap_area_px"], "2048")


if __name__ == "__main__":
    unittest.main()
