#!/usr/bin/env python
"""Vanilla <-> Magna Europa Strategic Region (Layer 5) query, translation & script audit CLI.

Pure Python stdlib at runtime (reads docs/states/eu_mapping/region_*.tsv).

Subcommands:
  --regions 1 2 7 ...      Translate Vanilla strategic region IDs -> ME regions
  --me-regions 45 46 ...   Reverse-translate ME strategic region IDs -> Vanilla parent/proxy
  --audit-scripts          Scan all ME-modified common/ and events/ files for region refs,
                           separating valid ME IDs, invalid/out-of-range IDs, off-map/inset IDs,
                           and Vanilla-parity collisions where ID matches Vanilla file / mismatch name.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402

REPO = Path(repo_root())
DEFAULT_MAPPING_DIR = REPO / "docs" / "states" / "eu_mapping"
DEFAULT_MOD_DIR = REPO / "mods" / "Magna-Europa"

VANILLA_CANDIDATES = [
    os.environ.get("HOI4_DIR", ""),
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"C:\Program Files\Steam\steamapps\common\Hearts of Iron IV",
]

AI_STRATEGY_SR_TYPES = frozenset({
    "strategic_air_importance",
    "naval_avoid_region",
    "naval_convoy_raid_region",
    "naval_dominance",
    "naval_invasion_support_priority",
    "strike_force_home_base",
})

RE_SR_SCALAR = re.compile(
    r"\b(region|strategic_region|naval_avoid_region|is_in_strategic_region)\s*=\s*(\d+)\b"
)


def _find_vanilla() -> Path | None:
    for c in VANILLA_CANDIDATES:
        if c and (Path(c) / "map" / "strategicregions").is_dir():
            return Path(c)
    return None


class EURegionMappingIndex:
    """In-memory index over docs/states/eu_mapping/region_{overlap,partition_m2v,partition_v2m}.tsv."""

    def __init__(self, mapping_dir: Path = DEFAULT_MAPPING_DIR) -> None:
        self.mapping_dir = Path(mapping_dir)
        self.m2v: dict[int, dict] = {}
        self.v2m: dict[int, dict] = {}
        self.overlap_by_v: dict[int, list[dict]] = defaultdict(list)
        self.overlap_by_m: dict[int, list[dict]] = defaultdict(list)

        with open(self.mapping_dir / "region_partition_m2v.tsv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                mid = int(row["me_region_id"])
                vid = int(row["vanilla_region_id"]) if row["vanilla_region_id"] else None
                proxy = int(row["proxy_vanilla_id"]) if row["proxy_vanilla_id"] else None
                self.m2v[mid] = {
                    "me_region_id": mid,
                    "me_name": row["me_name"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "vanilla_region_id": vid,
                    "vanilla_name": row["vanilla_name"],
                    "primary_share": float(row["primary_share"]),
                    "me_px": int(row["me_px"]),
                    "proxy_vanilla_id": proxy,
                    "proxy_vanilla_name": row["proxy_vanilla_name"],
                    "all_vanilla_overlaps": row["all_vanilla_overlaps"],
                    "notes": row["notes"],
                }

        with open(self.mapping_dir / "region_partition_v2m.tsv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                vid = int(row["vanilla_region_id"])
                me_regs = [int(x) for x in row["me_regions"].split("|") if x]
                self.v2m[vid] = {
                    "vanilla_region_id": vid,
                    "vanilla_name": row["vanilla_name"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "me_count": int(row["me_count"]),
                    "primary_me_region": int(row["primary_me_region"]),
                    "primary_me_name": row["primary_me_name"],
                    "me_regions": me_regs,
                    "me_region_names": row["me_region_names"].split("|") if row["me_region_names"] else [],
                    "v_coverage": float(row["v_coverage"]),
                }

        with open(self.mapping_dir / "region_overlap.tsv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                rec = {
                    "vanilla_region_id": int(row["vanilla_region_id"]),
                    "vanilla_name": row["vanilla_name"],
                    "me_region_id": int(row["me_region_id"]),
                    "me_name": row["me_name"],
                    "kind": row["kind"],
                    "overlap_px": float(row["overlap_px"]),
                    "me_total_px": int(row["me_total_px"]),
                    "me_share": float(row["me_share"]),
                    "v_total_px": int(row["v_total_px"]),
                    "v_share": float(row["v_share"]),
                    "relation_class": row["relation_class"],
                }
                self.overlap_by_v[rec["vanilla_region_id"]].append(rec)
                self.overlap_by_m[rec["me_region_id"]].append(rec)

    def translate_region(self, v_rid: int, *, include_proxy: bool = False, min_v_share: float = 0.0) -> dict:
        """Translate a Vanilla SR ID into covered ME strategic regions."""
        entry = self.v2m.get(v_rid)
        if not entry:
            return {
                "vanilla_region_id": v_rid,
                "status": "offmap_unmapped",
                "me_regions": [],
                "me_region_names": [],
            }
        if entry["status"] != "mapped" and not include_proxy:
            return {
                "vanilla_region_id": v_rid,
                "vanilla_name": entry["vanilla_name"],
                "kind": entry["kind"],
                "status": entry["status"],
                "me_regions": [],
                "me_region_names": [],
                "proxy_me_regions": entry["me_regions"],
            }
        me_regs = list(entry["me_regions"])
        if min_v_share > 0 and entry["status"] == "mapped":
            # Optionally append significant straddlers captured by another primary region
            seen = set(me_regs)
            for ov in sorted(self.overlap_by_v.get(v_rid, []), key=lambda x: -x["overlap_px"]):
                if ov["me_region_id"] not in seen and ov["me_share"] >= min_v_share:
                    me_regs.append(ov["me_region_id"])
                    seen.add(ov["me_region_id"])
        return {
            "vanilla_region_id": v_rid,
            "vanilla_name": entry["vanilla_name"],
            "kind": entry["kind"],
            "status": entry["status"],
            "primary_me_region": me_regs[0] if me_regs else None,
            "me_regions": me_regs,
            "me_region_names": [self.m2v[m]["me_name"] for m in me_regs],
            "v_coverage": entry["v_coverage"],
        }

    def reverse_region(self, m_rid: int) -> dict | None:
        return self.m2v.get(m_rid)


def _extract_ai_strategy_sr_hits(lines: list[str]) -> list[tuple[int, str, int, str]]:
    """Return (line_no, strat_type, rid, line_text) for ai_strategy blocks with numeric SR id."""
    text = "".join(lines)
    out = []
    for m in re.finditer(r"ai_strategy\s*=\s*\{([^}]*)\}", text):
        blk = m.group(1)
        tm = re.search(r"\btype\s*=\s*([A-Za-z0-9_]+)", blk)
        if not tm or tm.group(1) not in AI_STRATEGY_SR_TYPES:
            continue
        stype = tm.group(1)
        # Find each `id = <N>` inside non-comment portion
        base_offset = m.start(1)
        for im in re.finditer(r"(?m)^[^\S\n]*id\s*=\s*(\d+)\b", blk):
            abs_pos = base_offset + im.start()
            ln_no = text[:abs_pos].count("\n") + 1
            out.append((ln_no, stype, int(im.group(1)), lines[ln_no - 1].strip()))
    return out


def _find_ai_strategy_duplicates(lines: list[str]) -> list[tuple[int, str, int, str]]:
    """Detect duplicate (type, id) SR entries within the same top-level ai_strategy block."""
    text = "".join(lines)
    dups: list[tuple[int, str, int, str]] = []
    i = 0
    n = len(text)
    for top in re.finditer(r"(?m)^([A-Za-z0-9_]+)\s*=\s*\{", text):
        if top.start() < i:
            continue
        d = 0
        end = top.end() - 1
        for k in range(top.end() - 1, n):
            if text[k] == "{":
                d += 1
            elif text[k] == "}":
                d -= 1
                if d == 0:
                    end = k + 1
                    break
        i = end
        blk_txt = text[top.start() : end]
        seen: set[tuple[str, int]] = set()
        for m in re.finditer(r"ai_strategy\s*=\s*\{([^}]*)\}", blk_txt):
            sub_clean = "\n".join(l.split("#", 1)[0] for l in m.group(1).splitlines())
            tm = re.search(r"\btype\s*=\s*([A-Za-z0-9_]+)", sub_clean)
            if not tm or tm.group(1) not in AI_STRATEGY_SR_TYPES:
                continue
            stype = tm.group(1)
            for im in re.finditer(r"(?m)^[^\S\n]*id\s*=\s*(\d+)\b", sub_clean):
                rid = int(im.group(1))
                if (stype, rid) in seen:
                    abs_pos = top.start() + m.start(1) + im.start()
                    ln_no = text[:abs_pos].count("\n") + 1
                    dups.append((ln_no, stype, rid, f"{top.group(1)}: {lines[ln_no - 1].strip()}"))
                seen.add((stype, rid))
    return dups


def audit_scripts(idx: EURegionMappingIndex, mod_dir: Path = DEFAULT_MOD_DIR) -> int:
    van_dir = _find_vanilla()
    findings_by_file: dict[str, list[dict]] = defaultdict(list)
    cat_counts: Counter = Counter()

    for sub in ("common", "events"):
        root_dir = mod_dir / sub
        if not root_dir.is_dir():
            continue
        for root, _, fns in os.walk(root_dir):
            for fn in sorted(fns):
                if not fn.endswith(".txt"):
                    continue
                mp = Path(root) / fn
                rel = mp.relative_to(mod_dir).as_posix()
                vp = (van_dir / rel) if van_dir else None
                m_bytes = mp.read_bytes()
                if vp and vp.is_file() and m_bytes == vp.read_bytes():
                    continue

                lines = m_bytes.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
                v_ids: set[int] = set()
                if vp and vp.is_file():
                    v_text = vp.read_text(encoding="utf-8-sig", errors="replace")
                    for vm in RE_SR_SCALAR.finditer(v_text):
                        v_ids.add(int(vm.group(2)))
                    if rel.startswith("common/ai_strategy/"):
                        for _, _, vrid, _ in _extract_ai_strategy_sr_hits(v_text.splitlines(keepends=True)):
                            v_ids.add(vrid)

                raw_hits: list[tuple[int, str, int, str]] = []
                for ln_no, ln in enumerate(lines, 1):
                    code = ln.split("#", 1)[0]
                    for m in RE_SR_SCALAR.finditer(code):
                        raw_hits.append((ln_no, m.group(1), int(m.group(2)), ln.strip()))

                if rel.startswith("common/ai_strategy/"):
                    raw_hits.extend(_extract_ai_strategy_sr_hits(lines))
                    for d_ln, d_st, d_rid, d_txt in _find_ai_strategy_duplicates(lines):
                        cat_counts["DUPLICATE_AI_STRATEGY_SR"] += 1
                        findings_by_file[rel].append({
                            "line": d_ln,
                            "key": d_st,
                            "id": d_rid,
                            "status": "DUPLICATE_AI_STRATEGY_SR",
                            "me_name": idx.m2v[d_rid]["me_name"] if d_rid in idx.m2v else "MISSING",
                            "text": d_txt,
                        })

                if rel.startswith("common/ai_areas/"):
                    for ln_no, ln in enumerate(lines, 1):
                        code = ln.split("#", 1)[0]
                        if code.strip().isdigit():
                            raw_hits.append((ln_no, "ai_areas_member", int(code.strip()), ln.strip()))
                        elif "strategic_regions" in code and "{" in code:
                            inner = code.split("{", 1)[1].split("}", 1)[0]
                            for tok in inner.split():
                                if tok.isdigit():
                                    raw_hits.append((ln_no, "ai_areas_member", int(tok), ln.strip()))

                for ln_no, key, rid, ltxt in raw_hits:
                    comment_part = ltxt.split("#", 1)[1] if "#" in ltxt else ""
                    me_name_lower = idx.m2v[rid]["me_name"].lower() if rid in idx.m2v else ""
                    comment_verifies_me = bool(me_name_lower and me_name_lower in comment_part.lower())
                    has_eu_comment = (
                        "eu_mapping" in ltxt
                        or "was vanilla" in ltxt
                        or "# MER" in ltxt
                        or "# MEPORT" in ltxt
                        or "1.19-migration" in ltxt
                        or comment_verifies_me
                    )
                    if rid not in idx.m2v:
                        status = "INVALID_ME_ID"
                    else:
                        me_info = idx.m2v[rid]
                        if me_info["status"].startswith("excluded_"):
                            status = f"EXCLUDED_SR({me_info['status']})"
                        elif rid in v_ids and not has_eu_comment:
                            status = "SUSPECT_UNMIGRATED_VANILLA_COLLISION"
                        else:
                            status = "OK_ME"
                    cat_counts[status] += 1
                    findings_by_file[rel].append({
                        "line": ln_no,
                        "key": key,
                        "id": rid,
                        "status": status,
                        "me_name": idx.m2v[rid]["me_name"] if rid in idx.m2v else "MISSING",
                        "text": ltxt,
                    })

    print("=== Script Strategic Region Audit (ME-modified files) ===")
    for k, v in sorted(cat_counts.items()):
        print(f"  {k:40s}: {v}")

    print("\n=== Files with NON-OK_ME hits ===")
    for rel in sorted(findings_by_file):
        bad = [x for x in findings_by_file[rel] if x["status"] != "OK_ME"]
        if not bad:
            continue
        by_st = Counter(x["status"] for x in bad)
        print(f"  {rel:52s} total={len(findings_by_file[rel]):3d} bad={dict(by_st)}")
        for sample in bad[:4]:
            print(f"    L{sample['line']:5d} [{sample['status']}] id={sample['id']} ({sample['me_name']}): {sample['text'][:75]}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Vanilla <-> Magna Europa strategic region crosswalk CLI")
    ap.add_argument("--mapping-dir", default=str(DEFAULT_MAPPING_DIR))
    ap.add_argument("--regions", nargs="+", type=int, help="Translate Vanilla SR IDs -> ME SRs")
    ap.add_argument("--me-regions", nargs="+", type=int, help="Reverse-lookup ME SR IDs")
    ap.add_argument("--include-proxy", action="store_true", help="Include NA inset / off-map proxy matches")
    ap.add_argument("--audit-scripts", action="store_true", help="Audit all ME-modified script region refs")
    args = ap.parse_args()

    idx = EURegionMappingIndex(Path(args.mapping_dir))

    if args.regions:
        for vid in args.regions:
            res = idx.translate_region(vid, include_proxy=args.include_proxy)
            vname = res.get("vanilla_name", "?")
            print(f"v{vid} ({vname}) [{res['status']} cov={res.get('v_coverage', 0):.3f}]:")
            if res["me_regions"]:
                pairs = ", ".join(f"{m} ({n})" for m, n in zip(res["me_regions"], res["me_region_names"]))
                print(f"  ME -> {pairs}")
                print(f"  IDs: {' '.join(str(m) for m in res['me_regions'])}")
            else:
                print(f"  ME -> NONE ({res})")

    if args.me_regions:
        for mid in args.me_regions:
            r = idx.reverse_region(mid)
            if not r:
                print(f"ME {mid}: NOT FOUND")
            else:
                print(
                    f"ME {mid} ({r['me_name']}, {r['kind']}) [{r['status']}] -> "
                    f"v{r['vanilla_region_id'] or r['proxy_vanilla_id']} "
                    f"({r['vanilla_name'] or r['proxy_vanilla_name']}) "
                    f"share={r['primary_share']:.3f} | {r['all_vanilla_overlaps']}"
                )

    if args.audit_scripts:
        sys.exit(audit_scripts(idx))

    if not (args.regions or args.me_regions or args.audit_scripts):
        ap.print_help()


if __name__ == "__main__":
    main()
