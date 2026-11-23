#!/usr/bin/env python
"""Build the definitive European Vanilla <-> Magna Europa strategic region crosswalk (Layer 5).

Writes 4 deterministic UTF-8 (LF) artifacts under docs/states/eu_mapping/:
  - region_overlap.tsv          Weighted (vanilla_region_id, me_region_id) overlap matrix
  - region_partition_m2v.tsv    All 253 ME strategic regions -> primary Vanilla region (or excluded)
  - region_partition_v2m.tsv    All covered Vanilla regions -> ordered ME regions + proxy entries
  - region_mapping_summary.json Summary stats, low-share straddlers, and diff vs docs/states/regions.tsv

Dependencies: Pure Python stdlib only (no third-party packages required; parses 24-bit BMP directly).
Zero circularity:
  - On-map ME Land SRs (169) aggregate strictly from finalized Layer 1 `prov_m2v.tsv` (`me_px`).
  - On-map ME Sea SRs (73) project ME `provinces.bmp` sea pixels through Stage-1 landmass affines
    in `registration_report.json` onto Vanilla `provinces.bmp` water SRs (excluding land pixels,
    with expanding-radius nearest-water fallback up to 8 vanilla px for narrow straits).
  - The 11 off-map NA inset / Congo / Indonesia / synthetic border SRs are explicitly excluded
    and annotated with `proxy_vanilla_id` where applicable.

Usage:
  python tools/build_region_mapping.py
  python tools/build_region_mapping.py --vanilla "C:/path/to/Hearts of Iron IV"
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402

REPO = Path(repo_root())
DEFAULT_MOD = REPO / "mods" / "Magna-Europa"
DEFAULT_OUT = REPO / "docs" / "states" / "eu_mapping"

VANILLA_CANDIDATES = [
    os.environ.get("HOI4_DIR", ""),
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"C:\Program Files\Steam\steamapps\common\Hearts of Iron IV",
]

# Scope exclusions matching docs/states/eu_mapping/registration_report.json (11 ME SRs total)
EXCLUDED_ME_SRS: dict[int, tuple[str, int | None, str]] = {
    156: ("excluded_na_inset", 197, "Top-left NA inset: New England -> v197 New England"),
    157: ("excluded_na_inset", 117, "Top-left NA inset: East Coast -> v117 East Coast"),
    158: ("excluded_na_inset", 121, "Top-left NA inset: Canada -> v121 Eastern Canada"),
    159: ("excluded_na_inset", 220, "Top-left NA inset: Newfoundland -> v220 Labrador and Newfoundland"),
    189: ("excluded_na_inset", 54, "Top-left NA inset sea: American Coast -> v54 Eastern Seaboard"),
    190: ("excluded_na_inset", 55, "Top-left NA inset sea: Canadian Coast -> v55 Newfoundland Sea"),
    191: ("excluded_na_inset", 50, "Top-left NA inset sea: American Atlantic -> v50 Labrador Sea"),
    252: ("excluded_offmap_proxy", 227, "Off-map Congo proxy box -> v227 North East Congo"),
    253: ("excluded_offmap_proxy", 158, "Off-map Indonesia proxy box -> v158 Sunda Islands"),
    192: ("excluded_synthetic_border", None, "Synthetic MAP border region"),
    193: ("excluded_synthetic_border", None, "Synthetic American border region"),
}

BRITISH_ISLES_SEA_SRS = frozenset({209, 210, 211, 235, 241, 242})
ICELAND_SEA_SRS = frozenset({233, 234, 249})

# Empty unless geometric raster projection reveals an extreme map-edge clip requiring explicit override.
MANUAL_SEA_OVERRIDES: dict[int, tuple[int, str]] = {
    188: (
        104,
        "Bottom-edge canal strip (y=3064..3068) connecting Red Sea (77) and Gulf of Oman (187) across cropped southern Arabia; geometrically clips off Arabian coast, semantically v104 Arabian Sea",
    ),
}


def detect_vanilla(cli_path: str | None) -> Path:
    if cli_path:
        p = Path(cli_path)
        if not (p / "map" / "strategicregions").is_dir():
            raise SystemExit(f"Invalid --vanilla dir (missing map/strategicregions): {p}")
        return p
    for cand in VANILLA_CANDIDATES:
        if cand and (Path(cand) / "map" / "strategicregions").is_dir():
            return Path(cand)
    raise SystemExit("Could not locate Vanilla HOI4 install; pass --vanilla PATH")


def load_definition_csv(root: Path) -> dict[int, tuple[str, tuple[int, int, int]]]:
    """Return {prov_id: (type_str, (r, g, b))} from map/definition.csv."""
    out: dict[int, tuple[str, tuple[int, int, int]]] = {}
    with open(root / "map" / "definition.csv", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) >= 5 and parts[0].isdigit():
                pid = int(parts[0])
                out[pid] = (parts[4].lower(), (int(parts[1]), int(parts[2]), int(parts[3])))
    return out


def load_sr_loc(root: Path) -> dict[str, str]:
    """Load STRATEGICREGION_<ID> names from localisation/english (preferring replace/)."""
    loc: dict[str, str] = {}
    base_files = sorted(glob.glob(str(root / "localisation" / "english" / "*strategic_region*l_english.yml")))
    rep_files = sorted(glob.glob(str(root / "localisation" / "english" / "replace" / "*strategic_region*l_english.yml")))
    for p in base_files + rep_files:
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                m = re.match(r'\s*(STRATEGICREGION_\d+):\d*\s*"([^"]*)"', line)
                if m:
                    loc[m.group(1)] = m.group(2)
    return loc


def load_strategic_regions(root: Path, defs: dict[int, tuple[str, tuple[int, int, int]]], loc: dict[str, str]) -> tuple[dict[int, dict], dict[int, int]]:
    """Parse map/strategicregions/*.txt -> (sr_dict, prov_to_sr)."""
    srs: dict[int, dict] = {}
    prov_to_sr: dict[int, int] = {}
    for p in sorted(glob.glob(str(root / "map" / "strategicregions" / "*.txt"))):
        txt = Path(p).read_text(encoding="utf-8-sig", errors="replace")
        im = re.search(r"\bid\s*=\s*(\d+)", txt)
        if not im:
            continue
        srid = int(im.group(1))
        nm = re.search(r'\bname\s*=\s*"([^"]+)"', txt)
        naval = bool(re.search(r"\bnaval_terrain\s*=", txt))
        pm = re.search(r"provinces\s*=\s*\{([^}]*)\}", txt)
        provs = [int(x) for x in pm.group(1).split() if x.isdigit()] if pm else []
        for pr in provs:
            prov_to_sr[pr] = srid

        tc = Counter(defs.get(pr, ("unknown", (0, 0, 0)))[0] for pr in provs)
        water_cnt = tc["sea"] + tc["lake"]
        land_cnt = tc["land"]
        name_key = nm.group(1) if nm else f"STRATEGICREGION_{srid}"
        fallback_name = Path(p).stem.split("-", 1)[1] if "-" in Path(p).stem else Path(p).stem
        resolved_name = loc.get(name_key, fallback_name)
        is_sea = (
            naval
            or (tc["sea"] > land_cnt)
            or (water_cnt > 0 and land_cnt == 0)
            or (tc["sea"] > 0 and resolved_name.lower().endswith((" sea", " strait", " gulf", " bay", " channel")))
        )
        has_water = is_sea or (tc["sea"] > 0) or (tc["lake"] > 0 and land_cnt == 0)
        srs[srid] = {
            "id": srid,
            "name": resolved_name,
            "file": Path(p).name,
            "kind": "sea" if is_sea else "land",
            "naval_terrain": naval,
            "has_water": has_water,
            "provs": provs,
            "land_cnt": land_cnt,
            "sea_cnt": tc["sea"],
            "lake_cnt": tc["lake"],
        }
    return srs, prov_to_sr


def load_vanilla_state_regions(van_root: Path, v_prov_to_sr: dict[int, int], v_srs: dict[int, dict]) -> dict[int, int]:
    """Determine each Vanilla state's modal/land strategic region."""
    out: dict[int, int] = {}
    for p in sorted(glob.glob(str(van_root / "history" / "states" / "*.txt"))):
        m = re.match(r"^(\d+)", Path(p).stem)
        if not m:
            continue
        sid = int(m.group(1))
        txt = Path(p).read_text(encoding="utf-8-sig", errors="replace")
        pm = re.search(r"provinces\s*=\s*\{([^}]*)\}", txt)
        if not pm:
            continue
        counts = Counter()
        for tok in pm.group(1).split():
            if tok.isdigit():
                sr = v_prov_to_sr.get(int(tok))
                if sr is not None:
                    counts[sr] += 1
        if counts:
            # Deterministic modal region tie-broken by SR ID
            out[sid] = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return out


def classify_relation(me_share: float) -> str:
    if me_share >= 0.90:
        return "interior"
    if me_share >= 0.50:
        return "majority"
    if me_share >= 0.20:
        return "straddler"
    if me_share >= 0.05:
        return "minor"
    return "sliver"


def load_bmp24(path: Path) -> tuple[int, int, int, bytes, bool]:
    """Read an uncompressed 24-bit BMP header + raw pixel buffer (BGR, bottom-up when top_down=False)."""
    with open(path, "rb") as f:
        hdr = f.read(54)
        magic, _, _, _, offset = struct.unpack("<2sIHHI", hdr[:14])
        _, w, h_signed, _, bpp, comp, _ = struct.unpack("<IiiHHII", hdr[14:38])
        if magic != b"BM" or bpp != 24 or comp != 0:
            raise ValueError(f"Unsupported BMP format in {path}: magic={magic}, bpp={bpp}, comp={comp}")
        top_down = h_signed < 0
        h = abs(h_signed)
        f.seek(offset)
        buf = f.read()
    row_stride = ((w * 3 + 3) // 4) * 4
    return w, h, row_stride, buf, top_down


def build_mapping(van_root: Path, mod_root: Path, out_dir: Path, sea_stride: int = 2) -> None:
    reg_report = json.loads((out_dir / "registration_report.json").read_text(encoding="utf-8"))
    stage1 = reg_report["stage1_landmasses"]
    aff_main = stage1["mainland_eurasia_africa"]["affine_me_to_vanilla"]
    aff_brit = stage1["north_atlantic_british_isles"]["affine_me_to_vanilla"]
    aff_icel = stage1["iceland"]["affine_me_to_vanilla"]

    me_defs = load_definition_csv(mod_root)
    van_defs = load_definition_csv(van_root)
    me_loc = load_sr_loc(mod_root)
    van_loc = load_sr_loc(van_root)

    me_srs, me_prov_to_sr = load_strategic_regions(mod_root, me_defs, me_loc)
    van_srs, van_prov_to_sr = load_strategic_regions(van_root, van_defs, van_loc)
    v_state_to_sr = load_vanilla_state_regions(van_root, van_prov_to_sr, van_srs)

    # Load Layer 1 prov_m2v.tsv and prov_v2m.tsv + state_partition_m2v.tsv
    prov_rows: list[dict] = []
    with open(out_dir / "prov_m2v.tsv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            prov_rows.append(row)

    v_state_px: dict[int, tuple[int, int]] = {}
    with open(out_dir / "prov_v2m.tsv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            v_state_px[int(row["vanilla_id"])] = (int(row["v_native_px"]), int(row["v_covered_px"]))

    # Aggregate Vanilla state-covered/native pixels per Vanilla SR
    v_sr_state_native_px = Counter()
    v_sr_state_covered_px = Counter()
    for v_sid, v_srid in v_state_to_sr.items():
        if v_sid in v_state_px:
            nat, cov = v_state_px[v_sid]
            v_sr_state_native_px[v_srid] += nat
            v_sr_state_covered_px[v_srid] += cov

    # 1. Aggregate Land SRs strictly from prov_m2v.tsv (weighted by me_px, plus state fractional overlaps)
    land_overlap_px: dict[tuple[int, int], float] = defaultdict(float)
    land_me_total_px: Counter = Counter()
    v_sr_land_covered_px: Counter = Counter()

    for row in prov_rows:
        m_prov = int(row["me_prov"])
        m_px = int(row["me_px"])
        m_srid = me_prov_to_sr.get(m_prov)
        if m_srid is None or m_srid in EXCLUDED_ME_SRS:
            continue
        if me_srs[m_srid]["kind"] != "land":
            continue
        land_me_total_px[m_srid] += m_px

        # Parse all_v_states (e.g. "273:0.994|514:0.006") so border straddlers distribute cleanly
        parts = [p for p in (row["all_v_states"] or "").split("|") if ":" in p]
        used_weight = 0.0
        for part in parts:
            vs_s, w_s = part.split(":", 1)
            vs_id = int(vs_s)
            w = float(w_s)
            v_srid = v_state_to_sr.get(vs_id)
            if v_srid is not None and w > 0:
                land_overlap_px[(m_srid, v_srid)] += m_px * w
                v_sr_land_covered_px[v_srid] += m_px * w
                used_weight += w
        if used_weight == 0.0:
            v_srid = v_state_to_sr.get(int(row["v_state"]))
            if v_srid is not None:
                land_overlap_px[(m_srid, v_srid)] += float(m_px)
                v_sr_land_covered_px[v_srid] += float(m_px)

    # 2. Project on-map ME Sea SRs pixel-by-pixel via Stage-1 affines (pure Pillow, zero numpy)
    # Build (r<<16)|(g<<8)|b -> me_sea_sr_id for on-map ME Sea SRs' sea provinces ONLY
    onmap_sea_srs = {
        srid for srid, info in me_srs.items()
        if srid not in EXCLUDED_ME_SRS and info["kind"] == "sea"
    }
    rgb_to_me_sea_sr: dict[int, int] = {}
    for pid, (ptype, (r, g, b)) in me_defs.items():
        if ptype != "sea":
            continue
        srid = me_prov_to_sr.get(pid)
        if srid in onmap_sea_srs:
            rgb_to_me_sea_sr[(r << 16) | (g << 8) | b] = srid

    # Build (r<<16)|(g<<8)|b -> van_water_sr_id for Vanilla water provinces inside water/lake SRs
    rgb_to_van_water_sr: dict[int, int] = {}
    for pid, (ptype, (r, g, b)) in van_defs.items():
        if ptype not in ("sea", "lake"):
            continue
        vsrid = van_prov_to_sr.get(pid)
        if vsrid is not None and van_srs[vsrid]["has_water"]:
            rgb_to_van_water_sr[(r << 16) | (g << 8) | b] = vsrid

    vw, vh, v_stride, v_bytes, v_topdown = load_bmp24(van_root / "map" / "provinces.bmp")
    mw, mh, m_stride, m_bytes, m_topdown = load_bmp24(mod_root / "map" / "provinces.bmp")

    # Count sampled Vanilla water pixels per Vanilla water SR for v_coverage calculation
    # Note: 24-bit BMP stores B, G, R in memory and rows bottom-to-top unless top_down=True.
    v_water_sampled_px: Counter = Counter()
    for vy in range(0, vh, sea_stride):
        v_raw_y = vy if v_topdown else (vh - 1 - vy)
        row_off = v_raw_y * v_stride
        for vx in range(0, vw, sea_stride):
            idx = row_off + vx * 3
            rgb = (v_bytes[idx + 2] << 16) | (v_bytes[idx + 1] << 8) | v_bytes[idx]
            vsr = rgb_to_van_water_sr.get(rgb)
            if vsr:
                v_water_sampled_px[vsr] += 1

    # Expanding search offsets (sorted by Euclidean distance, up to radius 8 vanilla px)
    ring_offsets: list[tuple[int, int]] = []
    for r in range(1, 9):
        pts = []
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                d2 = dx * dx + dy * dy
                if (r - 1) ** 2 < d2 <= r * r:
                    pts.append((d2, dx, dy))
        pts.sort()
        ring_offsets.extend((dx, dy) for _, dx, dy in pts)

    sea_overlap_px: dict[tuple[int, int], int] = defaultdict(int)
    sea_me_total_px: Counter = Counter()
    sea_matched_px: Counter = Counter()
    sea_direct_px: Counter = Counter()
    sea_snapped_px: Counter = Counter()
    sea_oob_px: Counter = Counter()

    def lookup_v_water(vx: int, vy: int) -> int:
        if vx < 0 or vx >= vw or vy < 0 or vy >= vh:
            return 0
        v_raw_y = vy if v_topdown else (vh - 1 - vy)
        idx = v_raw_y * v_stride + vx * 3
        rgb = (v_bytes[idx + 2] << 16) | (v_bytes[idx + 1] << 8) | v_bytes[idx]
        return rgb_to_van_water_sr.get(rgb, 0)

    # Pre-unpack affine constants for speed
    a_m00, a_m01, a_m02 = aff_main[0]
    a_m10, a_m11, a_m12 = aff_main[1]
    a_b00, a_b01, a_b02 = aff_brit[0]
    a_b10, a_b11, a_b12 = aff_brit[1]
    a_i00, a_i01, a_i02 = aff_icel[0]
    a_i10, a_i11, a_i12 = aff_icel[1]

    px_area = sea_stride * sea_stride
    for my in range(0, mh, sea_stride):
        m_raw_y = my if m_topdown else (mh - 1 - my)
        row_off = m_raw_y * m_stride
        for mx in range(0, mw, sea_stride):
            idx = row_off + mx * 3
            rgb = (m_bytes[idx + 2] << 16) | (m_bytes[idx + 1] << 8) | m_bytes[idx]
            msr = rgb_to_me_sea_sr.get(rgb)
            if not msr:
                continue
            sea_me_total_px[msr] += px_area

            if msr in BRITISH_ISLES_SEA_SRS:
                vx = int(round(a_b00 * mx + a_b01 * my + a_b02))
                vy = int(round(a_b10 * mx + a_b11 * my + a_b12))
            elif msr in ICELAND_SEA_SRS:
                vx = int(round(a_i00 * mx + a_i01 * my + a_i02))
                vy = int(round(a_i10 * mx + a_i11 * my + a_i12))
            else:
                vx = int(round(a_m00 * mx + a_m01 * my + a_m02))
                vy = int(round(a_m10 * mx + a_m11 * my + a_m12))

            if vx < 0 or vx >= vw or vy < 0 or vy >= vh:
                sea_oob_px[msr] += px_area

            vsr = lookup_v_water(vx, vy)
            if vsr:
                sea_direct_px[msr] += px_area
            else:
                # Clamped nearest-water fallback up to radius 8 vanilla px
                cvx = min(max(vx, 0), vw - 1)
                cvy = min(max(vy, 0), vh - 1)
                for dx, dy in ring_offsets:
                    cand = lookup_v_water(cvx + dx, cvy + dy)
                    if cand:
                        vsr = cand
                        sea_snapped_px[msr] += px_area
                        break
            if vsr:
                sea_matched_px[msr] += px_area
                sea_overlap_px[(msr, vsr)] += px_area

    # Measure Vanilla pixel totals per Vanilla SR (land in ME-px scale ~9.89x; water in sampled Vanilla px)
    scale_area = 9.89  # 3.145^2 approx ratio ME_px / V_px for approximate v_share on water

    # Build region_overlap rows
    overlap_rows: list[dict] = []
    me_overlaps_by_id: dict[int, list[tuple[int, float, float]]] = defaultdict(list)

    # Land overlaps
    v_land_denom: Counter = Counter()
    for (msr, vsr), ov in land_overlap_px.items():
        v_land_denom[vsr] += ov
    for (msr, vsr), ov in sorted(land_overlap_px.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        mtot = float(land_me_total_px[msr])
        mshare = ov / mtot if mtot else 0.0
        v_nat_me_equiv = v_sr_state_native_px[vsr] * 9.89 if v_sr_state_native_px[vsr] else v_land_denom[vsr]
        vshare = min(1.0, ov / max(v_nat_me_equiv, ov)) if v_nat_me_equiv else 0.0
        rel_cls = classify_relation(mshare)
        me_overlaps_by_id[msr].append((vsr, ov, mshare))
        overlap_rows.append({
            "vanilla_region_id": vsr,
            "vanilla_name": van_srs[vsr]["name"],
            "me_region_id": msr,
            "me_name": me_srs[msr]["name"],
            "kind": "land",
            "overlap_px": round(ov, 1),
            "me_total_px": int(round(mtot)),
            "me_share": f"{mshare:.6f}",
            "v_total_px": int(round(v_nat_me_equiv)),
            "v_share": f"{vshare:.6f}",
            "relation_class": rel_cls,
        })

    # Sea overlaps
    v_sea_matched_sum: Counter = Counter()
    for (msr, vsr), ov in sea_overlap_px.items():
        v_sea_matched_sum[vsr] += ov
    for (msr, vsr), ov in sorted(sea_overlap_px.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        mtot = float(sea_me_total_px[msr])
        mshare = ov / mtot if mtot else 0.0
        v_equiv = float(v_water_sampled_px[vsr] * px_area) * scale_area if v_water_sampled_px[vsr] else float(v_sea_matched_sum[vsr])
        vshare = min(1.0, ov / max(v_equiv, float(ov))) if v_equiv else 0.0
        rel_cls = classify_relation(mshare)
        me_overlaps_by_id[msr].append((vsr, float(ov), mshare))
        overlap_rows.append({
            "vanilla_region_id": vsr,
            "vanilla_name": van_srs[vsr]["name"],
            "me_region_id": msr,
            "me_name": me_srs[msr]["name"],
            "kind": "sea",
            "overlap_px": ov,
            "me_total_px": int(sea_me_total_px[msr]),
            "me_share": f"{mshare:.6f}",
            "v_total_px": int(round(v_equiv)),
            "v_share": f"{vshare:.6f}",
            "relation_class": rel_cls,
        })

    # Sort overlap_rows cleanly by (vanilla_region_id, -float(me_share), me_region_id)
    overlap_rows.sort(key=lambda r: (int(r["vanilla_region_id"]), -float(r["overlap_px"]), int(r["me_region_id"])))

    # Build region_partition_m2v rows (ALL 253 ME SRs)
    m2v_rows: list[dict] = []
    v2m_members: dict[int, list[tuple[int, float, str, str]]] = defaultdict(list)

    for msr in sorted(me_srs):
        info = me_srs[msr]
        kind = info["kind"]
        if msr in EXCLUDED_ME_SRS:
            status, proxy_vid, note = EXCLUDED_ME_SRS[msr]
            m_px = int(sea_me_total_px[msr]) if kind == "sea" else 0
            proxy_vname = van_srs[proxy_vid]["name"] if proxy_vid else ""
            if proxy_vid is not None:
                v_status = "inset_proxy" if status == "excluded_na_inset" else "offmap_proxy"
                v2m_members[proxy_vid].append((msr, 1.0, v_status, kind))
            m2v_rows.append({
                "me_region_id": msr,
                "me_name": info["name"],
                "kind": kind,
                "status": status,
                "vanilla_region_id": "",
                "vanilla_name": "",
                "primary_share": "0.000000",
                "me_px": m_px,
                "proxy_vanilla_id": proxy_vid if proxy_vid is not None else "",
                "proxy_vanilla_name": proxy_vname,
                "all_vanilla_overlaps": "",
                "notes": note,
            })
            continue

        ovs = sorted(me_overlaps_by_id.get(msr, []), key=lambda x: (-x[1], x[0]))
        mtot = sea_me_total_px[msr] if kind == "sea" else land_me_total_px[msr]
        ov_str = "|".join(f"{vsr}:{msh:.3f}" for vsr, _, msh in ovs if msh >= 0.005)

        if msr in MANUAL_SEA_OVERRIDES:
            p_vid, note = MANUAL_SEA_OVERRIDES[msr]
            p_share = next((msh for vsr, _, msh in ovs if vsr == p_vid), 0.0)
            status = "manual_override"
        elif ovs:
            p_vid, _, p_share = ovs[0]
            status = "mapped"
            if kind == "sea":
                oob = sea_oob_px[msr]
                snp = sea_snapped_px[msr]
                note = f"direct_px={sea_direct_px[msr]}, snapped_px={snp}, oob_px={oob}"
            else:
                note = f"prov_m2v aggregate ({len(ovs)} candidate v_regions)"
        else:
            p_vid, p_share = "", 0.0
            status = "unmapped"
            note = f"no overlap (oob_px={sea_oob_px[msr]})" if kind == "sea" else "no overlap"

        if p_vid != "":
            v2m_members[int(p_vid)].append((msr, ovs[0][1] if ovs else 1.0, "mapped", kind))

        m2v_rows.append({
            "me_region_id": msr,
            "me_name": info["name"],
            "kind": kind,
            "status": status,
            "vanilla_region_id": p_vid,
            "vanilla_name": van_srs[int(p_vid)]["name"] if p_vid != "" else "",
            "primary_share": f"{p_share:.6f}",
            "me_px": int(mtot),
            "proxy_vanilla_id": "",
            "proxy_vanilla_name": "",
            "all_vanilla_overlaps": ov_str,
            "notes": note,
        })

    # Build region_partition_v2m rows
    v2m_rows: list[dict] = []
    for vsr in sorted(v2m_members):
        members = v2m_members[vsr]
        mapped_m = [x for x in members if x[2] == "mapped"]
        proxy_m = [x for x in members if x[2] != "mapped"]
        chosen = mapped_m if mapped_m else proxy_m
        chosen.sort(key=lambda x: (-x[1], x[0]))
        status = "mapped" if mapped_m else proxy_m[0][2]
        v_kind = "sea" if van_srs[vsr]["kind"] == "sea" else "land"

        if status != "mapped":
            cov = 1.0
        elif v_kind == "land" and v_sr_state_native_px[vsr] > 0:
            cov = v_sr_state_covered_px[vsr] / v_sr_state_native_px[vsr]
        elif v_water_sampled_px[vsr] > 0:
            tot_m_sea = sum(ov for (msr, v2), ov in sea_overlap_px.items() if v2 == vsr)
            v_sea_me_equiv = v_water_sampled_px[vsr] * px_area * scale_area
            cov = min(1.0, tot_m_sea / max(v_sea_me_equiv, 1.0))
        else:
            cov = 0.0

        prim_m = chosen[0][0]
        v2m_rows.append({
            "vanilla_region_id": vsr,
            "vanilla_name": van_srs[vsr]["name"],
            "kind": v_kind,
            "status": status,
            "me_count": len(chosen),
            "primary_me_region": prim_m,
            "primary_me_name": me_srs[prim_m]["name"],
            "me_regions": "|".join(str(x[0]) for x in chosen),
            "me_region_names": "|".join(me_srs[x[0]]["name"] for x in chosen),
            "v_coverage": f"{cov:.6f}",
        })

    # Write TSVs (UTF-8, LF)
    def write_tsv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    write_tsv(
        out_dir / "region_overlap.tsv",
        ["vanilla_region_id", "vanilla_name", "me_region_id", "me_name", "kind", "overlap_px", "me_total_px", "me_share", "v_total_px", "v_share", "relation_class"],
        overlap_rows,
    )
    write_tsv(
        out_dir / "region_partition_m2v.tsv",
        ["me_region_id", "me_name", "kind", "status", "vanilla_region_id", "vanilla_name", "primary_share", "me_px", "proxy_vanilla_id", "proxy_vanilla_name", "all_vanilla_overlaps", "notes"],
        m2v_rows,
    )
    write_tsv(
        out_dir / "region_partition_v2m.tsv",
        ["vanilla_region_id", "vanilla_name", "kind", "status", "me_count", "primary_me_region", "primary_me_name", "me_regions", "me_region_names", "v_coverage"],
        v2m_rows,
    )

    # Compare vs legacy docs/states/regions.tsv
    legacy_diffs = []
    legacy_path = out_dir.parent / "regions.tsv"
    if legacy_path.is_file():
        with open(legacy_path, encoding="utf-8", newline="") as f:
            leg_by_id = {int(r["me_region"]): r for r in csv.DictReader(f, delimiter="\t")}
        for r in m2v_rows:
            mid = int(r["me_region_id"])
            lr = leg_by_id.get(mid)
            if not lr:
                continue
            old_vid = int(lr["v_region"]) if lr["v_region"].isdigit() else None
            new_vid = int(r["vanilla_region_id"]) if str(r["vanilla_region_id"]).isdigit() else None
            if r["status"] == "mapped" and old_vid != new_vid:
                legacy_diffs.append({
                    "me_region_id": mid,
                    "me_name": r["me_name"],
                    "kind": r["kind"],
                    "legacy_v_region": old_vid,
                    "legacy_v_name": lr["v_name"],
                    "legacy_status": lr["status"],
                    "new_v_region": new_vid,
                    "new_v_name": r["vanilla_name"],
                    "new_share": float(r["primary_share"]),
                })

    low_share = [
        {
            "me_region_id": r["me_region_id"],
            "me_name": r["me_name"],
            "kind": r["kind"],
            "vanilla_region_id": r["vanilla_region_id"],
            "vanilla_name": r["vanilla_name"],
            "primary_share": float(r["primary_share"]),
            "all_vanilla_overlaps": r["all_vanilla_overlaps"],
        }
        for r in m2v_rows
        if r["status"] == "mapped" and float(r["primary_share"]) < 0.65
    ]

    summary = {
        "schema": "eu_region_mapping_summary/v1",
        "me_regions_total": len(m2v_rows),
        "status_counts": dict(Counter(r["status"] for r in m2v_rows)),
        "kind_status_counts": {f"{k}:{s}": c for (k, s), c in sorted(Counter((r["kind"], r["status"]) for r in m2v_rows).items())},
        "covered_vanilla_regions": dict(Counter(r["status"] for r in v2m_rows)),
        "sea_stride": sea_stride,
        "low_share_straddlers_lt_0_65": low_share,
        "legacy_diffs_count": len(legacy_diffs),
        "legacy_diffs": legacy_diffs,
    }
    (out_dir / "region_mapping_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Written 4 files under {out_dir}")
    print("ME region status:", summary["kind_status_counts"])
    print("Covered Vanilla regions:", summary["covered_vanilla_regions"])
    print(f"Legacy regions.tsv diffs: {len(legacy_diffs)} | Low-share (<0.65) straddlers: {len(low_share)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", help="Path to Vanilla HOI4 directory")
    ap.add_argument("--mod", default=str(DEFAULT_MOD), help="Path to Magna-Europa mod root")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory")
    ap.add_argument("--sea-stride", type=int, default=2, help="Pixel sampling stride for sea regions")
    args = ap.parse_args()
    van_root = detect_vanilla(args.vanilla)
    build_mapping(van_root, Path(args.mod), Path(args.out), sea_stride=args.sea_stride)


if __name__ == "__main__":
    main()
