# Map truth tables — vanilla 1.19 ↔ Magna Europa

Canonical vanilla↔ME identity tables for map content. **Single source of truth** for any
state/region/VP id question. Tracked in git (these files travel with the repo); regenerate
after every map-related change — never hand-edit a TSV.

Working copies used to live in the gitignored `_ctx/`; they were moved here (and the
generator from `tools/out/` to `tools/`) so the tables are versioned alongside the map data.

Regenerate:

```bash
python tools/build_state_map.py                 # from the repo root (or any cwd)
HOI4_DIR="C:/path/to/Hearts of Iron IV" python tools/build_state_map.py   # override vanilla dir
```

Inputs: `history/states/*.txt` (both sides), `map/strategicregions/*.txt`, all `localisation/english/*.yml`,
plus migration comments `# was vanilla N` scattered through `common/`, `events/`, `history/`.
Output: the four TSVs below + `province_pairs.json` (rejected province-level pairs), all written here.
A rerun over unchanged inputs is byte-identical.

## Files

| file | rows | grain | direction |
|---|---|---|---|
| `v2m_states.tsv` | 1081 | vanilla state → ME descendants | forward |
| `m2v_states.tsv` | 2985 | ME state → vanilla ancestor | backward |
| `regions.tsv` | 253 | ME strategic region → modal vanilla region | derived |
| `vps.tsv` | 1544 | vanilla VP → ME VP | derived |

Columns:

- `v2m_states.tsv` — `vanilla_id, vanilla_name, owner, v_prov_count, region, me_primary, me_members, me_names, basis, status`
  (`me_members` is `|`-joined, exhaustive; `me_primary` is the vote winner)
- `m2v_states.tsv` — `me_id, me_name, owner, prov_count, region, cat, manpower, v_ancestor, v_name, basis, status`
- `regions.tsv` — `me_region, me_name, v_region, v_name, status`
- `vps.tsv` — `v_state, v_state_name, v_prov, v_pts, vp_name, me_state, me_prov, me_pts, me_name, status`

## Status vocabulary

### v2m_states / m2v_states
- `1:1` — one ME descendant
- `1:many` — vanilla state subdivided; see `me_members` (all children) + `me_primary` (vote winner)
- `unmapped` — vanilla state absent (mostly off-map: BRA/CHI/JAP/RAJ/etc.)
- `mod-new` — ME state with no detectable vanilla ancestor (new subdivision or off-map-imported)
- `mapped` — ME state claims a vanilla ancestor

### basis (provenance, strongest→weakest)
- `comment` — `# was vanilla N` migration comment (the canonical evidence)
- `comment(member)` — ME state reached the vanilla ancestor via a non-winning comment vote
- `manual` — entry in `MANUAL`/`MANUAL_ME2V` inside the generator (domain knowledge)
- `name` — exact normalized name match
- `name~` — containment name match (unique candidate only)
- `name-singleton` — ME mod-new whose name equals a vanilla state → subdivision heir
- `vp-geo` — ME state's VP names modal-vote one vanilla state (Ajaccio→Corsica, Linz→Lower Austria)
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

## Invariants the table enforces

1. `v2m.me_members` and `m2v.v_ancestor` are symmetric — every member claims the same ancestor.
2. `comment` basis dominates: never overwritten by name/geo inference.
3. `1:many` member lists are exhaustive (all `# was vanilla N` refs + vp-geo/name-singleton claims).
4. A `cross-state` VP row is a red flag for loc/AI files using it.

## Known intentional gaps

- 440 vanilla `unmapped` states — mostly off-map countries deliberately cut (`Unused.txt` tags).
- ~1695 ME `mod-new` states — fine-grained subdivisions with no vanilla 1:1 (expected: ME ≈3× vanilla density).
- `cross-state` VPs — name collisions or VPs relocated between splits; review individually before citing.

## Maintenance

- Add to `MANUAL` when a split member can ONLY be found by domain knowledge
  (e.g. `77: [1579,818,767]` for Dobrudja's south-Bulgarian shard).
- If a `basis` proves wrong in-game, fix the generator's `MANUAL` first, never the TSV by hand.
- `province_pairs.json` collects `was vanilla N` pairs whose new-id isn't a state
  (province-level references) — source material for a future province-level table.
