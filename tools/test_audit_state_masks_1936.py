"""Unit tests for tools/audit_state_masks_1936.py pure helpers.

Covers the documented classification contract over synthetic numbers only:
the `_pair_class` ladder (containment at 0.90 on native-share denominators,
partial overlap at 0.20, bidirectional-hit precedence), the `_entity_class`
ladder, the claim vocabulary (`sampled_masks`/`withheld` only — never a
complete-subset proof), threshold/schema constants, and the fixed
`pair_reason` ordering. No real maps, no rasters, no history/states trees,
no canonical tables — the geometry inputs are hand-built numbers.
"""

from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_state_masks_1936 as asm  # noqa: E402

# Documented pair_reason emit order: geometry reasons from _pair_class,
# then caller-appended diagnostics.
REASON_ORDER = [
    "no_bidirectional_hit",
    "forward_only_hit",
    "reverse_only_hit",
    "me_share_below_partial",
    "v_share_below_partial",
    "me_share_below_containment",
    "v_share_below_containment",
    "mutual_containment",
    "me_not_sampled",
    "v_not_sampled",
    "geometry_not_usable",
    "cross_owner",
    "unknown_owner",
]

PAIR_CLASSES = {
    "no_sampled_overlap",
    "mutual_contained",
    "me_contained_in_vanilla",
    "vanilla_contained_in_me",
    "partial_overlap",
    "sliver_overlap",
}

ENTITY_CLASSES = {"unmatched", "contained", "split", "ambiguous"}

CLAIMS = {"sampled_masks", "withheld"}

# Forbidden identity claims (the audit never proves a complete subset).
FORBIDDEN_CLAIMS = {"proven", "complete_subset", "exact", "identical"}


def _pair(me_hit, v_hit, me_share, v_share_native):
    return asm._pair_class(me_hit, v_hit, me_share, v_share_native)


class ConstantTests(unittest.TestCase):
    """Gate thresholds pinned as literal constants (regression anchors)."""

    def test_gate_literals(self):
        self.assertEqual(asm.MIN_AREA, 64)
        self.assertEqual(asm.CONTAINMENT_SHARE, 0.90)
        self.assertEqual(asm.PARTIAL_SHARE, 0.20)
        self.assertEqual(asm.MIN_ASSIGNED_SHARE, 0.90)

    def test_cutoff_and_kind(self):
        self.assertEqual(asm.CUTOFF, (1936, 1, 1))
        # The summary kind string is the audit's own disclaimer.
        self.assertIn("not a complete-subset proof", asm.KIND)

    def test_default_output_names(self):
        self.assertEqual(
            asm.DEFAULT_OUTPUT.name, "state_mask_audit_1936.tsv")
        self.assertEqual(
            asm.DEFAULT_ENTITIES.name,
            "state_mask_audit_1936_entities.tsv")
        self.assertEqual(
            asm.DEFAULT_SUMMARY_JSON.name,
            "state_mask_audit_1936_summary.json")


class PairClassTests(unittest.TestCase):
    """`_pair_class(me_hit, v_hit, me_share, v_share_native)` -> (class,
    reasons): geometry-only ladder over the two directional native shares.
    Owner/country fields never enter the classification."""

    def test_no_hit_either_direction(self):
        cls, reasons = _pair(0, 0, None, None)
        self.assertEqual(cls, "no_sampled_overlap")
        self.assertIn("no_bidirectional_hit", reasons)
        self.assertNotIn("forward_only_hit", reasons)
        self.assertNotIn("reverse_only_hit", reasons)

    def test_no_hit_with_zero_shares(self):
        cls, _ = _pair(0, 0, 0.0, 0.0)
        self.assertEqual(cls, "no_sampled_overlap")

    def test_mutual_containment_at_threshold(self):
        cls, reasons = _pair(90, 180, 0.90, 0.90)
        self.assertEqual(cls, "mutual_contained")
        self.assertIn("mutual_containment", reasons)

    def test_mutual_containment_beats_single_side(self):
        # Both directions >= containment -> mutual, never a one-sided class.
        cls, _ = _pair(100, 200, 0.95, 1.0)
        self.assertEqual(cls, "mutual_contained")

    def test_me_contained_only_forward_direction(self):
        cls, _ = _pair(90, 40, 0.90, 0.40)
        self.assertEqual(cls, "me_contained_in_vanilla")
        cls, _ = _pair(95, 5, 0.95, 0.05)
        self.assertEqual(cls, "me_contained_in_vanilla")

    def test_vanilla_contained_only_reverse_direction(self):
        cls, _ = _pair(40, 90, 0.40, 0.90)
        self.assertEqual(cls, "vanilla_contained_in_me")
        cls, _ = _pair(5, 95, 0.05, 0.95)
        self.assertEqual(cls, "vanilla_contained_in_me")

    def test_containment_uses_native_share_not_sampled(self):
        # The ladder consumes the NATIVE-denominator shares; a pair that is
        # contained on a sampled denominator but below 0.90 natively is not
        # a containment class.
        cls, _ = _pair(70, 70, 0.89, 0.89)
        self.assertNotIn(cls, {
            "mutual_contained", "me_contained_in_vanilla",
            "vanilla_contained_in_me"})

    def test_partial_overlap_at_threshold(self):
        cls, _ = _pair(20, 20, 0.20, 0.20)
        self.assertEqual(cls, "partial_overlap")
        cls, _ = _pair(50, 60, 0.50, 0.60)
        self.assertEqual(cls, "partial_overlap")
        cls, _ = _pair(89, 89, 0.89, 0.89)
        self.assertEqual(cls, "partial_overlap")

    def test_sliver_overlap_below_partial(self):
        cls, _ = _pair(10, 10, 0.19, 0.19)
        self.assertEqual(cls, "sliver_overlap")
        cls, _ = _pair(1, 1, 0.01, 0.01)
        self.assertEqual(cls, "sliver_overlap")

    def test_containment_beats_partial_overlap(self):
        # A >=0.90 share claims containment even when the other direction
        # is well above the partial floor.
        cls, _ = _pair(90, 80, 0.90, 0.80)
        self.assertEqual(cls, "me_contained_in_vanilla")
        cls, _ = _pair(80, 90, 0.80, 0.90)
        self.assertEqual(cls, "vanilla_contained_in_me")

    def test_asymmetric_partial_vs_sliver(self):
        # partial_overlap requires BOTH shares >= PARTIAL_SHARE.
        cls, _ = _pair(50, 10, 0.50, 0.19)
        self.assertEqual(cls, "sliver_overlap")
        cls, _ = _pair(10, 50, 0.19, 0.50)
        self.assertEqual(cls, "sliver_overlap")
        cls, _ = _pair(50, 10, 0.50, 0.20)
        self.assertEqual(cls, "partial_overlap")

    def test_forward_only_hit(self):
        cls, reasons = _pair(30, 0, 0.30, 0.0)
        self.assertEqual(cls, "sliver_overlap")
        self.assertIn("forward_only_hit", reasons)
        self.assertNotIn("no_bidirectional_hit", reasons)
        self.assertNotIn("reverse_only_hit", reasons)

    def test_reverse_only_hit(self):
        cls, reasons = _pair(0, 30, 0.0, 0.30)
        self.assertEqual(cls, "sliver_overlap")
        self.assertIn("reverse_only_hit", reasons)
        self.assertNotIn("no_bidirectional_hit", reasons)
        self.assertNotIn("forward_only_hit", reasons)

    def test_unsampled_side_treated_as_zero_share(self):
        # None shares on a hit-free side must not raise and must not
        # produce a phantom containment class.
        cls, _ = _pair(30, 0, 0.30, None)
        self.assertEqual(cls, "sliver_overlap")
        cls, _ = _pair(0, 30, None, 0.30)
        self.assertEqual(cls, "sliver_overlap")
        cls, _ = _pair(95, 0, 0.95, None)
        self.assertEqual(cls, "me_contained_in_vanilla")

    def test_reason_fixed_order(self):
        # Every emitted reason list follows the documented order.
        for hits in ((0, 0), (30, 0), (0, 30), (10, 10), (50, 50),
                     (90, 40), (40, 90), (90, 90)):
            me_hit, v_hit = hits
            _, reasons = _pair(me_hit, v_hit, 0.5, 0.5)
            self.assertEqual(
                reasons, sorted(reasons, key=REASON_ORDER.index),
                (hits, reasons))

    def test_reason_vocabulary(self):
        vocab = set()
        for me_hit, v_hit, me_s, v_s in (
                (0, 0, None, None), (30, 0, 0.3, 0.0), (0, 30, 0.0, 0.3),
                (10, 10, 0.1, 0.1), (50, 60, 0.5, 0.6), (90, 90, 0.9, 0.9),
                (95, 40, 0.95, 0.4), (40, 95, 0.4, 0.95)):
            _, reasons = _pair(me_hit, v_hit, me_s, v_s)
            vocab.update(reasons)
        self.assertLessEqual(
            vocab, set(REASON_ORDER),
            vocab - set(REASON_ORDER))

    def test_class_vocabulary(self):
        seen = {_pair(*args)[0] for args in (
            (0, 0, None, None), (30, 0, 0.3, 0.0), (10, 10, 0.1, 0.1),
            (50, 60, 0.5, 0.6), (90, 90, 0.9, 0.9), (95, 40, 0.95, 0.4),
            (40, 95, 0.4, 0.95), (10, 10, 0.19, 0.19))}
        self.assertEqual(seen, PAIR_CLASSES)

    def test_no_identity_claim_classes(self):
        # The class/claim vocabulary can never assert a proven subset.
        self.assertFalse(PAIR_CLASSES & FORBIDDEN_CLAIMS)
        self.assertFalse(CLAIMS & FORBIDDEN_CLAIMS)
        for hits in ((0, 0), (90, 90), (95, 40), (10, 10)):
            cls, _ = _pair(hits[0], hits[1], 0.95, 0.95)
            self.assertIn(cls, PAIR_CLASSES)

    def test_owner_never_enters_geometry(self):
        # _pair_class takes no owner argument: identical hits/shares give
        # identical results regardless of which side "owns" the states;
        # owner diagnostics (cross_owner/unknown_owner) are appended by the
        # caller, never emitted here.
        same = _pair(50, 60, 0.5, 0.6)
        cross = _pair(50, 60, 0.5, 0.6)
        self.assertEqual(same, cross)
        _, reasons = same
        self.assertNotIn("cross_owner", reasons)
        self.assertNotIn("unknown_owner", reasons)

    def test_boundary_values(self):
        just_below = 0.90 - 1e-9
        just_below_partial = 0.20 - 1e-9
        cls, _ = _pair(89, 89, just_below, just_below)
        self.assertEqual(cls, "partial_overlap")
        cls, _ = _pair(19, 19, just_below_partial, just_below_partial)
        self.assertEqual(cls, "sliver_overlap")


class EntityClassTests(unittest.TestCase):
    """`_entity_class(top_share, partner_count, usable)` -> entity_class."""

    def test_unmatched_without_partners(self):
        self.assertEqual(asm._entity_class(None, 0, True), "unmatched")
        self.assertEqual(asm._entity_class(None, 0, False), "unmatched")
        self.assertEqual(asm._entity_class(0.0, 0, True), "unmatched")

    def test_contained_requires_usable_and_top_share(self):
        self.assertEqual(asm._entity_class(0.90, 1, True), "contained")
        self.assertEqual(asm._entity_class(1.0, 1, True), "contained")

    def test_containment_beats_partner_count(self):
        # A contained mask with several partners is contained, not split.
        self.assertEqual(asm._entity_class(0.95, 3, True), "contained")

    def test_unusable_geometry_not_contained(self):
        self.assertNotEqual(
            asm._entity_class(0.95, 1, False), "contained")

    def test_split_with_multiple_partners_below_containment(self):
        self.assertEqual(asm._entity_class(0.50, 2, True), "split")
        self.assertEqual(asm._entity_class(0.89, 5, True), "split")

    def test_ambiguous_single_partner_below_containment(self):
        self.assertEqual(asm._entity_class(0.50, 1, True), "ambiguous")
        self.assertEqual(asm._entity_class(0.89, 1, True), "ambiguous")

    def test_class_vocabulary(self):
        seen = {
            asm._entity_class(None, 0, True),
            asm._entity_class(0.95, 1, True),
            asm._entity_class(0.5, 2, True),
            asm._entity_class(0.5, 1, True),
        }
        self.assertEqual(seen, ENTITY_CLASSES)
        self.assertFalse(ENTITY_CLASSES & FORBIDDEN_CLAIMS)


class SchemaTests(unittest.TestCase):
    """Output schemas pinned as literal constants (regression anchors for
    the deterministic column layout; not parsed from source text)."""

    def test_columns(self):
        self.assertEqual(asm.COLUMNS, [
            "vanilla_id", "vanilla_name", "vanilla_owner",
            "me_id", "me_name", "me_owner",
            "owner_relation", "country_scope",
            "me_land_px", "me_outside_px", "me_water_px",
            "me_unassigned_px", "me_assigned_px", "me_hit_px",
            "me_share", "me_registered_share", "me_spill_px",
            "me_rank", "me_modal_vanilla", "me_modal_share",
            "me_modal_margin",
            "v_native_px", "v_sampled_px", "v_unreached_px",
            "v_hit_px", "v_share_native", "v_share_sampled",
            "pair_class", "pair_reason", "pair_claim",
            "me_components", "me_largest_share", "me_largest_hit_share",
            "components_agree", "geometry_usable",
            "source_scope", "edge_source", "evidence_families",
            "is_canonical", "canonical_ancestor", "edge_class",
            "comment_votes", "manual_kind", "consistency",
        ])

    def test_columns_grain_and_groups(self):
        cols = asm.COLUMNS
        # Pair grain key fields present once, ids first.
        self.assertEqual(cols.index("me_id") < cols.index("me_land_px"), True)
        self.assertEqual(cols.index("vanilla_id"), 0)
        # Two native frames stay separate column families, never merged:
        # forward me_* shares and reverse v_* shares are distinct columns.
        for name in ("me_share", "me_registered_share",
                     "v_share_native", "v_share_sampled"):
            self.assertEqual(cols.count(name), 1)
        # Owner/country fields exist only as parallel filter/context
        # columns — before the geometry block, never inside the shares.
        self.assertLess(cols.index("owner_relation"), cols.index("me_share"))
        self.assertLess(cols.index("country_scope"), cols.index("me_share"))
        # Ledger context is a verbatim-join block at the tail.
        tail = cols[-9:]
        self.assertEqual(tail, [
            "source_scope", "edge_source", "evidence_families",
            "is_canonical", "canonical_ancestor", "edge_class",
            "comment_votes", "manual_kind", "consistency"])
        self.assertEqual(len(cols), len(set(cols)))

    def test_entity_columns(self):
        self.assertEqual(asm.ENTITY_COLUMNS, [
            "entity_side", "entity_id", "entity_name", "entity_owner",
            "land_px", "assigned_px", "assigned_share",
            "geometry_usable", "partner_count",
            "top_partner", "top_partner_px", "top_share", "runner_share",
            "entity_class", "entity_reason",
            "same_owner_partner_count", "cross_owner_partner_count",
        ])

    def test_entity_columns_grain(self):
        cols = asm.ENTITY_COLUMNS
        self.assertEqual(cols[:2], ["entity_side", "entity_id"])
        self.assertEqual(len(cols), len(set(cols)))

    def test_summary_schema_literal(self):
        self.assertEqual(
            asm.SUMMARY_SCHEMA, "state_mask_audit_1936_summary/v1")

    def test_claim_vocabulary(self):
        # pair_claim is derived per row (me_hit or v_hit positive ->
        # sampled_masks, else withheld); the vocabulary is pinned here,
        # the no-hit class above covers the withheld row shape.
        self.assertEqual(CLAIMS, {"sampled_masks", "withheld"})


if __name__ == "__main__":
    unittest.main()
