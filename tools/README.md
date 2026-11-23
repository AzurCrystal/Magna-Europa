# tools/

Mod development utilities — **not shipped** to the workshop (release packaging only copies game content dirs).

## Map integrity

| Script | Checks |
|---|---|
| `prov_adjacency.py` | Fresh adjacency from `provinces.bmp` + `adjacencies.csv`: pixel contacts, `Through`, and named crossing rules; excludes impassable and unrestricted border rules. Explicit CLI output: `python tools/prov_adjacency.py map _ctx/prov_adj_fresh.json` (output path required, relative to repo root). |
| `railway_audit.py` | `railways.txt`/`supply_nodes.txt`: chain contiguity, sea-province hops (CTD source), dead prov ids, graph components. Rebuilds current adjacency in memory; never reads a cached JSON. |
| `buildings_audit.py` | `buildings.txt` pixels land on the declared state (naval on sea = OK) |

Run from the repo root; set `ME_MOD` to `mods/Magna-Europa` when selecting the payload explicitly. Regenerate exported adjacency after any bitmap or adjacency-rule edit; it is a research artifact, not an audit cache.

## Script / loc integrity

| Script | Checks |
|---|---|
| `pdx_parse.py` | Clausewitz syntax — run on any `.txt` after editing |
| `focus_ref_audit.py` | every `focus = <id>` in plans/decisions/events resolves in `national_focus/` |
| `event_ref_audit.py` | every `country_event`/`news_event`/`calls_event`/`trigger_event` id resolves in `events/` |
| `decision_audit.py` | decisions with `days_remove` but no effect/modifier (likely dead); visible-only selectors are legal, not flagged |
| `vp_loc_audit.py` | every mod VP has a `VICTORY_POINTS_<id>` loc key; flags name collisions |
| `loc_audit.py` | cross-language key coverage. Use `--vanilla <game dir>` to diff against vanilla english and count only **mod-added keys** (the meaningful metric: zh=100%, ru=45.9% — ru gap is mod-native, not a regression). |
| `sr_audit.py` | every province in exactly one SR; every state's provinces ⊆ one SR |
| `offmap_refs.py` | heuristic scan for state-ids/continents not present on the mod map |
| `scripted_ref_audit.py` | `name = yes` invocations resolve to defs in `scripted_triggers/`/`scripted_effects/`/`scripted_localisation/`; also lists never-invoked defs (dead weight) |
| `tag_ref_audit.py` | `tag`/`original_tag`/`country_exists`/`is_*` TAG refs resolve (merges `country_tag_aliases` + `common/countries` cosmetic tags, D\d+ dynamic tags, scope names). `--vanilla` merges vanilla definitions. |
| `char_idea_audit.py` | `*_character`/`character = X` refs resolve in `common/characters/`; `add_idea(s)`/`remove_idea(s)`/`swap_ideas`/`idea_token`/`advisor token` refs resolve in `common/ideas/` ∪ characters (vanilla hires character advisors via `add_ideas`). Depth-tracked defs (chars depth 1, ideas depth 1-2); unicode ids; skips scopes/`var:`/`[var]` subs/`_random_`. Dead advisor tokens listed separately. |
| `oob_audit.py` | `history/units/*.txt`: every `division_template = "X"` resolves to a `division_template = { name = "X" }` in the same file |
| `gfx_music_audit.py` | `GFX_x` tokens resolve to `interface/*.gfx` names; `play_song` refs resolve to `music/` defs |
| `modifier_audit.py` | `add_modifier`/`add_opinion_modifier`/`has_opinion_modifier` targets exist in `common/modifiers/`/`dynamic_modifiers/`/`opinion_modifiers/` |
| `unit_ref_audit.py` | unit/equipment/name-group refs: `regiments`/`support`/`definition`/`type`/`equipment`/`modules`/`upgrades`/`air_wings`/`name_group`/`division_names_group`/`need_equipment`/`enable_*` resolve to common/units defs. Vanilla merge ON by default (auto-detects standard install; `--no-vanilla` opts out); dead refs present verbatim in vanilla = `[inherited]`, documented not counted. |
| `tech_trait_audit.py` | tech ids: `set_technology`/`has_tech`/`technology`/`leads_to_tech`; traits: `has_trait`/`add_*_trait`/`remove_*_trait`/`traits = {}` under leader scopes; categories: `add_tech_bonus`/`research_bonus`. Same vanilla-merge + inherited convention — the mod ships no `common/country_leader`/`technology_tags`, so mod-only runs over-report. |

## Mapping / research

| Script | Output |
|---|---|
| `build_state_map.py` | Canonical vanilla↔ME identity tables for map content (AGENTS.md §2): `docs/states/{v2m_states,m2v_states,regions,vps,mapping_evidence}.tsv` + `province_pairs.json` (schema: `docs/states/README.md`). Run `audit_state_mapping.py` afterward for `mapping_audit.tsv` and `mapping_audit_summary.json`. Repo root derived from the script path; `HOI4_DIR=<game dir>` overrides vanilla detection. |
| `build_state_edges.py` | Generates `docs/states/state_edges.tsv`, the long-table vanilla↔ME one-to-many/many-to-many relation **evidence ledger**. Reuses the candidate-independent coast-affine raster census, canonical primary edges, migration-comment sets, and top-level evidence candidates; every row carries `relation_scope` — `compatibility`/`asserted` edges may drive relation expansion, `observation` edges are stored for explicit query only (raster overlap is sampled coverage, never a complete-subset proof). Deterministic and fail-closed. Run after `build_state_map.py`. |
| `build_state_edges_1936.py` | Generates `docs/states/state_edges_1936.tsv` plus per-owner TSV/JSON summaries. Resolves state owners at 1936.1.1, keeps all raster/ledger candidates, and marks `same_owner` as within-country candidates while retaining cross-owner rows only as diagnostics. Its `me_owner_*`/`v_owner_*` same-owner census columns and its `compatibility`/`asserted`-scope edges supply the confirmation layer's owner-scoped metrics and owner relation family. It is an owner-constrained fitting ledger, not a complete-subset proof. |
| `audit_state_masks_1936.py` | Generates `docs/states/state_mask_audit_1936.tsv` + `..._entities.tsv` + `..._summary.json` (`state_mask_audit_1936_summary/v1`): a read-only sampled-mask audit comparing binary state masks under the same frozen `initial="bbox"` coast affine (masks never participate in the fit). Forward ME-native and reverse vanilla-native censuses stay separate columns — never merged. `pair_class`/`entity_class` use pixel counts only (`MIN_AREA` 64, `CONTAINMENT_SHARE` 0.90, `PARTIAL_SHARE` 0.20); 1936 owners and joined `state_edges_1936.tsv` context are parallel fields, never gates; `sampled_masks`/`withheld` claims only, never a complete-subset proof. Nothing feeds canonical tables or `state_relations.py`. Optional `--render-dir DIR --render-owner TAG` (repeatable, requires `--render-dir`) writes `<TAG>_owner_overlay_me_frame.png` review aids — green overlap, red ME-only, blue vanilla-only, black neither — never into `docs/states/`. |
| `state_relations.py` | Stdlib-only read-only loader/query API for `state_edges.tsv`: `expand`, `ancestors`, `contains`, `intersect`, `coverage`, primary views, and contract validation. Queries default to asserted scopes (`compatibility`+`asserted`); `observation` edges need an explicit `scopes=` argument or the `*_observed`/`*_all` wrappers, and excluded synthetic Map Border edges never enter expansion by default. |
| `state_relation_registration.py` + `state_relation_census.py` | Library pair behind `build_state_relation_map.py`: topology-only registration (rasterised `history/states` + 1936.1.1 owners + country/coast/lake boundary classes; global bbox coast affine + mutual-NN ICP, then per-owner-component bounded local refits against the nearest same-owner vanilla component, optional river/lake natural correction, statuses `registered`/`global_fallback`/`unmatched_component`/`excluded_inset` — no VP/name/comment/canonical input) and the candidate-independent sampled census (each pixel classified once `confident`/`uncertain`/`water`/`unassigned`/`outside` against the `--border-radius` vanilla border band; explicit denominators; deterministic `one_to_*`/`many_to_*`/`uncertain` classes; full retained k:n matrix, never a complete-subset proof). |
| `audit_state_mapping.py` | Reads `mapping_evidence.tsv` and emits conservative `mapping_audit.tsv` + JSON summary; triage only, never edits canonical mappings. |
| `state_geography_evidence.py` | Optional candidate-independent geography evidence: bbox coast fit + local river/lake translation with province-centroid area-weighted natural votes, plus separate coast-affine native-pixel `overlap_*` diagnostics → `docs/states/geography_evidence.tsv` (unchanged natural/raw prefix, appended fixed columns, deterministic rerun). `--vanilla`/`--mod`/`--output`; optional `--me-id` filters rows without narrowing the shared full-raster census. |
| `auto_repair_state_mapping.py` | `--propose` emits conservative repair/review TSV + hash-manifest JSON; optional `--geography` (default `geography_evidence.tsv` beside `--evidence`) consumes candidate-independent raw-geography and accepted `overlap` diagnostics; overlap is independent only with comment/exact-name identity. Optional hand-authored `--approvals PATH` promotes selected `review` rows. `--apply` consumes those inspected files and adds only safe, new `[auto-repair]` or approved `[human-approved]` entries to the generator's `MANUAL_ME2V`. Never creates the approvals file, edits canonical TSVs, or edits the review queue. |
| `id_maps.py` | `tools/out/states_map.json` + `provinces_map.json` — mod↔vanilla id mapping (primary: same state id; secondary: name match). VP match is id-in-state then loc-name — coverage ceiling is real, not a bug: mod has ~2.5× vanilla's VP count. |
| `build_focus_state_refs.py` | `docs/states/{focus_state_refs,focus_state_map}.tsv` — every state reference in the vanilla + mod `common/national_focus/` corpora, resolved against `v2m`/`m2v` primary compatibility views. When `docs/states/state_edges.tsv` (relation evidence ledger) is present it is consumed too: only `compatibility`/`asserted`-scope secondary edges count as relations — comments citing one resolve `ok|comment_secondary_edge` instead of colliding, while `observation`-scope raster/candidate edges never suppress collisions. `edge_ancestors`/`edge_members` report asserted-scope relations; `observed_ancestors`/`observed_members` report heuristic-only candidates. `covers=full` is primary-family coverage only. |
| `build_state_relation_map.py` | `docs/states/{state_relation_edges,state_relation_identity,state_relation_substitution,state_relation_views,state_relation_v2m}.tsv` + `state_relation_registration.json` (`state_relation_registration/v1`) + `state_relation_summary.json` (`state_relation_summary/v1`) — the separate relation-map layer: three non-conflated meanings (`area_parent` sampled topology overlap, `identity` manual/comment/name semantic candidacy, `substitution` script/focus compatibility) plus convenience derived views (unique high-confidence area-parent → unique explicit identity → unique substitution; ambiguity → `review`/`unmapped`). Semantic inputs only: `mapping_comment_edges.tsv`, `focus_state_refs.tsv`, parsed `MANUAL`/`MANUAL_ME2V`. `claim`/`complete_subset`/`component_status`/`diagnostic_flags` on every row; `--no-natural` is a diagnostic ablation, not final acceptance. Run after `build_state_map.py` + `build_focus_state_refs.py`, before the confirmation layer. |
| `build_state_mapping_confirmation.py` | `docs/states/{state_mapping_confirmation,state_mapping_review_queue_95,state_mapping_confirmation_refs}.tsv` + `state_mapping_confirmation_summary.json` (schema `state_mapping_confirmation_summary/v2`) — read-only owner-scoped scoring layer over the canonical tables, `state_edges_1936.tsv`, the mask audit, comment/manual identity evidence, and project state references. `CONFIRMATION_THRESHOLD=0.95` is a conservative pair-level coverage gate, not a probability and never a complete-subset proof: `confidence_score=min(evidence_cap, me_owner_share, owner_family_reverse_share)` (missing metrics → 0). The owner relation family is scope-only — the same-owner `compatibility`/`asserted`-scope rows of `state_edges_1936.tsv`; `observation` edges never join it and a canonical row with a non-family scope is malformed input. `confirmed_95` requires: canonical pair, non-synthetic, `same_owner`, explicit identity cap 1.0, usable geometry, `me_owner_share` and `owner_family_reverse_share` ≥ 0.95 over positive owner denominators, no identity-risk audit reason (`context_only`/`clear_correction`/`comment(member)` are non-blocking context), no comment conflict, no synthetic member in either family. Noncanonical rows can never confirm; old `me_share`/`family_*` metrics stay as diagnostics and confirmed rows may still carry their reason annotations (diagnostics never gate). Summary `inputs` hash every input incl. `state_edges_1936.tsv` with repo-relative paths. This is the current per-pair gate layer — a broader owner-aware relation-map layer may supersede it. Pure-function regression suite: `tools/test_build_state_mapping_confirmation.py`. |

| `render_state_overlay.py` + `visual_warp.py` | Visual review overlays: coastline/natural/VP baselines plus bounded local stretch and optional candidate-conditioned neighbour-boundary aid (`--warp visual`). |
| `build_state_mapping_review.py` + `state_mapping_review.html` | Offline two-pane vanilla/ME hover review; Exact member is default, Full family is canonical-only. Generates coast-registered vanilla and native-ME PNG assets under `docs/states/state_mapping_review/`. Optional `geography_evidence.tsv` supplies diagnostic-only natural and raster-overlap facts on ME hover, never identity/fill changes; malformed geography is warned and ignored as a whole. Optional `--confirmation`/`--confirmation-summary` (defaults beside the canonical tables) embed the 95% confirmation overlay — **stale-safe**: a present summary must be schema `state_mapping_confirmation_summary/v2` and every `inputs` record is re-hashed against the current `docs/states` files at build time, so a stale or wrong-schema summary discards the whole overlay with a warning (an absent summary still embeds, flagged TSV-only/unverified). Dashboard counts, filterable review-queue table, click-to-pin ME↔Vanilla pairs on all three canvases, bidirectional candidate lists, owner-scoped shares + owner-family facts with canonical-family metrics as labeled diagnostics, prev/next navigation, `#me=&vanilla=` deep links, and localStorage-only `accept`/`reject`/`defer`/`candidate` decisions with JSON export — a human artifact that never writes back to canonical. Malformed confirmation input is warned and dropped as a whole. The comparison canvas defaults to the geometry-first, candidate-independent **Partition overlay**: continuous ME boundaries plus dashed transformed Vanilla boundaries and per-ME spatial overlap/coverage facts. **Canonical crosswalk** is an explicit secondary mode; Hovered exact pair remains available. All comparison output is registration/geometry aid, not identity proof. Regression suite: `python tools/test_state_mapping_review.py` (Node interaction checks when available). |

### Conservative state-mapping repair workflow

Start from an existing, current canonical/evidence/audit snapshot (build and
audit first if needed). The full regeneration chain — canonical tables, both
edge ledgers, mask audit, evidence audit, confirmation layer, review page —
is documented in `docs/states/README.md` ("Regenerate"); the confirmation
step consumes `state_edges_1936.tsv` directly, so the two 1936 builders must
run before it and the order is not optional. Run **propose → inspect →
apply → build → audit → review page**:

```bash
python tools/state_geography_evidence.py          # optional natural evidence + raster diagnostics
python tools/auto_repair_state_mapping.py --propose
# Inspect docs/states/auto_repair_proposals.tsv and auto_repair_summary.json.
python tools/auto_repair_state_mapping.py --apply
python tools/build_state_map.py
python tools/build_state_edges.py
python tools/build_state_edges_1936.py
python tools/audit_state_masks_1936.py
python tools/audit_state_mapping.py
python tools/build_focus_state_refs.py
python tools/build_state_mapping_confirmation.py
python tools/build_state_mapping_review.py
python tools/auto_repair_state_mapping.py --propose  # final review list on new snapshot
```

Without `--approvals`, this is the complete workflow: only deterministic
`auto_repair` rows are applied and the proposal TSV is left untouched. A human may
additionally promote individual `review` rows by hand-authoring
`docs/states/auto_repair_approvals.tsv` after inspecting the proposals. The tool
never creates or overwrites that file; absent and header-only files are both valid
and mean "no approvals", and the proposal TSV must never be edited to signal one.
The fixed columns are `me_id, approved_ancestor, review_note`, with a mandatory
single-line printable note (no `#`, no control characters).

```bash
# Human writes docs/states/auto_repair_approvals.tsv after reviewing the proposals.
python tools/auto_repair_state_mapping.py --propose \
    --approvals docs/states/auto_repair_approvals.tsv
# Inspect the unchanged proposal rows and the summary's approval hash/IDs, then:
python tools/auto_repair_state_mapping.py --apply \
    --approvals docs/states/auto_repair_approvals.tsv
```

Any `review` row may be approved, including ones whose `proposed_ancestor` is
empty or already equals `current_ancestor`. `approved_ancestor` must be a real
vanilla ID in `v2m_states.tsv` and must differ from `current_ancestor`; equal to
the proposal's ancestor is a normal approval, different (including from empty) is
recorded as a `manual_candidate_override` whose mandatory note is the reason.
Duplicate/unknown ME IDs, no-op ancestors, `no_action`/`already_manual`/
`auto_repair` rows, and states already covered by `MANUAL_ME2V`, `MANUAL` groups,
or known manual corrections are rejected. Because approvals do not change proposal
decisions, rerun `--propose --approvals` after every approval edit: the summary
hashes the approvals path, content, and presence plus approved IDs/override
reasons, and `--apply` refuses a proposal older than the current approvals file.

The optional producer writes `docs/states/geography_evidence.tsv`; use
`--output`/`--vanilla`/`--mod` for isolated trees. Its natural/raw-geography prefix
and semantics are unchanged: at least 10 kept natural pairs, residual p50 ≤ 6
and p95 ≤ 12 vanilla px, top share ≥ 0.85, margin ≥ 0.60, sampled state-raster area
≥ 0.95 are required before a raw-geography candidate is emitted.

Appended optional `overlap_*` fields separately census every ME-native-frame
pixel under the single global bbox ME→vanilla coast affine, never a natural
correction or candidate-conditioned warp. Land-state rasters exclude sea, lake,
and dummy province 0 using `definition.csv`; nearest sampling uses `np.rint`
with geometric and rounded bounds. Best/runner shares, margin, and assigned
coverage use the **entire ME state land-pixel area**, including out-of-frame or
vanilla-water misses; there is no covered-pixel renormalisation. Unregistered
share is one minus assigned coverage. Reverse containment divides the best
intersection by that vanilla state's sampled area across the **full ME frame**,
including ME-empty pixels, and is diagnostic only. Tied candidates choose the
lower positive ID; output selection with `--me-id` retains this shared census.

Overlap acceptance requires **64 pixels**, **0.90 assigned share**, **0.80 best
share**, **0.25 margin**, and agreement across all **4-connected** components.
The largest component is chosen by area then scan-label order, and its own best
is retained separately from the aggregate best. Every component must choose that
same candidate and meet coverage/share/margin gates; a small agreeing island does
not need 64 pixels itself, but weak, unregistered, or dissenting detached islands
withhold the whole ancestor. Island-only states meet the same whole-state gates.
Best/runner and component diagnostics remain on withheld rows. Accepted rows use
only the separate safe `overlap_marker=coast_bbox_affine_raster_overlap`, never
`raw_geography_marker`. These are diagnostic-only fields: they do not change
canonical mappings, add a repair evidence family, or alter natural/raw fields.
An unavailable/failed overlap setup leaves the appended fields empty without
changing natural results. See `docs/states/README.md` for denominator details.

Only independent agreeing evidence can qualify: pixel share ≥ 0.80 and margin
≥ 0.25 plus comment/exact-name/raw-geography evidence, or a direct VP/exact-name
identity plus another independent non-pixel family. Raw geography must agree with
a strong pixel, comment, or exact-name identity; geography alone or geography+VP
stays in `review`. Pixel+VP alone shares a positioning source and cannot qualify.
Name collisions, cross-state VP evidence, comment/pixel conflicts, core-disjoint
flags, and ambiguous/tied candidates stay in `review`; manual decisions stay
visible as `already_manual`. No candidate is inferred from reverse-map/region
symmetry or a conditioned visual boundary.

`--geography` defaults to `geography_evidence.tsv` beside the selected
`--evidence`. If that file is absent, proposal decisions are unchanged and the
summary records the optional input as absent. When present, only
`raw_geography_ancestor` and `raw_geography_marker` are merged into the in-memory
evidence; no existing field is overwritten. Fixed columns, unique ME IDs present
in the evidence table, nonnegative numeric counts, 0..1 shares/margin with a
consistent best/runner pair, and a concrete candidate-independent coast/river/lake
local-correction marker are required; generic approval, visual, boundary, region,
symmetry, VP, and city provenance are rejected.

Apply requires the original unedited proposal TSV and JSON manifest; source,
input, proposal, optional-geography, and approvals hashes/presence must still match
and decisions are recomputed before editing. It refuses
duplicate/malformed dictionaries, invalid approval rows, and existing manual keys,
including `MANUAL` members. The applied set is the deterministic `auto_repair` rows
plus approved `review` rows, unioned atomically in numeric ID order; approval
entries never appear in the proposal TSV. New IDs are processed numerically into
existing gaps without reordering the legacy dictionary's historical sections. The
existing indentation/comment alignment is reused; automatic entries carry
`[auto-repair]`, approved entries `[human-approved]` with the current, proposed, and
approved IDs plus the recorded note/evidence. An edited source gets an exact
`.py.bak` backup and atomic replacement; no edits means no backup, and an existing
backup is never overwritten.
Archive/move that backup before a later repair batch.

The JSON reports counts, applied/approved/review/manual IDs, override reasons, and
skipped reasons. Final human review uses the proposal TSV, not an automatically
edited review queue.
See `docs/states/README.md` for schema, optional raw-geography markers, path
overrides, and stale-input recovery. The repair tool itself needs no game install
or NumPy/SciPy/Pillow; the optional geography producer and subsequent generators
retain their own requirements.


## Legacy

| Script | Purpose |
|---|---|
| `unAccenternator.py` | strips diacritics from `history/states` filenames (one-off cleanup) |

## Usage

All audits auto-resolve the mod payload under `mods/<Name>/` via `tools/_modroot.py` (`ME_MOD=<path>` overrides). Run them from anywhere — repo root is the convention:

```bash
python tools/railway_audit.py     # after any map/ edit — expect 0/0/0
python tools/vp_loc_audit.py      # after loc or state-file edits
python tools/focus_ref_audit.py   # after focus tree edits
python tools/id_maps.py           # regenerate mapping tables (untracked output)
python tools/loc_audit.py --vanilla "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
```
