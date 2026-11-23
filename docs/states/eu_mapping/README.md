# European Vanilla 1.19 ↔ Magna Europa Definitive Mapping (`eu_mapping/`)

Definitive, non-circular 4-layer mapping between Vanilla HOI4 (1.19) and **Magna Europa: Reforged**, strictly scoped to the **European / MENA main map** (2,961 European ME states, 19,758 European ME land provinces, 464 European Vanilla states). The 25 off-map/inset/synthetic ME states (top-left North America inset, bottom-left Congo proxy, bottom-right Indonesia proxy, and MAP borders) are explicitly excluded and tagged.

## Regeneration & Sync Commands

```bash
# 1. Regenerate registration + all 4 layers + audits + offline visual review UI
#    (requires numpy, scipy, Pillow)
python tools/build_eu_mapping.py

# 2. Run unit & table invariant tests
python -m unittest discover -s tools -p test_eu_mapping.py -v

# 3. Regenerate Layer 5 (Strategic Regions crosswalk, pure Python stdlib!) & run tests
python tools/build_region_mapping.py
python -m unittest discover -s tools -p test_region_mapping.py -v
python tools/translate_vanilla_regions.py --regions 7 8 18 68 69
python tools/translate_vanilla_regions.py --audit-scripts

# 3. Sync / translate Vanilla HOI4 updates into Magna Europa (stdlib only — no numpy needed!)
#    Territory union mode (auto-cancels internal border straddlers across multi-state blocks):
python tools/translate_vanilla_states.py --states 42 51 978 --mode territory --emit transfer_state

#    City / economic single-target mode (1 -> 1 primary city for factories/slots/capitals/resources):
python tools/translate_vanilla_states.py --states 126 16 64 --mode city

#    Province crosswalk mode (for railways/bunkers/canals/province controllers):
python tools/translate_vanilla_states.py --provinces 6521 11506

#    Translate a full Vanilla focus/decision/event script (auto-detects territory vs city vs province):
python tools/translate_vanilla_states.py --snippet path/to/vanilla_focus.txt

#    Diff two Vanilla HOI4 versions (e.g. 1.19 -> 1.20) and translate ONLY changed lines:
python tools/translate_vanilla_states.py --diff old_vanilla.txt new_vanilla.txt
```

## Registration Architecture (`tools/eu_map_registration.py`)

Zero circularity: no Victory Points, state names, migration comments, or legacy tables enter any geometric warp.

1. **Physical Landmass Decoupling (Stage 1)**:
   - **Eurasia + North Africa Mainland**: fitted from 58 unique normalized 1936 country/coast/lake triple junctions + labeled mutual-NN boundary ICP (`~3.14×` uniform upscale, coast p50 ≈ 0.50 vanilla px).
   - **North Atlantic / British Isles** (Great Britain, Ireland, Man, Hebrides, Orkney, Shetland, Faroe Islands, Jan Mayen): dedicated affine (`(-9.1, -11.2)` px rigid shift vs mainland, coast p50 = 0.51 px).
   - **Iceland**: dedicated affine (`(0, -39.9)` px rigid shift vs mainland, coast p50 = 0.52 px).
2. **Auto-Validated Sub-National Cores (Stage 2)**:
   - Automatically validates all shared releasable `add_core_of` tags by Stage-1 geometric IoU (accepts 46 clean tags with IoU ≥ 0.72, automatically rejecting repurposed tags like `BRN/SAM/TNE`).
3. **Bounded Diffeomorphic Residual Field (Stage 3)**:
   - Sparse bilinear lattice (`24` vanilla px step) solved with Laplacian + bending regularization and Huber IRLS over 4 physical/topological cues: 1936+clean-core triple junctions, normal-projected labeled boundaries, normal-projected river skeletons (`rivers.bmp`), and high-confidence inland `(heightmap + river)` NCC patches.
   - Analytic Jacobian guard enforces `det(I + ∇D) ≥ 0.55` everywhere on land (achieved `min_det ≈ 0.964`, zero folding).

## Generated Files (4 Layers + Audits + Review UI)

| Layer | File | Grain & Purpose |
|---|---|---|
| **1 — Provinces** | `prov_m2v.tsv` | Every European ME land province (~19,758) → primary Vanilla state & province, purity class, weighted overlaps |
| **1 — Provinces** | `prov_v2m.tsv` | Every European Vanilla state (464) → ordered ME province list & reverse coverage |
| **2 — Overlap** | `state_overlap.tsv` | Full weighted many-to-many `(vanilla_id, me_id)` overlap matrix (`interior` / `majority` / `straddler` / `minor` / `sliver`) |
| **3 — Partition** | `state_partition_m2v.tsv` | Every ME state (2,986: 2,961 mapped + 25 excluded) → unique disjoint Vanilla parent (gap-free, overlap-free) |
| **3 — Partition** | `state_partition_v2m.tsv` | Every European Vanilla state (464) → ordered `me_members`, `interior_members`, `straddler_members`, `primary_me_id`, `block_id` |
| **3 — Blocks** | `state_aligned_blocks.tsv` | Minimal bipartite union-aligned blocks (`union(ME) ≡ union(Vanilla)`) |
| **4 — City ID** | `state_primary_city.tsv` | Every European Vanilla state (464) → single primary ME state (`1→1`) for factories/slots/capitals/resources |
| **Audit** | `legacy_audit.tsv` | Complete diff vs legacy `docs/states/m2v_states.tsv` flagging distant/wrong-owner legacy errors (204 high-confidence legacy errors caught) |
| **Adjudications** | `adjudications.tsv` | Full audit trail of all 59 human-approved border straddler & offshore-island adjudications (7 overrides + 52 confirmations, encoded in `tools/build_eu_mapping.py`) |
| **Review** | `review_queue.tsv` | Open human review worklist (**0 open items** — all 2,961 European ME states finalized) |
| **Review UI** | `review/index.html` | Offline 4-mode cartographic review UI: **Swipe Atlas**, **Parent-Color Check** (8-color graph-colored atlas + Legacy toggle), **Spotlight A/B/C/D** (4-candidate coloring, `rivers.bmp` overlay, ME `●` & Vanilla `◆` VP city pins, Vanilla territorial script diff, ME regional cores, one-click Wikipedia lookup, and full adjudicated-state inspection), and **Synced Side-by-Side** |
| **Metrics** | `registration_report.json`, `mapping_summary.json` | Full quantitative registration benchmarks and summary statistics |
| **5 — Regions** | `region_overlap.tsv`, `region_partition_m2v.tsv`, `region_partition_v2m.tsv`, `region_mapping_summary.json` | Weighted many-to-many overlaps & partition between Vanilla 304 and ME 253 strategic regions (169 on-map land + 73 on-map sea + 11 inset/proxy/border excluded) |
