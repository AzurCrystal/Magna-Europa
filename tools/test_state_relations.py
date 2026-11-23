"""Unit tests for state_relations over tiny temporary TSV fixtures.

No map files, no canonical tables: each test writes a minimal
state_edges.tsv + v2m/m2v registry into a TemporaryDirectory.
"""

import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import state_relations as sr  # noqa: E402


V2M_COLUMNS = ["vanilla_id", "vanilla_name", "owner", "v_prov_count",
               "region", "me_primary", "me_members", "me_names",
               "basis", "status"]
M2V_COLUMNS = ["me_id", "me_name", "owner", "prov_count", "region",
               "cat", "manpower", "v_ancestor", "v_name", "basis", "status"]


def _edge(v=10, m=100, cls="canonical", canon="1", prim="0",
          me_name=None, scope=None, **over):
    # Default scope mirrors the generator: excluded rows and canonical rows
    # are fixed; anything else needs an explicit scope override.
    if scope is None:
        scope = "excluded" if cls == "excluded" else \
            "compatibility" if canon == "1" else "observation"
    row = {
        "vanilla_id": str(v), "vanilla_name": "V%d" % v,
        "me_id": str(m), "me_name": me_name or "M%d" % m,
        "edge_source": "geometry|canonical",
        "evidence_families": "geometry|canonical",
        "is_canonical": canon,
        "canonical_ancestor": str(v) if canon == "1" else "10",
        "canonical_primary_of_vanilla": prim,
        "me_area_px": "100", "me_match_px": "80", "me_share": "0.800000",
        "me_rank": "1",
        "me_modal_vanilla": str(v), "me_modal_share": "0.800000",
        "me_modal_margin": "0.600000", "me_assigned_share": "0.800000",
        "me_outside_px": "10", "me_water_px": "5", "me_unassigned_px": "5",
        "me_components": "1", "relation_scope": scope,
        "edge_class": cls, "geometry_usable": "1",
        "v_native_area_px": "200", "v_observable_px": "180",
        "v_match_px": "90", "v_share_observable": "0.500000",
        "v_share_native": "0.450000", "v_rank": "1",
        "comment_votes": "", "vp_votes": "",
        "name_exact": "0", "same_id": "0", "manual_kind": "",
    }
    row.update({k: str(val) for k, val in over.items()})
    return row


def _v2m(v, primary="", members="", basis="comment", status="1:many"):
    return {"vanilla_id": str(v), "vanilla_name": "V%d" % v, "owner": "TAG",
            "v_prov_count": "1", "region": "1", "me_primary": str(primary),
            "me_members": members, "me_names": "", "basis": basis,
            "status": status}


def _m2v(m, ancestor="", owner="TAG", name=None, basis="comment"):
    return {"me_id": str(m), "me_name": name or "M%d" % m, "owner": owner,
            "prov_count": "1", "region": "1", "cat": "town", "manpower": "1",
            "v_ancestor": str(ancestor),
            "v_name": "V%s" % ancestor if ancestor else "",
            "basis": basis, "status": "mapped" if ancestor else "mod-new"}


def _fixture_edges():
    """One-to-many (10->100,101,999), asserted many-to-many (101->10,20),
    observation-only (30->301), synthetic 999."""
    mb = _edge(10, 999, cls="excluded", canon="1", prim="0",
               me_name="Map Border", me_match_px="1", me_share="0.010000",
               me_rank="2")
    return [  # sorted by (me_id, vanilla_id)
        _edge(10, 100, prim="1"),
        _edge(10, 101),
        _edge(20, 101, cls="significant", canon="0", prim="0",
               scope="asserted", edge_source="comment",
               evidence_families="comment",
               me_match_px="15", me_share="0.150000", me_rank="2",
               me_modal_vanilla="10", me_modal_share="0.800000",
               me_modal_margin="0.600000"),
        _edge(20, 200, cls="canonical", prim="1"),
        _edge(30, 301, cls="significant", canon="0", prim="0",
               scope="observation", edge_source="raster",
               evidence_families="pixel|raster",
               canonical_ancestor="", me_match_px="70",
               me_share="0.700000", me_modal_vanilla="30",
               me_modal_share="0.700000", me_modal_margin="0.500000"),
        mb,
    ]


def _fixture_registries():
    v2m = [
        _v2m(10, primary="100", members="100|101|999"),
        _v2m(20, primary="200", members="200"),
        _v2m(30, members="", basis="unmapped", status="unmapped"),
    ]
    m2v = [
        _m2v(100, "10"), _m2v(101, "10"), _m2v(200, "20"),
        _m2v(301, "", basis="pixel"),
        _m2v(999, "10", owner="MAP", name="Map Border", basis="pixel-geo-low"),
    ]
    return v2m, m2v


def _write(docs_dir, edges, v2m, m2v):
    os.makedirs(docs_dir, exist_ok=True)
    for name, cols, rows in ((sr.EDGES_FILENAME, sr.COLUMNS, edges),
                             ("v2m_states.tsv", V2M_COLUMNS, v2m),
                             ("m2v_states.tsv", M2V_COLUMNS, m2v)):
        with open(os.path.join(docs_dir, name), "w", encoding="utf-8",
                  newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t")
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c, "") for c in cols})


def _load(edges=None, v2m=None, m2v=None):
    tmp = tempfile.TemporaryDirectory()
    e = _fixture_edges() if edges is None else edges
    v, m = _fixture_registries()
    _write(tmp.name, e, v2m if v2m is not None else v,
           m2v if m2v is not None else m)
    return sr.load(tmp.name), tmp


class RelationApiTests(unittest.TestCase):
    def test_expand_one_to_many_primary_first(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.expand(10), [100, 101])
        self.assertEqual(rel.expand(20), [200, 101])  # asserted secondary edge
        self.assertEqual(rel.expand(30), [])          # observation hidden
        self.assertEqual(rel.expand(9999), [])

    def test_expand_include_excluded(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.expand(10, include_excluded=True), [100, 101, 999])
        self.assertTrue(rel.contains(10, 999, include_excluded=True))
        self.assertFalse(rel.contains(10, 999))
        # include_excluded only adds excluded scope; observations stay out.
        self.assertEqual(rel.expand(30, include_excluded=True), [])
        self.assertTrue(rel.contains(30, 301, scopes=sr.OBSERVED_SCOPES))

    def test_ancestors_many_to_many(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        # ME 101 has a canonical edge to 10 plus an asserted secondary to 20.
        self.assertEqual(rel.ancestors(101), [10, 20])
        self.assertEqual(rel.ancestors(999), [])  # excluded edge hidden
        self.assertEqual(rel.ancestors(999, include_excluded=True), [10])
        self.assertEqual(rel.ancestors(200), [20])
        self.assertEqual(rel.ancestors(301), [])  # observation hidden
        self.assertEqual(rel.ancestors(7777), [])

    def test_observed_queries(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        # Observation-scope raster edge (30, 301) needs explicit reach.
        self.assertEqual(rel.expand_observed(30), [301])
        self.assertEqual(rel.ancestors_observed(301), [30])
        self.assertTrue(rel.contains(30, 301, scopes={"observation"}))
        self.assertFalse(rel.contains(30, 301))
        # *_all adds the excluded diagnostic edges as well.
        self.assertEqual(rel.expand_all(10), [100, 101, 999])
        self.assertEqual(rel.ancestors_all(999), [10])
        # Observation scopes do not add edges inside asserted-only views.
        self.assertEqual(rel.expand_observed(10), [100, 101])
        self.assertEqual(rel.ancestors_observed(999), [])

    def test_explicit_scopes(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        only_obs = {"observation"}
        self.assertEqual(rel.expand(10, scopes=only_obs), [])
        self.assertEqual(rel.expand(30, scopes=only_obs), [301])
        self.assertEqual(rel.intersect([30], scopes=only_obs), [301])
        self.assertEqual(rel.intersect([10, 30]), [])
        cov = rel.coverage(30, [301], scopes=only_obs)
        self.assertEqual(cov["status"], "full")
        self.assertEqual(rel.coverage(30)["status"], "unmapped")

    def test_unknown_scope_rejected(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            rel.expand(10, scopes={"bogus"})
        with self.assertRaises(ValueError):
            rel.ancestors(100, scopes=["proven"])
        with self.assertRaises(ValueError):
            rel.coverage(10, scopes={"complete_subset"})

    def test_primaries(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.primary_member(10), 100)
        self.assertEqual(rel.primary_member(20), 200)
        self.assertIsNone(rel.primary_member(30))
        self.assertIsNone(rel.primary_ancestor(301))
        self.assertEqual(rel.primary_ancestor(100), 10)
        self.assertEqual(rel.primary_ancestor(101), 10)
        self.assertEqual(rel.primary_ancestor(999), 10)

    def test_contains(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertTrue(rel.contains(10, 100))
        self.assertTrue(rel.contains(20, 101))
        self.assertFalse(rel.contains(10, 200))
        self.assertFalse(rel.contains(30, 100))

    def test_intersect(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.intersect([10, 20]), [101])
        self.assertEqual(rel.intersect([10]), [100, 101])
        self.assertEqual(rel.intersect([10, 30]), [])
        self.assertEqual(rel.intersect([30]), [])
        self.assertEqual(rel.intersect([]), [])

    def test_coverage(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.coverage(10, [100, 101])["status"], "full")
        self.assertEqual(rel.coverage(10, [100])["status"], "partial")
        self.assertEqual(rel.coverage(10, [200])["status"], "none")
        self.assertEqual(rel.coverage(10, [200])["missing"], [100, 101])
        self.assertEqual(rel.coverage(30)["status"], "unmapped")
        # Excluded member 999 never enters the covered set by default.
        cov = rel.coverage(10, [100, 101, 999])
        self.assertEqual(cov["status"], "full")
        self.assertEqual(cov["covered"], [100, 101])

    def test_validate_clean(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(rel.validate(), [])
        self.assertEqual(sr.validate(tmp.name), [])

    def test_edge_row_access(self):
        rel, tmp = _load()
        self.addCleanup(tmp.cleanup)
        row = rel.edge(10, 999)
        self.assertIsNotNone(row)
        self.assertEqual(row["edge_class"], "excluded")
        self.assertEqual(row["relation_scope"], "excluded")
        self.assertEqual(rel.edge(30, 301)["relation_scope"], "observation")
        self.assertIsNone(rel.edge(10, 200))


class RelationValidationTests(unittest.TestCase):
    def _bad_load(self, edges, v2m=None, m2v=None):
        with tempfile.TemporaryDirectory() as d:
            v, m = _fixture_registries()
            _write(d, edges, v2m if v2m is not None else v,
                   m2v if m2v is not None else m)
            with self.assertRaises(sr.RelationError):
                sr.load(d)

    def test_duplicate_pair_rejected(self):
        edges = _fixture_edges() + [_edge(10, 100, prim="1")]
        edges.sort(key=lambda r: (int(r["me_id"]), int(r["vanilla_id"])))
        self._bad_load(edges)

    def test_unknown_ids_rejected(self):
        edges = _fixture_edges() + [_edge(77, 300)]
        edges.sort(key=lambda r: (int(r["me_id"]), int(r["vanilla_id"])))
        self._bad_load(edges)

    def test_unsorted_rows_rejected(self):
        edges = _fixture_edges()
        edges[0], edges[1] = edges[1], edges[0]  # (10,101) before (10,100)
        self._bad_load(edges)

    def test_synthetic_not_excluded_rejected(self):
        edges = _fixture_edges()
        edges[5]["edge_class"] = "significant"  # Map Border row
        self._bad_load(edges)

    def test_two_canonical_edges_same_me_rejected(self):
        edges = _fixture_edges()
        edges[2]["is_canonical"] = "1"      # asserted row, me 101 already has 10
        edges[2]["canonical_ancestor"] = "20"
        self._bad_load(edges)

    def test_bad_ratio_rejected(self):
        edges = _fixture_edges()
        edges[0]["me_share"] = "1.500000"
        self._bad_load(edges)

    def test_two_primaries_same_vanilla_rejected(self):
        edges = _fixture_edges()
        edges[1]["canonical_primary_of_vanilla"] = "1"
        self._bad_load(edges)

    def test_bad_scope_rejected(self):
        edges = _fixture_edges()
        edges[4]["relation_scope"] = "overlap"  # (30, 301) observation row
        self._bad_load(edges)

    def test_missing_scope_rejected(self):
        edges = _fixture_edges()
        edges[0]["relation_scope"] = ""
        self._bad_load(edges)

    def test_scope_edge_class_mismatch_rejected(self):
        edges = _fixture_edges()
        edges[0]["relation_scope"] = "excluded"  # scope says excluded, class not
        self._bad_load(edges)

    def test_excluded_scope_wrong_scope_rejected(self):
        edges = _fixture_edges()
        edges[5]["relation_scope"] = "asserted"  # Map Border excluded edge
        self._bad_load(edges)

    def test_compatibility_scope_not_canonical_rejected(self):
        edges = _fixture_edges()
        edges[2]["relation_scope"] = "compatibility"  # canon=0 asserted row
        self._bad_load(edges)

    def test_missing_edge_table(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, [], [], [])
            os.remove(os.path.join(d, sr.EDGES_FILENAME))
            self.assertFalse(sr.is_available(d))
            with self.assertRaises(FileNotFoundError):
                sr.load(d)
            self.assertTrue(sr.validate(d))  # reports, never raises


if __name__ == "__main__":
    unittest.main()
