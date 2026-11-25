#!/usr/bin/env python
"""Audit and repair Magna Europa state/province references against docs/states/eu_mapping.

Pure Python stdlib (reuses EUMappingIndex from translate_vanilla_states.py).

Subcommands:
  --audit-annotations   Verify all active lines annotated with `# ... was vanilla <VID>`
                        or `# eu_mapping: v<VID>` against state_partition_v2m.tsv.
  --check-collapses     Compare core focus/event files against baseline commit `be350601`
                        (and detect single-state collapses where multi-member vanilla states
                        were shrunk to 1 ME state in territory context).
  --audit-offmap        List all active lines whose annotation refers to off-map vanilla IDs,
                        distinguishing valid ME proxy/inset states from EU hijack bugs.
  --fix-annotations     Safely fix unambiguous wrong-state annotations and report complex blocks.
  --audit-unmapped-vanilla
                        Compare mod blocks against vanilla HOI4 by block ID/name to catch
                        unannotated raw vanilla state IDs (e.g. overwritten during syncs or
                        newly added vanilla states) that --audit-annotations cannot see.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
from translate_vanilla_states import (  # noqa: E402
    EUMappingIndex,
    CITY_INNER_KEYS,
    CITY_SCALAR_KEYS,
    NON_STATE_BLOCKS,
    TERRITORY_INNER_KEYS,
    TERRITORY_SCALAR_KEYS,
)

REPO = Path(repo_root())
MOD_DIR = REPO / "mods" / "Magna-Europa"

def get_valid_offmap_me_states(idx: EUMappingIndex) -> frozenset[int]:
    """Derive excluded NA inset + proxy + synthetic border ME IDs from state_partition_m2v.tsv."""
    return frozenset(mid for mid, info in idx.m2v.items() if info["status"] != "mapped")

# Blocks where integer keys or numeric values are NEVER state IDs
IGNORE_BLOCK_KEYS = NON_STATE_BLOCKS | frozenset({
    "random_owned_controlled_state", "factor", "limit",
})
STATE_LHS_KEYS = frozenset(
    set(TERRITORY_SCALAR_KEYS)
    | set(CITY_SCALAR_KEYS)
    | {"state", "core_state", "state_id", "highlight_state_targets", "capital"}
)

RE_WAS_VANILLA = re.compile(
    r'#.*\b(?:was vanilla|eu_mapping:\s*v)\s*(?:state\s+)?([\d]+(?:\s*[,/+&]\s*\d+)*)'
)
RE_COMMENT_IS_NONSTATE = re.compile(
    r'#.*\b(?:was vanilla\s+(?:prov(?:ince)?|region|sr\b)|SR\s+\d+)', re.I
)
RE_STMT = re.compile(
    r'^\s*(?:(\d+)\s*=\s*\{|([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+))\b'
)
RE_FOCUS_OR_EVENT_ID = re.compile(r'^(?:\t|\s{4})id\s*=\s*([A-Za-z0-9_.]+)\b')

NORM_OWNER = {
    "ALW": "SYR", "JDZ": "SYR",
    "EGY": "ENG", "BHR": "ENG", "QAT": "ENG", "UAE": "ENG", "MLT": "ENG",
    "ISM": "ENG", "GUE": "ENG", "JER": "ENG",
    "MOR": "FRA", "TUN": "FRA", "MRT": "FRA", "MNC": "FRA", "ADR": "FRA",
    "DNZ": "POL", "SMR": "ITA", "VAT": "ITA", "TNG": "SPR", "LIE": "SWI",
}


def norm_owner(tag: str) -> str:
    return NORM_OWNER.get(tag, tag)


def iter_script_files(mod_dir: Path = MOD_DIR):
    for sub in ("common", "events", "history"):
        base = mod_dir / sub
        if not base.is_dir():
            continue
        for root, _, files in os.walk(base):
            for fn in sorted(files):
                if fn.endswith(".txt"):
                    p = Path(root) / fn
                    yield p, p.relative_to(mod_dir).as_posix()


def track_brace_context(lines: list[str]) -> list[tuple[str, ...]]:
    """Return active block-key stack at start of each 1-based line (fast brace scanner)."""
    stacks: list[tuple[str, ...]] = []
    stack: list[str] = []
    # Match tokens outside strings/comments
    tok_re = re.compile(r'"(?:[^"\\]|\\.)*"|#[^\n]*|\{|\}|([A-Za-z0-9_.:@^-]+)\s*=\s*\{')
    for line in lines:
        stacks.append(tuple(stack))
        pos = 0
        for m in tok_re.finditer(line):
            txt = m.group(0)
            if txt.startswith("#") or txt.startswith('"'):
                if txt.startswith("#"):
                    break
                continue
            if txt == "{":
                stack.append("_anon")
            elif txt == "}":
                if stack:
                    stack.pop()
            else:
                # key = {
                stack.append(m.group(1))
    return stacks


def scan_annotations(idx: EUMappingIndex, mod_dir: Path = MOD_DIR) -> list[dict]:
    """Scan all .txt files for state-ID lines annotated with was vanilla / eu_mapping."""
    valid_offmap = get_valid_offmap_me_states(idx)
    findings = []
    for path, rel in iter_script_files(mod_dir):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        stacks = track_brace_context(lines)
        for ln_i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            code_part = line.split("#", 1)[0]
            if not code_part.strip():
                continue
            if "#" not in line:
                continue
            comment_part = line[line.index("#"):]
            if RE_COMMENT_IS_NONSTATE.search(comment_part):
                continue
            m_ann = RE_WAS_VANILLA.search(comment_part)
            if not m_ann:
                continue
            m_stmt = RE_STMT.match(code_part)
            if not m_stmt:
                continue

            stk = stacks[ln_i - 1]
            # Exclude lines strictly inside random_list / victory_points / province / build_railway / region
            if any(k in ("random_list", "victory_points", "provinces", "path", "build_railway") for k in stk):
                continue

            if m_stmt.group(1):
                lhs_key = "<scope>"
                me_id = int(m_stmt.group(1))
            else:
                lhs_key = m_stmt.group(2)
                me_id = int(m_stmt.group(3))
                if lhs_key not in STATE_LHS_KEYS:
                    continue

            vids = [int(x) for x in re.findall(r'\d+', m_ann.group(1))]
            if not vids:
                continue

            eu_vids = [v for v in vids if v in idx.v2m]
            off_vids = [v for v in vids if v not in idx.v2m]
            mi = idx.m2v.get(me_id)

            if not eu_vids:
                status = "OFFMAP_VALID_PROXY" if (me_id in valid_offmap) else "OFFMAP_EU_HIJACK"
                findings.append({
                    "file": rel,
                    "line": ln_i,
                    "lhs_key": lhs_key,
                    "me_id": me_id,
                    "me_name": mi["me_name"] if mi else "?",
                    "me_owner": mi["me_owner"] if mi else "?",
                    "vids": vids,
                    "eu_vids": [],
                    "off_vids": off_vids,
                    "status": status,
                    "text": line,
                })
                continue

            union_members = set()
            v_owners = set()
            for v in eu_vids:
                union_members.update(idx.v2m[v]["me_members"])
                v_owners.add(norm_owner(idx.v2m[v]["vanilla_owner"]))

            if me_id in union_members:
                status = "OK"
            else:
                me_norm_owner = norm_owner(mi["me_owner"]) if mi else "?"
                status = "MISMATCH_SAME_OWNER" if me_norm_owner in v_owners else "MISMATCH_WRONG_OWNER"

            findings.append({
                "file": rel,
                "line": ln_i,
                "lhs_key": lhs_key,
                "me_id": me_id,
                "me_name": mi["me_name"] if mi else "?",
                "me_owner": mi["me_owner"] if mi else "?",
                "actual_v_id": mi["v_id"] if mi else 0,
                "actual_v_name": mi["v_name"] if mi else "",
                "vids": vids,
                "eu_vids": eu_vids,
                "off_vids": off_vids,
                "status": status,
                "text": line,
            })
    return findings


def _count_block_state_refs(text: str) -> dict[str, dict]:
    """Extract state-ref counts per named focus/event/decision block."""
    pat_ref = re.compile(
        r'(?m)^\s*(\d+)\s*=\s*\{|(?:owns_state|controls_state|transfer_state|set_state_owner|'
        r'set_state_controller|add_state_core|remove_state_core|add_state_claim|remove_state_claim|'
        r'state|capital)\s*=\s*(\d+)\b'
    )
    lines = text.splitlines()
    blocks: dict[str, dict] = {}
    cur_id = None
    cur_lines: list[str] = []

    def _flush():
        if not cur_id:
            return
        clean = "\n".join(l.split("#", 1)[0] for l in cur_lines)
        refs = []
        for m in pat_ref.finditer(clean):
            v = int(m.group(1) or m.group(2))
            if 1 <= v <= 2986:
                refs.append(v)
        # Heuristic check for non-state random_list blocks omitted by keeping distinct IDs count + raw count
        blocks[cur_id] = {"total": len(refs), "uniq": len(set(refs)), "ids": sorted(set(refs))}

    for l in lines:
        m = RE_FOCUS_OR_EVENT_ID.match(l)
        if m:
            _flush()
            cur_id = m.group(1)
            cur_lines = [l]
        elif cur_id is not None:
            cur_lines.append(l)
    _flush()
    return blocks


def cmd_audit_annotations(args):
    idx = EUMappingIndex(Path(args.mapping_dir))
    findings = scan_annotations(idx)
    c = Counter(f["status"] for f in findings)
    print(f"Annotated state lines scanned: {len(findings)}")
    for k, v in sorted(c.items()):
        print(f"  {k:24s}: {v}")

    bad = [f for f in findings if f["status"] in ("MISMATCH_WRONG_OWNER", "MISMATCH_SAME_OWNER")]
    if args.file:
        bad = [f for f in bad if args.file in f["file"]]

    by_file = defaultdict(list)
    for b in bad:
        by_file[b["file"]].append(b)
    print(f"\nEuropean MISMATCHES: {len(bad)} across {len(by_file)} files")
    for rel, items in sorted(by_file.items(), key=lambda x: -len(x[1])):
        wo = sum(1 for x in items if x["status"] == "MISMATCH_WRONG_OWNER")
        so = len(items) - wo
        print(f"  {rel:58s} total={len(items):3d} (wrong_owner={wo:2d}, same_owner={so:3d})")
        if args.verbose:
            for it in items[:25]:
                v_str = ",".join(str(v) for v in it["eu_vids"])
                vnames = "/".join(idx.v2m[v]["vanilla_name"] for v in it["eu_vids"][:2])
                print(
                    f"      L{it['line']:5d} [{it['status']:20s}] "
                    f"me={it['me_id']}({it['me_name']},{it['me_owner']} -> actual v{it['actual_v_id']}) "
                    f"vs claimed v[{v_str}]({vnames}) | city={idx.v2m[it['eu_vids'][0]]['primary_me_id']}"
                )
    return 0


def cmd_audit_offmap(args):
    idx = EUMappingIndex(Path(args.mapping_dir))
    findings = scan_annotations(idx)
    off = [f for f in findings if f["status"].startswith("OFFMAP_")]
    by_pair = defaultdict(list)
    for f in off:
        by_pair[(f["status"], tuple(f["off_vids"]), f["me_id"], f["me_name"], f["me_owner"])].append((f["file"], f["line"]))
    print(f"Off-map annotated state refs: {len(off)} ({sum(1 for x in off if x['status']=='OFFMAP_EU_HIJACK')} EU hijacks)")
    for (st, vids, mid, mname, mown), locs in sorted(by_pair.items()):
        sample = ", ".join(f"{fl}:{ln}" for fl, ln in locs[:3])
        print(f"  [{st:18s}] v{list(vids)} -> ME {mid:4d} {mname:22s} ({mown:3s}) x{len(locs):2d}  e.g. {sample}")
    return 0


def cmd_check_collapses(args):
    baseline = args.baseline
    default_files = [
        "mods/Magna-Europa/common/national_focus/germany.txt",
        "mods/Magna-Europa/common/national_focus/hungary_wuw.txt",
        "mods/Magna-Europa/common/national_focus/yugoslavia.txt",
        "mods/Magna-Europa/events/Germany.txt",
        "mods/Magna-Europa/events/WUW_Germany.txt",
        "mods/Magna-Europa/common/national_focus/france.txt",
        "mods/Magna-Europa/common/national_focus/austria.txt",
        "mods/Magna-Europa/events/BFTB_Greece.txt",
        "mods/Magna-Europa/events/MTG_Britain.txt",
        "mods/Magna-Europa/common/national_focus/poland.txt",
        "mods/Magna-Europa/events/Yugoslavia.txt",
        "mods/Magna-Europa/events/MUN_Czechoslovakia.txt",
        "mods/Magna-Europa/common/national_focus/czechoslovakia_mu.txt",
        "mods/Magna-Europa/common/national_focus/turkey.txt",
        "mods/Magna-Europa/events/LaR_France.txt",
        "mods/Magna-Europa/common/on_actions/09_aat_on_actions.txt",
        "mods/Magna-Europa/events/BFTB_Turkey.txt",
        "mods/Magna-Europa/events/Finland.txt",
        "mods/Magna-Europa/events/France.txt",
        "mods/Magna-Europa/common/national_focus/sweden.txt",
        "mods/Magna-Europa/events/WTT_Germany.txt",
        "mods/Magna-Europa/common/national_focus/finland.txt",
        "mods/Magna-Europa/common/national_focus/switzerland.txt",
    ]
    files = args.files or default_files
    flagged = 0
    for f in files:
        p = REPO / f
        if not p.is_file():
            continue
        try:
            old_txt = subprocess.check_output(["git", "show", f"{baseline}:{f}"], cwd=REPO, text=True, encoding="utf-8", errors="replace")
        except subprocess.CalledProcessError:
            continue
        new_txt = p.read_text(encoding="utf-8-sig", errors="replace")
        ob = _count_block_state_refs(old_txt)
        nb = _count_block_state_refs(new_txt)
        diffs = []
        for k, ov in ob.items():
            nv = nb.get(k, {"total": 0, "uniq": 0})
            if ov["total"] - nv["total"] >= args.min_drop or (ov["uniq"] >= 8 and nv["uniq"] < ov["uniq"] * 0.6):
                diffs.append((ov["total"] - nv["total"], k, ov["total"], nv["total"], ov["uniq"], nv["uniq"]))
        diffs.sort(reverse=True)
        print(f"=== {f} ({len(diffs)} blocks with state-ref drop >= {args.min_drop}) ===")
        for d, k, ot, nt, ou, nu in diffs:
            flagged += 1
            print(f"  {k:48s} total {ot:4d}->{nt:4d} (delta={-d:+d}) | uniq {ou:3d}->{nu:3d}")
    print(f"\nTotal flagged collapsed blocks: {flagged}")
    return 0


def cmd_fix_annotations(args):
    """Conservatively fix MISMATCH_WRONG_OWNER / MISMATCH_SAME_OWNER lines whose fix is unambiguous."""
    idx = EUMappingIndex(Path(args.mapping_dir))
    findings = scan_annotations(idx)
    bad = [f for f in findings if f["status"] in ("MISMATCH_WRONG_OWNER", "MISMATCH_SAME_OWNER")]
    if args.file:
        bad = [f for f in bad if args.file in f["file"]]

    # Group by file & scan enclosing block context before deciding action
    by_file = defaultdict(list)
    for b in bad:
        by_file[b["file"]].append(b)

    fixed_comment_only = 0
    fixed_city_primary = 0
    fixed_single_to_single_parent = 0
    manual_review = []

    for rel, items in sorted(by_file.items()):
        path = MOD_DIR / rel
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)
        has_bom = path.read_bytes().startswith(b"\xef\xbb\xbf")
        stacks = track_brace_context([l.rstrip("\r\n") for l in lines])

        # Also collect all active ME state IDs & claimed vanilla IDs per top-2 block window (+/- 25 lines)
        changed = False
        for it in items:
            ln_i = it["line"] - 1
            raw = lines[ln_i]
            eol = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
            line = raw.rstrip("\r\n")
            me_id = it["me_id"]
            actual_v = it["actual_v_id"]
            actual_vn = it["actual_v_name"]
            eu_vids = it["eu_vids"]
            stk = stacks[ln_i]

            # Check if single claimed vid & its primary city vs territory mode
            is_city_ctx = (
                it["lhs_key"] in CITY_SCALAR_KEYS
                or any(k in CITY_INNER_KEYS for k in stk)
            )
            # Scan sibling lines +/- 30 lines for whether primary_me_id or members of eu_vids ALREADY exist nearby
            w0 = max(0, ln_i - 30)
            w1 = min(len(lines), ln_i + 31)
            nearby_ids = set()
            for j in range(w0, w1):
                if j == ln_i:
                    continue
                mj = RE_STMT.match(lines[j].split("#", 1)[0])
                if mj and (mj.group(1) or mj.group(2) in STATE_LHS_KEYS):
                    nearby_ids.add(int(mj.group(1) or mj.group(3)))

            primary_target = idx.v2m[eu_vids[0]]["primary_me_id"]
            union_target = []
            for v in eu_vids:
                union_target.extend(idx.v2m[v]["me_members"])

            # Heuristic A: If actual_v != 0 and same owner as eu_vids[0] AND another member of eu_vids[0] IS ALREADY in nearby_ids,
            # AND actual_v is part of the surrounding union/region (SWI/Poland/Aragon), this is a stale per-line comment!
            has_real_member_nearby = bool(set(union_target) & nearby_ids)

            if has_real_member_nearby and actual_v and it["status"] == "MISMATCH_SAME_OWNER":
                # Fix only the comment so it cites actual_v accurately
                code_part, comment_part = line.split("#", 1)
                new_line = f"{code_part.rstrip()} # eu_mapping: v{actual_v} ({actual_vn}) -> ME {me_id} ({it['me_name']}){eol}"
                if not args.dry_run:
                    lines[ln_i] = new_line
                    changed = True
                fixed_comment_only += 1
            elif len(eu_vids) == 1 and (is_city_ctx or len(idx.v2m[eu_vids[0]]["me_members"]) == 1) and primary_target and primary_target not in nearby_ids:
                # Replace wrong ME ID with exact primary_me_id!
                code_part, _ = line.split("#", 1)
                new_code = re.sub(rf'\b{me_id}\b', str(primary_target), code_part, count=1)
                mi_new = idx.m2v.get(primary_target, {})
                v0 = eu_vids[0]
                vn0 = idx.v2m[v0]["vanilla_name"]
                new_line = f"{new_code.rstrip()} # eu_mapping: v{v0} ({vn0}) -> ME {primary_target} ({mi_new.get('me_name', '')}){eol}"
                if not args.dry_run:
                    lines[ln_i] = new_line
                    changed = True
                if is_city_ctx:
                    fixed_city_primary += 1
                else:
                    fixed_single_to_single_parent += 1
            else:
                manual_review.append(it)

        if changed and not args.dry_run:
            enc = "utf-8-sig" if has_bom else "utf-8"
            path.write_text("".join(lines), encoding=enc)

    print(f"Fix summary (dry_run={args.dry_run}):")
    print(f"  Comment fixed (same-owner sibling union):   {fixed_comment_only}")
    print(f"  City/1-to-1 primary_me_id replaced:         {fixed_city_primary}")
    print(f"  1-member territory primary_me_id replaced:  {fixed_single_to_single_parent}")
    print(f"  Needs multi-state union/manual expansion:   {len(manual_review)}")
    by_f = defaultdict(list)
    for it in manual_review:
        by_f[it["file"]].append(it)
    for rel, items in sorted(by_f.items(), key=lambda x: -len(x[1]))[:40]:
        sample = items[0]
        print(f"    {rel:55s} ({len(items):2d}) e.g. L{sample['line']} [{sample['status']}] me={sample['me_id']}({sample['me_name']},{sample['me_owner']}) claimed v{sample['eu_vids']}")
    return 0


# ---------------------------------------------------------------------------
# Block-level unmapped vanilla state-ID leak audit (--audit-unmapped-vanilla)
# ---------------------------------------------------------------------------

DEFAULT_VANILLA_DIR = (
    os.environ.get("HOI4_DIR")
    or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
)

UNMAPPED_STATE_KEYS = frozenset({
    "owns_state", "controls_state", "has_full_control_of_state",
    "transfer_state", "transfer_state_to", "add_state_core", "remove_state_core",
    "add_state_claim", "remove_state_claim", "set_state_owner", "set_state_controller",
    "set_capital", "capital", "state", "core_state", "state_id",
    "add_contested_owner", "remove_contested_owner", "has_resources_rights",
    "goto_state", "add_state_modifier", "add_state_research_slot",
})

UNMAPPED_NON_STATE_BLOCKS = frozenset({
    "random_list", "victory_points", "provinces", "path", "build_railway",
    "ai_chance", "ai_will_do", "modifier", "region", "strategic_region",
    "resources", "equipment", "add_equipment_to_stockpile", "send_equipment",
    "unit_names", "divisions", "ships", "air_wings", "weight", "chance",
    "add_victory_points", "set_province_name", "buildings",
})

_RE_TOK_BRACE = re.compile(r'"(?:[^"\\]|\\.)*"|#[^\n]*|\{|\}|([A-Za-z0-9_.:@^-]+)\s*=\s*\{')
_RE_INLINE_KV = re.compile(r"\b(" + "|".join(sorted(UNMAPPED_STATE_KEYS)) + r")\s*=\s*(\d+)\b")
_RE_INLINE_SCOPE = re.compile(r"(?:^|[\s{])(\d+)\s*=\s*\{")
_RE_SCOPE_CHAIN = re.compile(r"\b(\d+)\.(?:owner|controller|OWNER|CONTROLLER)\b")
_RE_STATES_LIST = re.compile(r"\b(states|generator)\s*=\s*\{([^}]*)\}")
_RE_MIGRATION_CMT = re.compile(r"\b(?:was vanilla\b|eu_mapping:|MEPORT\b|MER:|1\.19\b|1\.20\b)", re.I)
_RE_NONSTATE_INLINE = re.compile(
    r"\b(?:naval_base|bunker|coastal_bunker|supply_node|rocket_site|land_fort|"
    r"factor|modifier|add_attack|add_defense|add_logistics|add_planning)\b"
)

UNMAPPED_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("events", "event"),
    ("common/national_focus", "focus"),
    ("common/decisions", "decision"),
    ("common/scripted_effects", "effect"),
    ("common/scripted_triggers", "trigger"),
    ("common/on_actions", "on_action"),
    ("common/ai_strategy", "strategy"),
)


def _load_vanilla_state_catalog(repo_dir: Path = REPO) -> dict[int, dict]:
    """Load full vanilla state table (European + off-map) from docs/states/v2m_states.tsv."""
    path = repo_dir / "docs" / "states" / "v2m_states.tsv"
    catalog: dict[int, dict] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            catalog[int(r["vanilla_id"])] = r
    return catalog


def _parse_named_blocks(text: str, mode: str) -> dict[str, list[tuple[int, str]]]:
    """Split a Clausewitz script into named blocks {block_id: [(1-based line_no, line), ...]}."""
    lines = text.splitlines()
    blocks: dict[str, list[tuple[int, str]]] = {}
    depth = 0
    cur_name: str | None = None
    cur_start_depth = 0
    cur_lines: list[tuple[int, str]] = []
    id_re = re.compile(r"^\s*id\s*=\s*([A-Za-z0-9_.-]+)\b")
    key_open_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*\{")
    event_open_re = re.compile(
        r"^(?:country_event|news_event|state_event|unit_leader_event|operative_leader_event)\s*=\s*\{"
    )
    focus_open_re = re.compile(r"^(?:focus|shared_focus|joint_focus)\s*=\s*\{")

    for ln, line in enumerate(lines, 1):
        code = line.split("#", 1)[0]
        stripped = code.strip()
        if cur_name is None:
            if mode == "event" and depth == 0 and event_open_re.match(stripped):
                cur_name = f"__pending_L{ln}"
                cur_start_depth = 0
                cur_lines = [(ln, line)]
            elif mode == "focus" and depth == 1 and focus_open_re.match(stripped):
                cur_name = f"__pending_L{ln}"
                cur_start_depth = 1
                cur_lines = [(ln, line)]
            elif mode in ("trigger", "effect", "strategy") and depth == 0:
                m = key_open_re.match(stripped)
                if m:
                    cur_name = m.group(1)
                    cur_start_depth = 0
                    cur_lines = [(ln, line)]
            elif mode in ("decision", "on_action") and depth == 1:
                m = key_open_re.match(stripped)
                if m:
                    cur_name = m.group(1)
                    cur_start_depth = 1
                    cur_lines = [(ln, line)]
        else:
            cur_lines.append((ln, line))
            if cur_name.startswith("__pending_") and depth == cur_start_depth + 1:
                m = id_re.match(code)
                if m:
                    cur_name = m.group(1)

        for m in _RE_TOK_BRACE.finditer(line):
            t = m.group(0)
            if t.startswith("#"):
                break
            if t.startswith('"'):
                continue
            if t == "{" or t.endswith("{"):
                depth += 1
            elif t == "}":
                depth = max(0, depth - 1)

        if cur_name is not None and depth <= cur_start_depth:
            if not cur_name.startswith("__pending_"):
                blocks.setdefault(cur_name, []).extend(cur_lines)
            cur_name = None
            cur_lines = []
    return blocks


def _extract_block_state_refs(numbered_lines: list[tuple[int, str]]) -> list[tuple[str, int, int, str, bool, set[int]]]:
    """Return list of (kind, sid, line_no, stripped_line, has_migration_cmt, cited_vids)."""
    stack: list[str] = []
    refs: list[tuple[str, int, int, str, bool, set[int]]] = []
    for ln, line in numbered_lines:
        code = line.split("#", 1)[0]
        comment = line[line.index("#"):] if "#" in line else ""
        has_cmt = bool(_RE_MIGRATION_CMT.search(comment))
        cited_vids: set[int] = set()
        if comment and not RE_COMMENT_IS_NONSTATE.search(comment):
            m_ann = RE_WAS_VANILLA.search(comment)
            if m_ann:
                cited_vids = {int(x) for x in re.findall(r"\d+", m_ann.group(1))}

        in_bad = any(k in UNMAPPED_NON_STATE_BLOCKS for k in stack)
        if not in_bad and code.strip():
            # Inside `id = <state_id>` in ai_strategy with type = conquer / invade / antagonize etc.,
            # only explicit state keys are matched (not generic `id =`).
            for m in _RE_INLINE_KV.finditer(code):
                k, sid = m.group(1), int(m.group(2))
                if 1 <= sid <= 3000:
                    refs.append((k, sid, ln, line.strip(), has_cmt, cited_vids))
            for m in _RE_INLINE_SCOPE.finditer(code):
                sid = int(m.group(1))
                after = code[m.end():]
                if _RE_NONSTATE_INLINE.search(after):
                    continue
                if 1 <= sid <= 3000:
                    refs.append(("<scope>", sid, ln, line.strip(), has_cmt, cited_vids))
            for m in _RE_SCOPE_CHAIN.finditer(code):
                sid = int(m.group(1))
                if 1 <= sid <= 3000:
                    refs.append(("<chain>", sid, ln, line.strip(), has_cmt, cited_vids))
            for m in _RE_STATES_LIST.finditer(code):
                list_key = m.group(1)
                for num in re.findall(r"\b(\d+)\b", m.group(2)):
                    sid = int(num)
                    if 1 <= sid <= 3000:
                        refs.append((f"{list_key}_list", sid, ln, line.strip(), has_cmt, cited_vids))

        for m in _RE_TOK_BRACE.finditer(line):
            t = m.group(0)
            if t.startswith("#"):
                break
            if t.startswith('"'):
                continue
            if t == "{":
                stack.append("_anon")
            elif t == "}":
                if stack:
                    stack.pop()
            else:
                stack.append(m.group(1))
    return refs


def scan_unmapped_vanilla(
    idx: EUMappingIndex,
    vanilla_dir: Path,
    mod_dir: Path = MOD_DIR,
    categories: tuple[tuple[str, str], ...] = UNMAPPED_CATEGORIES,
    file_filter: str = "",
) -> list[dict]:
    """Find unannotated (or half-remapped) raw vanilla state IDs by block-level vanilla diff."""
    v_catalog = _load_vanilla_state_catalog(REPO)
    findings: list[dict] = []

    for cat, mode in categories:
        mdir = mod_dir / cat
        if not mdir.is_dir():
            continue
        for p in sorted(mdir.rglob("*.txt")):
            rel = p.relative_to(mod_dir).as_posix()
            if file_filter and file_filter not in rel:
                continue
            vp = vanilla_dir / rel
            if not vp.is_file():
                continue

            m_blks = _parse_named_blocks(p.read_text(encoding="utf-8-sig", errors="replace"), mode)
            v_blks = _parse_named_blocks(vp.read_text(encoding="utf-8-sig", errors="replace"), mode)

            for bname, mlns in m_blks.items():
                if bname not in v_blks:
                    continue
                v_refs = _extract_block_state_refs(v_blks[bname])
                if not v_refs:
                    continue
                m_refs = _extract_block_state_refs(mlns)
                if not m_refs:
                    continue

                v_ids = {r[1] for r in v_refs if r[1] in v_catalog}
                v_pairs = Counter((r[0], r[1]) for r in v_refs if r[1] in v_catalog)
                m_ids = {r[1] for r in m_refs}

                for k, sid, ln, line, has_cmt, cited_vids in m_refs:
                    if (k, sid) not in v_pairs:
                        continue
                    # Exempt annotated lines UNLESS the code still literally contains a cited vanilla ID
                    # (e.g. `158 = { is_controlled_by = 120.OWNER } # was vanilla 120` -> 120 is still leaked!).
                    if has_cmt and sid not in cited_vids:
                        continue

                    if sid in idx.v2m:
                        members = set(idx.v2m[sid]["me_members"])
                        if sid in members:
                            continue  # valid self-map
                        mi = idx.m2v.get(sid)
                        actual_v = mi["v_id"] if mi else 0
                        # If ME state `sid` is legitimately a member of another vanilla state in this block
                        # AND vanilla state `sid` already has its own ME members present in this block, skip.
                        if actual_v in v_ids and (members & m_ids):
                            continue
                        findings.append({
                            "category": cat,
                            "file": rel,
                            "block": bname,
                            "line": ln,
                            "kind": k,
                            "sid": sid,
                            "status": "EU_UNMAPPED_VANILLA",
                            "v_name": v_catalog[sid]["vanilla_name"],
                            "v_owner": v_catalog[sid]["owner"],
                            "me_name": mi["me_name"] if mi else "?",
                            "me_owner": mi["me_owner"] if mi else "?",
                            "primary_me_id": idx.v2m[sid]["primary_me_id"],
                            "me_members": list(idx.v2m[sid]["me_members"]),
                            "text": line,
                        })
                    elif sid in v_catalog:
                        mi = idx.m2v.get(sid)
                        if mi and mi["status"] == "mapped":
                            if mi["v_id"] in v_ids:
                                continue  # legitimate ME member of an EU vanilla state in this block
                            findings.append({
                                "category": cat,
                                "file": rel,
                                "block": bname,
                                "line": ln,
                                "kind": k,
                                "sid": sid,
                                "status": "OFFMAP_UNMAPPED_VANILLA",
                                "v_name": v_catalog[sid]["vanilla_name"],
                                "v_owner": v_catalog[sid]["owner"],
                                "me_name": mi["me_name"],
                                "me_owner": mi["me_owner"],
                                "primary_me_id": 0,
                                "me_members": [],
                                "text": line,
                            })
    return findings


def cmd_audit_unmapped_vanilla(args):
    vanilla_dir = Path(args.vanilla)
    if not vanilla_dir.is_dir():
        print(
            f"ERROR: Vanilla HOI4 directory not found: {vanilla_dir}\n"
            "Pass --vanilla <path> or set HOI4_DIR.",
            file=sys.stderr,
        )
        return 2

    idx = EUMappingIndex(Path(args.mapping_dir))
    cats = UNMAPPED_CATEGORIES
    if args.category:
        cats = tuple((c, m) for c, m in UNMAPPED_CATEGORIES if args.category in c)
        if not cats:
            print(f"ERROR: Unknown --category filter: {args.category}", file=sys.stderr)
            return 2

    findings = scan_unmapped_vanilla(
        idx, vanilla_dir=vanilla_dir, mod_dir=MOD_DIR, categories=cats, file_filter=args.file
    )

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_cat[f["category"]].append(f)

    print(f"Unmapped vanilla state-ID leaks (block-level vs {vanilla_dir}):")
    print(f"{'CATEGORY':28s} {'FILES':>5s} {'BLOCKS':>6s} {'EU_BAD':>6s} {'OFFMAP':>6s} {'TOTAL':>6s}")
    print("-" * 64)
    tot_f = tot_b = tot_eu = tot_off = 0
    for cat, _ in cats:
        items = by_cat.get(cat, [])
        nf = len({x["file"] for x in items})
        nb = len({(x["file"], x["block"]) for x in items})
        neu = sum(1 for x in items if x["status"] == "EU_UNMAPPED_VANILLA")
        noff = len(items) - neu
        tot_f += nf
        tot_b += nb
        tot_eu += neu
        tot_off += noff
        print(f"{cat:28s} {nf:5d} {nb:6d} {neu:6d} {noff:6d} {len(items):6d}")
    print("-" * 64)
    print(f"{'TOTAL':28s} {tot_f:5d} {tot_b:6d} {tot_eu:6d} {tot_off:6d} {len(findings):6d}")

    by_file: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_file[f["file"]].append(f)

    if by_file:
        print("\nPer-file breakdown:")
        for rel, items in sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            nblks = len({x["block"] for x in items})
            neu = sum(1 for x in items if x["status"] == "EU_UNMAPPED_VANILLA")
            noff = len(items) - neu
            top_blks = Counter(x["block"] for x in items).most_common(4)
            sample = ", ".join(f"{b}({c})" for b, c in top_blks)
            print(f"  {rel:52s} blks={nblks:3d} tot={len(items):4d} (eu={neu:4d}, off={noff:3d}) | {sample}")
            if args.verbose:
                for it in items[:40]:
                    tag = "EU" if it["status"] == "EU_UNMAPPED_VANILLA" else "OFF"
                    tgt = f"-> city={it['primary_me_id']} members={it['me_members'][:4]}" if tag == "EU" else "-> OFFMAP"
                    print(
                        f"      L{it['line']:5d} [{tag:3s}] {it['block']:32s} "
                        f"{it['kind']}={it['sid']} (van:{it['v_name']}[{it['v_owner']}] vs ME:{it['me_name']}[{it['me_owner']}]) {tgt}"
                    )

    if args.json:
        Path(args.json).write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote JSON report: {args.json} ({len(findings)} rows)")

    if args.check and findings:
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit & repair Magna Europa state/province script refs.")
    ap.add_argument("--mapping-dir", default=str(REPO / "docs" / "states" / "eu_mapping"))
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("audit-annotations")
    p1.add_argument("--file", default="", help="Substring filter for relative path")
    p1.add_argument("--verbose", action="store_true")

    p2 = sub.add_parser("audit-offmap")

    p3 = sub.add_parser("check-collapses")
    p3.add_argument("--baseline", default="be350601")
    p3.add_argument("--min-drop", type=int, default=6)
    p3.add_argument("files", nargs="*")

    p4 = sub.add_parser("fix-annotations")
    p4.add_argument("--file", default="", help="Substring filter")
    p4.add_argument("--dry-run", action="store_true")

    p5 = sub.add_parser("audit-unmapped-vanilla")
    p5.add_argument("--vanilla", default=DEFAULT_VANILLA_DIR, help="Vanilla HOI4 install directory")
    p5.add_argument("--category", default="", help="Substring filter for category")
    p5.add_argument("--file", default="", help="Substring filter for relative path")
    p5.add_argument("--verbose", action="store_true")
    p5.add_argument("--json", default="", help="Optional path to write full JSON findings")
    p5.add_argument("--check", action="store_true", help="Exit 1 if any unmapped vanilla leaks remain")

    # Also allow convenience flags directly
    ap.add_argument("--audit-annotations", action="store_true")
    ap.add_argument("--audit-offmap", action="store_true")
    ap.add_argument("--check-collapses", action="store_true")
    ap.add_argument("--fix-annotations", action="store_true")
    ap.add_argument("--audit-unmapped-vanilla", action="store_true")
    ap.add_argument("--vanilla", default=DEFAULT_VANILLA_DIR)
    ap.add_argument("--category", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--file", default="")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--baseline", default="be350601")
    ap.add_argument("--min-drop", type=int, default=6)

    args = ap.parse_args(argv)
    if args.cmd == "audit-annotations" or args.audit_annotations:
        return cmd_audit_annotations(args)
    if args.cmd == "audit-offmap" or args.audit_offmap:
        return cmd_audit_offmap(args)
    if args.cmd == "check-collapses" or args.check_collapses:
        if not hasattr(args, "files"):
            args.files = []
        return cmd_check_collapses(args)
    if args.cmd == "fix-annotations" or args.fix_annotations:
        return cmd_fix_annotations(args)
    if args.cmd == "audit-unmapped-vanilla" or args.audit_unmapped_vanilla:
        return cmd_audit_unmapped_vanilla(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
