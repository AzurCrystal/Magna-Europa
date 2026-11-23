# Expert AI for Magna Europa: Reforged

A standalone high-challenge AI submod purpose-built for **Magna Europa: Reforged** (`HOI4 v1.20.*`).

> **Credits & Tribute**: Inspired by and paying tribute to the architectural philosophy of **Expert AI 5.0** (external scripted state machine, Meta equipment designs, structured templates, division limits, operational sectors, and intel counter-adaptation). Every subsystem here is independently engineered from scratch for Magna Europa's enlarged 2,986-state / 253-region European map, 2–2.5× army frontage, and tight supply economy.

## Design Pillars
- **Zero-Cheat Standard Difficulty**: Challenging through optimized national focus orders, Meta land/air (and key ITA/ENG/USA naval) equipment designs, supply-aware templates, dynamic economy/production loops, and operational theater coordination — no hidden stat cheats on Standard.
- **Land-First, Air-Supported, Key European Navies Upgraded**: Primary engineering focus on land combat & green-air CAS support, with targeted naval upgrades for Italy, Britain, and the United States.
- **Offline Precompiled Map Graph + Zero-`meta_effect` CSR Runtime**: Full 2,986-state & 253-strategic-region CSR adjacency graphs plus precompiled major-river defense lines, 13 operational sectors, and 5 amphibious corridors built offline by `tools/build_eai_map_graph.py`.
- **Lag-Free Staggered State Machine**: Zero hourly loops, zero daily full-map scans, Tier 1/2/3 country filtering, 28-day staggered monthly updates, and `O(1)` flag-driven `ai_strategy` triggers.
- **Clean Takeover**: Automatically sets `MER_EAI_enabled` on startup so base-mod pacing scripts yield cleanly with zero file conflicts.
