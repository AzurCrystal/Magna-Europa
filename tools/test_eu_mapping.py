#!/usr/bin/env python
"""Unit and invariant test suite for the definitive European mapping toolchain:
  - tools/eu_map_registration.py
  - tools/build_eu_mapping.py
  - tools/translate_vanilla_states.py

Run with:
  python -m unittest discover -s tools -p test_eu_mapping.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
import audit_eu_script_refs as aesr  # noqa: E402
import translate_vanilla_states as tvs  # noqa: E402

try:
    import numpy as np
    import eu_map_registration as emr
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class TestTranslatorSynthetic(unittest.TestCase):
    """Test territory union cancellation, city primary, province lookup, and Clausewitz parsing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)

        # Synthetic 2 vanilla states (42, 51) + 3 ME states:
        #   ME 101: 100% inside v42 (interior)
        #   ME 102: 55% inside v42, 45% inside v51 (internal straddler!)
        #   ME 103: 70% inside v51, 30% inside v99 (outer straddler)
        #   ME 104: 65% inside v99, 35% inside v51 (excluded outer straddler for {42, 51})
        self._write(d / "state_partition_v2m.tsv", [
            "vanilla_id", "vanilla_name", "vanilla_owner", "me_count",
            "primary_me_id", "primary_me_name", "me_members", "interior_members",
            "straddler_members", "v_native_px", "v_covered_px", "reverse_coverage",
            "block_id", "has_review_member",
        ], [
            dict(vanilla_id=42, vanilla_name="Moselland", vanilla_owner="GER", me_count=2,
                 primary_me_id=101, primary_me_name="Koblenz", me_members="101|102",
                 interior_members="101", straddler_members="102",
                 v_native_px=100, v_covered_px=98, reverse_coverage="0.980000", block_id=1, has_review_member=1),
            dict(vanilla_id=51, vanilla_name="Rhineland", vanilla_owner="GER", me_count=1,
                 primary_me_id=103, primary_me_name="Cologne", me_members="103",
                 interior_members="", straddler_members="103",
                 v_native_px=100, v_covered_px=95, reverse_coverage="0.950000", block_id=1, has_review_member=0),
            dict(vanilla_id=99, vanilla_name="Hessen", vanilla_owner="GER", me_count=1,
                 primary_me_id=104, primary_me_name="Wiesbaden", me_members="104",
                 interior_members="", straddler_members="104",
                 v_native_px=100, v_covered_px=96, reverse_coverage="0.960000", block_id=1, has_review_member=0),
        ])
        self._write(d / "state_partition_m2v.tsv", [
            "me_id", "me_name", "me_owner", "norm_owner", "status", "v_id", "v_name",
            "same_owner_share", "confidence_tier", "needs_review", "review_reason",
        ], [
            dict(me_id=101, me_name="Koblenz", me_owner="GER", norm_owner="GER", status="mapped",
                 v_id=42, v_name="Moselland", same_owner_share="1.000000", confidence_tier="high_interior", needs_review=0, review_reason=""),
            dict(me_id=102, me_name="Bonn", me_owner="GER", norm_owner="GER", status="mapped",
                 v_id=42, v_name="Moselland", same_owner_share="0.550000", confidence_tier="low_straddler", needs_review=1, review_reason="tight_straddler"),
            dict(me_id=103, me_name="Cologne", me_owner="GER", norm_owner="GER", status="mapped",
                 v_id=51, v_name="Rhineland", same_owner_share="0.700000", confidence_tier="high_majority", needs_review=0, review_reason=""),
            dict(me_id=104, me_name="Wiesbaden", me_owner="GER", norm_owner="GER", status="mapped",
                 v_id=99, v_name="Hessen", same_owner_share="0.650000", confidence_tier="high_majority", needs_review=0, review_reason=""),
        ])
        self._write(d / "state_primary_city.tsv", [
            "vanilla_id", "vanilla_name", "vanilla_owner", "primary_me_id", "primary_me_name",
            "primary_basis", "vp_matched_name", "vp_dist_px", "in_partition_members", "me_member_count",
        ], [
            dict(vanilla_id=42, vanilla_name="Moselland", vanilla_owner="GER", primary_me_id=101, primary_me_name="Koblenz",
                 primary_basis="vp_spatial_match", vp_matched_name="Koblenz", vp_dist_px="1.20", in_partition_members=1, me_member_count=2),
            dict(vanilla_id=51, vanilla_name="Rhineland", vanilla_owner="GER", primary_me_id=103, primary_me_name="Cologne",
                 primary_basis="vp_spatial_match", vp_matched_name="Cologne", vp_dist_px="0.80", in_partition_members=1, me_member_count=1),
            dict(vanilla_id=99, vanilla_name="Hessen", vanilla_owner="GER", primary_me_id=104, primary_me_name="Wiesbaden",
                 primary_basis="highest_vp_or_manpower_member", vp_matched_name="", vp_dist_px="", in_partition_members=1, me_member_count=1),
        ])
        self._write(d / "state_overlap.tsv", [
            "vanilla_id", "me_id", "norm_owner_match", "me_px", "me_total_px", "me_share",
            "me_same_owner_share", "edge_role", "is_partition_parent",
        ], [
            dict(vanilla_id=42, me_id=101, norm_owner_match=1, me_px=100, me_total_px=100, me_share="1.0", me_same_owner_share="1.0", edge_role="interior", is_partition_parent=1),
            dict(vanilla_id=42, me_id=102, norm_owner_match=1, me_px=55, me_total_px=100, me_share="0.55", me_same_owner_share="0.55", edge_role="majority", is_partition_parent=1),
            dict(vanilla_id=51, me_id=102, norm_owner_match=1, me_px=45, me_total_px=100, me_share="0.45", me_same_owner_share="0.45", edge_role="straddler", is_partition_parent=0),
            dict(vanilla_id=51, me_id=103, norm_owner_match=1, me_px=70, me_total_px=100, me_share="0.70", me_same_owner_share="0.70", edge_role="majority", is_partition_parent=1),
            dict(vanilla_id=99, me_id=103, norm_owner_match=1, me_px=30, me_total_px=100, me_share="0.30", me_same_owner_share="0.30", edge_role="straddler", is_partition_parent=0),
            dict(vanilla_id=51, me_id=104, norm_owner_match=1, me_px=35, me_total_px=100, me_share="0.35", me_same_owner_share="0.35", edge_role="straddler", is_partition_parent=0),
            dict(vanilla_id=99, me_id=104, norm_owner_match=1, me_px=65, me_total_px=100, me_share="0.65", me_same_owner_share="0.65", edge_role="majority", is_partition_parent=1),
        ])
        self._write(d / "prov_m2v.tsv", [
            "me_prov", "me_state", "me_state_name", "me_owner", "me_px", "v_state", "v_state_name",
            "v_prov", "purity_class", "same_owner_share",
        ], [
            dict(me_prov=5001, me_state=103, me_state_name="Cologne", me_owner="GER", me_px=40, v_state=51, v_state_name="Rhineland", v_prov=587, purity_class="pure_95", same_owner_share="1.0"),
            dict(me_prov=5002, me_state=103, me_state_name="Cologne", me_owner="GER", me_px=30, v_state=51, v_state_name="Rhineland", v_prov=587, purity_class="clean_80", same_owner_share="0.9"),
        ])
        self.idx = tvs.EUMappingIndex(d)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path, cols, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    def test_union_cancels_internal_straddler(self):
        tr = self.idx.translate_territory_union([42, 51, 999])
        self.assertEqual(tr["vanilla_on_map"], [42, 51])
        self.assertEqual(tr["vanilla_off_map"], [999])
        self.assertEqual(tr["me_ids"], [101, 102, 103])
        # ME 102 straddled 42 (55%) & 51 (45%) -> union_share = 1.0 -> auto-cancelled!
        self.assertEqual(tr["internal_straddlers_cancelled"], 1)
        # Only ME 103 (70% in {42,51}) is an included outer straddler
        self.assertEqual([x["me_id"] for x in tr["included_outer_straddlers"]], [103])
        # ME 104 (35% in {42,51}) is flagged as excluded outer straddler advisory
        self.assertEqual([x["me_id"] for x in tr["excluded_outer_straddlers"]], [104])

    def test_city_primary_single_target(self):
        tr = self.idx.translate_city_primary([42, 51, 888])
        self.assertEqual(tr["me_ids"], [101, 103])
        self.assertEqual(tr["vanilla_off_map"], [888])

    def test_province_lookup(self):
        tr = self.idx.translate_provinces([587, 99999])
        self.assertEqual(tr["results"][587]["primary_me_prov"], 5001)
        self.assertEqual(tr["results"][587]["me_provs"], [5001, 5002])
        self.assertEqual(tr["vanilla_provs_offmap_or_sea"], [99999])

    def test_clausewitz_context_inference_and_diff(self):
        old_script = """
        focus = {
            id = GER_test_focus
            completion_reward = {
                transfer_state = 42
            }
        }
        """
        new_script = """
        focus = {
            id = GER_test_focus
            completion_reward = {
                transfer_state = 42
                transfer_state = 51
                51 = {
                    add_extra_state_shared_building_slots = 2
                    add_building_construction = { type = arms_factory level = 2 province = 587 }
                }
            }
        }
        """
        findings = tvs.analyze_clausewitz_text(new_script, self.idx)
        kinds = {f["kind"]: f for f in findings}
        # Two sibling transfer_state lines grouped into a territory_union
        self.assertIn("scalar_group:territory_union", kinds)
        self.assertEqual(kinds["scalar_group:territory_union"]["translation"]["me_ids"], [101, 102, 103])
        # `51 = { add_building_construction ... }` inferred as city (1->1)
        self.assertIn("state_scope:city", kinds)
        self.assertEqual(kinds["state_scope:city"]["translation"]["me_ids"], [103])
        # Diff mode isolates only the newly added lines!
        changed = tvs._changed_lines_in_new(old_script, new_script)
        diff_findings = tvs.analyze_clausewitz_text(new_script, self.idx, only_lines=changed)
        self.assertTrue(any(f["kind"] == "state_scope:city" for f in diff_findings))


@unittest.skipUnless(HAS_NUMPY, "numpy/scipy required for registration math tests")
class TestRegistrationMath(unittest.TestCase):
    """Test SmoothResidualField interpolation and analytic Jacobian verification."""

    def test_identity_field_jacobian_one(self):
        dx = np.zeros((5, 6), dtype=np.float64)
        dy = np.zeros((5, 6), dtype=np.float64)
        fld = emr.SmoothResidualField(0, 0, 100, 80, 20, dx, dy, {})
        jac = fld.jacobian_det_grid()
        np.testing.assert_allclose(jac, 1.0)
        ex, ey = fld.eval_xy(np.array([15.0, 50.0, 200.0]), np.array([15.0, 40.0, 200.0]))
        np.testing.assert_allclose(ex, 0.0)
        np.testing.assert_allclose(ey, 0.0)

    def test_linear_stretch_field_exact_eval_and_jacobian(self):
        # D(x, y) = (0.1 * x, -0.05 * y) -> det(I + grad D) = 1.1 * 0.95 = 1.045
        step = 20
        xs = np.arange(6) * step
        ys = np.arange(5) * step
        gx, gy = np.meshgrid(xs, ys)
        fld = emr.SmoothResidualField(0, 0, 100, 80, step, 0.1 * gx, -0.05 * gy, {})
        np.testing.assert_allclose(fld.jacobian_det_grid(), 1.1 * 0.95)
        ex, ey = fld.eval_xy(np.array([33.0, 77.0]), np.array([25.0, 61.0]))
        np.testing.assert_allclose(ex, [3.3, 7.7])
        np.testing.assert_allclose(ey, [-1.25, -3.05])

    def test_affine_irls_rejects_outlier(self):
        rng = np.random.default_rng(42)
        src = rng.uniform(0, 100, size=(30, 2))
        A_true = np.array([[0.32, 0.0, 2400.0], [0.0, 0.32, 20.0]])
        dst = src @ A_true[:, :2].T + A_true[:, 2]
        # Corrupt 2 severe outliers
        dst[0] += [500.0, -300.0]
        dst[1] += [-250.0, 400.0]
        A_est = emr._fit_affine_irls(src, dst, iters=12, huber_delta=1.0)
        np.testing.assert_allclose(A_est, A_true, atol=0.15)


class TestGeneratedTablesInvariants(unittest.TestCase):
    """Verify structural invariants on the live generated docs/states/eu_mapping/ tables."""

    def setUp(self):
        self.mdir = Path(repo_root()) / "docs" / "states" / "eu_mapping"
        if not (self.mdir / "mapping_summary.json").is_file():
            self.skipTest("docs/states/eu_mapping not generated yet")

    def test_disjoint_complete_partition(self):
        summary = json.loads((self.mdir / "mapping_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["registration_diffeomorphic_ok"])
        self.assertGreaterEqual(summary["registration_same_owner_acc"], 0.99)
        self.assertEqual(summary["registration_capitals_hit"], "39/39")

        # Every ME state appears exactly once in state_partition_m2v.tsv
        with open(self.mdir / "state_partition_m2v.tsv", encoding="utf-8") as f:
            m2v = list(csv.DictReader(f, delimiter="\t"))
        with open(self.mdir / "state_partition_v2m.tsv", encoding="utf-8") as f:
            v2m = list(csv.DictReader(f, delimiter="\t"))
        with open(self.mdir / "state_primary_city.tsv", encoding="utf-8") as f:
            pc = {int(r["vanilla_id"]): r for r in csv.DictReader(f, delimiter="\t")}
        # Zero missing primary cities and Berlin/Edinburgh/Venice sanity checks!
        self.assertTrue(all(r["primary_me_id"] for r in pc.values()))
        self.assertEqual(pc[64]["primary_basis"], "national_capital:GER")  # Berlin, NOT Bonn (WGR)!
        self.assertEqual(pc[126]["primary_basis"], "national_capital:ENG")  # Greater London
        self.assertEqual(pc[16]["primary_basis"], "national_capital:FRA")   # Paris
        self.assertEqual(pc[107]["primary_basis"], "national_capital:YUG")  # Belgrade!
        self.assertEqual(len(m2v), summary["total_me_states"])

        mapped_m = {int(r["me_id"]): int(r["v_id"]) for r in m2v if r["status"] == "mapped"}
        self.assertEqual(len(mapped_m), summary["eu_me_states"])

        # Disjoint union of v2m me_members == exact set of mapped_m!
        seen_in_v2m = []
        for r in v2m:
            vid = int(r["vanilla_id"])
            for x in r["me_members"].split("|"):
                if not x:
                    continue
                mid = int(x)
                seen_in_v2m.append(mid)
                self.assertEqual(mapped_m[mid], vid)
        self.assertEqual(len(seen_in_v2m), len(set(seen_in_v2m)))
        self.assertEqual(set(seen_in_v2m), set(mapped_m.keys()))

        # Spot-check famous bug fixes vs legacy table!
        self.assertIn(mapped_m[194], (136, 121, 133))  # Perth is in Scotland, NEVER Western Australia (522)!
        self.assertEqual(mapped_m[790], 107)           # Belgrade is Serbia (107), NOT Vojvodina (45)!
        self.assertEqual(mapped_m[391], 57)            # Münster is Westfalen (57), NOT Weser-Ems (56)!
        self.assertEqual(mapped_m[393], 59)            # Hildesheim is Hannover (59), NOT Thüringen (60)!
        self.assertEqual(mapped_m[538], 18)            # Verdun is Champagne (18), NOT Alsace-Lorraine (28)!
        self.assertEqual(mapped_m[901], 176)           # Soria is Burgos (176), NOT Extremadura (170)!
        self.assertEqual(mapped_m[116], 933)           # Zetland is Shetland Islands (933)!
        self.assertEqual(mapped_m[130], 932)           # Isle of Mann is Isle of Man (932)!
        self.assertEqual(mapped_m[90], 337)            # Færø is Faroe Islands (337)!
        self.assertEqual(mapped_m[1822], 914)          # Jan Mayen is Jan Mayen (914)!
        # Overwhelming (>=75%) geometric interiors must never be overridden by a 7% core sliver!
        self.assertEqual(mapped_m[1697], 62)           # Neustrelitz (89.7% inside Vorpommern 62)
        self.assertEqual(mapped_m[2090], 200)          # Melitopol (87.8% inside Zaporozhe 200)
        self.assertEqual(mapped_m[2978], 70)           # Trnava (77.1% inside Western Slovakia 70)
        # VP/name/island straddler tie-break fixes!
        self.assertEqual(mapped_m[138], 263)           # Novgorod -> v263 Novgorod
        self.assertEqual(mapped_m[102], 244)           # Volkhov -> v244 Volkhov
        self.assertEqual(mapped_m[747], 20)            # Lyon -> v20 Rhone
        self.assertEqual(mapped_m[1066], 344)          # Adana -> v344 Malatya (Adana VP)
        self.assertEqual(mapped_m[1282], 662)          # Sirte -> v662 Sirte
        self.assertEqual(mapped_m[1240], 451)          # Quba (Derna VP) -> v451 Derna
        self.assertEqual(mapped_m[1193], 1003)         # Khorāsān-e Jonūbī -> v1003 South Khorasan
        self.assertEqual(mapped_m[1220], 421)          # Ilam -> v421 Ilam
        self.assertEqual(mapped_m[1621], 115)          # Lampedusa (ITA, SIC) -> v115 Sicily, NOT Tunisia!
        self.assertEqual(mapped_m[491], 92)            # Zamość (63% Lublin, 1-VP minor town) stays in v92 Lublin
        # Zero cross-owner fallbacks across all European ME states!
        self.assertEqual(sum(1 for r in m2v if r["status"] == "mapped" and r["norm_owner"] != r["v_owner"]), 0)
        # Approved human adjudications (7 overrides + 52 confirmations -> 0 open review queue items!)
        self.assertEqual(mapped_m[1632], 62)           # Randow -> v62 Vorpommern (1945 Oder-Neisse)
        self.assertEqual(mapped_m[856], 231)           # Samegrelo -> v231 Georgia (not Abkhazia)
        self.assertEqual(mapped_m[2853], 32)           # Vallée de la Romanche -> v32 Alpes (not Savoy)
        self.assertEqual(mapped_m[2212], 675)          # Al Bussayyah -> v675 Al Hajara (not Basrah)
        self.assertEqual(mapped_m[1264], 451)          # Butnan (Tobruch/Bardia) -> v451 Derna
        self.assertEqual(mapped_m[1289], 449)          # Ajdabiya (El-Agheila) -> v449 El Agheila
        self.assertEqual(mapped_m[1669], 66)           # Brieg/Ohlau -> v66 Lower Silesia
        self.assertEqual(summary["review_queue_count"], 0)
        self.assertEqual(summary["adjudicated_count"], 59)
        self.assertEqual(summary["adjudicated_overrides"], 7)
        with open(self.mdir / "adjudications.tsv", encoding="utf-8") as f:
            adj_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(adj_rows), 59)
            self.assertEqual(sum(1 for r in adj_rows if r["action"] == "override"), 7)
        # Review queue carries enriched auxiliary decision columns
        with open(self.mdir / "review_queue.tsv", encoding="utf-8") as f:
            rq_reader = csv.DictReader(f, delimiter="\t")
            for col in ("gameplay_impact", "border_script_diff_count", "me_vps", "me_sub_cores", "advisory"):
                self.assertIn(col, rq_reader.fieldnames)
            rq_rows = list(rq_reader)
            self.assertEqual(len(rq_rows), summary["review_queue_count"])


class TestUnmappedVanillaAudit(unittest.TestCase):
    """Test block-level unmapped vanilla state-ID detection in audit_eu_script_refs.py."""

    def test_catches_unannotated_and_half_migrated_without_false_positives(self):
        mdir = Path(repo_root()) / "docs" / "states" / "eu_mapping"
        if not (mdir / "state_partition_v2m.tsv").is_file():
            self.skipTest("eu_mapping tables not present")
        idx = tvs.EUMappingIndex(mdir)

        with tempfile.TemporaryDirectory() as td:
            van_root = Path(td) / "van"
            mod_root = Path(td) / "mod"
            (van_root / "events").mkdir(parents=True)
            (mod_root / "events").mkdir(parents=True)

            # Vanilla event references Hatay (799), Scottish Highlands (120.OWNER), and off-map Puerto Rico (686)
            van_evt = (
                "country_event = {\n"
                "\tid = test.1\n"
                "\toption = {\n"
                "\t\ttransfer_state = 799\n"
                "\t\t120 = { is_controlled_by = 120.OWNER }\n"
                "\t\towns_state = 686\n"
                "\t}\n"
                "}\n"
                "country_event = {\n"
                "\tid = test.2\n"
                "\toption = {\n"
                "\t\ttransfer_state = 799\n"
                "\t\tstate = 122\n"
                "\t}\n"
                "}\n"
            )
            # Mod: test.1 still has unannotated 799, half-migrated 120.OWNER on annotated line, and off-map 686;
            #      test.2 has properly remapped 1123 AND legitimate ME 122 (v86 Poznań, colliding with vanilla 122 Wales)
            #      annotated with `# eu_mapping:` -> neither must be flagged!
            mod_evt = (
                "country_event = {\n"
                "\tid = test.1\n"
                "\toption = {\n"
                "\t\ttransfer_state = 799\n"
                "\t\t158 = { is_controlled_by = 120.OWNER } # 1.19-migration: was vanilla 120\n"
                "\t\towns_state = 686\n"
                "\t}\n"
                "}\n"
                "country_event = {\n"
                "\tid = test.2\n"
                "\toption = {\n"
                "\t\ttransfer_state = 1123 # eu_mapping: v799 (Hatay) -> ME 1123 (Hatay)\n"
                "\t\tstate = 122 # eu_mapping: v86 (Poznań) -> ME 122 (Poznań)\n"
                "\t}\n"
                "}\n"
            )
            (van_root / "events" / "Test.txt").write_text(van_evt, encoding="utf-8")
            (mod_root / "events" / "Test.txt").write_text(mod_evt, encoding="utf-8")

            findings = aesr.scan_unmapped_vanilla(
                idx, vanilla_dir=van_root, mod_dir=mod_root, categories=(("events", "event"),)
            )
            got = {(f["block"], f["kind"], f["sid"], f["status"]) for f in findings}
            self.assertIn(("test.1", "transfer_state", 799, "EU_UNMAPPED_VANILLA"), got)
            self.assertIn(("test.1", "<chain>", 120, "EU_UNMAPPED_VANILLA"), got)
            self.assertIn(("test.1", "owns_state", 686, "OFFMAP_UNMAPPED_VANILLA"), got)
            self.assertFalse(any(f["block"] == "test.2" for f in findings))


if __name__ == "__main__":
    unittest.main()
