#!/usr/bin/env python3
"""Offline Map Graph Compiler for Expert AI for Magna Europa: Reforged.

Compiles Magna Europa's live map assets (provinces.bmp, rivers.bmp,
definition.csv, adjacencies.csv, adjacency_rules.txt, railways.txt,
supply_nodes.txt, history/states/*.txt, map/strategicregions/*.txt) into:

  1. _ctx/eai_map_graph.json
     L0 full offline multi-layer graph for Python tools and spectate audits.
  2. mods/Expert-AI-for-Magna-Europa/common/scripted_effects/ME_EAI_map_data.txt
     Zero-meta_effect Clausewitz runtime data:
       - L1-SR CSR graph (all 253 strategic regions, land + sea + coastal)
       - L1-State CSR graph (all 2,986 states, land + major river + rail + strait)
       - Per-state O(1) metadata arrays (SR id, hub/port/VP class)
       - L2 tactical indexes (12 operational sectors + anchors, 7 major river
         defensive lines with oriented bank provinces, per-SR top hub/airbase,
         5 European amphibious corridors)
  3. docs/eai/map_graph_preview.png
     Visual QA overlay of sectors, major river defense lines, and hubs.

Usage:
  python tools/build_eai_map_graph.py [--no-preview]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from _modroot import mod_root, repo_root
from prov_adjacency import sea_rules

# Major river palette indices in ME rivers.bmp (10/11 = wide -60% rivers; 8/9 included if present)
MAJOR_RIVER_INDICES = (8, 9, 10, 11)
ANY_RIVER_INDICES = tuple(range(0, 12))

# Edge flag encoding (Clausewitz-friendly, zero bitwise ops required):
#   1 = land
#   2 = land + railway
#   3 = land + major river
#   4 = land + major river + railway
#   5 = strait (walkable sea/canal crossing)
#   6 = strait + railway
#   7 = coastal (land SR <-> sea SR, SR graph only)
#   8 = maritime (sea SR <-> sea SR, SR graph only)
EFLAG_LAND = 1
EFLAG_LAND_RAIL = 2
EFLAG_RIVER = 3
EFLAG_RIVER_RAIL = 4
EFLAG_STRAIT = 5
EFLAG_STRAIT_RAIL = 6
EFLAG_COASTAL = 7
EFLAG_MARITIME = 8

# 12 European Operational Sectors (verified against ME's 253 strategic regions)
SECTORS: list[dict] = [
    {
        "id": 1,
        "key": "east_north",
        "label": "East Front North (Baltics-Leningrad-Murmansk)",
        "color": (90, 155, 220),
        "srs": [9, 27, 56, 28, 110, 111, 130, 112, 113, 114, 115, 146, 147, 136],
    },
    {
        "id": 2,
        "key": "east_center",
        "label": "East Front Center (Poland-Smolensk-Moscow)",
        "color": (220, 110, 90),
        "srs": [44, 96, 43, 42, 29, 97, 98, 108, 109, 131, 132, 140, 142, 135, 148, 149],
    },
    {
        "id": 3,
        "key": "east_south",
        "label": "East Front South (Ukraine-Stalingrad-Caucasus)",
        "color": (235, 175, 75),
        "srs": [
            41, 99, 100, 14, 107, 78, 79, 106, 101, 133,
            104, 105, 134, 141, 137, 138, 152, 139, 63, 129, 47,
        ],
    },
    {
        "id": 4,
        "key": "west_channel",
        "label": "West Front Channel (Benelux-Normandy-N.France)",
        "color": (85, 185, 130),
        "srs": [4, 5, 62, 67, 89, 93],
    },
    {
        "id": 5,
        "key": "west_france_south",
        "label": "West Front Interior & South France",
        "color": (120, 205, 170),
        "srs": [88, 90, 92, 87, 91, 61],
    },
    {
        "id": 6,
        "key": "west_germany",
        "label": "Rhine & German Heartland",
        "color": (140, 140, 165),
        "srs": [36, 70, 64, 35, 68, 69, 40, 11, 7, 8, 10, 94, 95, 21],
    },
    {
        "id": 7,
        "key": "italy_south",
        "label": "Italy South (Sicily-Mezzogiorno)",
        "color": (180, 110, 210),
        "srs": [18, 17, 19],
    },
    {
        "id": 8,
        "key": "italy_north",
        "label": "Italy Central & Po Valley (Gustav-Gothic)",
        "color": (150, 85, 185),
        "srs": [25, 16],
    },
    {
        "id": 9,
        "key": "north_africa",
        "label": "North Africa & Suez Corridor",
        "color": (215, 195, 115),
        "srs": [30, 37, 65, 38, 39, 59, 58, 57, 50, 32, 49],
    },
    {
        "id": 10,
        "key": "balkans",
        "label": "Balkans & Greece",
        "color": (195, 135, 155),
        "srs": [23, 24, 22, 20, 33, 34, 13, 15, 12, 52],
    },
    {
        "id": 11,
        "key": "scandinavia",
        "label": "Scandinavia & Norway",
        "color": (125, 185, 215),
        "srs": [26, 122, 121, 120, 123, 124, 117, 118, 119, 116],
    },
    {
        "id": 12,
        "key": "british_isles",
        "label": "British Isles",
        "color": (210, 95, 115),
        "srs": [45, 46, 3, 2, 1],
    },
    {
        "id": 13,
        "key": "iberia",
        "label": "Iberia (Spain & Portugal)",
        "color": (195, 155, 95),
        "srs": [51, 80, 81, 82, 83, 84, 85, 86],
    },
]

# Major River Defensive Lines (oriented automatically from rivers.bmp + centroid geometry)
# defend_side:
#   "east"  -> defender holds east bank (cx_def > cx_atk), facing attack from west
#   "west"  -> defender holds west bank (cx_def < cx_atk), facing attack from east
#   "north" -> defender holds north bank (cy_def < cy_atk), facing attack from south
#   "both"  -> either bank qualifies (e.g. Danube multi-directional)
RIVER_LINES: list[dict] = [
    {
        "id": 1,
        "key": "dnieper_east",
        "label": "Dnieper-Dvina Line (East Bank vs West)",
        "defend_side": "east",
        "srs": [56, 29, 97, 98, 107, 106, 78, 109],
        "max_provs": 80,
    },
    {
        "id": 2,
        "key": "vistula_west",
        "label": "Vistula-Narew Line (West Bank vs East)",
        "defend_side": "west",
        "srs": [95, 44, 94, 96, 10],
        "max_provs": 60,
    },
    {
        "id": 3,
        "key": "oder_elbe_west",
        "label": "Oder-Elbe Line (West Bank vs East)",
        "defend_side": "west",
        "srs": [40, 69, 11, 68, 64],
        "max_provs": 70,
    },
    {
        "id": 4,
        "key": "rhine_east",
        "label": "Rhine-Westwall Line (East Bank vs West)",
        "defend_side": "east",
        "srs": [36, 70, 35, 4],
        "x_max": 1710,
        "max_provs": 60,
    },
    {
        "id": 5,
        "key": "po_north",
        "label": "Po-Adige Line (North Bank vs South)",
        "defend_side": "north",
        "srs": [16, 25],
        "max_provs": 45,
    },
    {
        "id": 6,
        "key": "danube",
        "label": "Lower-Middle Danube Line",
        "defend_side": "both",
        "srs": [15, 13, 22, 23, 52],
        "max_provs": 60,
    },
    {
        "id": 7,
        "key": "don_volga_east",
        "label": "Don-Volga Line (East/North Bank vs West)",
        "defend_side": "east",
        "srs": [137, 138, 134, 152, 139],
        "max_provs": 65,
    },
]

# European Amphibious Corridors (staging SRs, sea SRs, target landing SRs)
INVASION_CORRIDORS: list[dict] = [
    {
        "id": 1,
        "key": "norway",
        "label": "Norway Corridor (North Sea / Skagerrak)",
        "sea_srs": [168, 237, 238, 239, 236, 231, 230],
        "target_srs": [122, 121, 120],
        "max_targets": 12,
    },
    {
        "id": 2,
        "key": "italy",
        "label": "Sicily & Southern Italy Corridor",
        "sea_srs": [200, 195, 202, 207, 197],
        "target_srs": [18, 17, 25],
        "max_targets": 14,
    },
    {
        "id": 3,
        "key": "normandy",
        "label": "Channel & Normandy Overlord Corridor",
        "sea_srs": [209, 211, 240],
        "target_srs": [62, 67, 89, 5, 4],
        "max_targets": 16,
    },
    {
        "id": 4,
        "key": "north_africa",
        "label": "North Africa Med Corridor (Torch / Egypt)",
        "sea_srs": [201, 204, 205, 200, 207, 206, 208],
        "target_srs": [30, 37, 65, 38, 39, 59, 58],
        "max_targets": 16,
    },
    {
        "id": 5,
        "key": "greece",
        "label": "Greece & Aegean Corridor",
        "sea_srs": [202, 75, 208],
        "target_srs": [34, 33],
        "max_targets": 10,
    },
]


def load_definitions(mapdir: Path) -> tuple[dict[tuple[int, int, int], int], dict[int, str], dict[int, str]]:
    rgb2id: dict[tuple[int, int, int], int] = {}
    ptype: dict[int, str] = {}
    terrain: dict[int, str] = {}
    with open(mapdir / "definition.csv", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if row and row[0].isdigit():
                pid = int(row[0])
                rgb2id[(int(row[1]), int(row[2]), int(row[3]))] = pid
                ptype[pid] = row[4].strip() if len(row) > 4 else "land"
                terrain[pid] = row[6].strip() if len(row) > 6 else "unknown"
    return rgb2id, ptype, terrain


def parse_sr_loc(mroot: Path) -> dict[str, str]:
    loc: dict[str, str] = {}
    for lp in glob.glob(str(mroot / "localisation" / "**" / "strategic_region_names*_l_english.yml"), recursive=True):
        with open(lp, encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                m = re.match(r'\s*(STRATEGICREGION_\d+):\d*\s*"([^"]+)"', line)
                if m:
                    loc[m.group(1)] = m.group(2)
    return loc


def parse_strategic_regions(mroot: Path, ptype: dict[int, str]) -> tuple[dict[int, dict], dict[int, int]]:
    loc = parse_sr_loc(mroot)
    srs: dict[int, dict] = {}
    prov2sr: dict[int, int] = {}
    for p in glob.glob(str(mroot / "map" / "strategicregions" / "*.txt")):
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        m_id = re.search(r"\bid\s*=\s*(\d+)", text)
        if not m_id:
            continue
        srid = int(m_id.group(1))
        m_nm = re.search(r'\bname\s*=\s*"([^"]+)"', text)
        nm_key = m_nm.group(1) if m_nm else f"STRATEGICREGION_{srid}"
        has_naval = bool(re.search(r"\bnaval_terrain\s*=", text))
        m_pr = re.search(r"\bprovinces\s*=\s*\{([^}]+)\}", text, re.S)
        provs = [int(x) for x in m_pr.group(1).split()] if m_pr else []
        n_sea = sum(1 for pr in provs if ptype.get(pr) == "sea")
        n_land = sum(1 for pr in provs if ptype.get(pr) == "land")
        is_sea = has_naval or (n_sea > n_land)
        srs[srid] = {
            "id": srid,
            "name": loc.get(nm_key, nm_key),
            "is_sea": is_sea,
            "provinces": provs,
            "n_land": n_land,
            "n_sea": n_sea,
        }
        for pr in provs:
            prov2sr[pr] = srid
    return srs, prov2sr


def parse_states(mroot: Path, prov2sr: dict[int, int]) -> tuple[dict[int, dict], dict[int, int]]:
    states: dict[int, dict] = {}
    prov2state: dict[int, int] = {}
    for p in glob.glob(str(mroot / "history" / "states" / "*.txt")):
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        # Strip comments
        clean = re.sub(r"#[^\n]*", "", text)
        m_id = re.search(r"\bid\s*=\s*(\d+)", clean)
        if not m_id:
            continue
        sid = int(m_id.group(1))
        m_owner = re.search(r"\bowner\s*=\s*([A-Z0-9]{3})\b", clean)
        owner = m_owner.group(1) if m_owner else ""
        impassable = bool(re.search(r"\bimpassable\s*=\s*yes\b", clean))
        m_mp = re.search(r"\bmanpower\s*=\s*(\d+)", clean)
        manpower = int(m_mp.group(1)) if m_mp else 0
        m_air = re.search(r"\bair_base\s*=\s*(\d+)", clean)
        air_base = int(m_air.group(1)) if m_air else 0
        m_pr = re.search(r"\bprovinces\s*=\s*\{([^}]+)\}", clean, re.S)
        provs = [int(x) for x in m_pr.group(1).split()] if m_pr else []

        vps: dict[int, int] = {}
        for m_vp in re.finditer(r"\bvictory_points\s*=\s*\{\s*(\d+)\s+(\d+)\s*\}", clean):
            vps[int(m_vp.group(1))] = int(m_vp.group(2))

        naval_bases: dict[int, int] = {}
        for m_nb in re.finditer(r"(\d+)\s*=\s*\{[^{}]*\bnaval_base\s*=\s*(\d+)[^{}]*\}", clean, re.S):
            naval_bases[int(m_nb.group(1))] = int(m_nb.group(2))

        sr_counts = Counter(prov2sr[pr] for pr in provs if pr in prov2sr)
        sr_id = sr_counts.most_common(1)[0][0] if sr_counts else 0

        playable = (not impassable) and (owner != "MAP") and (len(provs) > 0)
        states[sid] = {
            "id": sid,
            "file": Path(p).stem,
            "owner": owner,
            "impassable": impassable,
            "playable": playable,
            "manpower": manpower,
            "air_base": air_base,
            "sr": sr_id,
            "provinces": provs,
            "vps": vps,
            "total_vp": sum(vps.values()),
            "naval_bases": naval_bases,
            "supply_nodes": [],
            "is_coastal": len(naval_bases) > 0,
        }
        for pr in provs:
            prov2state[pr] = sid
    return states, prov2state


def parse_supply_and_rail(mroot: Path) -> tuple[dict[int, int], list[tuple[int, int, int]]]:
    nodes: dict[int, int] = {}
    with open(mroot / "map" / "supply_nodes.txt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                nodes[int(parts[1])] = int(parts[0])

    rail_edges: dict[tuple[int, int], int] = {}
    with open(mroot / "map" / "railways.txt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 4 and parts[0].isdigit() and parts[1].isdigit():
                lvl = int(parts[0])
                chain = [int(x) for x in parts[2:] if x.isdigit()]
                for a, b in zip(chain, chain[1:]):
                    if a != b:
                        k = (min(a, b), max(a, b))
                        if lvl > rail_edges.get(k, 0):
                            rail_edges[k] = lvl
    return nodes, [(a, b, lvl) for (a, b), lvl in rail_edges.items()]


def analyze_rasters(
    mapdir: Path,
    rgb2id: dict[tuple[int, int, int], int],
    ptype: dict[int, str],
) -> tuple[
    np.ndarray,
    dict[int, tuple[float, float, int]],
    set[tuple[int, int]],
    set[tuple[int, int]],
    set[tuple[int, int]],
    dict[tuple[int, int], int],
    set[tuple[int, int]],
]:
    """Extract province raster, centroids, 4-neighbor adjacencies, and major river crossings."""
    pim = np.array(Image.open(mapdir / "provinces.bmp").convert("RGB"))
    rim = np.array(Image.open(mapdir / "rivers.bmp"))
    h, w = pim.shape[:2]

    key = (
        pim[:, :, 0].astype(np.int64) * 65536
        + pim[:, :, 1].astype(np.int64) * 256
        + pim[:, :, 2].astype(np.int64)
    )
    uniq, inv = np.unique(key, return_inverse=True)
    k2id = {r * 65536 + g * 256 + b: i for (r, g, b), i in rgb2id.items()}
    uid = np.array([k2id.get(int(u), 0) for u in uniq], dtype=np.int32)
    pmap = uid[inv.reshape(h, w)]

    # Vectorized province centroids & pixel counts
    max_pid = int(pmap.max())
    flat = pmap.ravel()
    counts = np.bincount(flat, minlength=max_pid + 1)
    xs = np.broadcast_to(np.arange(w, dtype=np.float64)[None, :], (h, w)).ravel()
    ys = np.broadcast_to(np.arange(h, dtype=np.float64)[:, None], (h, w)).ravel()
    sum_x = np.bincount(flat, weights=xs, minlength=max_pid + 1)
    sum_y = np.bincount(flat, weights=ys, minlength=max_pid + 1)
    centroids: dict[int, tuple[float, float, int]] = {}
    for pid in np.nonzero(counts)[0].tolist():
        if pid > 0:
            c = int(counts[pid])
            centroids[pid] = (float(sum_x[pid] / c), float(sum_y[pid] / c), c)

    # Major river mask (indices 8, 9, 10, 11)
    major_riv = np.isin(rim, MAJOR_RIVER_INDICES)

    # Fast 4-neighbor edge & river crossing scan (horizontal + vertical diffs)
    land_pairs: set[tuple[int, int]] = set()
    coastal_pairs: set[tuple[int, int]] = set()  # (land_pid, sea_pid)
    sea_pairs: set[tuple[int, int]] = set()
    river_hits: Counter[tuple[int, int]] = Counter()

    land_flag = np.zeros(max_pid + 1, dtype=np.int8)  # 1=land, 2=sea
    for pid, pt in ptype.items():
        if pid <= max_pid:
            if pt == "land":
                land_flag[pid] = 1
            elif pt == "sea":
                land_flag[pid] = 2

    for axis in (0, 1):
        if axis == 0:
            a, b = pmap[:-1, :], pmap[1:, :]
            ra, rb = major_riv[:-1, :], major_riv[1:, :]
        else:
            a, b = pmap[:, :-1], pmap[:, 1:]
            ra, rb = major_riv[:, :-1], major_riv[:, 1:]
        nz = (a != b) & (a > 0) & (b > 0)
        aa = a[nz]
        bb = b[nz]
        r_cross = ra[nz] | rb[nz]

        fa = land_flag[aa]
        fb = land_flag[bb]

        # Land-Land
        m_ll = (fa == 1) & (fb == 1)
        if np.any(m_ll):
            u = np.minimum(aa[m_ll], bb[m_ll])
            v = np.maximum(aa[m_ll], bb[m_ll])
            # Deduplicate pairs quickly with 64-bit packing
            packed = u.astype(np.int64) * 100000 + v.astype(np.int64)
            for p_val in np.unique(packed).tolist():
                land_pairs.add((p_val // 100000, p_val % 100000))
            # Count major river border pixels per land-land pair
            m_riv = r_cross[m_ll]
            if np.any(m_riv):
                ru = u[m_riv]
                rv = v[m_riv]
                rpacked = ru.astype(np.int64) * 100000 + rv.astype(np.int64)
                r_uniq, r_cnt = np.unique(rpacked, return_counts=True)
                for p_val, c_val in zip(r_uniq.tolist(), r_cnt.tolist()):
                    river_hits[(p_val // 100000, p_val % 100000)] += c_val

        # Land-Sea
        m_ls = (fa == 1) & (fb == 2)
        if np.any(m_ls):
            packed = aa[m_ls].astype(np.int64) * 100000 + bb[m_ls].astype(np.int64)
            for p_val in np.unique(packed).tolist():
                coastal_pairs.add((p_val // 100000, p_val % 100000))
        m_sl = (fa == 2) & (fb == 1)
        if np.any(m_sl):
            packed = bb[m_sl].astype(np.int64) * 100000 + aa[m_sl].astype(np.int64)
            for p_val in np.unique(packed).tolist():
                coastal_pairs.add((p_val // 100000, p_val % 100000))

        # Sea-Sea
        m_ss = (fa == 2) & (fb == 2)
        if np.any(m_ss):
            u = np.minimum(aa[m_ss], bb[m_ss])
            v = np.maximum(aa[m_ss], bb[m_ss])
            packed = u.astype(np.int64) * 100000 + v.astype(np.int64)
            for p_val in np.unique(packed).tolist():
                sea_pairs.add((p_val // 100000, p_val % 100000))

    # Parse special adjacencies (straits / canals / impassable)
    cross_rules = sea_rules(str(mapdir))
    strait_pairs: set[tuple[int, int]] = set()
    impassable_pairs: set[tuple[int, int]] = set()
    with open(mapdir / "adjacencies.csv", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if len(row) < 4 or not row[0].isdigit() or not row[1].isdigit():
                continue
            a_p, b_p = int(row[0]), int(row[1])
            typ = row[2].strip().lower()
            through = row[3].strip()
            rule = row[8].strip() if len(row) > 8 else ""
            pair = (min(a_p, b_p), max(a_p, b_p))
            if typ == "impassable":
                impassable_pairs.add(pair)
                land_pairs.discard(pair)
                continue
            if ptype.get(a_p) == "land" and ptype.get(b_p) == "land":
                is_strait = typ == "sea" or (through.isdigit() and int(through) > 0) or (rule in cross_rules)
                if is_strait:
                    strait_pairs.add(pair)
                else:
                    land_pairs.add(pair)

    # Filter river_hits >= 2 pixels to exclude stray 1-px corner touches
    major_river_pairs = {k: c for k, c in river_hits.items() if c >= 2 and k in land_pairs}
    return pmap, centroids, land_pairs, coastal_pairs, sea_pairs, major_river_pairs, strait_pairs


def build_csr(num_nodes: int, edges_map: dict[int, dict[int, int]]) -> tuple[list[int], list[int], list[int]]:
    """Build 1-based CSR arrays indexed by node id 1..num_nodes.

    offsets has length num_nodes + 2:
      offsets[0] = 0 (dummy)
      For node u in 1..num_nodes: edges live in [offsets[u], offsets[u+1]).
    """
    offsets = [0] * (num_nodes + 2)
    adj: list[int] = []
    eflag: list[int] = []
    for u in range(1, num_nodes + 1):
        offsets[u] = len(adj)
        nbrs = edges_map.get(u, {})
        for v in sorted(nbrs.keys()):
            adj.append(v)
            eflag.append(nbrs[v])
    offsets[num_nodes + 1] = len(adj)
    return offsets, adj, eflag


def merge_land_eflag(has_land: bool, has_river: bool, has_strait: bool, has_rail: bool) -> int:
    if has_land:
        if has_river:
            return EFLAG_RIVER_RAIL if has_rail else EFLAG_RIVER
        return EFLAG_LAND_RAIL if has_rail else EFLAG_LAND
    if has_strait:
        return EFLAG_STRAIT_RAIL if has_rail else EFLAG_STRAIT
    return 0


def compute_state_meta(st: dict) -> int:
    """Compact integer state classification 0..8:
      0 = impassable / MAP
      1 = normal interior
      2 = interior + supply hub
      3 = coastal port
      4 = coastal port + supply hub
      5 = major city (VP>=15) interior
      6 = major city + supply hub
      7 = major city + coastal port
      8 = major city + coastal port + supply hub
    """
    if not st["playable"]:
        return 0
    has_hub = len(st["supply_nodes"]) > 0
    has_port = len(st["naval_bases"]) > 0
    major_vp = st["total_vp"] >= 15
    code = 1
    if has_hub:
        code += 1
    if has_port:
        code += 2
    if major_vp:
        code += 4
    return code


def extract_sector_anchors(
    sectors: list[dict],
    states: dict[int, dict],
) -> tuple[dict[int, int], dict[int, list[int]]]:
    sr_to_sector: dict[int, int] = {}
    sector_anchors: dict[int, list[int]] = {}
    sr_states: dict[int, list[dict]] = defaultdict(list)
    for st in states.values():
        if st["playable"] and st["sr"] > 0:
            sr_states[st["sr"]].append(st)

    for sec in sectors:
        sec_id = sec["id"]
        for srid in sec["srs"]:
            sr_to_sector[srid] = sec_id

        def state_score(s: dict) -> float:
            return (
                (1 if s["supply_nodes"] else 0) * 60.0
                + s["total_vp"] * 3.0
                + (1 if s["naval_bases"] else 0) * 15.0
                + s["air_base"] * 4.0
                + s["manpower"] / 500000.0
            )

        # Round-robin pick best states per SR so small sectors (e.g. Italy 2-3 SRs, UK 5 SRs)
        # still get 6-10 spatially distributed anchors, capped at 12 total.
        per_sr_ranked: dict[int, list[tuple[float, int]]] = {}
        for srid in sec["srs"]:
            sts = sr_states.get(srid, [])
            if sts:
                per_sr_ranked[srid] = sorted(
                    [(state_score(s), s["id"]) for s in sts],
                    key=lambda x: (-x[0], x[1]),
                )

        target_anchors = min(12, max(8, len(per_sr_ranked)))
        chosen: list[int] = []
        chosen_set: set[int] = set()
        depth = 0
        while len(chosen) < target_anchors:
            round_cands = []
            for srid in sec["srs"]:
                ranked = per_sr_ranked.get(srid, [])
                if depth < len(ranked):
                    sc, sid = ranked[depth]
                    if sid not in chosen_set:
                        round_cands.append((sc, sid))
            if not round_cands:
                break
            round_cands.sort(key=lambda x: (-x[0], x[1]))
            for _sc, sid in round_cands:
                if len(chosen) >= target_anchors:
                    break
                chosen.append(sid)
                chosen_set.add(sid)
            depth += 1

        sector_anchors[sec_id] = chosen
    return sr_to_sector, sector_anchors


def extract_river_lines(
    river_lines: list[dict],
    major_river_pairs: dict[tuple[int, int], int],
    centroids: dict[int, tuple[float, float, int]],
    prov2state: dict[int, int],
    prov2sr: dict[int, int],
    states: dict[int, dict],
    supply_nodes: dict[int, int],
    rail_provs: set[int],
) -> dict[int, list[tuple[int, int]]]:
    """Return {river_line_id: [(state_id, prov_id), ...]} ordered by tactical value."""
    result: dict[int, list[tuple[int, int]]] = {}
    for rl in river_lines:
        target_srs = set(rl["srs"])
        side = rl["defend_side"]
        prov_scores: dict[int, float] = {}

        for (p1, p2), hits in major_river_pairs.items():
            if p1 not in centroids or p2 not in centroids:
                continue
            cx1, cy1, _ = centroids[p1]
            cx2, cy2, _ = centroids[p2]

            def_provs: list[int] = []
            if side == "east":
                if cx1 > cx2 + 1.0:
                    def_provs.append(p1)
                elif cx2 > cx1 + 1.0:
                    def_provs.append(p2)
            elif side == "west":
                if cx1 < cx2 - 1.0:
                    def_provs.append(p1)
                elif cx2 < cx1 - 1.0:
                    def_provs.append(p2)
            elif side == "north":
                if cy1 < cy2 - 1.0:
                    def_provs.append(p1)
                elif cy2 < cy1 - 1.0:
                    def_provs.append(p2)
            else:  # "both"
                def_provs.extend([p1, p2])

            for dp in def_provs:
                srid = prov2sr.get(dp, 0)
                sid = prov2state.get(dp, 0)
                if srid not in target_srs or sid not in states or not states[sid]["playable"]:
                    continue
                dcx, dcy, _ = centroids[dp]
                if "x_max" in rl and dcx > rl["x_max"]:
                    continue
                if "x_min" in rl and dcx < rl["x_min"]:
                    continue
                st = states[sid]
                sc = (
                    hits
                    + (80.0 if dp in supply_nodes else 0.0)
                    + st["vps"].get(dp, 0) * 5.0
                    + (25.0 if dp in rail_provs else 0.0)
                )
                if sc > prov_scores.get(dp, -1.0):
                    prov_scores[dp] = sc

        ranked = sorted(prov_scores.items(), key=lambda kv: (-kv[1], kv[0]))[: rl["max_provs"]]
        result[rl["id"]] = [(prov2state[dp], dp) for dp, _ in ranked]
    return result


def extract_sr_logistics(
    num_srs: int,
    srs: dict[int, dict],
    states: dict[int, dict],
) -> tuple[list[int], list[int]]:
    """Return per-SR O(1) lookup tables [0..num_srs]: top_hub_state, top_air_state."""
    sr_states: dict[int, list[dict]] = defaultdict(list)
    for st in states.values():
        if st["playable"] and st["sr"] > 0:
            sr_states[st["sr"]].append(st)

    top_hub = [0] * (num_srs + 1)
    top_air = [0] * (num_srs + 1)
    for srid in range(1, num_srs + 1):
        if srs[srid]["is_sea"]:
            continue
        sts = sr_states.get(srid, [])
        if not sts:
            continue
        best_hub = max(
            sts,
            key=lambda s: (
                len(s["supply_nodes"]) * 100
                + s["total_vp"] * 3
                + len(s["naval_bases"]) * 10
                + s["manpower"] / 500000.0
            ),
        )
        best_air = max(
            sts,
            key=lambda s: (
                s["air_base"] * 40
                + len(s["supply_nodes"]) * 25
                + s["total_vp"] * 2
                + s["manpower"] / 500000.0
            ),
        )
        top_hub[srid] = best_hub["id"]
        top_air[srid] = best_air["id"]
    return top_hub, top_air


def extract_invasion_targets(
    corridors: list[dict],
    states: dict[int, dict],
) -> dict[int, list[int]]:
    res: dict[int, list[int]] = {}
    for cor in corridors:
        tsrs = set(cor["target_srs"])
        coastal = [
            s
            for s in states.values()
            if s["playable"] and s["sr"] in tsrs and (s["naval_bases"] or s["is_coastal"])
        ]
        coastal.sort(
            key=lambda s: (
                -(max(s["naval_bases"].values(), default=0) * 30 + s["total_vp"] * 3 + len(s["supply_nodes"]) * 40),
                s["id"],
            )
        )
        res[cor["id"]] = [s["id"] for s in coastal[: cor["max_targets"]]]
    return res


def emit_clausewitz_map_data(
    out_path: Path,
    num_srs: int,
    num_states: int,
    srs: dict[int, dict],
    sr_off: list[int],
    sr_adj: list[int],
    sr_eflag: list[int],
    sr_to_sector: dict[int, int],
    sr_top_hub: list[int],
    sr_top_air: list[int],
    st_off: list[int],
    st_adj: list[int],
    st_eflag: list[int],
    states: dict[int, dict],
    sector_anchors: dict[int, list[int]],
    river_forts: dict[int, list[tuple[int, int]]],
    invasion_targets: dict[int, list[int]],
) -> None:
    """Emit compact, zero-meta_effect Clausewitz scripted_effects for O(1) CSR & L2 tables."""
    lines: list[str] = []
    w = lines.append

    w("############################################################################################################")
    w("# AUTO-GENERATED BY tools/build_eai_map_graph.py — DO NOT EDIT BY HAND")
    w("# Expert AI for Magna Europa: Reforged — Precompiled Map Graph (CSR) & L2 Tactical Indexes")
    w(f"# Nodes: {num_srs} strategic regions, {num_states} states | Edges: {len(sr_adj)} SR, {len(st_adj)} state")
    w("# Edge flags (eflag): 1=land 2=land+rail 3=river 4=river+rail 5=strait 6=strait+rail 7=coastal 8=sea")
    w("# State meta (meta):  0=impassable/MAP 1=land 2=+hub 3=+port 4=+hub+port 5=+majorVP 6..8=+combos")
    w("############################################################################################################")
    w("")

    # Master entry point
    w("ME_EAI_map_load_all = {")
    w("\tif = {")
    w("\t\tlimit = { NOT = { has_global_flag = ME_EAI_map_loaded } }")
    w("\t\tset_global_flag = ME_EAI_map_loaded")
    w(f"\t\tset_variable = {{ global.me_map_num_srs = {num_srs} }}")
    w(f"\t\tset_variable = {{ global.me_map_num_states = {num_states} }}")
    w("\t\tME_EAI_map_load_sr_csr = yes")
    w("\t\tME_EAI_map_load_state_meta = yes")
    w("\t\tME_EAI_map_load_state_edges = yes")
    w("\t\tME_EAI_map_load_tactical_indexes = yes")
    w("\t}")
    w("}")
    w("")

    def emit_int_array(arr_name: str, vals: list[int]) -> None:
        for v in vals:
            w(f"\tadd_to_array = {{ {arr_name} = {v} }}")

    # 1. Strategic Region CSR + per-SR O(1) arrays
    w("ME_EAI_map_load_sr_csr = {")
    emit_int_array("global.me_sr_off", sr_off)
    emit_int_array("global.me_sr_adj", sr_adj)
    emit_int_array("global.me_sr_eflag", sr_eflag)
    sr_sea = [0] + [1 if srs[i]["is_sea"] else 0 for i in range(1, num_srs + 1)]
    sr_sec = [0] + [sr_to_sector.get(i, 0) for i in range(1, num_srs + 1)]
    emit_int_array("global.me_sr_is_sea", sr_sea)
    emit_int_array("global.me_sr_sector", sr_sec)
    emit_int_array("global.me_sr_top_hub", sr_top_hub)
    emit_int_array("global.me_sr_top_air", sr_top_air)
    w("}")
    w("")

    # 2. State CSR offsets + per-state O(1) metadata + bidirectional scope handle <-> int index map
    w("ME_EAI_map_load_state_meta = {")
    emit_int_array("global.me_st_off", st_off)
    st_sr = [0] + [states[i]["sr"] for i in range(1, num_states + 1)]
    st_meta = [0] + [compute_state_meta(states[i]) for i in range(1, num_states + 1)]
    emit_int_array("global.me_st_sr", st_sr)
    emit_int_array("global.me_st_meta", st_meta)
    w("\tadd_to_array = { global.me_st_ref = 0 }")
    for i in range(1, num_states + 1):
        w(f"\t{i} = {{ set_variable = {{ me_sid = {i} }} add_to_array = {{ global.me_st_ref = THIS.id }} }}")
    w("}")
    w("")

    # 3. State CSR flat edges (split into 4 balanced chunks for clean parser frames)
    n_edges = len(st_adj)
    chunk = (n_edges + 3) // 4
    w("ME_EAI_map_load_state_edges = {")
    for ci in range(4):
        w(f"\tME_EAI_map_load_state_edges_{ci + 1} = yes")
    w("}")
    w("")
    for ci in range(4):
        lo = ci * chunk
        hi = min((ci + 1) * chunk, n_edges)
        w(f"ME_EAI_map_load_state_edges_{ci + 1} = {{")
        for k in range(lo, hi):
            w(f"\tadd_to_array = {{ global.me_st_adj = {st_adj[k]} }}")
            w(f"\tadd_to_array = {{ global.me_st_eflag = {st_eflag[k]} }}")
        w("}")
        w("")

    # 4. L2 Tactical Indexes (Sectors, Major River Lines, Amphibious Corridors)
    w("ME_EAI_map_load_tactical_indexes = {")
    w("\t# Operational Sectors (SRs + representative Anchor States)")
    for sec in SECTORS:
        k = sec["key"]
        w(f"\t# Sector {sec['id']}: {sec['label']}")
        emit_int_array(f"global.me_sec_{k}_srs", sec["srs"])
        emit_int_array(f"global.me_sec_{k}_anchors", sector_anchors[sec["id"]])

    w("\t# Major River Fortification Lines (parallel arrays: state_id & prov_id)")
    for rl in RIVER_LINES:
        k = rl["key"]
        pairs = river_forts[rl["id"]]
        w(f"\t# River Line {rl['id']}: {rl['label']} ({len(pairs)} provinces)")
        emit_int_array(f"global.me_riv_{k}_states", [s for s, _ in pairs])
        emit_int_array(f"global.me_riv_{k}_provs", [p for _, p in pairs])

    w("\t# European Amphibious Corridors (sea SR chains & priority target coastal states)")
    for cor in INVASION_CORRIDORS:
        k = cor["key"]
        w(f"\t# Corridor {cor['id']}: {cor['label']}")
        emit_int_array(f"global.me_inv_{k}_sea_srs", cor["sea_srs"])
        emit_int_array(f"global.me_inv_{k}_targets", invasion_targets[cor["id"]])
    w("}")
    w("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def render_preview(
    out_png: Path,
    pmap: np.ndarray,
    ptype: dict[int, str],
    prov2sr: dict[int, int],
    sr_to_sector: dict[int, int],
    centroids: dict[int, tuple[float, float, int]],
    supply_nodes: dict[int, int],
    river_forts: dict[int, list[tuple[int, int]]],
    sector_anchors: dict[int, list[int]],
    states: dict[int, dict],
) -> None:
    """Render a 2048x1536 QA map showing all 12 sectors, river defense lines, hubs, and anchors."""
    h, w = pmap.shape
    scale = 2
    small = pmap[::scale, ::scale]
    sh, sw = small.shape
    max_pid = int(pmap.max())

    sec_colors = {s["id"]: s["color"] for s in SECTORS}
    lut = np.zeros((max_pid + 1, 3), dtype=np.uint8)
    for pid, pt in ptype.items():
        if pid > max_pid:
            continue
        if pt == "sea":
            lut[pid] = (24, 38, 56)
        elif pt == "lake":
            lut[pid] = (34, 56, 78)
        else:
            srid = prov2sr.get(pid, 0)
            sec_id = sr_to_sector.get(srid, 0)
            if sec_id in sec_colors:
                lut[pid] = sec_colors[sec_id]
            else:
                lut[pid] = (68, 72, 76)

    rgb = lut[small]
    # Darken SR borders slightly for crisp visual clarity
    sr_lut = np.zeros(max_pid + 1, dtype=np.int16)
    for pid, srid in prov2sr.items():
        if pid <= max_pid:
            sr_lut[pid] = srid
    sr_map = sr_lut[small]
    bmask = np.zeros((sh, sw), dtype=bool)
    bmask[:-1, :] |= sr_map[:-1, :] != sr_map[1:, :]
    bmask[:, :-1] |= sr_map[:, :-1] != sr_map[:, 1:]
    rgb[bmask] = (rgb[bmask].astype(np.int16) * 6 // 10).astype(np.uint8)

    img = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(img)

    # Draw supply hubs as small yellow dots
    for pid in supply_nodes:
        if pid in centroids:
            cx, cy, _ = centroids[pid]
            x, y = int(cx / scale), int(cy / scale)
            draw.rectangle((x - 1, y - 1, x + 1, y + 1), fill=(255, 235, 90))

    # Draw major river defensive line provinces as bright cyan/red markers
    riv_colors = {
        1: (0, 255, 255),    # Dnieper east bank
        2: (255, 80, 220),   # Vistula west bank
        3: (255, 50, 50),    # Oder-Elbe west bank
        4: (255, 140, 0),    # Rhine east bank
        5: (120, 255, 120),  # Po north bank
        6: (255, 255, 255),  # Danube
        7: (0, 190, 255),    # Don-Volga east bank
    }
    for rl_id, pairs in river_forts.items():
        col = riv_colors.get(rl_id, (255, 255, 255))
        for _, pid in pairs:
            if pid in centroids:
                cx, cy, _ = centroids[pid]
                x, y = int(cx / scale), int(cy / scale)
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=col, outline=(10, 10, 10))

    # Draw sector anchor states as dark-rimmed white squares
    for sec_id, anchor_sids in sector_anchors.items():
        for sid in anchor_sids:
            st = states[sid]
            pts = [centroids[p] for p in st["provinces"] if p in centroids]
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            x, y = int(cx / scale), int(cy / scale)
            draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=(255, 255, 255), outline=(0, 0, 0))

    # Legend box in top-left corner
    lx, ly = 16, 16
    draw.rectangle((lx - 8, ly - 8, lx + 460, ly + 312), fill=(15, 20, 28), outline=(180, 190, 205))
    draw.text((lx, ly), "ME-EAI Precompiled Map Graph QA Preview", fill=(255, 255, 255))
    cy_leg = ly + 18
    for sec in SECTORS:
        draw.rectangle((lx, cy_leg + 2, lx + 12, cy_leg + 12), fill=sec["color"])
        draw.text((lx + 18, cy_leg), f"S{sec['id']:02d}: {sec['label']}", fill=(230, 235, 240))
        cy_leg += 15
    cy_leg += 6
    draw.text((lx, cy_leg), "Dots: Yellow=SupplyHub | WhiteSq=SectorAnchor", fill=(255, 235, 130))
    cy_leg += 15
    draw.text((lx, cy_leg), "River Lines: Cyan=Dnieper/Volga Mag=Vistula Red=Oder Org=Rhine Grn=Po", fill=(140, 245, 255))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)


def compile_map_graph(write_preview: bool = True) -> dict:
    rroot = Path(repo_root())
    mroot = Path(mod_root())
    mapdir = mroot / "map"

    rgb2id, ptype, terrain = load_definitions(mapdir)
    srs, prov2sr = parse_strategic_regions(mroot, ptype)
    states, prov2state = parse_states(mroot, prov2sr)
    supply_nodes, rail_edges = parse_supply_and_rail(mroot)

    num_srs = max(srs.keys())
    num_states = max(states.keys())
    assert len(srs) == num_srs, f"SR IDs not contiguous: {len(srs)} vs {num_srs}"
    assert len(states) == num_states, f"State IDs not contiguous: {len(states)} vs {num_states}"

    # Assign supply nodes to states
    for pid in supply_nodes:
        sid = prov2state.get(pid)
        if sid and sid in states:
            states[sid]["supply_nodes"].append(pid)

    pmap, centroids, land_pairs, coastal_pairs, sea_pairs, major_river_pairs, strait_pairs = analyze_rasters(
        mapdir, rgb2id, ptype
    )

    # Mark coastal states from pixel contact too
    for lpid, _spid in coastal_pairs:
        sid = prov2state.get(lpid)
        if sid and sid in states:
            states[sid]["is_coastal"] = True

    # Aggregate rail connectivity at state & SR level
    rail_provs: set[int] = set()
    st_rail: set[tuple[int, int]] = set()
    sr_rail: set[tuple[int, int]] = set()
    for a, b, _lvl in rail_edges:
        rail_provs.add(a)
        rail_provs.add(b)
        sa, sb = prov2state.get(a, 0), prov2state.get(b, 0)
        if sa and sb and sa != sb:
            st_rail.add((min(sa, sb), max(sa, sb)))
        sra, srb = prov2sr.get(a, 0), prov2sr.get(b, 0)
        if sra and srb and sra != srb:
            sr_rail.add((min(sra, srb), max(sra, srb)))

    # Aggregate state-level edges (playable states only)
    st_land: set[tuple[int, int]] = set()
    st_river: set[tuple[int, int]] = set()
    st_strait: set[tuple[int, int]] = set()
    sr_land: set[tuple[int, int]] = set()
    sr_river: set[tuple[int, int]] = set()
    sr_strait: set[tuple[int, int]] = set()

    for a, b in land_pairs:
        sa, sb = prov2state.get(a, 0), prov2state.get(b, 0)
        if sa and sb and sa != sb and states[sa]["playable"] and states[sb]["playable"]:
            pair = (min(sa, sb), max(sa, sb))
            st_land.add(pair)
            if (a, b) in major_river_pairs:
                st_river.add(pair)
        sra, srb = prov2sr.get(a, 0), prov2sr.get(b, 0)
        if sra and srb and sra != srb and (not srs[sra]["is_sea"]) and (not srs[srb]["is_sea"]):
            spair = (min(sra, srb), max(sra, srb))
            sr_land.add(spair)
            if (a, b) in major_river_pairs:
                sr_river.add(spair)

    for a, b in strait_pairs:
        sa, sb = prov2state.get(a, 0), prov2state.get(b, 0)
        if sa and sb and sa != sb and states[sa]["playable"] and states[sb]["playable"]:
            st_strait.add((min(sa, sb), max(sa, sb)))
        sra, srb = prov2sr.get(a, 0), prov2sr.get(b, 0)
        if sra and srb and sra != srb and (not srs[sra]["is_sea"]) and (not srs[srb]["is_sea"]):
            sr_strait.add((min(sra, srb), max(sra, srb)))

    # Build symmetric edge maps for CSR
    st_edges: dict[int, dict[int, int]] = defaultdict(dict)
    for u, v in st_land | st_strait:
        ef = merge_land_eflag(
            has_land=(u, v) in st_land,
            has_river=(u, v) in st_river,
            has_strait=(u, v) in st_strait,
            has_rail=(u, v) in st_rail,
        )
        if ef > 0:
            st_edges[u][v] = ef
            st_edges[v][u] = ef

    sr_edges: dict[int, dict[int, int]] = defaultdict(dict)
    for u, v in sr_land | sr_strait:
        ef = merge_land_eflag(
            has_land=(u, v) in sr_land,
            has_river=(u, v) in sr_river,
            has_strait=(u, v) in sr_strait,
            has_rail=(u, v) in sr_rail,
        )
        if ef > 0:
            sr_edges[u][v] = ef
            sr_edges[v][u] = ef

    # Coastal Land-SR <-> Sea-SR edges & Sea-SR <-> Sea-SR maritime edges
    for lpid, spid in coastal_pairs:
        srl, srs_id = prov2sr.get(lpid, 0), prov2sr.get(spid, 0)
        if srl and srs_id and srl != srs_id and (not srs[srl]["is_sea"]) and srs[srs_id]["is_sea"]:
            sr_edges[srl].setdefault(srs_id, EFLAG_COASTAL)
            sr_edges[srs_id].setdefault(srl, EFLAG_COASTAL)
    for a, b in sea_pairs:
        sra, srb = prov2sr.get(a, 0), prov2sr.get(b, 0)
        if sra and srb and sra != srb and srs[sra]["is_sea"] and srs[srb]["is_sea"]:
            sr_edges[sra].setdefault(srb, EFLAG_MARITIME)
            sr_edges[srb].setdefault(sra, EFLAG_MARITIME)

    sr_off, sr_adj, sr_eflag = build_csr(num_srs, sr_edges)
    st_off, st_adj, st_eflag = build_csr(num_states, st_edges)

    sr_to_sector, sector_anchors = extract_sector_anchors(SECTORS, states)
    river_forts = extract_river_lines(
        RIVER_LINES, major_river_pairs, centroids, prov2state, prov2sr, states, supply_nodes, rail_provs
    )
    sr_top_hub, sr_top_air = extract_sr_logistics(num_srs, srs, states)
    invasion_targets = extract_invasion_targets(INVASION_CORRIDORS, states)

    # Write L0 offline JSON graph (for Python audit tools & unit tests)
    l0_path = rroot / "_ctx" / "eai_map_graph.json"
    l0_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "num_srs": num_srs,
        "num_land_srs": sum(1 for s in srs.values() if not s["is_sea"]),
        "num_sea_srs": sum(1 for s in srs.values() if s["is_sea"]),
        "num_states": num_states,
        "num_playable_states": sum(1 for s in states.values() if s["playable"]),
        "num_sr_directed_edges": len(sr_adj),
        "num_state_directed_edges": len(st_adj),
        "num_major_river_prov_edges": len(major_river_pairs),
        "num_major_river_state_edges": len(st_river),
        "num_rail_state_edges": len(st_rail),
        "num_strait_state_edges": len(st_strait),
        "num_supply_nodes": len(supply_nodes),
    }
    l0_payload = {
        "summary": summary,
        "sr_csr": {
            "off": sr_off,
            "adj": sr_adj,
            "eflag": sr_eflag,
            "is_sea": [0] + [1 if srs[i]["is_sea"] else 0 for i in range(1, num_srs + 1)],
            "sector": [0] + [sr_to_sector.get(i, 0) for i in range(1, num_srs + 1)],
            "top_hub": sr_top_hub,
            "top_air": sr_top_air,
            "names": {str(i): srs[i]["name"] for i in range(1, num_srs + 1)},
        },
        "state_csr": {
            "off": st_off,
            "adj": st_adj,
            "eflag": st_eflag,
            "sr": [0] + [states[i]["sr"] for i in range(1, num_states + 1)],
            "meta": [0] + [compute_state_meta(states[i]) for i in range(1, num_states + 1)],
        },
        "sectors": [
            {**sec, "anchors": sector_anchors[sec["id"]]} for sec in SECTORS
        ],
        "river_lines": [
            {**rl, "fort_targets": river_forts[rl["id"]]} for rl in RIVER_LINES
        ],
        "invasion_corridors": [
            {**cor, "targets": invasion_targets[cor["id"]]} for cor in INVASION_CORRIDORS
        ],
    }
    l0_path.write_text(json.dumps(l0_payload, indent=2), encoding="utf-8")

    # Emit Clausewitz runtime script
    pdx_out = rroot / "mods" / "Expert-AI-for-Magna-Europa" / "common" / "scripted_effects" / "ME_EAI_map_data.txt"
    emit_clausewitz_map_data(
        pdx_out,
        num_srs,
        num_states,
        srs,
        sr_off,
        sr_adj,
        sr_eflag,
        sr_to_sector,
        sr_top_hub,
        sr_top_air,
        st_off,
        st_adj,
        st_eflag,
        states,
        sector_anchors,
        river_forts,
        invasion_targets,
    )

    if write_preview:
        png_out = rroot / "docs" / "eai" / "map_graph_preview.png"
        render_preview(
            png_out,
            pmap,
            ptype,
            prov2sr,
            sr_to_sector,
            centroids,
            supply_nodes,
            river_forts,
            sector_anchors,
            states,
        )

    return {
        "summary": summary,
        "pdx_bytes": pdx_out.stat().st_size,
        "pdx_lines": sum(1 for _ in open(pdx_out, encoding="utf-8")),
        "sector_anchor_counts": {s["key"]: len(sector_anchors[s["id"]]) for s in SECTORS},
        "river_line_counts": {rl["key"]: len(river_forts[rl["id"]]) for rl in RIVER_LINES},
        "invasion_target_counts": {c["key"]: len(invasion_targets[c["id"]]) for c in INVASION_CORRIDORS},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile ME map graph for Expert AI submod.")
    ap.add_argument("--no-preview", action="store_true", help="Skip rendering docs/eai/map_graph_preview.png")
    args = ap.parse_args()
    res = compile_map_graph(write_preview=not args.no_preview)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
