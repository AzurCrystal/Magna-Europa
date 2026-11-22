#!/usr/bin/env python
"""Context-aware Vanilla -> Magna Europa state/province sync & translation CLI.

Pure Python stdlib at runtime (reads docs/states/eu_mapping/*.tsv in ~50 ms).
Designed for syncing Vanilla HOI4 focus/decision/event updates into Magna Europa.

Modes:
  1. Territory / Union mode (--states 42 51 978 --mode territory):
       Maps a single Vanilla state or a set of Vanilla states as a geographic
       UNION so internal border straddlers cancel out automatically. Emits the
       exact gap-free ME state list, outer-border straddler advisories, off-map
       exclusions, and ready-to-paste Clausewitz syntax.

  2. City / Single-state economic mode (--states 126 --mode city):
       Maps each Vanilla state to its single primary ME state (capital /
       spatially-verified highest VP / name / manpower) so factory/slot/resource
       rewards do not multiply 10x across sub-states.

  3. Province mode (--provinces 6521 11506):
       Maps Vanilla province IDs to overlapping ME provinces and their host
       ME states (for railways, bunkers, canals, province controllers).

  4. Clausewitz Script / Diff translation (--snippet FILE | --diff OLD NEW):
       Parses real HOI4 focus/decision/event scripts (or diffs between two
       Vanilla versions), auto-detects territory vs city vs province context
       per statement/block, computes union translations for multi-state groups,
       flags off-map states, and prints translated Clausewitz code + report.

Examples:
  python tools/translate_vanilla_states.py --states 42 51 978 --mode territory
  python tools/translate_vanilla_states.py --states 42 51 --mode territory --emit transfer_state
  python tools/translate_vanilla_states.py --states 126 16 64 --mode city
  python tools/translate_vanilla_states.py --provinces 6521 11506
  python tools/translate_vanilla_states.py --snippet path/to/vanilla_focus.txt
  python tools/translate_vanilla_states.py --diff old_germany.txt new_germany.txt
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
from pdx_parse import Parser  # noqa: E402

DEFAULT_MAPPING_DIR = Path(repo_root()) / "docs" / "states" / "eu_mapping"

# Clausewitz keywords that imply TERRITORY (full 1->N union coverage)
TERRITORY_SCALAR_KEYS = frozenset({
    "transfer_state", "transfer_state_to",
    "owns_state", "controls_state", "has_full_control_of_state",
    "add_state_core", "remove_state_core",
    "add_state_claim", "remove_state_claim",
    "set_state_owner", "set_state_controller",
    "is_owned_by", "is_controlled_by",
    "set_demilitarized_zone",
    "add_contested_owner", "remove_contested_owner",
})
TERRITORY_LIST_KEYS = frozenset({
    "states", "highlight_state_targets", "states_filter",
})

# Clausewitz keywords that imply CITY / SINGLE-TARGET (1->1 primary ME state)
CITY_SCALAR_KEYS = frozenset({
    "set_capital", "capital_scope",
})
CITY_INNER_KEYS = frozenset({
    "add_building_construction",
    "add_extra_state_shared_building_slots",
    "free_building_slots",
    "add_resource",
    "add_manpower",
    "add_victory_points",
    "set_victory_points",
    "set_building_level",
    "damage_building",
    "add_state_modifier",
    "add_dynamic_modifier",
    "start_resistance",
    "set_state_name",
    "reset_state_name",
})
TERRITORY_INNER_KEYS = frozenset({
    "transfer_state_to", "set_state_owner", "set_state_controller",
    "add_core_of", "remove_core_of", "add_claim_by", "remove_claim_by",
    "set_demilitarized_zone", "is_owned_by", "is_controlled_by",
    "is_fully_controlled_by", "is_owned_and_controlled_by",
    "is_core_of", "is_claimed_by", "owner", "controller",
})

PROVINCE_KEYS = frozenset({
    "province", "set_province_controller", "start_province", "target_province",
    "set_province_name", "reset_province_name",
})
NON_STATE_BLOCKS = frozenset({
    "province", "provinces", "victory_points", "region", "regions",
    "strategic_region", "strategic_regions", "random_list", "ai_will_do",
    "modifier", "build_railway", "path",
})
STATE_QUANTIFIER_SCOPES = frozenset({
    "random_state", "random_owned_state", "random_controlled_state",
    "random_owned_controlled_state", "random_core_state",
    "every_state", "every_owned_state", "every_controlled_state",
    "every_core_state", "any_state", "any_owned_state", "any_controlled_state",
})


class EUMappingIndex:
    """Fast stdlib-only index over docs/states/eu_mapping/*.tsv."""

    def __init__(self, mapping_dir: Path = DEFAULT_MAPPING_DIR):
        self.mapping_dir = Path(mapping_dir)
        if not (self.mapping_dir / "state_partition_v2m.tsv").is_file():
            raise FileNotFoundError(
                f"Mapping tables not found under {self.mapping_dir}. "
                f"Run `python tools/build_eu_mapping.py` first."
            )

        self.v2m: dict[int, dict] = {}
        with open(self.mapping_dir / "state_partition_v2m.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                vid = int(r["vanilla_id"])
                self.v2m[vid] = {
                    "vanilla_id": vid,
                    "vanilla_name": r["vanilla_name"],
                    "vanilla_owner": r["vanilla_owner"],
                    "primary_me_id": int(r["primary_me_id"]) if r["primary_me_id"] else 0,
                    "primary_me_name": r["primary_me_name"],
                    "me_members": [int(x) for x in r["me_members"].split("|") if x],
                    "interior_members": [int(x) for x in r["interior_members"].split("|") if x],
                    "straddler_members": [int(x) for x in r["straddler_members"].split("|") if x],
                    "reverse_coverage": float(r["reverse_coverage"] or 0.0),
                    "block_id": int(r["block_id"]) if r["block_id"] else 0,
                }

        self.m2v: dict[int, dict] = {}
        with open(self.mapping_dir / "state_partition_m2v.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                mid = int(r["me_id"])
                self.m2v[mid] = {
                    "me_id": mid,
                    "me_name": r["me_name"],
                    "me_owner": r["me_owner"],
                    "norm_owner": r["norm_owner"],
                    "status": r["status"],
                    "v_id": int(r["v_id"]) if r["v_id"] else 0,
                    "v_name": r["v_name"],
                    "same_owner_share": float(r["same_owner_share"] or 0.0),
                    "confidence_tier": r["confidence_tier"],
                    "needs_review": r["needs_review"] == "1",
                    "review_reason": r["review_reason"],
                }

        self.primary_city: dict[int, dict] = {}
        with open(self.mapping_dir / "state_primary_city.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                vid = int(r["vanilla_id"])
                self.primary_city[vid] = {
                    "vanilla_id": vid,
                    "vanilla_name": r["vanilla_name"],
                    "vanilla_owner": r["vanilla_owner"],
                    "primary_me_id": int(r["primary_me_id"]) if r["primary_me_id"] else 0,
                    "primary_me_name": r["primary_me_name"],
                    "primary_basis": r["primary_basis"],
                    "vp_matched_name": r["vp_matched_name"],
                    "vp_dist_px": r["vp_dist_px"],
                    "me_member_count": int(r["me_member_count"] or 0),
                }

        # Overlap edges indexed by vanilla_id and me_id
        self.ov_by_v: dict[int, list[dict]] = defaultdict(list)
        self.ov_by_m: dict[int, list[dict]] = defaultdict(list)
        with open(self.mapping_dir / "state_overlap.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                rec = {
                    "vanilla_id": int(r["vanilla_id"]),
                    "me_id": int(r["me_id"]),
                    "norm_owner_match": r["norm_owner_match"] == "1",
                    "me_px": int(r["me_px"] or 0),
                    "me_total_px": int(r["me_total_px"] or 0),
                    "me_share": float(r["me_share"] or 0.0),
                    "me_same_owner_share": float(r["me_same_owner_share"]) if r["me_same_owner_share"] else 0.0,
                    "edge_role": r["edge_role"],
                    "is_partition_parent": r["is_partition_parent"] == "1",
                }
                self.ov_by_v[rec["vanilla_id"]].append(rec)
                self.ov_by_m[rec["me_id"]].append(rec)

        # Province crosswalk: vanilla_prov -> list of ME prov dicts (lazy-loaded on demand)
        self._vprov_to_mprovs: dict[int, list[dict]] | None = None

    def _ensure_provinces(self):
        if self._vprov_to_mprovs is not None:
            return
        self._vprov_to_mprovs = defaultdict(list)
        p = self.mapping_dir / "prov_m2v.tsv"
        if not p.is_file():
            return
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                vp = int(r["v_prov"]) if r["v_prov"] else 0
                if vp <= 0:
                    continue
                self._vprov_to_mprovs[vp].append({
                    "me_prov": int(r["me_prov"]),
                    "me_state": int(r["me_state"]),
                    "me_state_name": r["me_state_name"],
                    "me_owner": r["me_owner"],
                    "me_px": int(r["me_px"]),
                    "v_state": int(r["v_state"]),
                    "v_state_name": r["v_state_name"],
                    "purity_class": r["purity_class"],
                    "same_owner_share": float(r["same_owner_share"] or 0.0),
                })

    # -------------------------------------------------------- translation ops

    def translate_territory_union(self, vanilla_ids: list[int], outer_warn_threshold: float = 0.20) -> dict:
        """Translate a set of Vanilla state IDs as a geographic UNION.

        Internal border straddlers between adjacent states in `vanilla_ids`
        have their shares summed across the union and automatically promote to
        clean interior members! Only straddlers on the OUTER perimeter of the
        union are reported as advisories.
        """
        uniq_vs = sorted(set(int(v) for v in vanilla_ids))
        on_map = [v for v in uniq_vs if v in self.v2m]
        off_map = [v for v in uniq_vs if v not in self.v2m]
        on_set = set(on_map)

        # 1. Exact disjoint partition union (guaranteed gap-free & overlap-free)
        part_members = set()
        for v in on_map:
            part_members.update(self.v2m[v]["me_members"])

        # 2. Compute union share for every ME state touching ANY state in on_map
        touching = set(part_members)
        for v in on_map:
            for edge in self.ov_by_v.get(v, ()):
                if edge["norm_owner_match"] or edge["me_share"] >= 0.15:
                    touching.add(edge["me_id"])

        included_details = []
        outer_straddlers_excluded = []
        internal_cancelled_count = 0

        for mid in sorted(touching):
            mi = self.m2v.get(mid)
            if not mi or mi["status"] != "mapped":
                continue
            edges = self.ov_by_m.get(mid, ())
            # Sum share across all vanilla states in on_set (preferring same-owner shares when owner matches)
            u_same_sh = sum(e["me_same_owner_share"] for e in edges if e["vanilla_id"] in on_set and e["norm_owner_match"])
            u_all_sh = sum(e["me_share"] for e in edges if e["vanilla_id"] in on_set)
            # If the ME state had fallback/nearest assignment with 0 raster share, treat partition parent as 1.0
            in_part = mid in part_members
            if in_part and u_same_sh == 0.0 and u_all_sh == 0.0:
                u_same_sh = 1.0
            eff_sh = max(u_same_sh, u_all_sh)
            eff_sh = min(1.0, eff_sh)

            # Check if this ME state was an internal straddler whose straddling cancelled out!
            if in_part and mi["same_owner_share"] < 0.80 and eff_sh >= 0.85 and len(on_map) > 1:
                internal_cancelled_count += 1

            if in_part:
                included_details.append({
                    "me_id": mid,
                    "me_name": mi["me_name"],
                    "me_owner": mi["me_owner"],
                    "partition_v_id": mi["v_id"],
                    "partition_v_name": mi["v_name"],
                    "union_share": round(eff_sh, 4),
                    "is_outer_border_straddler": eff_sh < 0.80,
                })
            elif eff_sh >= outer_warn_threshold:
                outer_straddlers_excluded.append({
                    "me_id": mid,
                    "me_name": mi["me_name"],
                    "me_owner": mi["me_owner"],
                    "partition_v_id": mi["v_id"],
                    "partition_v_name": mi["v_name"],
                    "union_share": round(eff_sh, 4),
                })

        # Sort included by (partition_v_id, primary-first, me_id) for readability
        prim_set = {self.v2m[v]["primary_me_id"] for v in on_map}
        included_details.sort(key=lambda r: (r["partition_v_id"], 0 if r["me_id"] in prim_set else 1, r["me_id"]))
        outer_straddlers_excluded.sort(key=lambda r: (-r["union_share"], r["me_id"]))

        return {
            "mode": "territory_union",
            "vanilla_input": uniq_vs,
            "vanilla_on_map": on_map,
            "vanilla_off_map": off_map,
            "me_ids": [r["me_id"] for r in included_details],
            "me_count": len(included_details),
            "internal_straddlers_cancelled": internal_cancelled_count,
            "included_outer_straddlers": [r for r in included_details if r["is_outer_border_straddler"]],
            "excluded_outer_straddlers": outer_straddlers_excluded,
            "details": included_details,
        }

    def translate_city_primary(self, vanilla_ids: list[int]) -> dict:
        """Translate Vanilla state IDs to single-target primary ME states (1->1)."""
        uniq_vs = sorted(set(int(v) for v in vanilla_ids))
        results = []
        off_map = []
        for v in uniq_vs:
            pc = self.primary_city.get(v)
            if not pc or not pc["primary_me_id"]:
                off_map.append(v)
            else:
                results.append(pc)
        return {
            "mode": "city_primary",
            "vanilla_input": uniq_vs,
            "vanilla_off_map": off_map,
            "me_ids": [r["primary_me_id"] for r in results],
            "details": results,
        }

    def translate_provinces(self, vanilla_provs: list[int]) -> dict:
        """Translate Vanilla province IDs to overlapping ME provinces & host states."""
        self._ensure_provinces()
        results = {}
        missing = []
        for vp in sorted(set(int(p) for p in vanilla_provs)):
            hits = sorted(self._vprov_to_mprovs.get(vp, ()), key=lambda x: (-x["me_px"], x["me_prov"]))
            if not hits:
                missing.append(vp)
            else:
                results[vp] = {
                    "vanilla_prov": vp,
                    "v_state": hits[0]["v_state"],
                    "v_state_name": hits[0]["v_state_name"],
                    "primary_me_prov": hits[0]["me_prov"],
                    "me_provs": [h["me_prov"] for h in hits],
                    "me_states": sorted({h["me_state"] for h in hits}),
                    "details": hits[:12],
                }
        return {
            "mode": "province",
            "vanilla_provs_found": len(results),
            "vanilla_provs_offmap_or_sea": missing,
            "results": results,
        }


# ---------------------------------------------- Clausewitz snippet translator

def _infer_numeric_block_mode(children) -> str:
    """Decide whether a numeric state scope `42 = { ... }` is city/economic or territory."""
    city_hits = 0
    terr_hits = 0
    for ch in children:
        k = ch.key
        if k in CITY_INNER_KEYS:
            city_hits += 1
        elif k in TERRITORY_INNER_KEYS:
            terr_hits += 1
        elif ch.is_block and k in ("if", "else_if", "else", "limit", "OR", "AND", "NOT"):
            sub = _infer_numeric_block_mode(ch.children)
            if sub == "city":
                city_hits += 1
            elif sub == "territory":
                terr_hits += 1
    if city_hits > 0 and terr_hits == 0:
        return "city"
    return "territory"


def analyze_clausewitz_text(text: str, idx: EUMappingIndex, only_lines: set[int] | None = None) -> list[dict]:
    """Parse Clausewitz text and extract context-classified state/province translations."""
    parser = Parser(text)
    nodes = parser.parse()
    findings = []

    def _walk(node_list, path: list[str], in_non_state: bool, quantifier_parent: str | None):
        # Also detect consecutive sibling territory statements (e.g. multiple `transfer_state = N`
        # inside the same effect/trigger block) so we can show their UNION translation!
        grouped_scalars: dict[str, list[tuple[int, int]]] = defaultdict(list)

        for n in node_list:
            k = n.key
            line = n.line
            line_ok = (only_lines is None) or (line in only_lines)

            if in_non_state:
                # Still catch explicit `province = N` inside build_railway or building blocks
                if not n.is_block and k in PROVINCE_KEYS and n.val and n.val.isdigit() and line_ok:
                    vp = int(n.val)
                    tr = idx.translate_provinces([vp])
                    findings.append({
                        "line": line, "path": ".".join(path + [k]), "kind": "province",
                        "key": k, "vanilla_ids": [vp], "translation": tr,
                    })
                continue

            if n.is_block:
                if k in NON_STATE_BLOCKS:
                    _walk(n.children, path + [k], True, None)
                    continue

                # Numeric state scope: `42 = { ... }`
                if k.isdigit():
                    vid = int(k)
                    # Exclude random_list weights or modifier blocks (already handled by NON_STATE_BLOCKS)
                    first_k = n.children[0].key if n.children else ""
                    if first_k in ("modifier", "factor", "add_attack", "add_defense", "add_logistics", "add_planning"):
                        continue
                    mode = _infer_numeric_block_mode(n.children)
                    if line_ok:
                        tr = idx.translate_city_primary([vid]) if mode == "city" else idx.translate_territory_union([vid])
                        findings.append({
                            "line": line, "path": ".".join(path + [f"{vid}={{}}"]),
                            "kind": f"state_scope:{mode}", "key": k,
                            "vanilla_ids": [vid], "translation": tr,
                        })
                    _walk(n.children, path + [k], False, None)
                    continue

                # List of state IDs: `states = { 42 51 978 }` or `prioritize = { ... }` under random_*_state
                if k in TERRITORY_LIST_KEYS or (k == "prioritize" and quantifier_parent in STATE_QUANTIFIER_SCOPES):
                    vids = [int(ch.key) for ch in n.children if ch.key.isdigit() and ch.val is None and not ch.children]
                    if vids and (only_lines is None or line in only_lines or any(ch.line in only_lines for ch in n.children)):
                        if k == "prioritize":
                            tr = idx.translate_city_primary(vids)
                            kind = "state_list:city"
                        else:
                            tr = idx.translate_territory_union(vids)
                            kind = "state_list:territory_union"
                        findings.append({
                            "line": line, "path": ".".join(path + [k]),
                            "kind": kind, "key": k,
                            "vanilla_ids": vids, "translation": tr,
                        })
                    continue

                # Check if `build_railway` or `add_building_construction` etc.
                next_q = k if k in STATE_QUANTIFIER_SCOPES else quantifier_parent
                _walk(n.children, path + [k], False, next_q)
            else:
                val = n.val or ""
                if not val.isdigit():
                    continue
                num = int(val)
                if k in PROVINCE_KEYS:
                    if line_ok:
                        findings.append({
                            "line": line, "path": ".".join(path + [k]), "kind": "province",
                            "key": k, "vanilla_ids": [num], "translation": idx.translate_provinces([num]),
                        })
                elif k in CITY_SCALAR_KEYS:
                    if line_ok:
                        findings.append({
                            "line": line, "path": ".".join(path + [k]), "kind": "scalar:city",
                            "key": k, "vanilla_ids": [num], "translation": idx.translate_city_primary([num]),
                        })
                elif k in TERRITORY_SCALAR_KEYS or (k == "state" and path and path[-1] != "victory_points"):
                    if line_ok:
                        grouped_scalars[k].append((line, num))

        # Emit grouped sibling territory scalars as a single union when multiple share the same key!
        for k, items in grouped_scalars.items():
            vids = [num for _, num in items]
            first_ln = items[0][0]
            # Check if parent block is a single-state economic effect (like `add_resource = { state = 51 }`)
            parent_k = path[-1] if path else ""
            if k == "state" and parent_k in CITY_INNER_KEYS:
                tr = idx.translate_city_primary(vids)
                kind = f"scalar:city({parent_k})"
            else:
                tr = idx.translate_territory_union(vids)
                kind = "scalar_group:territory_union" if len(vids) > 1 else "scalar:territory"
            findings.append({
                "line": first_ln,
                "path": ".".join(path + [k]),
                "kind": kind,
                "key": k,
                "vanilla_ids": vids,
                "translation": tr,
            })

    _walk(nodes, [], False, None)
    findings.sort(key=lambda x: (x["line"], x["path"]))
    return findings


def _changed_lines_in_new(old_text: str, new_text: str) -> set[int]:
    """Return 1-based line numbers in new_text that were added or modified vs old_text."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    changed = set()
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            for ln in range(j1 + 1, j2 + 1):
                changed.add(ln)
    return changed


def format_clausewitz_emit(tr: dict, emit_key: str | None = None) -> str:
    """Format a translation result into clean, commented Clausewitz script."""
    lines = []
    if tr["mode"] == "territory_union":
        if tr["vanilla_off_map"]:
            lines.append(f"# OFF-MAP in Magna Europa (skipped): vanilla {tr['vanilla_off_map']}")
        v_desc = ", ".join(str(v) for v in tr["vanilla_on_map"])
        lines.append(f"# Magna Europa territory union for vanilla states [{v_desc}] ({tr['me_count']} ME states)")
        if emit_key == "states":
            lines.append("states = {")
            for d in tr["details"]:
                flag = f" [outer straddler {d['union_share']*100:.0f}%]" if d["is_outer_border_straddler"] else ""
                lines.append(f"\t{d['me_id']} # {d['me_name']} (was vanilla {d['partition_v_id']} {d['partition_v_name']}){flag}")
            lines.append("}")
        elif emit_key:
            for d in tr["details"]:
                flag = f" [outer straddler {d['union_share']*100:.0f}%]" if d["is_outer_border_straddler"] else ""
                lines.append(f"{emit_key} = {d['me_id']} # {d['me_name']} (was vanilla {d['partition_v_id']} {d['partition_v_name']}){flag}")
        else:
            lines.append(" ".join(str(x) for x in tr["me_ids"]))
    elif tr["mode"] == "city_primary":
        if tr["vanilla_off_map"]:
            lines.append(f"# OFF-MAP in Magna Europa (skipped): vanilla {tr['vanilla_off_map']}")
        for d in tr["details"]:
            k_prefix = f"{emit_key} = " if emit_key else ""
            lines.append(
                f"{k_prefix}{d['primary_me_id']} # {d['primary_me_name']} "
                f"(was vanilla {d['vanilla_id']} {d['vanilla_name']}; basis={d['primary_basis']})"
            )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Context-aware Vanilla -> Magna Europa state/province translator.")
    parser.add_argument("--mapping-dir", default=str(DEFAULT_MAPPING_DIR))
    parser.add_argument("--states", nargs="+", type=int, help="Vanilla state IDs to translate")
    parser.add_argument("--mode", choices=("territory", "city"), default="territory",
                        help="territory (union 1->N) or city (primary 1->1)")
    parser.add_argument("--emit", default=None,
                        help="Emit ready-to-paste Clausewitz lines using this key (e.g. transfer_state, add_state_core, states)")
    parser.add_argument("--provinces", nargs="+", type=int, help="Vanilla province IDs to translate")
    parser.add_argument("--snippet", default=None, help="Clausewitz script file to parse and translate")
    parser.add_argument("--diff", nargs=2, metavar=("OLD_VANILLA", "NEW_VANILLA"),
                        help="Diff two Vanilla script files and translate only added/changed state/province refs")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args(argv)

    idx = EUMappingIndex(Path(args.mapping_dir))

    if args.states:
        tr = idx.translate_territory_union(args.states) if args.mode == "territory" else idx.translate_city_primary(args.states)
        if args.json:
            print(json.dumps(tr, indent=2, ensure_ascii=False))
        else:
            print(format_clausewitz_emit(tr, emit_key=args.emit or ("states" if args.mode == "territory" and len(args.states) > 1 else None)))
            if tr["mode"] == "territory_union":
                print(f"\n--- Summary: {tr['me_count']} ME states | "
                      f"internal straddlers auto-cancelled: {tr['internal_straddlers_cancelled']} | "
                      f"included outer straddlers: {len(tr['included_outer_straddlers'])} | "
                      f"excluded outer straddlers (>=20%): {len(tr['excluded_outer_straddlers'])} ---")
                for ex in tr["excluded_outer_straddlers"][:10]:
                    print(f"  [outer advisory, NOT included] ME #{ex['me_id']} {ex['me_name']} ({ex['me_owner']}): "
                          f"{ex['union_share']*100:.1f}% inside union (partition parent = v{ex['partition_v_id']} {ex['partition_v_name']})")
        return 0

    if args.provinces:
        tr = idx.translate_provinces(args.provinces)
        if args.json:
            print(json.dumps(tr, indent=2, ensure_ascii=False))
        else:
            for vp, info in tr["results"].items():
                print(f"Vanilla prov {vp} (v{info['v_state']} {info['v_state_name']}) -> "
                      f"primary ME prov {info['primary_me_prov']} | all ME provs ({len(info['me_provs'])}): {info['me_provs'][:10]} | "
                      f"ME states: {info['me_states']}")
            if tr["vanilla_provs_offmap_or_sea"]:
                print(f"Off-map / sea / unmatched vanilla provs: {tr['vanilla_provs_offmap_or_sea']}")
        return 0

    if args.snippet or args.diff:
        if args.diff:
            old_t = Path(args.diff[0]).read_text(encoding="utf-8-sig", errors="replace")
            new_t = Path(args.diff[1]).read_text(encoding="utf-8-sig", errors="replace")
            changed = _changed_lines_in_new(old_t, new_t)
            findings = analyze_clausewitz_text(new_t, idx, only_lines=changed)
        else:
            txt = Path(args.snippet).read_text(encoding="utf-8-sig", errors="replace")
            findings = analyze_clausewitz_text(txt, idx, only_lines=None)
        if args.json:
            print(json.dumps(findings, indent=2, ensure_ascii=False))
        else:
            print(f"Found {len(findings)} state/province reference group(s):\n")
            for item in findings:
                print(f"=== Line {item['line']} | {item['path']} [{item['kind']}] (vanilla={item['vanilla_ids']}) ===")
                tr = item["translation"]
                if tr["mode"] in ("territory_union", "city_primary"):
                    emit_k = item["key"] if not item["kind"].startswith("state_scope:") else None
                    print(format_clausewitz_emit(tr, emit_key=emit_k))
                else:
                    for vp, info in tr["results"].items():
                        print(f"  province = {info['primary_me_prov']} # was vanilla prov {vp} (v{info['v_state']} {info['v_state_name']}; all={info['me_provs'][:6]})")
                    if tr["vanilla_provs_offmap_or_sea"]:
                        print(f"  # OFF-MAP / sea vanilla provs: {tr['vanilla_provs_offmap_or_sea']}")
                print()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
