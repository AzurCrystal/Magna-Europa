#!/usr/bin/env python
"""Deterministic tests for the optional state-review geography diagnostics."""

from __future__ import annotations
import csv

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_state_mapping_review import (  # noqa: E402
    CONFIRMATION_COLUMNS,
    CONFIRMATION_QUEUE_COLUMNS,
    load_confirmation,
    load_metadata,
)
from build_state_mapping_confirmation import (  # noqa: E402
    CONFIRMATION_THRESHOLD,
    SUMMARY_SCHEMA,
)
from state_geography_evidence import (  # noqa: E402
    OVERLAP_COLUMNS,
    OVERLAP_DOMAIN,
    OVERLAP_MARKER,
    OVERLAP_METHOD,
)


V2M_COLUMNS = (
    "vanilla_id",
    "vanilla_name",
    "owner",
    "v_prov_count",
    "region",
    "me_primary",
    "me_members",
    "me_names",
    "basis",
    "status",
)
M2V_COLUMNS = (
    "me_id",
    "me_name",
    "owner",
    "prov_count",
    "region",
    "cat",
    "manpower",
    "v_ancestor",
    "v_name",
    "basis",
    "status",
)
GEOGRAPHY_COLUMNS = (
    "me_id",
    "me_name",
    "natural_ok",
    "natural_method",
    "natural_pairs",
    "natural_residual_p50",
    "natural_residual_p95",
    "natural_best",
    "natural_share",
    "natural_runner",
    "natural_runner_share",
    "natural_margin",
    "raw_geography_ancestor",
    "raw_geography_marker",
    "natural_reason",
    "natural_sampled_share",
    "natural_center_x",
    "natural_center_y",
    "natural_radius",
    "natural_river_pairs",
    "natural_lake_pairs",
)
GEOGRAPHY_WITH_OVERLAP_COLUMNS = GEOGRAPHY_COLUMNS + tuple(OVERLAP_COLUMNS)


class StateMappingReviewTests(unittest.TestCase):
    """Use synthetic canonical tables; no map assets or generated review output."""

    @staticmethod
    def _write_tsv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    @classmethod
    def _canonical_tables(cls, root: Path) -> None:
        cls._write_tsv(
            root / "v2m_states.tsv",
            V2M_COLUMNS,
            [
                {
                    "vanilla_id": "1",
                    "vanilla_name": "Vanilla One",
                    "owner": "AAA",
                    "v_prov_count": "1",
                    "region": "1",
                    "me_primary": "10",
                    "me_members": "10|11",
                    "me_names": "ME Ten|ME Eleven",
                    "basis": "canonical-basis",
                    "status": "mapped",
                },
                {
                    "vanilla_id": "2",
                    "vanilla_name": "Vanilla Two",
                    "owner": "BBB",
                    "v_prov_count": "1",
                    "region": "2",
                    "me_primary": "",
                    "me_members": "",
                    "me_names": "",
                    "basis": "not-in-table",
                    "status": "unmapped",
                },
            ],
        )
        cls._write_tsv(
            root / "m2v_states.tsv",
            M2V_COLUMNS,
            [
                {
                    "me_id": "10",
                    "me_name": "ME Ten",
                    "owner": "AAA",
                    "prov_count": "1",
                    "region": "1",
                    "cat": "rural",
                    "manpower": "1",
                    "v_ancestor": "1",
                    "v_name": "Vanilla One",
                    "basis": "canonical-me-basis",
                    "status": "mapped",
                },
                {
                    "me_id": "11",
                    "me_name": "ME Eleven",
                    "owner": "AAA",
                    "prov_count": "1",
                    "region": "1",
                    "cat": "rural",
                    "manpower": "1",
                    "v_ancestor": "1",
                    "v_name": "Vanilla One",
                    "basis": "canonical-me-basis-2",
                    "status": "mapped",
                },
            ],
        )

    @staticmethod
    def _accepted_row(me_id: int = 10) -> dict[str, str]:
        row = {field: "" for field in GEOGRAPHY_COLUMNS}
        row.update(
            {
                "me_id": str(me_id),
                "me_name": "ME Ten" if me_id == 10 else "ME Eleven",
                "natural_ok": "1",
                "natural_method": "translation",
                "natural_pairs": "10",
                "natural_residual_p50": "1.5",
                "natural_residual_p95": "3.5",
                "natural_best": "1",
                "natural_share": "0.9",
                "natural_runner": "2",
                "natural_runner_share": "0.05",
                "natural_margin": "0.85",
                "raw_geography_ancestor": "1",
                "raw_geography_marker": "coast_bbox_icp;local_river_lake_translation;river_pairs=6;lake_pairs=4",
                "natural_reason": "accepted",
                "natural_sampled_share": "0.97",
                "natural_center_x": "10.25",
                "natural_center_y": "20.5",
                "natural_radius": "30",
                "natural_river_pairs": "6",
                "natural_lake_pairs": "4",
            }
        )
        return row

    @staticmethod
    def _failed_row(me_id: int = 11) -> dict[str, str]:
        row = {field: "" for field in GEOGRAPHY_COLUMNS}
        row.update(
            {
                "me_id": str(me_id),
                "me_name": "ME Eleven",
                "natural_ok": "0",
                "natural_method": "",
                "natural_pairs": "0",
                "natural_reason": "pair_count_mismatch",
                # A failed producer row preserves nonnegative diagnostics even
                # when they do not describe a successful pair total.
                "natural_runner_share": "0",
                "natural_river_pairs": "2",
                "natural_lake_pairs": "5",
            }
        )
        return row

    @classmethod
    def _accepted_overlap_row(cls, me_id: int = 10) -> dict[str, str]:
        row = cls._accepted_row(me_id)
        row.update(
            {
                "overlap_ok": "1",
                "overlap_method": OVERLAP_METHOD,
                "overlap_domain": OVERLAP_DOMAIN,
                "overlap_area_px": "1000",
                "overlap_best": "2",
                "overlap_share": "0.9",
                "overlap_runner": "1",
                "overlap_runner_share": "0.05",
                "overlap_margin": "0.85",
                "overlap_assigned_share": "0.95",
                "overlap_containment": "0.6",
                "overlap_components": "2",
                "overlap_largest_share": "0.75",
                "overlap_largest_best": "2",
                "overlap_largest_candidate_share": "0.92",
                "overlap_components_agree": "1",
                "overlap_unregistered_share": "0.05",
                "overlap_ancestor": "2",
                "overlap_marker": OVERLAP_MARKER,
                "overlap_reason": "accepted",
            }
        )
        return row

    @classmethod
    def _withheld_overlap_row(cls, me_id: int = 11) -> dict[str, str]:
        row = cls._failed_row(me_id)
        accepted = cls._accepted_overlap_row(me_id)
        row.update({field: accepted[field] for field in OVERLAP_COLUMNS})
        row.update(
            {
                "overlap_ok": "0",
                "overlap_share": "0.55",
                "overlap_runner_share": "0.4",
                "overlap_margin": "0.15",
                "overlap_containment": "0.4",
                "overlap_largest_share": "0.6",
                "overlap_largest_candidate_share": "0.85",
                "overlap_components_agree": "0",
                "overlap_ancestor": "",
                "overlap_marker": "",
                "overlap_reason": "component_disagreement",
            }
        )
        return row

    @classmethod
    def _empty_overlap_row(cls, me_id: int = 11, *, area: int = 1000) -> dict[str, str]:
        row = cls._failed_row(me_id)
        row.update(dict.fromkeys(OVERLAP_COLUMNS, ""))
        row.update(
            {
                "overlap_ok": "0",
                "overlap_method": OVERLAP_METHOD,
                "overlap_domain": OVERLAP_DOMAIN,
                "overlap_area_px": str(area),
                "overlap_share": "0",
                "overlap_runner_share": "0",
                "overlap_margin": "0",
                "overlap_assigned_share": "0",
                "overlap_containment": "0",
                "overlap_components": "1" if area else "0",
                "overlap_largest_share": "1" if area else "0",
                "overlap_largest_candidate_share": "0",
                "overlap_components_agree": "0",
                "overlap_unregistered_share": "1" if area else "0",
                "overlap_reason": "low_assigned_share" if area else "insufficient_area",
            }
        )
        return row

    @classmethod
    def _blank_overlap_row(cls, me_id: int = 11) -> dict[str, str]:
        row = cls._failed_row(me_id)
        row.update(dict.fromkeys(OVERLAP_COLUMNS, ""))
        return row

    @classmethod
    def _write_geography(
        cls,
        root: Path,
        rows: list[dict[str, str]],
        columns: tuple[str, ...] = GEOGRAPHY_COLUMNS,
    ) -> None:
        with (root / "geography_evidence.tsv").open("w", encoding="utf-8", newline="") as handle:
            handle.write("\t".join(columns) + "\n")
            for row in rows:
                handle.write("\t".join(row.get(field, "") for field in columns) + "\n")

    @classmethod
    def _load_with_geography(
        cls,
        root: Path,
        rows: list[dict[str, str]],
        columns: tuple[str, ...] = GEOGRAPHY_COLUMNS,
    ):
        cls._write_geography(root, rows, columns)
        return load_metadata(root, {1: "Vanilla One", 2: "Vanilla Two"}, {10: "ME Ten", 11: "ME Eleven"})

    @staticmethod
    def _confirmation_row(
        me_id: int = 10,
        vanilla_id: int = 1,
        *,
        status: str = "review_candidate",
        kind: str = "canonical",
        gate: str = "fail",
        score: str = "0.5",
        blank_metrics: bool = False,
        review_priority: str | None = None,
    ) -> dict[str, str]:
        row = {field: "" for field in CONFIRMATION_COLUMNS | {"review_priority"}}
        row.update(
            {
                "me_id": str(me_id),
                "me_name": f"ME {me_id}",
                "me_owner": "AAA",
                "vanilla_id": str(vanilla_id),
                "vanilla_name": f"Vanilla {vanilla_id}",
                "vanilla_owner": "AAA",
                "relation_kind": kind,
                "relation_status": status,
                "source_scope": "compatibility",
                "edge_source": "candidate|canonical",
                "evidence_families": "canonical|pixel",
                "is_canonical": "1" if kind == "canonical" else "0",
                "canonical_basis": "pixel-geo",
                "owner_relation": "same_owner",
                "country_scope": "within_country",
                "edge_class": "significant",
                "me_share": "0.7",
                "me_registered_share": "0.7",
                "me_geometry_usable": "1",
                "v_hit_px": "3",
                "v_native_px": "10",
                "v_share_native": "0.3",
                "me_owner_area_px": "10",
                "me_owner_match_px": "8",
                "me_owner_share": "0.8",
                "v_owner_area_px": "10",
                "v_owner_match_px": "3",
                "v_owner_share": "0.3",
                "family_member_count": "1",
                "family_me_ids": str(me_id),
                "family_live_member_count": "1",
                "family_has_synthetic_member": "0",
                "family_min_me_share": "0.7",
                "family_reverse_hit_px": "3",
                "family_reverse_native_px": "10",
                "family_reverse_share": "0.3",
                "owner_family_member_count": "1",
                "owner_family_me_ids": str(me_id),
                "owner_family_live_member_count": "1",
                "owner_family_has_synthetic_member": "0",
                "owner_family_min_me_owner_share": "0.8",
                "owner_family_reverse_match_px": "3",
                "owner_family_reverse_share": "0.3",
                "mapping_audit_class": "review_required",
                "mapping_audit_reason_codes": "overlap-weak",
                "comment_votes": "",
                "explicit_identity_evidence": "",
                "evidence_cap": "0.8",
                "confidence_score": score,
                "confidence_gate": gate,
                "confidence_reasons": "me_owner_share_below_95",
                "project_ref_count": "0",
                "project_review_ref_count": "0",
            }
        )
        if blank_metrics:
            for field in (
                "me_share", "me_registered_share", "v_share_native",
                "v_hit_px", "v_native_px", "family_min_me_share",
                "family_reverse_hit_px", "family_reverse_native_px",
                "family_reverse_share",
                "me_owner_area_px", "me_owner_match_px", "me_owner_share",
                "v_owner_area_px", "v_owner_match_px", "v_owner_share",
                "owner_family_min_me_owner_share",
                "owner_family_reverse_match_px", "owner_family_reverse_share",
            ):
                row[field] = ""
        if review_priority is not None:
            row["review_priority"] = review_priority
        return row

    @staticmethod
    def _input_record(path: Path) -> dict:
        data = path.read_bytes()
        return {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "rows": max(data.count(b"\n") - 1, 0),
        }

    @staticmethod
    def _summary(pairs_total: int, queue_rows: int, inputs=None) -> dict:
        return {
            "schema": SUMMARY_SCHEMA,
            "threshold_contract": {"confirmation_threshold": CONFIRMATION_THRESHOLD},
            "counts": {
                "pairs_total": pairs_total,
                "review_queue_rows": queue_rows,
            },
            "inputs": {} if inputs is None else inputs,
        }

    def _edges_summary(self, root: Path, pairs_total: int, queue_rows: int):
        """Write a live state_edges_1936.tsv and return the matching summary."""
        edges = root / "state_edges_1936.tsv"
        edges.write_text("vanilla_id\tme_id\n1\t10\n", encoding="utf-8")
        return self._summary(
            pairs_total, queue_rows,
            inputs={"state_edges_1936": self._input_record(edges)})

    @classmethod
    def _write_confirmation(
        cls,
        root: Path,
        *,
        main_rows: list[dict[str, str]],
        queue_rows: list[dict[str, str]],
        summary=None,
        queue_name: str = "state_mapping_review_queue_95.tsv",
    ) -> tuple[Path, Path, Path]:
        main = root / "state_mapping_confirmation.tsv"
        queue = root / queue_name
        summary_path = root / "state_mapping_confirmation_summary.json"
        cls._write_tsv(
            main,
            sorted(CONFIRMATION_COLUMNS),
            [{key: value for key, value in row.items() if key != "review_priority"}
             for row in main_rows],
        )
        cls._write_tsv(queue, sorted(CONFIRMATION_QUEUE_COLUMNS), queue_rows)
        if summary is not None:
            if isinstance(summary, str):
                summary_path.write_text(summary, encoding="utf-8")
            else:
                summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return main, queue, summary_path

    @staticmethod
    def _load_confirmation(main, summary_path, warnings, queue_path=None):
        return load_confirmation(
            main,
            summary_path,
            {1: {"name": "Vanilla One"}, 2: {"name": "Vanilla Two"}},
            {10: {"name": "ME Ten"}, 11: {"name": "ME Eleven"}, 12: {"name": "ME Twelve"}},
            warnings,
            queue_path=queue_path,
        )

    def test_confirmation_blank_mask_metrics_parse_as_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row(blank_metrics=True)],
                queue_rows=[self._confirmation_row(review_priority="1")],
                summary=self._edges_summary(root, 1, 1),
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)

        self.assertEqual(warnings, [])
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay["available"])
        self.assertEqual(len(overlay["rows"]), 1)
        row = overlay["rows"][0]
        for field in (
            "me_share", "me_registered_share", "v_share_native",
            "v_hit_px", "v_native_px", "family_min_me_share",
            "family_reverse_hit_px", "family_reverse_native_px",
            "family_reverse_share",
            "me_owner_area_px", "me_owner_match_px", "me_owner_share",
            "v_owner_area_px", "v_owner_match_px", "v_owner_share",
            "owner_family_min_me_owner_share",
            "owner_family_reverse_match_px", "owner_family_reverse_share",
        ):
            self.assertIsNone(row[field], field)
        self.assertEqual(row["owner_family_member_count"], 1)
        self.assertEqual(row["owner_family_live_member_count"], 1)
        self.assertFalse(row["owner_family_has_synthetic_member"])
        self.assertEqual(row["confidence_score"], 0.5)
        self.assertEqual(row["review_priority"], 1)
        self.assertEqual(overlay["counts"]["review_candidate"], 1)

    def test_confirmation_blank_required_score_discards_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row(score="")],
                queue_rows=[self._confirmation_row(review_priority="0")],
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)

        self.assertIsNone(overlay)
        self.assertEqual(len(warnings), 1)
        self.assertIn("confirmation overlay ignored", warnings[0])

    def test_confirmation_malformed_summary_types_warn_and_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row()],
                queue_rows=[self._confirmation_row(review_priority="0")],
            )
            for bad in ("null", "[]", '"text"', "7"):
                summary_path.write_text(bad, encoding="utf-8")
                warnings = []
                overlay = self._load_confirmation(main, summary_path, warnings)
                self.assertIsNone(overlay, bad)
                self.assertEqual(len(warnings), 1, bad)
                self.assertIn("confirmation overlay ignored", warnings[0])
            summary_path.write_text(
                json.dumps({"schema": SUMMARY_SCHEMA, "threshold_contract": [], "counts": {}}),
                encoding="utf-8",
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)
            self.assertIsNone(overlay)
            self.assertIn("confirmation overlay ignored", warnings[0])

    def test_confirmation_stale_summary_input_discards_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = self._edges_summary(root, 1, 1)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row()],
                queue_rows=[self._confirmation_row(review_priority="0")],
                summary=summary,
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)
            self.assertEqual(warnings, [])
            self.assertIsNotNone(overlay)
            self.assertTrue(overlay["input_verified"])
            self.assertEqual(overlay["schema"], SUMMARY_SCHEMA)
            self.assertEqual(overlay["sources"]["edges"], str(root / "state_edges_1936.tsv"))
            self.assertEqual(
                overlay["counts"]["input_sha256"]["state_edges_1936"],
                summary["inputs"]["state_edges_1936"]["sha256"])

            # Any input that changed on disk since the summary was written
            # marks the whole embedded overlay stale — never shown as fresh.
            (root / "state_edges_1936.tsv").write_text(
                "vanilla_id\tme_id\n1\t10\n2\t11\n", encoding="utf-8")
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertEqual(len(warnings), 1)
            self.assertIn("stale", warnings[0])
            self.assertIn("state_edges_1936", warnings[0])

            # An input that vanished is equally stale.
            (root / "state_edges_1936.tsv").unlink()
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertIn("stale", warnings[0])

    def test_confirmation_summary_requires_edges_input_and_record_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row()],
                queue_rows=[self._confirmation_row(review_priority="0")],
                summary=self._summary(1, 1),
            )
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertIn("confirmation overlay ignored", warnings[0])

            edges = root / "state_edges_1936.tsv"
            edges.write_text("vanilla_id\tme_id\n", encoding="utf-8")
            for record in (
                {"path": str(edges)},
                {"sha256": hashlib.sha256(edges.read_bytes()).hexdigest()},
                "not-a-record",
            ):
                summary_path.write_text(
                    json.dumps(self._summary(
                        1, 1, inputs={"state_edges_1936": record})),
                    encoding="utf-8")
                warnings = []
                self.assertIsNone(
                    self._load_confirmation(main, summary_path, warnings), record)
                self.assertIn("confirmation overlay ignored", warnings[0])

    def test_confirmation_owner_family_integrity_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for mutate in (
                lambda row: row.update(owner_family_member_count="2"),
                lambda row: row.update(owner_family_live_member_count="2"),
                lambda row: row.update(owner_family_me_ids=f"{10}|{10}"),
                lambda row: row.update(owner_family_has_synthetic_member="x"),
            ):
                row = self._confirmation_row()
                mutate(row)
                main, _, summary_path = self._write_confirmation(
                    root,
                    main_rows=[row],
                    queue_rows=[self._confirmation_row(review_priority="0")],
                )
                warnings = []
                self.assertIsNone(
                    self._load_confirmation(main, summary_path, warnings))
                self.assertIn("confirmation overlay ignored", warnings[0])

    def test_confirmation_summary_absent_reports_unverified_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row()],
                queue_rows=[self._confirmation_row(review_priority="0")],
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)

        self.assertIsNotNone(overlay)
        self.assertFalse(overlay["input_verified"])
        self.assertIsNone(overlay["schema"])
        self.assertTrue(any("summary" in item for item in warnings))

    def test_confirmation_queue_defaults_beside_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            main, _, summary_path = self._write_confirmation(
                snapshot,
                main_rows=[
                    self._confirmation_row(10, 1),
                    self._confirmation_row(
                        11, 1, status="confirmed_95", gate="pass", score="0.96"),
                ],
                queue_rows=[self._confirmation_row(10, 1, review_priority="2")],
                summary=self._edges_summary(snapshot, 2, 1),
            )
            warnings = []
            overlay = self._load_confirmation(main, summary_path, warnings)

        self.assertEqual(warnings, [])
        self.assertIsNotNone(overlay)
        self.assertEqual(overlay["sources"]["queue"], str(snapshot / "state_mapping_review_queue_95.tsv"))
        priorities = {
            (row["me_id"], row["vanilla_id"]): row.get("review_priority")
            for row in overlay["rows"]
        }
        self.assertEqual(priorities[(10, 1)], 2)
        self.assertIsNone(priorities[(11, 1)])

    def test_confirmation_queue_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            main, _, summary_path = self._write_confirmation(
                snapshot,
                main_rows=[self._confirmation_row(10, 1)],
                queue_rows=[self._confirmation_row(10, 1, review_priority="0")],
                queue_name="other_queue.tsv",
            )
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            override = root / "custom_queue.tsv"
            self._write_tsv(
                override,
                sorted(CONFIRMATION_QUEUE_COLUMNS),
                [self._confirmation_row(10, 1, review_priority="3")],
            )
            warnings = []
            overlay = self._load_confirmation(
                main, summary_path, warnings, queue_path=override)

        self.assertFalse(any("confirmation overlay ignored" in item for item in warnings), warnings)
        self.assertIsNotNone(overlay)
        self.assertEqual(overlay["sources"]["queue"], str(override))
        self.assertEqual(overlay["rows"][0]["review_priority"], 3)

    def test_confirmation_queue_containment_both_directions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # A main review_candidate absent from the queue discards the overlay.
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[
                    self._confirmation_row(10, 1),
                    self._confirmation_row(11, 2),
                ],
                queue_rows=[self._confirmation_row(10, 1, review_priority="0")],
            )
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertIn("missing from the review queue", warnings[0])

            # A queue row absent from main still discards the overlay.
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row(10, 1)],
                queue_rows=[
                    self._confirmation_row(10, 1, review_priority="0"),
                    self._confirmation_row(11, 2, review_priority="1"),
                ],
            )
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertIn("absent from the main confirmation TSV", warnings[0])

            # A queue row may not disagree with the main status.
            main, _, summary_path = self._write_confirmation(
                root,
                main_rows=[self._confirmation_row(
                    10, 1, status="confirmed_95", gate="pass", score="0.96")],
                queue_rows=[self._confirmation_row(10, 1, review_priority="0")],
            )
            warnings = []
            self.assertIsNone(self._load_confirmation(main, summary_path, warnings))
            self.assertIn("disagrees with the main confirmation status", warnings[0])

    def test_confirmation_queue_cli_flag(self):
        completed = subprocess.run(
            [sys.executable, str(TOOLS / "build_state_mapping_review.py"), "--help"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--confirmation-queue", completed.stdout)
        self.assertIn("beside the selected confirmation", completed.stdout)

    def test_missing_geography_is_legacy_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._canonical_tables(root)

            vanilla, me, warnings = load_metadata(
                root,
                {1: "Vanilla One", 2: "Vanilla Two"},
                {10: "ME Ten", 11: "ME Eleven"},
            )

        self.assertEqual(warnings, [])
        self.assertNotIn("geography", vanilla[1])
        self.assertNotIn("geography", me[10])
        self.assertEqual(me[10]["ancestor"], 1)
        self.assertEqual(vanilla[1]["members"], [10, 11])

    def test_valid_accepted_failed_and_unknown_me_rows_are_typed_and_non_mutating(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._canonical_tables(root)
            accepted = self._accepted_row(10)
            failed = self._failed_row(11)
            unknown = self._accepted_row(999)
            unknown["me_name"] = "Unknown positive ME state"
            vanilla, me, warnings = self._load_with_geography(root, [accepted, failed, unknown])

        self.assertEqual(warnings, [])
        self.assertNotIn(999, me)

        accepted_geo = me[10]["geography"]
        self.assertIs(accepted_geo["natural_ok"], True)
        self.assertIsInstance(accepted_geo["natural_pairs"], int)
        self.assertIsInstance(accepted_geo["natural_residual_p50"], float)
        self.assertIsInstance(accepted_geo["natural_best"], int)
        self.assertIsInstance(accepted_geo["natural_runner"], int)
        self.assertIsInstance(accepted_geo["natural_share"], float)
        self.assertIsInstance(accepted_geo["natural_runner_share"], float)
        self.assertIsInstance(accepted_geo["natural_margin"], float)
        self.assertIsInstance(accepted_geo["natural_sampled_share"], float)
        self.assertIsInstance(accepted_geo["natural_center_x"], float)
        self.assertIsInstance(accepted_geo["natural_radius"], float)
        self.assertIsInstance(accepted_geo["natural_river_pairs"], int)
        self.assertEqual(accepted_geo["natural_river_pairs"] + accepted_geo["natural_lake_pairs"], 10)
        self.assertEqual(accepted_geo["raw_geography_ancestor"], 1)

        failed_geo = me[11]["geography"]
        self.assertIs(failed_geo["natural_ok"], False)
        self.assertEqual(failed_geo["natural_method"], "")
        self.assertEqual(failed_geo["natural_pairs"], 0)
        self.assertIsNone(failed_geo["natural_residual_p50"])
        self.assertIsNone(failed_geo["natural_best"])
        self.assertIsNone(failed_geo["natural_share"])
        self.assertEqual(failed_geo["natural_runner_share"], 0.0)
        self.assertEqual(failed_geo["natural_river_pairs"], 2)
        self.assertEqual(failed_geo["natural_lake_pairs"], 5)
        self.assertIsNone(failed_geo["raw_geography_ancestor"])

        # Geography is diagnostic-only: canonical names, relation, basis, and
        # status are still exactly the values supplied by the canonical TSVs.
        self.assertEqual(vanilla[1]["name"], "Vanilla One")
        self.assertEqual(vanilla[1]["basis"], "canonical-basis")
        self.assertEqual(vanilla[1]["status"], "mapped")
        self.assertEqual(vanilla[1]["members"], [10, 11])
        self.assertEqual(me[10]["name"], "ME Ten")
        self.assertEqual(me[10]["ancestor"], 1)
        self.assertEqual(me[10]["basis"], "canonical-me-basis")
        self.assertEqual(me[10]["status"], "mapped")

    def test_valid_overlap_fields_are_plain_typed_and_leave_natural_and_canonical_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._canonical_tables(root)
            legacy_vanilla, legacy_me, legacy_warnings = self._load_with_geography(
                root, [self._accepted_row(10), self._failed_row(11)]
            )
            vanilla, me, warnings = self._load_with_geography(
                root,
                [self._accepted_overlap_row(10), self._withheld_overlap_row(11), self._accepted_overlap_row(999)],
                GEOGRAPHY_WITH_OVERLAP_COLUMNS,
            )

        accepted = {
            "overlap_ok": True,
            "overlap_method": OVERLAP_METHOD,
            "overlap_domain": OVERLAP_DOMAIN,
            "overlap_area_px": 1000,
            "overlap_best": 2,
            "overlap_share": 0.9,
            "overlap_runner": 1,
            "overlap_runner_share": 0.05,
            "overlap_margin": 0.85,
            "overlap_assigned_share": 0.95,
            "overlap_containment": 0.6,
            "overlap_components": 2,
            "overlap_largest_share": 0.75,
            "overlap_largest_best": 2,
            "overlap_largest_candidate_share": 0.92,
            "overlap_components_agree": True,
            "overlap_unregistered_share": 0.05,
            "overlap_ancestor": 2,
            "overlap_marker": OVERLAP_MARKER,
            "overlap_reason": "accepted",
        }
        withheld = dict(accepted)
        withheld.update(
            overlap_ok=False,
            overlap_share=0.55,
            overlap_runner_share=0.4,
            overlap_margin=0.15,
            overlap_containment=0.4,
            overlap_largest_share=0.6,
            overlap_largest_candidate_share=0.85,
            overlap_components_agree=False,
            overlap_ancestor=None,
            overlap_marker="",
            overlap_reason="component_disagreement",
        )
        self.assertEqual(legacy_warnings, [])
        self.assertEqual(warnings, [])
        self.assertEqual(vanilla, legacy_vanilla)
        self.assertNotIn(999, me)
        for state_id, expected in ((10, accepted), (11, withheld)):
            with self.subTest(me_id=state_id):
                geography = me[state_id]["geography"]
                self.assertEqual({field: geography[field] for field in OVERLAP_COLUMNS}, expected)
                for field, value in expected.items():
                    self.assertIs(type(geography[field]), type(value), field)
                self.assertEqual(set(geography), set(legacy_me[state_id]["geography"]) | set(OVERLAP_COLUMNS))
                self.assertEqual(
                    {field: value for field, value in geography.items() if not field.startswith("overlap_")},
                    legacy_me[state_id]["geography"],
                )
                self.assertEqual(
                    {field: value for field, value in me[state_id].items() if field != "geography"},
                    {field: value for field, value in legacy_me[state_id].items() if field != "geography"},
                )
                self.assertFalse(any(field.startswith("overlap_") for field in me[state_id]))
        self.assertEqual(me[10]["ancestor"], 1)
        self.assertEqual(me[10]["geography"]["natural_best"], 1)
        self.assertEqual(me[10]["geography"]["overlap_best"], 2)
        self.assertEqual(me[10]["geography"]["overlap_ancestor"], 2)
        self.assertEqual(vanilla[1]["members"], [10, 11])

    def test_entirely_blank_overlap_extension_preserves_legacy_natural_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._canonical_tables(root)
            legacy_vanilla, legacy_me, legacy_warnings = self._load_with_geography(
                root, [self._accepted_row(10), self._failed_row(11)]
            )
            vanilla, me, warnings = self._load_with_geography(
                root,
                [self._accepted_overlap_row(10), self._blank_overlap_row(11)],
                GEOGRAPHY_WITH_OVERLAP_COLUMNS,
            )

        self.assertEqual(legacy_warnings, [])
        self.assertEqual(warnings, [])
        self.assertEqual(vanilla, legacy_vanilla)
        self.assertEqual(me[11], legacy_me[11])
        self.assertFalse(any(field.startswith("overlap_") for field in me[11]["geography"]))
        self.assertIs(me[10]["geography"]["overlap_ok"], True)

    def test_absent_overlap_runner_accepts_producer_zero_or_blank_share(self):
        for runner_share in ("0", ""):
            with self.subTest(runner_share=runner_share), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._canonical_tables(root)
                row = self._accepted_overlap_row()
                row.update(
                    overlap_runner="", overlap_runner_share=runner_share, overlap_share="1",
                    overlap_margin="1", overlap_assigned_share="1", overlap_unregistered_share="0",
                    overlap_largest_candidate_share="1",
                )
                vanilla, me, warnings = self._load_with_geography(root, [row], GEOGRAPHY_WITH_OVERLAP_COLUMNS)

                self.assertEqual(warnings, [])
                geography = me[10]["geography"]
                self.assertIs(geography["overlap_ok"], True)
                self.assertIsNone(geography["overlap_runner"])
                if runner_share:
                    self.assertEqual(geography["overlap_runner_share"], 0.0)
                    self.assertIsInstance(geography["overlap_runner_share"], float)
                else:
                    self.assertIsNone(geography["overlap_runner_share"])
                self.assertEqual(geography["overlap_margin"], 1.0)
                self.assertEqual(geography["natural_runner"], 2)
                self.assertEqual(me[10]["ancestor"], 1)
                self.assertEqual(vanilla[1]["members"], [10, 11])

    def test_no_candidate_overlap_accepts_zero_and_unavailable_diagnostics(self):
        optional_metrics = (
            "overlap_share", "overlap_runner_share", "overlap_margin", "overlap_containment",
            "overlap_largest_candidate_share",
        )
        for area in (0, 1000):
            for unavailable in (False, True):
                with self.subTest(area=area, unavailable=unavailable), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self._canonical_tables(root)
                    row = self._empty_overlap_row(area=area)
                    if unavailable:
                        row.update(dict.fromkeys(optional_metrics, ""))
                        if not area:
                            row.update(dict.fromkeys(
                                ("overlap_assigned_share", "overlap_unregistered_share", "overlap_largest_share"), ""
                            ))
                    vanilla, me, warnings = self._load_with_geography(root, [row], GEOGRAPHY_WITH_OVERLAP_COLUMNS)

                    self.assertEqual(warnings, [])
                    geography = me[11]["geography"]
                    self.assertEqual(set(OVERLAP_COLUMNS) - set(geography), set())
                    self.assertIs(geography["overlap_ok"], False)
                    self.assertIs(geography["overlap_components_agree"], False)
                    self.assertEqual(geography["overlap_area_px"], area)
                    self.assertEqual(geography["overlap_components"], 1 if area else 0)
                    for field in ("overlap_best", "overlap_runner", "overlap_largest_best", "overlap_ancestor"):
                        self.assertIsNone(geography[field], field)
                    for field in optional_metrics + (
                        "overlap_assigned_share", "overlap_unregistered_share", "overlap_largest_share",
                    ):
                        if row[field]:
                            self.assertEqual(geography[field], float(row[field]), field)
                            self.assertIsInstance(geography[field], float, field)
                        else:
                            self.assertIsNone(geography[field], field)
                    self.assertEqual(geography["overlap_marker"], "")
                    self.assertEqual(geography["overlap_reason"], "low_assigned_share" if area else "insufficient_area")
                    self.assertIs(geography["natural_ok"], False)
                    self.assertEqual(geography["natural_reason"], "pair_count_mismatch")
                    self.assertEqual(me[11]["ancestor"], 1)
                    self.assertEqual(vanilla[1]["members"], [10, 11])

    def test_present_overlap_candidates_may_have_six_decimal_rounded_zero_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._canonical_tables(root)
            row = self._withheld_overlap_row()
            row.update(
                overlap_area_px="8000000", overlap_components="1", overlap_largest_share="1",
                overlap_share="0.000000", overlap_runner_share="0.000000", overlap_margin="0.000000",
                overlap_assigned_share="0.000000", overlap_unregistered_share="1.000000",
                overlap_containment="0.000000", overlap_largest_candidate_share="0.000000",
                overlap_reason="low_assigned_share",
            )
            _, me, warnings = self._load_with_geography(root, [row], GEOGRAPHY_WITH_OVERLAP_COLUMNS)

        self.assertEqual(warnings, [])
        geography = me[11]["geography"]
        self.assertEqual(geography["overlap_best"], 2)
        self.assertEqual(geography["overlap_runner"], 1)
        self.assertEqual(geography["overlap_largest_best"], 2)
        for field in (
            "overlap_share", "overlap_runner_share", "overlap_margin", "overlap_assigned_share",
            "overlap_containment", "overlap_largest_candidate_share",
        ):
            self.assertEqual(geography[field], 0.0, field)
            self.assertIsInstance(geography[field], float, field)
        self.assertEqual(me[11]["ancestor"], 1)

    def test_known_withheld_overlap_reasons_are_preserved(self):
        for reason in ("insufficient_area", "component_disagreement", "low_assigned_share", "low_share", "low_margin"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._canonical_tables(root)
                row = self._withheld_overlap_row()
                row["overlap_reason"] = reason
                _, me, warnings = self._load_with_geography(root, [row], GEOGRAPHY_WITH_OVERLAP_COLUMNS)

                self.assertEqual(warnings, [])
                self.assertIs(me[11]["geography"]["overlap_ok"], False)
                self.assertEqual(me[11]["geography"]["overlap_reason"], reason)
                self.assertIsNone(me[11]["geography"]["overlap_ancestor"])
                self.assertEqual(me[11]["geography"]["overlap_marker"], "")

    def test_malformed_geography_is_atomic_and_warns_once(self):
        invalid_cases: list[tuple[str, list[dict[str, str]], tuple[str, ...]]] = []

        valid = self._accepted_row(10)
        missing_header = tuple(field for field in GEOGRAPHY_COLUMNS if field != "natural_margin")
        invalid_cases.append(("missing required header", [valid], missing_header))

        duplicate_header = GEOGRAPHY_COLUMNS + ("me_name",)
        invalid_cases.append(("duplicate header", [valid], duplicate_header))

        invalid = self._accepted_row(11)
        invalid["me_id"] = "10"
        invalid_cases.append(("duplicate ME ID", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["me_id"] = "0"
        invalid_cases.append(("nonpositive ME ID", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_best"] = "0"
        invalid["raw_geography_ancestor"] = "0"
        invalid_cases.append(("nonpositive candidate ID", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_best"] = "999"
        invalid["raw_geography_ancestor"] = "999"
        invalid_cases.append(("unknown candidate ID", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_residual_p50"] = "nan"
        invalid_cases.append(("NaN metric", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_residual_p95"] = "inf"
        invalid_cases.append(("infinite metric", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_share"] = "1.1"
        invalid_cases.append(("ratio outside range", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_pairs"] = "-1"
        invalid_cases.append(("negative count", [valid, invalid], GEOGRAPHY_COLUMNS))

        invalid = self._accepted_row(11)
        invalid["natural_river_pairs"] = "7"
        invalid["natural_lake_pairs"] = "2"
        invalid_cases.append(("accepted count mismatch", [valid, invalid], GEOGRAPHY_COLUMNS))

        for label, rows, columns in invalid_cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._canonical_tables(root)
                if label == "duplicate header":
                    # A raw row is sufficient because the duplicate header is
                    # rejected before row parsing.
                    values = [rows[0].get(field, "") for field in columns]
                    with (root / "geography_evidence.tsv").open("w", encoding="utf-8", newline="") as handle:
                        handle.write("\t".join(columns) + "\n")
                        handle.write("\t".join(values) + "\n")
                else:
                    self._write_geography(root, rows, columns)
                vanilla, me, warnings = load_metadata(
                    root,
                    {1: "Vanilla One", 2: "Vanilla Two"},
                    {10: "ME Ten", 11: "ME Eleven"},
                )

                self.assertEqual(len(warnings), 1)
                self.assertIn("geography_evidence.tsv diagnostics ignored", warnings[0])
                self.assertNotIn("geography", vanilla[1])
                self.assertNotIn("geography", me[10])
                self.assertNotIn("geography", me[11])

    def test_malformed_overlap_discards_all_geography_once_without_changing_canonical_tables(self):
        invalid_cases: list[tuple[str, dict[str, str], tuple[str, ...]]] = []

        def add_case(label, changes, *, base=None):
            row = dict(base) if base is not None else self._accepted_overlap_row(11)
            row.update(changes)
            invalid_cases.append((label, row, GEOGRAPHY_WITH_OVERLAP_COLUMNS))

        for field in OVERLAP_COLUMNS:
            columns = tuple(column for column in GEOGRAPHY_WITH_OVERLAP_COLUMNS if column != field)
            invalid_cases.append((f"incomplete overlap header: {field}", self._accepted_overlap_row(11), columns))
        invalid_cases.append((
            "unknown overlap prefix still requires full extension", self._accepted_row(11),
            GEOGRAPHY_COLUMNS + ("overlap_unknown",),
        ))
        invalid_cases.append((
            "duplicate overlap header", self._accepted_overlap_row(11),
            GEOGRAPHY_WITH_OVERLAP_COLUMNS + ("overlap_ok",),
        ))
        for field in ("overlap_ok", "overlap_components_agree"):
            for value in ("", "true", "false", "2", "-1", "0.0"):
                add_case(f"invalid flag {field}={value!r}", {field: value})
        for field in ("overlap_area_px", "overlap_components"):
            for value in ("", "-1", "1.5", "1e3", "nan", "invalid"):
                add_case(f"invalid integer {field}={value!r}", {field: value})
        for field in ("overlap_best", "overlap_runner", "overlap_largest_best", "overlap_ancestor"):
            for value in ("0", "-1", "1.5", "1e0", "999", "16777216"):
                add_case(f"invalid Vanilla ID {field}={value!r}", {field: value})
        ratios = (
            "overlap_share", "overlap_runner_share", "overlap_margin", "overlap_assigned_share",
            "overlap_containment", "overlap_largest_share", "overlap_largest_candidate_share",
            "overlap_unregistered_share",
        )
        for field in ratios:
            for value in ("nan", "inf", "-inf", "-0.01", "1.01", "not-a-ratio"):
                add_case(f"invalid ratio {field}={value!r}", {field: value})
            add_case(f"missing required candidate/census ratio {field}", {field: ""})
        for reason in ("", "accepted_by_overlap", "low_coverage"):
            add_case(f"unknown reason {reason!r}", {"overlap_reason": reason})
        add_case("accepted flag with withheld reason", {"overlap_reason": "low_share"})
        add_case("withheld flag with accepted reason", {"overlap_ok": "0"})
        for field in ("overlap_method", "overlap_domain"):
            add_case(f"missing {field}", {field: ""})
            add_case(f"withheld missing {field}", {field: ""}, base=self._withheld_overlap_row())

        add_case("runner repeats best", {"overlap_runner": "2"})
        add_case("runner outranks best", {
            "overlap_share": "0.4", "overlap_runner_share": "0.5", "overlap_margin": "0.1",
        })
        add_case("candidate shares exceed assigned coverage", {
            "overlap_runner_share": "0.06", "overlap_margin": "0.84",
        })
        add_case("margin is not best minus runner", {"overlap_margin": "0.8"})
        add_case("assigned and unregistered coverage do not sum to one", {"overlap_unregistered_share": "0.04"})
        add_case("positive area without components", {"overlap_components": "0"})
        add_case("component count exceeds pixel area", {"overlap_components": "1001"})
        add_case("zero area with components", {"overlap_area_px": "0"})
        add_case("largest component too small for count", {"overlap_largest_share": "0.49"})
        add_case("largest component leaves no pixels for other component", {"overlap_largest_share": "1"})
        add_case("largest component exceeds count/area upper bound", {"overlap_largest_share": "0.9995"})
        add_case("single component must occupy entire area", {"overlap_components": "1"})
        single_component = self._withheld_overlap_row()
        single_component.update(
            overlap_components="1", overlap_largest_share="1", overlap_largest_candidate_share="0.55",
        )
        add_case("single component best disagrees with whole state", {
            "overlap_largest_best": "1",
        }, base=single_component)
        add_case("single component share disagrees with whole state", {
            "overlap_largest_candidate_share": "0.56",
        }, base=single_component)
        add_case("agreement requires same largest best", {
            "overlap_components_agree": "1", "overlap_largest_best": "1",
        }, base=self._withheld_overlap_row())
        add_case("agreement requires strong largest candidate", {
            "overlap_components_agree": "1", "overlap_largest_candidate_share": "0.79",
        }, base=self._withheld_overlap_row())

        for field in ("overlap_share", "overlap_margin", "overlap_assigned_share", "overlap_containment"):
            add_case(f"nonzero {field} without best", {field: "0.1"}, base=self._empty_overlap_row())
        add_case("nonzero runner share without runner", {
            "overlap_runner": "", "overlap_runner_share": "0.05",
        })
        add_case("nonzero largest candidate share without largest best", {
            "overlap_largest_candidate_share": "0.1",
        }, base=self._empty_overlap_row())
        add_case("runner candidate without best", {
            "overlap_runner": "1",
        }, base=self._empty_overlap_row())
        add_case("agreement without candidate", {
            "overlap_components_agree": "1",
        }, base=self._empty_overlap_row())
        add_case("candidate at zero area", {
            "overlap_best": "2", "overlap_largest_best": "2",
        }, base=self._empty_overlap_row(area=0))
        add_case("nonzero diagnostics at zero area", {
            "overlap_unregistered_share": "1",
        }, base=self._empty_overlap_row(area=0))

        add_case("accepted method differs from producer", {"overlap_method": "local_translation"})
        add_case("accepted domain differs from producer", {"overlap_domain": "display_sample"})
        add_case("accepted area below producer minimum", {"overlap_area_px": "63"})
        add_case("accepted assigned coverage below gate", {
            "overlap_share": "0.84", "overlap_margin": "0.79", "overlap_assigned_share": "0.89",
            "overlap_unregistered_share": "0.11",
        })
        add_case("accepted best share below gate", {"overlap_share": "0.79", "overlap_margin": "0.74"})
        add_case("accepted best-runner margin below gate", {
            "overlap_share": "0.59", "overlap_runner_share": "0.35", "overlap_margin": "0.24",
        })
        add_case("accepted components disagree", {"overlap_components_agree": "0"})
        add_case("accepted largest best differs", {"overlap_largest_best": "1"})
        add_case("accepted largest candidate share below gate", {"overlap_largest_candidate_share": "0.79"})
        for field in ("overlap_best", "overlap_largest_best", "overlap_ancestor", "overlap_marker"):
            add_case(f"accepted missing {field}", {field: ""})
        add_case("accepted ancestor differs from best", {"overlap_ancestor": "1"})
        add_case("withheld ancestor leak", {"overlap_ancestor": "2"}, base=self._withheld_overlap_row())
        add_case("withheld marker leak", {"overlap_marker": OVERLAP_MARKER}, base=self._withheld_overlap_row())
        add_case("malformed unknown ME row is not skipped", {
            "overlap_share": "nan",
        }, base=self._accepted_overlap_row(999))

        for label, invalid, columns in invalid_cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._canonical_tables(root)
                canonical_vanilla, canonical_me, baseline_warnings = load_metadata(
                    root, {1: "Vanilla One", 2: "Vanilla Two"}, {10: "ME Ten", 11: "ME Eleven"}
                )
                vanilla, me, warnings = self._load_with_geography(
                    root, [self._accepted_overlap_row(10), invalid], columns
                )

                self.assertEqual(baseline_warnings, [])
                self.assertEqual(len(warnings), 1)
                self.assertIn("geography_evidence.tsv diagnostics ignored", warnings[0])
                self.assertNotIn("geography", vanilla[1])
                self.assertNotIn("geography", me[10])
                self.assertNotIn("geography", me[11])
                self.assertEqual(vanilla, canonical_vanilla)
                self.assertEqual(me, canonical_me)

    def test_html_has_diagnostic_only_me_scope_and_preserves_selection_contract(self):
        html_path = TOOLS / "state_mapping_review.html"
        html = html_path.read_text(encoding="utf-8")

        self.assertIn('id="natural-diagnostics"', html)
        self.assertIn('id="natural-facts"', html)
        self.assertIn('id="natural-notice"', html)
        self.assertRegex(html, r'<section\b[^>]*id="overlap-diagnostics"[^>]*\bhidden\b')
        self.assertRegex(html, r'<h4\b[^>]*id="overlap-heading"')
        self.assertRegex(html, r'<dl\b[^>]*id="overlap-facts"')
        self.assertRegex(html, r'<p\b[^>]*id="overlap-notice"[^>]*\bhidden\b')
        self.assertRegex(
            html,
            r'<section\b[^>]*id="natural-diagnostics"(?:(?!</section>)[\s\S])*'
            r'<section\b[^>]*id="overlap-diagnostics"',
        )
        self.assertIn('aria-labelledby="overlap-heading"', html)
        self.assertIn("full ME-native land-pixel census", html)
        self.assertIn("updateOverlapDiagnostics(side, hovered, canonicalAncestor);", html)
        self.assertIn("Diagnostic only, not state-identity proof.", html)
        self.assertIn("hovered ME state only", html)
        self.assertIn('if (side !== "me" || !hovered?.geography) return;', html)
        self.assertIn("updateNaturalDiagnostics(side, hovered, vanillaId);", html)
        self.assertIn("updateNaturalDiagnostics(null, null, null);", html)

        # Existing identity/highlight behavior remains explicit in the actual
        # embedded implementation, rather than being reconstructed by tests.
        self.assertIn('const fullFamily = highlightMode === "family";', html)
        self.assertIn("if (id === 0) clearSelection();", html)
        self.assertIn('pointerleave', html)
        self.assertIn('pointercancel', html)
        self.assertIn("The combined family shape is not an individual ME state boundary", html)
        self.assertIn("This is the canonical table relation, not geometric proof.", html)

        for element_id in (
            "pixel-comparison-panel",
            "pixel-comparison",
            "pixel-comparison-mode",
            "comparison-canonical",
            "comparison-hovered",
            "comparison-partition",
            "comparison-summary",
            "comparison-facts",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertRegex(html, r'<canvas\b[^>]*id="pixel-comparison"')
        self.assertRegex(html, r'<fieldset\b[^>]*id="pixel-comparison-mode"')
        self.assertRegex(html, r'<input\b[^>]*id="comparison-partition"[^>]*\bchecked\b')
        self.assertNotRegex(html, r'<input\b[^>]*id="comparison-canonical"[^>]*\bchecked\b')
        self.assertIn('let comparisonMode = "partition";', html)
        self.assertIn("registration/geometry visualization", html)
        self.assertIn("not mapping identity proof", html)
        self.assertIn("spatial overlap diagnostic — not identity proof", html)
        self.assertIn("function partitionBoundaryMask", html)
        self.assertIn("function preparePartition", html)

    def test_embedded_selection_and_natural_diagnostics_with_node_dom_fixture(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed")

        html = (TOOLS / "state_mapping_review.html").read_text(encoding="utf-8")
        match = re.search(r"<script>\s*(\"use strict\";.*?)(?:\s*)</script>", html, re.DOTALL)
        self.assertIsNotNone(match, "embedded review JavaScript was not found")
        source = match.group(1)

        fixture = r'''
const assert = require("assert");
const WIDTH = 8;
const HEIGHT = 4;
const HIT_MAPS = {
  me: [
    10, 10, 10, 11, 11, 11, 0, 0,
    10, 10, 11, 11, 12, 12, 99, 99,
    13, 13, 14, 14, 15, 15, 16, 16,
    20, 20, 20, 20, 0, 0, 0, 0
  ],
  vanilla: [
    1, 1, 2, 1, 2, 2, 2, 0,
    0, 1, 0, 2, 1, 0, 1, 0,
    1, 0, 1, 0, 2, 0, 2, 0,
    2, 2, 1, 0, 1, 2, 0, 0
  ]
};

class Element {
  constructor(tagName = "div", nodeType = 1) {
    this.tagName = tagName;
    this.nodeType = nodeType;
    this.children = [];
    this._text = "";
    this.hidden = false;
    this.style = { setProperty: (name, value) => { this.style[name] = value; } };
    this.className = "";
    this.classList = {
      add: (name) => { this.className += (this.className ? " " : "") + name; },
      remove: (name) => {
        this.className = this.className.split(" ").filter((part) => part && part !== name).join(" ");
      }
    };
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.scrollTop = 0;
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }
  append(...items) {
    for (const item of items) {
      if (!item) continue;
      if (item.nodeType === 11) {
        this.append(...item.children);
        item.children = [];
      } else {
        this.children.push(item);
      }
    }
  }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) {
    this.children = [];
    this._text = "";
    this.append(...items);
  }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }
  dispatch(type, event = {}) {
    for (const listener of this.listeners[type] || []) listener(event);
  }
  scrollIntoView() { this._scrolled = (this._scrolled || 0) + 1; }
  getBoundingClientRect() {
    return this.rect || { left: 0, top: 0, width: this.width || WIDTH, height: this.height || HEIGHT };
  }
}

class Canvas extends Element {
  constructor() {
    super("canvas");
    this.width = 1;
    this.height = 1;
    const canvas = this;
    this.context = {
      fillStyle: "",
      imageSmoothingEnabled: false,
      _image: null,
      _pixels: null,
      fills: [],
      putCount: 0,
      fillRect(x, y, width, height) {
        if (x === 0 && y === 0 && width === canvas.width && height === canvas.height) this.fills = [];
        this.fills.push({ x, y, width, height, color: this.fillStyle });
      },
      drawImage(image) { this._image = image; },
      createImageData(width, height) {
        return { width, height, data: new Uint8ClampedArray(width * height * 4) };
      },
      putImageData(imageData, x, y) {
        assert.strictEqual(x, 0);
        assert.strictEqual(y, 0);
        assert.strictEqual(imageData.width, canvas.width);
        assert.strictEqual(imageData.height, canvas.height);
        this._pixels = new Uint8ClampedArray(imageData.data);
        this.putCount++;
      },
      getImageData() {
        const result = this.createImageData(canvas.width, canvas.height);
        if (this._pixels) {
          result.data.set(this._pixels);
          return result;
        }
        const ids = HIT_MAPS[this._image?.side] || [];
        for (let index = 0; index < Math.min(ids.length, canvas.width * canvas.height); index++) {
          const id = ids[index];
          const offset = index * 4;
          result.data[offset] = (id >> 16) & 255;
          result.data[offset + 1] = (id >> 8) & 255;
          result.data[offset + 2] = id & 255;
          result.data[offset + 3] = 255;
        }
        return result;
      }
    };
  }
  getContext(kind) { return kind === "2d" ? this.context : null; }
}

class ImageStub {
  constructor() {
    this.naturalWidth = WIDTH;
    this.naturalHeight = HEIGHT;
    this.onload = null;
    this.onerror = null;
    this.side = "me";
  }
  set src(value) {
    this._src = value;
    this.side = value.includes("dmFuaWxsYQ") || value.includes("vanilla") ? "vanilla" : "me";
    if (this.onload) this.onload();
  }
  get src() { return this._src; }
}

const elements = new Map();
const document = {
  documentElement: new Element("html"),
  getElementById(id) { return elements.get(id); },
  createElement(tag) { return tag === "canvas" ? new Canvas() : new Element(tag); },
  createDocumentFragment() { return new Element("#fragment", 11); },
  register(id, element) { element.id = id; elements.set(id, element); return element; }
};
globalThis.document = document;
globalThis.Image = ImageStub;

document.documentElement.dataset = {};
for (const id of [
  "load-status", "load-error", "maps", "selection-heading", "selection-swatch",
  "selection-summary", "selection-content", "selection-facts", "selection-notice",
  "natural-diagnostics", "natural-facts", "natural-notice", "members-heading",
  "overlap-diagnostics", "overlap-heading", "overlap-facts", "overlap-notice",
  "members-summary", "member-list", "highlight-mode", "mode-family", "mode-exact",
  "vanilla-map", "me-map", "vanilla-coverage", "me-coverage", "warnings",
  "warning-list", "frame-facts", "registration-warning", "registration-json",
  "source-facts", "review-data", "pin-hovered",
  "confirmation-panel", "confirmation-unavailable", "confirmation-counts",
  "confirmation-notice", "confirmation-body",
  "conf-filter-rows", "conf-filter-status", "conf-filter-kind",
  "conf-filter-owner", "conf-filter-priority", "conf-filter-confidence",
  "conf-filter-search", "conf-prev", "conf-next", "conf-clear-pin",
  "confirmation-table-caption", "confirmation-tbody", "conf-more",
  "confirmation-record", "confirmation-record-title",
  "confirmation-record-summary", "confirmation-record-facts",
  "confirmation-record-notice", "me-candidates", "vanilla-candidates",
  "decision-status", "decision-note", "decision-export", "decision-clear",
  "decision-count", "decision-accept", "decision-reject", "decision-defer",
  "decision-candidate", "decision-clear-record",
  "pixel-comparison-panel", "pixel-comparison", "pixel-comparison-mode",
  "comparison-canonical", "comparison-hovered", "comparison-partition",
  "comparison-summary", "comparison-facts"
]) {
  const element = id.endsWith("-map") || id === "pixel-comparison" ? new Canvas() : new Element("div");
  document.register(id, element);
}
document.getElementById("mode-family").checked = false;
document.getElementById("mode-exact").checked = true;
document.getElementById("comparison-canonical").checked = false;
document.getElementById("comparison-hovered").checked = false;
document.getElementById("comparison-partition").checked = true;
document.getElementById("review-data").textContent = JSON.stringify({
  width: WIDTH,
  height: HEIGHT,
  frame: { sampling: "synthetic", crop: "synthetic", bbox_me_xyxy: [0, 0, WIDTH, HEIGHT] },
  registration: { method: "synthetic", init_source: "synthetic", ok: true, converged: true, affine_me_to_vanilla: [] },
  sources: { vanilla: "synthetic", mod: "synthetic", v2m: "synthetic", m2v: "synthetic" },
  vanilla: {
    "1": { id: 1, name: "Vanilla One", basis: "canonical", status: "mapped", members: [10, 11], primary: 10, visible_pixels: 10 },
    "2": { id: 2, name: "Vanilla Two", basis: "canonical", status: "mapped", members: [20], primary: 20, visible_pixels: 10 }
  },
  me: {
    "10": {
      id: 10, name: "ME Ten", ancestor: 1, basis: "canonical", status: "mapped", visible_pixels: 5,
      geography: {
        natural_ok: true, natural_method: "translation", natural_pairs: 10,
        natural_residual_p50: 1.5, natural_residual_p95: 3.5, natural_best: 2,
        natural_share: 0.9, natural_runner: 1, natural_runner_share: 0.05,
        natural_margin: 0.85, raw_geography_ancestor: 2, raw_geography_marker: "coast;river;lake",
        natural_sampled_share: 0.97, natural_reason: "accepted",
        natural_center_x: 10.25, natural_center_y: 20.5, natural_radius: 30,
        natural_river_pairs: 6, natural_lake_pairs: 4,
        overlap_ok: true, overlap_method: "coast_bbox_affine_raster", overlap_domain: "me_native_land",
        overlap_area_px: 1000, overlap_best: 2, overlap_share: 0.9, overlap_runner: 1,
        overlap_runner_share: 0.05, overlap_margin: 0.85, overlap_assigned_share: 0.95,
        overlap_containment: 0.6, overlap_components: 2, overlap_largest_share: 0.75,
        overlap_largest_best: 2, overlap_largest_candidate_share: 0.92, overlap_components_agree: true,
        overlap_unregistered_share: 0.05, overlap_ancestor: 2,
        overlap_marker: "coast_bbox_affine_raster_overlap", overlap_reason: "accepted"
      }
    },
    "11": {
      id: 11, name: "ME Eleven", ancestor: 1, basis: "canonical", status: "mapped", visible_pixels: 5,
      geography: {
        natural_ok: false, natural_method: "", natural_pairs: 0,
        natural_residual_p50: null, natural_residual_p95: null, natural_best: null,
        natural_share: null, natural_runner: null, natural_runner_share: 0,
        natural_margin: null, raw_geography_ancestor: null, raw_geography_marker: "",
        natural_sampled_share: null, natural_reason: "pair_count_mismatch",
        natural_river_pairs: 2, natural_lake_pairs: 5,
        overlap_ok: false, overlap_method: "coast_bbox_affine_raster", overlap_domain: "me_native_land",
        overlap_area_px: 1000, overlap_best: 2, overlap_share: 0.55, overlap_runner: 1,
        overlap_runner_share: 0.4, overlap_margin: 0.15, overlap_assigned_share: 0.95,
        overlap_containment: 0.4, overlap_components: 2, overlap_largest_share: 0.6,
        overlap_largest_best: 2, overlap_largest_candidate_share: 0.85, overlap_components_agree: false,
        overlap_unregistered_share: 0.05, overlap_ancestor: null, overlap_marker: "",
        overlap_reason: "component_disagreement"
      }
    },
    "12": { id: 12, name: "Unmapped", ancestor: null, status: "unmapped", visible_pixels: 2 },
    "13": { id: 13, name: "Zero ancestor", ancestor: 0, visible_pixels: 2 },
    "14": { id: 14, name: "Negative ancestor", ancestor: -1, visible_pixels: 2 },
    "15": { id: 15, name: "Fractional ancestor", ancestor: 1.5, visible_pixels: 2 },
    "16": { id: 16, name: "Out-of-range ancestor", ancestor: 0x1000000, visible_pixels: 2 },
    "20": {
      id: 20, name: "ME Twenty", ancestor: 2, basis: "canonical", status: "mapped", visible_pixels: 4,
      geography: {
        natural_ok: true, natural_method: "translation", natural_pairs: 10,
        natural_residual_p50: 1.5, natural_residual_p95: 3.5, natural_best: 2,
        natural_share: 0.9, natural_runner: 1, natural_runner_share: 0.05,
        natural_margin: 0.85, raw_geography_ancestor: 2, raw_geography_marker: "coast;river;lake",
        natural_sampled_share: 0.97, natural_reason: "accepted",
        natural_river_pairs: 6, natural_lake_pairs: 4
      }
    }
  },
  images: {
    vanilla: { hit_data_uri: "data:image/png;base64,dmFuaWxsYQ==", boundary: "vanilla.png" },
    me: { hit_data_uri: "data:image/png;base64,bWU=", boundary: "me.png" }
  },
  warnings: [],
  confirmation: {
    available: true,
    schema: "state_mapping_confirmation_summary/v2",
    threshold: 0.95,
    input_verified: true,
    sources: { confirmation: "synthetic-confirmation", queue: "synthetic-queue", edges: "docs/states/state_edges_1936.tsv" },
    counts: {
      pairs_total: 4, confirmed_95: 1, review_candidate: 2, excluded_synthetic: 1,
      owner_families_total: 2, owner_families_with_synthetic_member: 1,
      by_source_scope: { compatibility: 2, observation: 1, excluded: 1 },
      by_edge_class: { identity_accepted: 1, evidence_only: 1, significant: 1, excluded: 1 },
      project_refs_total: 7,
      project_refs_by_status: { review_target: 5, unresolved: 2 }
    },
    rows: [
      {
        me_id: 10, me_name: "ME Ten", me_owner: "AAA",
        vanilla_id: 1, vanilla_name: "Vanilla One", vanilla_owner: "AAA",
        relation_kind: "canonical", relation_status: "review_candidate",
        source_scope: "compatibility", edge_source: "candidate|canonical",
        evidence_families: "canonical|pixel", canonical_basis: "pixel-geo",
        canonical_status: "mapped", owner_relation: "same_owner",
        country_scope: "within_country", edge_class: "significant",
        me_share: 0.7, me_registered_share: 0.7,
        me_geometry_usable: true, v_hit_px: 3, v_native_px: 10, v_share_native: 0.3,
        me_owner_area_px: 10, me_owner_match_px: 7, me_owner_share: 0.7,
        v_owner_area_px: 10, v_owner_match_px: 3, v_owner_share: 0.3,
        owner_family_member_count: 2, owner_family_me_ids: [10, 11],
        owner_family_live_member_count: 2, owner_family_has_synthetic_member: false,
        owner_family_min_me_owner_share: 0.6,
        owner_family_reverse_match_px: 6, owner_family_reverse_share: 0.6,
        family_member_count: 2, family_me_ids: [10, 11], family_live_member_count: 2,
        family_has_synthetic_member: false, family_min_me_share: 0.6,
        family_reverse_hit_px: 6, family_reverse_native_px: 10, family_reverse_share: 0.6,
        mapping_audit_class: "review_required",
        mapping_audit_reason_codes: "overlap-weak", comment_votes: null,
        explicit_identity_evidence: "", evidence_cap: 0.8,
        confidence_score: 0.5, confidence_gate: "fail",
        confidence_reasons: "me_share_below_95|family_min_me_share_below_95",
        project_ref_count: 2, project_review_ref_count: 1, review_priority: 1
      },
      {
        me_id: 10, me_name: "ME Ten", me_owner: "AAA",
        vanilla_id: 2, vanilla_name: "Vanilla Two", vanilla_owner: "BBB",
        relation_kind: "candidate", relation_status: "review_candidate",
        source_scope: "observation", edge_source: "candidate",
        evidence_families: "pixel", canonical_basis: "pixel-geo",
        canonical_status: "mapped", owner_relation: "cross_owner",
        country_scope: "cross_country_diagnostic", edge_class: "evidence_only", me_share: 0.1,
        me_registered_share: 0.1, me_geometry_usable: true,
        v_hit_px: 1, v_native_px: 10, v_share_native: 0.1,
        me_owner_area_px: null, me_owner_match_px: null, me_owner_share: null,
        v_owner_area_px: null, v_owner_match_px: null, v_owner_share: null,
        owner_family_member_count: 0, owner_family_me_ids: [],
        owner_family_live_member_count: 0, owner_family_has_synthetic_member: false,
        owner_family_min_me_owner_share: null,
        owner_family_reverse_match_px: null, owner_family_reverse_share: null,
        family_member_count: 1, family_me_ids: [20], family_live_member_count: 1,
        family_has_synthetic_member: false, family_min_me_share: 0.9,
        family_reverse_hit_px: 4, family_reverse_native_px: 10, family_reverse_share: 0.4,
        mapping_audit_class: "review_required",
        mapping_audit_reason_codes: "pixel-share-low", comment_votes: null,
        explicit_identity_evidence: "", evidence_cap: 0.8,
        confidence_score: 0.4, confidence_gate: "fail",
        confidence_reasons: "candidate_not_canonical|owner_mismatch",
        project_ref_count: 0, project_review_ref_count: 0, review_priority: 3
      },
      {
        me_id: 11, me_name: "ME Eleven", me_owner: "AAA",
        vanilla_id: 1, vanilla_name: "Vanilla One", vanilla_owner: "AAA",
        relation_kind: "canonical", relation_status: "confirmed_95",
        source_scope: "compatibility", edge_source: "candidate|canonical",
        evidence_families: "canonical|comment", canonical_basis: "comment",
        canonical_status: "mapped", owner_relation: "same_owner",
        country_scope: "within_country", edge_class: "identity_accepted",
        me_share: 0.99, me_registered_share: 0.99,
        me_geometry_usable: true, v_hit_px: 9, v_native_px: 10, v_share_native: 0.9,
        me_owner_area_px: 10, me_owner_match_px: 10, me_owner_share: 1,
        v_owner_area_px: 10, v_owner_match_px: 10, v_owner_share: 1,
        owner_family_member_count: 2, owner_family_me_ids: [10, 11],
        owner_family_live_member_count: 2, owner_family_has_synthetic_member: false,
        owner_family_min_me_owner_share: 1,
        owner_family_reverse_match_px: 10, owner_family_reverse_share: 1,
        family_member_count: 2, family_me_ids: [10, 11], family_live_member_count: 2,
        family_has_synthetic_member: false, family_min_me_share: 0.96,
        family_reverse_hit_px: 10, family_reverse_native_px: 10, family_reverse_share: 1,
        mapping_audit_class: "no_action",
        mapping_audit_reason_codes: "", comment_votes: 1,
        explicit_identity_evidence: "comment_votes:1|evidence:comment", evidence_cap: 1,
        confidence_score: 1, confidence_gate: "pass",
        confidence_reasons: "", project_ref_count: 5,
        project_review_ref_count: 0
      },
      {
        me_id: 99, me_name: "Synthetic Border", me_owner: "MAP",
        vanilla_id: 1, vanilla_name: "Vanilla One", vanilla_owner: "AAA",
        relation_kind: "candidate", relation_status: "excluded_synthetic",
        source_scope: "excluded", edge_source: "raster",
        evidence_families: "raster", canonical_basis: "",
        canonical_status: "", owner_relation: "cross_owner",
        country_scope: "cross_country_diagnostic", edge_class: "excluded", me_share: 0,
        me_registered_share: 0, me_geometry_usable: false,
        v_hit_px: 0, v_native_px: 10, v_share_native: 0,
        me_owner_area_px: null, me_owner_match_px: null, me_owner_share: null,
        v_owner_area_px: null, v_owner_match_px: null, v_owner_share: null,
        owner_family_member_count: 0, owner_family_me_ids: [],
        owner_family_live_member_count: 0, owner_family_has_synthetic_member: true,
        owner_family_min_me_owner_share: null,
        owner_family_reverse_match_px: null, owner_family_reverse_share: null,
        family_member_count: 0, family_me_ids: [], family_live_member_count: 0,
        family_has_synthetic_member: true, family_min_me_share: null,
        family_reverse_hit_px: 0, family_reverse_native_px: 10, family_reverse_share: 0,
        mapping_audit_class: "review_required",
        mapping_audit_reason_codes: "synthetic_state", comment_votes: null,
        explicit_identity_evidence: "", evidence_cap: 0.5,
        confidence_score: 0, confidence_gate: "excluded",
        confidence_reasons: "synthetic_state", project_ref_count: 0,
        project_review_ref_count: 0
      }
    ]
  }
});
'''
        assertions = r'''
setTimeout(() => {
  try {
    const text = (id) => document.getElementById(id).textContent;
    const natural = document.getElementById("natural-diagnostics");
    const overlap = document.getElementById("overlap-diagnostics");
    const overlapNotice = document.getElementById("overlap-notice");
    const content = document.getElementById("selection-content");
    const me = document.getElementById("me-map");
    const vanilla = document.getElementById("vanilla-map");
    const mode = document.getElementById("highlight-mode");
    const family = document.getElementById("mode-family");
    const exact = document.getElementById("mode-exact");
    const comparison = document.getElementById("pixel-comparison");
    const comparisonMode = document.getElementById("pixel-comparison-mode");
    const canonicalMode = document.getElementById("comparison-canonical");
    const hoveredMode = document.getElementById("comparison-hovered");
    const partitionMode = document.getElementById("comparison-partition");
    const palette = [
      [212, 211, 207, 255], [64, 131, 93, 255], [201, 76, 76, 255],
      [201, 141, 49, 255], [84, 124, 183, 255], [147, 101, 173, 255]
    ];
    // Expectations are fixed in display-coordinate order, not recomputed by
    // the production classification algorithm or loaded from real map assets.
    const canonicalPixels = [
      1, 1, 2, 1, 2, 2, 4, 0,
      3, 1, 3, 2, 5, 5, 5, 5,
      5, 5, 5, 5, 5, 5, 5, 5,
      1, 1, 2, 3, 4, 4, 0, 0
    ];
    const tenPixels = [
      1, 1, 2, 0, 0, 0, 0, 0,
      3, 1, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0
    ];
    const elevenPixels = [
      0, 0, 0, 1, 2, 2, 0, 0,
      0, 0, 3, 2, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0
    ];
    const twentyPixels = [
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0,
      1, 1, 2, 3, 0, 0, 0, 0
    ];
    const neutralPixels = new Array(WIDTH * HEIGHT).fill(0);
    const categoryLabels = [
      "Neutral / water", "Match", "Mismatch", "ME-only", "Vanilla-only", "Unknown / unmapped"
    ];
    const readFacts = (id) => {
      const children = document.getElementById(id).children;
      assert.strictEqual(children.length % 2, 0);
      const result = new Map();
      for (let index = 0; index < children.length; index += 2) {
        assert.strictEqual(children[index].tagName, "dt");
        assert.strictEqual(children[index + 1].tagName, "dd");
        result.set(children[index].textContent, children[index + 1].textContent);
      }
      return result;
    };
    const acceptedOverlapFacts = new Map([
      ["Overlap acceptance", "Accepted"],
      ["Overlap method", "coast_bbox_affine_raster"],
      ["Overlap domain", "me_native_land"],
      ["Overlap area", "1000 ME-native land pixels"],
      ["Overlap best", "Vanilla 2 — Vanilla Two"],
      ["Overlap best share", "90.00%"],
      ["Overlap runner", "Vanilla 1 — Vanilla One"],
      ["Overlap runner share", "5.00%"],
      ["Overlap best–runner margin", "85.00%"],
      ["Assigned / unregistered share", "95.00% / 5.00%"],
      ["Best reverse containment", "60.00%"],
      ["Land components", "2 (4-connected)"],
      ["Largest-component area share", "75.00%"],
      ["Largest-component best", "Vanilla 2 — Vanilla Two"],
      ["Largest-component candidate share", "92.00%"],
      ["Component agreement", "Yes — all components meet the producer coverage/share/margin gates"],
      ["Overlap producer reason", "accepted"],
      ["Overlap accepted ancestor", "Vanilla 2 — Vanilla Two"],
      ["Overlap marker", "coast_bbox_affine_raster_overlap"]
    ]);
    const assertAcceptedOverlap = () => {
      assert.strictEqual(overlap.hidden, false);
      assert.deepStrictEqual(readFacts("overlap-facts"), acceptedOverlapFacts);
      assert.strictEqual(overlapNotice.hidden, false);
      assert.ok(text("overlap-notice").includes("Overlap best Vanilla 2 differs from canonical Vanilla 1"));
      assert.ok(text("overlap-notice").includes("canonical relation and fills remain unchanged"));
      assert.ok(!text("overlap-notice").includes("overlap_ok is false"));
    };
    const assertWithheldOverlap = () => {
      assert.strictEqual(overlap.hidden, false);
      assert.deepStrictEqual(readFacts("overlap-facts"), new Map([
        ...acceptedOverlapFacts,
        ["Overlap acceptance", "Withheld"],
        ["Overlap best share", "55.00%"],
        ["Overlap runner share", "40.00%"],
        ["Overlap best–runner margin", "15.00%"],
        ["Best reverse containment", "40.00%"],
        ["Largest-component area share", "60.00%"],
        ["Largest-component candidate share", "85.00%"],
        ["Component agreement", "No — an unregistered, weak or dissenting component withholds ancestry"],
        ["Overlap producer reason", "component_disagreement"],
        ["Overlap accepted ancestor", "None supplied"],
        ["Overlap marker", "Not supplied"]
      ]));
      assert.strictEqual(overlapNotice.hidden, false);
      assert.ok(text("overlap-notice").includes("overlap_ok is false"));
      assert.ok(text("overlap-notice").includes("component_disagreement"));
      assert.ok(text("overlap-notice").includes("Overlap best Vanilla 2 differs from canonical Vanilla 1"));
      assert.ok(text("overlap-notice").includes("canonical relation and fills remain unchanged"));
    };
    const assertOverlapHidden = () => {
      assert.strictEqual(overlap.hidden, true);
      assert.strictEqual(document.getElementById("overlap-facts").children.length, 0);
      assert.strictEqual(text("overlap-facts"), "");
      assert.strictEqual(overlapNotice.hidden, true);
      assert.strictEqual(text("overlap-notice"), "");
    };
    const assertComparison = (pixels, counts, denominator, hovered = false) => {
      assert.strictEqual(comparison.width, WIDTH);
      assert.strictEqual(comparison.height, HEIGHT);
      assert.ok(comparison.context.putCount > 0, "comparison must write canvas pixels");
      assert.strictEqual(pixels.length, WIDTH * HEIGHT);
      assert.deepStrictEqual(
        Array.from(comparison.context.getImageData(0, 0, WIDTH, HEIGHT).data),
        pixels.flatMap((category) => palette[category]),
        "comparison must use the documented RGBA category at every display pixel"
      );
      const facts = readFacts("comparison-facts");
      if (!hovered || denominator > 0) {
        assert.ok(text("comparison-summary").includes(
          `${counts[1]} match; ${counts[2]} mismatch; ${denominator} compared land pixels`
        ));
      }
      const denominatorText = facts.get("Comparison denominator");
      assert.ok(denominatorText?.startsWith(`${denominator} pixels — `), denominatorText);
      assert.ok(denominatorText.includes(hovered
        ? "hovered ME state's sampled pixels" : "union of land in either hit map"));
      for (let category = 0; category < categoryLabels.length; category++) {
        const label = categoryLabels[category];
        const value = facts.get(label);
        assert.ok(value?.startsWith(`${counts[category]} pixels (`), `${label}: ${value}`);
        const divisor = category === 0 ? WIDTH * HEIGHT : denominator;
        if (divisor === 0) {
          assert.match(value, /n\/a/i, `${label} must not report a percentage of zero`);
        } else {
          const percentage = (100 * counts[category] / divisor).toFixed(2);
          assert.ok(value.includes(`${percentage}% of ${category === 0 ? "full frame" : "comparison denominator"}`),
            `${label}: ${value}`);
        }
      }
    };
    const partitionPalette = {
      neutral: [212, 211, 207, 255],
      meBoundary: [44, 82, 59, 255],
      vanillaBoundary: [198, 69, 116, 255],
      combinedBoundary: [92, 70, 96, 255]
    };
    const assertPartitionNoTarget = () => {
      const data = comparison.context.getImageData(0, 0, WIDTH, HEIGHT).data;
      const pixel = (index) => Array.from(data.slice(index * 4, index * 4 + 4));
      assert.deepStrictEqual(pixel(7), partitionPalette.neutral, "water remains neutral in partition overlay");
      assert.deepStrictEqual(pixel(6), partitionPalette.vanillaBoundary, "dashed Vanilla boundary is visible");
      const facts = readFacts("comparison-facts");
      assert.strictEqual(facts.get("Comparison scope"), "Partition overlay — candidate-independent state boundaries");
      assert.strictEqual(facts.get("Diagnostic status"), "spatial overlap diagnostic — not identity proof");
      assert.ok(text("comparison-summary").includes("No ME target selected"));
      assert.ok(!facts.has("Top Vanilla overlap candidates"), "no Vanilla descendant is invented without an ME target");
    };
    const assertPartitionTen = () => {
      const data = comparison.context.getImageData(0, 0, WIDTH, HEIGHT).data;
      const pixel = (index) => Array.from(data.slice(index * 4, index * 4 + 4));
      assert.deepStrictEqual(pixel(0), partitionPalette.combinedBoundary, "overlapping boundaries use the combined diagnostic color");
      assert.deepStrictEqual(pixel(8), partitionPalette.meBoundary, "ME boundary remains continuous beside water");
      const facts = readFacts("comparison-facts");
      assert.strictEqual(facts.get("Sampled ME pixels"), "5");
      assert.ok(facts.get("Top Vanilla overlap candidates").includes("Vanilla 1: 3 pixels (60.00%)"));
      assert.ok(facts.get("Top Vanilla overlap candidates").includes("Vanilla 2: 1 pixels (20.00%)"));
      assert.ok(facts.get("Runner candidate").includes("Vanilla 2"));
      assert.strictEqual(facts.get("Runner share"), "20.00%");
      assert.strictEqual(facts.get("Best–runner margin"), "40.00%");
      assert.ok(text("comparison-summary").includes("spatial overlap diagnostic — not identity proof"));
    };

    const assertCanonical = () => {
      assertComparison(canonicalPixels, [3, 6, 5, 3, 3, 12], 29);
      assert.match(text("comparison-summary"), /Canonical crosswalk/);
    };
    const assertNoTarget = () => {
      assertComparison(neutralPixels, [32, 0, 0, 0, 0, 0], 0, true);
      assert.match(text("comparison-summary"), /hover an ME state/i);
      assert.match(text("comparison-summary"), /No ME target selected/i);
      assert.strictEqual(readFacts("comparison-facts").get("Comparison scope"), "No hovered ME state");
    };
    const assertTen = () => {
      assertComparison(tenPixels, [27, 3, 1, 1, 0, 0], 5, true);
      assert.ok(readFacts("comparison-facts").get("Comparison scope").includes("ME 10 ↔ Vanilla 1"));
    };
    const assertEleven = () => {
      assertComparison(elevenPixels, [27, 1, 3, 1, 0, 0], 5, true);
      assert.ok(readFacts("comparison-facts").get("Comparison scope").includes("ME 11 ↔ Vanilla 1"));
    };
    const assertTwenty = () => {
      assertComparison(twentyPixels, [28, 2, 1, 1, 0, 0], 4, true);
      assert.ok(readFacts("comparison-facts").get("Comparison scope").includes("ME 20 ↔ Vanilla 2"));
    };
    const moveTo = (canvas, x, y, type = "pointermove") => {
      const rect = canvas.getBoundingClientRect();
      canvas.dispatch(type, {
        clientX: rect.left + x * rect.width / WIDTH,
        clientY: rect.top + y * rect.height / HEIGHT
      });
    };
    const hover = (canvas, index, type = "pointermove") => {
      moveTo(canvas, index % WIDTH + 0.25, Math.floor(index / WIDTH) + 0.25, type);
    };
    const setHighlightMode = (fullFamily) => {
      family.checked = fullFamily;
      exact.checked = !fullFamily;
      mode.dispatch("change", {});
    };
    const setComparisonMode = (selection) => {
      const partition = selection === "partition";
      const hovered = selection === true || selection === "hovered";
      hoveredMode.checked = hovered;
      partitionMode.checked = partition;
      canonicalMode.checked = !hovered && !partition;
      comparisonMode.dispatch("change", {});
    };
    const assertHighlighted = (canvas, ids) => {
      const actual = new Set();
      for (const fill of canvas.context.fills) {
        if (fill.color === "#d4d3cf") continue;
        for (let y = fill.y; y < fill.y + fill.height; y++) {
          for (let x = fill.x; x < fill.x + fill.width; x++) actual.add(y * WIDTH + x);
        }
      }
      const sourceIds = HIT_MAPS[canvas === me ? "me" : "vanilla"];
      const expected = sourceIds.flatMap((id, index) => ids.includes(id) ? [index] : []);
      assert.deepStrictEqual(Array.from(actual).sort((a, b) => a - b), expected);
    };
    const assertPair = (meIds, vanillaIds) => {
      assertHighlighted(me, meIds);
      assertHighlighted(vanilla, vanillaIds);
    };
    const assertCleared = () => {
      assert.strictEqual(content.hidden, true);
      assert.strictEqual(natural.hidden, true);
      assert.strictEqual(text("natural-facts"), "");
      assert.strictEqual(document.getElementById("natural-notice").hidden, true);
      assert.strictEqual(text("natural-notice"), "");
      assertOverlapHidden();
      assertPair([], []);
    };

    assert.strictEqual(document.documentElement.dataset.reviewState, "ready");
    assert.strictEqual(canonicalMode.checked, false);
    assert.strictEqual(hoveredMode.checked, false);
    assert.strictEqual(partitionMode.checked, true);
    assertPartitionNoTarget();
    setComparisonMode("canonical");
    assert.strictEqual(canonicalMode.checked, true);
    assertCanonical();
    const canonicalFacts = text("comparison-facts");
    assert.strictEqual(partitionMode.checked, false);
    setComparisonMode("partition");
    assertPartitionNoTarget();
    hover(me, 0);
    assertPair([10], [1]);
    assertAcceptedOverlap();
    assertPartitionTen();
    // A Vanilla-only display pixel never invents an ME descendant or overlap target.
    hover(comparison, 6);
    assertOverlapHidden();
    assertPair([], [2]);
    assertPartitionNoTarget();
    setComparisonMode(false);
    assertCanonical();


    // A natural candidate deliberately disagrees with the canonical ancestor.
    // It must change neither exact fills nor any pixel comparison category.
    hover(me, 0);
    assert.ok(text("selection-summary").includes("Only this ME member and its ancestor are filled"));
    assert.ok(text("selection-summary").includes("ME 10 ↔ Vanilla 1"));
    assertPair([10], [1]);
    assert.strictEqual(natural.hidden, false);
    assert.ok(readFacts("natural-facts").get("Natural best").includes("Vanilla 2"));
    assert.ok(text("natural-notice").includes("differs from canonical Vanilla 1"));
    assertAcceptedOverlap();
    const acceptedNaturalFacts = text("natural-facts");
    const acceptedNaturalNotice = text("natural-notice");
    assertCanonical();
    assert.strictEqual(text("comparison-facts"), canonicalFacts);

    // Vanilla hover preserves exact semantics and hides ME-only diagnostics.
    hover(vanilla, 0);
    assert.strictEqual(natural.hidden, true);
    assertOverlapHidden();
    assert.ok(text("selection-summary").includes("no ME member selected"));
    assertPair([], [1]);
    assertCanonical();

    // Full family still uses canonical descendants, never natural candidates.
    hover(me, 0);
    setHighlightMode(true);
    assert.ok(text("selection-summary").startsWith("Full canonical family"));
    assert.ok(text("member-list").includes("ME 10"));
    assert.ok(text("member-list").includes("ME 11"));
    assert.ok(!text("member-list").includes("ME 20"));
    assertPair([10, 11], [1]);
    assertAcceptedOverlap();
    assert.strictEqual(text("natural-facts"), acceptedNaturalFacts);
    assert.strictEqual(text("natural-notice"), acceptedNaturalNotice);
    assertCanonical();

    // Failed diagnostics and accepted corroborating diagnostics both survive.
    hover(me, 3);
    assert.strictEqual(natural.hidden, false);
    assert.ok(text("natural-notice").includes("Producer quality gates not met"));
    assertWithheldOverlap();
    hover(me, 24);
    assert.strictEqual(natural.hidden, false);
    assertOverlapHidden();
    assert.ok(text("natural-facts").includes("Natural best"));
    assert.ok(text("natural-facts").includes("corroborating diagnostic only"));
    assertPair([20], [2]);
    assertCanonical();

    // Clearing the original panes leaves canonical comparison unchanged.
    me.dispatch("pointerleave", {});
    assertCleared();
    assertCanonical();
    hover(me, 0);
    hover(me, 7);
    assertCleared();
    assertCanonical();

    // No ME selection, including vanilla-only selection, is not a mismatch.
    setComparisonMode(true);
    assertNoTarget();
    hover(vanilla, 0);
    assert.strictEqual(content.hidden, false);
    assert.strictEqual(natural.hidden, true);
    assertOverlapHidden();
    assertPair([10, 11], [1]);
    assertNoTarget();

    // Hovered comparison is exact even while the linked maps show a family.
    hover(me, 0);
    assertPair([10, 11], [1]);
    assertAcceptedOverlap();
    assertTen();
    setHighlightMode(false);
    assertPair([10], [1]);
    assertAcceptedOverlap();
    assertTen();
    setComparisonMode(false);
    assertPair([10], [1]);
    assertAcceptedOverlap();
    assertCanonical();
    setComparisonMode(true);
    assertTen();

    // Equal-area siblings have intentionally different match/mismatch counts.
    hover(me, 3);
    assertPair([11], [1]);
    assertEleven();
    assert.ok(text("natural-notice").includes("Producer quality gates not met"));
    assertWithheldOverlap();
    hover(me, 24);
    assertPair([20], [2]);
    assertTwenty();
    assertOverlapHidden();
    setHighlightMode(true);
    assertTwenty();
    setHighlightMode(false);

    // The comparison canvas uses display coordinates even when CSS-scaled.
    // At mismatch pixel 2, ME 10 takes precedence over underlying Vanilla 2.
    comparison.rect = { left: 120, top: 50, width: 80, height: 40 };
    for (const type of ["pointermove", "pointerdown"]) {
      hover(comparison, 2, type);
      assert.ok(text("selection-summary").includes("ME 10 ↔ Vanilla 1"));
      assertPair([10], [1]);
      assert.strictEqual(natural.hidden, false);
      assertAcceptedOverlap();
      assertTen();

      // With no ME land, use Vanilla instead; this is still no hovered ME target.
      hover(comparison, 6, type);
      assert.ok(text("selection-summary").includes("Vanilla 2 only; no ME member selected"));
      assertPair([], [2]);
      assert.strictEqual(natural.hidden, true);
      assertOverlapHidden();
      assertNoTarget();
    }

    // Unknown outranks ME-only even over water. Null/zero/negative/noninteger/
    // out-of-range ancestors and missing ME metadata all have explicit coverage.
    for (const [id, firstPixel] of [[12, 12], [99, 14], [13, 16], [14, 18], [15, 20], [16, 22]]) {
      hover(comparison, firstPixel);
      assert.ok(text("selection-heading").includes(`ME ${id}`));
      assertPair([id], []);
      assert.strictEqual(natural.hidden, true);
      assertOverlapHidden();
      const expected = neutralPixels.slice();
      expected[firstPixel] = 5;
      expected[firstPixel + 1] = 5;
      assertComparison(expected, [30, 0, 0, 0, 0, 2], 2, true);
      hover(comparison, firstPixel + 1);
      assertComparison(expected, [30, 0, 0, 0, 0, 2], 2, true);
    }

    // Every pointer exit path clears both the linked selection and the hovered
    // diff. Frame bounds are half-open, including the bottom and right edges.
    for (const canvas of [me, vanilla, comparison]) {
      const clearingActions = [
        () => hover(canvas, 7),
        () => moveTo(canvas, -0.1, 0.25),
        () => moveTo(canvas, WIDTH, 0.25),
        () => moveTo(canvas, 0.25, -0.1),
        () => moveTo(canvas, 0.25, HEIGHT),
        () => canvas.dispatch("pointerleave", {}),
        () => canvas.dispatch("pointercancel", {})
      ];
      for (const clear of clearingActions) {
        hover(comparison, 2);
        assertTen();
        clear();
        assertCleared();
        assertNoTarget();
      }
    }

    // Mode switches restore the original full-frame classification, regardless
    // of the last target, and canonical clearing never erases this visualization.
    setComparisonMode(false);
    assertCanonical();
    assert.strictEqual(text("comparison-facts"), canonicalFacts);
    hover(comparison, 2);
    comparison.dispatch("pointercancel", {});
    assertCleared();
    assertCanonical();
    setComparisonMode(true);
    assertNoTarget();

    // --- 95% confirmation overlay: dashboard, queue table, pin, decisions ---
    const counts = readFacts("confirmation-counts");
    assert.strictEqual(document.getElementById("confirmation-body").hidden, false);
    assert.strictEqual(document.getElementById("confirmation-unavailable").hidden, true);
    assert.ok(counts.get("confirmed_95").includes("1 of 4"));
    assert.ok(counts.get("review_candidate").includes("2"));
    assert.ok(counts.get("excluded_synthetic").includes("1"));
    assert.ok(text("confirmation-counts").includes("conservative coverage gate, not a calibrated probability"));
    assert.ok(text("confirmation-counts").includes("state_mapping_confirmation_summary/v2"));
    assert.ok(text("confirmation-counts").includes("verified"));
    assert.ok(text("confirmation-notice").includes("0.95"));
    const tbody = document.getElementById("confirmation-tbody");
    assert.strictEqual(tbody.children.length, 2);
    assert.ok(text("confirmation-table-caption").includes("2 review-queue row"));

    const PIN_FILL = "rgba(28, 84, 90, 0.42)";
    const PIN_OUTLINE = "#103c42";
    const assertPinFill = (canvas, ids) => {
      const fills = new Set();
      const outlines = new Set();
      for (const fill of canvas.context.fills) {
        if (fill.color === PIN_FILL) {
          for (let y = fill.y; y < fill.y + fill.height; y++) {
            for (let x = fill.x; x < fill.x + fill.width; x++) fills.add(y * WIDTH + x);
          }
        }
        if (fill.color === PIN_OUTLINE) outlines.add(fill.y * WIDTH + fill.x);
      }
      const sourceIds = canvas === me ? HIT_MAPS.me : HIT_MAPS.vanilla;
      const expected = sourceIds.flatMap((id, index) => ids.includes(id) ? [index] : []);
      assert.deepStrictEqual(Array.from(fills).sort((a, b) => a - b), expected);
      assert.ok(outlines.size > 0, "pinned state must draw a boundary outline");
    };
    const press = (canvas, index) => {
      const rect = canvas.getBoundingClientRect();
      canvas.dispatch("pointerdown", {
        type: "pointerdown",
        clientX: rect.left + (index % WIDTH + 0.25) * rect.width / WIDTH,
        clientY: rect.top + (Math.floor(index / WIDTH) + 0.25) * rect.height / HEIGHT
      });
    };

    // Clicking a queue row pins the exact pair on both maps and the record panel.
    tbody.children[0].dispatch("click", {});
    assert.strictEqual(document.getElementById("confirmation-record").hidden, false);
    assert.ok(text("confirmation-record-title").includes("ME 10"));
    assert.ok(text("confirmation-record-title").includes("Vanilla 1"));
    assert.ok(text("confirmation-record-facts").includes("70.00%"));
    assert.ok(text("confirmation-record-facts").includes("me_share_below_95"));
    assert.ok(text("confirmation-record-facts").includes("owner share"));
    assert.ok(text("confirmation-record-facts").includes("Owner-family reverse share"));
    assert.ok(text("confirmation-record-facts").includes("Owner-family members"));
    assert.ok(text("confirmation-record-facts").includes("(diagnostic)"));
    assert.ok(text("confirmation-record-notice").includes("never a mapping-identity proof"));
    assert.ok(text("me-candidates").includes("Vanilla 2"));
    assert.ok(text("vanilla-candidates").includes("ME 11"));
    assert.ok(tbody.children[0].className.includes("is-selected"));
    assertPinFill(me, [10]);
    assertPinFill(vanilla, [1]);

    // Rows are focusable ARIA rows; Enter/Space select like a click.
    assert.strictEqual(tbody.children[0].tabIndex, 0);
    assert.strictEqual(tbody.children[0].attributes.role, "row");
    assert.strictEqual(tbody.children[0].attributes["aria-selected"], "true");
    assert.strictEqual(tbody.children[1].attributes["aria-selected"], "false");
    const enterEvent = { key: "Enter", preventDefault() { this.prevented = true; } };
    tbody.children[1].dispatch("keydown", enterEvent);
    assert.strictEqual(enterEvent.prevented, true);
    assert.ok(text("confirmation-record-title").includes("Vanilla 2"));
    assert.strictEqual(tbody.children[1].attributes["aria-selected"], "true");
    assert.strictEqual(tbody.children[0].attributes["aria-selected"], "false");
    const spaceEvent = { key: " ", preventDefault() { this.prevented = true; } };
    tbody.children[0].dispatch("keydown", spaceEvent);
    assert.strictEqual(spaceEvent.prevented, true);
    assert.ok(text("confirmation-record-title").includes("Vanilla 1"));
    tbody.children[0].dispatch("keydown", { key: "ArrowDown" });
    assert.ok(text("confirmation-record-title").includes("Vanilla 1"));

    // Synthetic rows never appear in candidate navigation lists.
    assert.ok(!text("vanilla-candidates").includes("Synthetic"));
    assert.ok(!text("me-candidates").includes("Synthetic"));

    // Previous/Next navigates the filtered queue without losing the pin.
    document.getElementById("conf-next").dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("Vanilla 2"));
    assert.ok(text("confirmation-record-notice").includes("Non-canonical candidate"));
    assert.ok(text("confirmation-record-notice").includes("cross_owner"));
    assertPinFill(me, [10]);
    assertPinFill(vanilla, [2]);
    document.getElementById("conf-prev").dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("Vanilla 1"));
    assertPinFill(vanilla, [1]);

    // Scope, status, owner, confidence and text filters all apply to the table.
    const rowsFilter = document.getElementById("conf-filter-rows");
    const statusFilter = document.getElementById("conf-filter-status");
    const ownerFilter = document.getElementById("conf-filter-owner");
    const confFilter = document.getElementById("conf-filter-confidence");
    const search = document.getElementById("conf-filter-search");
    rowsFilter.value = "all"; rowsFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 4);
    statusFilter.value = "confirmed_95"; statusFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 1);
    assert.ok(text("confirmation-tbody").includes("confirmed_95"));
    statusFilter.value = "excluded_synthetic"; statusFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 1);
    assert.ok(text("confirmation-tbody").includes("Synthetic Border"));
    statusFilter.value = ""; statusFilter.dispatch("change", {});
    ownerFilter.value = "cross_owner"; ownerFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 2);
    ownerFilter.value = ""; ownerFilter.dispatch("change", {});
    rowsFilter.value = "queue"; rowsFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 2);
    confFilter.value = "0.5"; confFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 1);
    confFilter.value = ""; confFilter.dispatch("change", {});
    search.value = "vanilla two"; search.dispatch("input", {});
    assert.strictEqual(tbody.children.length, 1);
    assert.ok(text("confirmation-tbody").includes("Vanilla Two"));
    search.value = ""; search.dispatch("input", {});
    assert.strictEqual(tbody.children.length, 2);

    // Decision capture stores locally only; export and clear report counts.
    tbody.children[0].dispatch("click", {});
    document.getElementById("decision-accept").dispatch("click", {});
    assert.strictEqual(document.getElementById("decision-accept").dataset.active, "true");
    assert.ok(text("decision-count").includes("1 recorded decision"));
    const decisionNote = document.getElementById("decision-note");
    decisionNote.value = "check owner tag";
    decisionNote.dispatch("input", {});
    document.getElementById("decision-export").dispatch("click", {});
    assert.ok(text("decision-status").includes("Exported 1 decision"));
    document.getElementById("decision-clear").dispatch("click", {});
    assert.ok(text("decision-count").includes("0 recorded decision"));

    // Map clicks pin too: ME resolves its ancestor, vanilla resolves primary.
    document.getElementById("conf-clear-pin").dispatch("click", {});
    assert.strictEqual(document.getElementById("confirmation-record").hidden, true);
    press(me, 0);
    assert.ok(text("confirmation-record-title").includes("ME 10"));
    assertPinFill(me, [10]);
    assertPinFill(vanilla, [1]);
    assertPair([10], [1]);
    press(vanilla, 6);
    assert.ok(text("confirmation-record-title").includes("map pin, not a confirmation row"));
    assertPinFill(vanilla, [2]);
    assertPair([20], [2]);
    document.getElementById("conf-clear-pin").dispatch("click", {});
    vanilla.dispatch("pointerleave", {});
    assertCleared();

    // The selection-panel pin button pins the hovered exact pair.
    hover(me, 0);
    assert.strictEqual(document.getElementById("pin-hovered").hidden, false);
    document.getElementById("pin-hovered").dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("ME 10"));
    assertPinFill(me, [10]);
    assertPinFill(vanilla, [1]);
    document.getElementById("conf-clear-pin").dispatch("click", {});
    me.dispatch("pointerleave", {});
    assertCleared();
  } catch (error) {
    console.error(error.stack || error);
    process.exitCode = 1;
  }
}, 0);
'''
        completed = subprocess.run(
            [node, "-"],
            input=fixture + "\n" + source + "\n" + assertions,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

        # Second run: 450 extra queue rows push the selected pair beyond the
        # 400-row page window, a zero-confirmed ledger exercises the honest
        # notice, and a #me=&vanilla= deep-link pins the last row.
        long_list_setup = r'''
globalThis.location = { hash: "#me=9449&vanilla=2" };
{
  const payload = JSON.parse(document.getElementById("review-data").textContent);
  const template = payload.confirmation.rows[0];
  for (let index = 0; index < 450; index++) {
    const row = JSON.parse(JSON.stringify(template));
    row.me_id = 9000 + index;
    row.me_name = `ME ${9000 + index}`;
    row.vanilla_id = 2;
    row.vanilla_name = "Vanilla Two";
    row.relation_kind = "candidate";
    payload.confirmation.rows.push(row);
  }
  const confirmed = payload.confirmation.rows[2];
  confirmed.relation_status = "review_candidate";
  confirmed.confidence_gate = "fail";
  confirmed.confidence_reasons = "me_share_below_95";
  payload.confirmation.counts.confirmed_95 = 0;
  payload.confirmation.counts.review_candidate = 453;
  payload.confirmation.counts.pairs_total = 454;
  document.getElementById("review-data").textContent = JSON.stringify(payload);
}
'''
        long_list_assertions = r'''
setTimeout(() => {
  try {
    const text = (id) => document.getElementById(id).textContent;
    const tbody = document.getElementById("confirmation-tbody");

    // The deep-linked last queue row is rendered even beyond the 400-row
    // page window, marked selected, and scrolled into view.
    assert.strictEqual(tbody.children.length, 453);
    assert.ok(text("confirmation-record-title").includes("ME 9449"));
    const selected = tbody.children[452];
    assert.ok(selected.className.includes("is-selected"));
    assert.strictEqual(selected.attributes["aria-selected"], "true");
    assert.strictEqual(selected.tabIndex, 0);
    assert.ok(selected._scrolled >= 1, "deep-linked row must scroll into view");

    // The zero-confirmed notice separates review candidates from the
    // excluded synthetic rows instead of claiming every row is a candidate.
    const notice = text("confirmation-notice");
    assert.ok(notice.includes("0 of 454"));
    assert.ok(notice.includes("review_candidate"));
    assert.ok(notice.includes("excluded_synthetic"));
    assert.ok(notice.includes("NOT silently accepted"));
    assert.ok(notice.includes("never candidates"));

    // Enter selects a row; Previous wraps to the last queue row and it is
    // rendered + scrolled; Next returns to the first row.
    tbody.children[0].dispatch("keydown", { key: "Enter" });
    assert.ok(text("confirmation-record-title").includes("ME 10"));
    assert.strictEqual(tbody.children[0].attributes["aria-selected"], "true");
    assert.strictEqual(selected.attributes["aria-selected"], "false");
    document.getElementById("conf-prev").dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("ME 9449"));
    assert.strictEqual(tbody.children.length, 453);
    assert.ok(tbody.children[452].className.includes("is-selected"));
    assert.ok(tbody.children[452]._scrolled >= 1);
    document.getElementById("conf-next").dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("ME 10"));

    // The synthetic row stays in the all-rows table, selectable, but never
    // appears in candidate navigation.
    const rowsFilter = document.getElementById("conf-filter-rows");
    rowsFilter.value = "all"; rowsFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 400);
    assert.strictEqual(document.getElementById("conf-more").hidden, false);
    document.getElementById("conf-more").dispatch("click", {});
    assert.strictEqual(tbody.children.length, 454);
    const syntheticRow = tbody.children[3];
    assert.ok(syntheticRow.textContent.includes("Synthetic Border"));
    syntheticRow.dispatch("click", {});
    assert.ok(text("confirmation-record-title").includes("ME 99"));
    assert.ok(!text("vanilla-candidates").includes("Synthetic"));
    assert.ok(!text("me-candidates").includes("Synthetic"));
    rowsFilter.value = "queue"; rowsFilter.dispatch("change", {});
    assert.strictEqual(tbody.children.length, 400);

    // Space also selects a keyboard-focused row.
    const spaceEvent = { key: " ", preventDefault() { this.prevented = true; } };
    tbody.children[0].dispatch("keydown", spaceEvent);
    assert.strictEqual(spaceEvent.prevented, true);
    assert.ok(text("confirmation-record-title").includes("ME 10"));
  } catch (error) {
    console.error(error.stack || error);
    process.exitCode = 1;
  }
}, 0);
'''
        completed = subprocess.run(
            [node, "-"],
            input=fixture + "\n" + long_list_setup + "\n" + source + "\n" + long_list_assertions,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
