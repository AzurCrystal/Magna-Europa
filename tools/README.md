# tools/

Mod development utilities — **not shipped** to the workshop (release packaging only copies game content dirs).

## Map integrity

| Script | Checks |
|---|---|
| `prov_adjacency.py` | Rebuild province adjacency from `provinces.bmp` + `adjacencies.csv` (straits via `Through` column) → `_ctx/prov_adj_fresh.json` |
| `railway_audit.py` | `railways.txt`/`supply_nodes.txt`: chain contiguity, sea-province hops (CTD source), dead prov ids, graph components |
| `buildings_audit.py` | `buildings.txt` pixels land on the declared state (naval on sea = OK) |

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

## Mapping / research

| Script | Output |
|---|---|
| `id_maps.py` | `tools/out/states_map.json` + `provinces_map.json` — mod↔vanilla id mapping (primary: same state id; secondary: name match). VP match is id-in-state then loc-name — coverage ceiling is real, not a bug: mod has ~2.5× vanilla's VP count. |

## Legacy

| Script | Purpose |
|---|---|
| `unAccenternator.py` | strips diacritics from `history/states` filenames (one-off cleanup) |

## Usage

```bash
python tools/railway_audit.py     # after any map/ edit — expect 0/0/0
python tools/vp_loc_audit.py      # after loc or state-file edits
python tools/focus_ref_audit.py   # after focus tree edits
python tools/id_maps.py           # regenerate mapping tables (untracked output)
python tools/loc_audit.py --vanilla "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
```
