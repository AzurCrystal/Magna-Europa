# Toolpack for Magna Europa: Reforged — porting notes

Patch-layer submod. Required mods (all three, any order works):
- `Magna Europa: Reforged` (workshop 3809580289)
- `Toolpack without the Errors` (workshop 2913150560)
- this submod, loaded **last** (`dependencies` enforces it).

## Why a patch layer is needed at all

ME `replace_path`s 38 directories — including every one toolpack writes into:
`common/decisions`, `common/decisions/categories`, `common/ideas`, `common/on_actions`,
`common/scripted_effects`, `common/scripted_guis`, `common/scripted_localisation`,
`common/scripted_triggers`, `common/units`, `events`.

`replace_path` discards files from mods loaded **earlier** in that dir. Toolpack's
descriptor `dependencies` only lists "Magna Europa: Reloaded" (the predecessor mod),
never "Magna Europa: Reforged" — so nothing forces toolpack to load after ME.
If it loads first, **its entire script layer is silently wiped** and the mod does
nothing ("Toolpack doesn't work" symptom). The old "Toolpack for Magna Europa"
(workshop 2973258716, discontinued, 1.13) dodged this by declaring the correct
dependency — and was otherwise byte-identical to its parent revision (verified
against smods.ru mirrors, May-8 rev == Apr-18-2023 parent except descriptor).

This submod therefore ships verbatim copies of all 41 toolpack files under
`replace_path` dirs (see `tools/sync_toolpack.py`), so the script layer survives
regardless of whether Steam/the launcher orders toolpack after ME. Files NOT under
`replace_path` (interface/*.gui, toolpack_gfx.gfx, gfx/, localisation/*,
opinion_modifiers) are still sourced from the toolpack mod itself — do NOT copy
them here.

## ME adaptations applied on top of verbatim copies

### `common/scripted_guis/tpt.txt` — faction-name flavor state ids
8 `owns_state` literals inside `tpt_create_faction_click` (flavor names like
"Treaty of Geneva/Paris/London/Berlin/Rome/Moscow/Vienna/Warsaw") use vanilla
state ids that mean something else on the ME map. Remapped to the ME state that
holds the capital-city victory point:

| vanilla | vanilla meaning | ME id | ME state (VP evidence) |
|---|---|---|---|
| 3  | Swiss Plateau (Geneva) | 1452 | Geneve — VP 19840 "Genève" |
| 16 | Ile-de-France (Paris) | 571  | Paris — VP 19865 "Paris" |
| 126| London              | 2475 | London — VP 12038 "London" |
| 64 | Brandenburg (Berlin) | 1650 | East Berlin — VP 6184 "Berlin" |
| 2  | Rome                | 876  | Roma Centro — VP 20009 "Roma" |
| 219| Moscow              | 229  | Moscow — VP 18646 "Moskva" |
| 4  | Vienna              | 1817 | North Vienna — VP 20848 "Wien" |
| 10 | Warsaw              | 508  | Warszawa — VP 11800 "Warszawa" |

Note: ME splits capitals into multiple states — the VP-holding state is the right
target (e.g. Berlin VP lives in 1650-East Berlin, not 3-West Berlin).
`is_core_of = <TAG>` fallbacks in the same blocks already work (all 8 tags exist
in ME history/countries).

## Verified NOT needing patches (census 2026-10-02, toolpack rev Aug-10-2026)

- **All other state/province access is scope-driven**: `transfer_state = var:…`
  (~140×), clicked-state contexts (`selected_state_context`), `set_capital =
  { state = FROM.FROM }`, `bst_marked` state flags. Zero literal province ids.
- **Buildings**: toolpack uses 14 building ids; ME's `common/buildings` id set is
  byte-identical to vanilla → all exist.
- **Resources**: ME has no `common/resources` → vanilla 7-type set; toolpack adds
  `coal` via rmt tool, which is a vanilla resource here. (`tp_scripted_triggers`
  references `coal` inside `mrt_has_resources` — harmless leftover clause that
  just never fires for non-coal states; kept verbatim.)
- **Ideologies**: toolpack uses only vanilla groups democratic/fascism/communism/
  neutrality — all present in ME.
- **State categories**: smt upgrade/downgrade cycles all 12 ME categories.
- **Tech/equipment/modules**: all `has_tech`, ship-hull tiers, MtG module ids and
  equipment archetypes verified present in ME (only `improved/advanced_battlecruiser`
  absent → those two spawn options silently no-op, same as vanilla behavior for
  unresearched hulls).
- **Country rules, autonomy effects, civil-war tools, MP action log, template
  unlocks**: engine-generic; missing DLC template names are silent no-ops.
- **Interface**: all 17 toolpack .gui files define own-named containers anchored
  to screen edges (LOWER_RIGHT etc.) — no vanilla window overrides, resolution-
  independent. ME has zero .gui files → no collision.
- **Map modes**: untouched by both mods; `selected_state_context` GUIs trigger on
  state click as designed.

## Known non-issues / won't-fix
- `cct.txt` cosmetic-tag tool needs per-country `TAG_ideology` cosmetic tags; ME
  doesn't define them → that tool silently does nothing (same on vanilla for mods
  without cosmetic tags).
- `sst` ship spawning writes to stockpile; fine.
- Dead `rmt_main_container_variable_version` window def — upstream leftover, kept.

## Updating when toolpack updates
Run `python tools/sync_toolpack.py <path-to-workshop-toolpack>` — it re-copies the
script layer and re-applies the tpt.txt remap. Any *new* file under a replaced dir
is auto-included; check `git status` afterwards.
