"""Focused synthetic tests for tools/state_relation_registration.py.

Covers only the pure/synthetic contracts: non-wrapping boundary classes
(edge pixels never wrap to the opposite edge, owned-vs-owned-only
country borders), the positive-det/singular-value guard on the composed
local-after-global transform, same-owner component selection and the
registered/global_fallback/unmatched_component/excluded_inset status
ladder, owner resolution at the 1936.1.1 cutoff, and deterministic
JSON-serialisable output. One tmp-dir map fixture exercises load_side
end to end; no real game data, canonical tables, VPs or review queues.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import continent_registration as cr  # noqa: E402
import state_relation_registration as reg  # noqa: E402

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


# ---------------------------------------------------------------- helpers

def _rect(arr, x0, x1, y0, y1, value):
    arr[y0:y1 + 1, x0:x1 + 1] = value


def _side(owner_raster, sea_mask=None, lake_mask=None, owner_tags=(),
          grid_cell=1):
    """Assemble a load_side-shaped dict from synthetic rasters.

    owner_raster codes: -1 non-land, 0 unowned, >=1 -> owner_tags[i-1].
    """
    own = np.asarray(owner_raster, dtype=np.int32)
    land = own >= 0
    sea = np.zeros(own.shape, dtype=bool) if sea_mask is None \
        else np.asarray(sea_mask, dtype=bool)
    lake = np.zeros(own.shape, dtype=bool) if lake_mask is None \
        else np.asarray(lake_mask, dtype=bool)
    masks = reg._boundary_masks(land, sea, lake, own)
    points = reg._boundary_points(masks, grid_cell=grid_cell, margin=0)
    comps = reg._connected_components(own, list(owner_tags), points)
    return {
        "map_dir": "<synthetic>",
        "cache": {},
        "width": int(own.shape[1]),
        "height": int(own.shape[0]),
        "land_mask": land,
        "sea_mask": sea,
        "lake_mask": lake,
        "owner_raster": own,
        "owner_tags": list(owner_tags),
        "boundary_masks": masks,
        "boundary_points": points,
        "components": comps,
    }


def _global(affine=None):
    A = IDENTITY.copy() if affine is None else np.asarray(
        affine, dtype=np.float64)
    return {
        "kind": "state_relation_registration",
        "ok": True,
        "affine_me_to_vanilla": A.tolist(),
        "affine_det": float(np.linalg.det(A[:, :2])),
        "metrics": {},
        "diagnostics": {},
    }


def _scenario_sides():
    """ME/vanilla sides exercising every fit_components status.

    Coordinates are provinces.bmp x/y on a 120x30 canvas; the global
    affine is the identity. ME owner codes: FRA=1 GER=2 ITA=3;
    vanilla: FRA=1 GER=2.
      ME FRA (60-79)   -> vanilla FRA (60-79): registered
      ME GER (5-14)    -> vanilla GER (5-14):  registered
      ME GER (22-24)   -> 3x3, under-powered ICP: global_fallback
      ME GER (90-99)   -> >64px from GER, lands on FRA: excluded_inset
      ME ITA (110-119) -> no ITA on vanilla:   unmatched_component
    """
    me_own = np.full((30, 120), -1, dtype=np.int32)
    _rect(me_own, 5, 14, 10, 19, 2)     # GER main
    _rect(me_own, 22, 24, 10, 12, 2)    # GER tiny
    _rect(me_own, 60, 79, 10, 19, 1)    # FRA
    _rect(me_own, 90, 99, 10, 19, 2)    # GER far/inset
    _rect(me_own, 110, 119, 10, 19, 3)  # ITA
    me = _side(me_own, sea_mask=me_own < 0,
               owner_tags=["FRA", "GER", "ITA"])

    v_own = np.full((30, 120), -1, dtype=np.int32)
    _rect(v_own, 5, 14, 10, 19, 2)      # GER
    _rect(v_own, 60, 79, 10, 19, 1)     # FRA west
    _rect(v_own, 90, 109, 10, 19, 1)    # FRA east (inset host)
    van = _side(v_own, sea_mask=v_own < 0, owner_tags=["FRA", "GER"])
    return me, van


def _write_fixture(root):
    """Minimal playable map: 8x6 grid, 5 provinces, 2 state files.

    cols0-1 pid1 land (state1 GER) | cols2-3 pid2 land (state2, dated
    ENG->FRA 1934, ITA 1940) | cols4-5 pid3 land with NO state file
    (unowned) | cols6-7 rows0-2 pid4 sea | cols6-7 rows3-5 pid5 lake.
    """
    root = Path(root)
    mdir = root / "map"
    states = root / "history" / "states"
    states.mkdir(parents=True)
    mdir.mkdir()

    palette = {1: (10, 0, 0), 2: (20, 0, 0), 3: (30, 0, 0),
               4: (40, 0, 0), 5: (50, 0, 0)}
    types = {1: "land", 2: "land", 3: "land", 4: "sea", 5: "lake"}
    rows = ["0;0;0;0;x;x;x;0"]
    for pid in sorted(palette):
        r, g, b = palette[pid]
        rows.append("%d;%d;%d;%d;%s;x;x;1" % (pid, r, g, b, types[pid]))
    (mdir / "definition.csv").write_text("\n".join(rows) + "\n",
                                         encoding="utf-8")

    img = Image.new("RGB", (8, 6), (0, 0, 0))
    px = img.load()
    for y in range(6):
        for x in range(8):
            if x < 2:
                pid = 1
            elif x < 4:
                pid = 2
            elif x < 6:
                pid = 3
            else:
                pid = 4 if y < 3 else 5
            px[x, y] = palette[pid]
    img.save(mdir / "provinces.bmp")

    (states / "1 - Alpha.txt").write_text(
        'state={\n\tid=1\n\tprovinces={ 1 }\n'
        '\thistory={\n\t\towner = GER\n\t}\n}\n', encoding="utf-8")
    (states / "2 - Beta.txt").write_text(
        'state={\n\tid=2\n\tprovinces={ 2 }\n'
        '\thistory={\n\t\towner = ENG\n'
        '\t\t1934.6.1 = {\n\t\t\towner = FRA\n\t\t}\n'
        '\t\t1940.1.1 = {\n\t\t\towner = ITA\n\t\t}\n'
        '\t}\n}\n', encoding="utf-8")
    return root


# ------------------------------------------------------- boundary masks

class BoundaryMaskTests(unittest.TestCase):
    """Non-wrapping adjacency and the country-class ownership rule."""

    def test_country_requires_owned_on_both_sides(self):
        own = np.zeros((4, 10), dtype=np.int32)
        own[:, 0:4] = 1      # GER
        own[:, 6:10] = 2     # FRA, gap of unowned at cols 4-5
        mask = reg._country_mask(own)
        self.assertFalse(mask.any())

    def test_country_border_marks_both_owned_sides(self):
        own = np.zeros((4, 10), dtype=np.int32)
        own[:, 0:5] = 1
        own[:, 5:10] = 2
        mask = reg._country_mask(own)
        self.assertTrue(mask[:, 4].all())
        self.assertTrue(mask[:, 5].all())
        self.assertFalse(mask[:, 3].any())
        self.assertFalse(mask[:, 6].any())

    def test_no_wraparound_country(self):
        """Owners at x=0 and x=W-1 are never neighbours (no np.roll)."""
        own = np.zeros((4, 10), dtype=np.int32)
        own[:, 0] = 1
        own[:, -1] = 2
        self.assertFalse(reg._country_mask(own).any())

    def test_no_wraparound_edge_touch(self):
        """Land at x=0 must not see sea at x=W-1 (or vice versa)."""
        land = np.zeros((4, 10), dtype=bool)
        land[:, 0] = True
        sea = np.zeros((4, 10), dtype=bool)
        sea[:, -1] = True
        masks = reg._boundary_masks(land, sea,
                                    np.zeros_like(land), land * 0)
        self.assertFalse(masks["coast"].any())

    def test_edge_touch_is_symmetric_neighbourhood(self):
        sea = np.zeros((4, 10), dtype=bool)
        sea[:, 5] = True
        touch = reg._edge_touch(sea)
        self.assertTrue(touch[:, 4].all())
        self.assertTrue(touch[:, 6].all())
        self.assertFalse(touch[:, 3].any())
        self.assertFalse(touch[:, 0].any())   # never wraps to col 9

    def test_lake_class_is_land_lake_only(self):
        land = np.zeros((4, 10), dtype=bool)
        land[:, 0:5] = True
        lake = np.zeros((4, 10), dtype=bool)
        lake[:, 5:10] = True
        masks = reg._boundary_masks(land, np.zeros_like(land), lake,
                                    np.zeros((4, 10), dtype=np.int32))
        self.assertTrue(masks["lake"][:, 4].all())   # land side
        self.assertTrue(masks["lake"][:, 5].all())   # lake side
        self.assertFalse(masks["lake"][:, 9].any())  # deep lake interior
        self.assertFalse(masks["coast"].any())
        self.assertFalse(masks["country"].any())


# ----------------------------------------------------- raster derivations

class RasterDerivationTests(unittest.TestCase):

    def test_state_raster_and_owner_codes(self):
        prov = np.array([[7, 9, 0], [7, -1, 9]], dtype=np.int32)
        states = {
            2: {"provinces": [7], "owner": "GER"},
            5: {"provinces": [9], "owner": ""},
        }
        sr = reg._state_raster(prov, states)
        self.assertEqual(sr.tolist(), [[2, 5, -1], [2, -1, 5]])
        land = np.array([[True, True, False],
                         [True, False, True]])
        own, tags = reg._owner_raster(sr, states, land)
        self.assertEqual(tags, ["GER"])
        self.assertEqual(own.tolist(), [[1, 0, -1], [1, -1, 0]])

    def test_duplicate_province_membership_fails(self):
        prov = np.array([[7]], dtype=np.int32)
        states = {1: {"provinces": [7], "owner": "A"},
                  2: {"provinces": [7], "owner": "B"}}
        with self.assertRaises(ValueError):
            reg._state_raster(prov, states)


# --------------------------------------------------------------- load_side

class LoadSideTests(unittest.TestCase):
    """End-to-end tiny fixture: parses definition/bmp/states + owners."""

    def test_load_side_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            side = reg.load_side(_write_fixture(tmp))
        self.assertEqual(side["width"], 8)
        self.assertEqual(side["height"], 6)
        self.assertEqual(side["owner_tags"], ["FRA", "GER"])
        self.assertEqual(side["states"][1]["owner"], "GER")
        # dated 1934 block applies, 1940 does not -> FRA at 1936.1.1
        self.assertEqual(side["states"][2]["owner"], "FRA")

        prov = side["province_raster"]
        self.assertEqual(prov[0, 0], 1)
        self.assertEqual(prov[0, 7], 4)      # sea north-east
        self.assertEqual(prov[5, 7], 5)      # lake south-east
        sr = side["state_raster"]
        self.assertEqual(sr[0, 1], 1)
        self.assertEqual(sr[0, 3], 2)
        self.assertEqual(sr[0, 5], -1)       # pid 3: land, no state
        own = side["owner_raster"]
        self.assertTrue((own[:, 0:2] == 2).all())   # GER code 2
        self.assertTrue((own[:, 2:4] == 1).all())   # FRA code 1
        self.assertTrue((own[:, 4:6] == 0).all())   # unowned land
        self.assertTrue((own[:, 6:8] == -1).all())  # sea+lake

        masks = side["boundary_masks"]
        # GER|FRA owned-owned border at cols 1/2
        self.assertTrue(masks["country"][:, 1].all())
        self.assertTrue(masks["country"][:, 2].all())
        self.assertFalse(masks["country"][:, 3].any())  # FRA|unowned
        self.assertFalse(masks["country"][:, 4].any())  # unowned side
        # unowned-land|sea coast at cols 5/6 rows 0-2
        self.assertTrue(masks["coast"][0:3, 5].all())
        self.assertTrue(masks["coast"][0:3, 6].all())
        # unowned-land|lake shore at cols 5/6 rows 3-5
        self.assertTrue(masks["lake"][3:6, 5].all())
        self.assertTrue(masks["lake"][3:6, 6].all())

        comps = side["components"]
        self.assertEqual(len(comps), 2)      # unowned forms none
        self.assertEqual(sorted(c["owner"] for c in comps),
                         ["FRA", "GER"])
        for comp in comps:
            self.assertEqual(comp["area_px"], 12)
            self.assertEqual(comp["mask"].shape, (6, 2))
            self.assertEqual(set(comp["boundary_points"]),
                             set(reg.BOUNDARY_CLASSES))


# --------------------------------------------------------------- fit_global

class FitGlobalTests(unittest.TestCase):
    """Stage-2 boundary ICP over synthetic clouds (custom init = no I/O)."""

    def _cloud(self, n=6, step=25.0):
        yy, xx = np.mgrid[0:n, 0:n]
        return np.stack([xx.ravel() * step, yy.ravel() * step],
                        axis=1).astype(np.float64)

    def test_recovers_pure_translation(self):
        v_pts = self._cloud()
        me_pts = v_pts - np.array([5.0, 3.0])
        v_side = {"cache": {}, "boundary_points":
                  {"country": v_pts, "coast": np.zeros((0, 2)),
                   "lake": np.zeros((0, 2))}}
        me_side = {"cache": {}, "boundary_points":
                   {"country": me_pts, "coast": np.zeros((0, 2)),
                    "lake": np.zeros((0, 2))}}
        res = reg.fit_global(me_side, v_side, initial=IDENTITY.copy())
        self.assertTrue(res["ok"])
        self.assertEqual(res["metrics"]["refinement_status"], "refined")
        np.testing.assert_allclose(
            res["affine_me_to_vanilla"],
            [[1.0, 0.0, 5.0], [0.0, 1.0, 3.0]], atol=0.5)
        self.assertGreater(res["affine_det"], 0.0)
        json.dumps(res)      # whole result must be JSON-serialisable

    def test_underpowered_refinement_keeps_warm_start(self):
        v_pts = self._cloud()
        me_pts = v_pts + np.array([500.0, 0.0])   # beyond the NN cap
        v_side = {"cache": {}, "boundary_points":
                  {"country": v_pts, "coast": np.zeros((0, 2)),
                   "lake": np.zeros((0, 2))}}
        me_side = {"cache": {}, "boundary_points":
                   {"country": me_pts, "coast": np.zeros((0, 2)),
                    "lake": np.zeros((0, 2))}}
        res = reg.fit_global(me_side, v_side, initial=IDENTITY.copy())
        self.assertTrue(res["ok"])
        self.assertEqual(res["metrics"]["refinement_status"],
                         "coast_fallback")
        np.testing.assert_allclose(res["affine_me_to_vanilla"],
                                   IDENTITY)
        self.assertTrue(res["diagnostics"]["det_abort"])

    def test_vp_init_is_unreachable(self):
        for bad in ("vp", "auto", "both"):
            with self.assertRaises(ValueError):
                reg.fit_global({"cache": {}, "boundary_points": {}},
                               {"cache": {}, "boundary_points": {}},
                               initial=bad)

    def test_bad_custom_affine_rejected(self):
        side = {"cache": {}, "boundary_points": {}}
        with self.assertRaises(ValueError):
            reg.fit_global(side, side,
                           initial=[[-1.0, 0.0, 0.0],
                                    [0.0, 1.0, 0.0]])


# -------------------------------------------------- local transform guard

class LocalGuardTests(unittest.TestCase):
    """Positive-det + 0.80..1.25 singular-value bounds vs the global."""

    def test_pure_translation_is_in_bounds(self):
        local = IDENTITY.copy()
        local[0, 2] = 8.0
        ok, why, sv, det = reg._local_transform_ok(IDENTITY, local)
        self.assertTrue(ok)
        self.assertIsNone(why)
        self.assertEqual(det, 1.0)
        np.testing.assert_allclose(sv, [1.0, 1.0])

    def test_scale_above_bound_rejected(self):
        local = IDENTITY.copy() * 1.0
        local[:, :2] *= 1.30
        ok, why, _, _ = reg._local_transform_ok(IDENTITY, local)
        self.assertFalse(ok)
        self.assertIn("scale", why)

    def test_scale_below_bound_rejected(self):
        local = IDENTITY.copy()
        local[:, :2] *= 0.70
        ok, why, _, _ = reg._local_transform_ok(IDENTITY, local)
        self.assertFalse(ok)
        self.assertIn("scale", why)

    def test_folded_transform_rejected(self):
        local = np.array([[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        ok, why, _, det = reg._local_transform_ok(IDENTITY, local)
        self.assertFalse(ok)
        self.assertLess(det, 0.0)
        self.assertIn("folds", why)

    def test_bounds_are_relative_to_global(self):
        # G at scale 2, L at scale 2.2 -> relative sv 1.1, accepted
        g = IDENTITY.copy()
        g[:, :2] *= 2.0
        local = IDENTITY.copy()
        local[:, :2] *= 2.2
        ok, _, sv, _ = reg._local_transform_ok(g, local)
        self.assertTrue(ok)
        np.testing.assert_allclose(sv, [1.1, 1.1], atol=1e-9)


# ------------------------------------------------------------ fit_components

class FitComponentsTests(unittest.TestCase):

    def test_status_ladder_and_targets(self):
        me, van = _scenario_sides()
        out = reg.fit_components(me, van, _global(), apply_natural=False)
        self.assertEqual([c["status"] for c in out],
                         ["registered", "registered", "global_fallback",
                          "excluded_inset", "unmatched_component"])
        by_status = {c["status"]: c for c in out}

        reg_entries = [c for c in out if c["status"] == "registered"]
        for entry in reg_entries:
            self.assertIsNotNone(entry["target_component_id"])
            self.assertIsNone(entry["metrics"]["fallback_reason"])
            np.testing.assert_allclose(
                entry["registration"]["affine_me_to_vanilla"],
                IDENTITY, atol=1e-6)
            # map_points-compatible: finite output through the API
            mapped = cr.map_points(entry["registration"],
                                   np.array([[5.0, 5.0]]))
            self.assertTrue(np.isfinite(mapped).all())

        fb = by_status["global_fallback"]
        self.assertIsNotNone(fb["target_component_id"])
        self.assertIn("under-powered", fb["metrics"]["fallback_reason"])
        np.testing.assert_allclose(
            fb["registration"]["affine_me_to_vanilla"], IDENTITY)

        inset = by_status["excluded_inset"]
        self.assertIsNone(inset["target_component_id"])
        self.assertIn("foreign/unowned/non-land",
                      inset["metrics"]["fallback_reason"])
        self.assertEqual(
            inset["metrics"]["footprint"]["same_owner_land_share"], 0.0)

        unmatched = by_status["unmatched_component"]
        self.assertIsNone(unmatched["target_component_id"])
        self.assertIn("no same-owner component",
                      unmatched["metrics"]["fallback_reason"])

        for entry in out:
            json.dumps(reg.serialisable_component(entry))

    def test_masks_are_in_memory_only(self):
        me, van = _scenario_sides()
        out = reg.fit_components(me, van, _global(), apply_natural=False)
        for entry in out:
            self.assertIsInstance(entry["mask"], np.ndarray)
            self.assertEqual(entry["mask"].dtype, np.bool_)
            self.assertNotIn("mask", reg.serialisable_component(entry))


class SelectTargetTests(unittest.TestCase):

    def test_nearest_same_owner_and_cap(self):
        own = np.full((10, 40), -1, dtype=np.int32)
        _rect(own, 0, 4, 0, 9, 1)
        _rect(own, 20, 29, 0, 9, 1)
        side = _side(own, sea_mask=own < 0, owner_tags=["GER"])
        trees = {}
        # inside the far component -> distance 0
        cand, d = reg._select_target(np.array([24.0, 4.0]),
                                     side["components"], trees)
        self.assertIsNotNone(cand)
        self.assertEqual(d, 0.0)
        self.assertEqual(cand["bbox_xyxy"], [20, 0, 29, 9])
        # nearest is the close component
        cand, d = reg._select_target(np.array([10.0, 4.0]),
                                     side["components"], trees)
        self.assertIsNotNone(cand)
        self.assertGreater(d, 0.0)
        self.assertEqual(cand["bbox_xyxy"], [0, 0, 4, 9])
        # beyond the cap -> no target
        cand, _ = reg._select_target(np.array([4.0, -1000.0]),
                                     side["components"], trees)
        self.assertIsNone(cand)


# ------------------------------------------------------------- serialise

class SerialisationTests(unittest.TestCase):

    def test_jsonable_covers_numpy_types(self):
        payload = {
            "i": np.int64(7), "f": np.float64(1.5),
            "b": np.bool_(True), "a": np.array([[1, 2], [3, 4]]),
            "n": {"inner": np.int32(3)},
        }
        out = json.loads(json.dumps(reg._jsonable(payload)))
        self.assertEqual(out, {"i": 7, "f": 1.5, "b": True,
                               "a": [[1, 2], [3, 4]], "n": {"inner": 3}})

    def test_boundary_icp_is_deterministic(self):
        yy, xx = np.mgrid[0:5, 0:5]
        v_pts = np.stack([xx.ravel() * 20.0, yy.ravel() * 20.0],
                         axis=1).astype(np.float64)
        me_pts = v_pts - np.array([6.0, 4.0])
        src = {"country": me_pts, "coast": np.zeros((0, 2)),
               "lake": np.zeros((0, 2))}
        dst = {"country": v_pts, "coast": np.zeros((0, 2)),
               "lake": np.zeros((0, 2))}
        r1 = reg._boundary_icp(src, dst, IDENTITY)
        r2 = reg._boundary_icp(src, dst, IDENTITY)
        self.assertEqual(json.dumps(reg._jsonable(r1), sort_keys=True),
                         json.dumps(reg._jsonable(r2), sort_keys=True))
        self.assertTrue(r1["converged"])
        self.assertGreater(r1["det"], 0.0)
        # refit should recover the +6/+4 translation
        np.testing.assert_allclose(
            r1["affine"], [[1.0, 0.0, 6.0], [0.0, 1.0, 4.0]], atol=0.5)

    def test_serialisable_component_roundtrip(self):
        me, van = _scenario_sides()
        out = reg.fit_components(me, van, _global(), apply_natural=False)
        for entry in out:
            row = reg.serialisable_component(entry)
            parsed = json.loads(json.dumps(row, sort_keys=True))
            self.assertIn("registration", parsed)
            self.assertIn("mask_shape", parsed)
            self.assertNotIn("mask", parsed)


if __name__ == "__main__":
    unittest.main()
