"""Deterministic same-grid partition tests; no map files or canonical tables."""

import json
from pathlib import Path
import sys
import unittest

import numpy as np


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from partition_matching import (  # noqa: E402
    BACKGROUND_RGBA,
    ME_BOUNDARY_RGBA,
    VANILLA_BOUNDARY_RGBA,
    boundary_mask,
    draw_partition_overlay,
    partition_summary,
    ranked_overlaps,
)


class BoundaryMaskTests(unittest.TestCase):
    def test_water_and_negative_empty_pixels_are_never_boundaries(self):
        ids = np.array([[0, -1, 0], [-2, 0, -3]], dtype=np.int16)
        result = boundary_mask(ids)
        self.assertEqual(result.dtype, np.dtype(np.bool_))
        np.testing.assert_array_equal(result, np.zeros(ids.shape, dtype=bool))

    def test_only_positive_side_of_land_water_edge_is_marked(self):
        ids = np.zeros((5, 5), dtype=np.int32)
        ids[1:4, 1:4] = 7
        expected = np.array([
            [False, False, False, False, False],
            [False, True, True, True, False],
            [False, True, False, True, False],
            [False, True, True, True, False],
            [False, False, False, False, False],
        ])
        np.testing.assert_array_equal(boundary_mask(ids), expected)

    def test_frame_edges_and_both_sides_of_state_borders_are_marked(self):
        ids = np.full((5, 7), 11, dtype=np.uint16)
        ids[:, 4:] = 12
        expected = np.zeros(ids.shape, dtype=bool)
        expected[0, :] = True
        expected[-1, :] = True
        expected[:, 0] = True
        expected[:, -1] = True
        expected[:, 3:5] = True
        np.testing.assert_array_equal(boundary_mask(ids), expected)

    def test_diagonal_difference_is_not_a_boundary(self):
        ids = np.full((5, 5), 1, dtype=np.int32)
        ids[1, 1] = 0
        result = boundary_mask(ids)
        self.assertFalse(result[1, 1])
        self.assertTrue(result[1, 2])
        self.assertTrue(result[2, 1])
        self.assertFalse(result[2, 2])

    def test_single_row_column_and_pixel_are_frame_boundaries(self):
        for ids in (
            np.array([[4, 0, 5]], dtype=np.int32),
            np.array([[4], [0], [5]], dtype=np.int32),
            np.array([[4]], dtype=np.int32),
        ):
            with self.subTest(shape=ids.shape):
                np.testing.assert_array_equal(boundary_mask(ids), ids > 0)

    def test_empty_frames_preserve_shape(self):
        for shape in ((0, 0), (0, 3), (4, 0)):
            with self.subTest(shape=shape):
                result = boundary_mask(np.empty(shape, dtype=np.int32))
                self.assertEqual(result.shape, shape)
                self.assertEqual(result.dtype, np.dtype(np.bool_))


class RankedOverlapTests(unittest.TestCase):
    def test_one_vanilla_state_can_contain_many_me_descendants(self):
        me = np.array([[12, 12, 35, 35], [12, 12, 8, 8]], dtype=np.int32)
        vanilla = np.full(me.shape, 100, dtype=np.uint16)
        expected = [
            {
                "me_id": me_id,
                "sampled_pixels": pixels,
                "candidates": [{"vanilla_id": 100, "pixels": pixels, "share": 1.0}],
                "runner_share": 0.0,
                "margin": 1.0,
            }
            for me_id, pixels in ((8, 2), (12, 4), (35, 2))
        ]
        self.assertEqual(ranked_overlaps(me, vanilla), expected)

    def test_candidates_runner_and_margin_use_all_me_pixels(self):
        me = np.full((2, 5), 42, dtype=np.int32)
        vanilla = np.array([[8, 8, 3, 0, 0], [8, 8, 3, 9, -4]], dtype=np.int32)
        record, = ranked_overlaps(me, vanilla)
        self.assertEqual(record["sampled_pixels"], 10)
        self.assertEqual(record["candidates"], [
            {"vanilla_id": 8, "pixels": 4, "share": 0.4},
            {"vanilla_id": 3, "pixels": 2, "share": 0.2},
            {"vanilla_id": 9, "pixels": 1, "share": 0.1},
        ])
        self.assertAlmostEqual(record["runner_share"], 0.2)
        self.assertAlmostEqual(record["margin"], 0.2)
        self.assertAlmostEqual(sum(row["share"] for row in record["candidates"]), 0.7)

    def test_ties_use_vanilla_id_and_records_use_me_id_deterministically(self):
        me = np.array([[99, 2, 99, 2], [99, 2, 99, 2]], dtype=np.int32)
        vanilla = np.array([[8, 7, 3, 2], [3, 2, 8, 7]], dtype=np.int32)
        records = ranked_overlaps(me, vanilla)
        self.assertEqual([row["me_id"] for row in records], [2, 99])
        self.assertEqual([row["vanilla_id"] for row in records[0]["candidates"]], [2, 7])
        self.assertEqual([row["vanilla_id"] for row in records[1]["candidates"]], [3, 8])
        for record in records:
            self.assertEqual(record["sampled_pixels"], 4)
            self.assertEqual(record["runner_share"], 0.5)
            self.assertEqual(record["margin"], 0.0)
        # Traversal order and non-contiguous views cannot change the result.
        self.assertEqual(ranked_overlaps(me[:, ::-1], vanilla[:, ::-1]), records)
        self.assertEqual(ranked_overlaps(me.T, vanilla.T), records)
        self.assertEqual(json.dumps(ranked_overlaps(me, vanilla)), json.dumps(records))

    def test_uncovered_me_states_still_have_records_and_empty_ids_do_not(self):
        me = np.array([[5, 5, 0], [7, 0, -3]], dtype=np.int32)
        vanilla = np.array([[0, -1, 12], [-8, 13, 14]], dtype=np.int32)
        self.assertEqual(ranked_overlaps(me, vanilla), [
            {"me_id": 5, "sampled_pixels": 2, "candidates": [], "runner_share": 0.0, "margin": 0.0},
            {"me_id": 7, "sampled_pixels": 1, "candidates": [], "runner_share": 0.0, "margin": 0.0},
        ])
        self.assertEqual(ranked_overlaps(np.zeros((2, 3), dtype=np.int32), vanilla), [])

    def test_large_unsigned_ids_are_preserved_without_packing_or_float_casts(self):
        me_id, vanilla_id = 2**63 + 15, 2**64 - 1
        me = np.array([[me_id, me_id, 0]], dtype=np.uint64)
        vanilla = np.array([[vanilla_id, 1, 9]], dtype=np.uint64)
        record, = ranked_overlaps(me, vanilla)
        self.assertEqual(record["me_id"], me_id)
        self.assertIs(type(record["me_id"]), int)
        self.assertEqual(record["candidates"], [
            {"vanilla_id": 1, "pixels": 1, "share": 0.5},
            {"vanilla_id": vanilla_id, "pixels": 1, "share": 0.5},
        ])
        mixed, = ranked_overlaps(me[:, :2], np.array([[4, -1]], dtype=np.int16))
        self.assertEqual(mixed["candidates"], [{"vanilla_id": 4, "pixels": 1, "share": 0.5}])
        self.assertEqual(mixed["sampled_pixels"], 2)

    def test_integer_nested_lists_are_accepted_without_broadcasting(self):
        record, = ranked_overlaps([[1, 1]], [[2, 0]])
        self.assertEqual(record["sampled_pixels"], 2)
        self.assertEqual(record["candidates"], [{"vanilla_id": 2, "pixels": 1, "share": 0.5}])


class PartitionSummaryTests(unittest.TestCase):
    def assert_json_primitives(self, value):
        if type(value) is dict:
            for key, item in value.items():
                self.assertIs(type(key), str)
                self.assert_json_primitives(item)
        elif type(value) is list:
            for item in value:
                self.assert_json_primitives(item)
        else:
            self.assertIn(type(value), (int, float))

    def test_union_shared_and_one_sided_land_counts(self):
        me = np.array([[1, 1, 0, 0], [2, 0, -3, 2]], dtype=np.int32)
        vanilla = np.array([[9, 0, 9, 0], [-1, 9, 8, 9]], dtype=np.int16)
        result = partition_summary(me, vanilla)
        self.assertEqual(result, {
            "union_land_pixels": 7,
            "me_land_pixels": 4,
            "vanilla_land_pixels": 5,
            "shared_land_pixels": 2,
            "me_only_pixels": 2,
            "vanilla_only_pixels": 3,
            "overlaps": [
                {
                    "me_id": me_id,
                    "sampled_pixels": 2,
                    "candidates": [{"vanilla_id": 9, "pixels": 1, "share": 0.5}],
                    "runner_share": 0.0,
                    "margin": 0.5,
                }
                for me_id in (1, 2)
            ],
        })
        self.assert_json_primitives(result)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_empty_water_frames_have_zero_counts_and_no_records(self):
        for shape in ((2, 3), (0, 0), (0, 3), (4, 0)):
            with self.subTest(shape=shape):
                me = np.zeros(shape, dtype=np.int32)
                vanilla = np.full(shape, -7, dtype=np.int32)
                result = partition_summary(me, vanilla)
                self.assertEqual(result["overlaps"], [])
                self.assertTrue(all(value == 0 for key, value in result.items() if key != "overlaps"))
                self.assert_json_primitives(result)

    def test_diagnostics_do_not_mutate_either_input(self):
        me = np.array([[1, 1, 0], [-2, 4, 4]], dtype=np.int32)
        vanilla = np.array([[-3, 8, 8], [9, 7, 0]], dtype=np.int16)
        me_before, vanilla_before = me.copy(), vanilla.copy()
        me.setflags(write=False)
        vanilla.setflags(write=False)
        boundary_mask(me)
        ranked_overlaps(me, vanilla)
        partition_summary(me, vanilla)
        draw_partition_overlay(me, vanilla)
        np.testing.assert_array_equal(me, me_before)
        np.testing.assert_array_equal(vanilla, vanilla_before)


class PartitionOverlayTests(unittest.TestCase):
    def test_me_boundaries_are_continuous_and_interiors_are_neutral(self):
        me = np.zeros((11, 13), dtype=np.int32)
        me[2:9, 2:11] = 5
        vanilla = np.zeros_like(me)
        image = draw_partition_overlay(me, vanilla)
        self.assertEqual(image.shape, (11, 13, 4))
        self.assertEqual(image.dtype, np.dtype(np.uint8))
        self.assertTrue(np.all(image[:, :, 3] == 255))
        for y, x in ((0, 0), (5, 5), (1, 5), (9, 5)):
            with self.subTest(background=(y, x)):
                np.testing.assert_array_equal(image[y, x], BACKGROUND_RGBA)
        for y, x in ((2, 5), (5, 2), (8, 5), (5, 10)):
            with self.subTest(me_boundary=(y, x)):
                np.testing.assert_array_equal(image[y, x], ME_BOUNDARY_RGBA)
        self.assertEqual(BACKGROUND_RGBA[0], BACKGROUND_RGBA[1])
        self.assertEqual(BACKGROUND_RGBA[1], BACKGROUND_RGBA[2])
        self.assertGreater(ME_BOUNDARY_RGBA[1], ME_BOUNDARY_RGBA[0])
        self.assertGreater(ME_BOUNDARY_RGBA[1], ME_BOUNDARY_RGBA[2])

    def test_vanilla_boundaries_have_stable_diagonal_dashes_not_fills(self):
        me = np.zeros((10, 10), dtype=np.int32)
        vanilla = np.full_like(me, 99)
        image = draw_partition_overlay(me, vanilla)
        for y, x in ((0, 0), (0, 3), (0, 8), (3, 0), (8, 0)):
            with self.subTest(dash_on=(y, x)):
                np.testing.assert_array_equal(image[y, x], VANILLA_BOUNDARY_RGBA)
        for y, x in ((0, 4), (0, 7), (4, 0), (7, 0), (1, 1), (5, 5)):
            with self.subTest(dash_off_or_interior=(y, x)):
                np.testing.assert_array_equal(image[y, x], BACKGROUND_RGBA)
        self.assertGreater(VANILLA_BOUNDARY_RGBA[0], VANILLA_BOUNDARY_RGBA[1])
        self.assertGreater(VANILLA_BOUNDARY_RGBA[1], VANILLA_BOUNDARY_RGBA[2])

    def test_coincident_borders_keep_me_between_vanilla_dashes(self):
        me = np.ones((10, 10), dtype=np.int32)
        # Different numeric IDs do not matter to the boundary-only diagnostic.
        vanilla = np.full_like(me, 800)
        image = draw_partition_overlay(me, vanilla)
        np.testing.assert_array_equal(image[0, 1], VANILLA_BOUNDARY_RGBA)
        np.testing.assert_array_equal(image[0, 5], ME_BOUNDARY_RGBA)
        np.testing.assert_array_equal(image[5, 5], BACKGROUND_RGBA)
        np.testing.assert_array_equal(
            image,
            draw_partition_overlay(me * 73, vanilla * 2),
        )

    def test_empty_and_negative_water_images_are_valid_opaque_rgba(self):
        for shape in ((2, 3), (0, 0), (0, 3), (4, 0)):
            with self.subTest(shape=shape):
                me = np.zeros(shape, dtype=np.int32)
                vanilla = np.full(shape, -1, dtype=np.int32)
                image = draw_partition_overlay(me, vanilla)
                expected = np.empty((*shape, 4), dtype=np.uint8)
                expected[:] = BACKGROUND_RGBA
                np.testing.assert_array_equal(image, expected)


class PartitionValidationTests(unittest.TestCase):
    PAIR_FUNCTIONS = (ranked_overlaps, partition_summary, draw_partition_overlay)

    def test_rejects_non_2d_arrays_on_either_side(self):
        valid = np.ones((2, 2), dtype=np.int32)
        for bad in (
            np.array(1, dtype=np.int32),
            np.ones(2, dtype=np.int32),
            np.ones((2, 2, 1), dtype=np.int32),
        ):
            with self.subTest(boundary_shape=bad.shape):
                with self.assertRaisesRegex(ValueError, "2-D"):
                    boundary_mask(bad)
            for function in self.PAIR_FUNCTIONS:
                for left, right in ((bad, valid), (valid, bad)):
                    with self.subTest(function=function.__name__, left=left.shape, right=right.shape):
                        with self.assertRaisesRegex(ValueError, "2-D"):
                            function(left, right)

    def test_rejects_float_bool_complex_string_and_object_arrays(self):
        valid = np.ones((2, 2), dtype=np.int32)
        for dtype in (np.float64, np.bool_, np.complex128, "U2", object):
            bad = np.ones((2, 2), dtype=dtype)
            with self.subTest(boundary_dtype=bad.dtype):
                with self.assertRaisesRegex(TypeError, "integer dtype"):
                    boundary_mask(bad)
            for function in self.PAIR_FUNCTIONS:
                for left, right in ((bad, valid), (valid, bad)):
                    with self.subTest(function=function.__name__, left=left.dtype, right=right.dtype):
                        with self.assertRaisesRegex(TypeError, "integer dtype"):
                            function(left, right)
        # Fractional and non-finite values must not be truncated to integer IDs.
        for value in (1.5, float("nan"), float("inf")):
            for function in self.PAIR_FUNCTIONS:
                with self.subTest(function=function.__name__, value=value):
                    with self.assertRaises(TypeError):
                        function(valid, np.full((2, 2), value))

    def test_rejects_shape_mismatch_including_broadcastable_shapes(self):
        valid = np.ones((2, 3), dtype=np.int32)
        for shape in ((3, 2), (2, 1), (1, 3), (1, 1), (0, 3)):
            bad = np.ones(shape, dtype=np.int32)
            for function in self.PAIR_FUNCTIONS:
                with self.subTest(function=function.__name__, shape=shape):
                    with self.assertRaisesRegex(ValueError, "same shape"):
                        function(valid, bad)


if __name__ == "__main__":
    unittest.main()
