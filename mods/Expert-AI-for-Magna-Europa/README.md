# Expert AI for Magna Europa: Reforged (`v0.1.0`)

A standalone high-challenge AI submod purpose-built for **Magna Europa: Reforged** (`HOI4 v1.20.*`, *Operation Postern* + *To The Bitter End*).

> **Credits & Tribute**: Inspired by and paying explicit tribute to the architectural philosophy of **Expert AI 5.0** (external scripted state machine, Meta equipment blueprints, standardized templates, soft division caps, operational fronts, and dynamic intel counter-adaptation). Every subsystem, map graph, and strategy here is independently engineered from scratch for Magna Europa's enlarged **2,986-state / 253-region** European map, 2–2.5× army frontage, and tight supply economy — **no separate Expert AI 5.0 subscription required**.

---

## Core Design Pillars

- **Zero-Cheat Standard Difficulty (Default)**: Challenging purely through deep mastery of vanilla HOI4 mechanics (NSB + BBA + AAT + LaR + Götterdämmerung) — **0% hidden AI stat cheats** on Standard. Opt-in `Challenging`, `Brutal`, `Custom`, and targeted `Axis / USSR / Allies / Anti-Player` boosts are available both in Custom Game Rules and live via the in-game Control Panel.
- **Historical Four-Year Plan Germany ("欧陆风云")**:
  - Historical `Four-Year Plan` + `MEFO Bills` / `Economy of Conquest` discipline (price controls + zero-cheat all-France gold seizure before Vichy partition).
  - `Hess → Goebbels → Todt → [Inner Circle Free Swap to Speer]` double-dip stacking Todt T1 (`Org Todt`) with Speer (`Plenipotentiary of Armaments`) and `Totaler Krieg` (hard-bans Göring & Bormann).
  - Pure 2-cavalry suppression templates (zero support-equipment rounding bleed), deterministic MIOs, 100% FRA & SOV Collaboration Governments, 54-MIL rifle crisis floor, heavy 30w medium Panzer divisions, and Atlantic Wall garrisoning.
- **Red Storm Rising Soviet Union ("钢铁洪流")**:
  - Authentic Summer 1941 Barbarossa shock (zero pre-war psychic Dnieper forts!), followed by an overwhelming 1942–43 Urals + Stavka counter-offensive.
  - **Zero Army/Air Random Great Purge**: Mathematically scheduled Centre focus tree + zero-cheat weekly paranoia guard (prioritizes `Forge Production Reports`, falls back to `Inspection in the Navy` when needed) — cuts random purges from `15 → 3` with 0 Army/Air debuffs and 0 dissolved aircraft bureaus.
  - Early Trotsky assassination (`Behead the Snake` in Dec 1937 + Canada/Norway operative targeting), Gosproyektstroy foreign experts, early Agitprop, full Red Air Force reform (`Transpolar Flights → Pilot Training`), and proactive synthetic rubber refineries curing Magna Europa's rubber crunch (`110 → 0` resource-starved MILs).
  - Day-1 wartime `Desperate Measures → Move Industry to the Urals → Defense of Moscow → Lessons of War` + Stavka training-queue strategic reserves.
- **Offline CSR Map Graph + Super-Battle Commander**:
  - Precompiled 2,986-state & 253-region CSR adjacency graphs (`tools/build_eai_map_graph.py`), zero `meta_effect`, 13 European operational sectors, 7 wartime-only oriented major-river fortification lines, and 5 amphibious corridors (Torch-before-Overlord, Italy Greece/Med synergy).
- **Lag-Free Staggered State Machine**:
  - Zero hourly loops, zero daily full-map scans, Tier 1/2/3 country funnel, 28-day staggered monthly scheduler, and `O(1)` flag-driven `ai_strategy` blocks with configurable soft division caps.

---

## Difficulty Tiers & Custom Game Rules

Configure before game start under **Custom Game Rules → Expert AI for Magna Europa**, or switch live anytime from the **Expert AI: Control Panel** decision category (hidden cleanly on demand; human-only):

| Tier | AI Modifiers | Recommended For |
|---|---|---|
| **Standard (Default)** | **None (0% stat cheats)** | Authentic, fair high-challenge gameplay |
| **Challenging** | `+15%` Factory Output & Construction, `+10%` Research, `-10%` Supply Use, `+15%` Org Regain | Experienced players seeking heavier industrial pressure |
| **Brutal** | `+30%` Factory Output, `+25%` Construction, `+20%` Research, `-20%` Supply, `+10%` Combat, `+25%` Manpower | Veteran Expert AI 5.0 players |
| **Targeted Bloc Boost** | `+20%` Output, `+15%` Construction, `-10%` Supply, `+5%` Combat for Axis / USSR / Allies / Anti-Player | Buffing only your chosen rival bloc |

---

## Installation & Compatibility

1. Enable **Magna Europa: Reforged** and **Expert AI for Magna Europa: Reforged** in the Paradox Launcher (place Expert AI below Magna Europa in load order).
2. On startup, the submod automatically sets `MER_EAI_enabled` so base-mod pacing scripts yield cleanly with zero file conflicts — safe to enable/disable between campaigns.
3. Full **English** and **Simplified Chinese (`简体中文`)** localization included.
