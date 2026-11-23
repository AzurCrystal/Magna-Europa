#!/usr/bin/env python3
"""Integrity and topology unit tests for the ME-EAI precompiled map graph.

Run:
  C:\\Users\\AzurCrystal\\scoop\\apps\\python311\\current\\python.exe tools/test_eai_map_graph.py
"""

from __future__ import annotations

import json
import re
import unittest
from collections import deque
from pathlib import Path

from _modroot import repo_root


class TestEAIMapGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rroot = Path(repo_root())
        cls.l0_path = cls.rroot / "_ctx" / "eai_map_graph.json"
        cls.pdx_path = (
            cls.rroot
            / "mods"
            / "Expert-AI-for-Magna-Europa"
            / "common"
            / "scripted_effects"
            / "ME_EAI_map_data.txt"
        )
        assert cls.l0_path.exists(), f"Missing {cls.l0_path}; run tools/build_eai_map_graph.py first"
        assert cls.pdx_path.exists(), f"Missing {cls.pdx_path}; run tools/build_eai_map_graph.py first"
        cls.data = json.loads(cls.l0_path.read_text(encoding="utf-8"))
        cls.pdx_text = cls.pdx_path.read_text(encoding="utf-8")

    def _csr_neighbors(self, csr: dict, u: int) -> list[tuple[int, int]]:
        off, adj, eflag = csr["off"], csr["adj"], csr["eflag"]
        return [(adj[k], eflag[k]) for k in range(off[u], off[u + 1])]

    def _bfs(self, csr: dict, start: int, target: int, pred) -> int:
        q = deque([(start, 0)])
        seen = {start}
        while q:
            u, d = q.popleft()
            if u == target:
                return d
            for v, ef in self._csr_neighbors(csr, u):
                if v not in seen and pred(ef):
                    seen.add(v)
                    q.append((v, d + 1))
        return -1

    def test_sr_csr_invariants(self) -> None:
        sr = self.data["sr_csr"]
        n = self.data["summary"]["num_srs"]
        self.assertEqual(n, 253)
        self.assertEqual(len(sr["off"]), n + 2)
        self.assertEqual(len(sr["adj"]), len(sr["eflag"]))
        self.assertEqual(sr["off"][0], 0)
        self.assertEqual(sr["off"][n + 1], len(sr["adj"]))
        for u in range(1, n + 1):
            self.assertLessEqual(sr["off"][u], sr["off"][u + 1])
            nbrs = self._csr_neighbors(sr, u)
            seen = set()
            for v, ef in nbrs:
                self.assertNotEqual(u, v, f"SR self-loop on {u}")
                self.assertNotIn(v, seen, f"Duplicate SR edge {u}->{v}")
                seen.add(v)
                self.assertIn(ef, range(1, 9))
                # Symmetry
                rev = dict(self._csr_neighbors(sr, v))
                self.assertEqual(rev.get(u), ef, f"Asymmetric SR edge {u}<->{v}")

    def test_state_csr_invariants(self) -> None:
        st = self.data["state_csr"]
        n = self.data["summary"]["num_states"]
        self.assertEqual(n, 2986)
        self.assertEqual(len(st["off"]), n + 2)
        self.assertEqual(len(st["adj"]), len(st["eflag"]))
        self.assertEqual(len(st["sr"]), n + 1)
        self.assertEqual(len(st["meta"]), n + 1)
        self.assertEqual(st["off"][0], 0)
        self.assertEqual(st["off"][n + 1], len(st["adj"]))
        for u in range(1, n + 1):
            self.assertLessEqual(st["off"][u], st["off"][u + 1])
            self.assertIn(st["meta"][u], range(0, 9))
            nbrs = self._csr_neighbors(st, u)
            seen = set()
            for v, ef in nbrs:
                self.assertNotEqual(u, v, f"State self-loop on {u}")
                self.assertNotIn(v, seen, f"Duplicate state edge {u}->{v}")
                seen.add(v)
                self.assertIn(ef, range(1, 7))
                rev = dict(self._csr_neighbors(st, v))
                self.assertEqual(rev.get(u), ef, f"Asymmetric state edge {u}<->{v}")

    def test_european_connectivity_geography(self) -> None:
        sr = self.data["sr_csr"]
        # Brandenburg (69) -> Moscow (135) overland
        d_bm = self._bfs(sr, 69, 135, lambda ef: ef <= 4)
        self.assertGreater(d_bm, 0)
        self.assertLessEqual(d_bm, 12)
        # Brandenburg (69) -> Stalingrad (138) rail-only (even eflag in 2,4,6)
        d_bs_rail = self._bfs(sr, 69, 138, lambda ef: ef <= 6 and ef % 2 == 0)
        self.assertGreater(d_bs_rail, 0)
        # Southern England (45) -> Normandy (62): NOT overland, YES via sea corridor
        self.assertEqual(self._bfs(sr, 45, 62, lambda ef: ef <= 6), -1)
        d_eng_norm_sea = self._bfs(sr, 45, 62, lambda ef: ef >= 7)
        self.assertEqual(d_eng_norm_sea, 2)  # 45 -> 209 (English Channel) -> 62
        # Sicily (18) <-> Mezzogiorno (17) connected via strait (5 or 6)
        sic_nbrs = dict(self._csr_neighbors(sr, 18))
        self.assertIn(17, sic_nbrs)
        self.assertIn(sic_nbrs[17], (5, 6))

    def test_sectors_rivers_and_corridors(self) -> None:
        sectors = self.data["sectors"]
        self.assertEqual(len(sectors), 13)
        seen_srs = set()
        for sec in sectors:
            self.assertGreaterEqual(len(sec["anchors"]), 8, f"Too few anchors in {sec['key']}")
            self.assertLessEqual(len(sec["anchors"]), 12)
            for srid in sec["srs"]:
                self.assertNotIn(srid, seen_srs, f"SR {srid} in multiple sectors")
                seen_srs.add(srid)
                self.assertEqual(self.data["sr_csr"]["is_sea"][srid], 0, f"Sea SR {srid} in sector {sec['key']}")

        river_lines = self.data["river_lines"]
        self.assertEqual(len(river_lines), 7)
        for rl in river_lines:
            self.assertGreaterEqual(len(rl["fort_targets"]), 20, f"Too few river fort targets in {rl['key']}")
            for sid, pid in rl["fort_targets"]:
                self.assertGreater(sid, 0)
                self.assertGreater(pid, 0)
                self.assertGreater(self.data["state_csr"]["meta"][sid], 0)

        corridors = self.data["invasion_corridors"]
        self.assertEqual(len(corridors), 5)
        for cor in corridors:
            self.assertGreaterEqual(len(cor["targets"]), 8)
            for srid in cor["sea_srs"]:
                self.assertEqual(self.data["sr_csr"]["is_sea"][srid], 1, f"Land SR {srid} in sea_srs of {cor['key']}")

    def test_pdx_script_hygiene(self) -> None:
        # Zero meta_effect anywhere in generated map data
        self.assertNotIn("meta_effect", self.pdx_text)
        # Balanced braces
        opens = self.pdx_text.count("{")
        closes = self.pdx_text.count("}")
        self.assertEqual(opens, closes, f"Unbalanced braces: {opens} vs {closes}")
        # Required entry points & bidirectional scope mapping present
        for fn in (
            "ME_EAI_map_load_all",
            "ME_EAI_map_load_sr_csr",
            "ME_EAI_map_load_state_meta",
            "ME_EAI_map_load_state_edges",
            "ME_EAI_map_load_tactical_indexes",
        ):
            self.assertRegex(self.pdx_text, rf"(?m)^{fn}\s*=\s*\{{")
        self.assertIn("set_variable = { me_sid = 1 }", self.pdx_text)
        self.assertIn("set_variable = { me_sid = 2986 }", self.pdx_text)
        self.assertIn("add_to_array = { global.me_st_ref = THIS.id }", self.pdx_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
