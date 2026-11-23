#!/usr/bin/env python
"""Unit and cartographic invariant tests for Layer 5 (Vanilla <-> ME Strategic Regions):
  - tools/build_region_mapping.py
  - tools/translate_vanilla_regions.py
  - docs/states/eu_mapping/region_{overlap,partition_m2v,partition_v2m}.tsv

Run with:
  python -m unittest discover -s tools -p test_region_mapping.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
import translate_vanilla_regions as tvr  # noqa: E402

REPO = Path(repo_root())
MOD_SR_DIR = REPO / "mods" / "Magna-Europa" / "map" / "strategicregions"


class TestRegionMappingInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = tvr.EURegionMappingIndex()
        cls.sr_file_count = len(list(MOD_SR_DIR.glob("*.txt")))

    def test_complete_me_coverage_no_unmapped(self) -> None:
        self.assertEqual(len(self.idx.m2v), self.sr_file_count)
        self.assertEqual(len(self.idx.m2v), 253)
        unmapped = [mid for mid, r in self.idx.m2v.items() if r["status"] == "unmapped"]
        self.assertEqual(unmapped, [], f"Found unmapped ME SRs: {unmapped}")

    def test_excluded_scope_exact_11(self) -> None:
        excluded = {mid for mid, r in self.idx.m2v.items() if r["status"].startswith("excluded_")}
        self.assertEqual(excluded, {156, 157, 158, 159, 189, 190, 191, 192, 193, 252, 253})

        # Off-map / inset excluded regions stay out of translate_region unless include_proxy=True
        for vid in (197, 117, 121, 220, 54, 55, 50, 227, 158):
            default_res = self.idx.translate_region(vid)
            self.assertEqual(default_res["me_regions"], [])
            proxy_res = self.idx.translate_region(vid, include_proxy=True)
            self.assertTrue(len(proxy_res["me_regions"]) >= 1)

    def test_m2v_and_v2m_symmetry(self) -> None:
        assigned_from_v2m: dict[int, int] = {}
        for vid, vrow in self.idx.v2m.items():
            for mid in vrow["me_regions"]:
                self.assertNotIn(mid, assigned_from_v2m, f"Duplicate ME region {mid} in v2m")
                assigned_from_v2m[mid] = vid

        for mid, mrow in self.idx.m2v.items():
            target = mrow["vanilla_region_id"] or mrow["proxy_vanilla_id"]
            if target is not None:
                self.assertEqual(assigned_from_v2m.get(mid), target, f"Asymmetry on ME region {mid}")

    def test_sea_never_maps_to_pure_land(self) -> None:
        # Known Vanilla sea/water SR IDs must cover every mapped ME Sea SR
        for mid, mrow in self.idx.m2v.items():
            if mrow["kind"] == "sea" and mrow["status"] in ("mapped", "manual_override"):
                vid = mrow["vanilla_region_id"]
                self.assertIsNotNone(vid, f"ME sea SR {mid} missing vanilla parent")
                v_entry = self.idx.v2m[vid]
                self.assertEqual(v_entry["kind"], "sea", f"ME sea {mid} mapped to non-sea v{vid} ({v_entry['vanilla_name']})")

    def test_europe_land_ground_truth(self) -> None:
        cases = [
            # (ME SR ID, expected Vanilla SR ID)
            (45, 1),    # Southern England
            (46, 2),    # Northern England
            (2, 3),     # Scotland
            (1, 4),     # Ireland
            (4, 5),     # Netherlands -> Benelux
            (5, 5),     # Belgium -> Benelux
            (35, 7),    # Southern Germany -> Western Germany
            (36, 7),    # Rhineland -> Western Germany
            (68, 8),    # Saxony -> Eastern Germany
            (69, 8),    # Brandenburg -> Eastern Germany
            (89, 19),   # Northern France
            (87, 20),   # Aquitaine -> Southern France
            (6, 21),    # Switzerland -> Alpine Region
            (8, 22),    # Czechia -> Czechoslovakia
            (25, 23),   # Central Italy -> Italy
            (18, 23),   # Sicily -> Italy
            (19, 169),  # Tyrrhenian Islands -> Tyrrhenian Sea
            (34, 25),   # Greece
            (13, 26),   # Bulgaria -> Eastern Balkans
            (52, 27),   # Hungary -> Northern Balkans
            (44, 38),   # Masovia -> Western Poland
            (41, 39),   # Galicia -> Eastern Poland
            (135, 133), # Moscow -> Central Russia
            (118, 277), # Northern Finland (was wrongly v13 in legacy regions.tsv!)
            (144, 150), # Onega -> Archangelsk (was wrongly v265 in legacy regions.tsv!)
            (226, 298), # Southern Persia -> Eastern Zagros (was wrongly v116 in legacy regions.tsv!)
            (126, 237), # Hejaz
            (54, 28),   # Mesopotamia -> Middle East
            (58, 128),  # Lower Nile -> Egypt
            (37, 126),  # Algeria -> Eastern Maghreb
            (30, 182),  # Morocco -> Western Maghreb
            (38, 225),  # Tripolitania -> Tripoli
        ]
        for mid, expected_vid in cases:
            self.assertEqual(
                self.idx.m2v[mid]["vanilla_region_id"],
                expected_vid,
                f"ME {mid} ({self.idx.m2v[mid]['me_name']}) mismatch: {self.idx.m2v[mid]}",
            )

    def test_sea_ground_truth(self) -> None:
        sea_cases = [
            (75, 202),  # Aegean Sea
            (76, 202),  # Marmara Sea -> Aegean Sea
            (77, 100),  # Red Sea
            (160, 9),   # Gulf of Finland
            (161, 206), # Baltic Coast -> Lower Baltic Sea
            (163, 279), # Gulf of Bothnia
            (167, 207), # Danish Straits -> Danish Belts
            (176, 30),  # Crimean Black Sea -> Black Sea
            (180, 70),  # Persian Caspian Sea -> Caspian Sea
            (184, 70),  # Southern Caspian Sea -> Caspian Sea
            (185, 203), # North Persian Gulf -> Persian Gulf
            (187, 104), # Gulf of Oman -> Arabian Sea
            (188, 104), # Indian Ocean Passage -> Arabian Sea (manual bottom-edge override)
            (194, 168), # Adriatic Sea
            (195, 169), # Tyrrhenian Sea
            (199, 68),  # Balearic Sea -> Western Mediterranean Sea
            (200, 29),  # Strait of Sicily -> Central Mediterranean Sea
            (203, 69),  # Eastern Levantine Sea -> Eastern Mediterranean Sea
            (209, 18),  # English Channel
            (220, 42),  # Gulf of Biscay -> Bay of Biscay
            (232, 45),  # Norwegian Sea
            (234, 44),  # Denmark Strait
            (238, 173), # Jutland Sea -> Eastern North Sea
            (240, 16),  # North Sea
            (244, 43),  # Western Approaches
            (245, 47),  # Iberian Coast
            (246, 48),  # Moroccan Sea -> African Coast
        ]
        for mid, expected_vid in sea_cases:
            self.assertEqual(
                self.idx.m2v[mid]["vanilla_region_id"],
                expected_vid,
                f"ME sea {mid} ({self.idx.m2v[mid]['me_name']}) mismatch: {self.idx.m2v[mid]}",
            )


if __name__ == "__main__":
    unittest.main()
