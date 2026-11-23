#!/usr/bin/env python
"""Propose conservative state repairs, then apply inspected proposals to MANUAL_ME2V.

    python tools/auto_repair_state_mapping.py --propose
    python tools/auto_repair_state_mapping.py --apply
    python tools/auto_repair_state_mapping.py --propose --geography docs/states/geography_evidence.tsv

Apply consumes an existing, unchanged proposal/summary pair. It never regenerates
proposals implicitly, imports the generator, or edits a canonical TSV/review queue.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from audit_state_mapping import (
    AuditInputError,
    EVIDENCE_COLUMNS,
    KNOWN_MANUAL_CORRECTIONS,
    OVERLAP_AREA_MIN,
    OVERLAP_ASSIGNED_SHARE_MIN,
    OVERLAP_COLUMNS,
    OVERLAP_MARGIN_MIN,
    OVERLAP_SHARE_MIN,
    clean,
    index_rows,
    normalized_name,
    overlap_candidate_ids,
    parse_bool,
    parse_id,
    parse_id_list,
    parse_int,
    parse_tags,
    value,
    validate_overlap_row,
)

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "docs" / "states"
DEFAULT_EVIDENCE = TABLES / "mapping_evidence.tsv"
DEFAULT_PROPOSALS = TABLES / "auto_repair_proposals.tsv"
DEFAULT_SUMMARY = TABLES / "auto_repair_summary.json"
DEFAULT_MANUAL_SOURCE = ROOT / "tools" / "build_state_map.py"
PIXEL_SHARE_MIN = Decimal("0.80")
PIXEL_MARGIN_MIN = Decimal("0.25")
GEOGRAPHY_COLUMNS = (
    "me_id", "me_name", "natural_ok", "natural_method", "natural_pairs",
    "natural_residual_p50", "natural_residual_p95", "natural_best", "natural_share",
    "natural_runner", "natural_runner_share", "natural_margin",
    "raw_geography_ancestor", "raw_geography_marker",
)
RAW_GEOGRAPHY_FIELDS = ("raw_geography_ancestor", "raw_geography_marker")
NATURAL_PAIRS_MIN = 10
NATURAL_P50_MAX = Decimal("6")
NATURAL_P95_MAX = Decimal("12")
NATURAL_SHARE_MIN = Decimal("0.85")
NATURAL_MARGIN_MIN = Decimal("0.60")
NATURAL_SAMPLED_SHARE_MIN = Decimal("0.95")
DECIMAL_TOKEN = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
GEOGRAPHY_MARKER_FORBIDDEN = re.compile(
    r"v2m|m2v|regions?|symmetr|candidate|visual|boundar|neighbou?r|review[_ -]*queue|"
    r"victory|city|cities|(?:^|[^a-z])vps?(?:$|[^a-z])|approved|manual|canonical|"
    r"(?:^|[^a-z])(?:true|false|yes|no)(?:$|[^a-z])", re.I,
)
PROPOSAL_COLUMNS = (
    "me_id", "me_name", "current_ancestor", "current_basis", "proposed_ancestor",
    "decision", "confidence", "evidence_families", "reason_codes", "evidence_summary",
)
APPROVAL_COLUMNS = ("me_id", "approved_ancestor", "review_note")
DECISIONS = ("auto_repair", "review", "already_manual", "no_action")
FAMILIES = ("pixel", "comment", "vp", "name", "raw_geography", "overlap")
SOFT_REASONS = {"region-contradiction", "vp-absence-gap", "raw-geography-weak", "overlap-weak"}
PROTECTED_FILENAMES = {
    "m2v_states.tsv", "v2m_states.tsv", "regions.tsv", "vps.tsv",
    "registration_anchors.tsv", "mapping_evidence.tsv", "mapping_audit.tsv",
    "review_queue.tsv", "province_pairs.json", "geography_evidence.tsv",
}


class RepairError(ValueError):
    """Unsafe or stale input; no manual source edit is permitted."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def table_rows(data: bytes, path: Path, required: Sequence[str]) -> List[Dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter="\t")
    headers = reader.fieldnames or []
    if not headers or any(not header or header != clean(header) for header in headers):
        raise RepairError(f"missing or malformed TSV header: {path}")
    if len(headers) != len(set(headers)):
        raise RepairError(f"duplicate TSV column: {path}")
    missing = sorted(set(required) - set(headers))
    if missing:
        raise RepairError(f"missing columns in {path}: {', '.join(missing)}")
    rows = []
    for line, row in enumerate(reader, start=2):
        if None in row or any(item is None for item in row.values()):
            raise RepairError(f"malformed TSV row at {path}:{line}")
        rows.append({header: clean(row[header]) for header in headers})
    return rows


def approval_rows(data: bytes, path: Path) -> Dict[int, Dict[str, str]]:
    """Read only the fixed, single-line human approval contract, without cleaning unsafe text."""
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter="\t", strict=True)
    approvals = {}
    try:
        if next(reader, None) != list(APPROVAL_COLUMNS):
            raise RepairError(f"approval columns must be exactly {', '.join(APPROVAL_COLUMNS)}: {path}")
        for fields in reader:
            context = f"{path}:{reader.line_num}"
            if len(fields) != len(APPROVAL_COLUMNS):
                raise RepairError(f"malformed approval TSV row at {context}")
            if any(not char.isprintable() or char == "#" for field in fields for char in field):
                raise RepairError(f"approval values must be single-line printable text without # at {context}")
            mid_raw, ancestor_raw, note = (field.strip() for field in fields)
            if (not re.fullmatch(r"[0-9]+", mid_raw) or int(mid_raw) <= 0
                    or not re.fullmatch(r"[0-9]+", ancestor_raw) or int(ancestor_raw) <= 0):
                raise RepairError(f"approval IDs must be positive integers at {context}")
            mid = int(mid_raw)
            if mid in approvals:
                raise RepairError(f"duplicate approval me_id {mid} in {path}")
            if not note:
                raise RepairError(f"approval review_note is required for ME {mid} at {context}")
            approvals[mid] = {
                "me_id": str(mid), "approved_ancestor": str(int(ancestor_raw)), "review_note": note,
            }
    except csv.Error as error:
        raise RepairError(f"malformed approval TSV in {path}: {error}") from error
    return approvals


def comment_text(raw: str) -> str:
    """Keep evidence/name text inside one Python comment without control characters."""
    return "".join(char if char.isprintable() and char != "#" else " " for char in raw).strip()


def literal_dict(tree: ast.Module, name: str, required: bool = True) -> Optional[ast.Dict]:
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        else:
            continue
        if any(isinstance(part, ast.Name) and part.id == name
               for target in targets for part in ast.walk(target)):
            assignments.append(node)
    if not assignments and not required:
        return None
    if len(assignments) != 1:
        raise RepairError(f"expected exactly one literal {name} assignment")
    assignment = assignments[0]
    if (assignment not in tree.body or not isinstance(assignment, ast.Assign)
            or len(assignment.targets) != 1
            or not isinstance(assignment.targets[0], ast.Name)
            or not isinstance(assignment.value, ast.Dict)):
        raise RepairError(f"malformed {name}: expected a module-level literal dictionary")
    return assignment.value


def positive_literal(node: Optional[ast.AST], context: str) -> int:
    if not isinstance(node, ast.Constant) or type(node.value) is not int or node.value <= 0:
        raise RepairError(f"{context} must be a positive integer literal")
    return node.value


@dataclass
class ManualSource:
    path: Path
    data: bytes
    text: str
    encoding: str
    lines: List[str]
    entries: Dict[int, int]
    protected: Dict[int, int]
    entry_lines: List[Tuple[int, int]]
    closing_line: int
    indent: str
    comment_column: int
    newline: str

    @classmethod
    def load(cls, path: Path) -> "ManualSource":
        if path.suffix.lower() != ".py":
            raise RepairError("manual source must be a Python source file, never a canonical table")
        data = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            raise RepairError(f"malformed manual source: {error}") from error
        dictionary = literal_dict(tree, "MANUAL_ME2V")
        assert dictionary is not None
        lines = text.splitlines(keepends=True)
        end_line = dictionary.end_lineno
        if (end_line is None or dictionary.lineno == end_line
                or not re.fullmatch(r"[ \t]*MANUAL_ME2V[ \t]*=[ \t]*\{[ \t]*(?:#[^\r\n]*)?\r?\n",
                                    lines[dictionary.lineno - 1])
                or not re.fullmatch(r"[ \t]*\}[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?", lines[end_line - 1])):
            raise RepairError("MANUAL_ME2V must use its existing multiline, one-entry-per-line layout")
        entries: Dict[int, int] = {}
        entry_lines = []
        indents = set()
        comment_columns: Counter = Counter()
        for key_node, value_node in zip(dictionary.keys, dictionary.values):
            key = positive_literal(key_node, "MANUAL_ME2V key")
            ancestor = positive_literal(value_node, f"MANUAL_ME2V[{key}]")
            if key in entries:
                raise RepairError(f"duplicate MANUAL_ME2V key: {key}")
            assert key_node is not None
            line_number = key_node.lineno
            entry = lines[line_number - 1]
            match = re.fullmatch(r"([ \t]+)(\d+):[ \t]+(\d+),[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?", entry)
            if (not match or key_node.end_lineno != line_number or value_node.end_lineno != line_number
                    or (int(match[2]), int(match[3])) != (key, ancestor)):
                raise RepairError(f"unsupported MANUAL_ME2V entry layout at {path}:{line_number}")
            entries[key] = ancestor
            entry_lines.append((key, line_number - 1))
            indents.add(match[1])
            if "#" in entry:
                comment_columns[entry.index("#")] += 1
        if len(indents) > 1:
            raise RepairError("MANUAL_ME2V has inconsistent entry indentation")
        occupied = {line for _, line in entry_lines}
        for line in range(dictionary.lineno, end_line - 1):
            if line not in occupied and lines[line].strip() and not lines[line].lstrip().startswith("#"):
                raise RepairError(f"unexpected content inside MANUAL_ME2V at {path}:{line + 1}")

        # MANUAL's grouped members are manual decisions too; never shadow them.
        protected = {}
        grouped = literal_dict(tree, "MANUAL", required=False)
        if grouped is not None:
            group_keys = set()
            for key_node, members in zip(grouped.keys, grouped.values):
                ancestor = positive_literal(key_node, "MANUAL key")
                if ancestor in group_keys:
                    raise RepairError(f"duplicate MANUAL key: {ancestor}")
                group_keys.add(ancestor)
                if not isinstance(members, (ast.List, ast.Tuple)):
                    raise RepairError(f"MANUAL[{ancestor}] must be a literal member list")
                for member in members.elts:
                    mid = positive_literal(member, f"MANUAL[{ancestor}] member")
                    if mid in protected:
                        raise RepairError(f"duplicate MANUAL member: {mid}")
                    protected[mid] = ancestor
        protected.update(entries)
        newline = "\r\n" if "\r\n" in text else "\n"
        if "\r" in text.replace("\r\n", "") or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
            raise RepairError("manual source has mixed or unsupported line endings")
        indent = next(iter(indents)) if indents else "    "
        comment_column = sorted(comment_columns, key=lambda column: (-comment_columns[column], column))[0] if comment_columns else 16
        return cls(path, data, text, encoding, lines, entries, protected, entry_lines,
                   end_line - 1, indent, comment_column, newline)

    def insert(self, proposals: Sequence[Mapping[str, str]]) -> bytes:
        additions: Dict[int, List[str]] = defaultdict(list)
        inserted = {}
        for row in sorted(proposals, key=lambda item: int(item["me_id"])):
            mid = parse_id(row["me_id"])
            human = row["decision"] == "review" and bool(row.get("review_note"))
            ancestor = parse_id(row.get("approved_ancestor", "") if human else row["proposed_ancestor"])
            if mid is None or ancestor is None or not (human or row["decision"] == "auto_repair"):
                raise RepairError("apply received a malformed or unapproved repair row")
            if mid in self.protected or mid in KNOWN_MANUAL_CORRECTIONS or mid in inserted:
                raise RepairError(f"refusing to overwrite an existing or duplicate manual key: {mid}")
            inserted[mid] = ancestor
            # Existing dictionaries contain historical, locally sorted sections.
            # Preserve those sections/bytes; process new IDs in numeric order and
            # insert before the first larger existing key (or before the closer).
            gap = next((line for key, line in self.entry_lines if key > mid), self.closing_line)
            prefix = f"{self.indent}{mid}: {ancestor},"
            padding = " " * max(2, self.comment_column - len(prefix))
            if human:
                comment = (f"[human-approved] ME {mid} {comment_text(row['me_name'])}; "
                           f"current={row['current_ancestor'] or 'unknown'}; "
                           f"proposed={row['proposed_ancestor'] or 'none'}; approved={ancestor}; "
                           f"{row['approval_kind']}; note={comment_text(row['review_note'])}; "
                           f"evidence={comment_text(row['evidence_families'])}")
            else:
                comment = (f"[auto-repair] {comment_text(row['me_name'])}; "
                           f"{row['current_ancestor'] or 'unknown'} -> {ancestor}; "
                           f"evidence={comment_text(row['evidence_families'])}")
            additions[gap].append(prefix + padding + "# " + comment + self.newline)
        result = []
        for index, line in enumerate(self.lines):
            result.extend(additions.get(index, ()))
            result.append(line)
        updated = "".join(result)
        # Parse the proposed source without executing any generator code.
        tree = ast.parse(updated, filename=str(self.path))
        dictionary = literal_dict(tree, "MANUAL_ME2V")
        assert dictionary is not None
        actual = {}
        for key_node, value_node in zip(dictionary.keys, dictionary.values):
            key = positive_literal(key_node, "updated MANUAL_ME2V key")
            if key in actual:
                raise RepairError(f"duplicate key after insertion: {key}")
            actual[key] = positive_literal(value_node, f"updated MANUAL_ME2V[{key}]")
        expected = dict(self.entries)
        expected.update(inserted)
        if actual != expected:
            raise RepairError("source insertion changed entries other than the approved additions")
        return updated.encode(self.encoding)


def fraction(raw: str) -> Optional[Decimal]:
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    return number if number.is_finite() and Decimal(0) <= number <= Decimal(1) else None


def strict_ids(raw: str) -> Optional[List[int]]:
    if not raw:
        return []
    tokens = re.split(r"[|,;\s]+", raw)
    ids = [parse_id(token) for token in tokens]
    if any(item is None for item in ids) or len(ids) != len(set(ids)):
        return None
    return [item for item in ids if item is not None]


def geography_marker_safe(marker: str) -> bool:
    """Require physical sources, a local correction, and auditable numeric detail."""
    return bool(
        marker and not GEOGRAPHY_MARKER_FORBIDDEN.search(marker)
        and re.search(r"coast", marker, re.I)
        and re.search(r"river|lake", marker, re.I)
        and re.search(r"local.*(?:translation|correction|warp)", marker, re.I)
        and re.search(r"pairs?\s*[=:]\s*\d+", marker, re.I)
        and re.search(r"residual[^=;:]*[=:]\s*" + DECIMAL_TOKEN, marker, re.I)
        and re.search(r"(?:center|radius)[^=;:]*[=:]\s*" + DECIMAL_TOKEN, marker, re.I)
    )


def geography_rows(data: bytes, path: Path) -> Dict[int, Dict[str, str]]:
    """Validate the optional producer contract before accepting any identity."""
    parsed = table_rows(data, path, GEOGRAPHY_COLUMNS)
    headers = next(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter="\t"))
    try:
        validate_overlap_row(dict.fromkeys(headers, ""))
    except AuditInputError as error:
        raise RepairError(f"invalid overlap columns in {path}: {error}") from error
    rows = index_rows(parsed, "me_id", path)
    for mid, row in rows.items():
        context = f"geography evidence for ME {mid} in {path}"
        try:
            validate_overlap_row(row)
        except AuditInputError as error:
            raise RepairError(f"invalid overlap in {context}: {error}") from error
        ok = value(row, "natural_ok").lower()
        if ok not in {"0", "1", "false", "true"}:
            raise RepairError(f"invalid natural_ok in {context}")
        method = value(row, "natural_method")
        if not re.fullmatch(r"[a-z][a-z0-9_+.-]*", method):
            raise RepairError(f"invalid natural_method in {context}")
        pairs = parse_int(value(row, "natural_pairs"))
        if pairs is None or pairs < 0:
            raise RepairError(f"invalid natural_pairs in {context}")
        residuals = {}
        for field in ("natural_residual_p50", "natural_residual_p95"):
            raw = value(row, field)
            try:
                number = Decimal(raw) if re.fullmatch(DECIMAL_TOKEN, raw) else None
            except InvalidOperation:
                number = None
            if raw and (number is None or not number.is_finite()):
                raise RepairError(f"invalid {field} in {context}")
            residuals[field] = number
        p50, p95 = residuals["natural_residual_p50"], residuals["natural_residual_p95"]
        if p50 is not None and p95 is not None and p50 > p95:
            raise RepairError(f"unordered natural residuals in {context}")
        ratios = {}
        for field in ("natural_share", "natural_runner_share", "natural_margin", "natural_sampled_share"):
            raw = value(row, field)
            number = fraction(raw) if re.fullmatch(DECIMAL_TOKEN, raw) else None
            if raw and number is None:
                raise RepairError(f"invalid {field} in {context}")
            ratios[field] = number
        ids = {}
        for field in ("natural_best", "natural_runner", "raw_geography_ancestor"):
            raw = value(row, field)
            sid = parse_id(raw)
            if raw and sid is None and not (field == "natural_runner" and raw == "0"):
                raise RepairError(f"invalid {field} in {context}")
            ids[field] = sid
        best, runner = ids["natural_best"], ids["natural_runner"]
        share, runner_share, margin = (ratios[field] for field in
                                       ("natural_share", "natural_runner_share", "natural_margin"))
        if runner is not None and (best is None or runner == best):
            raise RepairError(f"invalid natural runner in {context}")
        if best is not None:
            runner_share = runner_share if runner_share is not None else Decimal(0)
            if (share is None or margin is None or runner is not None and ratios["natural_runner_share"] is None
                    or runner is None and runner_share != 0 or runner_share > share or margin > share
                    or share + runner_share > Decimal("1.000002")
                    or abs(share - runner_share - margin) > Decimal("0.000002")):
                raise RepairError(f"inconsistent natural voting ratios in {context}")
        elif any(number not in {None, Decimal(0)} for number in (share, runner_share, margin)):
            raise RepairError(f"natural voting ratios without a candidate in {context}")
        marker = value(row, "raw_geography_marker")
        ancestor = ids["raw_geography_ancestor"]
        if ancestor is not None or marker:
            sampled = ratios["natural_sampled_share"]
            if (ancestor is None or not geography_marker_safe(marker) or best != ancestor
                    or ok not in {"1", "true"} or method != "translation"
                    or pairs < NATURAL_PAIRS_MIN or p50 is None or p95 is None
                    or p50 > NATURAL_P50_MAX or p95 > NATURAL_P95_MAX
                    or share is None or share < NATURAL_SHARE_MIN
                    or margin is None or margin < NATURAL_MARGIN_MIN
                    or "natural_sampled_share" in row and (sampled is None or sampled < NATURAL_SAMPLED_SHARE_MIN)):
                raise RepairError(f"unqualified raw geography identity or unsafe marker in {context}")
    return rows


def merge_geography(evidence: Mapping[int, Mapping[str, str]], geography: Mapping[int, Mapping[str, str]],
                    path: Path) -> Dict[int, Dict[str, str]]:
    """Merge raw identity and separate overlap diagnostics, never replacing evidence."""
    merged = {mid: dict(row) for mid, row in evidence.items()}
    for mid, row in geography.items():
        if mid not in merged:
            raise RepairError(f"geography ME ID {mid} absent from evidence: {path}")
        raw = merged[mid]
        if value(row, "me_name") and value(raw, "me_name") and value(row, "me_name") != value(raw, "me_name"):
            raise RepairError(f"geography/evidence name mismatch for ME {mid}: {path}")
        for field in RAW_GEOGRAPHY_FIELDS + tuple(field for field in OVERLAP_COLUMNS if field in row):
            incoming = value(row, field)
            if field in raw and value(raw, field) != incoming and (incoming or field in OVERLAP_COLUMNS):
                raise RepairError(f"geography would overwrite existing {field} for ME {mid}: {path}")
            raw.setdefault(field, incoming)
    return merged


def vote_candidate(row: Mapping[str, str], family: str, reasons: Set[str]) -> Optional[int]:
    raw = value(row, family + "_best")
    if not raw:
        return None
    best = parse_id(raw)
    votes = parse_int(value(row, family + "_votes"))
    runner_raw = value(row, family + "_runner")
    runner = parse_id(runner_raw)
    runner_votes = parse_int(value(row, family + "_runner_votes")) if runner_raw else 0
    margin = parse_int(value(row, family + "_margin"))
    if (best is None or votes is None or votes <= 0 or runner_votes is None or runner_votes < 0
            or (runner_raw and (runner is None or runner == best))
            or margin is None or margin != votes - runner_votes):
        reasons.add(family + "-evidence-invalid")
        return None
    if margin <= 0:
        reasons.add(family + "-vote-tie")
        return None
    return best


def independent_pair(families: Sequence[str]) -> str:
    present = set(families)
    # The pixel warp is VP-derived. Pixel+VP alone is not independent proof.
    if "pixel" in present:
        for other in ("comment", "name", "raw_geography"):
            if other in present:
                return "pixel+" + other
    for direct in ("name", "vp"):
        if direct in present:
            for other in ("comment", "name", "vp"):
                if other in present and other != direct:
                    return direct + "+" + other
    # VP identity alone cannot corroborate a natural-positioning repair.
    if "comment" in present and "raw_geography" in present:
        return "comment+raw_geography"
    if "name" in present and "raw_geography" in present:
        return "name+raw_geography"
    # Overlap shares the positioning source of pixel/raw geography; neither it
    # nor VP positioning can independently corroborate another positioning vote.
    for direct in ("comment", "name"):
        if direct in present and "overlap" in present:
            return direct + "+overlap"
    return ""


def proposal_rows(evidence: Mapping[int, Mapping[str, str]], audit: Mapping[int, Mapping[str, str]],
                  canonical: Mapping[int, Mapping[str, str]], vanilla_ids: Set[int],
                  source: ManualSource) -> List[Dict[str, str]]:
    state_ids = sorted(set(evidence) | set(audit) | set(canonical) | set(source.protected))
    names: Dict[str, List[int]] = defaultdict(list)
    for mid in state_ids:
        raw_name = value(evidence.get(mid), "me_name") or value(canonical.get(mid), "me_name")
        key = normalized_name(raw_name)
        if key:
            names[key].append(mid)
    proposals = []
    for mid in state_ids:
        raw, checked, current = evidence.get(mid, {}), audit.get(mid, {}), canonical.get(mid, {})
        me_name = value(raw, "me_name") or value(current, "me_name") or value(checked, "me_name") or f"State {mid}"
        ancestor = parse_id(value(current, "v_ancestor"))
        basis = value(current, "basis")
        reasons = {reason for reason in value(checked, "reason_codes").split("|") if reason}
        blockers: Set[str] = set()
        for table, row in (("evidence", raw), ("audit", checked), ("canonical", current)):
            if not row:
                blockers.add(table + "-missing")
        if ancestor is None:
            reasons.add("ancestor-unknown")
        if raw and (parse_id(value(raw, "ancestor")) != ancestor or value(raw, "basis") != basis):
            blockers.add("canonical-evidence-mismatch")
        if checked and (parse_id(value(checked, "ancestor")) != ancestor or value(checked, "basis") != basis):
            blockers.add("canonical-audit-mismatch")
        if raw and current and value(raw, "me_name") != value(current, "me_name"):
            blockers.add("canonical-name-mismatch")
        manual = (mid in source.protected or mid in KNOWN_MANUAL_CORRECTIONS or basis == "manual"
                  or any(value(row, "basis") == "manual" or parse_bool(value(row, "manual_override"))
                         or parse_bool(value(row, "known_manual_correction")) for row in (raw, checked)))
        if mid in source.protected and ancestor != source.protected[mid]:
            reasons.add("manual-source-canonical-mismatch")

        signals: Dict[str, int] = {}
        eligible: Dict[str, int] = {}
        pixel = parse_id(value(raw, "pixel_best"))
        share = fraction(value(raw, "pixel_share"))
        margin = fraction(value(raw, "pixel_margin"))
        strong_pixel = False
        if value(raw, "pixel_best"):
            runner = parse_id(value(raw, "pixel_runner"))
            runner_share = fraction(value(raw, "pixel_runner_share")) if value(raw, "pixel_runner") else Decimal(0)
            if (pixel is None or share is None or margin is None or runner_share is None
                    or (value(raw, "pixel_runner") and (runner is None or runner == pixel))
                    or margin > share or runner_share > share
                    or abs((share - runner_share) - margin) > Decimal("0.000002")):
                blockers.add("pixel-evidence-invalid")
            else:
                signals["pixel"] = pixel
                strong_pixel = share >= PIXEL_SHARE_MIN and margin >= PIXEL_MARGIN_MIN
                if strong_pixel:
                    eligible["pixel"] = pixel
        local_reasons: Set[str] = set()
        for family in ("comment", "vp"):
            candidate = vote_candidate(raw, family, local_reasons)
            if candidate is not None:
                signals[family] = eligible[family] = candidate
        blockers.update(local_reasons)
        exact = strict_ids(value(raw, "name_exact_me_ids"))
        if exact is None:
            blockers.add("name-evidence-invalid")
        elif len(exact) == 1:
            signals["name"] = eligible["name"] = exact[0]
        elif len(exact) > 1:
            blockers.add("name-collision")
        name_key = normalized_name(me_name)
        if (len(names.get(name_key, ())) > 1
                or any((parse_int(value(row, "name_collision_count")) or 0) > 1 for row in (raw, checked))
                or len(parse_id_list(value(checked, "name_same_me_ids"))) > 1):
            blockers.add("name-collision")

        # Optional producer extension: a candidate ID plus a textual citation to
        # raw geography, never a generic correction boolean or fitted boundary.
        geo_raw, marker = value(raw, "raw_geography_ancestor"), value(raw, "raw_geography_marker")
        if geo_raw or marker:
            geo = parse_id(geo_raw)
            if geo is None or not geography_marker_safe(marker):
                blockers.add("raw-geography-marker-invalid")
            else:
                signals["raw_geography"] = eligible["raw_geography"] = geo
        # Keep the full raster family distinct: diagnostic bests are not votes,
        # and its marker must never acquire raw-geography identity semantics.
        try:
            validate_overlap_row(raw)
        except AuditInputError:
            blockers.add("overlap-invalid")
        else:
            overlap = overlap_candidate_ids(raw)
            if overlap:
                signals["overlap"] = eligible["overlap"] = overlap[0]
                if overlap[0] != ancestor:
                    reasons.add("overlap-conflict")
                    if basis.startswith(("comment", "name", "vp")) or basis == "same-id":
                        blockers.add("existing-identity-mapping")
            elif any(value(raw, field) for field in OVERLAP_COLUMNS):
                reasons.add("overlap-weak")
            if value(raw, "overlap_reason") == "component_disagreement":
                reasons.add("overlap-component-disagreement")
        if pixel is not None and parse_id(value(raw, "comment_best")) not in {None, pixel}:
            blockers.add("comment-vs-pixel-conflict")
        vp_best = parse_id(value(raw, "vp_best"))
        if (vp_best is not None and ancestor is not None and vp_best != ancestor
                or (parse_int(value(checked, "vp_table_cross_state_rows")) or 0) > 0
                or (parse_id(value(raw, "vp_runner")) is not None
                    and (parse_int(value(raw, "vp_runner_votes")) or 0) > 0)):
            blockers.add("vp-cross-state")
        me_core, ancestor_core = parse_tags(value(raw, "me_core_tags")), parse_tags(value(raw, "ancestor_core_tags"))
        if me_core and ancestor_core and me_core.isdisjoint(ancestor_core):
            blockers.add("core-disjoint")
        blockers.update(reasons & {"name-collision", "vp-cross-state", "comment-vs-pixel-conflict", "core-disjoint",
                                   "canonical-evidence-mismatch", "canonical-basis-mismatch", "vp-evidence-unparseable",
                                   "overlap-invalid", "overlap-component-disagreement"})
        if any(candidate not in vanilla_ids for candidate in signals.values()):
            blockers.add("candidate-not-vanilla")
        if len(set(signals.values())) > 1:
            blockers.add("evidence-family-conflict")

        support: Dict[int, List[str]] = defaultdict(list)
        for family in FAMILIES:
            if family in eligible:
                support[eligible[family]].append(family)
        ranked = sorted(support, key=lambda candidate: (-len(support[candidate]), candidate))
        proposed = None
        if ranked:
            if len(ranked) == 1 or len(support[ranked[0]]) > len(support[ranked[1]]):
                proposed = ranked[0]
            else:
                blockers.add("candidate-tie")
        elif len(set(signals.values())) == 1:
            proposed = next(iter(signals.values()))
        agreeing = [family for family in FAMILIES if signals.get(family) == proposed] if proposed is not None else []
        qualified = support.get(proposed, []) if proposed is not None else []
        pair = independent_pair(qualified)
        reasons.update(blockers)
        alternate = proposed is not None and proposed != ancestor
        risk = (bool(blockers) or value(checked, "class") in {"review_required", "clear_correction"}
                or bool(reasons - SOFT_REASONS - {"explicit-independent-correction"}) or alternate
                or "raw_geography" in qualified and not pair)
        if manual:
            decision, confidence = "already_manual", "manual"
            reasons.add("existing-manual")
        elif alternate and proposed in vanilla_ids and not blockers and len(qualified) >= 2 and pair:
            decision, confidence = "auto_repair", "high"
            reasons.add("independent-evidence-agrees")
        elif risk:
            decision, confidence = "review", "low"
            if alternate:
                reasons.add("candidate-differs-current")
            if len(qualified) < 2:
                reasons.add("insufficient-independent-evidence")
            elif not pair:
                if set(qualified) == {"pixel", "vp"}:
                    reasons.add("pixel-vp-dependent")
                elif set(qualified) == {"raw_geography", "vp"}:
                    reasons.add("geography-vp-insufficient")
                elif "overlap" in qualified:
                    reasons.add("overlap-positioning-dependent")
                else:
                    reasons.add("direct-identity-or-strong-pixel-required")
            if "pixel" in signals and not strong_pixel:
                reasons.add("pixel-below-auto-threshold")
        else:
            decision, confidence = "no_action", "unchanged"
            reasons.add("retained-current-ancestor")
        summary = [f"ancestor={ancestor or 'unknown'}", f"basis={basis or 'unknown'}"]
        summary.extend(f"{family}={signals[family]}" for family in FAMILIES if family in signals)
        if pixel is not None:
            summary.append(f"pixel_share={value(raw, 'pixel_share') or 'unknown'}/margin={value(raw, 'pixel_margin') or 'unknown'}")
        summary.append("qualified=" + ("|".join(qualified) or "none"))
        if pair:
            summary.append("independent_pair=" + pair)
        if set(agreeing) == {"pixel", "vp"}:
            summary.append("pixel+vp share VP-derived positioning; not independent")
        if marker:
            summary.append("raw_geography_marker=" + marker)
        summary.extend(field + "=" + value(raw, field) for field in OVERLAP_COLUMNS if value(raw, field))
        if name_key and len(names.get(name_key, ())) > 1:
            summary.append("same_name_me_ids=" + "|".join(map(str, names[name_key])))
        summary.append("audit_class=" + (value(checked, "class") or "missing"))
        proposals.append({
            "me_id": str(mid), "me_name": me_name,
            "current_ancestor": str(ancestor) if ancestor is not None else "",
            "current_basis": basis, "proposed_ancestor": str(proposed) if proposed is not None else "",
            "decision": decision, "confidence": confidence, "evidence_families": "|".join(agreeing),
            "reason_codes": "|".join(sorted(reasons)), "evidence_summary": "; ".join(summary),
        })
    return proposals


def approved_rows(approvals: Mapping[int, Mapping[str, str]], proposals: Sequence[Mapping[str, str]],
                  vanilla_ids: Set[int], source: ManualSource) -> List[Dict[str, str]]:
    """Join approvals only to recomputed proposals, never to human-edited proposal bytes."""
    indexed = {int(row["me_id"]): row for row in proposals}
    approved = []
    for mid in sorted(approvals):
        approval = approvals[mid]
        row = indexed.get(mid)
        if row is None:
            raise RepairError(f"approval ME {mid} is absent from the proposals")
        if (mid in source.protected or mid in KNOWN_MANUAL_CORRECTIONS
                or row["current_basis"] == "manual" or row["decision"] == "already_manual"):
            raise RepairError(f"approval ME {mid} is an existing protected manual decision")
        if row["decision"] != "review":
            raise RepairError(f"approval ME {mid} must have a review proposal, not {row['decision']}")
        ancestor = int(approval["approved_ancestor"])
        if ancestor not in vanilla_ids:
            raise RepairError(f"approval ME {mid} has unknown vanilla ancestor {ancestor}")
        if ancestor == parse_id(row["current_ancestor"]):
            raise RepairError(f"approval ME {mid} would retain the current ancestor {ancestor}; no-op refused")
        kind = "normal_approval" if ancestor == parse_id(row["proposed_ancestor"]) else "manual_candidate_override"
        approved.append({**row, **approval, "approval_kind": kind})
    return approved


def proposal_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PROPOSAL_COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def json_bytes(summary: Mapping[str, object]) -> bytes:
    return (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def optional_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def require_unchanged(path: Path, expected: Optional[bytes]) -> None:
    if optional_bytes(path) != expected:
        raise RepairError(f"source/input changed since proposal generation: {path}; run --propose and inspect again")


def apply_source(source: ManualSource, updated: bytes) -> Path:
    backup = source.path.with_name(source.path.name + ".bak")
    if backup.exists():
        raise RepairError(f"backup already exists; preserve/move it before applying another batch: {backup}")
    require_unchanged(source.path, source.data)
    fd, temporary = tempfile.mkstemp(prefix="." + source.path.name + ".auto-repair-", suffix=".tmp", dir=str(source.path.parent))
    temporary_path = Path(temporary)
    backup_created = False
    replaced = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, stat.S_IMODE(source.path.stat().st_mode))
        require_unchanged(source.path, source.data)
        with backup.open("xb") as handle:
            backup_created = True
            handle.write(source.data)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck immediately before replacement, including after backup I/O.
        require_unchanged(source.path, source.data)
        os.replace(temporary_path, source.path)
        replaced = True
        return backup
    finally:
        temporary_path.unlink(missing_ok=True)
        if backup_created and not replaced:
            backup.unlink(missing_ok=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--propose", action="store_true", help="generate proposals and a source/input hash manifest")
    action.add_argument("--apply", action="store_true", help="apply unchanged auto_repair proposals plus explicit human approvals")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--geography", type=Path,
                        help="optional geography_evidence.tsv (default: beside evidence; absence is recorded)")
    parser.add_argument("--approvals", type=Path,
                        help="optional human-edited approval TSV after proposal review; rerun --propose with it before --apply (default: absent; never created)")
    parser.add_argument("--audit", type=Path, help="audit TSV (default: beside evidence)")
    parser.add_argument("--canonical", "--m2v", type=Path, help="m2v_states.tsv (default: beside evidence)")
    parser.add_argument("--vanilla-states", type=Path, help="v2m_states.tsv; vanilla_id registry only (default: beside evidence)")
    parser.add_argument("--manual-source", type=Path, default=DEFAULT_MANUAL_SOURCE)
    parser.add_argument("--proposals", "--output", type=Path, default=DEFAULT_PROPOSALS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)
    evidence_path = args.evidence.expanduser().resolve()
    paths = {
        "evidence": evidence_path,
        "audit": (args.audit or evidence_path.parent / "mapping_audit.tsv").expanduser().resolve(),
        "canonical": (args.canonical or evidence_path.parent / "m2v_states.tsv").expanduser().resolve(),
        "vanilla_states": (args.vanilla_states or evidence_path.parent / "v2m_states.tsv").expanduser().resolve(),
        "geography": (args.geography or evidence_path.parent / "geography_evidence.tsv").expanduser().resolve(),
    }
    manual_path = args.manual_source.expanduser().resolve()
    proposals_path = args.proposals.expanduser().resolve()
    summary_path = args.summary.expanduser().resolve()
    approvals_path = args.approvals.expanduser().resolve() if args.approvals is not None else None
    protected_paths = set(paths.values()) | {manual_path, manual_path.with_name(manual_path.name + ".bak")}
    if approvals_path is not None:
        if (approvals_path in protected_paths
                or approvals_path.exists() and any(path.exists() and os.path.samefile(approvals_path, path)
                                                   for path in protected_paths)):
            raise RepairError("approvals must be separate from all source/input/backup files")
        protected_paths.add(approvals_path)
    if proposals_path == summary_path:
        raise RepairError("proposals and summary must be different files")
    for output in (proposals_path, summary_path):
        if output in protected_paths or output.name.lower() in PROTECTED_FILENAMES:
            raise RepairError(f"refusing to overwrite a canonical/input/manual file: {output}")
        if output.exists() and any(path.exists() and os.path.samefile(output, path) for path in protected_paths):
            raise RepairError(f"output aliases a protected input/manual file: {output}")
    for key, path in paths.items():
        if path == manual_path:
            raise RepairError(f"manual source aliases {key} input")

    # Apply must not generate or silently replace its own missing proposal.
    saved_summary = None
    saved_proposals = None
    if args.apply:
        if not proposals_path.is_file() or not summary_path.is_file():
            raise RepairError("--apply requires existing proposals and summary; run --propose, then inspect them")
        saved_summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        if not isinstance(saved_summary, dict) or saved_summary.get("schema_version") != 1 or saved_summary.get("mode") != "propose":
            raise RepairError("invalid or already-applied proposal summary; run --propose and inspect again")

    source = ManualSource.load(manual_path)
    inputs = {key: path.read_bytes() for key, path in paths.items() if key != "geography"}
    geography_data = optional_bytes(paths["geography"])
    approvals_data = optional_bytes(approvals_path) if approvals_path is not None else None
    approvals_manifest = {
        "path": str(approvals_path) if approvals_path is not None else None,
        "sha256": sha256(approvals_data) if approvals_data is not None else None,
        "optional": True, "status": "present" if approvals_data is not None else "absent",
    }
    snapshots = {**inputs, "geography": geography_data}
    manifest = {key: {"path": str(paths[key]), "sha256": sha256(data)} for key, data in inputs.items()}
    manifest["geography"] = {
        "path": str(paths["geography"]), "sha256": sha256(geography_data) if geography_data is not None else None,
        "optional": True, "status": "present" if geography_data is not None else "absent",
    }
    source_manifest = {"path": str(manual_path), "sha256": sha256(source.data)}
    if saved_summary is not None:
        if saved_summary.get("manual_source") != source_manifest or saved_summary.get("inputs") != manifest:
            raise RepairError("stale source/input hash; run --propose and inspect again before --apply")
        if saved_summary.get("approvals") != approvals_manifest:
            raise RepairError("stale approvals path/content/presence; run --propose with the reviewed approvals and inspect again")

    evidence = index_rows(table_rows(inputs["evidence"], paths["evidence"], EVIDENCE_COLUMNS), "me_id", paths["evidence"])
    audit = index_rows(table_rows(inputs["audit"], paths["audit"], ("me_id", "ancestor", "basis", "class", "reason_codes")), "me_id", paths["audit"])
    canonical = index_rows(table_rows(inputs["canonical"], paths["canonical"], ("me_id", "me_name", "v_ancestor", "basis")), "me_id", paths["canonical"])
    # Do not read me_members, me_primary, regions, or any reverse-map symmetry.
    vanilla = index_rows(table_rows(inputs["vanilla_states"], paths["vanilla_states"], ("vanilla_id",)), "vanilla_id", paths["vanilla_states"])
    if not vanilla:
        raise RepairError("vanilla ancestor registry is empty")
    if any(value(row, "class") not in {"review_required", "clear_correction", "context_only", "no_action"} for row in audit.values()):
        raise RepairError("unrecognized audit classification")
    geography = geography_rows(geography_data, paths["geography"]) if geography_data is not None else {}
    if geography:
        evidence = merge_geography(evidence, geography, paths["geography"])
    rows = proposal_rows(evidence, audit, canonical, set(vanilla), source)
    generated = proposal_bytes(rows)
    if args.apply:
        saved_proposals = proposals_path.read_bytes()
        if saved_summary.get("proposals") != {"path": str(proposals_path), "sha256": sha256(saved_proposals)}:
            raise RepairError("proposal file changed since generation; run --propose and inspect again")
        loaded = table_rows(saved_proposals, proposals_path, PROPOSAL_COLUMNS)
        if list(csv.DictReader(io.StringIO(saved_proposals.decode("utf-8-sig")), delimiter="\t").fieldnames or []) != list(PROPOSAL_COLUMNS):
            raise RepairError("proposal columns do not match the fixed schema")
        index_rows(loaded, "me_id", proposals_path)
        if loaded != rows or saved_proposals != generated:
            raise RepairError("proposals no longer match deterministic current evidence decisions; regenerate and inspect")

    approvals = approval_rows(approvals_data, approvals_path) if approvals_data is not None else {}
    approved = approved_rows(approvals, rows, set(vanilla), source)
    approved_ids = {int(row["me_id"]) for row in approved}

    counts = Counter(row["decision"] for row in rows)
    skipped = {row["me_id"]: row["reason_codes"].split("|") for row in rows
               if row["decision"] != "auto_repair" and int(row["me_id"]) not in approved_ids}
    summary: Dict[str, object] = {
        "schema_version": 1, "mode": "propose", "manual_source": source_manifest,
        "inputs": manifest, "proposals": {"path": str(proposals_path), "sha256": sha256(generated)},
        "approvals": approvals_manifest,
        "approved_ids": sorted(approved_ids),
        "approval_decisions": [
            {"me_id": int(row["me_id"]), "current_ancestor": parse_id(row["current_ancestor"]),
             "proposed_ancestor": parse_id(row["proposed_ancestor"]), "approved_ancestor": int(row["approved_ancestor"]),
             "decision": row["approval_kind"], "review_note": row["review_note"]} for row in approved
        ],
        "thresholds": {
            "pixel_share_min": str(PIXEL_SHARE_MIN), "pixel_margin_min": str(PIXEL_MARGIN_MIN),
            "natural_pairs_min": NATURAL_PAIRS_MIN, "natural_residual_p50_max": str(NATURAL_P50_MAX),
            "natural_residual_p95_max": str(NATURAL_P95_MAX), "natural_share_min": str(NATURAL_SHARE_MIN),
            "natural_margin_min": str(NATURAL_MARGIN_MIN), "natural_sampled_share_min": str(NATURAL_SAMPLED_SHARE_MIN),
            "overlap_area_min": OVERLAP_AREA_MIN, "overlap_assigned_share_min": str(OVERLAP_ASSIGNED_SHARE_MIN),
            "overlap_share_min": str(OVERLAP_SHARE_MIN), "overlap_margin_min": str(OVERLAP_MARGIN_MIN),
        },
        "counts": {
            "states": len(rows), "by_decision": {decision: counts[decision] for decision in DECISIONS}, "applied": 0,
            "approved": len(approved),
            "geography_rows": len(geography),
            "geography_qualified": sum(bool(value(row, "raw_geography_ancestor")) for row in geography.values()),
            "overlap_qualified": sum(bool(overlap_candidate_ids(row)) for row in geography.values()),
        },
        "geography": {
            "status": manifest["geography"]["status"],
            "by_method": dict(sorted(Counter(value(row, "natural_method") for row in geography.values()).items())),
            "natural_ok": sum(parse_bool(value(row, "natural_ok")) for row in geography.values()),
            "diagnostic_only": sum(not value(row, "raw_geography_ancestor") for row in geography.values()),
            "overlap": {
                "rows": sum(any(value(row, field) for field in OVERLAP_COLUMNS) for row in geography.values()),
                "accepted": sum(bool(overlap_candidate_ids(row)) for row in geography.values()),
                "reason_counts": dict(sorted(Counter(value(row, "overlap_reason") for row in geography.values()
                                                     if value(row, "overlap_reason")).items())),
            },
        },
        "auto_repair_ids": [int(row["me_id"]) for row in rows if row["decision"] == "auto_repair"],
        "applied_ids": [], "review_ids": [int(row["me_id"]) for row in rows if row["decision"] == "review"],
        "already_manual_ids": [int(row["me_id"]) for row in rows if row["decision"] == "already_manual"],
        "skipped_reasons": skipped,
        "skipped_reason_counts": dict(sorted(Counter(reason for reasons in skipped.values() for reason in reasons).items())),
        "note": "Only MANUAL_ME2V may change on apply. Canonical tables and review_queue.tsv are never edited. Pixel+VP and geography+VP alone cannot qualify; overlap can add an independent pair only with comment/name, never pixel/VP/raw geography. Existing direct identities stay review-only on overlap conflicts. v2m is used only as a vanilla ID registry. Optional geography absence is hashed as input state, not substituted evidence.",
    }
    for key, path in paths.items():
        require_unchanged(path, snapshots[key])
    require_unchanged(manual_path, source.data)
    if approvals_path is not None:
        require_unchanged(approvals_path, approvals_data)
    if args.propose:
        atomic_write(proposals_path, generated)
    else:
        assert saved_proposals is not None
        require_unchanged(proposals_path, saved_proposals)
        repairs = sorted([row for row in rows if row["decision"] == "auto_repair"] + approved,
                         key=lambda row: int(row["me_id"]))
        summary["mode"] = "apply"
        if repairs:
            updated = source.insert(repairs)
            # Input files are checked again after source preparation, before any edit.
            for key, path in paths.items():
                require_unchanged(path, snapshots[key])
            require_unchanged(proposals_path, saved_proposals)
            if approvals_path is not None:
                require_unchanged(approvals_path, approvals_data)
            backup = apply_source(source, updated)
            summary["backup"] = str(backup)
            summary["manual_source_sha256_after"] = sha256(updated)
            summary["applied_ids"] = [int(row["me_id"]) for row in repairs]
            summary["counts"]["applied"] = len(repairs)
    atomic_write(summary_path, json_bytes(summary))
    print(f"{'Proposed' if args.propose else 'Applied'} state mapping repairs: "
          f"auto_repair={counts['auto_repair']}, review={counts['review']}, "
          f"already_manual={counts['already_manual']}, no_action={counts['no_action']}; "
          f"applied={len(summary['applied_ids'])}")
    print(f"Proposals: {proposals_path}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, AuditInputError, RepairError, ValueError, SyntaxError) as error:
        raise SystemExit(f"Cannot auto-repair state mapping: {error}") from error
