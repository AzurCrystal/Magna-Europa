#!/usr/bin/env python
"""Audit the raw evidence behind the canonical ME state mapping.

The canonical ``m2v_states.tsv``/``v2m_states.tsv`` files remain the identity
source of truth.  This tool only reads those tables, the per-state evidence TSV,
and optional ``vps.tsv``/``regions.tsv`` tables, then writes a deterministic
triage report.  It never edits a canonical mapping or calls a renderer.

An optional ``geography_evidence.tsv`` beside the selected evidence file (or
the path passed with ``--geography``) is audited as natural-feature and optional
full-raster overlap evidence; neither family changes the canonical mapping.

The generated ``mapping_audit.tsv`` is triage, not proof.  In particular,
``clear_correction`` is emitted only when an input row contains an explicit
independent-correction marker; this script does not promote any candidate into
canonical mapping.

Usage::

    python tools/audit_state_mapping.py
    python tools/audit_state_mapping.py --evidence path/to/mapping_evidence.tsv \
        --geography path/to/geography_evidence.tsv \
        --output path/to/mapping_audit.tsv --summary path/to/mapping_audit_summary.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "docs" / "states"
DEFAULT_EVIDENCE = TABLES / "mapping_evidence.tsv"
DEFAULT_OUTPUT = TABLES / "mapping_audit.tsv"
DEFAULT_SUMMARY = TABLES / "mapping_audit_summary.json"

# These are deliberately more demanding than build_state_map.py's 50% claim
# threshold.  A low share or a narrow top/runner margin is a review signal even
# when pixel-geo was not the winning basis.
PIXEL_SHARE_THRESHOLD = 0.70
PIXEL_MARGIN_THRESHOLD = 0.20
SOFT_REASON_CODES = {"vp-absence-gap", "region-contradiction", "raw-geography-weak", "overlap-weak"}

# State-audit corrections that must stay visible as manual regression rows.
KNOWN_MANUAL_CORRECTIONS = {83, 106, 131, 2439, 2502, 2711}

# Fixed evidence contract.  Unknown/additional columns are retained in the
# audit output after these columns so future evidence can remain inspectable.
EVIDENCE_COLUMNS = (
    "me_id",
    "me_name",
    "owner",
    "me_region",
    "ancestor",
    "ancestor_name",
    "basis",
    "status",
    "manual_override",
    "pixel_best",
    "pixel_share",
    "pixel_runner",
    "pixel_runner_share",
    "pixel_margin",
    "comment_best",
    "comment_votes",
    "comment_runner",
    "comment_runner_votes",
    "comment_margin",
    "name_norm",
    "name_exact_me_ids",
    "name_collision_count",
    "vp_best",
    "vp_votes",
    "vp_runner",
    "vp_runner_votes",
    "vp_margin",
    "me_core_tags",
    "ancestor_core_tags",
    "me_vp_count",
    "ancestor_vp_count",
)
# Geography is an optional, candidate-independent natural-feature evidence
# table.  The producer may append diagnostic columns, but these columns are
# the stable contract consumed by this audit.
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
)
GEOGRAPHY_OUTPUT_COLUMNS = (
    "geography_ok",
    "geography_method",
    "geography_pairs",
    "geography_residual_p50",
    "geography_residual_p95",
    "geography_best",
    "geography_share",
    "geography_runner",
    "geography_runner_share",
    "geography_margin",
    "raw_geography_ancestor",
    "raw_geography_marker",
)
GEOGRAPHY_OUTPUT_MAP = {
    "geography_ok": "natural_ok",
    "geography_method": "natural_method",
    "geography_pairs": "natural_pairs",
    "geography_residual_p50": "natural_residual_p50",
    "geography_residual_p95": "natural_residual_p95",
    "geography_best": "natural_best",
    "geography_share": "natural_share",
    "geography_runner": "natural_runner",
    "geography_runner_share": "natural_runner_share",
    "geography_margin": "natural_margin",
    "raw_geography_ancestor": "raw_geography_ancestor",
    "raw_geography_marker": "raw_geography_marker",
}
GEOGRAPHY_ID_COLUMNS = (
    "me_id",
    "natural_best",
    "natural_runner",
    "raw_geography_ancestor",
)
GEOGRAPHY_RATIO_COLUMNS = (
    "natural_share",
    "natural_runner_share",
    "natural_margin",
)
GEOGRAPHY_NONNEGATIVE_COLUMNS = (
    "natural_pairs",
    "natural_residual_p50",
    "natural_residual_p95",
)
GEOGRAPHY_OPTIONAL_RATIO_COLUMNS = ("natural_sampled_share",)

# Optional atomic extension from the full-raster bbox coast-affine producer.
# This stays separate from natural/raw geography, including its identity marker.
OVERLAP_COLUMNS = (
    "overlap_ok", "overlap_method", "overlap_domain", "overlap_area_px",
    "overlap_best", "overlap_share", "overlap_runner", "overlap_runner_share",
    "overlap_margin", "overlap_assigned_share", "overlap_containment",
    "overlap_components", "overlap_largest_share", "overlap_largest_best",
    "overlap_largest_candidate_share", "overlap_components_agree",
    "overlap_unregistered_share", "overlap_ancestor", "overlap_marker",
    "overlap_reason",
)
OVERLAP_METHOD = "coast_bbox_affine_raster"
OVERLAP_DOMAIN = "me_native_land"
OVERLAP_MARKER = "coast_bbox_affine_raster_overlap"
OVERLAP_AREA_MIN = 64
OVERLAP_ASSIGNED_SHARE_MIN = Decimal("0.90")
OVERLAP_SHARE_MIN = Decimal("0.80")
OVERLAP_MARGIN_MIN = Decimal("0.25")
OVERLAP_ROUNDING_TOLERANCE = Decimal("0.000002")
OVERLAP_RATIO_COLUMNS = (
    "overlap_share", "overlap_runner_share", "overlap_margin",
    "overlap_assigned_share", "overlap_containment", "overlap_largest_share",
    "overlap_largest_candidate_share", "overlap_unregistered_share",
)

# These are the producer's quality gates for a candidate-independent natural
# fit.  A candidate can still be retained in the audit when it misses a gate,
# but it is explicitly marked weak rather than treated as corroboration.
NATURAL_PAIRS_MIN = 10
NATURAL_RESIDUAL_P50_MAX = 6.0
NATURAL_RESIDUAL_P95_MAX = 12.0
NATURAL_SHARE_MIN = 0.85
NATURAL_MARGIN_MIN = 0.60
NATURAL_SAMPLED_SHARE_MIN = 0.95

_UNSAFE_GEOGRAPHY_MARKER = re.compile(
    r"v2m|regions?|symmetr|visual|boundar|neighbou?r|candidate[ _-]*condition",
    re.IGNORECASE,
)
_MARKER_BOOLEAN_VALUES = {"", "0", "1", "false", "true", "no", "yes", "approved", "none", "null", "unknown", "off", "on"}

# Evidence is not currently required to have a correction field, but accepting
# only explicit, named fields lets an independently reviewed producer opt in
# without mistaking ``manual_override`` for an independent correction.
CORRECTION_MARKER_FIELDS = (
    "independent_correction",
    "independent_correction_marker",
    "explicit_independent_correction",
    "explicit_correction",
    "correction_marker",
    "clear_correction_marker",
    "clear_correction",
)

_FALSE_VALUES = {"", "0", "false", "no", "none", "null", "n", "off"}
_TRUE_VALUES = {"1", "true", "yes", "y", "on", "approved", "independent", "clear_correction"}


class AuditInputError(ValueError):
    """Raised for malformed input tables rather than silently guessing."""


def clean(value: object) -> str:
    """Return a TSV/JSON-safe scalar string."""

    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def value(row: Optional[Mapping[str, str]], key: str) -> str:
    if not row:
        return ""
    return clean(row.get(key, ""))

def normalized_name(raw: object) -> str:
    """Match build_state_map.py's NFKD/ascii/lower/no-punctuation key."""

    text = unicodedata.normalize("NFKD", clean(raw)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def parse_id(raw: object) -> Optional[int]:
    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "unmapped", "-"}:
        return None
    # ID fields are emitted as integers.  Requiring a complete token avoids
    # treating prose such as "v205 (review)" as a valid canonical ID.
    if not re.fullmatch(r"\d+", text):
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def parse_id_list(raw: object) -> List[int]:
    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "-", "[]", "{}"}:
        return []
    # Fixed evidence uses |, but commas/spaces make the reader tolerant of
    # hand-produced fixtures without relaxing the individual ID validation.
    result: List[int] = []
    for token in re.split(r"[|,;\s]+", text.strip("[]{}()")):
        if token and re.fullmatch(r"\d+", token):
            parsed = int(token)
            if parsed > 0 and parsed not in result:
                result.append(parsed)
    return result


def parse_int(raw: object) -> Optional[int]:
    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "-"}:
        return None
    if not re.fullmatch(r"-?\d+", text):
        return None
    return int(text)


def parse_ratio(raw: object) -> Optional[float]:
    """Parse a fraction, accepting a percent suffix for diagnostic fixtures."""

    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "-"}:
        return None
    match = re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)%?", text)
    if not match:
        return None
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return None
    if text.endswith("%"):
        number /= 100.0
    # Evidence shares are fractions.  A bare value above one is only accepted
    # as a percentage-like fixture when it is in a plausible percentage range.
    elif number > 1.0 and number <= 100.0:
        number /= 100.0
    return number


def parse_bool(raw: object, default: bool = False) -> bool:
    text = clean(raw).lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def parse_tags(raw: object) -> Set[str]:
    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "-", "[]", "{}"}:
        return set()
    text = text.strip("[]{}()")
    return {token for token in re.split(r"[|,;\s]+", text) if token and token.lower() not in {"none", "null"}}


def read_tsv(path: Path, required: bool = False) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        if required:
            raise AuditInputError(f"missing required TSV: {path}")
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise AuditInputError(f"TSV has no header: {path}")
        headers = [clean(header) for header in reader.fieldnames]
        if any(not header for header in headers):
            raise AuditInputError(f"TSV has an empty header: {path}")
        rows: List[Dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            # DictReader puts surplus cells under None.  They cannot be safely
            # assigned to a named evidence field, so reject rather than drop.
            if None in row and any(clean(item) for item in (row.get(None) or [])):
                raise AuditInputError(f"extra TSV cells at {path}:{row_number}")
            rows.append({header: clean(row.get(header, "")) for header in headers})
    return rows, headers


def index_rows(rows: Iterable[Mapping[str, str]], id_column: str, path: Path) -> Dict[int, Dict[str, str]]:
    indexed: Dict[int, Dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        sid = parse_id(row.get(id_column, ""))
        if sid is None:
            raise AuditInputError(f"invalid {id_column} at {path}:{row_number}: {row.get(id_column, '')!r}")
        if sid in indexed:
            raise AuditInputError(f"duplicate {id_column} {sid} in {path}")
        indexed[sid] = dict(row)
    return indexed

def _parse_nonnegative_number(raw: object) -> Optional[float]:
    text = clean(raw)
    if not text or text.lower() in {"none", "null", "unknown", "-"}:
        return None
    if not re.fullmatch(r"\+?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def geography_marker_valid(raw: object) -> bool:
    """Return whether a raw-geography citation is concrete and auditable.

    This deliberately accepts producer extensions (semicolon-separated
    tokens, with optional ``key=value`` details) while rejecting booleans,
    empty tokens, and candidate-conditioned/circular vocabulary.
    """

    marker = clean(raw)
    lowered = marker.lower()
    if lowered in _MARKER_BOOLEAN_VALUES or _UNSAFE_GEOGRAPHY_MARKER.search(marker):
        return False
    if len(marker) < 3 or not re.search(r"[a-z]", lowered, re.IGNORECASE):
        return False
    parts = marker.split(";")
    if any(not part.strip() for part in parts):
        return False
    for part in parts:
        token = part.strip()
        if "=" in token:
            key, detail = (piece.strip() for piece in token.split("=", 1))
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", key) or not detail:
                return False
        elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,/+() -]*", token):
            return False
    return True


def validate_overlap_row(row: Mapping[str, str]) -> None:
    """Reject a partial or inconsistent overlap extension, without promoting it.

    Legacy rows have no extension; a complete but empty extension means raster
    setup was unavailable. Fractions use the producer's six-decimal rounding
    tolerance, not the legacy audit reader's percentage shorthand.
    """

    if not any(field.startswith("overlap_") for field in row):
        return
    missing = [field for field in OVERLAP_COLUMNS if field not in row]
    if missing:
        raise AuditInputError("overlap extension missing columns: " + ", ".join(missing))
    if not any(value(row, field) for field in OVERLAP_COLUMNS):
        return
    for field in ("overlap_ok", "overlap_components_agree"):
        if value(row, field) not in {"0", "1"}:
            raise AuditInputError(f"invalid {field}: {value(row, field)!r}")
    if value(row, "overlap_method") != OVERLAP_METHOD or value(row, "overlap_domain") != OVERLAP_DOMAIN:
        raise AuditInputError("invalid overlap method or domain")

    counts = {}
    for field in ("overlap_area_px", "overlap_components"):
        raw = value(row, field)
        if not re.fullmatch(r"[0-9]+", raw):
            raise AuditInputError(f"invalid {field}: {raw!r}")
        counts[field] = int(raw)
    area, components = counts["overlap_area_px"], counts["overlap_components"]
    if components > area or bool(area) != bool(components):
        raise AuditInputError("inconsistent overlap area/components")

    ids = {}
    for field in ("overlap_best", "overlap_runner", "overlap_largest_best", "overlap_ancestor"):
        raw = value(row, field)
        sid = parse_id(raw)
        if raw and sid is None and not (field == "overlap_runner" and raw == "0"):
            raise AuditInputError(f"invalid {field}: {raw!r}")
        ids[field] = sid
    best, runner = ids["overlap_best"], ids["overlap_runner"]
    largest, ancestor = ids["overlap_largest_best"], ids["overlap_ancestor"]
    if runner is not None and (best is None or runner == best):
        raise AuditInputError("invalid overlap runner")

    ratios = {}
    for field in OVERLAP_RATIO_COLUMNS:
        raw = value(row, field)
        if not raw and field == "overlap_containment":
            ratios[field] = None
            continue
        if not raw and field == "overlap_runner_share" and runner is None:
            ratios[field] = Decimal(0)
            continue
        try:
            number = Decimal(raw) if re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw) else None
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite() or not Decimal(0) <= number <= Decimal(1):
            raise AuditInputError(f"invalid {field}: {raw!r}")
        ratios[field] = number
    share, runner_share, margin = (ratios[field] for field in
                                   ("overlap_share", "overlap_runner_share", "overlap_margin"))
    assigned, unregistered = ratios["overlap_assigned_share"], ratios["overlap_unregistered_share"]
    largest_share, largest_candidate_share = ratios["overlap_largest_share"], ratios["overlap_largest_candidate_share"]
    tolerance = OVERLAP_ROUNDING_TOLERANCE
    if (abs(share - runner_share - margin) > tolerance or runner_share > share
            or share + runner_share > assigned + tolerance
            or abs(assigned + unregistered - Decimal(1)) > tolerance
            or runner is None and (runner_share != 0 or abs(share - assigned) > tolerance)):
        raise AuditInputError("inconsistent overlap voting ratios")
    if best is None and (any(number != 0 for number in (share, runner_share, margin, assigned))
                         or largest is not None or ratios["overlap_containment"] not in {None, Decimal(0)}):
        raise AuditInputError("overlap voting ratios without a candidate")
    if largest is None and largest_candidate_share != 0:
        raise AuditInputError("overlap largest-component share without a candidate")
    if (area == 0 and (best is not None or largest_share != 0)
            or area > 0 and largest_share == 0
            or components == 1 and (abs(largest_share - Decimal(1)) > tolerance
                                    or largest != best or abs(largest_candidate_share - share) > tolerance)):
        raise AuditInputError("inconsistent overlap largest component")
    agrees = value(row, "overlap_components_agree") == "1"
    gates = (area >= OVERLAP_AREA_MIN and assigned >= OVERLAP_ASSIGNED_SHARE_MIN
             and share >= OVERLAP_SHARE_MIN and margin >= OVERLAP_MARGIN_MIN
             and components > 0 and agrees and best is not None and largest == best
             and largest_candidate_share >= OVERLAP_SHARE_MIN)
    marker, reason = value(row, "overlap_marker"), value(row, "overlap_reason")
    if value(row, "overlap_ok") == "1":
        if not gates or ancestor != best or marker != OVERLAP_MARKER or reason != "accepted":
            raise AuditInputError("unqualified overlap identity, acceptance gates, or unsafe marker")
    elif ancestor is not None or marker or reason not in {
        "insufficient_area", "component_disagreement", "low_assigned_share", "low_share", "low_margin",
    }:
        raise AuditInputError("withheld overlap must have a diagnostic reason and no identity/marker")


def overlap_quality_ok(row: Optional[Mapping[str, str]]) -> bool:
    """Only a fully validated producer acceptance can supply a positioning vote."""

    if not row or value(row, "overlap_ok") != "1":
        return False
    try:
        validate_overlap_row(row)
    except AuditInputError:
        return False
    return True


def overlap_candidate_ids(row: Optional[Mapping[str, str]]) -> Tuple[int, ...]:
    if not overlap_quality_ok(row):
        return ()
    ancestor = parse_id(value(row, "overlap_ancestor"))
    return (ancestor,) if ancestor is not None else ()


def validate_geography_rows(
    rows: Sequence[Mapping[str, str]], headers: Sequence[str], path: Path
) -> Dict[int, Dict[str, str]]:
    """Validate and index optional natural-geography and overlap evidence.

    Legacy natural marker safety remains a per-row audit reason. An overlap
    extension is atomic: malformed columns, diagnostics, or acceptance are input
    errors rather than a partly usable identity signal.
    """

    duplicate_headers = sorted({header for header in headers if headers.count(header) > 1})
    if duplicate_headers:
        raise AuditInputError(f"duplicate geography header(s) in {path}: {', '.join(duplicate_headers)}")
    missing = [field for field in GEOGRAPHY_COLUMNS if field not in headers]
    if missing:
        raise AuditInputError(f"geography TSV missing required columns in {path}: {', '.join(missing)}")
    if any(field.startswith("overlap_") for field in headers):
        missing_overlap = [field for field in OVERLAP_COLUMNS if field not in headers]
        if missing_overlap:
            raise AuditInputError(f"geography TSV missing overlap columns in {path}: {', '.join(missing_overlap)}")

    indexed = index_rows(rows, "me_id", path)
    for row_number, row in enumerate(rows, start=2):
        try:
            validate_overlap_row(row)
        except AuditInputError as error:
            raise AuditInputError(f"invalid overlap at {path}:{row_number}: {error}") from error
        for field in GEOGRAPHY_ID_COLUMNS[1:]:
            raw = value(row, field)
            if raw and parse_id(raw) is None and not (field == "natural_runner" and raw == "0"):
                raise AuditInputError(f"invalid geography {field} at {path}:{row_number}: {raw!r}")
        raw_ok = value(row, "natural_ok")
        if raw_ok and raw_ok.lower() not in _TRUE_VALUES | _FALSE_VALUES:
            raise AuditInputError(f"invalid geography natural_ok at {path}:{row_number}: {raw_ok!r}")
        for field in GEOGRAPHY_RATIO_COLUMNS + GEOGRAPHY_OPTIONAL_RATIO_COLUMNS:
            raw = value(row, field)
            if raw:
                ratio = parse_ratio(raw)
                if ratio is None or not 0.0 <= ratio <= 1.0:
                    raise AuditInputError(f"invalid geography ratio {field} at {path}:{row_number}: {raw!r}")
        raw_pairs = value(row, "natural_pairs")
        parsed_pairs = parse_int(raw_pairs)
        if raw_pairs and (parsed_pairs is None or parsed_pairs < 0):
            raise AuditInputError(f"invalid geography natural_pairs at {path}:{row_number}: {raw_pairs!r}")
        for field in GEOGRAPHY_NONNEGATIVE_COLUMNS[1:]:
            raw = value(row, field)
            if raw and _parse_nonnegative_number(raw) is None:
                raise AuditInputError(f"invalid geography {field} at {path}:{row_number}: {raw!r}")
    return indexed


def geography_candidate_ids(row: Optional[Mapping[str, str]]) -> Tuple[int, ...]:
    if not row:
        return ()
    candidates: List[int] = []
    for field in ("natural_best", "raw_geography_ancestor"):
        candidate = parse_id(value(row, field))
        if candidate is not None and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def geography_quality_ok(row: Optional[Mapping[str, str]]) -> bool:
    """Check the producer's natural-fit and candidate-quality gates."""

    if not row or not geography_candidate_ids(row):
        return False
    if not parse_bool(value(row, "natural_ok")):
        return False
    pairs = parse_int(value(row, "natural_pairs"))
    p50 = _parse_nonnegative_number(value(row, "natural_residual_p50"))
    p95 = _parse_nonnegative_number(value(row, "natural_residual_p95"))
    share = parse_ratio(value(row, "natural_share"))
    margin = parse_ratio(value(row, "natural_margin"))
    if pairs is None or pairs < NATURAL_PAIRS_MIN:
        return False
    if p50 is None or p50 > NATURAL_RESIDUAL_P50_MAX:
        return False
    if p95 is None or p95 > NATURAL_RESIDUAL_P95_MAX:
        return False
    if share is None or share < NATURAL_SHARE_MIN:
        return False
    if margin is None or margin < NATURAL_MARGIN_MIN:
        return False
    sampled_share = value(row, "natural_sampled_share")
    if sampled_share:
        sampled = parse_ratio(sampled_share)
        if sampled is None or sampled < NATURAL_SAMPLED_SHARE_MIN:
            return False
    return True


def find_table(name: str, evidence_path: Path) -> Path:
    """Prefer fixture tables beside custom evidence, then repository tables."""

    sibling = evidence_path.parent / name
    if sibling.exists():
        return sibling
    return TABLES / name


def detect_marker(row: Optional[Mapping[str, str]]) -> Tuple[bool, str]:
    if not row:
        return False, ""
    for field in CORRECTION_MARKER_FIELDS:
        if field not in row:
            continue
        raw = value(row, field)
        if parse_bool(raw):
            return True, raw
    return False, ""


def candidate_value(row: Optional[Mapping[str, str]], field: str) -> Optional[int]:
    return parse_id(value(row, field))


def add_reason(reasons: List[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def canonical_paths(evidence_path: Path) -> Dict[str, Path]:
    return {
        "m2v": find_table("m2v_states.tsv", evidence_path),
        "v2m": find_table("v2m_states.tsv", evidence_path),
        "vps": find_table("vps.tsv", evidence_path),
        "regions": find_table("regions.tsv", evidence_path),
    }


def load_canonical(evidence_path: Path) -> Tuple[
    Dict[int, Dict[str, str]],
    Dict[int, Dict[str, str]],
    Dict[int, Dict[str, str]],
    Dict[int, List[Dict[str, str]]],
    Dict[int, Dict[str, str]],
]:
    paths = canonical_paths(evidence_path)

    m2v_rows, _ = read_tsv(paths["m2v"], required=False)
    v2m_rows, _ = read_tsv(paths["v2m"], required=False)
    region_rows, _ = read_tsv(paths["regions"], required=False)
    vp_rows, _ = read_tsv(paths["vps"], required=False)

    m2v = index_rows(m2v_rows, "me_id", paths["m2v"]) if m2v_rows else {}
    v2m = index_rows(v2m_rows, "vanilla_id", paths["v2m"]) if v2m_rows else {}
    regions = index_rows(region_rows, "me_region", paths["regions"]) if region_rows else {}

    vps_by_me: Dict[int, List[Dict[str, str]]] = defaultdict(list)
    for row_number, row in enumerate(vp_rows, start=2):
        me_id = parse_id(row.get("me_state", ""))
        if me_id is not None:
            vps_by_me[me_id].append(dict(row))
        elif value(row, "me_state") and value(row, "status") not in {"unmapped", ""}:
            raise AuditInputError(f"invalid me_state at {paths['vps']}:{row_number}")

    return m2v, v2m, regions, dict(vps_by_me), paths


def evidence_candidate_ids(row: Optional[Mapping[str, str]]) -> Dict[str, object]:
    exact_ids = parse_id_list(value(row, "name_exact_me_ids"))
    return {
        "pixel": candidate_value(row, "pixel_best"),
        "comment": candidate_value(row, "comment_best"),
        "name_exact": exact_ids,
        "vp": candidate_value(row, "vp_best"),
    }


def state_region_conflict(
    me_region: Optional[int],
    ancestor: Optional[int],
    regions: Mapping[int, Mapping[str, str]],
    v2m: Mapping[int, Mapping[str, str]],
) -> bool:
    """Compare the ME region's modal vanilla region with ancestor's region."""

    if me_region is None or ancestor is None:
        return False
    mapped_region = regions.get(me_region)
    ancestor_row = v2m.get(ancestor)
    if not mapped_region or not ancestor_row:
        return False
    mapped_v_region = parse_id(mapped_region.get("v_region", ""))
    ancestor_region = parse_id(ancestor_row.get("region", ""))
    return mapped_v_region is not None and ancestor_region is not None and mapped_v_region != ancestor_region


def evaluate_row(
    me_id: int,
    evidence: Optional[Mapping[str, str]],
    canonical: Optional[Mapping[str, str]],
    v2m: Mapping[int, Mapping[str, str]],
    regions: Mapping[int, Mapping[str, str]],
    vps_by_me: Mapping[int, Sequence[Mapping[str, str]]],
    same_name_ids: Sequence[int],
    geography: Optional[Mapping[str, str]] = None,
    include_geography: Optional[bool] = None,
) -> Dict[str, str]:
    evidence = dict(evidence or {})
    canonical = dict(canonical or {})

    me_name = value(evidence, "me_name") or value(canonical, "me_name") or f"State {me_id}"
    ancestor = parse_id(value(evidence, "ancestor"))
    if ancestor is None:
        ancestor = parse_id(value(canonical, "v_ancestor"))
    ancestor_name = value(evidence, "ancestor_name") or (
        value(canonical, "v_name") if ancestor is not None else ""
    )
    basis = value(evidence, "basis") or value(canonical, "basis")
    status = value(evidence, "status") or value(canonical, "status")
    if not basis and ancestor is None:
        basis = "unmapped"

    manual_override = parse_bool(
        value(evidence, "manual_override"),
        default=(basis == "manual"),
    )
    marker, marker_value = detect_marker(evidence)
    candidates = evidence_candidate_ids(evidence)
    pixel_best = candidates["pixel"]
    comment_best = candidates["comment"]
    vp_best = candidates["vp"]
    exact_ids = candidates["name_exact"]
    pixel_share = parse_ratio(value(evidence, "pixel_share"))
    pixel_margin = parse_ratio(value(evidence, "pixel_margin"))
    me_vp_count = parse_int(value(evidence, "me_vp_count"))
    ancestor_vp_count = parse_int(value(evidence, "ancestor_vp_count"))
    me_region = parse_id(value(evidence, "me_region")) or parse_id(value(canonical, "region"))
    geography_enabled = geography is not None if include_geography is None else include_geography
    geography = dict(geography or {})
    geography_candidates = geography_candidate_ids(geography) if geography_enabled else ()
    geography_quality = geography_quality_ok(geography) if geography_enabled else False
    geography_marker = value(geography, "raw_geography_marker") if geography_enabled else ""
    geography_marker_ok = geography_marker_valid(geography_marker) if geography_marker else True
    overlap_candidates = overlap_candidate_ids(geography) if geography_enabled else ()
    overlap_present = geography_enabled and any(value(geography, field) for field in OVERLAP_COLUMNS)

    reasons: List[str] = []
    if evidence is None or not evidence:
        add_reason(reasons, "evidence-missing")
    if ancestor is None:
        add_reason(reasons, "ancestor-unknown")

    evidence_ancestor = parse_id(value(evidence, "ancestor"))
    canonical_ancestor = parse_id(value(canonical, "v_ancestor"))
    if evidence_ancestor is not None and canonical_ancestor is not None and evidence_ancestor != canonical_ancestor:
        add_reason(reasons, "canonical-evidence-mismatch")

    if value(evidence, "basis") and value(canonical, "basis") and value(evidence, "basis") != value(canonical, "basis"):
        add_reason(reasons, "canonical-basis-mismatch")

    if basis == "pixel-geo-low":
        add_reason(reasons, "pixel-geo-low")
    if pixel_share is not None and pixel_share < PIXEL_SHARE_THRESHOLD:
        add_reason(reasons, "pixel-share-low")
    if pixel_margin is not None and pixel_margin < PIXEL_MARGIN_THRESHOLD:
        add_reason(reasons, "pixel-margin-low")

    raw_collision_count = parse_int(value(evidence, "name_collision_count"))
    # The producer's name_exact_me_ids column contains vanilla IDs.  A
    # Kirov-like collision is instead a set of multiple ME states sharing the
    # same normalized ME name, so use the all-row aggregate as the audit
    # collision count and retain the producer value only as a fallback.
    name_collision_count = len(same_name_ids) if same_name_ids else raw_collision_count
    if (name_collision_count is not None and name_collision_count > 1) or (
        raw_collision_count is not None and raw_collision_count > 1
    ) or len(exact_ids) > 1:
        add_reason(reasons, "name-collision")
    if basis == "name-singleton":
        add_reason(reasons, "name-singleton")

    if basis == "comment(member)":
        add_reason(reasons, "comment(member)")
    if comment_best is not None and pixel_best is not None and comment_best != pixel_best:
        add_reason(reasons, "comment-vs-pixel-conflict")

    vp_rows = list(vps_by_me.get(me_id, ()))
    vp_cross_count = sum(1 for row in vp_rows if value(row, "status") == "cross-state")
    vp_usable_count = sum(1 for row in vp_rows if value(row, "status") in {"name", "near"})
    if vp_best is not None and ancestor is not None and vp_best != ancestor:
        add_reason(reasons, "vp-cross-state")
    if vp_cross_count:
        add_reason(reasons, "vp-cross-state")
    # An empty modal VP result is only a gap when raw state VP data says there
    # was something to match.  A state with zero VPs is not penalised.
    if me_vp_count is not None and me_vp_count > 0 and vp_best is None:
        add_reason(reasons, "vp-absence-gap")
    if value(evidence, "vp_best") and vp_best is None:
        add_reason(reasons, "vp-evidence-unparseable")

    me_core = parse_tags(value(evidence, "me_core_tags"))
    ancestor_core = parse_tags(value(evidence, "ancestor_core_tags"))
    if me_core and ancestor_core and me_core.isdisjoint(ancestor_core):
        add_reason(reasons, "core-disjoint")

    if state_region_conflict(me_region, ancestor, regions, v2m):
        add_reason(reasons, "region-contradiction")
    geography_conflict = ancestor is not None and any(candidate != ancestor for candidate in geography_candidates)
    if geography_conflict:
        add_reason(reasons, "raw-geography-conflict")
    if geography_candidates and not geography_quality:
        add_reason(reasons, "raw-geography-weak")
    if geography_marker and not geography_marker_ok:
        add_reason(reasons, "raw-geography-invalid")
    if geography_enabled:
        try:
            validate_overlap_row(geography)
        except AuditInputError:
            add_reason(reasons, "overlap-invalid")
        else:
            if overlap_present and not overlap_candidates:
                add_reason(reasons, "overlap-weak")
            if value(geography, "overlap_reason") == "component_disagreement":
                add_reason(reasons, "overlap-component-disagreement")
            overlap_reference = canonical_ancestor if canonical_ancestor is not None else ancestor
            if overlap_reference is not None and any(candidate != overlap_reference for candidate in overlap_candidates):
                add_reason(reasons, "overlap-conflict")


    # A manual row is allowed to agree with all automatic signals.  It is a
    # conflict only when a usable automatic candidate actively names another
    # ancestor (or when the final ancestor is unavailable).
    automatic_conflict = False
    if manual_override:
        for automatic in (pixel_best, comment_best, vp_best) + geography_candidates + overlap_candidates:
            if automatic is not None and (ancestor is None or automatic != ancestor):
                automatic_conflict = True
                break
        if exact_ids and (ancestor is None or ancestor not in exact_ids):
            automatic_conflict = True
        if automatic_conflict:
            add_reason(reasons, "manual-automatic-conflict")
    if basis == "manual" and not manual_override:
        add_reason(reasons, "manual-flag-mismatch")
    if manual_override and basis != "manual":
        add_reason(reasons, "manual-flag-mismatch")

    if me_id in KNOWN_MANUAL_CORRECTIONS:
        if basis == "manual":
            add_reason(reasons, "known-manual-correction")
        else:
            add_reason(reasons, "known-correction-not-manual")

    if marker:
        add_reason(reasons, "explicit-independent-correction")

    risk_reasons = [reason for reason in reasons
                    if reason not in SOFT_REASON_CODES
                    and reason != "explicit-independent-correction"]
    soft_reasons = [reason for reason in reasons if reason in SOFT_REASON_CODES]
    if risk_reasons:
        row_class = "review_required"
        action = "review_required"
    elif marker:
        row_class = "clear_correction"
        action = "clear_correction"
    elif soft_reasons:
        row_class = "context_only"
        action = "retain"
    else:
        row_class = "no_action"
        action = "retain"

    region_row = regions.get(me_region) if me_region is not None else None
    ancestor_region = parse_id(v2m.get(ancestor, {}).get("region", "")) if ancestor is not None else None
    mapped_v_region = parse_id(region_row.get("v_region", "")) if region_row else None

    summary_parts = [
        f"ancestor={ancestor if ancestor is not None else 'unknown'}",
        f"basis={basis or 'unknown'}",
    ]
    if pixel_best is not None:
        pixel_text = f"{pixel_best}"
        if pixel_share is not None:
            pixel_text += f"/{pixel_share:.4f}"
        if pixel_margin is not None:
            pixel_text += f"/margin={pixel_margin:.4f}"
        summary_parts.append(f"pixel={pixel_text}")
    if comment_best is not None:
        summary_parts.append(f"comment={comment_best}")
    if exact_ids:
        summary_parts.append("name_exact=" + "|".join(str(item) for item in exact_ids))
    if name_collision_count is not None:
        summary_parts.append(f"name_me_collision={name_collision_count}")
    if len(same_name_ids) > 1:
        summary_parts.append("name_me_ids=" + "|".join(str(item) for item in same_name_ids))
    if vp_best is not None:
        summary_parts.append(f"vp={vp_best}")
    elif me_vp_count is not None and me_vp_count:
        summary_parts.append(f"vp=empty/{me_vp_count}")
    if geography_candidates:
        geography_text = "|".join(str(candidate) for candidate in geography_candidates)
        geography_text += "/quality=" + ("ok" if geography_quality else "weak")
        summary_parts.append(f"raw_geography={geography_text}")
    if geography_marker:
        summary_parts.append("raw_geography_marker=" + geography_marker)
    if overlap_present:
        for field in ("overlap_ok", "overlap_best", "overlap_ancestor", "overlap_reason", "overlap_marker"):
            if value(geography, field):
                summary_parts.append(field + "=" + value(geography, field))
    if me_core or ancestor_core:
        summary_parts.append(
            "core=" + "|".join(sorted(me_core)) + " vs " + "|".join(sorted(ancestor_core))
        )
    if mapped_v_region is not None or ancestor_region is not None:
        summary_parts.append(f"region={mapped_v_region or 'unknown'}->{ancestor_region or 'unknown'}")
    if automatic_conflict:
        summary_parts.append("automatic_conflict=true")
    if reasons:
        summary_parts.append("reasons=" + "|".join(reasons))
    evidence_summary = "; ".join(summary_parts)

    output: Dict[str, str] = {
        "me_id": str(me_id),
        "me_name": me_name,
        "ancestor": str(ancestor) if ancestor is not None else "",
        "basis": basis,
        "class": row_class,
        "reason_codes": "|".join(reasons),
        "recommended_action": action,
        "automatic_conflict": "true" if automatic_conflict else "false",
        "evidence_summary": evidence_summary,
    }

    # Preserve the fixed evidence row verbatim where possible.  For a
    # canonical-only row, this leaves raw evidence cells empty and lets the
    # missing-evidence reason remain visible.
    for field in EVIDENCE_COLUMNS:
        if field in {"me_id", "me_name", "ancestor", "basis"}:
            continue
        output[field] = value(evidence, field)
    # Preserve future producer columns, but never let them overwrite the
    # required audit fields.
    for field in sorted(set(evidence) - set(EVIDENCE_COLUMNS)):
        if field not in output:
            output[field] = value(evidence, field)
    if geography_enabled:
        for field in GEOGRAPHY_OUTPUT_COLUMNS:
            output[field] = value(geography, GEOGRAPHY_OUTPUT_MAP[field])
        # Keep producer diagnostics inspectable without making them part of
        # the required audit contract (for example natural_sampled_share).
        for field in sorted(set(geography) - set(GEOGRAPHY_COLUMNS)):
            suffix = field[len("natural_"):] if field.startswith("natural_") else field
            output_field = "geography_" + suffix
            if output_field not in output:
                output[output_field] = value(geography, field)

    output.update(
        {
            "canonical_status": value(canonical, "status"),
            "canonical_basis": value(canonical, "basis"),
            "canonical_ancestor": str(canonical_ancestor) if canonical_ancestor is not None else "",
            "canonical_name": value(canonical, "v_name") or ancestor_name,
            "canonical_region": value(canonical, "region"),
            "ancestor_region": str(ancestor_region) if ancestor_region is not None else "",
            "region_vanilla": str(mapped_v_region) if mapped_v_region is not None else "",
            "vp_table_rows": str(len(vp_rows)),
            "vp_table_cross_state_rows": str(vp_cross_count),
            "vp_table_usable_rows": str(vp_usable_count),
            "name_collision_count": str(name_collision_count) if name_collision_count is not None else "",
            "name_same_me_ids": "|".join(str(item) for item in same_name_ids),
            "known_manual_correction": "true" if me_id in KNOWN_MANUAL_CORRECTIONS else "false",
            "independent_correction_marker": marker_value if marker else "",
            "pixel_share_threshold": f"{PIXEL_SHARE_THRESHOLD:.2f}",
            "pixel_margin_threshold": f"{PIXEL_MARGIN_THRESHOLD:.2f}",
        }
    )
    # Required columns use the normalized values even when evidence was absent.
    output["me_name"] = me_name
    output["ancestor"] = str(ancestor) if ancestor is not None else ""
    output["basis"] = basis
    output["status"] = status
    output["manual_override"] = "true" if manual_override else "false"
    output["ancestor_name"] = ancestor_name
    return output


def audit_rows(
    evidence_rows: Sequence[Mapping[str, str]],
    canonical_m2v: Mapping[int, Mapping[str, str]],
    canonical_v2m: Mapping[int, Mapping[str, str]],
    regions: Mapping[int, Mapping[str, str]],
    vps_by_me: Mapping[int, Sequence[Mapping[str, str]]],
    geography_by_id: Optional[Mapping[int, Mapping[str, str]]] = None,
    include_geography: Optional[bool] = None,
) -> List[Dict[str, str]]:
    evidence_by_id: Dict[int, Mapping[str, str]] = {}
    for row_number, row in enumerate(evidence_rows, start=2):
        sid = parse_id(row.get("me_id", ""))
        if sid is None:
            raise AuditInputError(f"invalid me_id at evidence row {row_number}: {row.get('me_id', '')!r}")
        if sid in evidence_by_id:
            raise AuditInputError(f"duplicate evidence me_id {sid}")
        evidence_by_id[sid] = row

    # name_exact_me_ids is the producer's vanilla candidate list.  Build the
    # separate ME-twin index here so a repeated ME name (Kirov 108/2439) is
    # audited even when each state has only one exact vanilla candidate.
    name_to_ids: Dict[str, List[int]] = defaultdict(list)
    for sid, row in evidence_by_id.items():
        key = value(row, "name_norm") or normalized_name(value(row, "me_name"))
        if key:
            name_to_ids[key].append(sid)
    same_name_by_id = {
        sid: tuple(sorted(name_to_ids[key]))
        for key in name_to_ids
        for sid in name_to_ids[key]
    }
    geography_enabled = geography_by_id is not None if include_geography is None else include_geography
    geography_by_id = geography_by_id or {}

    # The union makes a missing evidence row an explicit review item while
    # retaining the required one-row-per-canonical-ME-state invariant.
    state_ids = sorted(set(evidence_by_id) | set(canonical_m2v) | (set(geography_by_id) if geography_enabled else set()))
    return [
        evaluate_row(
            sid,
            evidence_by_id.get(sid),
            canonical_m2v.get(sid),
            canonical_v2m,
            regions,
            vps_by_me,
            same_name_by_id.get(sid, ()),
            geography_by_id.get(sid),
            geography_enabled,
        )
        for sid in state_ids
    ]


def audit_fieldnames(rows: Sequence[Mapping[str, str]]) -> List[str]:
    base = [
        "me_id",
        "me_name",
        "ancestor",
        "basis",
        "class",
        "reason_codes",
        "recommended_action",
        "automatic_conflict",
        "evidence_summary",
    ]
    fixed_raw = [field for field in EVIDENCE_COLUMNS if field not in {"me_id", "me_name", "ancestor", "basis"}]
    include_geography = any(field in row for row in rows for field in GEOGRAPHY_OUTPUT_COLUMNS)
    fixed_geography = list(GEOGRAPHY_OUTPUT_COLUMNS) if include_geography else []
    fixed_names = set(base) | set(fixed_raw) | set(fixed_geography)
    extras = sorted({field for row in rows for field in row if field not in fixed_names})
    return base + fixed_raw + fixed_geography + extras


def write_audit(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = audit_fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field, "")) for field in fields})


def reason_counter(rows: Sequence[Mapping[str, str]]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        for reason in value(row, "reason_codes").split("|"):
            if reason:
                counts[reason] += 1
    return counts

def file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary_for(
    rows: Sequence[Mapping[str, str]],
    evidence_path: Path,
    output_path: Path,
    summary_path: Path,
    canonical_paths_used: Mapping[str, Path],
    geography_path: Optional[Path] = None,
    geography_present: Optional[bool] = None,
) -> Dict[str, object]:
    by_class = Counter(value(row, "class") or "unknown" for row in rows)
    by_basis = Counter(value(row, "basis") or "unknown" for row in rows)
    by_reason = reason_counter(rows)
    geography_path = geography_path or evidence_path.parent / "geography_evidence.tsv"
    geography_present = geography_path.exists() if geography_present is None else geography_present
    geography_reasons = {
        reason: by_reason[reason]
        for reason in ("raw-geography-conflict", "raw-geography-weak", "raw-geography-invalid",
                       "overlap-conflict", "overlap-weak", "overlap-invalid", "overlap-component-disagreement")
        if by_reason[reason]
    }
    geography_manifest = {
        "path": str(geography_path),
        "present": bool(geography_present),
        "sha256": file_sha256(geography_path) if geography_present else None,
        "reason_counts": geography_reasons,
    }

    risk_rows = [row for row in rows if value(row, "class") == "review_required"]
    # More independent signals first, then manual conflicts and stable ID order.
    risk_rows.sort(
        key=lambda row: (
            -len([item for item in value(row, "reason_codes").split("|") if item]),
            -(value(row, "automatic_conflict").lower() == "true"),
            parse_id(value(row, "me_id")) or 0,
        )
    )
    top_risk = [
        {
            "me_id": parse_id(value(row, "me_id")),
            "me_name": value(row, "me_name"),
            "ancestor": parse_id(value(row, "ancestor")),
            "basis": value(row, "basis") or "unknown",
            "class": value(row, "class"),
            "reason_codes": value(row, "reason_codes"),
            "automatic_conflict": value(row, "automatic_conflict").lower() == "true",
            "evidence_summary": value(row, "evidence_summary"),
        }
        for row in risk_rows[:25]
    ]

    return {
        "schema_version": 1,
        "note": "mapping_audit.tsv is triage, not proof; no canonical mapping or review queue was modified.",
        "source": {
            "evidence": str(evidence_path),
            "output": str(output_path),
            "canonical": {name: str(path) for name, path in canonical_paths_used.items()},
            "geography": geography_manifest,
        },
        "thresholds": {
            "pixel_share_below": PIXEL_SHARE_THRESHOLD,
            "pixel_margin_below": PIXEL_MARGIN_THRESHOLD,
            "geography_pairs_below": NATURAL_PAIRS_MIN,
            "geography_residual_p50_above": NATURAL_RESIDUAL_P50_MAX,
            "geography_residual_p95_above": NATURAL_RESIDUAL_P95_MAX,
            "geography_share_below": NATURAL_SHARE_MIN,
            "geography_margin_below": NATURAL_MARGIN_MIN,
            "geography_sampled_share_below": NATURAL_SAMPLED_SHARE_MIN,
            "overlap_area_below": OVERLAP_AREA_MIN,
            "overlap_assigned_share_below": str(OVERLAP_ASSIGNED_SHARE_MIN),
            "overlap_share_below": str(OVERLAP_SHARE_MIN),
            "overlap_margin_below": str(OVERLAP_MARGIN_MIN),
        },
        "counts": {
            "states": len(rows),
            "risk_rows": len(risk_rows),
            "by_class": dict(sorted(by_class.items())),
            "by_basis": dict(sorted(by_basis.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "geography_by_reason": geography_reasons,
        },
        "top_risk_rows": top_risk,
    }


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE, help=f"raw evidence TSV (default: {DEFAULT_EVIDENCE})")
    parser.add_argument("--geography", type=Path, default=None, help="optional natural-geography TSV (default: <evidence dir>/geography_evidence.tsv)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"audit TSV (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY, help=f"summary JSON (default: {DEFAULT_SUMMARY})")
    args = parser.parse_args(argv)

    evidence_path = args.evidence.expanduser().resolve()
    geography_path = (args.geography if args.geography is not None else evidence_path.parent / "geography_evidence.tsv").expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    summary_path = args.summary.expanduser().resolve()

    evidence_rows, _ = read_tsv(evidence_path, required=True)
    geography_rows, geography_headers = read_tsv(geography_path, required=False)
    geography_by_id = validate_geography_rows(geography_rows, geography_headers, geography_path) if geography_rows or geography_path.exists() else {}
    canonical_m2v, canonical_v2m, regions, vps_by_me, paths = load_canonical(evidence_path)
    rows = audit_rows(evidence_rows, canonical_m2v, canonical_v2m, regions, vps_by_me, geography_by_id, geography_path.exists())
    write_audit(output_path, rows)
    write_summary(
        summary_path,
        summary_for(rows, evidence_path, output_path, summary_path, paths, geography_path, geography_path.exists()),
    )
    print(f"Wrote {output_path} ({len(rows)} state rows)")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, AuditInputError, ValueError) as error:
        raise SystemExit(f"Cannot audit state mapping: {error}") from error
