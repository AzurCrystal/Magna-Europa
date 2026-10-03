# AGENTS.md — Magna Europa: Reforged

Guidance for AI agents working on this HOI4 mod. Read before touching anything.


## Non-negotiable rules learned the hard way

### 1. Vanilla parity includes PDX bugs
If a file is byte-identical to vanilla and produces the same log error in vanilla, **leave it**. Do not "fix" PDX bugs. Known offenders kept deliberately:
- `common/dynamic_modifiers/bba_dynamic_modifiers.txt` — invalid ROOT scope
- `common/on_actions/15_mun_on_actions.txt` — `has_border_war_between` noise (states 593↔562 share a border; the trigger logs when no war exists)
- `Italy.txt:58` `add_country_leader_trait`, `transfer_advisors_to_vichy` None-scope

### 2. ID remap source-of-truth hierarchy
Mod state IDs ≠ vanilla. Province IDs share the number space but map differently. When resolving names/positions, in order of trust:
1. `victory_points = { id n }` block inside `history/states/<sid>-Name.txt` (authoritative)
2. Regional VP loc files (`balkan_victory_points_l_english.yml`, `russia_…`, `easterneurope_…`, `centraleurope_…`)
3. State-name match
4. Raw coordinates

`localisation/victory_points_l_english.yml` is rebuilt to only contain real mod VPs without regional coverage (state-name fallback). Regional VP files are authoritative; keep it that way — stale vanilla IDs must never re-enter.

### 3. Map data invariants
- `map/railways.txt` chains must be **consecutive land-adjacent** provinces. A sea/lake hop renders but **crashes supply mapmode** (renderer walks the chain onto water → CTD). Verify every hop against adjacency built from the *current* `provinces.bmp` — prebuilt `_ctx/prov_adj.json` can be stale after bmp edits.
- Province adjacency: 4-neighbor pixel contacts in `provinces.bmp`, plus straits from `map/adjacencies.csv`. Diagonal-only contact = "invalid X crossing" engine error; repaint one border pixel to fix.
- `map/supply_nodes.txt`: every node must be a land province, on-rail preferred. Off-rail nodes draw straight-line supply routes (visual starburst — cosmetic, not a bug).
- `map/buildings.txt` coordinates: `x` = bmp column, `z` = `3072 - bmp_row`. "not over the land" errors mean the point sits in water — relocate to the centroid of a land province in the same state. No trailing newline at EOF (phantom-line parse error).

### 4. game.log focus lines are not a census
`"Focus X"` lines only appear for focuses with explicit `log=` in `completion_reward`. A country showing few focus lines can still be progressing normally. Verify via observer mode, not log counts.

### 5. Non-historical AI explains "anomalies"
RSI in 1939, Soviet Poland, Yugoslavia splits etc. are legal non-historical outcomes — check game rules before diagnosing.

### 6. No edits while the game is running
While any `hoi4.exe` process is alive, do not modify any file under the test directory (`_ctx/`, snapshots, or anything the running game may re-read). Save games absorb mid-flight edits silently; diagnosing them later is impossible.


## Workflow conventions

- Conventional commits, English messages, `--no-verify`, push `develop`.
- Rebase hygiene: after any squash/drop, `git diff --quiet <pre-rebase-backup> develop` MUST exit 0 (tree-identical).
- Releases: bump `version=` in `descriptor.mod` + `Magna Europa.mod`, tag `vX.Y.Z`, GitHub release notes in English.

## Deliberate balance choices (not bugs)

- `EDO_defines.lua`: `CORPS_COMMANDER_DIVISIONS_CAP=48`, `FIELD_MARSHAL_DIVISIONS_CAP=72` (vanilla 24/24) — explains general-heavy screenshots.
- `EDO_defines.lua`: `DEPLOYED_UNIT_MANPOWER_RATIO_TO_BUFFER_PEACETIME=0.5`, `MAX_AVAILABLE_MANPOWER_RATIO_TO_BUFFER_PEACETIME=0.5` (vanilla 0.1/0.2). Commit `22:05` fix: at vanilla values GER kept only ~37k of ~750k deployed manpower in-training ≈ 4-8 lines, starving the 2× wanted-divisions target — 0.5 lets the pipeline fill. Deliberate, keep.
- ~50 spawnable tags lack `history/countries` files — intentional, they're spawn-only.
- `map/unitstacks.txt` imperfections — visual-only, low priority.
- Volunteer scale is 2x: `NDiplomacy.VOLUNTEERS_PER_*` 0.05→0.10, `MAX_VOLUNTEER_ARMY_FRACTION` 0.25→0.50, all positive `send_volunteer_size` in `common/ideas/` doubled — half-cost battalions make a 1-div volunteer force half the manpower of vanilla; SCW is the only pre-war volunteer venue left.
- Supply is deliberately scarce and rail-centric — **not a bug**. `common/defines/EDO_defines.lua`: `CAPITAL_SUPPLY_BASE` 1.6 (vanilla 5.0), `CAPITAL_INITIAL_SUPPLY_FLOW` 3.0 (5.0), `NODE_INITIAL_SUPPLY_FLOW` 2.0 (2.8), `NUM_RAILWAYS_TRAIN_FACTOR=0.03` ("Distance should matter much more now"), `TRUCK_ATTRITION=0.01`. `map/supply_nodes.txt` is the mod's own redraw (674 nodes, unchanged v0.99.3→v0.99.6), giving ~0.23 nodes/state across the 2400+ European states vs vanilla's 0.67 — sparsity plus low per-node flow plus heavy distance penalty is the intended logistics model, with hubs/rail/infra as the backbone. Workshop feedback of the form "add more supply hubs" is a consequence of this design; do not raise node density or flow without an explicit maintainer decision.
