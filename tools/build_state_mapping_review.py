#!/usr/bin/env python
"""Build the offline, coast-registered vanilla / Magna Europa state review page.

    python tools/build_state_mapping_review.py
    python tools/build_state_mapping_review.py --vanilla "C:/path/to/Hearts of Iron IV"

Requires numpy, scipy and Pillow, as do the existing map registration tools.
The canonical TSVs are read-only inputs. No game or mod content is changed.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image

from _modroot import mod_root, repo_root
from continent_registration import DEFAULT_VANILLA, fit_coast_warp, map_points
from render_state_overlay import province_raster, state_raster
from state_geography_evidence import (
    MIN_OVERLAP_AREA, MIN_OVERLAP_ASSIGNED_SHARE, MIN_OVERLAP_MARGIN,
    MIN_OVERLAP_SHARE, OVERLAP_COLUMNS, OVERLAP_DOMAIN, OVERLAP_METHOD,
)
from build_state_mapping_confirmation import CONFIRMATION_THRESHOLD, SUMMARY_SCHEMA


METADATA_TOKEN = "__STATE_MAPPING_REVIEW_METADATA__"
MAX_STATE_ID = 0xFFFFFF
CONFIRMATION_COLUMNS = {
    "me_id", "me_name", "me_owner", "vanilla_id", "vanilla_name", "vanilla_owner",
    "relation_kind", "relation_status", "source_scope", "edge_source",
    "evidence_families", "is_canonical", "canonical_basis", "canonical_status",
    "owner_relation", "country_scope", "edge_class",
    "me_share", "me_registered_share", "me_geometry_usable",
    "v_hit_px", "v_native_px", "v_share_native",
    "me_owner_area_px", "me_owner_match_px", "me_owner_share",
    "v_owner_area_px", "v_owner_match_px", "v_owner_share",
    "family_member_count", "family_me_ids", "family_live_member_count",
    "family_has_synthetic_member", "family_min_me_share", "family_reverse_hit_px",
    "family_reverse_native_px", "family_reverse_share",
    "owner_family_member_count", "owner_family_me_ids",
    "owner_family_live_member_count", "owner_family_has_synthetic_member",
    "owner_family_min_me_owner_share",
    "owner_family_reverse_match_px", "owner_family_reverse_share",
    "mapping_audit_class", "mapping_audit_reason_codes", "comment_votes",
    "explicit_identity_evidence", "evidence_cap", "confidence_score",
    "confidence_gate", "confidence_reasons",
    "project_ref_count", "project_review_ref_count",
}
CONFIRMATION_QUEUE_COLUMNS = CONFIRMATION_COLUMNS | {"review_priority"}
CONFIRMATION_SUMMARY_SCHEMA = SUMMARY_SCHEMA
GEOGRAPHY_COLUMNS = {
    "me_id", "me_name", "natural_ok", "natural_method", "natural_pairs",
    "natural_residual_p50", "natural_residual_p95", "natural_best", "natural_share",
    "natural_runner", "natural_runner_share", "natural_margin",
    "raw_geography_ancestor", "raw_geography_marker",
}


def parse_overlap(row, vanilla_ids, line):
    """Parse the optional raster census separately from the natural evidence."""
    if not any(row[field] for field in OVERLAP_COLUMNS):
        return {}

    def invalid(message):
        raise ValueError(f"line {line}: {message}")

    parsed = {field: row[field] for field in (
        "overlap_method", "overlap_domain", "overlap_marker", "overlap_reason")}
    for field in ("overlap_ok", "overlap_components_agree"):
        if row[field] not in {"0", "1"}:
            invalid(f"{field} must be 0 or 1")
        parsed[field] = row[field] == "1"
    candidates = {"overlap_best", "overlap_runner", "overlap_largest_best", "overlap_ancestor"}
    for field in ("overlap_area_px", "overlap_components", *sorted(candidates)):
        value = row[field]
        if not value and field in candidates:
            parsed[field] = None
            continue
        if not re.fullmatch(r"[0-9]+", value):
            invalid(f"{field} must be an integer")
        value = int(value)
        if field in candidates and (not 0 < value <= MAX_STATE_ID or value not in vanilla_ids):
            invalid(f"{field} must reference a known positive 24-bit vanilla ID")
        parsed[field] = value
    for field in (
        "overlap_share", "overlap_runner_share", "overlap_margin",
        "overlap_assigned_share", "overlap_containment", "overlap_largest_share",
        "overlap_largest_candidate_share", "overlap_unregistered_share",
    ):
        if not row[field]:
            parsed[field] = None
            continue
        try:
            value = float(row[field])
        except ValueError as error:
            raise ValueError(f"line {line}: {field} must be numeric") from error
        if not math.isfinite(value) or not 0 <= value <= 1:
            invalid(f"{field} is outside its finite ratio range")
        parsed[field] = value
    if not parsed["overlap_method"] or not parsed["overlap_domain"]:
        invalid("overlap method and domain must be supplied")
    if parsed["overlap_reason"] not in {
        "accepted", "insufficient_area", "component_disagreement",
        "low_assigned_share", "low_share", "low_margin",
    } or parsed["overlap_ok"] != (parsed["overlap_reason"] == "accepted"):
        invalid("overlap_reason must agree with overlap_ok")

    best, runner, largest_best, ancestor = (parsed[field] for field in (
        "overlap_best", "overlap_runner", "overlap_largest_best", "overlap_ancestor"))
    share, runner_share, margin, assigned, containment, largest_share, largest_candidate_share, unregistered = (
        parsed[field] for field in (
            "overlap_share", "overlap_runner_share", "overlap_margin", "overlap_assigned_share",
            "overlap_containment", "overlap_largest_share", "overlap_largest_candidate_share",
            "overlap_unregistered_share"))
    if runner is not None and (best is None or runner == best or runner_share is None):
        invalid("overlap runner must differ from a supplied best and have a share")
    if runner is None and runner_share not in (None, 0):
        invalid("overlap runner share requires a runner candidate")
    if best is None and any(value not in (None, 0) for value in (share, margin, assigned, containment)):
        invalid("overlap best metrics require a best candidate")
    if best is not None and any(value is None for value in (share, margin, assigned, containment)):
        invalid("overlap best requires its share, margin, assigned share and containment")
    if largest_best is None and largest_candidate_share not in (None, 0):
        invalid("overlap largest-component candidate share requires a candidate")
    if largest_best is not None and largest_candidate_share is None:
        invalid("overlap largest-component candidate requires its share")
    # The producer writes six-decimal ratios, including zero for absent votes.
    tolerance = 0.000002
    if share is not None and (
        (runner_share or 0) > share + tolerance
        or share + (runner_share or 0) > (assigned or 0) + tolerance
        or margin is None or abs(margin - (share - (runner_share or 0))) > tolerance
    ):
        invalid("inconsistent overlap best/runner shares, margin or assigned share")

    area, components = parsed["overlap_area_px"], parsed["overlap_components"]
    if components > area or (area > 0) != (components > 0):
        invalid("overlap component count is inconsistent with its pixel area")
    if area > 0:
        if any(value is None for value in (assigned, unregistered, largest_share)):
            invalid("overlap census requires assigned, unregistered and largest-component shares")
        if abs(assigned + unregistered - 1) > tolerance:
            invalid("overlap assigned and unregistered shares must sum to one")
        if largest_share + tolerance < 1 / components or largest_share > 1 - (components - 1) / area + tolerance:
            invalid("overlap largest-component share is inconsistent with its component count")
        if components == 1 and (
            largest_best != best or abs((largest_candidate_share or 0) - (share or 0)) > tolerance
        ):
            invalid("a single overlap component must match the whole-state candidate and share")
    elif any(value not in (None, 0) for value in (
        share, runner_share, margin, assigned, containment, largest_share, largest_candidate_share, unregistered,
    )) or any(value is not None for value in (best, runner, largest_best)):
        invalid("zero overlap area cannot have candidates or nonzero shares")
    agrees = parsed["overlap_components_agree"]
    if agrees and (best is None or largest_best != best or largest_candidate_share is None
                   or largest_candidate_share < MIN_OVERLAP_SHARE):
        invalid("overlap component agreement requires the best candidate and its component share")
    if parsed["overlap_ok"]:
        if ancestor is None or ancestor != best or not parsed["overlap_marker"]:
            invalid("accepted overlap ancestor and marker must accompany the best candidate")
        if (parsed["overlap_method"] != OVERLAP_METHOD or parsed["overlap_domain"] != OVERLAP_DOMAIN
                or area < MIN_OVERLAP_AREA or assigned is None or assigned < MIN_OVERLAP_ASSIGNED_SHARE
                or share is None or share < MIN_OVERLAP_SHARE or margin is None or margin < MIN_OVERLAP_MARGIN
                or not agrees):
            invalid("accepted overlap does not meet the producer method, domain, area, coverage or component gates")
    elif ancestor is not None or parsed["overlap_marker"]:
        invalid("withheld overlap must not expose an accepted ancestor or marker")
    return parsed


def parse_confirmation_row(row, line, queue=False):
    """Type-check one confirmation row; fail closed on any bad cell.

    Identity/status/gate/score cells stay strict, but the mask-census metrics
    (unconstrained and 1936-owner-constrained shares/pixel counts, the
    canonical-family diagnostics and the owner-family reverse union) may be
    blank when a source state row was not sampled or has no usable same-owner
    denominator; blanks parse as None instead of discarding the whole overlay.
    """
    def invalid(message):
        raise ValueError(f"line {line}: {message}")

    def state_id(field):
        value = row[field]
        if not re.fullmatch(r"[0-9]+", value):
            invalid(f"{field} must be an integer")
        value = int(value)
        if not 0 < value <= MAX_STATE_ID:
            invalid(f"{field} must be a positive 24-bit state ID")
        return value

    def integer(field, *, optional=False):
        value = row[field]
        if not value and optional:
            return None
        if not re.fullmatch(r"[0-9]+", value):
            invalid(f"{field} must be an integer")
        return int(value)

    def ratio(field, *, optional=False):
        value = row[field]
        if not value and optional:
            return None
        try:
            value = float(value)
        except ValueError as error:
            raise ValueError(f"line {line}: {field} must be numeric") from error
        if not math.isfinite(value) or not 0 <= value <= 1:
            invalid(f"{field} must be a finite ratio in [0, 1]")
        return value

    status = row["relation_status"]
    if status not in {"confirmed_95", "review_candidate", "excluded_synthetic"}:
        invalid(f"unknown relation_status {status!r}")
    if queue and status != "review_candidate":
        invalid("the review queue may only contain review_candidate rows")
    kind = row["relation_kind"]
    if kind not in {"canonical", "candidate"}:
        invalid(f"unknown relation_kind {kind!r}")
    if (row["is_canonical"] == "1") != (kind == "canonical") or row["is_canonical"] not in {"0", "1"}:
        invalid("is_canonical must be 1 exactly on canonical rows")
    if status == "confirmed_95" and kind != "canonical":
        invalid("confirmed_95 can never appear on a noncanonical row")
    gate = row["confidence_gate"]
    if gate not in {"pass", "fail", "excluded"}:
        invalid(f"unknown confidence_gate {gate!r}")
    if (status == "confirmed_95") != (gate == "pass") or (status == "excluded_synthetic") != (gate == "excluded"):
        invalid("confidence_gate must agree with relation_status")

    me_id, vanilla_id = state_id("me_id"), state_id("vanilla_id")
    family_ids = []
    if row["family_me_ids"]:
        for member in row["family_me_ids"].split("|"):
            if not re.fullmatch(r"[0-9]+", member) or not 0 < int(member) <= MAX_STATE_ID:
                invalid("family_me_ids must be |-separated positive 24-bit ME IDs")
            family_ids.append(int(member))
        if len(family_ids) != len(set(family_ids)):
            invalid("family_me_ids contains a duplicate ME ID")
    member_count = integer("family_member_count")
    if member_count != len(family_ids):
        invalid("family_member_count must equal the number of family_me_ids")
    live_count = integer("family_live_member_count")
    if live_count > member_count:
        invalid("family_live_member_count cannot exceed family_member_count")
    if row["family_has_synthetic_member"] not in {"0", "1"}:
        invalid("family_has_synthetic_member must be 0 or 1")
    if row["me_geometry_usable"] not in {"0", "1"}:
        invalid("me_geometry_usable must be 0 or 1")

    # Owner relation family: the same_owner compatibility/asserted members of
    # this vanilla state; observation-scope edges never join it.
    owner_family_ids = []
    if row["owner_family_me_ids"]:
        for member in row["owner_family_me_ids"].split("|"):
            if not re.fullmatch(r"[0-9]+", member) or not 0 < int(member) <= MAX_STATE_ID:
                invalid("owner_family_me_ids must be |-separated positive 24-bit ME IDs")
            owner_family_ids.append(int(member))
        if len(owner_family_ids) != len(set(owner_family_ids)):
            invalid("owner_family_me_ids contains a duplicate ME ID")
    owner_member_count = integer("owner_family_member_count")
    if owner_member_count != len(owner_family_ids):
        invalid("owner_family_member_count must equal the number of owner_family_me_ids")
    owner_live_count = integer("owner_family_live_member_count")
    if owner_live_count > owner_member_count:
        invalid("owner_family_live_member_count cannot exceed owner_family_member_count")
    if row["owner_family_has_synthetic_member"] not in {"0", "1"}:
        invalid("owner_family_has_synthetic_member must be 0 or 1")

    parsed = {
        "me_id": me_id, "me_name": row["me_name"], "me_owner": row["me_owner"],
        "vanilla_id": vanilla_id, "vanilla_name": row["vanilla_name"],
        "vanilla_owner": row["vanilla_owner"],
        "relation_kind": kind, "relation_status": status,
        "source_scope": row["source_scope"], "edge_source": row["edge_source"],
        "evidence_families": row["evidence_families"],
        "canonical_basis": row["canonical_basis"], "canonical_status": row["canonical_status"],
        "owner_relation": row["owner_relation"], "country_scope": row["country_scope"],
        "me_geometry_usable": row["me_geometry_usable"] == "1",
        "v_hit_px": integer("v_hit_px", optional=True),
        "v_native_px": integer("v_native_px", optional=True),
        "family_member_count": member_count, "family_me_ids": family_ids,
        "family_live_member_count": live_count,
        "family_has_synthetic_member": row["family_has_synthetic_member"] == "1",
        "family_reverse_hit_px": integer("family_reverse_hit_px", optional=True),
        "family_reverse_native_px": integer("family_reverse_native_px", optional=True),
        "me_owner_area_px": integer("me_owner_area_px", optional=True),
        "me_owner_match_px": integer("me_owner_match_px", optional=True),
        "v_owner_area_px": integer("v_owner_area_px", optional=True),
        "v_owner_match_px": integer("v_owner_match_px", optional=True),
        "owner_family_member_count": owner_member_count,
        "owner_family_me_ids": owner_family_ids,
        "owner_family_live_member_count": owner_live_count,
        "owner_family_has_synthetic_member": row["owner_family_has_synthetic_member"] == "1",
        "owner_family_reverse_match_px": integer("owner_family_reverse_match_px", optional=True),
        "mapping_audit_class": row["mapping_audit_class"],
        "mapping_audit_reason_codes": row["mapping_audit_reason_codes"],
        "edge_class": row["edge_class"],
        "comment_votes": integer("comment_votes", optional=True),
        "explicit_identity_evidence": row["explicit_identity_evidence"],
        "confidence_gate": gate,
        "confidence_reasons": row["confidence_reasons"],
        "project_ref_count": integer("project_ref_count"),
        "project_review_ref_count": integer("project_review_ref_count"),
    }
    for field in (
        "me_share", "me_registered_share", "v_share_native",
        "me_owner_share", "v_owner_share",
        "family_min_me_share", "family_reverse_share",
        "owner_family_min_me_owner_share", "owner_family_reverse_share",
    ):
        parsed[field] = ratio(field, optional=True)
    for field in ("evidence_cap", "confidence_score"):
        parsed[field] = ratio(field)
    if parsed["confidence_score"] is None:
        invalid("confidence_score must be supplied")
    score = parsed["confidence_score"]
    if gate == "pass" and score < CONFIRMATION_THRESHOLD:
        invalid("a passing gate requires confidence_score at the confirmation threshold")
    if queue:
        priority = integer("review_priority")
        if priority not in {0, 1, 2, 3}:
            invalid("review_priority must be a deterministic tier 0-3")
        parsed["review_priority"] = priority
    return parsed


def read_confirmation_table(path, required, queue=False):
    """Read a confirmation TSV; every row is validated before any is kept."""
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t", strict=True)
        columns = reader.fieldnames
        if not columns or len(columns) != len(set(columns)) or not required <= set(columns):
            raise ValueError("missing required or duplicate confirmation columns")
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {line}: row width does not match the header")
            row = {key: value.strip() for key, value in row.items()}
            parsed = parse_confirmation_row(row, line, queue=queue)
            key = (parsed["me_id"], parsed["vanilla_id"])
            if key in rows:
                raise ValueError(f"line {line}: duplicate (me_id, vanilla_id) pair {key}")
            rows[key] = parsed
    return rows


def load_confirmation(main_path, summary_path, vanilla, me, warnings, queue_path=None):
    """Attach the read-only 95% confirmation overlay when its inputs exist.

    The review queue TSV is the human worklist; the full confirmation TSV also
    contributes excluded_synthetic rows for completeness. The queue sits beside
    the confirmation TSV unless queue_path overrides it. Both files and the
    optional summary are validated as a unit: a malformed file discards the
    whole overlay with a warning and never changes canonical metadata. When a
    summary is present its recorded input paths are re-hashed against the
    current repo tables; any changed or unreadable input marks the whole
    overlay stale and it is discarded the same way.
    """
    if queue_path is None and main_path is not None:
        queue_path = main_path.with_name("state_mapping_review_queue_95.tsv")
    if main_path is None or queue_path is None or not main_path.exists() or not queue_path.exists():
        return None
    try:
        queue_rows = read_confirmation_table(queue_path, CONFIRMATION_QUEUE_COLUMNS, queue=True)
        main_rows = read_confirmation_table(main_path, CONFIRMATION_COLUMNS)
        summary = None
        if summary_path is not None and summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            contract = summary.get("threshold_contract") if isinstance(summary, dict) else None
            if (not isinstance(summary, dict)
                    or summary.get("schema") != CONFIRMATION_SUMMARY_SCHEMA
                    or not isinstance(contract, dict)
                    or contract.get("confirmation_threshold") != CONFIRMATION_THRESHOLD
                    or not isinstance(summary.get("counts"), dict)
                    or not isinstance(summary.get("inputs"), dict)
                    or "state_edges_1936" not in summary["inputs"]):
                raise ValueError(
                    f"{summary_path.name} must use schema {CONFIRMATION_SUMMARY_SCHEMA} "
                    f"with a {CONFIRMATION_THRESHOLD} threshold and hashed inputs "
                    "(including state_edges_1936)")

            # Stale-safe: every recorded producer input is re-hashed against
            # the current repo file before its rows can be presented as fresh.
            stale_inputs = []
            for name, record in summary["inputs"].items():
                if not isinstance(record, dict):
                    raise ValueError(f"{summary_path.name} input {name!r} is not a record")
                recorded_path = record.get("path")
                recorded_sha = record.get("sha256")
                if not isinstance(recorded_path, str) or not recorded_path \
                        or not isinstance(recorded_sha, str) or not recorded_sha:
                    raise ValueError(
                        f"{summary_path.name} input {name!r} lacks a recorded path/sha256")
                input_path = Path(recorded_path)
                if not input_path.is_absolute():
                    input_path = Path(repo_root()) / input_path
                if not input_path.is_file() \
                        or hashlib.sha256(input_path.read_bytes()).hexdigest() != recorded_sha:
                    stale_inputs.append(name)
            if stale_inputs:
                raise ValueError(
                    "summary inputs are stale: "
                    + ", ".join(sorted(stale_inputs))
                    + " changed or are unreadable; regenerate "
                    "build_state_mapping_confirmation.py outputs first")

        # The queue is authoritative for its rows and must contain every
        # main-ledger review_candidate exactly once; cross-check it so a
        # stale, extra or missing pair can never be embedded.
        for key, parsed in main_rows.items():
            merged = dict(parsed)
            queue_row = queue_rows.pop(key, None)
            if queue_row is not None:
                if queue_row["relation_status"] != parsed["relation_status"]:
                    raise ValueError(
                        f"queue row {key} disagrees with the main confirmation status")
                merged["review_priority"] = queue_row["review_priority"]
            elif parsed["relation_status"] == "review_candidate":
                raise ValueError(f"main review_candidate {key} is missing from the review queue")
            main_rows[key] = merged
        if queue_rows:
            raise ValueError(f"{len(queue_rows)} queue rows are absent from the main confirmation TSV")
        unknown_me = sorted({key[0] for key in main_rows if key[0] not in me})
        unknown_vanilla = sorted({key[1] for key in main_rows if key[1] not in vanilla})
        if unknown_me or unknown_vanilla:
            warnings.append(
                "confirmation table references IDs outside the canonical metadata "
                f"(ME: {len(unknown_me)}, vanilla: {len(unknown_vanilla)}); the rows are kept for manual review.")
        order = {"review_candidate": 0, "confirmed_95": 1, "excluded_synthetic": 2}
        rows = [
            main_rows[key]
            for key in sorted(
                main_rows,
                key=lambda item: (
                    order[main_rows[item]["relation_status"]],
                    main_rows[item].get("review_priority", 0),
                    item,
                ),
            )
        ]
        by_status = {}
        for parsed in rows:
            by_status[parsed["relation_status"]] = by_status.get(parsed["relation_status"], 0) + 1
        counts = {
            "pairs_total": len(rows),
            "confirmed_95": by_status.get("confirmed_95", 0),
            "review_candidate": by_status.get("review_candidate", 0),
            "excluded_synthetic": by_status.get("excluded_synthetic", 0),
        }
        if summary is not None:
            expected = summary["counts"]
            for field, claimed in (
                ("pairs_total", counts["pairs_total"]),
                ("review_queue_rows", counts["review_candidate"]),
            ):
                if expected.get(field) != claimed:
                    raise ValueError(
                        f"{summary_path.name} claims {field}={expected.get(field)} "
                        f"but the embedded tables contain {claimed}")
            counts["project_refs_total"] = expected.get("project_refs_total")
            counts["project_refs_by_status"] = expected.get("project_refs_by_status")
            counts["owner_families_total"] = expected.get("owner_families_total")
            counts["owner_families_with_synthetic_member"] = expected.get(
                "owner_families_with_synthetic_member")
            counts["by_source_scope"] = expected.get("by_source_scope")
            counts["by_edge_class"] = expected.get("by_edge_class")
            counts["input_sha256"] = {
                name: record.get("sha256")
                for name, record in summary.get("inputs", {}).items()
                if isinstance(record, dict)
            }
        else:
            warnings.append(
                "state_mapping_confirmation_summary.json is absent; "
                "dashboard counts come from the confirmation TSV only.")
        return {
            "available": True,
            "schema": CONFIRMATION_SUMMARY_SCHEMA if summary is not None else None,
            "threshold": CONFIRMATION_THRESHOLD,
            "input_verified": summary is not None,
            "sources": {
                "confirmation": str(main_path),
                "queue": str(queue_path),
                "edges": (summary["inputs"]["state_edges_1936"].get("path")
                          if summary is not None else None),
            },
            "counts": counts,
            "rows": rows,
        }
    except (OSError, UnicodeError, ValueError, csv.Error, KeyError, TypeError) as error:
        warnings.append(
            f"confirmation overlay ignored: {error}. "
            "Canonical mappings and the review page are unchanged.")
        return None


def read_geography(path, vanilla_ids):
    """Validate an optional diagnostic table completely before attaching any row."""
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t", strict=True)
        columns = reader.fieldnames
        if not columns or len(columns) != len(set(columns)) or not GEOGRAPHY_COLUMNS <= set(columns):
            raise ValueError("missing required or duplicate geography columns")
        has_overlap = any(column.startswith("overlap_") for column in columns)
        if has_overlap and not set(OVERLAP_COLUMNS) <= set(columns):
            raise ValueError("incomplete overlap geography columns")
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {line}: row width does not match the header")
            row = {key: value.strip() for key, value in row.items()}

            def integer(field, *, optional=False, state_id=False):
                value = row[field]
                if not value and optional:
                    return None
                if not re.fullmatch(r"[0-9]+", value):
                    raise ValueError(f"line {line}: {field} must be an integer")
                value = int(value)
                if state_id and not 0 < value <= MAX_STATE_ID:
                    raise ValueError(f"line {line}: {field} must be a positive 24-bit state ID")
                return value

            def number(field, *, ratio=False, nonnegative=True):
                value = row[field]
                if not value:
                    return None
                try:
                    value = float(value)
                except ValueError as error:
                    raise ValueError(f"line {line}: {field} must be numeric") from error
                if not math.isfinite(value) or (nonnegative and value < 0) or (ratio and not 0 <= value <= 1):
                    raise ValueError(f"line {line}: {field} is outside its finite numeric range")
                return value

            sid = integer("me_id", state_id=True)
            if sid in rows:
                raise ValueError(f"line {line}: duplicate ME ID {sid}")
            if row["natural_ok"] not in {"0", "1"}:
                raise ValueError(f"line {line}: natural_ok must be 0 or 1")
            parsed = {field: row[field] for field in ("me_id", "me_name", "natural_method", "raw_geography_marker")}
            parsed["me_id"] = sid
            parsed["natural_ok"] = row["natural_ok"] == "1"
            parsed["natural_pairs"] = integer("natural_pairs")
            for field in ("natural_best", "natural_runner", "raw_geography_ancestor"):
                candidate = integer(field, optional=True, state_id=True)
                if candidate is not None and candidate not in vanilla_ids:
                    raise ValueError(f"line {line}: {field} references unknown vanilla ID {candidate}")
                parsed[field] = candidate
            for field in ("natural_residual_p50", "natural_residual_p95"):
                parsed[field] = number(field)
            for field in ("natural_share", "natural_runner_share", "natural_margin", "natural_sampled_share"):
                if field in row:
                    parsed[field] = number(field, ratio=True)
            for field in ("natural_center_x", "natural_center_y", "natural_radius"):
                if field in row:
                    parsed[field] = number(field, nonnegative=field == "natural_radius")
            for field in ("natural_river_pairs", "natural_lake_pairs"):
                if field in row:
                    parsed[field] = integer(field, optional=True)
            if "natural_reason" in row:
                parsed["natural_reason"] = row["natural_reason"]
            best, runner, raw = (parsed[field] for field in ("natural_best", "natural_runner", "raw_geography_ancestor"))
            share, runner_share, margin = (parsed[field] for field in ("natural_share", "natural_runner_share", "natural_margin"))
            if runner is not None and (best is None or runner == best):
                raise ValueError(f"line {line}: runner must differ from a supplied best candidate")
            if best is None and (share is not None or margin is not None):
                raise ValueError(f"line {line}: best share and margin require a best candidate")
            if best is not None and any(value is None for value in (share, runner_share, margin)):
                raise ValueError(f"line {line}: a best candidate requires all vote ratios")
            if runner is None and runner_share not in (None, 0):
                raise ValueError(f"line {line}: runner share requires a runner candidate")
            if share is not None and (runner_share > share or share + runner_share > 1.000002 or abs(margin - (share - runner_share)) > 0.000002):
                raise ValueError(f"line {line}: inconsistent best/runner shares and margin")
            sampled = parsed.get("natural_sampled_share")
            if share is not None and sampled is not None and share + runner_share > sampled + 0.000002:
                raise ValueError(f"line {line}: vote shares exceed sampled state-raster area")
            p50, p95 = parsed["natural_residual_p50"], parsed["natural_residual_p95"]
            if p50 is not None and p95 is not None and p50 > p95:
                raise ValueError(f"line {line}: residual p50 exceeds p95")
            river, lake = parsed.get("natural_river_pairs"), parsed.get("natural_lake_pairs")
            if parsed["natural_ok"]:
                if any(value is not None and value > parsed["natural_pairs"] for value in (river, lake)):
                    raise ValueError(f"line {line}: successful fit feature pairs exceed the total")
                if river is not None and lake is not None and river + lake != parsed["natural_pairs"]:
                    raise ValueError(f"line {line}: successful fit feature pairs do not sum to the total")
            if (raw is None) != (not parsed["raw_geography_marker"]) or raw is not None and raw != best:
                raise ValueError(f"line {line}: accepted raw ancestor and marker must accompany the best candidate")
            if has_overlap:
                parsed.update(parse_overlap(row, vanilla_ids, line))
            rows[sid] = parsed
    return rows


def load_states(root):
    """Read actual state IDs/provinces, ignoring filenames as an ID source."""
    states, names = {}, {}
    paths = sorted((root / "history" / "states").glob("*.txt"))
    if not paths:
        raise ValueError(f"No history/states/*.txt under {root}")
    for path in paths:
        text = re.sub(r"#[^\n]*", "", path.read_text(encoding="utf-8-sig", errors="replace"))
        match = re.search(r"\bid\s*=\s*(\d+)\b", text)
        if not match:
            raise ValueError(f"No state id in {path}")
        sid = int(match.group(1))
        if not 0 < sid <= MAX_STATE_ID or sid in states:
            raise ValueError(f"Duplicate or non-24-bit state id {sid} in {path}")
        match = re.search(r"\bprovinces\s*=\s*\{", text)
        if not match:
            raise ValueError(f"No provinces block in {path}")
        start, end, depth = match.end(), match.end(), 1
        while end < len(text) and depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        if depth:
            raise ValueError(f"Unclosed provinces block in {path}")
        states[sid] = [int(pid) for pid in re.findall(r"\b\d+\b", text[start:end - 1])]
        names[sid] = path.stem.split("-", 1)[-1].strip()
    return states, names


def load_state_map(root, states, need_continent_bbox=False):
    """Build a land-only state raster using the overlay tool's raster loaders."""
    colours, land, continent1 = {}, [], []
    with (root / "map" / "definition.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if not row or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            colours[pid] = tuple(int(value) for value in row[1:4])
            if pid > 0 and row[4].strip().lower() == "land":
                land.append(pid)
                if row[7].strip() == "1":
                    continent1.append(pid)
    if not colours or not land:
        raise ValueError(f"No land provinces in {root / 'map' / 'definition.csv'}")
    provinces = province_raster(str(root / "map" / "provinces.bmp"), colours)
    raster = state_raster(provinces, states)
    membership = np.zeros(max(colours) + 1, dtype=bool)
    membership[land] = True
    # Unknown colours (-1) are never allowed to borrow the last LUT entry.
    indices = np.maximum(provinces, 0)
    land_mask = membership[indices] & (provinces > 0)
    raster[~land_mask | (raster < 0)] = 0
    bbox = None
    if need_continent_bbox:
        membership[:] = False
        membership[continent1] = True
        mask = membership[indices] & (provinces > 0)
        rows, columns = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        if not len(rows) or not len(columns):
            raise ValueError(f"No continent=1 land pixels in {root}")
        # Half-open pixel bounds, matching NumPy crop conventions.
        bbox = (int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1)
    return raster, bbox


def read_table(path, id_column):
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            sid = int(row[id_column])
            if sid in rows or not 0 < sid <= MAX_STATE_ID:
                raise ValueError(f"Duplicate or non-24-bit {id_column} {sid} in {path}")
            rows[sid] = row
    return rows


def load_metadata(tables, vanilla_names, me_names):
    """Keep provenance from both canonical tables; never infer a new relation."""
    v_rows = read_table(tables / "v2m_states.tsv", "vanilla_id")
    m_rows = read_table(tables / "m2v_states.tsv", "me_id")
    vanilla, me, warnings = {}, {}, []
    for sid in sorted(vanilla_names.keys() | v_rows.keys()):
        row = v_rows.get(sid, {})
        members = [int(value) for value in row.get("me_members", "").split("|") if value]
        if len(members) != len(set(members)):
            raise ValueError(f"Duplicate ME members for vanilla {sid} in v2m_states.tsv")
        vanilla[sid] = {
            "id": sid, "name": row.get("vanilla_name") or vanilla_names.get(sid, f"State {sid}"),
            "basis": row.get("basis", "not-in-table"), "status": row.get("status", "unmapped"),
            "members": members, "primary": int(row["me_primary"]) if row.get("me_primary") else None,
            "visible_pixels": 0,
        }
    for sid in sorted(me_names.keys() | m_rows.keys()):
        row = m_rows.get(sid, {})
        ancestor = int(row["v_ancestor"]) if row.get("v_ancestor") else None
        me[sid] = {
            "id": sid, "name": row.get("me_name") or me_names.get(sid, f"State {sid}"),
            "ancestor": ancestor or None, "basis": row.get("basis", "not-in-table"),
            "status": row.get("status", "unmapped"), "visible_pixels": 0,
        }
    for sid, state in vanilla.items():
        for member in state["members"]:
            if member not in me or me[member]["ancestor"] != sid:
                raise ValueError(f"Canonical tables disagree on vanilla {sid} -> ME {member}; rebuild them first")
    for sid, state in me.items():
        ancestor = state["ancestor"]
        if ancestor is not None and (ancestor not in vanilla or sid not in vanilla[ancestor]["members"]):
            raise ValueError(f"Canonical tables disagree on ME {sid} -> vanilla {ancestor}; rebuild them first")
    for label, names, rows in (("Vanilla", vanilla_names, v_rows), ("ME", me_names, m_rows)):
        missing = len(names.keys() - rows.keys())
        stale = len(rows.keys() - names.keys())
        if missing:
            warnings.append(f"{label}: {missing} history states have no canonical TSV row; no mapping was inferred.")
        if stale:
            warnings.append(f"{label}: {stale} canonical TSV states have no history file and cannot appear in the raster.")
    geography_path = tables / "geography_evidence.tsv"
    try:
        geography = read_geography(geography_path, vanilla.keys())
    except FileNotFoundError:
        geography = {}
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        geography = {}
        warnings.append(f"geography_evidence.tsv diagnostics ignored: {error}. Canonical mappings are unchanged.")
    for sid, diagnostics in geography.items():
        if sid in me:
            me[sid]["geography"] = diagnostics
    return vanilla, me, warnings


def display_frame(bbox, shape, max_width, max_height):
    x0, y0, x1, y1 = bbox
    margin = max(32, round(max(x1 - x0, y1 - y0) * 0.03))
    height, width = shape
    x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
    x1, y1 = min(width, x1 + margin), min(height, y1 + margin)
    scale = min(1.0, max_width / (x1 - x0), max_height / (y1 - y0))
    out_width, out_height = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    frame = {
        "bbox_me_xyxy": [x0, y0, x1, y1],
        "crop": "ME continent=1 land bbox + margin (clipped to the ME raster)",
        "margin_me_px": margin,
        "sampling": "nearest-neighbour at display-pixel centres; zero outside the source raster",
        "me_pixels_per_display_pixel": [(x1 - x0) / out_width, (y1 - y0) / out_height],
    }
    # Registration uses integer native pixel centres, not pixel-edge coordinates.
    xs = x0 - 0.5 + (np.arange(out_width) + 0.5) * (x1 - x0) / out_width
    ys = y0 - 0.5 + (np.arange(out_height) + 0.5) * (y1 - y0) / out_height
    return frame, xs, ys


def resample_states(raster, xs, ys, registration=None):
    """Backward-sample ME display centres; vanilla uses the ME->vanilla affine."""
    result = np.zeros((len(ys), len(xs)), dtype=np.uint32)
    height, width = raster.shape
    for start in range(0, len(ys), 128):
        stop = min(start + 128, len(ys))
        xx, yy = np.meshgrid(xs, ys[start:stop])
        points = np.column_stack((xx.ravel(), yy.ravel()))
        if registration is not None:
            points = map_points(registration, points)
        pixels = np.rint(points).astype(np.int64)
        inside = (pixels[:, 0] >= 0) & (pixels[:, 0] < width) & (pixels[:, 1] >= 0) & (pixels[:, 1] < height)
        sampled = result[start:stop].reshape(-1)
        sampled[inside] = raster[pixels[inside, 1], pixels[inside, 0]]
    return result


def record_visibility(metadata, hit_map):
    ids, counts = np.unique(hit_map, return_counts=True)
    for sid, count in zip(ids.tolist(), counts.tolist()):
        if sid:
            metadata[sid]["visible_pixels"] = count


def write_images(output, prefix, hit_map):
    """Write 24-bit ID PNG + transparent, unfilled state outlines at identical size."""
    rgb = np.empty((*hit_map.shape, 3), dtype=np.uint8)
    rgb[..., 0] = (hit_map >> 16) & 255
    rgb[..., 1] = (hit_map >> 8) & 255
    rgb[..., 2] = hit_map & 255
    encoded = io.BytesIO()
    Image.fromarray(rgb).save(encoded, format="PNG")
    hit_bytes = encoded.getvalue()
    hit_name, boundary_name = f"{prefix}_hit.png", f"{prefix}_boundaries.png"
    (output / hit_name).write_bytes(hit_bytes)

    edge = np.zeros(hit_map.shape, dtype=bool)
    different = hit_map[:, 1:] != hit_map[:, :-1]
    edge[:, 1:] |= different & (hit_map[:, 1:] > 0)
    edge[:, :-1] |= different & (hit_map[:, :-1] > 0)
    different = hit_map[1:, :] != hit_map[:-1, :]
    edge[1:, :] |= different & (hit_map[1:, :] > 0)
    edge[:-1, :] |= different & (hit_map[:-1, :] > 0)
    edge[0, :] |= hit_map[0, :] > 0
    edge[-1, :] |= hit_map[-1, :] > 0
    edge[:, 0] |= hit_map[:, 0] > 0
    edge[:, -1] |= hit_map[:, -1] > 0
    outlines = np.zeros((*hit_map.shape, 4), dtype=np.uint8)
    outlines[..., :3] = (57, 64, 74)
    outlines[..., 3] = edge.astype(np.uint8) * 210
    Image.fromarray(outlines).save(output / boundary_name)
    return {
        "hit": hit_name,
        "boundary": boundary_name,
        # Relative file images can taint canvas reads in Chromium on file://.
        # A data-URI copy gives the isolated hit-test canvas an origin-clean source.
        "hit_data_uri": "data:image/png;base64," + base64.b64encode(hit_bytes).decode("ascii"),
    }


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vanilla", default=DEFAULT_VANILLA, help="vanilla game root (or HOI4_DIR)")
    parser.add_argument("--mod", help="mod root (or ME_MOD / mods/* auto-discovery)")
    parser.add_argument("--output", default=str(Path(repo_root()) / "docs/states/state_mapping_review"), help="static output directory")
    parser.add_argument("--max-width", type=positive_int, default=1800, help="maximum raster width per pane (default: 1800)")
    parser.add_argument("--max-height", type=positive_int, default=1200, help="maximum raster height per pane (default: 1200)")
    parser.add_argument("--confirmation", type=Path,
        default=Path(repo_root()) / "docs/states/state_mapping_confirmation.tsv",
        help="optional state_mapping_confirmation.tsv (embeds the 95%% review overlay when present)")
    parser.add_argument("--confirmation-queue", type=Path, default=None,
        help="optional state_mapping_review_queue_95.tsv (default: beside the selected confirmation TSV)")
    parser.add_argument("--confirmation-summary", type=Path,
        default=Path(repo_root()) / "docs/states/state_mapping_confirmation_summary.json",
        help="optional summary JSON; counts are cross-checked against the embedded rows")
    args = parser.parse_args(argv)
    vanilla_root = Path(args.vanilla).expanduser().resolve()
    me_root = Path(args.mod or mod_root()).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    tables = Path(repo_root()) / "docs" / "states"
    template = Path(__file__).with_name("state_mapping_review.html").read_text(encoding="utf-8")
    if template.count(METADATA_TOKEN) != 1:
        raise ValueError("The review HTML template must contain exactly one metadata placeholder")
    v_states, v_names = load_states(vanilla_root)
    m_states, m_names = load_states(me_root)
    vanilla, me, warnings = load_metadata(tables, v_names, m_names)
    confirmation = load_confirmation(
        args.confirmation, args.confirmation_summary, vanilla, me, warnings,
        queue_path=args.confirmation_queue)

    print("Fitting the candidate-independent continent coast affine (bbox initialisation)...", flush=True)
    registration = fit_coast_warp(str(vanilla_root), str(me_root), initial="bbox")
    affine = np.asarray(registration["affine_me_to_vanilla"], dtype=np.float64)
    if not registration["ok"] or not np.isfinite(affine).all() or not math.isfinite(registration["affine_det"]):
        raise ValueError("Coast registration did not converge to a valid affine; no review page was written")

    print("Building ME state raster and the shared display frame...", flush=True)
    m_raster, bbox = load_state_map(me_root, m_states, need_continent_bbox=True)
    frame, xs, ys = display_frame(bbox, m_raster.shape, args.max_width, args.max_height)
    me_hit = resample_states(m_raster, xs, ys)
    del m_raster
    print("Backward-sampling vanilla state raster into the ME frame...", flush=True)
    v_raster, _ = load_state_map(vanilla_root, v_states)
    vanilla_hit = resample_states(v_raster, xs, ys, registration)
    del v_raster
    record_visibility(vanilla, vanilla_hit)
    record_visibility(me, me_hit)
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1, "width": len(xs), "height": len(ys), "frame": frame,
        "sources": {
            "vanilla": str(vanilla_root), "mod": str(me_root),
            "m2v": str(tables / "m2v_states.tsv"), "v2m": str(tables / "v2m_states.tsv"),
        },
        "registration": registration, "vanilla": vanilla, "me": me,
        "confirmation": confirmation,
        "images": {"vanilla": write_images(output, "vanilla", vanilla_hit), "me": write_images(output, "me", me_hit)},
        "warnings": warnings,
    }
    payload = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # Names and paths must not be able to terminate the inert JSON script block.
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    (output / "index.html").write_text(template.replace(METADATA_TOKEN, payload), encoding="utf-8")
    print(f"Wrote {output / 'index.html'} ({len(xs)} x {len(ys)} pixels per pane)")
    print("Open index.html directly; keep its four relative PNG assets in the same directory.")
    for warning in warnings:
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot build state mapping review: {error}") from error
