"""Unit tests for tools/build_state_edges_1936.py pure helpers.

Covers the documented owner-resolution contract (`state_owner_at` over a
parsed `history` block's children): the undated `owner = TAG` base, dated
`Y.M.D[.H]` blocks applied only while <= the cutoff (any hour of the cutoff
day counts, ascending date order), and "" for missing owners. The
owner_relation/candidate_scope ladder lives inline in main(); the tests pin
its documented behaviour by feeding real resolved owners into the documented
mapping, plus schema constants. Synthetic pdx_parse nodes only — no real
history/states trees, no rasters, no game data.
"""

from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_state_edges_1936 as bse  # noqa: E402
from pdx_parse import Node, Parser  # noqa: E402

CUTOFF = (1936, 1, 1)


def _leaf(key, val):
    return Node(key, val, 0)


def _block(key, *children):
    return Node(key, None, 0, list(children))


def _history(text):
    """Parse synthetic Clausewitz text; return the `history` children list."""
    nodes = Parser(text).parse()
    hist = [n for n in nodes if n.key == "history" and n.is_block]
    assert len(hist) == 1, "fixture must contain exactly one history block"
    return hist[0].children


def _owner_relation(me_owner, vanilla_owner):
    """Documented owner_relation/candidate_scope ladder (module docstring +
    the row loop in main): both sides known -> same_owner/within_country when
    tags match else cross_owner/cross_country_diagnostic; either side "" ->
    unknown_owner/unknown_owner. Mirrors the contract so date-cutoff or
    owner-resolution regressions surface as relation failures."""
    if me_owner and vanilla_owner:
        if me_owner == vanilla_owner:
            return "same_owner", "within_country"
        return "cross_owner", "cross_country_diagnostic"
    return "unknown_owner", "unknown_owner"


class DateKeyTests(unittest.TestCase):
    def test_plain_date(self):
        self.assertEqual(bse._parse_date_key("1936.1.1"), (1936, 1, 1, 0))

    def test_date_with_hour(self):
        self.assertEqual(bse._parse_date_key("1936.1.1.6"), (1936, 1, 1, 6))
        self.assertEqual(bse._parse_date_key("1936.1.1.24"), (1936, 1, 1, 24))

    def test_unpadded_and_short_year(self):
        self.assertEqual(bse._parse_date_key("36.1.1"), (36, 1, 1, 0))
        self.assertEqual(bse._parse_date_key("1936.12.31"), (1936, 12, 31, 0))

    def test_rejects_non_dates(self):
        for key in ("", None, "add_core_of", "1936.1", "1936.1.1.1.1",
                    "1936-1-1", "victory_points", "1936.1.1x"):
            self.assertIsNone(bse._parse_date_key(key), key)


class StateOwnerAtTests(unittest.TestCase):
    def test_undated_owner(self):
        children = _history('history = { owner = GER }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")

    def test_last_undated_owner_wins(self):
        children = _history('history = { owner = FRA owner = ITA }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_dated_before_cutoff_applies(self):
        children = _history(
            'history = { owner = GER 1935.6.1 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_dated_on_cutoff_applies(self):
        children = _history(
            'history = { owner = GER 1936.1.1 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_dated_on_cutoff_with_hour_applies(self):
        children = _history(
            'history = { owner = GER 1936.1.1.6 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")
        children = _history(
            'history = { owner = GER 1936.1.1.24 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_dated_after_cutoff_ignored(self):
        children = _history(
            'history = { owner = GER 1936.1.2 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")
        children = _history(
            'history = { owner = GER 1936.1.2.1 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")
        children = _history(
            'history = { owner = GER 1937.1.1 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")

    def test_dated_blocks_apply_in_ascending_date_order(self):
        # File order is not date order; ascending (date, order) applies.
        children = _history(
            'history = { owner = GER '
            '1936.1.1 = { owner = ITA } '
            '1930.1.1 = { owner = FRA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_later_hour_of_cutoff_day_wins(self):
        children = _history(
            'history = { owner = GER '
            '1936.1.1.12 = { owner = ITA } '
            '1936.1.1.1 = { owner = FRA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")

    def test_owner_inside_non_dated_block_not_read(self):
        children = _history(
            'history = { owner = GER '
            'buildings = { owner = FRA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")

    def test_malformed_date_block_ignored(self):
        children = _history(
            'history = { owner = GER '
            '1936.13 = { owner = FRA } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")

    def test_missing_owner_returns_empty(self):
        children = _history('history = { add_core_of = GER }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "")
        children = _history('history = { }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "")
        self.assertEqual(bse.state_owner_at(None, CUTOFF), "")
        self.assertEqual(bse.state_owner_at([], CUTOFF), "")

    def test_dated_only_state_resolves_to_dated_owner(self):
        children = _history(
            'history = { 1919.1.1 = { owner = POL } }')
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "POL")

    def test_owner_whitespace_stripped(self):
        children = [_leaf("owner", " GER ")]
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "GER")

    def test_custom_cutoff(self):
        children = _history(
            'history = { owner = GER 1936.1.1 = { owner = ITA } }')
        self.assertEqual(
            bse.state_owner_at(children, (1935, 12, 31)), "GER")
        self.assertEqual(
            bse.state_owner_at(children, (1937, 1, 1)), "ITA")

    def test_default_cutoff_is_1936(self):
        children = _history(
            'history = { owner = GER 1936.1.1 = { owner = ITA } }')
        self.assertEqual(bse.state_owner_at(children), "ITA")
        self.assertEqual(bse.CUTOFF, (1936, 1, 1))

    def test_synthetic_nodes_path(self):
        # Hand-built nodes (no Parser) follow the same contract.
        children = [
            _leaf("owner", "GER"),
            _block("1936.1.1.6", _leaf("owner", "ITA")),
            _block("1936.1.2", _leaf("owner", "FRA")),
        ]
        self.assertEqual(bse.state_owner_at(children, CUTOFF), "ITA")


class OwnerRelationTests(unittest.TestCase):
    """Documented owner_relation/candidate_scope behaviour over owners
    resolved by the real state_owner_at helper."""

    def _owners(self, me_text, vanilla_text):
        me = bse.state_owner_at(_history(me_text), CUTOFF)
        va = bse.state_owner_at(_history(vanilla_text), CUTOFF)
        return _owner_relation(me, va)

    def test_same_owner(self):
        self.assertEqual(
            _owner_relation("GER", "GER"),
            ("same_owner", "within_country"))
        self.assertEqual(
            self._owners('history = { owner = GER }',
                         'history = { owner = GER }'),
            ("same_owner", "within_country"))

    def test_cross_owner(self):
        self.assertEqual(
            _owner_relation("GER", "ITA"),
            ("cross_owner", "cross_country_diagnostic"))
        self.assertEqual(
            self._owners('history = { owner = GER }',
                         'history = { owner = ITA }'),
            ("cross_owner", "cross_country_diagnostic"))

    def test_unknown_owner(self):
        for me_o, v_o in (("", "GER"), ("GER", ""), ("", "")):
            self.assertEqual(
                _owner_relation(me_o, v_o),
                ("unknown_owner", "unknown_owner"))
        self.assertEqual(
            self._owners('history = { add_core_of = GER }',
                         'history = { owner = GER }'),
            ("unknown_owner", "unknown_owner"))

    def test_cutoff_regression_flips_relation(self):
        # The dated 1936.1.1.6 override brings the vanilla state to GER:
        # any regression that drops same-day-hour blocks flips this row
        # from same_owner to cross_owner.
        me_text = 'history = { owner = GER }'
        v_text = ('history = { owner = ITA '
                  '1936.1.1.6 = { owner = GER } }')
        self.assertEqual(self._owners(me_text, v_text),
                         ("same_owner", "within_country"))
        # A 1936.1.2 takeover must NOT count: still cross_owner.
        v_text = ('history = { owner = ITA '
                  '1936.1.2 = { owner = GER } }')
        self.assertEqual(self._owners(me_text, v_text),
                         ("cross_owner", "cross_country_diagnostic"))


class EdgeClassTests(unittest.TestCase):
    def test_map_owner_and_border_name_excluded(self):
        self.assertEqual(
            bse._edge_class(1, "X", "MAP", True, 0, 0), "excluded")
        self.assertEqual(
            bse._edge_class(1, "Alpine map border", "GER", True, 0, 0),
            "excluded")

    def test_precedence_ladder(self):
        self.assertEqual(
            bse._edge_class(1, "X", "GER", True, 0, 0),
            "identity_accepted")
        self.assertEqual(
            bse._edge_class(1, "X", "GER", False, bse.MIN_AREA, 0),
            "significant")
        self.assertEqual(
            bse._edge_class(1, "X", "GER", False, bse.MIN_AREA - 1, 0),
            "sliver")
        self.assertEqual(
            bse._edge_class(1, "X", "GER", False, 0, bse.MIN_AREA),
            "backward_only")
        self.assertEqual(
            bse._edge_class(1, "X", "GER", False, 0, bse.MIN_AREA - 1),
            "evidence_only")

    def test_min_area_constant(self):
        self.assertEqual(bse.MIN_AREA, 64)


class RankTests(unittest.TestCase):
    def test_rank_orders_by_pixels_then_id(self):
        pairs = {10: 50, 20: 50, 30: 90}
        self.assertEqual(bse._rank(pairs, 30), 1)
        self.assertEqual(bse._rank(pairs, 10), 2)   # tie: lower id first
        self.assertEqual(bse._rank(pairs, 20), 3)
        self.assertIsNone(bse._rank(pairs, 99))
        self.assertIsNone(bse._rank({}, 10))


class SchemaTests(unittest.TestCase):
    """Output schemas pinned as literal constants (regression anchors for
    the deterministic column layout; not parsed from source text)."""

    def test_columns(self):
        self.assertEqual(bse.COLUMNS, [
            "vanilla_id", "vanilla_name", "vanilla_owner",
            "me_id", "me_name", "me_owner",
            "owner_relation", "candidate_scope", "source_scope",
            "edge_source", "evidence_families",
            "is_canonical", "canonical_ancestor",
            "edge_class", "comment_votes", "manual_kind",
            "me_area_px", "me_match_px", "me_share",
            "me_owner_area_px", "me_owner_match_px", "me_owner_share",
            "me_rank_global", "me_rank_owner",
            "v_native_area_px", "v_observable_px", "v_match_px", "v_share",
            "v_owner_area_px", "v_owner_match_px", "v_owner_share",
            "v_rank_global", "v_rank_owner",
        ])

    def test_summary_columns(self):
        self.assertEqual(bse.SUMMARY_COLUMNS, [
            "owner",
            "me_states", "me_land_px",
            "me_owner_sampled_px", "me_owner_sampled_share",
            "vanilla_states", "v_land_px",
            "v_owner_sampled_px", "v_owner_sampled_share",
            "rows_total", "within_country", "cross_country_diagnostic",
            "unknown_owner_rows",
            "canonical_rows", "canonical_within_country",
            "canonical_cross_owner",
            "same_owner_partners", "cross_owner_partners",
        ])

    def test_ledger_copy(self):
        self.assertEqual(bse.LEDGER_COPY, (
            "edge_source", "evidence_families", "is_canonical",
            "canonical_ancestor", "edge_class", "comment_votes",
            "manual_kind", "relation_scope"))

    def test_relation_vocabulary(self):
        # Documented owner_relation/candidate_scope vocabulary.
        self.assertEqual(
            {rel for rel, _ in
             [_owner_relation(a, b)
              for a, b in (("G", "G"), ("G", "I"), ("", "G"))]},
            {"same_owner", "cross_owner", "unknown_owner"})
        self.assertEqual(
            {scope for _, scope in
             [_owner_relation(a, b)
              for a, b in (("G", "G"), ("G", "I"), ("", "G"))]},
            {"within_country", "cross_country_diagnostic",
             "unknown_owner"})


if __name__ == "__main__":
    unittest.main()
