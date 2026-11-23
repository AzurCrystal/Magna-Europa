#!/usr/bin/env python
"""Read-only relation API over docs/states/state_edges.tsv.

state_edges.tsv is the generated long-table truth for vanilla <-> ME state
relations: exactly one row per (vanilla_id, me_id) pair, generated — never
hand-edited. v2m_states.tsv / m2v_states.tsv remain the single-value
compatibility views; this module exposes the full one-to-many and
many-to-many graph behind them.

Every query partitions edges by relation_scope:

- ``compatibility``: is_canonical=1 edges — the old m2v/v2m primary views
  restated as an existing compatibility assertion (NOT a complete-subset
  proof).
- ``asserted``: non-canonical edges with an explicit relation declaration
  (migration comment / manual review).
- ``observation``: raster/candidate/name/pixel/vp heuristic evidence only;
  overlap is an observation, not a proven relation.
- ``excluded``: synthetic MAP/Map Border diagnostic rows.

Default queries (expand/ancestors/contains/intersect/coverage) use only
compatibility+asserted scopes; excluded edges NEVER enter member expansion.
Raster evidence stays reachable through expand_observed()/ancestors_observed()
(all usable scopes) or an explicit ``scopes=`` argument; expand_all()/
ancestors_all() include excluded. There is no "proven"/"complete_subset"
scope: a province-level crosswalk is required before any complete-subset
claim; this module only partitions edges by evidential status.

Determinism: expand()/ancestors() return the primary relation first where
known, then remaining IDs in numeric order. intersect() returns numeric
order. Nothing here mutates inputs or files.

Stdlib only::

    import state_relations
    if state_relations.is_available(docs_dir):
        rel = state_relations.load(docs_dir)
        members = rel.expand(42)            # ME ids related to vanilla 42
        anc     = rel.ancestors(431)        # vanilla ids related to ME 431
"""

import csv
import os


# Fixed generated schema (one row per (vanilla_id, me_id) pair).
COLUMNS = [
    "vanilla_id", "vanilla_name", "me_id", "me_name",
    "edge_source", "evidence_families",
    "is_canonical", "canonical_ancestor", "canonical_primary_of_vanilla",
    "me_area_px", "me_match_px", "me_share", "me_rank",
    "me_modal_vanilla", "me_modal_share", "me_modal_margin",
    "me_assigned_share", "me_outside_px", "me_water_px",
    "me_unassigned_px", "me_components", "relation_scope",
    "edge_class", "geometry_usable",
    "v_native_area_px", "v_observable_px", "v_match_px",
    "v_share_observable", "v_share_native", "v_rank",
    "comment_votes", "vp_votes", "name_exact", "same_id", "manual_kind",
]
KEY_COLUMNS = ("vanilla_id", "me_id")
FLAG_COLUMNS = ("is_canonical", "canonical_primary_of_vanilla",
                "geometry_usable", "name_exact", "same_id")
# 0..1 ratios; empty string is allowed (unavailable/uncomputable).
RATIO_COLUMNS = ("me_share", "me_modal_share", "me_modal_margin",
                 "me_assigned_share", "v_share_observable", "v_share_native")
# Non-negative integer cells; empty allowed for derived/diagnostic fields.
INT_COLUMNS = ("me_area_px", "me_match_px", "me_rank",
               "me_outside_px", "me_water_px", "me_unassigned_px",
               "me_components", "v_native_area_px", "v_observable_px",
               "v_match_px", "v_rank", "comment_votes", "vp_votes")
# Optional single-ID cells inside otherwise per-state constant fields.
ID_REF_COLUMNS = ("canonical_ancestor", "me_modal_vanilla")

# Fixed relation_scope enum; unknown or missing values fail validation.
RELATION_SCOPES = ("compatibility", "asserted", "observation", "excluded")
_EXCLUDED_SCOPE = "excluded"
# Default query scope set: asserted relations only, raster observations out.
DEFAULT_SCOPES = frozenset(("compatibility", "asserted"))
# All usable (non-excluded) edges: DEFAULT plus raster observations.
OBSERVED_SCOPES = frozenset(RELATION_SCOPES) - {_EXCLUDED_SCOPE}
ALL_SCOPES = frozenset(RELATION_SCOPES)

# Ratios are written with six decimals by the generator; allow half a ulp
# plus float noise when recomputing share == match/area.
_SHARE_TOL = 2e-6

EDGES_FILENAME = "state_edges.tsv"
_V2M_FILENAME = "v2m_states.tsv"
_M2V_FILENAME = "m2v_states.tsv"
_EXCLUDED_CLASS = "excluded"
_SYNTHETIC_ME_NAME = "map border"
_SYNTHETIC_ME_OWNER = "MAP"


class RelationError(ValueError):
    """Raised by load() when the edge table violates its contract."""


def edges_path(docs_dir):
    return os.path.join(docs_dir, EDGES_FILENAME)


def is_available(docs_dir):
    """True when docs_dir contains a state_edges.tsv to load."""
    return os.path.isfile(edges_path(docs_dir))


def _read_tsv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _int_cell(row, col):
    """Return (value, ok): empty -> (None, True); positive int required."""
    raw = (row.get(col) or "").strip()
    if raw == "":
        return None, True
    try:
        val = int(raw)
    except ValueError:
        return None, False
    return val, val > 0


def _ratio_ok(raw):
    raw = (raw or "").strip()
    if raw == "":
        return True
    try:
        val = float(raw)
    except ValueError:
        return False
    return 0.0 <= val <= 1.0


def _flag_ok(raw):
    return (raw or "").strip() in ("", "0", "1")


class StateRelations:
    """Validated, immutable view of the vanilla<->ME edge graph.

    Construct via load()/from_rows(); __init__ is intentionally simple and
    performs no I/O. All queries default to DEFAULT_SCOPES (compatibility +
    asserted); include_excluded unions the excluded scope back in and the
    ``scopes`` argument selects an explicit scope set.
    """

    @staticmethod
    def _scope_set(scopes, include_excluded):
        """Resolve the query scope set; fail closed on unknown names."""
        if scopes is None:
            result = set(DEFAULT_SCOPES)
        elif isinstance(scopes, str):
            result = {scopes.strip()}
        else:
            result = {str(s).strip() for s in scopes}
        unknown = result - ALL_SCOPES
        if unknown:
            raise ValueError("unknown relation_scope %r; valid: %s"
                             % (sorted(unknown),
                                ", ".join(RELATION_SCOPES)))
        if include_excluded:
            result.add(_EXCLUDED_SCOPE)
        return result

    def __init__(self, edges, v2m, m2v):
        self.edges = edges                      # tuple of row dicts
        self.v2m = v2m                          # vanilla_id -> registry row
        self.m2v = m2v                          # me_id -> registry row
        self._by_v = {}                         # vanilla_id -> [me_id]
        self._by_me = {}                        # me_id -> [vanilla_id]
        self._scope = {}                        # (vanilla_id, me_id) -> scope
        self._edge_map = {}                     # (vanilla_id, me_id) -> row
        self._canonical_edge = {}               # me_id -> vanilla_id
        self._primary_edge = {}                 # vanilla_id -> me_id
        for row in edges:
            v, m = row["_v"], row["_m"]
            self._by_v.setdefault(v, []).append(m)
            self._by_me.setdefault(m, []).append(v)
            self._edge_map[(v, m)] = row
            self._scope[(v, m)] = row["relation_scope"].strip()
            if row["is_canonical"].strip() == "1":
                self._canonical_edge[m] = v
            if row["canonical_primary_of_vanilla"].strip() == "1":
                self._primary_edge[v] = m
        for lst in self._by_v.values():
            lst.sort()
        for lst in self._by_me.values():
            lst.sort()

    # -------------------------------------------------------- edge access

    def edge(self, vanilla_id, me_id):
        """Raw edge row dict for one pair, or None."""
        return self._edge_map.get((int(vanilla_id), int(me_id)))

    def _live(self, vanilla_id, me_id, scope_set):
        return self._scope.get((vanilla_id, me_id)) in scope_set

    def contains(self, vanilla_id, me_id, include_excluded=False,
                 scopes=None):
        """True when a (vanilla_id, me_id) edge row exists in scope."""
        v, m = int(vanilla_id), int(me_id)
        if m not in self._by_v.get(v, ()):
            return False
        return self._live(v, m, self._scope_set(scopes, include_excluded))

    # -------------------------------------------------------- expansion

    def expand(self, vanilla_id, include_excluded=False, scopes=None):
        """ME ids with an in-scope edge to vanilla_id.

        Primary first, then numeric. Defaults to DEFAULT_SCOPES; use
        expand_observed() for all usable edges or ``scopes=`` for an
        explicit set.
        """
        v = int(vanilla_id)
        scope_set = self._scope_set(scopes, include_excluded)
        members = [m for m in self._by_v.get(v, ())
                   if self._live(v, m, scope_set)]
        primary = self.primary_member(v)
        if primary in members:
            members.remove(primary)
            members.insert(0, primary)
        return members

    def expand_observed(self, vanilla_id):
        """expand() over all usable scopes (observations included)."""
        return self.expand(vanilla_id, scopes=OBSERVED_SCOPES)

    def expand_all(self, vanilla_id):
        """expand() over every scope, excluded diagnostics included."""
        return self.expand(vanilla_id, scopes=ALL_SCOPES)

    def ancestors(self, me_id, include_excluded=False, scopes=None):
        """Vanilla ids with an in-scope edge to me_id.

        Primary first, then numeric. Defaults to DEFAULT_SCOPES; use
        ancestors_observed() for all usable edges or ``scopes=`` for an
        explicit set.
        """
        m = int(me_id)
        scope_set = self._scope_set(scopes, include_excluded)
        ancestors = [v for v in self._by_me.get(m, ())
                     if self._live(v, m, scope_set)]
        primary = self.primary_ancestor(m)
        if primary in ancestors:
            ancestors.remove(primary)
            ancestors.insert(0, primary)
        return ancestors

    def ancestors_observed(self, me_id):
        """ancestors() over all usable scopes (observations included)."""
        return self.ancestors(me_id, scopes=OBSERVED_SCOPES)

    def ancestors_all(self, me_id):
        """ancestors() over every scope, excluded diagnostics included."""
        return self.ancestors(me_id, scopes=ALL_SCOPES)

    def intersect(self, vanilla_ids, include_excluded=False, scopes=None):
        """ME ids with an in-scope edge to EVERY given vanilla_id (numeric)."""
        ids = [int(v) for v in vanilla_ids]
        if not ids:
            return []
        scope_set = self._scope_set(scopes, include_excluded)
        common = set(self.expand(ids[0], scopes=scope_set))
        for v in ids[1:]:
            common &= set(self.expand(v, scopes=scope_set))
            if not common:
                break
        return sorted(common)

    # -------------------------------------------------------- primaries

    def primary_member(self, vanilla_id):
        """Canonical strongest member of vanilla_id, or None."""
        v = int(vanilla_id)
        if v in self._primary_edge:
            return self._primary_edge[v]
        row = self.v2m.get(v)
        if row is None:
            return None
        raw = (row.get("me_primary") or "").strip()
        return int(raw) if raw.isdigit() else None

    def primary_ancestor(self, me_id):
        """Canonical adjudicated ancestor of me_id, or None."""
        m = int(me_id)
        if m in self._canonical_edge:
            return self._canonical_edge[m]
        row = self.m2v.get(m)
        if row is None:
            return None
        raw = (row.get("v_ancestor") or "").strip()
        return int(raw) if raw.isdigit() else None

    # -------------------------------------------------------- coverage

    def coverage(self, vanilla_id, me_ids=None, include_excluded=False,
                 scopes=None):
        """Membership coverage of vanilla_id by literal me_ids.

        me_ids=None means "the full expansion covers itself" -> status full.
        Returns a plain dict: member_count, covered_count, covered (sorted),
        missing (sorted), status in {full, partial, none, unmapped}.
        """
        members = set(self.expand(vanilla_id, include_excluded, scopes))
        covered = sorted(members & {int(m) for m in me_ids}) \
            if me_ids is not None else sorted(members)
        missing = sorted(members - set(covered))
        if not members:
            status = "unmapped"
        elif not missing:
            status = "full"
        elif covered:
            status = "partial"
        else:
            status = "none"
        return {"member_count": len(members), "covered_count": len(covered),
                "covered": covered, "missing": missing, "status": status}

    # -------------------------------------------------------- validation

    def validate(self):
        """Re-check every contract invariant; returns a list of issue strings."""
        issues, _ = _validate_rows(
            [dict(r) for r in self.edges], self.v2m, self.m2v)
        return issues


# ------------------------------------------------------------------ loading

def _validate_rows(rows, v2m, m2v):
    """Contract checks shared by load() and StateRelations.validate()."""
    issues = []
    seen = set()
    by_me_consts = {}      # me_id -> {col: value}
    by_v_consts = {}       # vanilla_id -> {col: value}
    canonical_of_me = {}   # me_id -> vanilla_id with is_canonical=1
    primary_of_v = {}      # vanilla_id -> me_id with canonical_primary=1
    prev_key = None

    for idx, row in enumerate(rows, start=2):  # line 1 is the header
        where = "row %d" % idx
        cols = [c for c in row.keys() if c not in ("_v", "_m")]
        if cols != list(COLUMNS):
            missing = [c for c in COLUMNS if c not in row]
            extra = [c for c in cols if c not in COLUMNS]
            issues.append("%s: column mismatch (missing=%s extra=%s)"
                          % (where, missing, extra))
            break
        v, ok_v = _int_cell(row, "vanilla_id")
        m, ok_m = _int_cell(row, "me_id")
        if not ok_v or v is None:
            issues.append("%s: bad vanilla_id %r" % (where, row["vanilla_id"]))
            continue
        if not ok_m or m is None:
            issues.append("%s: bad me_id %r" % (where, row["me_id"]))
            continue
        row["_v"], row["_m"] = v, m

        key = (m, v)
        if prev_key is not None and key <= prev_key:
            issues.append("%s: rows not sorted by (me_id, vanilla_id)" % where)
        prev_key = key
        if (v, m) in seen:
            issues.append("%s: duplicate edge (%d, %d)" % (where, v, m))
        seen.add((v, m))

        if v not in v2m:
            issues.append("%s: vanilla_id %d not in v2m_states.tsv" % (where, v))
        if m not in m2v:
            issues.append("%s: me_id %d not in m2v_states.tsv" % (where, m))

        for col in RATIO_COLUMNS:
            if not _ratio_ok(row[col]):
                issues.append("%s: %s=%r is not a 0..1 ratio"
                              % (where, col, row[col]))
        for col in INT_COLUMNS:
            raw = row[col].strip()
            if raw and (not raw.lstrip("-").isdigit() or int(raw) < 0):
                issues.append("%s: %s=%r is not a non-negative integer"
                              % (where, col, raw))
        for col in FLAG_COLUMNS:
            if not _flag_ok(row[col]):
                issues.append("%s: %s=%r is not a 0/1 flag"
                              % (where, col, row[col]))
        for col in ID_REF_COLUMNS:
            val, ok = _int_cell(row, col)
            if not ok:
                issues.append("%s: %s=%r is not a positive integer"
                              % (where, col, row[col]))
            elif val is not None and val not in v2m:
                issues.append("%s: %s=%d not in v2m_states.tsv"
                              % (where, col, val))

        # Per-state constant cells must agree across that state's rows.
        for col in ("canonical_ancestor", "me_area_px", "me_modal_vanilla",
                    "me_modal_share", "me_modal_margin", "me_assigned_share",
                    "me_outside_px", "me_water_px", "me_unassigned_px",
                    "me_components"):
            slot = by_me_consts.setdefault(m, {})
            if col in slot and slot[col] != row[col].strip():
                issues.append("%s: %s disagrees with earlier row of me %d "
                              "(%r != %r)" % (where, col, m,
                                              row[col], slot[col]))
            else:
                slot[col] = row[col].strip()
        for col in ("v_native_area_px", "v_observable_px"):
            slot = by_v_consts.setdefault(v, {})
            if col in slot and slot[col] != row[col].strip():
                issues.append("%s: %s disagrees with earlier row of vanilla "
                              "%d (%r != %r)" % (where, col, v,
                                                 row[col], slot[col]))
            else:
                slot[col] = row[col].strip()

        if row["is_canonical"].strip() == "1":
            if m in canonical_of_me:
                issues.append("%s: me %d already has canonical edge "
                              "(%d, %d)" % (where, m, canonical_of_me[m], m))
            canonical_of_me[m] = v
            anc = row["canonical_ancestor"].strip()
            if anc and int(anc) != v:
                issues.append("%s: is_canonical edge (%d, %d) but "
                              "canonical_ancestor=%s" % (where, v, m, anc))
        if row["canonical_primary_of_vanilla"].strip() == "1":
            if v in primary_of_v:
                issues.append("%s: vanilla %d already has canonical primary "
                              "me %d" % (where, v, primary_of_v[v]))
            primary_of_v[v] = m

        # Accounting: match pixels can never exceed their denominators.
        def _num(col):
            raw = row[col].strip()
            return int(raw) if raw.lstrip("-").isdigit() else None
        me_match, me_area = _num("me_match_px"), _num("me_area_px")
        v_match, v_obs, v_nat = (_num("v_match_px"), _num("v_observable_px"),
                                 _num("v_native_area_px"))
        if me_match is not None and me_area is not None and me_match > me_area:
            issues.append("%s: me_match_px %d > me_area_px %d"
                          % (where, me_match, me_area))
        if None not in (v_match, v_obs) and v_match > v_obs:
            issues.append("%s: v_match_px %d > v_observable_px %d"
                          % (where, v_match, v_obs))
        if None not in (v_obs, v_nat) and v_obs > v_nat:
            issues.append("%s: v_observable_px %d > v_native_area_px %d"
                          % (where, v_obs, v_nat))
        share_raw = row["me_share"].strip()
        if me_match and me_area and share_raw:
            try:
                if abs(me_match / me_area - float(share_raw)) > _SHARE_TOL:
                    issues.append("%s: me_share %s != me_match_px/me_area_px"
                                  % (where, share_raw))
            except ValueError:
                pass

        # Scope consistency: relation_scope is a fixed enum (missing/unknown
        # fails closed); excluded edges are exactly the excluded scope, and
        # compatibility edges are exactly the non-excluded canonical ones —
        # an existing compatibility assertion, never a complete-subset proof.
        reg = m2v.get(m) or {}
        synthetic = (row["me_name"].strip().lower() == _SYNTHETIC_ME_NAME or
                     (reg.get("owner") or "").strip() == _SYNTHETIC_ME_OWNER)
        excluded = row["edge_class"].strip() == _EXCLUDED_CLASS
        canonical = row["is_canonical"].strip() == "1"
        scope = row["relation_scope"].strip()
        if scope not in RELATION_SCOPES:
            issues.append("%s: relation_scope=%r is not one of %s"
                          % (where, row["relation_scope"],
                             ", ".join(RELATION_SCOPES)))
        elif (scope == _EXCLUDED_SCOPE) != excluded:
            issues.append("%s: relation_scope=%s but edge_class=%s"
                          % (where, scope, row["edge_class"]))
        elif scope == "compatibility" and not canonical:
            issues.append("%s: relation_scope=compatibility on non-canonical "
                          "edge (%d, %d)" % (where, v, m))
        elif scope in ("asserted", "observation") and canonical:
            issues.append("%s: canonical edge (%d, %d) has relation_scope=%s"
                          % (where, v, m, scope))
        # Synthetic Map Border / MAP-owned ME states must carry
        # edge_class=excluded. Such edges may still be canonical (real data:
        # ME 2986/2552 keep canonical flags) — exclusion only removes them
        # from compatibility member expansion.
        if synthetic and not excluded:
            issues.append("%s: synthetic/MAP me %d edge (%d, %d) is not "
                          "edge_class=excluded" % (where, m, v, m))

    # Cross-view consistency: canonical edges are the registries' single values.
    for m, v in sorted(canonical_of_me.items()):
        reg = m2v.get(m)
        if reg is not None:
            anc = (reg.get("v_ancestor") or "").strip()
            if anc and int(anc) != v:
                issues.append("me %d: canonical edge ancestor %d != m2v "
                              "v_ancestor %s" % (m, v, anc))
    for v, m in sorted(primary_of_v.items()):
        reg = v2m.get(v)
        if reg is not None:
            prim = (reg.get("me_primary") or "").strip()
            if prim and int(prim) != m:
                issues.append("vanilla %d: canonical primary %d != v2m "
                              "me_primary %s" % (v, m, prim))
    for v, reg in sorted(v2m.items()):
        prim = (reg.get("me_primary") or "").strip()
        if prim and int(prim) in m2v:
            if primary_of_v.get(v) is None:
                issues.append("vanilla %d: v2m me_primary %s has no "
                              "canonical_primary_of_vanilla edge" % (v, prim))
        for m in (int(x) for x in (reg.get("me_members") or "").split("|")
                  if x.strip()):
            if (v, m) not in seen:
                issues.append("v2m member (%d, %d) has no edge row" % (v, m))
            elif canonical_of_me.get(m) != v:
                issues.append("v2m member (%d, %d) is not is_canonical=1"
                              % (v, m))
    for m, reg in sorted(m2v.items()):
        anc = (reg.get("v_ancestor") or "").strip()
        if anc and int(anc) in v2m and canonical_of_me.get(m) != int(anc):
            issues.append("me %d: m2v v_ancestor %s has no is_canonical edge"
                          % (m, anc))
    return issues, seen


def from_rows(edge_rows, v2m_rows, m2v_rows):
    """Build a validated StateRelations from raw DictReader rows (tests)."""
    v2m = {int(r["vanilla_id"]): r for r in v2m_rows}
    m2v = {int(r["me_id"]): r for r in m2v_rows}
    issues, _ = _validate_rows(edge_rows, v2m, m2v)
    if issues:
        raise RelationError("state edges invalid: " + "; ".join(issues[:10]))
    return StateRelations(tuple(edge_rows), v2m, m2v)


def load(docs_dir):
    """Load and validate the edge graph under docs_dir.

    Raises FileNotFoundError when state_edges.tsv (or a registry TSV needed
    for key validation) is absent, and RelationError on contract violations.
    """
    path = edges_path(docs_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError("missing edge table %s" % path)
    rows = _read_tsv(path)
    v2m_rows = _read_tsv(os.path.join(docs_dir, _V2M_FILENAME))
    m2v_rows = _read_tsv(os.path.join(docs_dir, _M2V_FILENAME))
    return from_rows(rows, v2m_rows, m2v_rows)


def validate(docs_dir):
    """Validation without raising: list of issue strings ([] = clean).

    Missing files are reported as issues rather than exceptions.
    """
    path = edges_path(docs_dir)
    if not os.path.isfile(path):
        return ["missing edge table %s" % path]
    try:
        load(docs_dir)
    except (RelationError, FileNotFoundError, csv.Error) as exc:
        return [str(exc)]
    return []
