#!/usr/bin/env python
"""Generate crisp cartographic showcase PNGs for docs/mapping/ (pure Python stdlib).

Outputs under docs/mapping/assets/:
  - parent_color_comparison.png     Side-by-side Vanilla (left) vs ME (right) parent-color atlas
  - confidence_and_legacy_audit.png ME states colored by confidence tier & rescued legacy errors
  - strategic_regions_map.png       All 253 ME land+sea SRs colored by Vanilla parent SR (layer 5)

Dependencies: Python standard library only (no Pillow/numpy required).
"""
from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from _modroot import repo_root  # noqa: E402
from build_region_mapping import (  # noqa: E402
    load_bmp24,
    load_definition_csv,
    load_sr_loc,
    load_strategic_regions,
)

REPO = Path(repo_root())
DEFAULT_MOD = REPO / "mods" / "Magna-Europa"
DEFAULT_EU_DIR = REPO / "docs" / "states" / "eu_mapping"
DEFAULT_OUT = REPO / "docs" / "mapping" / "assets"

# 8 distinct cartographic land colors (muted, high contrast, colorblind-safe spread)
LAND_PALETTE_8: list[tuple[int, int, int]] = [
    (216, 179, 101),  # warm ochre
    (118, 179, 157),  # sage teal
    (217, 134, 114),  # terracotta
    (141, 160, 203),  # periwinkle
    (179, 205, 116),  # olive meadow
    (224, 159, 189),  # dusty rose
    (136, 196, 214),  # sky slate
    (201, 184, 154),  # sand beige
]

# 6 oceanic hues for sea strategic regions
SEA_PALETTE_6: list[tuple[int, int, int]] = [
    (29, 68, 102),
    (36, 86, 122),
    (24, 58, 88),
    (43, 101, 132),
    (32, 76, 114),
    (48, 92, 118),
]


def read_png_rgb(path: Path) -> tuple[int, int, list[int]]:
    """Decode an 8-bit color_type=2 PNG into (w, h, packed_int_pixels)."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG file: {path}")
    pos = 8
    idat = bytearray()
    w = h = bd = ct = 0
    while pos < len(raw):
        ln, typ = struct.unpack(">I4s", raw[pos : pos + 8])
        data = raw[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", data[:10])
            if bd != 8 or ct != 2:
                raise ValueError(f"Expected 8-bit RGB PNG in {path}, got bd={bd}, ct={ct}")
        elif typ == b"IDAT":
            idat.extend(data)
        elif typ == b"IEND":
            break
    dec = zlib.decompress(bytes(idat))
    stride = w * 3
    pixels = [0] * (w * h)
    prev = bytearray(stride)
    for y in range(h):
        off = y * (stride + 1)
        f = dec[off]
        row = bytearray(dec[off + 1 : off + 1 + stride])
        if f == 1:
            for i in range(3, stride):
                row[i] = (row[i] + row[i - 3]) & 0xFF
        elif f == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                row[i] = (row[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                b = prev[i]
                c = prev[i - 3] if i >= 3 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        row_off = y * w
        for x in range(w):
            k = x * 3
            pixels[row_off + x] = (row[k] << 16) | (row[k + 1] << 8) | row[k + 2]
        prev = row
    return w, h, pixels


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_indexed_png(path: Path, w: int, h: int, indices: bytes | bytearray, palette: list[tuple[int, int, int]]) -> None:
    """Write a compact 8-bit indexed-color PNG (zlib level 9)."""
    if len(palette) > 256:
        raise ValueError(f"Palette exceeds 256 colors: {len(palette)}")
    if len(indices) != w * h:
        raise ValueError(f"Index buffer length {len(indices)} != {w}*{h}")
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0)
    plte = b"".join(struct.pack("BBB", r, g, b) for r, g, b in palette)
    rows = bytearray((w + 1) * h)
    src = memoryview(indices)
    for y in range(h):
        dst_off = y * (w + 1)
        rows[dst_off] = 0  # filter=None
        rows[dst_off + 1 : dst_off + 1 + w] = src[y * w : (y + 1) * w]
    idat = zlib.compress(bytes(rows), level=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"PLTE", plte))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


def graph_color(adj: dict[int, set[int]], nodes: list[int], n_colors: int) -> dict[int, int]:
    """Deterministic greedy graph coloring ordered by descending degree, then ID."""
    order = sorted(nodes, key=lambda u: (-len(adj.get(u, ())), u))
    colors: dict[int, int] = {}
    for u in order:
        used = {colors[v] for v in adj.get(u, ()) if v in colors}
        chosen = None
        for c in range(n_colors):
            if c not in used:
                chosen = c
                break
        if chosen is None:
            # Minimum-conflict fallback if local subgraph exceeds n_colors
            counts = [sum(1 for v in adj.get(u, ()) if colors.get(v) == c) for c in range(n_colors)]
            chosen = min(range(n_colors), key=lambda c: (counts[c], c))
        colors[u] = chosen
    return colors


def render_state_maps(eu_dir: Path, out_dir: Path) -> None:
    van_w, van_h, van_px = read_png_rgb(eu_dir / "review" / "van_hit.png")
    me_w, me_h, me_px = read_png_rgb(eu_dir / "review" / "me_hit.png")
    if (van_w, van_h) != (me_w, me_h):
        raise ValueError(f"Mismatched hit map dimensions: {(van_w, van_h)} vs {(me_w, me_h)}")
    w, h = van_w, van_h

    # Load m2v state_partition_m2v + legacy_audit
    m2v_parent: dict[int, int] = {}
    m2v_tier: dict[int, str] = {}
    with open(eu_dir / "state_partition_m2v.tsv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            mid = int(row["me_id"])
            if row["v_id"]:
                m2v_parent[mid] = int(row["v_id"])
            m2v_tier[mid] = row["confidence_tier"]

    legacy_rescued: set[int] = set()
    with open(eu_dir / "legacy_audit.tsv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row["diff_class"] in ("legacy_distant_error", "legacy_wrong_owner"):
                legacy_rescued.add(int(row["me_id"]))

    # Build 4-neighbor adjacency of Vanilla IDs from van_hit.png (horizontal + vertical scan)
    van_adj: dict[int, set[int]] = defaultdict(set)
    van_nodes: set[int] = set()
    for y in range(h):
        roff = y * w
        for x in range(w):
            a = van_px[roff + x]
            if a == 0:
                continue
            van_nodes.add(a)
            if x + 1 < w:
                b = van_px[roff + x + 1]
                if b and b != a:
                    van_adj[a].add(b)
                    van_adj[b].add(a)
            if y + 1 < h:
                c = van_px[roff + w + x]
                if c and c != a:
                    van_adj[a].add(c)
                    van_adj[c].add(a)
    van_colors = graph_color(van_adj, sorted(van_nodes), len(LAND_PALETTE_8))

    # Palette for parent_color_comparison.png:
    # 0: sea (#162333)
    # 1: dark divider / macro border (#121923)
    # 2: sibling ME border (#445263)
    # 3..10: LAND_PALETTE_8
    cmp_palette: list[tuple[int, int, int]] = [
        (22, 35, 51),
        (18, 25, 35),
        (68, 82, 99),
        *LAND_PALETTE_8,
    ]
    gap = 6
    cw = w * 2 + gap
    cmp_idx = bytearray(cw * h)
    # Fill divider bar with color index 1
    for y in range(h):
        d_off = y * cw + w
        cmp_idx[d_off : d_off + gap] = b"\x01" * gap

    # Left panel: Vanilla
    for y in range(h):
        roff = y * w
        dst_off = y * cw
        for x in range(w):
            v = van_px[roff + x]
            if v == 0:
                cmp_idx[dst_off + x] = 0
                continue
            xr = van_px[roff + x + 1] if x + 1 < w else 0
            yd = van_px[roff + w + x] if y + 1 < h else 0
            xl = van_px[roff + x - 1] if x > 0 else 0
            yu = van_px[roff - w + x] if y > 0 else 0
            if v != xr or v != yd or v != xl or v != yu:
                cmp_idx[dst_off + x] = 1
            else:
                cmp_idx[dst_off + x] = 3 + van_colors.get(v, 0)

    # Right panel: ME colored by parent Vanilla color
    for y in range(h):
        roff = y * w
        dst_off = y * cw + w + gap
        for x in range(w):
            m = me_px[roff + x]
            if m == 0:
                cmp_idx[dst_off + x] = 0
                continue
            p = m2v_parent.get(m, 0)
            mr = me_px[roff + x + 1] if x + 1 < w else 0
            md = me_px[roff + w + x] if y + 1 < h else 0
            ml = me_px[roff + x - 1] if x > 0 else 0
            mu = me_px[roff - w + x] if y > 0 else 0
            if (
                p != m2v_parent.get(mr, -1 if mr == 0 else 0)
                or p != m2v_parent.get(md, -1 if md == 0 else 0)
                or p != m2v_parent.get(ml, -1 if ml == 0 else 0)
                or p != m2v_parent.get(mu, -1 if mu == 0 else 0)
                or mr == 0
                or md == 0
                or ml == 0
                or mu == 0
            ):
                cmp_idx[dst_off + x] = 1
            elif m != mr or m != md:
                cmp_idx[dst_off + x] = 2
            else:
                cmp_idx[dst_off + x] = 3 + van_colors.get(p, 0) if p else 2

    cmp_path = out_dir / "parent_color_comparison.png"
    write_indexed_png(cmp_path, cw, h, cmp_idx, cmp_palette)

    # confidence_and_legacy_audit.png
    # 0: sea (#162333)
    # 1: coastline/macro border (#111822)
    # 2: sibling border (#243242)
    # 3: high_confidence (#3e867e)
    # 4: resolved straddler / island (#e5a336)
    # 5: human adjudicated (#3a86ff)
    # 6: rescued legacy error (#e63946)
    aud_palette = [
        (22, 35, 51),
        (17, 24, 34),
        (36, 50, 66),
        (62, 134, 126),
        (229, 163, 54),
        (58, 134, 255),
        (230, 57, 70),
    ]
    cls_for_me: dict[int, int] = {}
    counts = defaultdict(int)
    for m in m2v_parent:
        if m in legacy_rescued:
            c = 6
        elif m2v_tier.get(m) == "adjudicated":
            c = 5
        elif m2v_tier.get(m) in ("medium_straddler", "fallback_island"):
            c = 4
        else:
            c = 3
        cls_for_me[m] = c
        counts[c] += 1

    aud_idx = bytearray(w * h)
    for y in range(h):
        roff = y * w
        for x in range(w):
            m = me_px[roff + x]
            if m == 0:
                aud_idx[roff + x] = 0
                continue
            mr = me_px[roff + x + 1] if x + 1 < w else 0
            md = me_px[roff + w + x] if y + 1 < h else 0
            ml = me_px[roff + x - 1] if x > 0 else 0
            mu = me_px[roff - w + x] if y > 0 else 0
            if mr == 0 or md == 0 or ml == 0 or mu == 0:
                aud_idx[roff + x] = 1
            elif m != mr or m != md:
                aud_idx[roff + x] = 2
            else:
                aud_idx[roff + x] = cls_for_me.get(m, 3)

    aud_path = out_dir / "confidence_and_legacy_audit.png"
    write_indexed_png(aud_path, w, h, aud_idx, aud_palette)
    print(f"Wrote {cmp_path.name} ({cmp_path.stat().st_size:,} B) & {aud_path.name} ({aud_path.stat().st_size:,} B)")
    print(f"  audit pixel-tier state counts: legacy_rescued={counts[6]}, adj_only={counts[5]}, straddler_island={counts[4]}, high={counts[3]}")


def render_strategic_regions_map(mod_root: Path, eu_dir: Path, out_dir: Path, stride: int = 4) -> None:
    me_defs = load_definition_csv(mod_root)
    me_loc = load_sr_loc(mod_root)
    me_srs, me_prov_to_sr = load_strategic_regions(mod_root, me_defs, me_loc)

    rgb_to_sr: dict[tuple[int, int, int], tuple[int, bool]] = {}
    for pid, (ptype, rgb) in me_defs.items():
        srid = me_prov_to_sr.get(pid)
        if srid is None:
            continue
        is_sea = (ptype != "land")
        rgb_to_sr[rgb] = (srid, is_sea)

    sr_parent: dict[int, int] = {}
    sr_status: dict[int, str] = {}
    sr_kind: dict[int, str] = {}
    with open(eu_dir / "region_partition_m2v.tsv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            mid = int(row["me_region_id"])
            sr_status[mid] = row["status"]
            sr_kind[mid] = row["kind"]
            if row["vanilla_region_id"]:
                sr_parent[mid] = int(row["vanilla_region_id"])

    bw, bh, rstride, buf, top_down = load_bmp24(mod_root / "map" / "provinces.bmp")
    ow = bw // stride
    oh = bh // stride

    grid_sr = [0] * (ow * oh)
    grid_sea = bytearray(ow * oh)
    for oy in range(oh):
        sy = oy * stride
        bmp_row = sy if top_down else (bh - 1 - sy)
        row_base = bmp_row * rstride
        dst_base = oy * ow
        for ox in range(ow):
            k = row_base + (ox * stride) * 3
            # 24-bit BMP stores B, G, R
            rgb = (buf[k + 2], buf[k + 1], buf[k])
            info = rgb_to_sr.get(rgb)
            if info is not None:
                grid_sr[dst_base + ox] = info[0]
                grid_sea[dst_base + ox] = 1 if info[1] else 0

    # Adjacency among mapped Vanilla parent SRs (separately for land and sea)
    land_adj: dict[int, set[int]] = defaultdict(set)
    sea_adj: dict[int, set[int]] = defaultdict(set)
    land_nodes: set[int] = set()
    sea_nodes: set[int] = set()
    for y in range(oh):
        roff = y * ow
        for x in range(ow):
            s1 = grid_sr[roff + x]
            if s1 == 0 or sr_status.get(s1) not in ("mapped", "manual_override"):
                continue
            v1 = sr_parent.get(s1, 0)
            k1 = grid_sea[roff + x]
            if k1 == 0:
                land_nodes.add(v1)
            else:
                sea_nodes.add(v1)
            for nx, ny in ((x + 1, y), (x, y + 1)):
                if nx < ow and ny < oh:
                    s2 = grid_sr[ny * ow + nx]
                    if s2 == 0 or sr_status.get(s2) not in ("mapped", "manual_override"):
                        continue
                    v2 = sr_parent.get(s2, 0)
                    k2 = grid_sea[ny * ow + nx]
                    if k1 == k2 and v1 != v2:
                        if k1 == 0:
                            land_adj[v1].add(v2)
                            land_adj[v2].add(v1)
                        else:
                            sea_adj[v1].add(v2)
                            sea_adj[v2].add(v1)

    land_colors = graph_color(land_adj, sorted(land_nodes), len(LAND_PALETTE_8))
    sea_colors = graph_color(sea_adj, sorted(sea_nodes), len(SEA_PALETTE_6))

    # Palette:
    # 0: unmapped / background (#141b24)
    # 1: coastline border (#10161e)
    # 2: macro v_region border (#202b38)
    # 3: sibling ME SR border (#465668)
    # 4: excluded inset/proxy land (#3b424d)
    # 5: excluded inset sea (#1f2c3a)
    # 6..13: LAND_PALETTE_8 (8)
    # 14..19: SEA_PALETTE_6 (6)
    sr_palette: list[tuple[int, int, int]] = [
        (20, 27, 36),
        (16, 22, 30),
        (32, 43, 56),
        (70, 86, 104),
        (59, 66, 77),
        (31, 44, 58),
        *LAND_PALETTE_8,
        *SEA_PALETTE_6,
    ]

    sr_idx = bytearray(ow * oh)
    for y in range(oh):
        roff = y * ow
        for x in range(ow):
            s = grid_sr[roff + x]
            if s == 0:
                sr_idx[roff + x] = 0
                continue
            is_sea = grid_sea[roff + x]
            sr_r = grid_sr[roff + x + 1] if x + 1 < ow else 0
            sr_d = grid_sr[roff + ow + x] if y + 1 < oh else 0
            sea_r = grid_sea[roff + x + 1] if x + 1 < ow else is_sea
            sea_d = grid_sea[roff + ow + x] if y + 1 < oh else is_sea
            if is_sea != sea_r or is_sea != sea_d:
                sr_idx[roff + x] = 1
                continue
            st = sr_status.get(s, "")
            if st not in ("mapped", "manual_override"):
                sr_idx[roff + x] = 5 if is_sea else 4
                continue
            v = sr_parent.get(s, 0)
            if (sr_r != s and sr_parent.get(sr_r, -1) != v) or (sr_d != s and sr_parent.get(sr_d, -1) != v):
                sr_idx[roff + x] = 2
            elif sr_r != s or sr_d != s:
                sr_idx[roff + x] = 3
            else:
                sr_idx[roff + x] = (14 + sea_colors.get(v, 0)) if is_sea else (6 + land_colors.get(v, 0))

    sr_path = out_dir / "strategic_regions_map.png"
    write_indexed_png(sr_path, ow, oh, sr_idx, sr_palette)
    print(f"Wrote {sr_path.name} ({ow}x{oh}, {sr_path.stat().st_size:,} B)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render docs/mapping/assets/*.png showcase maps.")
    parser.add_argument("--mod", type=Path, default=DEFAULT_MOD)
    parser.add_argument("--eu-dir", type=Path, default=DEFAULT_EU_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sr-stride", type=int, default=4)
    args = parser.parse_args()

    render_state_maps(args.eu_dir, args.out)
    render_strategic_regions_map(args.mod, args.eu_dir, args.out, stride=args.sr_stride)


if __name__ == "__main__":
    main()
