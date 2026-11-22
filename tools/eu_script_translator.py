#!/usr/bin/env python
"""Span-aware Clausewitz script translator backed by docs/states/eu_mapping.

Translates Vanilla HOI4 scripts (or single extracted blocks) into Magna Europa
state & province IDs while enforcing 4 safety invariants:
  1. Never mutates non-state numeric contexts (`random_list`, `ai_will_do`,
     `ai_chance`, `modifier`, `victory_points`, `region`, `strategic_region`,
     `provinces`, `unit_names`, weight blocks, numeric params like `days`/`factor`).
  2. Enforces the Province-in-State invariant: `province = <vp>` inside `<vid> = { ... }`
     maps to a verified land ME province AND wraps/moves the inner effect into the
     exact host ME state containing that ME province.
  3. Distinguishes territory (1->N `v2m[vid].me_members`) vs city (1->1 `primary_me_id`)
     by enclosing/inner Clausewitz keywords (`TERRITORY_*` vs `CITY_*`).
  4. Handles `build_railway` (`start_province`/`target_province` and `path = { ... }`)
     using verified land ME provinces, and comments out off-map vanilla IDs cleanly.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
from pdx_parse import Parser  # noqa: E402
from translate_vanilla_states import (  # noqa: E402
    EUMappingIndex,
    CITY_INNER_KEYS,
    CITY_SCALAR_KEYS,
    NON_STATE_BLOCKS,
    PROVINCE_KEYS,
    STATE_QUANTIFIER_SCOPES,
    TERRITORY_INNER_KEYS,
    TERRITORY_LIST_KEYS,
    TERRITORY_SCALAR_KEYS,
)

REPO = Path(repo_root())
MOD_DIR = REPO / "mods" / "Magna-Europa"

# Expanded keyword classification for full Clausewitz parity
EXTRA_CITY_INNER = frozenset({
    "add_state_modifier", "add_dynamic_modifier", "remove_dynamic_modifier",
    "set_building_level", "add_building_construction", "add_extra_state_shared_building_slots",
    "free_building_slots", "add_resource", "add_manpower", "add_victory_points",
    "set_victory_points", "damage_building", "set_state_name", "reset_state_name",
    "set_province_name", "reset_province_name", "set_capital", "add_compliance",
    "add_resistance", "set_compliance", "set_resistance", "add_province_modifier",
    "add_province_modifier", "remove_province_modifier", "give_resource_rights",
    "set_state_category",
}) | CITY_INNER_KEYS

EXTRA_TERR_INNER = frozenset({
    "transfer_state_to", "set_state_owner", "set_state_controller", "set_state_owner_to",
    "set_state_controller_to", "add_core_of", "remove_core_of", "add_claim_by",
    "remove_claim_by", "set_demilitarized_zone", "is_demilitarized_zone",
    "is_owned_by", "is_controlled_by", "is_fully_controlled_by",
    "is_owned_and_controlled_by", "is_core_of", "is_claimed_by", "owner", "controller",
    "set_state_flag", "clr_state_flag", "has_state_flag", "start_resistance",
    "cancel_resistance", "force_enable_resistance", "force_disable_resistance",
    "add_to_array", "add_to_temp_array", "set_border_war", "calculate_claimant_effect",
}) | TERRITORY_INNER_KEYS

IMMUNE_BLOCKS = NON_STATE_BLOCKS | frozenset({
    "ai_chance", "ai_will_do", "random_list", "modifier", "victory_points",
    "provinces", "region", "regions", "strategic_region", "strategic_regions",
    "unit_names", "divisions", "ships", "air_wings", "resources", "manpower",
    "equipment", "trigger", "mean_time_to_happen",
})

# Blocks whose *content* contains non-state raw ID lists (never recurse as state IDs)
NON_STATE_CONTAINERS = NON_STATE_BLOCKS | frozenset({
    "victory_points", "provinces", "region", "regions",
    "strategic_region", "strategic_regions", "resources", "equipment",
    "add_equipment_to_stockpile", "add_equipment_production",
    "send_equipment", "add_tech_bonus",
}) - frozenset({"random_list", "ai_will_do", "modifier", "build_railway", "path"})

# Blocks whose immediate `<N> = { ... }` integer children are weights, NOT state IDs
EXTRA_TERR_LIST_KEYS = TERRITORY_LIST_KEYS | frozenset({
    "generator", "owns_any_state_of", "target", "targets",
})

WEIGHTED_LIST_BLOCKS = frozenset({"random_list"})

TERR_SCALAR_ALL = TERRITORY_SCALAR_KEYS | frozenset({
    "transfer_state", "transfer_state_to", "owns_state", "controls_state",
    "has_full_control_of_state", "add_state_core", "remove_state_core",
    "add_state_claim", "remove_state_claim", "set_state_owner",
    "set_state_controller", "is_owned_by", "is_controlled_by",
    "set_demilitarized_zone", "add_contested_owner", "remove_contested_owner",
    "core_state",
})
CITY_SCALAR_ALL = CITY_SCALAR_KEYS | frozenset({"capital", "set_capital"})

_SPAN_TOK = re.compile(r'''
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>\#[^\n]*)
  | (?P<open>\{)
  | (?P<close>\})
  | (?P<op>[<>=])
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<lit>[^\s{}<>=#"']+)
''', re.X)


@dataclass
class SpanNode:
    key: str
    op: str | None
    val: str | None
    line: int
    start: int
    end: int
    children: list["SpanNode"] | None = None
    modifier: str | None = None

    @property
    def is_block(self) -> bool:
        return self.children is not None


def parse_spans(text: str) -> tuple[list[SpanNode], list[str]]:
    """Parse Clausewitz text preserving exact character [start, end) offsets."""
    nl = [0] + [m.end() for m in re.finditer(r'\n', text)]
    toks = []
    for m in _SPAN_TOK.finditer(text):
        k = m.lastgroup
        if k in ("ws", "comment"):
            continue
        toks.append((k, m.group(0), m.start(), m.end(), bisect.bisect_right(nl, m.start())))

    errors: list[str] = []
    idx = 0
    n_tok = len(toks)

    def _parse_list(stop_close: bool, depth: int = 0) -> list[SpanNode]:
        nonlocal idx
        nodes: list[SpanNode] = []
        while idx < n_tok:
            tk, tv, ts, te, tln = toks[idx]
            if tk == "close":
                if stop_close:
                    break
                errors.append(f"stray '}}' at line {tln}")
                idx += 1
                continue
            if tk not in ("lit", "str"):
                idx += 1
                continue
            key = tv[1:-1] if tk == "str" else tv
            idx += 1
            if idx < n_tok and toks[idx][0] == "op":
                op = toks[idx][1]
                idx += 1
                if idx >= n_tok:
                    nodes.append(SpanNode(key, op, None, tln, ts, toks[idx - 1][3]))
                    break
                k3, v3, s3, e3, _ = toks[idx]
                if k3 == "open":
                    idx += 1
                    kids = _parse_list(True, depth + 1)
                    if idx < n_tok and toks[idx][0] == "close":
                        c_end = toks[idx][3]
                        idx += 1
                    else:
                        c_end = toks[idx - 1][3] if idx > 0 else e3
                        errors.append(f"unclosed '{{' opened at line {tln} ({key})")
                    nodes.append(SpanNode(key, op, None, tln, ts, c_end, kids))
                elif k3 in ("lit", "str"):
                    idx += 1
                    val = v3[1:-1] if k3 == "str" else v3
                    if idx < n_tok and toks[idx][0] == "open":
                        idx += 1
                        kids = _parse_list(True, depth + 1)
                        if idx < n_tok and toks[idx][0] == "close":
                            c_end = toks[idx][3]
                            idx += 1
                        else:
                            c_end = toks[idx - 1][3]
                            errors.append(f"unclosed '{{' at line {tln} ({key}={val})")
                        nodes.append(SpanNode(key, op, None, tln, ts, c_end, kids, modifier=val))
                    else:
                        nodes.append(SpanNode(key, op, val, tln, ts, e3))
                else:
                    nodes.append(SpanNode(key, op, None, tln, ts, toks[idx - 1][3]))
            else:
                nodes.append(SpanNode(key, None, None, tln, ts, te))
        return nodes

    root = _parse_list(False, 0)
    return root, errors


def load_me_state_provs(mod_dir: Path = MOD_DIR) -> tuple[dict[int, set[int]], dict[int, int], set[int]]:
    """Read current ME history/states provinces and map/definition.csv land provinces."""
    state_provs: dict[int, set[int]] = {}
    prov_host: dict[int, int] = {}
    land_provs: set[int] = set()

    def_csv = mod_dir / "map" / "definition.csv"
    if def_csv.is_file():
        with open(def_csv, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                parts = line.strip().split(";")
                if len(parts) >= 5 and parts[0].isdigit() and parts[4].lower() == "land":
                    land_provs.add(int(parts[0]))

    st_dir = mod_dir / "history" / "states"
    re_id = re.compile(r'\bid\s*=\s*(\d+)')
    re_provs = re.compile(r'provinces\s*=\s*\{([^}]*)\}', re.S)
    if st_dir.is_dir():
        for fn in os.listdir(st_dir):
            if not fn.endswith(".txt"):
                continue
            txt = (st_dir / fn).read_text(encoding="utf-8-sig", errors="replace")
            mid_m = re_id.search(txt)
            pm = re_provs.search(txt)
            if mid_m and pm:
                sid = int(mid_m.group(1))
                provs = {int(x) for x in pm.group(1).split() if x.isdigit()}
                state_provs[sid] = provs
                for p in provs:
                    prov_host[p] = sid
    return state_provs, prov_host, land_provs


class ClausewitzTranslator:
    """Deterministic span-preserving Vanilla -> Magna Europa Clausewitz translator."""

    def __init__(self, mapping_dir: Path | None = None, mod_dir: Path = MOD_DIR):
        self.idx = EUMappingIndex(mapping_dir) if mapping_dir else EUMappingIndex()
        self.idx._ensure_provinces()
        self.state_provs, self.prov_host, self.land_provs = load_me_state_provs(mod_dir)
        self.stats = defaultdict(int)

    def resolve_me_prov(self, vanilla_prov: int, preferred_v_state: int | None = None) -> tuple[int, int] | None:
        """Return (me_prov, host_me_state) guaranteed land & verified in host state's provinces=[] block."""
        hits = self.idx._vprov_to_mprovs.get(vanilla_prov, ())
        land_hits = [
            h for h in hits
            if (not self.land_provs or h["me_prov"] in self.land_provs)
            and h["me_prov"] in self.prov_host
        ]
        if not land_hits:
            return None
        if preferred_v_state and preferred_v_state in self.idx.v2m:
            pref_states = set(self.idx.v2m[preferred_v_state]["me_members"])
            in_pref = [h for h in land_hits if self.prov_host[h["me_prov"]] in pref_states]
            if in_pref:
                in_pref.sort(key=lambda h: (-h["me_px"], h["me_prov"]))
                mp = in_pref[0]["me_prov"]
                return mp, self.prov_host[mp]
        land_hits.sort(key=lambda h: (-h["me_px"], h["me_prov"]))
        mp = land_hits[0]["me_prov"]
        return mp, self.prov_host[mp]

    def _collect_provinces_in_subtree(self, node: SpanNode) -> list[int]:
        out = []
        if not node.is_block and node.key in PROVINCE_KEYS and node.val and node.val.isdigit():
            out.append(int(node.val))
        if node.children:
            for ch in node.children:
                out.extend(self._collect_provinces_in_subtree(ch))
        return out

    def _is_border_or_coastal_bunker(self, node: SpanNode) -> bool:
        """Check whether an add_building_construction uses all_provinces / limit_to_border."""
        txt_keys = set()
        def _walk(n: SpanNode):
            txt_keys.add(n.key)
            if n.children:
                for ch in n.children:
                    _walk(ch)
        _walk(node)
        return bool(txt_keys & {"all_provinces", "limit_to_border", "limit_to_coastal", "limit_to_naval_base"})

    def _infer_scope_mode(self, node: SpanNode) -> str:
        """Decide territory vs city vs province_container for `<VID> = { ... }`."""
        provs = self._collect_provinces_in_subtree(node)
        if provs:
            return "province_container"
        city_hits = 0
        terr_hits = 0
        def _walk(n: SpanNode):
            nonlocal city_hits, terr_hits
            if n.key == "add_building_construction" and self._is_border_or_coastal_bunker(n):
                terr_hits += 2
                return
            if n.key in EXTRA_CITY_INNER:
                city_hits += 1
            elif n.key in EXTRA_TERR_INNER:
                terr_hits += 1
            elif n.key in NON_STATE_CONTAINERS:
                return
            if n.children:
                for ch in n.children:
                    _walk(ch)
        for ch in node.children or ():
            _walk(ch)
        if city_hits > 0 and terr_hits == 0:
            return "city"
        return "territory"

    def _line_indent(self, text: str, offset: int) -> str:
        ls = text.rfind("\n", 0, offset) + 1
        seg = text[ls:offset]
        m = re.match(r'^([ \t]*)', seg)
        return m.group(1) if m else ""

    def _has_closing_brace_on_same_line(self, text: str, pos: int) -> bool:
        nl = text.find("\n", pos)
        rest = text[pos:] if nl == -1 else text[pos:nl]
        return "}" in rest.split("#", 1)[0]

    def _translate_inner_provs(self, snippet: str, vid: int) -> str:
        """Replace all `province = N` inside a single-host snippet with host-matched ME prov."""
        nodes, errs = parse_spans(snippet)
        if errs:
            return snippet
        edits: list[tuple[int, int, str]] = []

        def _walk(nlist: list[SpanNode]):
            for n in nlist:
                if not n.is_block and n.key in PROVINCE_KEYS and n.val and n.val.isdigit():
                    vp = int(n.val)
                    res = self.resolve_me_prov(vp, preferred_v_state=vid)
                    if res:
                        mp, hs = res
                        self.stats["provinces_translated"] += 1
                        if self._has_closing_brace_on_same_line(snippet, n.end):
                            edits.append((n.start, n.end, f"{n.key} {n.op} {mp}"))
                        else:
                            edits.append((n.start, n.end, f"{n.key} {n.op} {mp} # eu_mapping: v_prov {vp} (in ME {hs})"))
                    else:
                        self.stats["provinces_offmap"] += 1
                elif n.children:
                    _walk(n.children)

        _walk(nodes)
        return self._apply_edits(snippet, edits)

    @staticmethod
    def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
        out = []
        pos = 0
        for s, e, rep in sorted(edits, key=lambda x: (x[0], x[1])):
            if s < pos:
                continue
            out.append(text[pos:s])
            out.append(rep)
            pos = e
        out.append(text[pos:])
        return "".join(out)

    def translate_text(
        self,
        text: str,
        *,
        preserve_mode_comments: bool = True,
        force_city_keys: set[str] | None = None,
        _nested: bool = False,
    ) -> tuple[str, dict]:
        """Translate Vanilla state/province refs in Clausewitz text cleanly."""
        nodes, errs = parse_spans(text)
        if errs:
            raise ValueError(f"Parse errors in input text: {errs[:5]}")
        if not _nested:
            self.stats.clear()
        edits: list[tuple[int, int, str]] = []

        def _visit_list(nlist: list[SpanNode], path: tuple[str, ...]):
            for n in nlist:
                k = n.key
                if n.is_block:
                    # 1. Explicit railway builder
                    if k == "build_railway":
                        rep = self._translate_build_railway(text, n)
                        if rep is not None:
                            edits.append((n.start, n.end, rep))
                        continue

                    # 2. Non-state numeric containers (do not recurse)
                    if k in NON_STATE_CONTAINERS:
                        continue
                    # Weighted list (e.g. `random_list = { 40 = { 46 = { ... } } }`):
                    # skip weight key itself, DO recurse into its children!
                    if path and path[-1] in WEIGHTED_LIST_BLOCKS and k.isdigit():
                        _visit_list(n.children, path + (k,))
                        continue
                    # Heuristic guard: weight block starting directly with modifier/factor/base
                    if k.isdigit():
                        first_k = n.children[0].key if n.children else ""
                        if first_k in ("modifier", "factor", "base", "add_attack", "add_defense", "add_logistics", "add_planning") and any("weight" in p or "random" in p or "chance" in p for p in path):
                            _visit_list(n.children, path + (k,))
                            continue

                    # 3. Territory list blocks or province list blocks (`highlight_provinces`)
                    if k == "highlight_provinces" and any(ch.key.isdigit() and ch.val is None and not ch.is_block for ch in n.children):
                        rep = self._translate_prov_list_block(text, n)
                        edits.append((n.start, n.end, rep))
                        continue
                    if k in EXTRA_TERR_LIST_KEYS or (k == "prioritize" and path and path[-1] in STATE_QUANTIFIER_SCOPES):
                        if any(ch.key.isdigit() and ch.val is None and not ch.is_block for ch in n.children):
                            rep = self._translate_id_list_block(text, n, city_mode=(k == "prioritize"))
                            edits.append((n.start, n.end, rep))
                            continue

                    # 4. Numeric state scope `<VID> = { ... }`
                    if k.isdigit() and 1 <= int(k) <= 1200:
                        vid = int(k)
                        rep = self._translate_numeric_scope(text, n, vid)
                        if rep is not None:
                            edits.append((n.start, n.end, rep))
                        continue

                    _visit_list(n.children, path + (k,))
                else:
                    val = n.val or ""
                    if not val.isdigit():
                        continue
                    num = int(val)
                    indent = self._line_indent(text, n.start)

                    same_line_close = self._has_closing_brace_on_same_line(text, n.end)

                    if k in PROVINCE_KEYS:
                        res = self.resolve_me_prov(num)
                        if res:
                            mp, hs = res
                            self.stats["provinces_translated"] += 1
                            if same_line_close:
                                edits.append((n.start, n.end, f"{k} {n.op} {mp}"))
                            else:
                                edits.append((n.start, n.end, f"{k} {n.op} {mp} # eu_mapping: v_prov {num} (ME {hs})"))
                        else:
                            self.stats["provinces_offmap"] += 1
                        continue

                    if k == "generator":
                        if num in self.idx.v2m:
                            info = self.idx.v2m[num]
                            self.stats["id_list_blocks"] += 1
                            ids_str = " ".join(str(m) for m in info["me_members"])
                            if same_line_close:
                                edits.append((n.start, n.end, f"generator = {{ {ids_str} }}"))
                            else:
                                edits.append((
                                    n.start, n.end,
                                    f"generator = {{ {ids_str} }} # eu_mapping: v{num} ({info['vanilla_name']})"
                                ))
                        elif 1 <= num <= 1200 and not same_line_close:
                            self.stats["offmap_skipped"] += 1
                            edits.append((n.start, n.end, f"# off-map in Magna Europa: {text[n.start:n.end]}"))
                        continue

                    # Exclude numeric params that happen to be digits outside state refs
                    if k in CITY_SCALAR_ALL:
                        if num in self.idx.primary_city:
                            pm = self.idx.primary_city[num]
                            self.stats["city_scalars"] += 1
                            if same_line_close:
                                edits.append((n.start, n.end, f"{k} {n.op} {pm['primary_me_id']}"))
                            else:
                                edits.append((
                                    n.start, n.end,
                                    f"{k} {n.op} {pm['primary_me_id']} # eu_mapping: v{num} ({pm['vanilla_name']}) -> ME {pm['primary_me_id']} ({pm['primary_me_name']})"
                                ))
                        elif 1 <= num <= 1200 and not same_line_close:
                            self.stats["offmap_skipped"] += 1
                            edits.append((n.start, n.end, f"# off-map in Magna Europa: {text[n.start:n.end]}"))
                        continue

                    if k in TERR_SCALAR_ALL or (k == "state" and (not path or path[-1] != "victory_points")):
                        if force_city_keys and k in force_city_keys:
                            mode = "city"
                        elif k == "state" and path and path[-1] in EXTRA_CITY_INNER:
                            mode = "city"
                        else:
                            mode = "territory"

                        if num not in self.idx.v2m:
                            if 1 <= num <= 1200 and not same_line_close:
                                self.stats["offmap_skipped"] += 1
                                edits.append((n.start, n.end, f"# off-map in Magna Europa: {text[n.start:n.end]}"))
                            continue

                        if mode == "city":
                            pm = self.idx.primary_city[num]
                            self.stats["city_scalars"] += 1
                            if same_line_close:
                                edits.append((n.start, n.end, f"{k} {n.op} {pm['primary_me_id']}"))
                            else:
                                edits.append((
                                    n.start, n.end,
                                    f"{k} {n.op} {pm['primary_me_id']} # eu_mapping: v{num} ({pm['vanilla_name']}) -> ME {pm['primary_me_id']} ({pm['primary_me_name']})"
                                ))
                        else:
                            info = self.idx.v2m[num]
                            self.stats["territory_scalars"] += 1
                            rep_lines = []
                            for i_m, mid in enumerate(info["me_members"]):
                                mi = self.idx.m2v[mid]
                                pref = "" if i_m == 0 else indent
                                if same_line_close:
                                    rep_lines.append(f"{pref}{k} {n.op} {mid}")
                                else:
                                    rep_lines.append(
                                        f"{pref}{k} {n.op} {mid} # eu_mapping: v{num} ({info['vanilla_name']}) -> ME {mid} ({mi['me_name']})"
                                    )
                            edits.append((n.start, n.end, "\n".join(rep_lines)))

        _visit_list(nodes, ())
        result = self._apply_edits(text, edits)
        _, post_errs = parse_spans(result)
        if post_errs:
            raise RuntimeError(f"Post-translation parse error: {post_errs[:5]}")
        return result, dict(self.stats)

    def _translate_build_railway(self, text: str, n: SpanNode) -> str | None:
        indent = self._line_indent(text, n.start)
        inner_indent = indent + "\t"
        parts = []
        offmap_vp = []
        had_start = False
        had_target = False
        for ch in n.children or ():
            if not ch.is_block and ch.key in ("start_province", "target_province") and ch.val and ch.val.isdigit():
                vp = int(ch.val)
                res = self.resolve_me_prov(vp)
                if res:
                    mp, hs = res
                    if ch.key == "start_province":
                        had_start = True
                    else:
                        had_target = True
                    self.stats["railway_provs"] += 1
                    parts.append(f"{inner_indent}{ch.key} = {mp} # eu_mapping: v_prov {vp} (ME {hs})")
                else:
                    offmap_vp.append(vp)
            elif ch.is_block and ch.key in ("path", "fallback_path"):
                vprovs = [int(x.key) for x in ch.children or () if x.key.isdigit()]
                if ch.key == "path" and len(vprovs) >= 2:
                    r0 = self.resolve_me_prov(vprovs[0])
                    r1 = self.resolve_me_prov(vprovs[-1])
                    if r0 and r1 and r0[0] != r1[0]:
                        if not had_start:
                            parts.append(f"{inner_indent}start_province = {r0[0]} # eu_mapping: v_prov {vprovs[0]} (ME {r0[1]})")
                            had_start = True
                        if not had_target:
                            parts.append(f"{inner_indent}target_province = {r1[0]} # eu_mapping: v_prov {vprovs[-1]} (ME {r1[1]})")
                            had_target = True
                        self.stats["railway_paths_converted"] += 1
                    elif r0 and r1 and r0[0] == r1[0]:
                        # Start and end fell in the same single ME province: keep valid adjacent neighbor if available, or comment
                        self.stats["railway_collapsed_same_prov"] += 1
                        return "\n".join(
                            f"{indent}# eu_mapping: skipped single-ME-prov railway (vanilla {vprovs[0]}..{vprovs[-1]} -> ME {r0[0]})"
                            if i == 0 else f"{indent}# {l.lstrip()}"
                            for i, l in enumerate(text[n.start:n.end].splitlines())
                        )
                    else:
                        offmap_vp.extend(vprovs)
                # drop raw fallback_path/path lists so stale vanilla provs don't cause invalid chain errors
            else:
                parts.append(f"{inner_indent}{text[ch.start:ch.end]}")

        if offmap_vp:
            self.stats["offmap_skipped"] += 1
            return "\n".join(f"{indent}# off-map in Magna Europa: {l.lstrip()}" if i > 0 else f"# off-map in Magna Europa: {l}" for i, l in enumerate(text[n.start:n.end].splitlines()))
        return f"build_railway = {{\n" + "\n".join(parts) + f"\n{indent}}}"

    def _translate_prov_list_block(self, text: str, n: SpanNode) -> str:
        indent = self._line_indent(text, n.start)
        inner_indent = indent + "\t"
        vprovs = [int(ch.key) for ch in n.children or () if ch.key.isdigit()]
        out_provs = []
        seen = set()
        for vp in vprovs:
            res = self.resolve_me_prov(vp)
            if not res:
                out_provs.append(f"{inner_indent}# off-map vanilla prov {vp}")
            else:
                mp, hs = res
                if mp not in seen:
                    seen.add(mp)
                    out_provs.append(f"{inner_indent}{mp} # eu_mapping: v_prov {vp} (ME {hs})")
        self.stats["prov_list_blocks"] += 1
        return f"{n.key} = {{\n" + "\n".join(out_provs) + f"\n{indent}}}"

    def _translate_id_list_block(self, text: str, n: SpanNode, city_mode: bool) -> str:
        indent = self._line_indent(text, n.start)
        inner_indent = indent + "\t"
        vids = [int(ch.key) for ch in n.children or () if ch.key.isdigit()]
        out_ids = []
        seen = set()
        comments = []
        for vid in vids:
            if vid not in self.idx.v2m:
                comments.append(f"{inner_indent}# off-map vanilla {vid}")
                continue
            if city_mode:
                pm = self.idx.primary_city[vid]
                mid = pm["primary_me_id"]
                if mid not in seen:
                    seen.add(mid)
                    out_ids.append(f"{inner_indent}{mid} # eu_mapping: v{vid} ({pm['vanilla_name']}) -> ME {mid} ({pm['primary_me_name']})")
            else:
                info = self.idx.v2m[vid]
                for mid in info["me_members"]:
                    if mid not in seen:
                        seen.add(mid)
                        mi = self.idx.m2v[mid]
                        out_ids.append(f"{inner_indent}{mid} # eu_mapping: v{vid} ({info['vanilla_name']}) -> ME {mid} ({mi['me_name']})")
        self.stats["id_list_blocks"] += 1
        return f"{n.key} = {{\n" + "\n".join(comments + out_ids) + f"\n{indent}}}"

    def _translate_numeric_scope(self, text: str, n: SpanNode, vid: int) -> str | None:
        indent = self._line_indent(text, n.start)
        raw_block = text[n.start:n.end]
        if vid not in self.idx.v2m:
            # Off-map numeric state scope: comment every line safely
            self.stats["offmap_skipped"] += 1
            blines = raw_block.splitlines()
            return "\n".join(
                (f"# off-map in Magna Europa: {l}" if i == 0 else f"{indent}# off-map: {l.lstrip()}")
                for i, l in enumerate(blines)
            )

        mode = self._infer_scope_mode(n)
        open_brace = raw_block.find("{")
        close_brace = raw_block.rfind("}")
        inner = raw_block[open_brace + 1:close_brace]

        if mode == "province_container":
            # Split per host ME state if inner children hold province = N pointing to different ME states!
            # Special common pattern: `76 = { add_building_construction = { ... province = 9704 } add_building_construction = { ... province = 746 } }`
            # Or `81 = { if = { limit = { is_controlled_by = ROOT } add_building_construction = { ... province = 6652 } } }`
            provs = self._collect_provinces_in_subtree(n)
            host_pairs = []
            for vp in provs:
                res = self.resolve_me_prov(vp, preferred_v_state=vid)
                if res:
                    host_pairs.append((vp, res[0], res[1]))
            unique_hosts = sorted({hs for _, _, hs in host_pairs})

            if len(unique_hosts) <= 1:
                hs = unique_hosts[0] if unique_hosts else self.idx.v2m[vid]["primary_me_id"]
                mi = self.idx.m2v.get(hs, {})
                inner_tx = self._translate_inner_provs(inner, vid)
                if "\n" not in inner_tx and "#" in inner_tx:
                    inner_tx = inner_tx + "\n" + indent
                elif "#" in inner_tx.rsplit("\n", 1)[-1]:
                    inner_tx = inner_tx + "\n" + indent
                self.stats["prov_container_single"] += 1
                return f"{hs} = {{ # eu_mapping: v{vid} ({self.idx.v2m[vid]['vanilla_name']}) -> ME {hs} ({mi.get('me_name', '')}){inner_tx}}}"

            # Multi-host: split direct children containing provinces into separate host state scopes!
            child_blocks_by_host: dict[int, list[str]] = defaultdict(list)
            shared_children: list[str] = []
            for ch in n.children or ():
                ch_provs = self._collect_provinces_in_subtree(ch)
                ch_str = text[ch.start:ch.end]
                if not ch_provs:
                    ch_tx, _ = self.translate_text(ch_str, _nested=True)
                    shared_children.append(ch_tx)
                else:
                    res = self.resolve_me_prov(ch_provs[0], preferred_v_state=vid)
                    hs = res[1] if res else self.idx.v2m[vid]["primary_me_id"]
                    child_blocks_by_host[hs].append(self._translate_inner_provs(ch_str, vid))

            out_scopes = []
            inner_indent = indent + "\t"
            for i_h, hs in enumerate(unique_hosts):
                mi = self.idx.m2v.get(hs, {})
                pref = "" if i_h == 0 else indent
                body_items = []
                for sc in shared_children:
                    body_items.append(f"{inner_indent}{sc}")
                for cb in child_blocks_by_host.get(hs, []):
                    body_items.append(f"{inner_indent}{cb}")
                out_scopes.append(
                    f"{pref}{hs} = {{ # eu_mapping: v{vid} ({self.idx.v2m[vid]['vanilla_name']}) -> ME {hs} ({mi.get('me_name', '')})\n"
                    + "\n".join(body_items)
                    + f"\n{indent}}}"
                )
            self.stats["prov_container_split"] += 1
            return "\n".join(out_scopes)

        # Also translate any nested state/province refs inside `inner` first!
        inner_tx, _ = self.translate_text(inner, _nested=True)
        same_line_close = self._has_closing_brace_on_same_line(text, n.end)
        if "\n" not in inner_tx and "\r" not in inner_tx:
            # Single-line block (e.g. `42 = { is_demilitarized_zone = no }`):
            # NEVER place `# ...` between `{` and `}`, or if same_line_close is True, NEVER place `#` after final `}`!
            inner_clean = inner_tx.strip()
            # If inner_tx itself got a trailing `# eu_mapping:` comment, move it outside `}`
            if "#" in inner_clean:
                code_part, c_part = inner_clean.split("#", 1)
                inner_clean = code_part.strip()
                extra_c = f" ; {c_part.strip()}"
            else:
                extra_c = ""
            if mode == "city":
                pm = self.idx.primary_city[vid]
                mid = pm["primary_me_id"]
                self.stats["city_scopes"] += 1
                if same_line_close:
                    return f"{mid} = {{ {inner_clean} }}"
                return f"{mid} = {{ {inner_clean} }} # eu_mapping: v{vid} ({pm['vanilla_name']}) -> ME {mid} ({pm['primary_me_name']}){extra_c}"
            info = self.idx.v2m[vid]
            self.stats["territory_scopes"] += 1
            out_scopes = []
            last_i = len(info["me_members"]) - 1
            for i_m, mid in enumerate(info["me_members"]):
                mi = self.idx.m2v[mid]
                pref = "" if i_m == 0 else indent
                if same_line_close and i_m == last_i:
                    out_scopes.append(f"{pref}{mid} = {{ {inner_clean} }}")
                else:
                    out_scopes.append(
                        f"{pref}{mid} = {{ {inner_clean} }} # eu_mapping: v{vid} ({info['vanilla_name']}) -> ME {mid} ({mi['me_name']}){extra_c}"
                    )
            return "\n".join(out_scopes)

        # Multi-line block: if last line of inner_tx has a `#` comment without trailing newline, break line before `}`
        last_line = inner_tx.rsplit("\n", 1)[-1]
        if "#" in last_line:
            inner_tx = inner_tx + "\n" + indent

        if mode == "city":
            pm = self.idx.primary_city[vid]
            mid = pm["primary_me_id"]
            self.stats["city_scopes"] += 1
            return f"{mid} = {{ # eu_mapping: v{vid} ({pm['vanilla_name']}) -> ME {mid} ({pm['primary_me_name']}){inner_tx}}}"

        info = self.idx.v2m[vid]
        self.stats["territory_scopes"] += 1
        out_scopes = []
        for i_m, mid in enumerate(info["me_members"]):
            mi = self.idx.m2v[mid]
            pref = "" if i_m == 0 else indent
            out_scopes.append(
                f"{pref}{mid} = {{ # eu_mapping: v{vid} ({info['vanilla_name']}) -> ME {mid} ({mi['me_name']}){inner_tx}}}"
            )
        return "\n".join(out_scopes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Span-aware Clausewitz script translator using docs/states/eu_mapping.")
    ap.add_argument("--input", required=True, help="Vanilla input file path")
    ap.add_argument("--output", help="Write translated output here (default: print summary or stdout)")
    ap.add_argument("--block-id", help="Extract & translate a single focus/event/decision block by id = <ID>")
    args = ap.parse_args(argv)

    txt = Path(args.input).read_text(encoding="utf-8-sig", errors="replace")
    if args.block_id:
        m = re.search(rf'(?m)^\t*(?:focus|country_event|news_event|state_event)\s*=\s*\{{(?=(?:(?!\n\t*(?:focus|country_event|news_event|state_event)\s*=).)*?\bid\s*=\s*{re.escape(args.block_id)}\b)', txt, re.S)
        if not m:
            raise SystemExit(f"Block id={args.block_id} not found")
        # Extract balanced block
        depth = 0; i = txt.find("{", m.start()); n = len(txt); in_s = False; in_c = False
        while i < n:
            c = txt[i]
            if in_c:
                if c == "\n": in_c = False
            elif in_s:
                if c == '"' and txt[i-1] != '\\': in_s = False
            else:
                if c == '#': in_c = True
                elif c == '"': in_s = True
                elif c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
            i += 1
        txt = txt[m.start():i]

    tr = ClausewitzTranslator()
    out, stats = tr.translate_text(txt)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {args.output} | stats={stats}")
    else:
        print(out)
        print(f"# stats={stats}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
