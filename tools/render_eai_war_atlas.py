#!/usr/bin/env python3
"""
Expert AI for Magna Europa: Reforged — Tactical War Atlas & Frontline/Logistics Renderer
Renders high-resolution military operations maps directly from HOI4 .hoi4 saves + ME map files:
  - Exact province-level occupation & political control (faction-coded)
  - Per-province troop concentration heatmap (deeper/richer faction shading where divisions mass)
  - Armored/Mechanized corps cluster diamonds (Panzer / Tank Army highlights)
  - Glowing active opposing combat frontlines (between belligerents at war)
  - Complete railway network (trunk lines Lv 3-5 bold, branch lines Lv 1-2 thin)
  - All 674 supply hubs + highlighted active OP1/OP2 railway-hub battle targets
  - Built honest river fortifications (bunker >= 1 on precompiled river lines)
  - Operational battle arrows (jump-off state -> target railway supply hub) with phase badges
  - Supports both Full Europe+Med view and zoomed Eastern Front / Western Front detail crops!
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _modroot import mod_root, repo_root  # noqa: E402

# Theater crop boxes on 4096x3072 provinces.bmp: (x0, y0, x1, y1)
VIEWS = {
    "europe": (760, 420, 3960, 2980),       # 3200x2560 -> 2000x1600
    "east_front": (1720, 760, 3560, 2232),  # 1840x1472 -> 1840x1472 (Berlin -> Moscow/Stalingrad)
    "west_med": (920, 1080, 2600, 2680),    # 1680x1600 -> 1680x1600 (UK/France/Germany/Italy/Libya)
}

# Clean cartographic base palette (muted so troop density, railways, hubs & frontlines pop!)
SEA_COL = (20, 32, 46)
LAKE_COL = (30, 48, 66)
IMPASS_COL = (48, 50, 54)

TAG_BASE_COLORS = {
    # Axis
    "GER": (92, 98, 106),
    "RKB": (110, 114, 120),
    "RKN": (110, 114, 120),
    "RKH": (110, 114, 120),
    "RKU": (112, 116, 122),
    "RKO": (112, 116, 122),
    "ITA": (98, 122, 86),
    "HUN": (132, 112, 84),
    "ROM": (142, 124, 88),
    "BUL": (114, 120, 90),
    "SLO": (122, 118, 106),
    "FIN": (132, 142, 150),
    # Comintern
    "SOV": (144, 78, 74),
    "SOU": (144, 78, 74),
    # Allies
    "ENG": (152, 100, 108),
    "USA": (76, 130, 146),
    "FRA": (84, 114, 152),
    "POL": (144, 112, 122),
    "NOR": (116, 126, 146),
    "GRE": (96, 134, 158),
    "YUG": (108, 118, 146),
    "HOL": (148, 122, 94),
    "BEL": (138, 128, 98),
    "EGY": (156, 136, 108),
    "CAN": (148, 96, 104),
    "DEN": (136, 116, 116),
    # Neutrals
    "SWE": (106, 124, 136),
    "SPR": (150, 134, 106),
    "SPA": (150, 134, 106),
    "POR": (112, 130, 108),
    "TUR": (146, 132, 118),
    "SWI": (144, 126, 122),
    "IRE": (116, 138, 114),
    "IRQ": (140, 128, 108),
    "PER": (136, 128, 116),
    "SAU": (138, 136, 116),
}
DEFAULT_LAND_COL = (132, 130, 120)

# Deep saturated hotspot colors for heavy troop concentration (重兵汇集颜色加深)
BLOC_HOT_COLORS = {
    "axis": (22, 24, 34),        # deep iron-black / Feldgrau core
    "comintern": (195, 16, 18),  # deep Soviet blood-crimson
    "allies": (18, 70, 205),     # deep royal cobalt blue
    "neutral": (84, 80, 68),
}
BLOC_ARMOR_COLORS = {
    "axis": (255, 175, 40),      # Panzer amber-gold
    "comintern": (255, 58, 58),  # Red Army tank scarlet
    "allies": (56, 224, 255),    # Allied armor cyan
    "neutral": (210, 210, 200),
}
OP_ARROW_COLORS = {
    "GER": (255, 168, 32),
    "SOV": (255, 52, 52),
    "ENG": (65, 195, 255),
    "USA": (40, 245, 210),
    "ITA": (145, 245, 75),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arialbd.ttf"]
        if bold
        else [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"]
    )
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


class StaticMapAssets:
    """Loads provinces.bmp, definition.csv, history/states, railways, supply hubs, river lines once."""

    def __init__(self) -> None:
        self.rroot = Path(repo_root())
        self.mroot = Path(mod_root())
        mapdir = self.mroot / "map"

        # 1. definition.csv
        rgb2id: dict[tuple[int, int, int], int] = {}
        self.ptype: dict[int, str] = {}
        with open(mapdir / "definition.csv", encoding="utf-8", errors="replace") as fh:
            for r in csv.reader(fh, delimiter=";"):
                if len(r) < 5 or not r[0].isdigit():
                    continue
                pid = int(r[0])
                rgb2id[(int(r[1]), int(r[2]), int(r[3]))] = pid
                self.ptype[pid] = r[4].strip().lower()

        # 2. provinces.bmp -> int32 pmap
        pim = np.array(Image.open(mapdir / "provinces.bmp").convert("RGB"))
        self.h, self.w = pim.shape[:2]
        key = (
            pim[:, :, 0].astype(np.int64) * 65536
            + pim[:, :, 1].astype(np.int64) * 256
            + pim[:, :, 2].astype(np.int64)
        )
        uniq, inv = np.unique(key, return_inverse=True)
        k2id = {r * 65536 + g * 256 + b: pid for (r, g, b), pid in rgb2id.items()}
        uid = np.array([k2id.get(int(u), 0) for u in uniq], dtype=np.int32)
        self.pmap = uid[inv.reshape(self.h, self.w)]
        self.max_pid = int(self.pmap.max())

        # Vectorized centroids
        flat = self.pmap.ravel()
        counts = np.bincount(flat, minlength=self.max_pid + 1)
        xs = np.broadcast_to(np.arange(self.w, dtype=np.float64)[None, :], (self.h, self.w)).ravel()
        ys = np.broadcast_to(np.arange(self.h, dtype=np.float64)[:, None], (self.h, self.w)).ravel()
        sx = np.bincount(flat, weights=xs, minlength=self.max_pid + 1)
        sy = np.bincount(flat, weights=ys, minlength=self.max_pid + 1)
        self.centroids: dict[int, tuple[float, float]] = {}
        for pid in np.nonzero(counts)[0].tolist():
            if pid > 0:
                c = counts[pid]
                self.centroids[pid] = (float(sx[pid] / c), float(sy[pid] / c))

        # 3. history/states -> prov2state, state2provs, state_names, state_centroids
        self.prov2state: dict[int, int] = {}
        self.state2provs: dict[int, list[int]] = {}
        self.state_names: dict[int, str] = {}
        self.state_centroids: dict[int, tuple[float, float]] = {}
        self.state_impassable: set[int] = set()
        for p in sorted((self.mroot / "history" / "states").glob("*.txt")):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            m_id = re.search(r"\bid\s*=\s*(\d+)", txt)
            if not m_id:
                continue
            sid = int(m_id.group(1))
            m_nm = re.match(r"^\d+-(.+)\.txt$", p.name)
            self.state_names[sid] = m_nm.group(1) if m_nm else f"State {sid}"
            if re.search(r"\bimpassable\s*=\s*yes", txt):
                self.state_impassable.add(sid)
            m_pr = re.search(r"provinces\s*=\s*\{([^}]+)\}", txt)
            pids = [int(x) for x in m_pr.group(1).split() if x.isdigit()] if m_pr else []
            self.state2provs[sid] = pids
            pts = []
            for pid in pids:
                self.prov2state[pid] = sid
                if pid in self.centroids:
                    pts.append(self.centroids[pid])
            if pts:
                self.state_centroids[sid] = (
                    sum(x for x, _ in pts) / len(pts),
                    sum(y for _, y in pts) / len(pts),
                )

        # 4. railways.txt -> list of (level, [pid1, pid2, ...])
        self.railways: list[tuple[int, list[int]]] = []
        rfile = mapdir / "railways.txt"
        if rfile.exists():
            for line in rfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                toks = [int(x) for x in line.split() if x.isdigit()]
                if len(toks) >= 4:
                    lvl = toks[0]
                    chain = toks[2:]
                    self.railways.append((lvl, chain))

        # 5. supply_nodes.txt -> set of hub province IDs
        self.supply_hubs: set[int] = set()
        sfile = mapdir / "supply_nodes.txt"
        if sfile.exists():
            for line in sfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                toks = [int(x) for x in line.split() if x.isdigit()]
                if len(toks) >= 2:
                    self.supply_hubs.add(toks[1])

        # 6. Precompiled river line provinces from _ctx/eai_map_graph.json
        self.river_provs: set[int] = set()
        gpath = self.rroot / "_ctx" / "eai_map_graph.json"
        if gpath.exists():
            g = json.loads(gpath.read_text(encoding="utf-8"))
            for rl in g.get("river_lines", []):
                for _sid, pid in rl.get("fort_targets", []):
                    self.river_provs.add(int(pid))


def extract_top_block(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}=\{{\r?\n", text, re.M)
    if not m:
        return ""
    if name == "countries":
        end = re.search(r"^faction=\{\r?\n", text[m.end():], re.M)
        return text[m.end(): m.end() + end.start()] if end else text[m.end():]
    end = re.search(r"^\}\r?\n", text[m.end():], re.M)
    return text[m.end(): m.end() + end.start()] if end else text[m.end():]


def get_subblock(text: str, key: str, indent: int) -> str:
    tabs = "\t" * indent
    m = re.search(rf"^{tabs}{re.escape(key)}=\{{\r?\n", text, re.M)
    if not m:
        return ""
    end = re.search(rf"^{tabs}\}}\r?\n", text[m.end():], re.M)
    return text[m.end(): m.end() + end.start()] if end else ""


def parse_save_state(save_path: Path, assets: StaticMapAssets) -> dict:
    """Parse province controllers, factions, active wars, divisions (+armor), OP1/OP2 targets, and river forts."""
    txt = save_path.read_text(encoding="utf-8", errors="ignore")

    dm = re.search(r'^date="(\d+\.\d+\.\d+)\.', txt, re.M)
    date_str = dm.group(1) if dm else save_path.stem

    # 1. State owners & base state controllers
    states_blk = extract_top_block(txt, "states")
    state_owner: dict[int, str] = {}
    state_ctrl: dict[int, str] = {}
    state_riv_forts: set[int] = set()
    for m in re.finditer(r"^\t(\d+)=\{\r?\n(.*?)^\t\}\r?\n", states_blk, re.M | re.S):
        sid = int(m.group(1))
        sb = m.group(2)
        om = re.search(r'^\t\towner="([A-Z0-9]{3})"', sb, re.M)
        if om:
            own = om.group(1)
            state_owner[sid] = own
            cm = re.search(r'^\t\tcontroller="([A-Z0-9]{3})"', sb, re.M)
            state_ctrl[sid] = cm.group(1) if cm else own
        if "me_eai_riv_lv=" in sb:
            state_riv_forts.add(sid)

    # 2. Province-level exact controller overrides & built bunkers
    prov_ctrl: dict[int, str] = {}
    for pid, sid in assets.prov2state.items():
        if sid in state_ctrl:
            prov_ctrl[pid] = state_ctrl[sid]

    prov_bunkers: dict[int, int] = {}
    provs_blk = extract_top_block(txt, "provinces")
    for m in re.finditer(r"^\t(\d+)=\{\r?\n(.*?)^\t\}\r?\n", provs_blk, re.M | re.S):
        pid = int(m.group(1))
        pb = m.group(2)
        cm = re.search(r'^\t\tcontroller="([A-Z0-9]{3})"', pb, re.M)
        if cm:
            prov_ctrl[pid] = cm.group(1)
        bm = re.search(r"bunker=\{\s*level=(\d+)", pb, re.S)
        if bm:
            prov_bunkers[pid] = int(bm.group(1))

    # 3. Factions -> map each country to bloc: axis / comintern / allies / neutral
    bloc_of: dict[str, str] = {}
    for m in re.finditer(r"(?:^|\n)faction=\{\r?\n(.*?)\n\}\r?\n", txt, re.S):
        fb = m.group(1)
        members = re.findall(r'"([A-Z0-9]{3})"', (re.search(r"members=\{(.*?)\}", fb, re.S) or re.match("", "")).group(1) if re.search(r"members=\{(.*?)\}", fb, re.S) else "")
        mset = set(members)
        if "GER" in mset or "ITA" in mset or "faction_template_axis" in fb:
            bname = "axis"
        elif "SOV" in mset or "faction_template_comintern" in fb:
            bname = "comintern"
        elif "ENG" in mset or "USA" in mset or "FRA" in mset or "faction_template_allies" in fb:
            bname = "allies"
        else:
            bname = "neutral"
        for tag in members:
            bloc_of[tag] = bname

    # Fallbacks for key tags even before joining faction
    for t, b in [("GER", "axis"), ("ITA", "axis"), ("SOV", "comintern"), ("ENG", "allies"), ("FRA", "allies"), ("USA", "allies")]:
        bloc_of.setdefault(t, b)

    # 4. Active wars -> set of belligerent tag pairs (a, b)
    war_pairs: set[tuple[str, str]] = set()
    for m in re.finditer(r"active_war=\{(.*?)\n\}\r?\n", txt, re.S):
        wb = m.group(1)
        atk_m = re.search(r"attackers=\{(.*?)\}", wb, re.S)
        def_m = re.search(r"defenders=\{(.*?)\}", wb, re.S)
        atks = re.findall(r'"([A-Z0-9]{3})"', atk_m.group(1)) if atk_m else []
        defs = re.findall(r'"([A-Z0-9]{3})"', def_m.group(1)) if def_m else []
        for a in atks:
            for d in defs:
                war_pairs.add((a, d))
                war_pairs.add((d, a))

    # Also detect active wars inside diplomacy block (HOI4 1.20 stores active_war nested inside diplomacy/wars)
    if not war_pairs:
        for m in re.finditer(r"war=\{\r?\n(.*?)\n\t\}", txt, re.S):
            wb = m.group(1)
            atk_m = re.search(r"attackers=\{(.*?)\}", wb, re.S)
            def_m = re.search(r"defenders=\{(.*?)\}", wb, re.S)
            atks = re.findall(r'"([A-Z0-9]{3})"', atk_m.group(1)) if atk_m else []
            defs = re.findall(r'"([A-Z0-9]{3})"', def_m.group(1)) if def_m else []
            for a in atks:
                for d in defs:
                    war_pairs.add((a, d))
                    war_pairs.add((d, a))

    # Fallback: parse war casualties pairs via original_attacker / active belligerents from faction hostilities if needed
    if not war_pairs:
        # Check country blocks for enemies={ ... } or active wars in year >= 1939.9
        yr = int(date_str.split(".")[0])
        mo = int(date_str.split(".")[1])
        if (yr, mo) >= (1939, 9):
            for t1, b1 in bloc_of.items():
                for t2, b2 in bloc_of.items():
                    if b1 == "axis" and b2 in ("allies", "comintern"):
                        if b2 == "comintern" and (yr, mo) < (1940, 6):
                            continue
                        war_pairs.add((t1, t2))
                        war_pairs.add((t2, t1))

    # 5. Division templates -> identify armored/mechanized templates
    armor_tpls: set[int] = set()
    tpl_blk = extract_top_block(txt, "division_templates")
    for m in re.finditer(r"^\tdivision_template=\{\r?\n(.*?)^\t\}\r?\n", tpl_blk, re.M | re.S):
        tb = m.group(1)
        idm = re.search(r"id=\{\s*id=(\d+)", tb)
        if not idm:
            continue
        if re.search(r"\b(medium_armor|heavy_armor|light_armor|modern_armor|mechanized|medium_tank_destroyer_brigade)\b", tb):
            armor_tpls.add(int(idm.group(1)))

    # 6. Divisions -> per-province total divisions & armored divisions by bloc + major OP1/OP2 data
    prov_div_counts: dict[int, Counter[str]] = defaultdict(Counter)
    prov_arm_counts: dict[int, Counter[str]] = defaultdict(Counter)
    bloc_div_totals: Counter[str] = Counter()
    major_ops: dict[str, dict] = {}

    countries_blk = extract_top_block(txt, "countries")
    # Split countries block cleanly by top-level 1-tab TAG={ headers
    c_starts = list(re.finditer(r"^\t([A-Z0-9]{3})=\{\r?\n", countries_blk, re.M))
    for ci, m in enumerate(c_starts):
        tag = m.group(1)
        c_end = c_starts[ci + 1].start() if ci + 1 < len(c_starts) else len(countries_blk)
        cb = countries_blk[m.end():c_end]
        bloc = bloc_of.get(tag, "neutral")

        # Parse active divisions in this country's units subblock
        units_blk = get_subblock(cb, "units", 2)
        for dm2 in re.finditer(r"^\t\t\tdivision=\{\r?\n(.*?)^\t\t\t\}\r?\n", units_blk, re.M | re.S):
            db = dm2.group(1)
            lm = re.search(r"^\t\t\t\tlocation=(\d+)", db, re.M)
            if not lm:
                continue
            pid = int(lm.group(1))
            prov_div_counts[pid][bloc] += 1
            bloc_div_totals[bloc] += 1
            tm = re.search(r"division_template_id=\{\s*id=(\d+)", db)
            if tm and int(tm.group(1)) in armor_tpls:
                prov_arm_counts[pid][bloc] += 1

        # Extract OP1/OP2 Battle Commander state for Big-5
        if tag in ("GER", "SOV", "ENG", "USA", "ITA"):
            def _gv(vname: str) -> int:
                vm = re.search(rf"\b{vname}=([\d\.]+)", cb)
                return int(float(vm.group(1))) if vm else 0

            p1 = _gv("me_eai_op1_phase")
            t1 = _gv("me_eai_op1_tgt_sid")
            j1 = _gv("me_eai_op1_jump_sid")
            p2 = _gv("me_eai_op2_phase")
            t2 = _gv("me_eai_op2_tgt_sid")
            j2 = _gv("me_eai_op2_jump_sid")
            major_ops[tag] = {
                "p1": p1, "t1": t1, "j1": j1,
                "p2": p2, "t2": t2, "j2": j2,
                "def": "ME_EAI_defensive_posture=" in cb,
                "wint": "ME_EAI_sov_winter_surge=" in cb,
                "ca": "ME_EAI_counter_armor=" in cb,
            }

    return {
        "date": date_str,
        "prov_ctrl": prov_ctrl,
        "prov_bunkers": prov_bunkers,
        "state_riv_forts": state_riv_forts,
        "bloc_of": bloc_of,
        "war_pairs": war_pairs,
        "prov_div_counts": prov_div_counts,
        "prov_arm_counts": prov_arm_counts,
        "bloc_div_totals": bloc_div_totals,
        "major_ops": major_ops,
    }


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    p0: tuple[float, float],
    p1: tuple[float, float],
    color: tuple[int, int, int],
    width: int = 5,
    head_len: float = 18.0,
) -> None:
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 4:
        return
    ux, uy = dx / dist, dy / dist
    # Shorten slightly so arrowhead lands right at the edge of the target hub circle
    end_x = x1 - ux * 10
    end_y = y1 - uy * 10
    shaft_x = end_x - ux * (head_len * 0.7)
    shaft_y = end_y - uy * (head_len * 0.7)

    # Dark outline shadow for crisp readability
    draw.line((x0, y0, shaft_x, shaft_y), fill=(12, 14, 18), width=width + 4)
    draw.line((x0, y0, shaft_x, shaft_y), fill=color, width=width)

    px, py = -uy, ux
    hl = head_len
    hw = head_len * 0.58
    tri = [
        (end_x, end_y),
        (end_x - ux * hl + px * hw, end_y - uy * hl + py * hw),
        (end_x - ux * hl - px * hw, end_y - uy * hl - py * hw),
    ]
    draw.polygon(tri, fill=color, outline=(12, 14, 18))


def render_map(
    assets: StaticMapAssets,
    state: dict,
    out_png: Path,
    view: str = "europe",
    out_size: tuple[int, int] = (2000, 1600),
    subtitle: str = "",
    title_prefix: str = "Magna Europa: Expert AI (Phase 3.1) 战线与兵力态势图",
) -> Image.Image:
    x0, y0, x1, y1 = VIEWS[view]
    cw, ch = x1 - x0, y1 - y0
    ow, oh = out_size
    sx, sy = ow / cw, oh / ch

    def to_px(pt: tuple[float, float]) -> tuple[float, float]:
        return ((pt[0] - x0) * sx, (pt[1] - y0) * sy)

    # 1. Build per-province RGB LUT (Base Territory + Troop Concentration Shading!)
    max_pid = assets.max_pid
    lut = np.zeros((max_pid + 1, 3), dtype=np.uint8)
    tag_id_lut = np.zeros(max_pid + 1, dtype=np.int16)
    bloc_id_lut = np.zeros(max_pid + 1, dtype=np.int8)  # 0=sea/none, 1=axis, 2=comintern, 3=allies, 4=neutral
    tag_to_int: dict[str, int] = {}
    bloc_to_int = {"axis": 1, "comintern": 2, "allies": 3, "neutral": 4}

    prov_ctrl = state["prov_ctrl"]
    bloc_of = state["bloc_of"]
    prov_divs = state["prov_div_counts"]

    for pid in range(1, max_pid + 1):
        pt = assets.ptype.get(pid, "sea")
        if pt == "sea":
            lut[pid] = SEA_COL
            continue
        if pt == "lake":
            lut[pid] = LAKE_COL
            continue
        sid = assets.prov2state.get(pid, 0)
        if sid in assets.state_impassable:
            lut[pid] = IMPASS_COL
            continue

        tag = prov_ctrl.get(pid, "")
        if not tag:
            lut[pid] = IMPASS_COL
            continue

        if tag not in tag_to_int:
            tag_to_int[tag] = len(tag_to_int) + 1
        tag_id_lut[pid] = tag_to_int[tag]
        bloc = bloc_of.get(tag, "neutral")
        bloc_id_lut[pid] = bloc_to_int[bloc]

        base = np.array(TAG_BASE_COLORS.get(tag, DEFAULT_LAND_COL), dtype=np.float32)

        # Troop concentration shading: deepen & saturate provinces where divisions mass!
        dc = prov_divs.get(pid)
        if dc:
            dom_bloc, n_dom = dc.most_common(1)[0]
            n_tot = sum(dc.values())
            # Smooth log scale: 1 div -> 0.28, 3 divs -> 0.52, 6 divs -> 0.72, 12+ divs -> 0.92
            alpha = min(0.92, 0.14 + 0.78 * (math.log2(1.0 + n_tot) / math.log2(1.0 + 14.0)))
            hot = np.array(BLOC_HOT_COLORS[dom_bloc], dtype=np.float32)
            col = base * (1.0 - alpha) + hot * alpha
            lut[pid] = np.clip(col, 0, 255).astype(np.uint8)
        else:
            lut[pid] = base.astype(np.uint8)

    # 2. Crop pmap and map to RGB at cropped resolution -> resize cleanly
    crop_pmap = assets.pmap[y0:y1, x0:x1]
    # Downsample pmap with nearest-neighbor to exact output resolution for razor-sharp borders!
    idx_y = (np.arange(oh) * (ch / oh)).astype(np.int32)
    idx_x = (np.arange(ow) * (cw / ow)).astype(np.int32)
    pmap_out = crop_pmap[np.ix_(idx_y, idx_x)]

    rgb = lut[pmap_out]
    tmap = tag_id_lut[pmap_out]
    bmap = bloc_id_lut[pmap_out]

    # Subtle coastlines & country borders
    sea_mask = bmap == 0
    coast = np.zeros((oh, ow), dtype=bool)
    coast[:-1, :] |= sea_mask[:-1, :] != sea_mask[1:, :]
    coast[:, :-1] |= sea_mask[:, :-1] != sea_mask[:, 1:]
    rgb[coast] = (46, 70, 94)

    border = np.zeros((oh, ow), dtype=bool)
    border[:-1, :] |= (tmap[:-1, :] != tmap[1:, :]) & (tmap[:-1, :] > 0) & (tmap[1:, :] > 0)
    border[:, :-1] |= (tmap[:, :-1] != tmap[:, 1:]) & (tmap[:, :-1] > 0) & (tmap[:, 1:] > 0)
    rgb[border] = (rgb[border].astype(np.int16) * 55 // 100).astype(np.uint8)

    # 3. Active Combat Frontline detection (4-neighbor borders between belligerent tags!)
    # Build fast hostile pair lookup matrix on tag ints
    n_tags = len(tag_to_int) + 1
    hostile = np.zeros((n_tags, n_tags), dtype=bool)
    for a, b in state["war_pairs"]:
        ia = tag_to_int.get(a, 0)
        ib = tag_to_int.get(b, 0)
        if ia > 0 and ib > 0:
            hostile[ia, ib] = True
            hostile[ib, ia] = True

    front = np.zeros((oh, ow), dtype=bool)
    v_diff = (tmap[:-1, :] != tmap[1:, :]) & (tmap[:-1, :] > 0) & (tmap[1:, :] > 0)
    vy, vx = np.nonzero(v_diff)
    if len(vy):
        vh = hostile[tmap[vy, vx], tmap[vy + 1, vx]]
        front[vy[vh], vx[vh]] = True
        front[vy[vh] + 1, vx[vh]] = True

    h_diff = (tmap[:, :-1] != tmap[:, 1:]) & (tmap[:, :-1] > 0) & (tmap[:, 1:] > 0)
    hy, hx = np.nonzero(h_diff)
    if len(hy):
        hh = hostile[tmap[hy, hx], tmap[hy, hx + 1]]
        front[hy[hh], hx[hh]] = True
        front[hy[hh], hx[hh] + 1] = True

    # Dilate frontline cleanly: dark 1-px shadow halo + blazing yellow-white core
    def dilate1(mask: np.ndarray) -> np.ndarray:
        out = mask.copy()
        out[:-1, :] |= mask[1:, :]
        out[1:, :] |= mask[:-1, :]
        out[:, :-1] |= mask[:, 1:]
        out[:, 1:] |= mask[:, :-1]
        return out

    front_core = front
    front_halo = dilate1(front_core)
    rgb[front_halo] = (24, 18, 12)
    rgb[front_core] = (255, 245, 92)

    img = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # 4. Railways Overlay (branch Lv1-2 thin dark-silver, trunk Lv3-5 bold track!)
    # Draw branch rails first, then trunk rails on top
    for pass_trunk in (False, True):
        for lvl, chain in assets.railways:
            is_trunk = lvl >= 3
            if is_trunk != pass_trunk:
                continue
            pts = []
            for pid in chain:
                c = assets.centroids.get(pid)
                if c:
                    px, py = to_px(c)
                    pts.append((px, py))
            if len(pts) < 2:
                continue
            # Quick bounds reject
            if max(p[0] for p in pts) < 0 or min(p[0] for p in pts) > ow or max(p[1] for p in pts) < 0 or min(p[1] for p in pts) > oh:
                continue
            if is_trunk:
                draw.line(pts, fill=(18, 16, 14, 215), width=3)
                draw.line(pts, fill=(215, 205, 185, 190), width=1)
            else:
                draw.line(pts, fill=(32, 28, 26, 145), width=1)

    # 5. Built Honest River Fortifications (bright cyan shields on fortified river-bank provinces)
    bunkers = state["prov_bunkers"]
    riv_fort_states = state["state_riv_forts"]
    for pid in assets.river_provs:
        sid = assets.prov2state.get(pid, 0)
        blv = bunkers.get(pid, 0)
        if blv >= 1 or sid in riv_fort_states:
            c = assets.centroids.get(pid)
            if not c:
                continue
            px, py = to_px(c)
            if 0 <= px <= ow and 0 <= py <= oh:
                r = 3.0 if blv >= 2 else 2.2
                draw.ellipse((px - r, py - r, px + r, py + r), fill=(0, 238, 255, 230), outline=(8, 22, 32, 240))

    # 6. Supply Hubs Overlay (crisp golden-yellow squares with black outline)
    hs = 3.2 if view == "europe" else 4.2
    for pid in assets.supply_hubs:
        c = assets.centroids.get(pid)
        if not c:
            continue
        px, py = to_px(c)
        if 0 <= px <= ow and 0 <= py <= oh:
            draw.rectangle((px - hs, py - hs, px + hs, py + hs), fill=(255, 224, 62, 235), outline=(14, 12, 8, 255))

    # 7. Armored / Mechanized Heavy Cluster Markers (>= 2 armored divs in a province!)
    for pid, ac in state["prov_arm_counts"].items():
        dom_b, n_arm = ac.most_common(1)[0]
        if n_arm < 2:
            continue
        c = assets.centroids.get(pid)
        if not c:
            continue
        px, py = to_px(c)
        if 0 <= px <= ow and 0 <= py <= oh:
            r = min(8.0, 3.6 + n_arm * 0.65)
            col = BLOC_ARMOR_COLORS[dom_b]
            diamond = [(px, py - r), (px + r, py), (px, py + r), (px - r, py)]
            draw.polygon(diamond, fill=(*col, 220), outline=(10, 10, 12, 255))

    # 8. Active Phase 3 Super-Battle Arrows (OP1 & OP2 jump-off -> target railway supply hub)
    f_lbl = load_font(15, bold=True)
    phase_txt = {1: "集结", 2: "强攻", 3: "换轨"}
    active_ops_summary: list[str] = []
    placed_label_boxes: list[tuple[float, float, float, float]] = []

    for tag in ("GER", "SOV", "ENG", "USA", "ITA"):
        op = state["major_ops"].get(tag)
        if not op:
            continue
        col = OP_ARROW_COLORS[tag]
        for slot, p_k, t_k, j_k in (("OP1", "p1", "t1", "j1"), ("OP2", "p2", "t2", "j2")):
            ph = op[p_k]
            tsid = op[t_k]
            jsid = op[j_k]
            if ph not in (1, 2, 3) or tsid <= 0 or jsid <= 0:
                continue
            p_tgt = assets.state_centroids.get(tsid)
            p_jmp = assets.state_centroids.get(jsid)
            if not p_tgt or not p_jmp:
                continue
            pt0, pt1 = to_px(p_jmp), to_px(p_tgt)
            if not (0 <= pt1[0] <= ow and 0 <= pt1[1] <= oh):
                continue
            # Target crosshair ring
            tr = 11 if slot == "OP1" else 8
            draw.ellipse((pt1[0] - tr, pt1[1] - tr, pt1[0] + tr, pt1[1] + tr), outline=(10, 10, 10, 255), width=5)
            draw.ellipse((pt1[0] - tr, pt1[1] - tr, pt1[0] + tr, pt1[1] + tr), outline=(*col, 255), width=3)
            # Operational arrow
            draw_arrow(draw, pt0, pt1, col, width=5 if slot == "OP1" else 3, head_len=17 if slot == "OP1" else 13)

            cname = assets.state_names.get(tsid, str(tsid))
            badge = f"{tag} {slot}[{phase_txt[ph]}]→{cname}"
            if slot == "OP1":
                active_ops_summary.append(badge)
            # Draw clean non-overlapping pill label near target (stack cleanly if multiple allies target same hub)
            lx = min(ow - 240, max(12, pt1[0] + 15))
            ly = min(oh - 36, max(78, pt1[1] - 12))
            tb = draw.textbbox((lx, ly), badge, font=f_lbl)
            bw, bh = tb[2] - tb[0] + 12, tb[3] - tb[1] + 7
            for _ in range(8):
                cand = (lx - 5, ly - 2, lx - 5 + bw, ly - 2 + bh)
                hit = any(
                    not (cand[2] < ob[0] or cand[0] > ob[2] or cand[3] < ob[1] or cand[1] > ob[3])
                    for ob in placed_label_boxes
                )
                if not hit:
                    break
                ly += bh + 3
            tb = draw.textbbox((lx, ly), badge, font=f_lbl)
            box = (tb[0] - 5, tb[1] - 2, tb[2] + 5, tb[3] + 3)
            placed_label_boxes.append(box)
            draw.rectangle(box, fill=(12, 16, 24, 222), outline=(*col, 240), width=1)
            draw.text((lx, ly), badge, fill=(250, 252, 255, 255), font=f_lbl)

    # 9. Key City Reference Labels (subtle high-contrast cartographic markers)
    f_city = load_font(13, bold=False)
    ref_cities = [
        (1650, "柏林"), (571, "巴黎"), (453, "伦敦"), (124, "奥斯陆"),
        (1075, "帕勒莫"), (1241, "的黎波里"), (1889, "开罗"),
        (2045, "敖德萨"), (497, "哈尔科夫"), (2189, "斯摩棱斯克"),
        (2027, "基辅"), (2135, "明斯克"), (508, "华沙"), (250, "梁赞/莫斯科圈"),
        (1749, "鲁尔"), (709, "米兰"), (934, "那不勒斯"), (1109, "突尼斯"),
    ]
    for sid, label in ref_cities:
        c = assets.state_centroids.get(sid)
        if not c:
            continue
        px, py = to_px(c)
        if 40 <= px <= ow - 100 and 82 <= py <= oh - 110:
            draw.text((px + 6, py - 7), label, fill=(10, 12, 16, 220), font=f_city)
            draw.text((px + 5, py - 8), label, fill=(235, 238, 242, 215), font=f_city)

    # 10. Top Tactical HUD Banner
    f_title = load_font(26, bold=True)
    f_sub = load_font(16, bold=False)
    f_leg = load_font(14, bold=False)

    draw.rectangle((0, 0, ow, 70), fill=(12, 17, 25, 232))
    draw.line((0, 70, ow, 70), fill=(90, 115, 145, 220), width=2)

    title_txt = f"{title_prefix}  |  {state['date']}"
    if subtitle:
        title_txt += f"  —  {subtitle}"
    draw.text((18, 10), title_txt, fill=(248, 250, 255, 255), font=f_title)

    dtot = state["bloc_div_totals"]
    ops_line = "   ".join(active_ops_summary[:4]) if active_ops_summary else "和平集结 / 战线休整"
    sub_txt = (
        f"阵营总师数: 轴心 {dtot['axis']} 师 | 共产 {dtot['comintern']} 师 | 同盟 {dtot['allies']} 师"
        f"    ||    活跃超级会战: {ops_line}"
    )
    draw.text((18, 43), sub_txt, fill=(195, 212, 232, 255), font=f_sub)

    # 11. Bottom-Left Compact Legend Box
    lx, ly = 16, oh - 146
    lw, lh = 760, 132
    draw.rectangle((lx, ly, lx + lw, ly + lh), fill=(12, 17, 25, 228), outline=(110, 130, 155, 210), width=1)

    # Row 1: Troop concentration shading
    ry = ly + 10
    draw.text((lx + 12, ry), "重兵密度(颜色越深师数越多):", fill=(225, 232, 240, 255), font=f_leg)
    for ix, (name, col) in enumerate([
        ("轴心重兵", BLOC_HOT_COLORS["axis"]),
        ("苏军重兵", BLOC_HOT_COLORS["comintern"]),
        ("盟军重兵", BLOC_HOT_COLORS["allies"]),
    ]):
        bx = lx + 215 + ix * 120
        draw.rectangle((bx, ry + 2, bx + 16, ry + 16), fill=(*col, 255), outline=(220, 220, 220, 220))
        draw.text((bx + 22, ry), name, fill=(235, 240, 246, 255), font=f_leg)
    # Armored diamond legend
    bx = lx + 575
    draw.polygon([(bx + 7, ry + 2), (bx + 14, ry + 9), (bx + 7, ry + 16), (bx, ry + 9)], fill=(255, 175, 40, 240), outline=(10, 10, 10, 255))
    draw.text((bx + 20, ry), "装甲集群(≥2装甲师)", fill=(255, 215, 120, 255), font=f_leg)

    # Row 2: Frontline, Railways, Hubs, River Forts
    ry = ly + 40
    draw.line((lx + 14, ry + 9, lx + 44, ry + 9), fill=(255, 245, 92, 255), width=3)
    draw.text((lx + 50, ry), "交战前线", fill=(255, 245, 140, 255), font=f_leg)

    draw.line((lx + 135, ry + 9, lx + 170, ry + 9), fill=(18, 16, 14, 255), width=4)
    draw.line((lx + 135, ry + 9, lx + 170, ry + 9), fill=(215, 205, 185, 255), width=1)
    draw.text((lx + 176, ry), "铁路干线(3-5级)/支线", fill=(225, 222, 212, 255), font=f_leg)

    draw.rectangle((lx + 345, ry + 4, lx + 355, ry + 14), fill=(255, 224, 62, 255), outline=(14, 12, 8, 255))
    draw.text((lx + 362, ry), "补给枢纽(674个)", fill=(255, 232, 130, 255), font=f_leg)

    draw.ellipse((lx + 492, ry + 4, lx + 502, ry + 14), fill=(0, 238, 255, 255), outline=(8, 22, 32, 255))
    draw.text((lx + 508, ry), "大河诚实筑垒要塞(7条定向河岸)", fill=(150, 245, 255, 255), font=f_leg)

    # Row 3: Operational Super-Battle Arrows
    ry = ly + 72
    draw.text((lx + 12, ry), "CSR铁路枢纽超级会战箭头 (出发跳板 → 目标枢纽):", fill=(220, 228, 238, 255), font=f_leg)
    ry2 = ly + 98
    for ix, (t, cname) in enumerate([("GER", "德国"), ("SOV", "苏联"), ("ENG", "英国"), ("USA", "美国"), ("ITA", "意大利")]):
        bx = lx + 16 + ix * 128
        col = OP_ARROW_COLORS[t]
        draw.line((bx, ry2 + 9, bx + 26, ry2 + 9), fill=(*col, 255), width=4)
        draw.ellipse((bx + 26, ry2 + 3, bx + 38, ry2 + 15), outline=(*col, 255), width=2)
        draw.text((bx + 44, ry2), f"{t} {cname}", fill=(240, 244, 248, 255), font=f_leg)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="Render tactical war atlas PNGs + GIF from HOI4 saves.")
    ap.add_argument("--out-dir", required=True, help="Output directory for PNGs & GIF")
    ap.add_argument("--saves", nargs="+", required=True, help="Save entries as path|label|slug")
    ap.add_argument("--view", default="europe", choices=list(VIEWS.keys()))
    ap.add_argument("--gif", default=None, help="Optional animated GIF output filename")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = StaticMapAssets()

    out_size = (2000, 1600) if args.view == "europe" else (1840, 1472) if args.view == "east_front" else (1680, 1600)
    gif_frames: list[Image.Image] = []

    for spec in args.saves:
        sp, label, slug = spec.split("|", 2)
        save_path = Path(sp)
        st = parse_save_state(save_path, assets)
        out_png = out_dir / f"{slug}.png"
        img = render_map(assets, st, out_png, view=args.view, out_size=out_size, subtitle=label)
        print(f"[rendered] {out_png.name} ({st['date']}) divs={dict(st['bloc_div_totals'])}", flush=True)
        if args.gif:
            gif_frames.append(img.resize((1200, 960), Image.Resampling.LANCZOS).convert("P", palette=Image.Palette.ADAPTIVE, colors=256))

    if args.gif and gif_frames:
        gif_path = out_dir / args.gif
        gif_frames[0].save(
            gif_path,
            save_all=True,
            append_images=gif_frames[1:],
            duration=1600,
            loop=0,
            optimize=True,
        )
        print(f"[gif] {gif_path} ({len(gif_frames)} frames)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
