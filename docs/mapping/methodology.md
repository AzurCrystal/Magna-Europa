[← Back to Overview](README.md) · [简体中文主页](README_zh-CN.md) · [CLI Guide](guide.md) · [Interactive Atlas](atlas.md)

# Technical Architecture, Benchmarks & Layer Schemas

This document details the mathematical registration pipeline ([`tools/eu_map_registration.py`](../../tools/eu_map_registration.py)), the 5 canonical crosswalk layers ([`docs/states/eu_mapping/`](../states/eu_mapping/)), and all quantitative accuracy benchmarks ([`registration_report.json`](../states/eu_mapping/registration_report.json), [`mapping_summary.json`](../states/eu_mapping/mapping_summary.json), [`region_mapping_summary.json`](../states/eu_mapping/region_mapping_summary.json)).

---

## 1. Zero-Circularity Principle

Many cross-map heuristics inadvertently bake their own guesses back into the geometry (for instance, fitting warps to Victory Point tables derived from old IDs, or tuning boundaries to agree with `# was vanilla N` migration comments). In our pipeline, **zero semantic or textual hints enter any coordinate transform**:

- **Allowed warp inputs**: binary landmass topology, 1936 country borders, triple junctions, geometry-validated cores, `rivers.bmp` skeletons, and topography (`heightmap`).
- **Held-out validation signals**: all **39 national capitals**, **1936–1939 historical event territories** (Rhineland DMZ, Anschluss, Sudetenland, Slovakia, Albania), **Victory Points**, and **state names** are kept strictly outside the geometric solver and used solely to audit alignment and break true border-straddler ties in Layers 3–4.

---

## 2. Three-Stage Registration Pipeline

### Stage 1 — Physical Landmass Decoupling

Because Magna Europa shifts Great Britain and Iceland slightly relative to continental Europe compared to Vanilla's projection, a single global affine introduces ~10–40 px systemic error on offshore islands. Fitting separate 2D affines over three independent landmasses solves this cleanly (`~3.14×` uniform upscale):

| Landmass | Area (`ME px`) | ME → Vanilla Shift vs Mainland | Alignment Residual (`vanilla px`) |
| --- | ---: | --- | --- |
| **Mainland Eurasia + North Africa** | `6,749,928` | Reference frame (`scale ≈ 0.3184 / 0.3201`) | `58` common triple junctions: initial `p50 = 1.837` → refined `p50 = 1.658`, `p90 = 4.319` |
| **North Atlantic / British Isles** | `143,680` | `(-9.10, -11.16)` px | Coastline `p50 = 0.508`, `p90 = 1.081` |
| **Iceland** | `75,724` | `(-0.12, -39.87)` px | Coastline `p50 = 0.517`, `p90 = 1.417` |

### Stage 2 — Auto-Validated Sub-National Core Priors

Before refining the local displacement field, every shared sub-national releasable `add_core_of` tag present in both Vanilla and ME is scored purely geometrically against Stage 1 affines:

- **Accepted (`46` clean tags, geometric IoU ≥ 0.72)**: e.g., `ALG` (`IoU = 0.9733`), `BOS` (`0.8872`), `BHR` (`0.8457`), `BLR` (`0.8103`), `BAY` (`0.7924`). These reinforce sub-national interior junctions without trusting state IDs or names.
- **Rejected (`32` tags, IoU < 0.72 or repurposed)**: automatically excluded before Stage 3, catching both shifted historical tags (`OCC` `0.7144`, `SIL` `0.6302`, `WUR` `0.5061`, `SAX` `0.3742`) and completely repurposed mod tags (`BRN`, `SAM` `0.0000`, `TNE` `0.0000`).

### Stage 3 — Jacobian-Guarded Diffeomorphic Residual Field

A sparse bilinear displacement lattice (`24` vanilla px grid step) refines local map distortions using Huber IRLS (`lam_smooth = 6.0`, `lam_bend = 4.0`) over four physical/topological constraint sets:

| Constraint Family | Observation Count |
| --- | ---: |
| 1936 + clean-core triple junctions | `140` |
| Normal-projected labeled border samples | `14,709` |
| Normal-projected river skeleton samples (`rivers.bmp`) | `6,910` |
| High-confidence inland topography (`heightmap + river`) NCC patches | `44` |

An analytic Jacobian determinant guard enforces `det(I + ∇D) ≥ 0.55` everywhere on land so topology never folds:
- Achieved `min_jacobian_det_on_land = 0.964` (`guard_attempts = 1`, `diffeomorphic_ok = true`)
- Land displacement magnitude: `p50 = 0.699` px, `p95 = 1.863` px, `max = 3.21` px

---

## 3. Quantitative Benchmarks (Stage 1 → Stage 3)

All values below are recorded verbatim in [`registration_report.json`](../states/eu_mapping/registration_report.json):

| Metric | Stage 1 (3-Landmass Affines) | Stage 3 (Diffeomorphic Field) | Notes |
| --- | :---: | :---: | --- |
| **Triple junction `p50` / `p90`** | `1.658 px` / `4.319 px` | **`1.089 px` / `3.422 px`** | Sub-pixel median alignment at borders |
| **1936 same-owner pixel accuracy** | `98.94%` | **`99.27%`** (`0.99266`) | Over all main-map European/MENA land |
| **River skeleton distance `p50` / `p90`** | `3.162 px` / `24.597 px` | **`3.000 px` / `24.597 px`** | Across `rivers.bmp` hydrography |
| **Heightmap NCC** | `0.8031` | **`0.8041`** | Cross-correlation of relief rasters |
| **ME states ≥ 95% same-owner purity** | `61.31%` | **`62.12%`** | — |
| **ME states ≥ 80% same-owner purity** | `82.29%` | **`83.59%`** | Only `5.31%` fall below `60%` |
| **ME provinces ≥ 80% owner purity** | `87.00%` | **`87.90%`** | `75.73%` of provinces are ≥ 95% pure |
| **National capitals hit (`39` countries)** | `39 / 39` (`100%`) | **`39 / 39` (`100%`)** | Zero misses |
| **Held-out IoU — Rhineland DMZ (1936)** | `0.7110` | **`0.7134`** | Never seen during warp |
| **Held-out IoU — Anschluss (1938)** | `0.9007` | **`0.9376`** | Never seen during warp |
| **Held-out IoU — Sudetenland (1938)** | `0.7924` | **`0.8707`** | Never seen during warp |
| **Held-out IoU — Slovakia (1939)** | `0.6608` | **`0.6962`** | Never seen during warp |
| **Held-out IoU — Albania (1939)** | `0.8445` | **`0.8728`** | Never seen during warp |

---

## 4. State Classification, Union Blocks & Human Adjudications

### State Partition Breakdown (`2,961` Mapped + `25` Excluded = `2,986`)

- **Confidence tiers** ([`mapping_summary.json`](../states/eu_mapping/mapping_summary.json)):
  - `high_interior`: **`2,261`** (`area_interior` — virtually 100% enclosed inside a single Vanilla parent)
  - `high_majority`: **`318`** (`area_majority`)
  - `high_core_locked`: **`206`** (`196` `area_interior_core_verified` + `10` `core_signature_lock`)
  - `medium_straddler`: **`99`** (`35` `straddler_area_modal`, `27` `straddler_hist_tiebreak`, `19` `straddler_vp_tiebreak`, `10` `straddler_name_tiebreak`, `8` `straddler_core_confirmed`)
  - `adjudicated`: **`59`** ([`adjudications.tsv`](../states/eu_mapping/adjudications.tsv): `52` confirmed + `7` overrides, encoded deterministically in [`tools/build_eu_mapping.py`](../../tools/build_eu_mapping.py))
  - `fallback_island`: **`18`** (`10` `nearest_same_owner_land`, `7` `island_core_confirmed`, `1` `island_sole_owner_state`)
- **Explicitly excluded off-map / synthetic ME states (`25`)**: `21` top-left North America inset states (`CAN`/`USA`/`ENG`/`FRA`, IDs `1..64, 2920`), `2` off-map colonial proxies (`2974` Congo `COG`, `2975` Indonesia `HOL`), and `2` synthetic border states (`2552` American Border, `2986` Map Border `MAP`).
- **Explicitly excluded ME Strategic Regions (`11`)**: `4` land NA inset (`156..159`), `3` sea NA inset (`189..191`), `2` off-map proxy (`252` Congo → `v227`, `253` Indonesia → `v158`), and `2` synthetic border regions (`192`, `193`). Plus `1` explicit sea override (`188` Arabia canal strip → `v104 Arabian Sea`).
- **Union-Aligned Macro Blocks (`157` blocks in [`state_aligned_blocks.tsv`](../states/eu_mapping/state_aligned_blocks.tsv))**:
  - Minimal bipartite connected components where $\bigcup \text{ME} \equiv \bigcup \text{Vanilla}$: **`22`** `1:1` blocks, **`72`** `1:many` blocks, and **`63`** `many:many` blocks (enabling zero-gap, multi-state territorial translation).

---

## 5. Exact Layer Schemas (`docs/states/eu_mapping/`)

All TSV artifacts use UTF-8 (`LF`) encoding and are deterministically reproducible:

| File | Rows | Columns (TSV Header) |
| --- | ---: | --- |
| [`prov_m2v.tsv`](../states/eu_mapping/prov_m2v.tsv) | `19,758` | `me_prov`, `me_state`, `me_state_name`, `me_owner`, `norm_owner`, `me_px`, `v_state`, `v_state_name`, `v_prov`, `v_state_share`, `same_owner_share`, `purity_class`, `geo_method`, `nearest_dist_px`, `all_v_states` |
| [`prov_v2m.tsv`](../states/eu_mapping/prov_v2m.tsv) | `464` | `vanilla_id`, `vanilla_name`, `vanilla_owner`, `me_prov_count`, `v_native_px`, `v_covered_px`, `reverse_coverage`, `me_provs_ordered` |
| [`state_overlap.tsv`](../states/eu_mapping/state_overlap.tsv) | `5,658` | `vanilla_id`, `vanilla_name`, `vanilla_owner`, `me_id`, `me_name`, `me_owner`, `norm_owner_match`, `me_px`, `me_total_px`, `me_share`, `me_same_owner_share`, `v_unique_px`, `v_native_px`, `v_share`, `prov_count_in_v`, `edge_role`, `core_sig_match`, `is_partition_parent`, `block_id` |
| [`state_partition_m2v.tsv`](../states/eu_mapping/state_partition_m2v.tsv) | `2,986` | `me_id`, `me_name`, `me_owner`, `norm_owner`, `status`, `v_id`, `v_name`, `v_owner`, `same_owner_share`, `all_share`, `runner_v_id`, `runner_share`, `margin`, `confidence_tier`, `resolution`, `geo_method`, `nearest_dist_px`, `core_sig_match`, `block_id`, `needs_review`, `review_reason`, `legacy_v_id`, `legacy_diff` |
| [`state_partition_v2m.tsv`](../states/eu_mapping/state_partition_v2m.tsv) | `464` | `vanilla_id`, `vanilla_name`, `vanilla_owner`, `me_count`, `primary_me_id`, `primary_me_name`, `me_members`, `interior_members`, `straddler_members`, `v_native_px`, `v_covered_px`, `reverse_coverage`, `block_id`, `has_review_member` |
| [`state_aligned_blocks.tsv`](../states/eu_mapping/state_aligned_blocks.tsv) | `157` | `block_id`, `owners`, `block_type`, `v_count`, `me_count`, `vanilla_ids`, `vanilla_names`, `me_ids`, `union_forward_purity`, `union_reverse_coverage` |
| [`state_primary_city.tsv`](../states/eu_mapping/state_primary_city.tsv) | `464` | `vanilla_id`, `vanilla_name`, `vanilla_owner`, `primary_me_id`, `primary_me_name`, `primary_basis`, `vp_matched_name`, `vp_dist_px`, `in_partition_members`, `me_member_count` |
| [`region_overlap.tsv`](../states/eu_mapping/region_overlap.tsv) | `600` | `vanilla_region_id`, `vanilla_name`, `me_region_id`, `me_name`, `kind`, `overlap_px`, `me_total_px`, `me_share`, `v_total_px`, `v_share`, `relation_class` |
| [`region_partition_m2v.tsv`](../states/eu_mapping/region_partition_m2v.tsv) | `253` | `me_region_id`, `me_name`, `kind`, `status`, `vanilla_region_id`, `vanilla_name`, `primary_share`, `me_px`, `proxy_vanilla_id`, `proxy_vanilla_name`, `all_vanilla_overlaps`, `notes` |
| [`region_partition_v2m.tsv`](../states/eu_mapping/region_partition_v2m.tsv) | `99` | `vanilla_region_id`, `vanilla_name`, `kind`, `status`, `me_count`, `primary_me_region`, `primary_me_name`, `me_regions`, `me_region_names`, `v_coverage` |
| [`adjudications.tsv`](../states/eu_mapping/adjudications.tsv) | `59` | `me_id`, `me_name`, `me_owner`, `action`, `chosen_v_id`, `chosen_v_name`, `prev_auto_v_id`, `prev_auto_v_name`, `chosen_share`, `reason` |
| [`legacy_audit.tsv`](../states/eu_mapping/legacy_audit.tsv) | `440` | `me_id`, `me_name`, `me_owner`, `new_v_id`, `new_v_name`, `new_share`, `legacy_v_id`, `legacy_v_name`, `legacy_share`, `dist_to_legacy_px`, `legacy_basis`, `diff_class` |
