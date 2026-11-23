# Map truth tables — vanilla 1.19 ↔ Magna Europa

Canonical vanilla↔ME identity tables for map content. **Single source of truth** for any
state/region/VP id question. Tracked in git (these files travel with the repo); regenerate
after every map-related change — never hand-edit a generated TSV. The separate
human-authored approvals TSV described below is not a canonical truth table.

Working copies used to live in the gitignored `_ctx/`; they were moved here (and the
generator from `tools/out/` to `tools/`) so the tables are versioned alongside the map data.

Regenerate — the confirmation layer consumes `state_edges_1936.tsv` and the
mask audit directly, so the full chain is order-sensitive:

```bash
python tools/build_state_map.py                 # canonical m2v/v2m + evidence + comment edges
python tools/build_state_edges.py               # full relation-evidence ledger
python tools/build_state_edges_1936.py          # 1936 owner-constrained candidate ledger
python tools/audit_state_masks_1936.py          # sampled-mask audit (requires the 1936 ledger)
python tools/audit_state_mapping.py             # conservative raw-evidence audit
python tools/build_focus_state_refs.py          # focus-corpus state references
python tools/build_state_relation_map.py        # relation-map registration + census + semantic tables
python tools/build_state_mapping_confirmation.py  # 95% owner-scoped gate over the snapshot
python tools/build_state_mapping_review.py      # static review page + map PNGs
HOI4_DIR="C:/path/to/Hearts of Iron IV" python tools/build_state_map.py   # override vanilla dir
HOI4_DIR="C:/path/to/Hearts of Iron IV" python tools/build_state_edges.py
HOI4_DIR="C:/path/to/Hearts of Iron IV" python tools/build_state_edges_1936.py
HOI4_DIR="C:/path/to/Hearts of Iron IV" python tools/audit_state_masks_1936.py
```

The first generator produces the primary compatibility views and complete
multi-ID migration-comment evidence. `build_state_edges.py` produces the
relation-evidence ledger. `build_state_edges_1936.py` adds the 1936.1.1 owner
constraint: `same_owner` rows are within-country candidates and `cross_owner`
rows remain diagnostics. It uses the same global registration as the
observation ledger; it is not a per-country affine proof and never claims a
complete subset. `audit_state_masks_1936.py` joins that ledger with the live
raster census and fails closed when the ledger is missing or malformed, so it
always runs after both edge builders. `audit_state_mapping.py` produces
`mapping_audit.tsv` + `mapping_audit_summary.json`; the confirmation builder
then scores every mask-audit pair against the owner-scoped metrics (see
"Mapping confirmation" below) and the review page renders the result.
Between the focus-corpus table and the confirmation layer,
`build_state_relation_map.py` builds the separate `state_relation_*` layer —
its own topology registration and sampled census; `mapping_comment_edges.tsv`
and `focus_state_refs.tsv` are its only generated semantic inputs (see "State
relation map" below).

Generator outputs and the edge tables are written here. Audit output is
`mapping_audit.tsv` and `mapping_audit_summary.json`.


Inputs: `history/states/*.txt` (both sides), `map/strategicregions/*.txt`, all `localisation/english/*.yml`,
plus migration comments `# was vanilla N` (including multi-ID sets like `42/51/978`
or `{291 1011 676}`) scattered through `common/`, `events/`, `history/`.
A rerun over unchanged inputs is byte-identical.
## Focus-tree state references

`focus_state_refs.tsv` and `focus_state_map.tsv` record every state reference in
the vanilla and mod `common/national_focus/*.txt` corpora, resolved against the
canonical tables. Regenerate (stdlib only, no map rasters):

```bash
python tools/build_focus_state_refs.py      # HOI4_DIR / ME_MOD overridable
```

Inputs: both focus corpora, `state_edges.tsv` when present (the generated
relation-evidence ledger — every `(me_id, vanilla_id)` edge classified by
`relation_scope`), `v2m_states.tsv` + `m2v_states.tsv` (the primary
compatibility views the coverage columns are built on — the generator never
re-derives ancestry), and vanilla
`history/states/*.txt` + `map/definition.csv` (for `is_europe`). A rerun over
unchanged inputs is byte-identical.

Recognized state namespaces: scalar state keys (`state`, `owns_state`,
`controls_state`, `has_full_control_of_state`, `transfer_state`,
`transfer_state_to`, `add_state_claim`, `remove_state_claim`, `add_state_core`,
`remove_state_core`, `set_state_owner`, `set_state_controller`,
`add_state_modifier`, `add_state_research_slot`, and the state-valued argument
keys `set_capital`, `capital_scope`, `has_resources_rights`, `add_resource`);
numeric state scopes `N = { ... }`; `states = { N ... }` lists; `prioritize`
member lists only beneath `*_state` quantifier scopes (`random_state`,
`random_owned_state`, `random_controlled_state`,
`random_owned_controlled_state`, `random_core_state`, `every_*_state`);
numeric entries under `states_filter`; and numeric scope chains such as
`has_war_with = 762.owner`.

Namespace traps excluded: `province`, `path`, `region`, `strategic_region`,
`victory_points` keys and province `id`s under `set_province_name` /
`add_victory_points`; numeric weight blocks under `random` / `random_list`;
numeric scopes whose first key is `modifier`, `factor`, `add_attack`,
`add_defense`, `add_logistics`, or `add_planning`. Numbers are never classified
by range — a literal is a state ref only where the grammar demands one;
literals that resolve to no state keep their value with
`resolution = unknown_vanilla_state` / `stale_vanilla_id` / `unknown_literal`.

Coverage semantics: `focus_state_refs.tsv` is occurrence-level. On **mod** rows
`current_me_refs`/`covers`/`missing_members` describe only the literals emitted
by that single occurrence — the enclosing `states = { ... }` / `prioritize` /
`states_filter` list block, or the scalar itself (`coverage_scope=occurrence`);
a lone scalar is `single`, never promoted to `full` by references elsewhere.
On **vanilla** rows the same columns are corpus-union
(`coverage_scope=corpus_union`): which cell members appear anywhere in the mod
corpus. `focus_state_map.tsv`'s `mod_coverage`/`mod_covered_members` are also
corpus-union (`mod_coverage_scope=corpus_union`) — they summarize the whole mod
corpus per vanilla ancestor, not any single reference. `full` means every
member of the ancestor's ordered `v2m` partition cell is covered in that scope;
empty member sets are `offmap`. Coverage is **primary-family coverage**: the
`me_members` primary compatibility view, never extended by secondary
`state_edges.tsv` members (`edge_members`/`edge_ancestors` report the
asserted-scope relation set and `observed_members`/`observed_ancestors`
the heuristic-only one — all for inspection only).

Collision semantics: sharing a number between the vanilla and ME namespaces is
never by itself a collision. Vanilla rows are never flagged —
`literal_is_live_me` is informational only. On mod rows,
`collision_live_me`/`comment_mismatch` requires an explicit `# was vanilla ...`
attribution whose cited id set is disjoint from every **asserted** ancestor
of the literal — the canonical `m2v` ancestor plus, when `state_edges.tsv`
is loaded, the `compatibility`/`asserted`-scope secondary edges in the
relation ledger (the comment's whole id list is parsed:
`was vanilla 42/51/978` cites all three). A comment that matches only an
asserted secondary ancestor is a real relation and resolves
`ok|comment_secondary_edge`, not a collision. An `observation`-scope edge —
raster/candidate/name/pixel/vp evidence, which records sampled overlap only
and can never prove a relation — does NOT count as a secondary ancestor: a
comment matching only such a geometry candidate still collides, and the
candidate itself remains visible in `observed_ancestors`. A mod literal that
overlaps the vanilla namespace without such an attribution is
`ok|namespace_overlap`, and a mod literal that is a vanilla id but not a
live ME state stays `stale_vanilla_id`. In the map table,
`collision_live_me` counts only those real mod collision rows per ancestor,
`collision_me_state` is true only when such a row exists, and
`collision_comment_claims` lists the vanilla ids those rows claimed.
`migration_comment` carries the raw `# was vanilla N` text,
`comment_vanilla_id` its parsed id set. `edge_ancestors` (refs table) lists
the literal's asserted-scope ancestor set, canonical first;
`observed_ancestors` lists the observation-scope candidates; `edge_members`
(map table) lists every ME state with an asserted-scope edge to the vanilla
state, primary member first, and `observed_members` the observation-scope
rest — all are empty when `state_edges.tsv` is absent.
Vanilla refs inside files the mod
shadows are flagged in map `notes` (`shadowed_by_mod:`); those refs are not
live in-game.

## Conservative automatic repairs

Use the existing evidence/audit/canonical snapshot to propose safe source-level
repairs. If that snapshot is missing or out of date, regenerate and audit it first.
The normal sequence is **propose → inspect → apply → build → audit → review page**:

```bash
python tools/auto_repair_state_mapping.py --propose
# Inspect docs/states/auto_repair_proposals.tsv and auto_repair_summary.json.
python tools/auto_repair_state_mapping.py --apply
python tools/build_state_map.py
python tools/audit_state_mapping.py
python tools/build_state_mapping_review.py
# Refresh the proposals against the regenerated snapshot for final human review.
python tools/auto_repair_state_mapping.py --propose
```

The repair tool uses only the Python standard library and the existing audit
helpers. `--propose` writes one deterministically ME-ID-sorted row per state with
decision `auto_repair`, `review`, `already_manual`, or `no_action`. The summary
contains decision counts, proposed/applied/review/manual IDs, skipped reasons,
and SHA-256 hashes of the proposals, generator source, all input tables, and the
optional geography input (including whether it was present).
Confidence labels (`high`, `low`, `manual`, `unchanged`) describe these decisions,
not statistical probabilities. Final reviewers use the proposal TSV's `review`
rows and keep `already_manual` rows visible as regression context; the tool never
edits `review_queue.tsv`.

Automatic repair requires at least two agreeing independent evidence families.
Existing eligible combinations are unchanged: strong pixel evidence (share ≥ 0.80
and margin ≥ 0.25) paired with a comment, exact name, or candidate-independent
raw-geography citation, or a direct VP/exact-name identity paired with another
independent non-pixel family. Raw geography is never sufficient alone and cannot
qualify alongside VP-only evidence; it must agree with a strong pixel, comment,
or exact-name identity. Pixel+VP alone is rejected because the pixel warp is
VP-derived. The separate `overlap` positioning family adds only **overlap+comment**
and **overlap+exact-name** as eligible independent pairs, using a fully validated,
producer-accepted ancestor. Overlap alone, overlap+pixel, overlap+VP, and
overlap+raw-geography never supply an independent pair. A change justified by a
new overlap pair may replace only a pixel-geographic or unmapped canonical
mapping, not an existing direct comment/name/VP identity; manual corrections
remain protected. Tied votes, disagreeing families, same-name ME/vanilla
collisions, VP cross-state evidence, comment/pixel conflict, and core-disjoint
flags remain review-only. `context_only` audit rows are context, not corroboration.
`v2m_states.tsv` supplies **only** the set of real vanilla IDs; its descendants,
region symmetry, and candidate-conditioned visual boundaries never supply
identity evidence. `raw_geography_marker` must cite concrete coast initialization
plus local river/lake correction provenance; generic approval booleans and visual,
boundary, region, symmetry, VP, or city-anchor markers are not proof.

`--geography` selects an optional natural-geography table, defaulting to
`geography_evidence.tsv` beside the chosen `--evidence`. A missing file is valid:
proposal decisions stay exactly as without it, and the summary records the optional
input as absent. When present, the file must use the fixed columns, unique ME IDs
that already appear in the evidence table, nonnegative numeric pair/residual
counts, 0..1 shares and margin with a consistent best/runner pair, and a safe
marker; malformed shape or values are rejected before any row is merged. A row
qualifies only from a candidate-independent bbox coast fit plus local river/lake
translation (no VP/city control anchors or candidate-conditioned warps) that meets
the producer's conservative gates: `natural_ok`, ≥ 10 kept natural pairs, residual
p50 ≤ 6 and p95 ≤ 12 vanilla pixels, top share ≥ 0.85, margin ≥ 0.60, and sampled
state-raster area ≥ 0.95. Rows without an accepted natural candidate contribute
no raw-geography family and remain producer diagnostics. The in-memory merge
preserves `raw_geography_ancestor`, `raw_geography_marker`, and all optional
`overlap_*` fields without replacing any existing evidence field. Geography rows
are never written into `mapping_evidence.tsv`, the canonical TSVs, or
`review_queue.tsv`.

The optional producer `tools/state_geography_evidence.py` also appends separate
`overlap_*` positioning diagnostics to `geography_evidence.tsv`; a validated,
producer-accepted ancestor can contribute the `overlap` repair evidence family.
The existing natural and raw-geography columns keep their order and meaning.
This is a candidate-independent **global bbox coast-affine raster** census, not
the local river/lake correction, a province-centroid vote, or a
candidate-conditioned warp.
It samples every pixel in the ME native frame with the one ME→vanilla coast
affine and nearest-pixel `np.rint` lookup, checking both geometric and rounded
image bounds. Positive state IDs come only from land provinces in `definition.csv`;
sea, lake, and dummy province 0 are excluded. `--me-id` filters output rows, not
the shared full-frame census or registration.

`overlap_area_px` is the full ME state's native land-pixel area. Best/runner
shares, their margin, and `overlap_assigned_share` all use that denominator:
out-of-frame samples and vanilla water/unassigned pixels reduce coverage and
are **never renormalised away**. `overlap_unregistered_share` is one minus
assigned share. Ties choose the lower positive vanilla ID. Reverse
`overlap_containment` is the best candidate's intersection divided by **all**
pixels sampling that positive vanilla state anywhere in the full ME frame,
including ME-empty pixels; it is neither a best-share alias nor the candidate's
unsampled vanilla-native area. Containment is diagnostic, not an acceptance gate.

The producer reports 4-connected ME components, the largest component's fraction
of state area, its own best candidate, and that candidate's fraction of component
area. Equal-area largest components keep scan-label order. Acceptance requires
at least **64** state pixels, assigned share **≥ 0.90**, best share **≥ 0.80**,
margin **≥ 0.25**, and agreement from every component: each must have the largest
component's best candidate and independently meet the assigned/share/margin
gates. Small agreeing detached islands need not each reach 64 pixels, but a
missing, weak, or dissenting detached component withholds the entire ancestor;
island-only states face the same whole-state gates. Diagnostics retain the
aggregate and largest-component bests even when ancestry is withheld. Below-area
rows report `insufficient_area`; otherwise multi-component disagreement takes
priority over whole-state coverage/share/margin failures.

Only accepted rows have `overlap_ok=1`, `overlap_ancestor`, and the standalone
safe `overlap_marker=coast_bbox_affine_raster_overlap`. That marker is never put
in `raw_geography_marker`. Audit and repair validate the entire 20-column
extension atomically, including positive candidate IDs, strict 0..1 ratios and
their arithmetic, acceptance flags and gates, component agreement, and the exact
`overlap_method=coast_bbox_affine_raster`, `overlap_domain=me_native_land`, and
accepted marker. A legacy table without the extension, or an entirely blank
extension when overlap setup is unavailable or fails, is valid; partial schemas
and malformed or inconsistent populated rows are rejected. Natural/raw rows and
their acceptance gates are unchanged. Only the accepted `overlap_ancestor` can
supply overlap evidence: a withheld `overlap_best` or largest-component best is
diagnostic, never a fallback identity.

An accepted overlap ancestor that differs from the canonical ancestor is always
`overlap-conflict` / `review_required` in the audit, even if the evidence table
already names that candidate. Accepted agreement can remain `no_action`.
Withheld rows are `overlap-weak` / `context_only` unless another hard reason
requires review; producer `component_disagreement` specifically requires review.
Audit output retains the producer fields as `geography_overlap_*` diagnostics.
The repair pass may use a conflicting accepted ancestor for an automatic repair
only when an independent direct comment or exact-name identity agrees and the
current-mapping protections and all existing blockers above pass; otherwise a
change requires human approval. Neither overlap diagnostics nor their merge
directly writes canonical mappings, changes natural votes or raw-geography
ancestry/provenance, or overrides an already-manual row.

### Human approval of conservative repairs

Automatic repairs are the default: without `--approvals`, `--propose` and
`--apply` behave exactly as described above and the proposal TSV is unchanged.
A human can optionally promote specific `review` proposals by creating
`docs/states/auto_repair_approvals.tsv` **after** inspecting the deterministic
`auto_repair_proposals.tsv`. The tool never creates or overwrites this file; it is
hand-authored and may also be absent or header-only (both are valid and mean "no
approvals"). Never edit the proposal TSV to signal approval.

The approval TSV has the fixed header and column order
`me_id, approved_ancestor, review_note`. Every row requires a non-empty
`review_note`: single-line printable text with no `#` and no control characters,
recorded as the evidence/override reason. Any proposal with decision `review` may
be approved, including one whose `proposed_ancestor` is empty or already equals
`current_ancestor`; the approval itself supplies or changes the ancestor.
`approved_ancestor` must be a real vanilla ID present in `v2m_states.tsv` and must
differ from `current_ancestor`, so a no-op approval is always rejected. When
`approved_ancestor` equals the proposal's `proposed_ancestor` it is a normal
approval; when it differs (including when the proposal had none) the approval is
recorded as a `manual_candidate_override` with the mandatory note as the override
reason. Duplicate or unknown ME IDs, no-op ancestors, rows whose decision is
`no_action`, `already_manual`, or `auto_repair`, and states already covered by
`MANUAL_ME2V`, grouped `MANUAL` members, or other known/current manual
corrections are rejected before any source edit.

After writing or editing approvals, re-propose against them and inspect the result
before applying:

```bash
python tools/auto_repair_state_mapping.py --propose
# Human writes docs/states/auto_repair_approvals.tsv after reviewing the proposals.
python tools/auto_repair_state_mapping.py --propose \
    --approvals docs/states/auto_repair_approvals.tsv
# Inspect the still-deterministic proposal rows and the updated summary hashes.
python tools/auto_repair_state_mapping.py --apply \
    --approvals docs/states/auto_repair_approvals.tsv
```

`--propose --approvals` leaves the proposal rows themselves unchanged; it records
the approvals path, SHA-256 content hash, and present/absent state in the summary
alongside the approved IDs, whether each was a normal approval or a
`manual_candidate_override`, and the override reasons. Any later edit to the approvals
file (or any other hashed input) invalidates that proposal and forces another
`--propose --approvals` before `--apply`.

`--apply` consumes the **already generated, unedited** proposal/summary pair;
it does not silently regenerate it. Missing files, changed source/input/proposal
hashes (including the geography file's content hash, its present/absent status,
and the approvals hash/presence when `--approvals` is given), duplicate IDs/keys,
malformed source dictionaries, invalid approval rows, or decisions that no longer
match recomputation fail closed before source edits. Re-run `--propose` and inspect
again after any input/source/approvals change, including a geography file that
appears, disappears, or changes. Existing `MANUAL_ME2V` entries,
grouped `MANUAL` members, and known/current manual corrections are never
overwritten. New entries carry `[auto-repair]` comments (or `[human-approved]`
with the current, proposed, approved IDs and the recorded note/evidence when the
row came from an approval), reuse the dictionary's
indentation/comment alignment, and are processed in numeric ID order at the
first larger existing key. Historical locally sorted sections and old entries
are not reordered; the legacy dictionary is not claimed to be globally sorted.

A source edit — automatic repairs, approved repairs, or their union — creates
`tools/build_state_map.py.bak` with the exact previous bytes before atomically
replacing the source. Zero repairs create no backup;
an existing `.bak` is never overwritten (archive/move it before a later batch).
Canonical TSVs are written only by the subsequent generator/audit commands,
never by this tool. Use `HOI4_DIR` on the generator as above when needed.

For isolated inputs, `--evidence`, `--geography`, `--audit`, `--canonical`
(alias `--m2v`), `--vanilla-states`, `--manual-source`, `--proposals`
(alias `--output`), `--summary`, and `--approvals` override paths. Unspecified
audit/canonical/vanilla-ID tables and the optional geography table are looked up
beside the selected evidence, not silently borrowed from the repo; an absent
geography file is recorded, not treated as an error. Use the same paths for
propose and apply.


## Visual overlay and registration evidence

```bash
python tools/render_state_overlay.py --me-id 2624 --warp visual \
    --visual-boundary neighbours --output tmp_diag2/visual/2624.png --padding 96
```

The default `--warp natural` is the candidate-independent baseline: a global
continent-coast affine plus a bounded river/lake correction. For Magna Europa's
non-standard local stretch, `--warp visual` adds named VP/city residuals and a
regularised local displacement field. With `--visual-boundary neighbours` it
also uses the adjudicated neighbouring-state borders to place deformation where
the overlay is judged. This boundary layer is **candidate-conditioned visual
aid only**; it is not identity evidence and never writes a canonical table.
Use `--visual-boundary off` to see the candidate-independent evidence field,
or `ancestor` to use only the target silhouette.
For an alternative candidate, rerun with `--visual-ancestor <vanilla-id>`;
otherwise the boundary aid uses the canonical `m2v_states.tsv` ancestor.
Compare one conditioned image per candidate, never the candidates under a
single candidate's fitted deformation.

The PNG is the primary manual-review artifact. Its JSON sidecar records the
active warp, evidence counts, sampled Jacobian floor, valid raster coverage,
candidate overlap, `boundary_metrics.shared_segments`, and provenance labels.
`boundary_metrics` is a visual comparison metric, not a pass/fail test.
Candidate-conditioned output must be compared with the natural/coast baseline;
an attractive boundary after conditioning does not prove the candidate.

`--warp coastline` is the global-only baseline. `--warp vp` is the legacy
city/VP Delaunay comparison: it uses `registration_anchors.tsv`, does not
extrapolate outside its control-point hull, and rejects folded or degenerate
simplices. VP and city coordinates are valid positioning evidence, but inland
overlap remains a review signal rather than proof.

The sidecar also contains the review-only natural-feature landmark and
undirected branch/connectivity diagnostics. `overall_assessment` is the strict
topology corroboration label only; `marginal` or `insufficient_evidence` does
not mean that the visual overlay failed, and no topology field assigns a state.
Invalid/out-of-frame pixels are never inferred.

## Interactive offline state review

Generate a linked two-pane review page from the existing canonical state TSVs
and the vanilla/ME maps. From the repository root, with Python and NumPy, SciPy,
and Pillow installed:

```bash
python -m pip install numpy scipy Pillow
python tools/build_state_mapping_review.py
```

Optional source, output-directory, and raster-size overrides:

```bash
python tools/build_state_mapping_review.py --vanilla "C:/path/to/Hearts of Iron IV" --mod "C:/path/to/ME mod root" --output "docs/states/state_mapping_review" --max-width 1800 --max-height 1200
```

The default output is `docs/states/state_mapping_review/index.html`; double-click
it to open directly in a browser. Keep the HTML and **all generated PNGs together**
when moving or sharing the page. No server, internet connection, JavaScript
dependency, or runtime fetch is required. Hit testing uses embedded PNG data so
Chromium's `file://` canvas restrictions do not prevent local interaction. The
generator/page do not modify the canonical TSVs or game data.

- Both panes start neutral gray with charcoal state boundaries and no assigned
  state fill. **Exact member** is the default mode. In it, hovering a mapped ME
  state highlights only that hovered member plus its vanilla ancestor; hovering
  vanilla highlights only the vanilla silhouette and lists that state's canonical
  ME family as available, asking you to hover one ME state on the right. The page
  never chooses the primary member for you.
- **Full family** (the other radio mode) instead highlights the hovered vanilla
  state and every canonical ME descendant. In both modes the live panel labels the
  current scope as **Exact pair** or **Full canonical family**, and the family
  list remains available with each member's provenance. Water/unassigned pixels
  and leaving a pane clear the selection.
- An unmapped vanilla state highlights only itself; an unmapped ME state
  highlights only itself, with no vanilla identity inferred. The live panel shows
  IDs/names, the `1 → N` member count, vanilla `v2m` basis/status, and each ME
  descendant's `m2v` basis/status, explicitly including the hovered ME state.
- The live panel also warns when the hovered basis is an indirect or collision-prone automatic result: `pixel-geo`/`pixel-geo-low`, `comment(member)`, `name-singleton`, fuzzy names, and `vp-geo`. These warnings do not rewrite the canonical relation; they identify where visual/geographic review is still appropriate.
  Large descendant lists scroll and label members not sampled in the frame.
- The generator optionally reads `geography_evidence.tsv` beside the canonical
  tables. Missing evidence leaves the review unchanged; malformed columns, IDs,
  numeric values, or duplicate ME rows produce a generation warning and discard
  **the entire diagnostic table**, not a partial join. Valid rows attach only to
  existing ME records and never replace canonical names, ancestors, or provenance.
  Only hovering an ME state shows its natural registration method, kept pairs,
  residuals, best/runner votes, accepted raw ancestor/marker, and available local
  sampling facts. A differing natural best or failed producer quality gate is
  explicitly warned about. Best-vote agreement alone is not corroboration: only
  a producer-accepted raw ancestor matching the canonical ancestor can be labelled
  corroborating, and it remains diagnostic, not identity proof. Missing optional
  sampled-area coverage is labelled as an unchecked gate. These facts never add
  fills, change Exact/Full-family scope, or expand canonical membership; vanilla
  hover and cleared selections show no natural diagnostics.
- The ME hover panel also shows the appended `overlap_*` census when available: one global bbox coast-affine method/domain, full ME land-pixel area, best/runner shares and margin, assigned/unregistered coverage, reverse containment, 4-connected component agreement, producer reason, and accepted ancestor/marker. It is explicitly diagnostic only; withheld bests never change canonical fills or membership, and an accepted candidate differing from canonical is warned as a review conflict.
- Vanilla is sampled into the ME-coordinate frame using one candidate-independent
  global coast affine, initialized from the land bounding boxes; ME geometry stays
  native. There is **no candidate-conditioned local visual warp**. Registration
  method, initialization, affine, full fit diagnostics, source paths, and generation
  warnings are available on the page. Registration is a positioning aid, **not
  state identity evidence**; canonical membership still comes only from the TSVs.
- A third **Pixel-to-pixel comparison** canvas renders the current
  coast-registered vanilla hit map and the native-ME hit map in one display grid;
  it does not alter the two overview panes. **Partition overlay** is the default
  geometry-first mode. **Canonical crosswalk** is an explicit secondary mode
  covering the full frame. It compares each ME state ID's canonical ancestor with
  the vanilla state ID at the same display coordinate (`panes.me.index.ids` and
  `panes.vanilla.index.ids`). The six opaque classifications are:
  **neutral** (no land in either map), **match** (both maps are land and the ME
  state's canonical ancestor equals the vanilla state ID at that pixel),
  **mismatch** (both maps are land but the ME state's canonical ancestor differs
  from the vanilla state ID at that pixel), **ME-only** (mapped ME land with no
  vanilla land), **Vanilla-only** (no ME land, vanilla land present), and
  **unknown** (ME land with no positive valid canonical ancestor, including
  missing metadata). Unknown takes precedence over ME-only and is never reported
  as mismatch.
- **Partition overlay** is the geometry-first comparison mode. It draws continuous native-ME state boundaries and dashed transformed Vanilla state boundaries from the two decoded hit maps only; it does not use canonical ancestors to fit, move, fill, or classify either boundary. Hovering an ME state reports its same-frame Vanilla overlap candidates, sampled area, runner, and margin. A Vanilla-only hover clears the ME target instead of inventing a descendant. The panel labels this **spatial overlap diagnostic — not identity proof**.
- **Hovered exact pair** (the other comparison mode) draws only the pixels of the
  currently hovered ME state in their classification colors and makes every other
  pixel neutral, applying the same category rules. It follows that ME member
  regardless of whether the pane highlight scope is Exact pair or Full canonical
  family. With no ME selection — a cleared selection or a hovered vanilla state —
  the canvas stays neutral and the panel shows an explicit hover prompt.
- Comparison percentages use explicit denominators. Canonical mode counts against
  the union of the two land hit maps: frame pixels outside that union are never in
  the denominator but remain in the frame. Hovered mode counts against the
  selected ME state's pixel count. **Neutral** percentages always use the whole
  frame in both modes and therefore include out-of-target pixels. The summary
  lists each category's pixel count and percentage next to its denominator.
- Moving the pointer over, touching, or clicking the comparison canvas resolves
  the selection at that pixel: the ME state first, otherwise the vanilla state,
  otherwise the selection clears. Pointer leave, pointer cancel, touch cancel,
  and coordinates outside the frame clear the selection.
- Pixel agreement and mismatch visualize **registration and geometry**, not
  mapping identity proof. The comparison never changes the canonical tables or
  the natural diagnostics, and it introduces no candidate-conditioned or other
  warp; both hit maps remain the current coast-registered assets. No new command
  or flag is required: `python tools/build_state_mapping_review.py` and its
  existing overrides still generate the page.
- The shared crop is ME `continent=1` land bounds plus a 3% margin, represented
  by a half-open native-coordinate bbox `[x0, x1) × [y0, y1)`. Default raster limits
  are `--max-width 1800 --max-height 1200`, with nearest-neighbour sampling. A
  member labelled **“not sampled in this frame”** may be outside the crop **or**
  be a tiny island/microstate lost during reduction; zero visible pixels do not
  prove it is geographically outside the crop. Increase the raster limits for
  finer inspection. Inland borders need not align under a single coast affine.
- When the 95% confirmation outputs exist beside the canonical tables
  (`state_mapping_confirmation.tsv` + `state_mapping_review_queue_95.tsv`,
  overridable with `--confirmation PATH`; the queue defaults to a file beside
  the selected confirmation TSV and can be overridden with
  `--confirmation-queue PATH`; `state_mapping_confirmation_summary.json`
  via `--confirmation-summary PATH`), the page gains a **95% mapping confirmation**
  panel above the maps. All three files are validated fail-closed as a unit —
  malformed content drops the whole overlay with a generation warning — and the
  page still generates with an explicit *unavailable* notice when they are absent.
  **Stale-safe**: when the summary JSON is present it must declare schema
  `state_mapping_confirmation_summary/v2`, and every `inputs` record (a
  repo-relative path + SHA-256 + row count per input, including
  `state_edges_1936.tsv`) is re-hashed against the current `docs/states`
  files at page-build time; any changed, missing, unreadable, or
  wrong-schema input discards the entire overlay with a generation warning,
  so a stale summary can never present old rows as fresh. When the summary
  is absent the overlay still embeds — flagged that its counts come from the
  confirmation TSV only, with input hashes unverified.
  The dashboard states the threshold as a **conservative owner-scoped
  coverage gate, not a calibrated probability and not a complete-subset
  proof**, shows `confirmed_95`/`review_candidate`/
  `excluded_synthetic` counts plus project-reference status totals, and calls
  out a zero-confirmed outcome as a data result — nothing below the line is
  silently accepted. The queue table filters by scope (queue/all rows),
  relation status and kind, 1936 owner relation, review-priority tier,
  confidence presets, and free-text ID/name/owner search. Clicking a row pins
  the exact ME↔Vanilla pair on all three canvases (map clicks pin too: ME
  resolves its canonical ancestor, vanilla resolves its primary/first member);
  **Previous**/**Next** walk the filtered list, the selected-record panel shows
  the owner-scoped shares (`me_owner_share`, `v_owner_share`), the
  owner-family reverse share and members, evidence cap/families/basis, owner
  relation, audit class/reasons, project-reference counts and gate reasons —
  the canonical-family metrics remain visible as labeled diagnostics — and
  each side's other confirmation-ledger candidates click through the family.
  `#me=<id>&vanilla=<id>` URL hashes deep-link a selection.
- **Local decisions are an external human artifact, never a write-back.**
  `Accept`/`Reject`/`Defer`/`Candidate` plus a note are kept only in this
  browser's `localStorage` (in-memory fallback when storage is unavailable).
  **Export decisions…** downloads `state-mapping-review-decisions.json`
  (schema `me-state-mapping-review-decisions/v1`: export timestamp, source
  confirmation paths and input hashes, threshold, and each pair's
  decision/note/context); **Clear local decisions** empties the store. Neither
  touches `v2m_states.tsv`, `m2v_states.tsv`, or any canonical table — apply
  them by hand-editing the generator's `MANUAL`/`MANUAL_ME2V` approvals, the
  same discipline as the auto-repair approvals flow.

The deterministic optional-input and hover-panel regression suite is
`python tools/test_state_mapping_review.py`; its JavaScript interaction checks
use Node.js when available and do not load real maps or rewrite review output.



## State-mask audit (1936)

`tools/audit_state_masks_1936.py` is a read-only audit layer that compares
**binary state masks** — each state's land-pixel set (`state raster == id`
restricted to `definition.csv` land) — between the vanilla and ME maps under
one frozen coast registration. It re-derives nothing about identity: the
three output tables are triage/diagnostic material only, they never feed
`v2m_states.tsv`/`m2v_states.tsv`, `state_edges*.tsv`, `auto_repair` evidence
families, or `state_relations.py` queries. Regenerate after any
`history/states` or map bitmap change:

```bash
python tools/audit_state_masks_1936.py
```

Outputs (no timestamps, LF endings, six-decimal ratios, empty string for
unavailable; a rerun over unchanged inputs is byte-identical):

- `state_mask_audit_1936.tsv` — one row per `(me_id, vanilla_id)` mask pair:
  every pair with a positive sampled hit in either direction UNION the rows
  of `state_edges_1936.tsv` (ledger-only rows stay distinguishable — see
  `source_scope`/`consistency` below). Sorted by `(me_id, vanilla_id)`.
- `state_mask_audit_1936_entities.tsv` — one row per `(entity_side,
  entity_id)`: each ME state and each vanilla state summarised over its
  partner set. Sorted by `(entity_side, entity_id)`.
- `state_mask_audit_1936_summary.json` — fixed schema
  `state_mask_audit_1936_summary/v1`: `inputs`, `registration` (the frozen
  affine), `gates` (the echoed thresholds), `counts`, `entities`,
  `owners`, `rendered_overlays`, plus `schema`/`kind`/`cutoff`.

Registration is fixed to the same candidate-independent **bbox coast fit**
(`fit_coast_warp(..., initial="bbox")`, built from the `definition.csv`
land/sea silhouette only) that the edge ledgers and geography evidence use.
The state masks under audit never participate in the registration: the 2x3
affine is frozen before any mask is compared, a non-finite or unavailable
affine fails closed, and no `vp`/`auto` initialisation, VP anchors,
candidate-conditioned warp, or per-pair refit is reachable from this layer.

**Two native frames, never merged.** The forward census counts each ME
mask's pixels on the vanilla raster in the ME-native frame (the ME mask is
exact, the vanilla side is sampled); the reverse census counts each vanilla
mask's pixels on the ME raster in the vanilla-native frame (the vanilla mask
is exact, the ME side is sampled). Nearest-neighbour pixel-centre sampling
is not bijective between the two grids, so the two directions measure
different sets — they are reported in separate `me_*` (forward) and `v_*`
(reverse) columns and are never averaged or merged. Denominators stay
explicit on both sides: forward shares divide by the whole ME land area
(outside-frame, water, and unassigned misses reduce coverage and are never
renormalised away); the reverse side keeps the true native area and the
sampled/observable portion as separate columns
(`v_sampled_px + v_unreached_px = v_native_px`).

**Classification sees pixel counts only.** `pair_class` is a fixed ladder:
`no_sampled_overlap` when neither direction records a positive hit;
`mutual_contained` when both directional native shares reach
`CONTAINMENT_SHARE` (0.90); `me_contained_in_vanilla` /
`vanilla_contained_in_me` when only the forward / only the reverse native
share does; `partial_overlap` when both shares reach `PARTIAL_SHARE`
(0.20) without containment; and `sliver_overlap` for any remaining positive
hit. Both containment shares are **native** denominators (`me_share` =
`me_hit_px`/`me_land_px`, `v_share_native` = `v_hit_px`/`v_native_px`) —
the sampled `me_registered_share`/`v_share_sampled` variants stay
diagnostic-only and never enter the ladder. `pair_reason` records the
codes in a fixed documented order, `|`-joined (geometry reasons first —
`no_bidirectional_hit`, `forward_only_hit`, `reverse_only_hit`,
`*_below_partial`, `*_below_containment`, `mutual_containment` — then the
caller-appended diagnostics `me_not_sampled`, `v_not_sampled`,
`geometry_not_usable`, `cross_owner`, `unknown_owner`). `pair_claim` is
`sampled_masks` iff either direction hit, else `withheld` — the only two
claim values. The synthetic MAP/`Map Border` rows stay marked
`edge_class=excluded` (carried from the ledger or recomputed for
census-only pairs) and are never candidate relations.
`entity_class` aggregates each state's partner set per side:
`unmatched` (no partners), `contained` (a usable mask whose top partner
native share reaches `CONTAINMENT_SHARE`), `split` (two or more partners
below containment — the geometric analogue of the canonical `1:many`
split), and `ambiguous` for the rest.

**1936 owner is a parallel filter field, never a gate.** `vanilla_owner`/
`me_owner`/`owner_relation`/`country_scope` are resolved with the same
`state_owner_at` rules as `state_edges_1936.tsv` (undated `owner`, then
dated `Y.M.D[.H]` blocks <= the `--cutoff`, default 1936.1.1; owner only,
never core/controller). They scope rows for reading and drive the optional
owner overlay, but `pair_class`/`entity_class` and every geometry column
are computed identically for `same_owner`, `cross_owner`, and
`unknown_owner` rows — owner strings appear in `pair_reason` only as the
appended `cross_owner`/`unknown_owner` diagnostics, and two rows that
differ only in owners carry identical geometry. Carried ledger columns
(`source_scope`, `edge_source`, `evidence_families`, `is_canonical`,
`canonical_ancestor`, `edge_class`, `comment_votes`, `manual_kind`) are
verbatim context joined from the `--edges` ledger (default
`state_edges_1936.tsv`; the ledger is required — a missing or malformed
file fails closed). `consistency` records only whether that context
agrees with the sampled geometry —
`canonical_geometry_agree`/`canonical_geometry_partial`/
`canonical_geometry_conflict` for canonical rows,
`ledger_geometry_hit`/`ledger_no_raster_hit` for other carried rows, and
`raster_only`/`raster_only_no_hit` for census-only pairs — and never
rewrites any table.

**This is a sampled-mask audit, not a complete-subset proof.** No row can
claim `proven`/`complete_subset`/`exact`/`identical` status — the claim
vocabulary is `sampled_masks` and `withheld` only. A high containment share
proves sampled coverage under the frozen coast affine, exactly the
`state_edges.tsv` contract; proving complete subset would still require an
independent province-level crosswalk the toolchain does not produce.

Optional owner overlays (default: none). `--render-dir DIR` together with
one or more repeated `--render-owner TAG` flags (`--render-owner` requires
`--render-dir`) writes one `<TAG>_owner_overlay_me_frame.png` per owner
into `DIR` — meant for a gitignored diagnostic area such as
`tmp_diag2/state_masks/`, never into `docs/states/`. Each PNG shows the
owner's union mask in the **ME-native frame**: same-owner ME-state land
versus the same-owner vanilla union resampled by the frozen affine —
overlap green `(0,200,0)`, ME-only red `(220,0,0)`, vanilla-only blue
`(0,80,255)`, neither black. The overlays are a human-review aid
rasterised after classification; they never feed `pair_class`,
`entity_class`, or any table, and an owner tag with no states on either
side fails closed rather than emitting an empty image.


## Mapping confirmation (95% gate)

`tools/build_state_mapping_confirmation.py` is a read-only scoring layer that
grades the canonical `m2v`/`v2m` partition against the sampled-mask audit, the
1936 owner-constrained edge ledger `state_edges_1936.tsv`, and the
migration-comment/manual identity evidence. It never edits, regenerates,
or "corrects" `v2m_states.tsv`, `m2v_states.tsv`, `state_edges*.tsv`, or any
`state_relations.py` view, and nothing downstream reads its outputs back into
those tables — a low score changes nothing in the canonical tables; it only
sorts the pair into `state_mapping_review_queue_95.tsv` for a human.

Regenerate from the existing snapshot (all inputs are the generated tables,
never the game or mod trees directly; `state_edges_1936.tsv` is a required
input — run both edge builders and the mask audit first, see the regeneration
chain at the top of this file):

```bash
python tools/build_state_mapping_confirmation.py
```

Outputs: three TSV tables plus one JSON summary —
`state_mapping_confirmation.tsv` (one row per `(me_id, vanilla_id)` pair in
the mask-audit ledger, canonical and candidate rows alike),
`state_mapping_review_queue_95.tsv` (every `review_candidate` row plus its
deterministic `review_priority` tier — this is the human worklist),
`state_mapping_confirmation_refs.tsv` (every `focus_state_refs` row with
confirmation context appended), and
`state_mapping_confirmation_summary.json` (fixed schema
`state_mapping_confirmation_summary/v2`). Deterministic: LF endings,
six-decimal ratios, fixed column and reason order, input SHA-256 hashes, no
timestamps; a rerun over unchanged inputs is byte-identical.

**The 0.95 gate is a conservative owner-scoped coverage threshold, not a
probability — and `confirmed_95` is a per-pair verdict, never a
complete-subset proof.** `CONFIRMATION_THRESHOLD = 0.95` is a fixed constant
with no CLI override. It does not express "95% confidence this mapping is
right": it is a fail-closed pair gate. `confirmed_95` on `(me, vanilla)`
states only that this specific canonical pair clears every gate — it says
nothing about whether the ME state's land is a complete subset of the
vanilla state, and no row anywhere in this contract may claim that (the
ledger ships zero proven rows by design). Below the line the mapping is not
wrong — it is `review_candidate` and lands in the review queue. Nothing
below 0.95 is silently accepted and nothing writes back anywhere: humans
adjudicate queue rows, then the canonical generator's
`MANUAL`/`MANUAL_ME2V` is edited by hand (the same discipline as the
auto-repair approvals flow).

`confidence_score = min(evidence_cap, me_owner_share,
owner_family_reverse_share)`; a missing metric counts as 0. All coverage
terms are **owner-scoped**: they read `state_edges_1936.tsv`'s same-owner
census columns with their explicit denominators, never the global mask
shares. The global-census `me_share`/`me_registered_share` and the
canonical-family `family_*` metrics remain on the row as diagnostics only —
they annotate, they never gate.

- `me_owner_share` — the member's own inward coverage inside its 1936
  owner's borders: the pair's forward sampled hits divided by all of the ME
  state's land pixels that sampled onto same-owner vanilla states
  (`me_owner_match_px`/`me_owner_area_px`, emitted only on `same_owner`
  rows with a positive owner denominator). Water/outside/unassigned mass
  stays in separate columns, never renormalised in.
- `owner_family_reverse_share` — the **owner relation family's** union
  reverse coverage of this vanilla state: the sum of live members' reverse
  hits on the pair's vanilla state (`owner_family_reverse_match_px`)
  divided by the vanilla state's same-owner reverse mass
  (`v_owner_area_px`). The reverse census maps every vanilla-native pixel
  to at most one ME state, so members' reverse hits are mutually exclusive
  and the union is a plain sum — never a max, never a merge of
  denominators. This is coverage of the *observed same-owner mass*, not a
  complete-subset proof.
- `owner_family_min_me_owner_share` — weakest live owner-family member's
  `me_owner_share`; diagnostic only (may be blank), not a gate.
- `evidence_cap` — the pair's identity-evidence ceiling: explicit
  manual/comment identity (`manual`, `comment`, `comment(member)` basis,
  nonzero `comment_votes`, `manual_kind`, or a comment/manual evidence
  family) caps at 1.00; pixel/vp-only evidence caps at 0.80, name-only at
  0.65, everything else at 0.50 — positioning evidence alone can never
  reach `confirmed_95`, because sampled pixel overlap is coverage, not
  identity proof.

**The owner relation family.** Per vanilla state, the family is exactly the
set of same-owner partner rows in `state_edges_1936.tsv` whose
`source_scope` is `compatibility` or `asserted` — membership is scope-only.
`observation`-scope edges — raster/candidate heuristics — **never join the
family**: they can extend a candidate list for a human but cannot vote
coverage for the gate. Cross-owner and `excluded` rows are likewise never
members, and a canonical row carrying a non-family scope is malformed input
that fails the build closed (canonical rows always carry `compatibility`
scope in practice). Synthetic MAP/"Map Border" members are counted in
`owner_family_member_count`/`owner_family_me_ids` for visibility but are
excluded from the live count, the union share, and the member minimum.

Status vocabulary: `confirmed_95` (canonical pair only, non-synthetic ME
state, `same_owner`, `evidence_cap` 1.00, usable owner denominators,
`me_owner_share ≥ 0.95`, `owner_family_reverse_share ≥ 0.95`, positive
`me_owner_area_px` and `v_owner_area_px`, no identity-risk
audit reason, no comment edge citing a different ancestor, and no
synthetic member in the canonical or the owner family), `review_candidate`
(anything else — including every noncanonical candidate row, however high
its coverage), and `excluded_synthetic` (MAP-owner/"Map Border" synthetic
rows: reported with `confidence_gate=excluded`, never counted in live
family denominators, never a candidate). `confirmed_95` can never appear
on a noncanonical row.

Audit signals split by blocking power: only an identity-risk
`mapping_audit` reason blocks the gate. `context_only` and
`clear_correction` classes never block, and the non-blocking reason codes
(`vp-absence-gap`, `region-contradiction`, `raw-geography-weak`,
`overlap-weak`, `comment(member)`, `explicit-independent-correction`)
annotate the row without disqualifying it — including a canonical
`comment(member)` basis, which is context, not a conflict. What still
blocks: `review_required` rows carrying an identity-risk reason, a comment
edge citing a different ancestor (`canonical_comment_conflict`), missing
explicit identity evidence, `missing_owner_edge`, and a synthetic member
in either family.

Owner, region, and focus-reference fields are gating/annotation context:
1936 owners decide which census columns are read and which partner edges
join the owner family, and `same_owner` is a hard gate — but owner
agreement itself is never identity evidence (a `cross_owner` or
`unknown_owner` pair can never confirm). Region agreement is context
(region tables are derived from the partition itself); focus/project
reference coverage is a usage fact appended in
`state_mapping_confirmation_refs.tsv` —
`confirmed_target`/`review_target`/`excluded_target`/`unresolved` there
describes what the corpus references resolve to, never whether the mapping
is right. Reason codes are emitted `|`-joined in a fixed documented order
so summaries and the queue sort deterministically; the legacy coverage
codes (`me_share_below_95`, `family_reverse_share_below_95`,
`family_member_below_95`) remain in that order as diagnostics, while the
gating codes are `me_owner_share_below_95`,
`owner_family_reverse_share_below_95`, `owner_denominator_unusable`, and
`missing_owner_edge`. Diagnostics never gate: a `confirmed_95` row may still
carry diagnostic reason annotations (e.g. `family_member_below_95`) when a
legacy metric dips below 0.95 — intended v2 semantics.

This layer is the current per-pair coverage-gate contract, not a final
relation map: a broader owner-aware relation-map layer is expected to
build on these ledgers, so treat v2 semantics as scoped to this gate.

## State relation map

`tools/build_state_relation_map.py` builds a **separate** relation-map
layer. It reads the canonical tables' semantic artifacts only —
`mapping_comment_edges.tsv`, `focus_state_refs.tsv`, and the literal
`MANUAL`/`MANUAL_ME2V` dicts parsed (never executed) out of
`tools/build_state_map.py` — plus its own raster census; the canonical
`m2v`/`v2m` partition is not an input and is never rewritten. Run it
after `build_state_map.py` and `build_focus_state_refs.py` and before
the confirmation layer in the regeneration chain above:

```bash
python tools/build_state_relation_map.py          # all seven outputs, natural correction on
python tools/build_state_relation_map.py --no-natural   # ablation diagnostic only — see below
```

Outputs (fixed column order, `|`-joined sets, six-decimal ratios, LF
endings, no timestamps; a rerun over unchanged inputs is byte-identical):

- `state_relation_edges.tsv` — one row per `(me_id, vanilla_id)` edge
  with a positive sampled cell: hit counts, `forward_share` and the
  bounded `reverse_share` (unique confident target pixels per component;
  explicit `reverse_hits_px`/native denominator; a
  `reverse_overlap_possible` flag marks component-union overcount), the
  zero-bin split (`outside_px`/`water_px`/`unassigned_px`), a deterministic
  relation class (`one_to_one`/`one_to_many`/`many_to_one`/
  `many_to_many`/`uncertain`), `claim=sampled_area_overlap`, and
  `complete_subset=unproven` on every row.
- `state_relation_identity.tsv` — semantic identity candidates,
  `claim=semantic_identity_candidate`: `comment` edges from
  `mapping_comment_edges.tsv`, `manual_member`/`manual_direct` rows from
  the parsed manual dicts (these three are `explicit`), plus weak
  `name`/`same_id_name` unique normalized-name matches. Never derived
  from raster output.
- `state_relation_substitution.tsv` — occurrence-level script/focus
  compatibility edges (`focus_state_refs` occurrences plus aggregated
  comment claims), `claim=script_substitution`.
- `state_relation_views.tsv` / `state_relation_v2m.tsv` — the derived
  compatibility views described below, `claim=compatibility_view`.
- `state_relation_registration.json` — fixed schema
  `state_relation_registration/v1`, `claim=sampled_boundary_geometry`:
  input SHA-256 hashes, parameters, the global fit result, and every
  ME owner-component's status, bounded local metrics, optional natural
  correction, fallback reasons, and bbox/area/centroid.
- `state_relation_summary.json` — fixed schema
  `state_relation_summary/v1`: the claim contract (with
  `complete_subset_proof: unavailable`), count histograms, and the
  `diagnostics` block (`unreachable_identity_pairs`,
  `unreachable_substitution_pairs`, `component_outliers`,
  `cross_owner_pairs`, `global_refinement_status`).

**Three meanings, never conflated.** `area_parent` is topology/area
overlap: under the topology registration every source pixel is classified
exactly once — `confident` (positive target id outside the ±2 px
`--border-radius` vanilla border band, which models vanilla-boundary
registration uncertainty), `uncertain` (inside the band), `water`,
`unassigned`, or `outside`. Confident and uncertain hits accumulate per
`(source, target)` cell; confident cells of at least `--min-relation-px`
pixels (64) drive the significant relation classes. The full k:n matrix —
forward and reverse — is retained in `state_relation_edges.tsv` for
inspection; no cell is collapsed to a winner-take-all vote. `identity` is
semantic candidacy only: manual/comment/name evidence, never raster.
`substitution` is script/focus compatibility, never identity.

**Derived compatibility views are convenience joins.** Precedence: a
unique high-confidence area-parent candidate (significant, component
status ok, confident share of the native area ≥ 0.95, uncertain share
≤ 0.05), then a unique explicit identity, then a unique substitution.
Any ambiguity or absence leaves `derived_v_ancestor` empty with
`status=review`/`unmapped`; weak name-only candidates promote an
`unmapped` state to `review` but never derive an ancestor. These views
are a conservative way to *read* the relation map — not an adjudication
and not a complete-subset proof. The old canonical tables remain
untouched until an explicit cutover.

**Registration claims — and no proof.** Every transform is
topology-only: the global fit (bbox-initialized coast affine, then
mutual-nearest-neighbour affine ICP against the union of
country/coast/lake boundary classes, each class matched only against
itself) and each ME owner-connected component's bounded local refit
(singular values pinned near the global fit) against its nearest
same-1936-owner vanilla component. The optional river/lake
natural-feature correction composes on top when powered. Owner tags are
a physical partition only — never an identity claim — and no VP, name,
comment, or canonical input enters any transform. No row asserts a
`proven` or complete-subset relation: the tool does produce a sampled
province-raster crosswalk, but proving an exhaustive administrative
complete subset would still need an independent province-level
correspondence that accounts for every province and border exception.

**Diagnostics, not rejections.** Component statuses are `registered`,
`global_fallback` (local fit under-powered or out of bounds — the
component keeps the global affine and records a reason),
`unmatched_component` (no same-owner counterpart inside the select cap),
and `excluded_inset` (the mapped footprint lands on
foreign/unowned/non-land pixels — out-of-scope territory such as an
enclave). Edge `diagnostic_flags` surface `local_fallback`,
`natural_fallback`, `high_residual`, `cross_owner`, `uncertain_only`,
`uncertain_dominant`, and the unmatched/excluded statuses. Edges whose
component is `unmatched_component` or `excluded_inset` cannot drive the
derived area-parent tier but stay in the table for inspection.

`--no-natural` is a diagnostic ablation, not final acceptance: it skips
the river/lake natural-feature correction so the affine-only fit can be
inspected and its deltas compared. The delivered build always runs with
the correction enabled.

**Human review boundary.** Nothing here is a certified full mapping:
the derived views are convenience joins and every row is `unproven`. The
authoritative human-review worklist remains
`state_mapping_review_queue_95.tsv` plus the review page described under
"Mapping confirmation"; identity and substitution candidates beyond the
derived views are for manual adjudication — the canonical generator's
`MANUAL`/`MANUAL_ME2V` is edited by hand, the same discipline as the
auto-repair approvals flow.


## Files

| file | rows | grain | direction |
|---|---|---|---|
| `v2m_states.tsv` | 1081 | vanilla state → ME descendants | forward |
| `m2v_states.tsv` | 2986 | ME state → vanilla ancestor | backward |
| `regions.tsv` | 253 | ME strategic region → modal vanilla region | derived |
| `vps.tsv` | 1533 | vanilla VP → ME VP | derived |
| `registration_anchors.tsv` | 560 | cleaned VP control points | ME → vanilla warp |
| `review_queue.tsv` | 4 | low-confidence ME targets → candidate vanilla ancestors | review only |
| `state_mapping_confirmation.tsv` | generated | `(me_id, vanilla_id)` mask pair → gate/confidence | confirmation |
| `state_mapping_review_queue_95.tsv` | generated | `review_candidate` pair + `review_priority` | review only |
| `state_mapping_confirmation_refs.tsv` | generated | project state reference + confirmation context | confirmation |
| `state_mapping_confirmation_summary.json` | — | fixed schema `state_mapping_confirmation_summary/v2` | confirmation |
| `mapping_evidence.tsv` | 2986 | ME state → raw mapping evidence | evidence |
| `mapping_comment_edges.tsv` | generated | every `(vanilla_id, me_id)` was-vanilla comment edge | evidence |
| `mapping_audit.tsv` | 2986 | ME state → conservative risk audit | audit |
| `mapping_audit_summary.json` | — | audit counts and reason-code summary | audit |
| `auto_repair_proposals.tsv` | generated | ME state → safe repair or final review decision | proposal |
| `auto_repair_summary.json` | — | proposal counts, IDs, skipped reasons, input/source hashes | proposal |
| `auto_repair_approvals.tsv` | human | ME state → human-approved ancestor + mandatory note | approval |
| `focus_state_refs.tsv` | generated | one focus-corpus state reference per row | derived |
| `focus_state_map.tsv` | generated | vanilla state (used by vanilla foci) → coverage | derived |
| `state_edges.tsv` | generated | one `(me_id, vanilla_id)` edge | relation evidence ledger |
| `state_edges_1936.tsv` | generated | one `(me_id, vanilla_id)` edge | 1936 owner-constrained fitting ledger |
| `state_edges_1936_summary.tsv` | generated | one owner tag | owner-side census totals and scope counts |
| `state_edges_1936_summary.json` | — | fixed schema v1 | inputs, row/scope counts, unknown-owner states |
| `state_mask_audit_1936.tsv` | generated | one `(me_id, vanilla_id)` mask pair | sampled-mask audit |
| `state_mask_audit_1936_entities.tsv` | generated | one `(entity_side, entity_id)` | sampled-mask audit |
| `state_mask_audit_1936_summary.json` | — | fixed schema `state_mask_audit_1936_summary/v1` | audit |
| `state_relation_edges.tsv` | generated | one `(me_id, vanilla_id)` sampled edge | relation map — area overlap |
| `state_relation_identity.tsv` | generated | one `(vanilla_id, me_id, source)` candidate | relation map — semantic identity |
| `state_relation_substitution.tsv` | generated | one script/focus occurrence edge | relation map — substitution |
| `state_relation_views.tsv` | generated | one ME state → derived ancestor | relation map — derived view |
| `state_relation_v2m.tsv` | generated | one vanilla state → derived members | relation map — derived view |
| `state_relation_registration.json` | — | fixed schema `state_relation_registration/v1` | topology registration + claims |
| `state_relation_summary.json` | — | fixed schema `state_relation_summary/v1` | counts, claims, diagnostics |

Columns:

- `v2m_states.tsv` — `vanilla_id, vanilla_name, owner, v_prov_count, region, me_primary, me_members, me_names, basis, status`
  (`me_members` is `|`-joined and exhaustive; `me_primary` is the strongest-evidence member)
- `m2v_states.tsv` — `me_id, me_name, owner, prov_count, region, cat, manpower, v_ancestor, v_name, basis, status`
- `regions.tsv` — `me_region, me_name, v_region, v_name, status`
- `vps.tsv` — `v_state, v_state_name, v_prov, v_pts, vp_name, me_state, me_prov, me_pts, me_name, status`
- `registration_anchors.tsv` — `v_prov, v_state, me_prov, me_state`; the exact cleaned control set used by the pixel census and verifier.
- `mapping_evidence.tsv` — fixed 31-column raw evidence schema: `me_id, me_name, owner, me_region, ancestor, ancestor_name, basis, status, manual_override, pixel_best, pixel_share, pixel_runner, pixel_runner_share, pixel_margin, comment_best, comment_votes, comment_runner, comment_runner_votes, comment_margin, name_norm, name_exact_me_ids, name_collision_count, vp_best, vp_votes, vp_runner, vp_runner_votes, vp_margin, me_core_tags, ancestor_core_tags, me_vp_count, ancestor_vp_count`. Candidate IDs and tags are `|`-joined where multiple values are present; unavailable values are empty. Pixel shares/margin are area-weighted fractions; comment/VP margins are top-minus-runner vote counts.
- `mapping_comment_edges.tsv` — `vanilla_id, me_id, comment_votes` (LF line
  endings). One row per observed vanilla→ME comment edge: unlike the canonical
  vote tables it records EVERY vanilla ID in a `was vanilla` set
  (`42/51/978`, `45,764`, `{291 1011 676}`), each occurrence counting one vote,
  sorted by `(vanilla_id, me_id)`. Evidence only — canonical adjudication still
  follows the first-ID rule; pairs whose ends are not both real states fall
  into `province_pairs.json`.
- `mapping_audit.tsv` — conservative audit rows with reason codes and `review_required`/`context_only`/`clear_correction` classification; `context_only` records weak signals without escalating them to a review verdict. It never edits canonical mappings.
- `auto_repair_proposals.tsv` — fixed schema: `me_id, me_name, current_ancestor, current_basis, proposed_ancestor, decision, confidence, evidence_families, reason_codes, evidence_summary`. An empty proposed ancestor means no unique candidate; it is never resolved by choosing the lower ID in a tie.
- `auto_repair_summary.json` — versioned proposal hash manifest and decision counts; after apply, also the applied/approved IDs, override reasons, backup path (only if edited), and post-edit source hash.
- `auto_repair_approvals.tsv` — hand-authored, never generated: fixed columns `me_id, approved_ancestor, review_note`; absent or header-only means no approvals. It only marks `review` proposals for application (see “Human approval of conservative repairs”); the proposal TSV itself is never edited.
- `state_edges.tsv` — generated by `tools/build_state_edges.py`; the
  relation **evidence ledger** between ME states and vanilla states: every
  observed raster intersection, the canonical ancestor edge, and every
  top-level evidence candidate, each classified by `relation_scope`. One row
  per `(me_id, vanilla_id)` pair, sorted by `(me_id, vanilla_id)`; ratios
  are six decimals; empty values are empty strings. The ledger records
  evidence and assertions, NOT relation truth: raster overlap can prove only
  sampled pixel overlap/coverage, never that an ME state is a complete
  subset of a vanilla state. Secondary edges never rewrite the
  `v_ancestor`/`me_members` partition — this table is a strict superset used
  for scope-filtered relation queries and soft audit expansion, never for
  canonical membership.
  Columns: `vanilla_id, vanilla_name, me_id, me_name, edge_source,
  evidence_families, is_canonical, canonical_ancestor,
  canonical_primary_of_vanilla, me_area_px, me_match_px, me_share, me_rank,
  me_modal_vanilla, me_modal_share, me_modal_margin, me_assigned_share,
  me_outside_px, me_water_px, me_unassigned_px, me_components,
  relation_scope, edge_class, geometry_usable, v_native_area_px,
  v_observable_px, v_match_px,
  v_share_observable, v_share_native, v_rank, comment_votes, vp_votes,
  name_exact, same_id, manual_kind`.
  `me_*` columns describe the forward coast-affine census (this ME state's
  land pixels sampled onto vanilla states): `me_area_px` is the land pixel
  count, `me_match_px`/`me_share`/`me_rank` the edge's hit count/share/rank,
  `me_modal_*` the state's top candidate, `me_assigned_share` the share
  landing on any vanilla state, and `me_outside/water/unassigned_px` the
  zero-bin split. `v_*` columns mirror it in the vanilla frame under the
  inverse affine: `v_native_area_px` is the true land area in the vanilla
  raster, `v_observable_px` the portion reachable through the ME frame, and
  `v_share_observable`/`v_share_native` divide `v_match_px` by each.
  `geometry_usable=1` iff `me_area_px>=64` and `me_assigned_share>=0.90`.
- `state_edges_1936.tsv` — generated by `tools/build_state_edges_1936.py`;
  the 1936.1.1 owner-constrained view of the same edge union
  (`state_edges.tsv` rows UNION the live raster census, both directions).
  Each side's `owner` is resolved from `history/states` (undated `owner`,
  then dated `Y.M.D[.H]` blocks <= 1936.1.1 in ascending order; `owner` is
  the state owner, never core/controller). `owner_relation` is
  `same_owner`/`cross_owner`/`unknown_owner`; `candidate_scope` is
  `within_country` only for `same_owner`, else
  `cross_country_diagnostic`/`unknown_owner`. `me_owner_*`/`v_owner_*`
  repeat the census columns inside the same-owner partner subset with
  explicit denominators (never renormalised global shares), and
  `*_rank_global`/`*_rank_owner` rank partners by `(-px, id)` inside the
  global and same-owner sets. `source_scope` carries the edge table's
  `relation_scope`. This is an observation/fitting ledger for scoping
  candidates inside a country's borders — NOT a complete-subset proof —
  and it supplies the confirmation layer's owner-scoped metrics: its
  same-owner `compatibility`/`asserted` rows define the owner relation
  family and its `me_owner_*`/`v_owner_*` census columns define the gate
  denominators.
  Companion `state_edges_1936_summary.tsv` (per owner tag: state counts,
  land px, same-owner sampled px/share, row/scope counts incl. canonical
  cross-owner edges, same/cross partner id lists) and
  `state_edges_1936_summary.json` (fixed schema
  `state_edges_1936_summary/v1`: inputs, counts, unknown-owner state lists).
- `state_mask_audit_1936.tsv` — generated by `tools/audit_state_masks_1936.py`;
  the sampled-mask audit pair table (`state_edges_1936.tsv` rows UNION the
  live raster census, both directions), sorted by `(me_id, vanilla_id)`.
  Identity/owner columns `vanilla_id, vanilla_name, vanilla_owner, me_id,
  me_name, me_owner, owner_relation, country_scope` mirror the 1936
  ledger's resolution; forward `me_*` columns `me_land_px, me_outside_px,
  me_water_px, me_unassigned_px, me_assigned_px, me_hit_px, me_share,
  me_registered_share, me_spill_px, me_rank, me_modal_vanilla,
  me_modal_share, me_modal_margin` report the ME-native-frame census
  (`me_share` = `me_hit_px`/`me_land_px` over the whole land area;
  `me_registered_share` = `me_hit_px`/(`me_land_px` − `me_outside_px`);
  `me_spill_px` = `me_assigned_px` − `me_hit_px`; `me_rank`/`me_modal_*`
  rank partners by `(-px, id)`, margin = top − runner share).
  Reverse `v_*` columns `v_native_px, v_sampled_px, v_unreached_px,
  v_hit_px, v_share_native, v_share_sampled` mirror it in the
  vanilla-native frame (`v_unreached_px` = `v_native_px` −
  `v_sampled_px`; `v_share_native`/`v_share_sampled` divide `v_hit_px` by
  each denominator). `pair_class`, `pair_reason`, `pair_claim` carry the
  classification; `me_components`, `me_largest_share`,
  `me_largest_hit_share`, `components_agree`, `geometry_usable` describe
  the ME mask's 4-connected components and usability gate. The
  ledger-context block `source_scope, edge_source, evidence_families,
  is_canonical, canonical_ancestor, edge_class, comment_votes,
  manual_kind, consistency` is verbatim context joined from
  `state_edges_1936.tsv` and never participates in classification.
- `state_mask_audit_1936_entities.tsv` — one row per `(entity_side,
  entity_id)`, sorted likewise: `entity_side` (`me`/`vanilla`),
  `entity_id`, `entity_name`, `entity_owner`, `land_px`, `assigned_px`,
  `assigned_share`, `geometry_usable`, `partner_count`, `top_partner`,
  `top_partner_px`, `top_share`, `runner_share`, `entity_class`,
  `entity_reason`, `same_owner_partner_count`,
  `cross_owner_partner_count`. ME-side `top_share` uses `me_share`,
  vanilla-side uses `v_share_native`; empty partner sets leave the
  partner fields blank.
- `state_mask_audit_1936_summary.json` — fixed schema
  `state_mask_audit_1936_summary/v1`: `schema`, `kind`, `cutoff`,
  `inputs`, `registration` (frozen affine, determinant, match count,
  symmetric-p95 chamfer), `gates` (the echoed `min_area_px` = 64,
  `containment_share` = 0.90, `partial_share` = 0.20,
  `min_assigned_share` = 0.90 and the `geometry_usable` rule), `counts`
  (row/ledger split plus `pair_class`/`pair_claim`/`owner_relation`/
  `country_scope`/`consistency` histograms), `entities` (per-side row and
  `entity_class` counts), `owners` (per-tag state/scope tallies),
  `rendered_overlays` (written PNG names, normally empty); `sort_keys`,
  no timestamps.

- `state_mapping_confirmation.tsv` — generated by
  `tools/build_state_mapping_confirmation.py`; one row per
  `(me_id, vanilla_id)` mask-audit pair, canonical and candidate rows
  alike, sorted by `(me_id, vanilla_id)`. Columns: identity/context
  (`me_id, me_name, me_owner, vanilla_id, vanilla_name, vanilla_owner,
  relation_kind, relation_status, source_scope, edge_source,
  evidence_families, is_canonical, canonical_basis, canonical_status,
  owner_relation, country_scope, edge_class`), carried mask figures
  (`me_share, me_registered_share, me_geometry_usable, v_hit_px,
  v_native_px, v_share_native`), canonical-family diagnostics
  (`family_member_count, family_me_ids, family_live_member_count,
  family_has_synthetic_member, family_min_me_share,
  family_reverse_hit_px, family_reverse_native_px, family_reverse_share`),
  owner-scoped metrics joined from `state_edges_1936.tsv`
  (`me_owner_area_px, me_owner_match_px, me_owner_share,
  v_owner_area_px, v_owner_match_px, v_owner_share,
  owner_family_member_count, owner_family_me_ids,
  owner_family_live_member_count, owner_family_has_synthetic_member,
  owner_family_min_me_owner_share, owner_family_reverse_match_px,
  owner_family_reverse_share`), audit context
  (`mapping_audit_class, mapping_audit_reason_codes,
  comment_votes, explicit_identity_evidence`), and the gate block
  (`evidence_cap, confidence_score, confidence_gate, confidence_reasons,
  project_ref_count, project_review_ref_count`). `relation_status` is
  `confirmed_95`/`review_candidate`/`excluded_synthetic`;
  `confidence_gate` is `pass`/`fail`/`excluded`;
  `confidence_reasons` is `|`-joined in fixed `REASON_CODES` order. The
  owner-family columns define the gate; the carried mask and
  canonical-family columns are diagnostics only.
- `state_mapping_review_queue_95.tsv` — the same columns plus
  `review_priority`, restricted to `review_candidate` rows and sorted by
  `(review_priority, me_id, vanilla_id)`: canonical+explicit first, then
  canonical other, candidate explicit, candidate other. This is the human
  review worklist; the confirmation layer never edits `review_queue.tsv`.
- `state_mapping_confirmation_refs.tsv` — every `focus_state_refs.tsv`
  row in original order with six appended columns:
  `resolved_me_id` (canonical members covering the reference),
  `resolved_vanilla_id`, `relation_status` (member statuses, `|`-joined),
  `confidence_score` (minimum across resolved pairs),
  `reference_status` (`confirmed_target`/`review_target`/
  `excluded_target`/`unresolved`), and `review_reason`. These describe
  what the corpus *uses*; they never feed the confidence gate.
- `state_mapping_confirmation_summary.json` — fixed schema
  `state_mapping_confirmation_summary/v2`: `schema`, `kind` (the
  "conservative gate thresholds, not calibrated probabilities — and no
  complete-subset proof" disclaimer), `threshold_contract` (echoed
  `confirmation_threshold` = 0.95, the `confidence_score` formula
  `min(evidence_cap, me_owner_share, owner_family_reverse_share)`,
  `evidence_caps`, the `confirmed_95_requires` gate list, and
  `reason_code_order`), `inputs` (`m2v_states`, `v2m_states`,
  `mask_audit`, `state_edges_1936`, `mapping_audit`, `comment_edges`,
  `focus_refs`, each `{path, sha256, rows}` with repo-relative paths —
  consumers re-hash these against the live files, so a stale summary is
  detectable), and `counts` histograms (`pairs_total`,
  `by_relation_status`, `by_confidence_gate`, `by_owner_relation`,
  `by_source_scope`, `by_edge_class`, `by_reason_code`,
  `review_queue_rows`, `by_review_priority`, `vanilla_families_total`,
  `owner_families_total`, `families_with_synthetic_member`,
  `owner_families_with_synthetic_member`,
  `project_refs_total`, `project_refs_by_status`); `sort_keys`, no
  timestamps.

- `state_relation_edges.tsv` — generated by
  `tools/build_state_relation_map.py`; one row per `(me_id, vanilla_id)`
  edge with a positive sampled cell, sorted by `(me_id, vanilla_id)`:
  `me_id, me_name, me_owner_1936, me_component_ids, vanilla_id,
  vanilla_name, vanilla_owner_1936, relation, significant,
  confident_hits_px, uncertain_hits_px, source_area_px,
  source_sampled_px, forward_share, target_native_area_px,
  reverse_share, outside_px, water_px, unassigned_px,
  registration_statuses, registration_methods, claim, complete_subset,
  diagnostic_flags`. The full retained k:n matrix — no cell is a winner-
  take-all verdict; `forward_share` divides by `source_sampled_px` (the
  actually sampled mass) and `reverse_share` is a coverage estimate
  against the whole native target area.
- `state_relation_identity.tsv` — `vanilla_id, vanilla_name, me_id,
  me_name, source, evidence_strength, comment_votes, claim`; `source` is
  `comment`, `manual_member`, `manual_direct` (explicit) or `name`,
  `same_id_name` (weak). Semantic candidates only, never raster-derived.
- `state_relation_substitution.tsv` — `file, line, focus_id, block_path,
  construct, key, literal, me_id, vanilla_id, ref_kind, source,
  comment_vanilla_id, migration_comment, claim`; occurrence-level
  script/focus compatibility edges plus aggregated comment claims.
- `state_relation_views.tsv` — `me_id, me_name, me_owner_1936,
  area_parent_ids, identity_ids, substitution_ids, derived_v_ancestor,
  view_source, status, claim`; one row per ME state. `view_source` is
  `area_parent`/`identity`/`substitution`/`none` and `status` is
  `derived`/`review`/`unmapped`.
- `state_relation_v2m.tsv` — `vanilla_id, vanilla_name,
  vanilla_owner_1936, derived_members, primary_me_member, view_source,
  status, claim`; the reverse derived view.
- `state_relation_registration.json` — fixed schema
  `state_relation_registration/v1`: `claim` (`sampled_boundary_geometry`),
  `inputs` (roots, cutoff, per-file `{path, sha256}`), `parameters`,
  `global` (the stage-1 coast affine plus stage-2 mutual-NN refinement
  metrics including `refinement_status`), `components` (status,
  `registration_method`, bounded local metrics, optional natural
  correction and its fallback, `source_bbox_xyxy`/`source_area_px`/
  `source_centroid_xy`), and `notes` recording that every transform is
  topology-only and sampled overlap proves no subset.
- `state_relation_summary.json` — fixed schema
  `state_relation_summary/v1`: `claims` (with `complete_subset_proof:
  unavailable`), `counts` (state/component/edge/identity/substitution/
  view histograms, `cross_owner_edge_rows`, `focus_state_refs_present`),
  `diagnostics` (`unreachable_identity_pairs`,
  `unreachable_substitution_pairs`, `component_outliers`,
  `cross_owner_pairs`, `global_refinement_status`), and `outputs`; no
  timestamps.

## Status vocabulary

### v2m_states / m2v_states
- `1:1` — one ME descendant
  `1:many` — vanilla state subdivided; see `me_members` (all final adjudicated
  descendants) + `me_primary` (strongest-evidence member)
- `unmapped` — vanilla state absent (mostly off-map: BRA/CHI/JAP/RAJ/etc.)
- `mapped` — ME state claims a vanilla ancestor

### basis (provenance, strongest→weakest)
- `comment` — `# was vanilla N` migration comment (the canonical evidence)
- `comment(member)` — ME state reached the vanilla ancestor via a non-winning comment vote
- `manual` — entry in `MANUAL`/`MANUAL_ME2V` inside the generator (domain knowledge)
- `name` — exact normalized name match
- `name~` — unique longer ME prefix/suffix extension of a vanilla name (`Leningrad Area`→`Leningrad`)
- `vp-geo` — ME state's VP names modal-vote one vanilla state (Ajaccio→Corsica, Linz→Lower Austria)
- `pixel-geo` — spatial majority vote: ME province centroids reverse-warped into
  vanilla `provinces.bmp` land on this vanilla state (≥50% of member-province pixels)
- `pixel-geo-low` — same, <50% share (border/bisected state — review before citing)
- `same-id` — ME id == vanilla id AND names match (untouched state)

### regions.tsv
- `member-modal` — modal vanilla region among this region's member states' ancestors
- `name` — exact normalized region-name match
- `mod-new` — no ancestor

### vps.tsv
- `name` — normalized name match
- `near` — boundary-edit match (Roma↔Rome) proven inside the vanilla state's descendant set
- `cross-state` — name matched but the ME state is a different vanilla state's descendant — DO NOT trust
- `unmapped` — no candidate

### state_edges.tsv vocabulary

`edge_source` (alphabetical `|` join) records how the row was discovered:
`raster` (positive pixel intersection in either census), `canonical` (the
adjudicated `m2v` ancestor edge), `comment` (a `# was vanilla …` migration
comment — all set members via `mapping_comment_edges.tsv` when present, else
`comment_best`/`comment_runner`), `candidate` (top-level `mapping_evidence`
candidates: `pixel_best`/`pixel_runner`, `vp_best`/`vp_runner`,
`name_exact_me_ids`, `same_id`).

`evidence_families` (alphabetical `|` join) records which evidence channels
corroborate the pair: `canonical`, `comment`, `manual`, `name`, `pixel`,
`raster`, `same_id`, `vp`. `manual_kind` distinguishes `member` (MANUAL
vanilla-side member groups) from `direct` (MANUAL_ME2V per-state claims).

`relation_scope` (precedence order) records how much weight a consumer may
put on the relation itself — independent of the `edge_class` geometry
buckets:

- `excluded` — synthetic MAP/"Map Border" edges; never enter expansion.
- `compatibility` — the canonical edge (`is_canonical=1`): an existing
  compatibility assertion inherited from the adjudicated `m2v`/`v2m`
  primary views, NOT a mathematical complete-subset proof.
- `asserted` — non-canonical edge with an explicit human relation
  declaration: comment and/or manual provenance.
- `observation` — everything else: raster/candidate/pixel/vp/name/same_id
  heuristic evidence only. An observation edge records sampled
  overlap/coverage; images can never prove that an ME state is a complete
  subset of a vanilla state.

Only `compatibility` and `asserted` edges may drive relation expansion
(comment attribution, secondary-ancestor reasoning, default
`state_relations` queries); `observation` edges are stored for explicit
query only, never for automatic reasoning.

Complete-subset proof contract: no row may claim `proven`/
`complete_subset` status. Proving complete subset requires an independent
province-level (or equivalent administrative) crosswalk demonstrating that
the ME state's provinces cover the vanilla state entirely with no spill
into other vanilla states — a necessary prerequisite the current toolchain
does not produce, so the ledger intentionally ships zero proven rows.
Raster intersection rows remain observations regardless of how high their
shares are.

`edge_class` (precedence order):

- `excluded` — MAP-owner/"Map Border" ME rows; never enter member expansion.
- `identity_accepted` — the canonical `m2v` edge (`is_canonical=1`), even
  with no raster overlap; identity is adjudicated, not geometric.
- `significant` — non-canonical edge with `me_match_px>=64` forward hits.
- `backward_only` — no forward hits, but the reverse census sees this ME
  state covering `>=64` vanilla-side pixels.
- `sliver` — `0<me_match_px<64`: pixel dust only.
- `evidence_only` — no usable raster hits either direction; asserted solely
  by comment/pixel/vp/name/same_id/manual evidence.

### mapping confirmation vocabulary

`relation_status` — the per-pair verdict:

- `confirmed_95` — canonical pair passing every owner-scoped gate:
  non-synthetic ME state, `same_owner`, `evidence_cap` 1.00, usable owner
  denominators, finite `me_owner_share` ≥ 0.95 over a positive owner
  denominator, `owner_family_reverse_share` ≥ 0.95 over a positive vanilla
  owner denominator, no identity-risk audit reason (context-only and
  `comment(member)` context never block), no comment conflict, and no
  synthetic member in the canonical or owner family. A pair-level
  conservative gate result — coverage of observed same-owner mass, not a
  probability and not a complete-subset proof.
- `review_candidate` — anything else, including every noncanonical
  candidate row; lands in `state_mapping_review_queue_95.tsv`.
- `excluded_synthetic` — MAP-owner/"Map Border" synthetic rows; reported,
  never gated, and removed from family live denominators.

`confidence_gate` mirrors the same three buckets as
`pass`/`fail`/`excluded`. `reference_status` (refs table) aggregates the
pair statuses of the members a reference resolves to:
`confirmed_target` (all confirmed), `review_target` (any below),
`excluded_target` (any excluded member), `unresolved` (no resolvable
member). `review_priority` orders the queue 0→3: canonical+explicit,
canonical other, candidate explicit, candidate other.


## Invariants the table enforces

1. `v2m.me_members` and `m2v.v_ancestor` are symmetric — every member claims the same ancestor. That partition is the compatibility view only; `state_edges.tsv` is the long-edge ledger and its secondary edges never feed `me_members`. Only `compatibility`/`asserted`-scope edges may answer "is this a relation"; `observation` edges answer "what was sampled".
2. `manual` overrides stale comments; comment evidence precedes name/VP/pixel inference.
3. `1:many` member lists are exhaustive after ancestry adjudication; rejected comment candidates are not members.
4. A `cross-state` VP row is a red flag for loc/AI files using it.
5. The confirmation layer's owner relation family is built only from same-owner `compatibility`/`asserted`-scope rows of `state_edges_1936.tsv` — membership is scope-only and a canonical row carrying a non-family scope is malformed input; `observation` edges can never extend it. `confirmed_95` is pair-level coverage of observed same-owner mass — never a complete-subset proof and never a probability.

## Known intentional gaps

- 601 vanilla `unmapped` states — mostly off-map countries deliberately cut (`Unused.txt` tags).
- 0 ME `mod-new` states in the current generated table; every 2986 ME state has an ancestor.
- The legacy `auto_repair_proposals.tsv` report is separate from the current bidirectional 95% confirmation layer; `state_mapping_review_queue_95.tsv` is the authoritative human-review worklist and `state_mapping_confirmation_summary.json` the authoritative counts source for this contract — read the live numbers there, not in this README (they shift whenever the snapshot or the gate contract changes; e.g. the v2 build yielded 134 `confirmed_95` pairs out of 7,058).
- State-audit corrections beyond the low-confidence queue: ME 83 Oppland → vanilla 920 Opplandene, ME 106 Buskerud and 131 Vestfold → vanilla 921 Telemark, ME 2439 Kirov → vanilla 205 Kaluga, ME 2502 Tikhoretsk → vanilla 234 Krasnodar, and ME 2711 Gällivare → vanilla 666 Norrnorrland. Each combines candidate-independent natural coverage or explicit migration/state/VP evidence; the global page affine is not the identity proof.
- `cross-state` VPs — name collisions or VPs relocated between splits; review individually before citing.

## Maintenance

- Add to `MANUAL` when a split member can ONLY be found by domain knowledge
  (e.g. `77: [1579,818,767]` for Dobrudja's south-Bulgarian shard).
- If a `basis` proves wrong in-game, fix the generator's `MANUAL` first, never the TSV by hand.
- `province_pairs.json` collects `was vanilla N` pairs whose new-id isn't a state
  (province-level references) — source material for a future province-level table.
