"""Deterministic synthetic-raster tests for state_relation_census.

No map files, provinces.bmp, or canonical tables: every case uses tiny
in-memory state rasters and bare 2x3 affines (or map_points-compatible
dicts). Classification tests build row dicts directly.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest import mock

import numpy as np


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from state_relation_census import (  # noqa: E402
    CLAIM,
    MIN_RELATION_PX,
    RELATION_CLASSES,
    STATUS_CONFIDENT,
    STATUS_UNCERTAIN_ONLY,
    build_border_band,
    census_component,
    classify_relation_matrix,
)


IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _full(shape, value):
    return np.full(shape, value, dtype=np.int32)


def _reference_band(raster, radius):
    """O(N * radius^2) reference: every pixel within Chebyshev distance
    ``radius`` of an in-frame positive-side boundary seed. Scans only
    in-frame offsets, so it can neither leak past the raster nor wrap."""
    raster = np.asarray(raster)
    h, w = raster.shape
    seeds = np.zeros((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            if raster[y, x] <= 0:
                continue
            if y == 0 or y == h - 1 or x == 0 or x == w - 1:
                seeds[y, x] = True
                continue
            if (raster[y - 1, x] != raster[y, x]
                    or raster[y + 1, x] != raster[y, x]
                    or raster[y, x - 1] != raster[y, x]
                    or raster[y, x + 1] != raster[y, x]):
                seeds[y, x] = True
    band = np.zeros((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            for sy in range(max(0, y - radius), min(h, y + radius + 1)):
                for sx in range(max(0, x - radius), min(w, x + radius + 1)):
                    if seeds[sy, sx]:
                        band[y, x] = True
    return band


class BorderBandTests(unittest.TestCase):
    """Seeds, bounded dilation, and no wraparound."""

    def test_matches_reference_on_mixed_raster(self):
        raster = np.zeros((9, 11), dtype=np.int32)
        raster[:, 3:8] = 1          # vertical land strip
        raster[2:5, 5:11] = 2       # second state reaching the east edge
        raster[6, 1:3] = 3          # small third state touching water
        for radius in (0, 1, 2, 3):
            np.testing.assert_array_equal(
                build_border_band(raster, radius=radius),
                _reference_band(raster, radius))

    def test_split_raster_marks_only_within_radius(self):
        # Tall raster so middle rows are far from edge seeds; seam at x=8.
        raster = np.empty((12, 16), dtype=np.int32)
        raster[:, :8] = 1
        raster[:, 8:] = 2
        band = build_border_band(raster, radius=2)
        self.assertTrue(band.dtype == np.bool_)
        # Both seam columns are seeds; band reaches two steps either side.
        for x in (5, 6, 7, 8, 9, 10):
            self.assertTrue(band[6, x], f"x={x}")
        # Three steps from the seam and far from any edge: not banded.
        self.assertFalse(band[6, 4])
        self.assertFalse(band[6, 11])

    def test_uniform_raster_marks_only_the_edge_ring(self):
        band = build_border_band(np.full((8, 8), 5, dtype=np.int32),
                                 radius=2)
        # Frame edge is a boundary: the outer two rings are banded.
        self.assertTrue(band[1, 4] and band[2, 2] and band[6, 4])
        self.assertFalse(band[3, 3])
        self.assertFalse(band[4, 4])

    def test_water_pixels_are_banded_but_never_seeds(self):
        # A wide single-row-ish strip: water left, land right.
        raster = np.zeros((6, 12), dtype=np.int32)
        raster[:, 6:] = 1
        band = build_border_band(raster, radius=1)
        # Water pixel one step from the land strip is inside the band.
        self.assertTrue(band[3, 5])
        self.assertFalse(band[3, 4])
        # Water carries no seeds of its own on the open-water side.
        self.assertFalse(band[3, 0])

    def test_radius_zero_returns_seed_boundary_only(self):
        raster = np.full((4, 4), 1, dtype=np.int32)
        raster[1:3, 1:3] = 2
        band = build_border_band(raster, radius=0)
        # Interior block pixels are seeds; perimeter pixels are edge seeds;
        # nothing else is marked.
        self.assertTrue(band[1, 1] and band[2, 2] and band[0, 3])
        np.testing.assert_array_equal(
            band, _reference_band(raster, 0))

    def test_empty_and_validation(self):
        empty = np.zeros((0, 0), dtype=np.int32)
        self.assertFalse(build_border_band(empty, radius=2).any())
        with self.assertRaises(TypeError):
            build_border_band(np.zeros((4, 4), dtype=np.float64))
        with self.assertRaises(ValueError):
            build_border_band(np.zeros(4, dtype=np.int32))
        with self.assertRaises(ValueError):
            build_border_band(_full((4, 4), 1), radius=-1)


class CensusComponentTests(unittest.TestCase):
    """Chunked map_points sampling with explicit denominators."""

    def setUp(self):
        self.source = _full((4, 4), 10)
        self.target = _full((4, 4), 7)
        self.land = np.ones((4, 4), dtype=bool)
        self.mask = np.ones((4, 4), dtype=bool)
        self.no_band = np.zeros((4, 4), dtype=bool)

    def _census(self, **kwargs):
        args = dict(
            source_state_raster=self.source,
            target_state_raster=self.target,
            target_land_mask=self.land,
            source_mask=self.mask,
            registration=IDENTITY,
            border_band=self.no_band,
            min_relation_px=1,
        )
        args.update(kwargs)
        return census_component(**args)

    def test_identity_full_overlap_single_confident_row(self):
        result = self._census()
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["source_id"], 10)
        self.assertEqual(row["target_id"], 7)
        self.assertEqual(row["confident_hits_px"], 16)
        self.assertEqual(row["uncertain_hits_px"], 0)
        self.assertEqual(row["source_area_px"], 16)
        self.assertEqual(row["source_sampled_px"], 16)
        self.assertEqual(row["target_native_area_px"], 16)
        self.assertEqual(row["reverse_hits_px"], 16)
        self.assertEqual(row["forward_share"], 1.0)
        self.assertEqual(row["reverse_share"], 1.0)
        self.assertTrue(row["significant"])
        self.assertEqual(row["status"], STATUS_CONFIDENT)
        self.assertEqual(row["claim"], CLAIM)
        self.assertEqual(
            result["source_totals"][10],
            {"source_area_px": 16, "source_sampled_px": 16,
             "confident_px": 16, "uncertain_px": 0, "outside_px": 0,
             "water_px": 0, "unassigned_px": 0})
        self.assertEqual(result["masked_px"], 16)
        self.assertEqual(result["sampled_state_px"], 16)

    def test_shifted_affine_counts_everything_outside(self):
        result = self._census(registration=np.array(
            [[1.0, 0.0, 40.0], [0.0, 1.0, 0.0]]))
        self.assertEqual(result["rows"], [])
        totals = result["source_totals"][10]
        self.assertEqual(totals["outside_px"], 16)
        self.assertEqual(totals["confident_px"], 0)

    def test_rounded_outside_joins_outside_denominator(self):
        # x=0 maps to 3.6: geometrically inside (<4) but rint() -> 4.
        result = self._census(
            source_state_raster=np.array([[10]], dtype=np.int32),
            source_mask=np.ones((1, 1), dtype=bool),
            registration=np.array([[1.0, 0.0, 3.6], [0.0, 1.0, 0.0]]))
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["source_totals"][10]["outside_px"], 1)

    def test_water_and_unassigned_are_explicit(self):
        self.target[:] = 0
        water = self._census(
            target_land_mask=np.zeros((4, 4), dtype=bool))
        self.assertEqual(water["source_totals"][10]["water_px"], 16)
        self.assertEqual(water["source_totals"][10]["unassigned_px"], 0)
        land_no_state = self._census()
        self.assertEqual(
            land_no_state["source_totals"][10]["unassigned_px"], 16)
        self.assertEqual(
            land_no_state["source_totals"][10]["water_px"], 0)

    def test_band_pixels_count_uncertain_not_confident(self):
        band = np.zeros((4, 4), dtype=bool)
        band[:, :2] = True
        result = self._census(border_band=band)
        row = result["rows"][0]
        self.assertEqual(row["confident_hits_px"], 8)
        self.assertEqual(row["uncertain_hits_px"], 8)
        # Band pixels are uncertain and never count as confident reverse
        # hits: only the right half's 8 unique target pixels count.
        self.assertEqual(row["reverse_hits_px"], 8)
        totals = result["source_totals"][10]
        self.assertEqual(totals["confident_px"], 8)
        self.assertEqual(totals["uncertain_px"], 8)

    def test_all_band_row_is_uncertain_only_and_not_significant(self):
        result = self._census(
            border_band=np.ones((4, 4), dtype=bool))
        row = result["rows"][0]
        self.assertEqual(row["confident_hits_px"], 0)
        self.assertEqual(row["uncertain_hits_px"], 16)
        self.assertFalse(row["significant"])
        self.assertEqual(row["status"], STATUS_UNCERTAIN_ONLY)

    def test_source_mask_bounds_samples_not_native_denominator(self):
        mask = np.zeros((4, 4), dtype=bool)
        mask[:, :2] = True
        result = self._census(source_mask=mask)
        row = result["rows"][0]
        self.assertEqual(row["source_sampled_px"], 8)
        self.assertEqual(row["source_area_px"], 16)
        self.assertEqual(row["confident_hits_px"], 8)
        self.assertEqual(row["forward_share"], 1.0)

    def test_reverse_counts_unique_target_pixels_not_source_hits(self):
        # 8x8 source downscaled 0.5 onto a 4x4 target: 49 source samples
        # land inside (the rightmost source column rounds outside), but
        # they cover only the target's 16 distinct pixels. Reverse
        # coverage is a true target-frame ratio, never source hits over
        # native pixels.
        source = _full((8, 8), 10)
        target = _full((4, 4), 7)
        land = np.ones((4, 4), dtype=bool)
        mask = np.ones((8, 8), dtype=bool)
        band = np.zeros((4, 4), dtype=bool)
        result = census_component(
            source, target, land, mask,
            np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0]]), band,
            min_relation_px=1)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["confident_hits_px"], 49)
        self.assertEqual(row["reverse_hits_px"], 16)
        self.assertEqual(row["target_native_area_px"], 16)
        self.assertEqual(row["reverse_share"], 1.0)

    def test_masked_nonstate_pixels_never_create_cells(self):
        source = np.array([[10, 10, -1, -1], [10, 10, 0, 0],
                           [10, 10, 10, 10], [10, 10, 10, 10]],
                          dtype=np.int32)
        result = self._census(source_state_raster=source)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["confident_hits_px"], 12)
        self.assertEqual(result["masked_px"], 16)
        self.assertEqual(result["sampled_state_px"], 12)

    def test_two_states_each_retain_their_cell(self):
        source = np.block([[_full((4, 2), 10), _full((4, 2), 11)]])
        target = np.block([[_full((4, 2), 7), _full((4, 2), 8)]])
        result = self._census(
            source_state_raster=source, target_state_raster=target)
        self.assertEqual(
            [(r["source_id"], r["target_id"], r["confident_hits_px"])
             for r in result["rows"]],
            [(10, 7, 8), (11, 8, 8)])

    def test_every_positive_cell_is_retained_not_collapsed(self):
        # A split target: left half 7, right half 8 — both cells survive.
        target = np.block([[_full((4, 2), 7), _full((4, 2), 8)]])
        result = self._census(target_state_raster=target)
        self.assertEqual(
            [(r["source_id"], r["target_id"]) for r in result["rows"]],
            [(10, 7), (10, 8)])

    def test_dict_registration_routes_through_map_points(self):
        registration = {"affine_me_to_vanilla": IDENTITY.tolist()}
        result = self._census(registration=registration)
        self.assertEqual(result["rows"][0]["confident_hits_px"], 16)

    def test_chunk_size_does_not_change_counts(self):
        source = np.resize(
            np.arange(1, 21, dtype=np.int32), (8, 8))
        target = np.resize(
            np.arange(1, 11, dtype=np.int32), (8, 8))
        land = np.ones((8, 8), dtype=bool)
        mask = np.ones((8, 8), dtype=bool)
        band = build_border_band(target, radius=1)
        baseline = census_component(
            source, target, land, mask, IDENTITY, band,
            min_relation_px=1)
        with mock.patch("state_relation_census.CHUNK_PIXELS", 7):
            chunked = census_component(
                source, target, land, mask, IDENTITY, band,
                min_relation_px=1)
        self.assertEqual(baseline, chunked)

    def test_result_is_json_serialisable(self):
        result = self._census()
        self.assertIsInstance(json.dumps(result), str)

    def test_empty_mask_returns_empty_result(self):
        result = self._census(
            source_mask=np.zeros((4, 4), dtype=bool))
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["source_totals"], {})
        self.assertEqual(result["masked_px"], 0)

    def test_target_without_states_still_counts_water(self):
        self.target[:] = -5
        self.land[:] = False
        result = self._census()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["target_ids"], [])
        self.assertEqual(result["source_totals"][10]["water_px"], 16)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self._census(registration=np.array(
                [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]))  # det <= 0
        with self.assertRaises(ValueError):
            self._census(registration=np.eye(3))
        with self.assertRaises(TypeError):
            self._census(source_state_raster=np.zeros((4, 4), float))
        with self.assertRaises(TypeError):
            self._census(source_state_raster=np.ones((4, 4), bool))
        with self.assertRaises(ValueError):
            self._census(source_mask=np.zeros((3, 3), bool))
        with self.assertRaises(ValueError):
            self._census(border_band=np.zeros((3, 3), bool))
        with self.assertRaises(ValueError):
            self._census(min_relation_px=0)


class ClassifyRelationMatrixTests(unittest.TestCase):
    """Deterministic k:n labels on merged significant cells."""

    def _row(self, source, target, confident, uncertain=0,
             source_area=100, sampled=100, target_native=200,
             reverse=None):
        if reverse is None:
            reverse = confident
        return {
            "source_id": source,
            "target_id": target,
            "confident_hits_px": confident,
            "uncertain_hits_px": uncertain,
            "source_area_px": source_area,
            "source_sampled_px": sampled,
            "forward_share": confident / sampled if sampled else 0.0,
            "target_native_area_px": target_native,
            "reverse_hits_px": reverse,
            "reverse_share": min(
                reverse / target_native if target_native else 0.0, 1.0),
            "significant": confident >= MIN_RELATION_PX,
            "status": STATUS_CONFIDENT if confident
            else STATUS_UNCERTAIN_ONLY,
            "claim": CLAIM,
        }

    def test_single_significant_cell_is_one_to_one(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 100)], min_relation_px=64)
        self.assertEqual(rows[0]["relation"], "one_to_one")
        self.assertTrue(rows[0]["significant"])
        self.assertEqual(rows[0]["reverse_hits_px"], 100)
        self.assertEqual(rows[0]["reverse_share"], 0.5)
        self.assertEqual(rows[0]["reverse_overlap_possible"], 0)

    def test_one_source_two_targets_is_one_to_many_on_both(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 80), self._row(10, 8, 70)],
            min_relation_px=64)
        self.assertEqual(
            [r["relation"] for r in rows],
            ["one_to_many", "one_to_many"])

    def test_two_sources_one_target_is_many_to_one(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 80), self._row(11, 7, 70)],
            min_relation_px=64)
        self.assertEqual(
            [r["relation"] for r in rows],
            ["many_to_one", "many_to_one"])

    def test_two_by_two_block_is_many_to_many(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 80), self._row(10, 8, 70),
             self._row(11, 7, 90), self._row(11, 8, 65)],
            min_relation_px=64)
        self.assertEqual(
            [r["relation"] for r in rows], ["many_to_many"] * 4)

    def test_degrees_ignore_insignificant_cells(self):
        # 10->8 is below threshold, so 10 still counts as 1:n with only 7.
        rows = classify_relation_matrix(
            [self._row(10, 7, 80), self._row(10, 8, 10)],
            min_relation_px=64)
        self.assertEqual(rows[0]["relation"], "one_to_one")
        self.assertEqual(rows[1]["relation"], "uncertain")

    def test_uncertain_only_cell_is_retained_as_uncertain(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 0, uncertain=40)], min_relation_px=64)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation"], "uncertain")
        self.assertEqual(rows[0]["status"], STATUS_UNCERTAIN_ONLY)
        self.assertEqual(rows[0]["uncertain_hits_px"], 40)

    def test_duplicate_cells_merge_before_degrees(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 40), self._row(10, 7, 40),
             self._row(10, 8, 70)],
            min_relation_px=64)
        # Merged 10->7 = 80 significant, so 10 has two significant targets.
        self.assertEqual(
            [r["relation"] for r in rows],
            ["one_to_many", "one_to_many"])
        merged = rows[0]
        self.assertEqual(merged["confident_hits_px"], 80)
        self.assertEqual(merged["source_sampled_px"], 200)
        self.assertEqual(merged["source_area_px"], 100)
        self.assertEqual(merged["reverse_hits_px"], 80)
        self.assertEqual(merged["reverse_overlap_possible"], 0)

    def test_reverse_merge_caps_share_and_flags_overlap(self):
        # Two components each uniquely covering the same 160 of 200
        # target pixels sum to 320 > 200: the share caps at 1.0 and the
        # overlap flag is set instead of emitting a ratio > 1.
        rows = classify_relation_matrix(
            [self._row(10, 7, 40, reverse=160),
             self._row(10, 7, 40, reverse=160)],
            min_relation_px=64)
        self.assertEqual(len(rows), 1)
        merged = rows[0]
        self.assertEqual(merged["reverse_hits_px"], 320)
        self.assertEqual(merged["reverse_share"], 1.0)
        self.assertEqual(merged["reverse_overlap_possible"], 1)
        # A merged sum within the denominator keeps the true ratio.
        rows = classify_relation_matrix(
            [self._row(10, 7, 40, reverse=60),
             self._row(10, 7, 40, reverse=60)],
            min_relation_px=64)
        self.assertEqual(rows[0]["reverse_share"], 0.6)
        self.assertEqual(rows[0]["reverse_overlap_possible"], 0)

    def test_output_sorted_and_inputs_untouched(self):
        rows = [self._row(11, 8, 70), self._row(10, 7, 80)]
        original = [dict(r) for r in rows]
        out = classify_relation_matrix(rows, min_relation_px=64)
        self.assertEqual([r["source_id"] for r in out], [10, 11])
        self.assertEqual(rows, original)

    def test_empty_and_validation(self):
        self.assertEqual(classify_relation_matrix([]), [])
        with self.assertRaises(ValueError):
            classify_relation_matrix([], min_relation_px=0)

    def test_relation_classes_constant(self):
        self.assertEqual(
            RELATION_CLASSES,
            ("one_to_one", "one_to_many", "many_to_one",
             "many_to_many", "uncertain"))

    def test_classified_rows_are_json_serialisable(self):
        rows = classify_relation_matrix(
            [self._row(10, 7, 100)], min_relation_px=64)
        self.assertIsInstance(json.dumps(rows), str)


class EndToEndTests(unittest.TestCase):
    """Census rows feed classification without reshaping."""

    def test_split_target_classifies_one_to_many(self):
        source = _full((8, 8), 10)
        target = np.block([[_full((8, 4), 7), _full((8, 4), 8)]])
        land = np.ones((8, 8), dtype=bool)
        mask = np.ones((8, 8), dtype=bool)
        # Keep the sampled hits away from the band so both cells clear the
        # threshold confidently.
        band = np.zeros((8, 8), dtype=bool)
        result = census_component(
            source, target, land, mask, IDENTITY, band,
            min_relation_px=8)
        rows = classify_relation_matrix(result["rows"], min_relation_px=8)
        self.assertEqual(
            [(r["source_id"], r["target_id"], r["relation"])
             for r in rows],
            [(10, 7, "one_to_many"), (10, 8, "one_to_many")])


if __name__ == "__main__":
    unittest.main()
